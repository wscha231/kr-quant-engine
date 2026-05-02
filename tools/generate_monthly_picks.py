"""tools/generate_monthly_picks.py — monthly auto picks CLI (Phase D).

Designed to run inside GitHub Actions on the 1st of every month. Produces:
  outputs/live_portfolio_<YYYY-MM-DD>.csv   # this rebalance's picks
  outputs/live_portfolio_latest.csv         # symlink/copy of the same
  outputs/live_meta_<YYYY-MM-DD>.json       # diagnostics

Pipeline (uses Phase C1-C4 PIT-correct components):
  1. Pick the most recent month-end business day.
  2. Build PIT universe via kr_universe.build_universe_snapshot.
  3. Add features (kr_features.add_universe_features) using cached panels.
  4. Predict picks via the persisted classifier (models/p_mb_v1_*.cbm).
     If no classifier is found, fall back to ranking by p0_momentum_score.
  5. Apply governance hard-veto + risk-tiered weight cap.
  6. Write CSV + meta JSON.

Env vars / secrets used:
  KR_DATA_DIR             optional override of data root
  DART_API_KEY            DART OpenAPI key (for refresh)
  BOK_ECOS_API_KEY        BOK ECOS key (for refresh)

Exit codes:
  0  picks generated successfully
  1  hard failure (no universe / no panels / etc.)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, DEFAULT_CFG  # noqa: E402
from kr_helpers import log  # noqa: E402


def _latest_month_end(today: datetime | None = None) -> pd.Timestamp:
    """Most recent KRX business day at or before the last day of the previous
    month. If today is the 1st, returns previous month-end; otherwise returns
    the last business day of the current month so far."""
    today = today or datetime.now()
    ts = pd.Timestamp(today).normalize()
    # Use last calendar day of the previous month, then snap to KRX business day
    prev_month_end = (ts.replace(day=1) - pd.Timedelta(days=1))
    # Walk back if it is a Sat/Sun
    while prev_month_end.weekday() >= 5:
        prev_month_end -= pd.Timedelta(days=1)
    return prev_month_end


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Monthly picks generator")
    p.add_argument("--rebalance-date", default=None,
                   help="Override (YYYY-MM-DD). Default = previous month-end.")
    p.add_argument("--top-n", type=int, default=20,
                   help="Number of picks to produce (default 20).")
    p.add_argument("--no-classifier", action="store_true",
                   help="Skip classifier; rank by p0_momentum_score only.")
    p.add_argument("--no-governance", action="store_true",
                   help="Skip governance overlay.")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # 1. Resolve target date
    if args.rebalance_date:
        rd = pd.Timestamp(args.rebalance_date).normalize()
    else:
        rd = _latest_month_end()
    log(f"[picks] target rebalance_date = {rd.date()}")

    # 2. Build PIT universe
    from kr_universe import build_universe_snapshot
    cfg = {**DEFAULT_CFG}
    universe = build_universe_snapshot(rd, cfg=cfg)
    if universe.empty:
        log(f"[picks] empty universe at {rd.date()} — aborting", level="ERROR")
        return 1
    eligible = universe[universe["eligible"]].copy()
    log(f"[picks] universe: {len(universe)} total, {len(eligible)} eligible")

    # 3. Build features (uses cached panels — no fresh network unless missing)
    from kr_features import add_universe_features

    # Lightweight panels: try loading any persisted feature_store; if none we
    # still get a usable feature set from add_basic_momentum + add_basic_value.
    fund_panel = None
    event_panel = None
    macro_panel = None
    derivatives_panel = None

    feature_store = DATA_ROOT / "feature_store"
    if feature_store.exists():
        for pq in feature_store.glob("event_panel_*.parquet"):
            try:
                event_panel = pd.read_parquet(pq)
                log(f"[picks] reuse event_panel: {pq.name} ({len(event_panel)} rows)")
                break
            except Exception:
                continue
        for pq in feature_store.glob("macro_panel_*.parquet"):
            try:
                macro_panel = pd.read_parquet(pq)
                break
            except Exception:
                continue
        for pq in feature_store.glob("derivatives_panel_*.parquet"):
            try:
                derivatives_panel = pd.read_parquet(pq)
                break
            except Exception:
                continue
        for pq in feature_store.glob("fund_panel_*.parquet"):
            try:
                fund_panel = pd.read_parquet(pq)
                break
            except Exception:
                continue

    enriched = add_universe_features(
        eligible, rd,
        fund_panel=fund_panel, event_panel=event_panel,
        macro_panel=macro_panel, derivatives_panel=derivatives_panel,
    )

    # 4. Score / rank
    from kr_governance import is_hard_veto, governance_weight_cap

    if not args.no_classifier:
        # Try persisted classifier
        classifier_path = _find_classifier()
        if classifier_path is not None:
            try:
                from catboost import CatBoostClassifier
                model = CatBoostClassifier()
                model.load_model(str(classifier_path))
                from kr_multibagger_classifier import select_feature_columns
                feat_cols = select_feature_columns(enriched)
                X = enriched[feat_cols].fillna(0).values
                proba = model.predict_proba(X)[:, 1]
                enriched["p_pre_surge"] = proba
                log(f"[picks] classifier scored {len(enriched)} rows from "
                    f"{classifier_path.name} ({len(feat_cols)} features)")
            except Exception as ex:
                log(f"[picks] classifier load fail: {ex} -> momentum fallback",
                    level="WARN")
                enriched["p_pre_surge"] = enriched.get(
                    "p0_momentum_score", pd.Series(0.0, index=enriched.index))
        else:
            log("[picks] no classifier found -> momentum fallback", level="WARN")
            enriched["p_pre_surge"] = enriched.get(
                "p0_momentum_score", pd.Series(0.0, index=enriched.index))
    else:
        enriched["p_pre_surge"] = enriched.get(
            "p0_momentum_score", pd.Series(0.0, index=enriched.index))

    # 5. Governance overlay (already in columns from add_universe_features)
    if not args.no_governance and "governance_hard_veto_flag" in enriched.columns:
        n0 = len(enriched)
        enriched = enriched[
            enriched["governance_hard_veto_flag"].fillna(0).astype(float) < 0.5
        ].copy()
        log(f"[picks] governance vetoed {n0 - len(enriched)}/{n0} tickers")
        if "governance_risk_score" in enriched.columns:
            r = enriched["governance_risk_score"].fillna(0).astype(float)
            enriched["adjusted_score"] = (
                enriched["p_pre_surge"].fillna(0).astype(float)
                * (1.0 - 0.35 * r)
            )
        else:
            enriched["adjusted_score"] = enriched["p_pre_surge"]
    else:
        enriched["adjusted_score"] = enriched["p_pre_surge"]

    enriched = enriched.sort_values("adjusted_score", ascending=False)
    picks = enriched.head(args.top_n).copy()

    # Equal-weight by default; cap by governance tier
    picks["weight_raw"] = 1.0 / max(1, len(picks))
    if "governance_risk_score" in picks.columns:
        picks["weight"] = [
            governance_weight_cap(float(r), float(w))
            for r, w in zip(picks["governance_risk_score"].fillna(0),
                             picks["weight_raw"])
        ]
        tot = float(picks["weight"].sum())
        if tot > 0:
            picks["weight"] = picks["weight"] / tot
    else:
        picks["weight"] = picks["weight_raw"]

    # 6. Persist outputs
    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = rd.strftime("%Y-%m-%d")
    csv_path = out_dir / f"live_portfolio_{stamp}.csv"
    latest_path = out_dir / "live_portfolio_latest.csv"
    keep_cols = [
        c for c in (
            "rebalance_date", "ticker", "name", "exchange", "market_cap",
            "p_pre_surge", "adjusted_score",
            "governance_risk_score", "governance_hard_veto_flag",
            "weight",
        ) if c in picks.columns
    ]
    out_df = picks[keep_cols].reset_index(drop=True)
    out_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    out_df.to_csv(latest_path, index=False, encoding="utf-8-sig")

    meta = {
        "rebalance_date": stamp,
        "n_picks": int(len(picks)),
        "n_universe_eligible": int(len(eligible)),
        "n_universe_total": int(len(universe)),
        "classifier_used": (not args.no_classifier),
        "governance_overlay": (not args.no_governance),
        "p_pre_surge_mean": float(picks["p_pre_surge"].mean())
            if "p_pre_surge" in picks else None,
        "p_pre_surge_min_top": float(picks["p_pre_surge"].min())
            if "p_pre_surge" in picks else None,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    meta_path = out_dir / f"live_meta_{stamp}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    log(f"[picks] wrote {csv_path.name} ({len(picks)} picks)")
    log(f"[picks] wrote {meta_path.name}")
    return 0


def _find_classifier() -> Path | None:
    """Look for the most recent persisted CatBoost model under DATA_ROOT/models."""
    models_dir = DATA_ROOT / "models"
    if not models_dir.exists():
        return None
    candidates = sorted(
        list(models_dir.glob("p_mb_v*.cbm"))
        + list(models_dir.glob("classifier_*.cbm")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


if __name__ == "__main__":
    sys.exit(main())
