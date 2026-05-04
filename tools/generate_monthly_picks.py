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

import numpy as np
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
    # Concentrated portfolio defaults (Phase D-fix v3, 2026-05-04). User
    # explicitly requested high-conviction concentration: <10 picks with
    # the top name carrying 40%+ of the book to maximize expected return
    # per name. Use --top-n / --weighting / --weight-cap to revert to the
    # diversified preset.
    p.add_argument("--top-n", type=int, default=8,
                   help="Number of picks (default 8 = concentrated portfolio).")
    p.add_argument("--no-classifier", action="store_true",
                   help="Skip classifier; rank by p0_momentum_score only.")
    p.add_argument("--no-governance", action="store_true",
                   help="Skip governance overlay.")
    # Picks selection knobs (Phase D-fix v3, 2026-05-02). User feedback:
    # mega-caps (Samsung, SK Hynix) DO produce multibagger-scale returns over
    # multi-year windows AND are real quality. Conversely a 1억 -> 30억 jump
    # is "30x" but the company is still micro-cap junk. So we set a
    # MINIMUM mcap (default 5,000억) and NO maximum. This aligns the live
    # universe with the multibagger training distribution
    # (multibagger_min_mcap_krw = 5e11) and removes fake-multibagger noise.
    p.add_argument("--min-mcap-krw", type=float, default=5e11,
                   help="Mcap LOWER bound for picks (default 5,000억 = quality filter).")
    p.add_argument("--max-mcap-krw", type=float, default=None,
                   help="Mcap upper bound for picks. Default = no cap (allow mega-caps).")
    p.add_argument("--composite-alpha-weight", type=float, default=0.6,
                   help="Weight on classifier p_pre_surge in composite score (0..1).")
    p.add_argument("--composite-momentum-weight", type=float, default=0.4,
                   help="Weight on momentum z-score in composite score (0..1).")
    p.add_argument("--weighting", default="concentrated",
                   choices=("equal", "score", "score_power", "capped",
                              "concentrated"),
                   help="Position weighting (default concentrated for high-conviction book).")
    p.add_argument("--score-power", type=float, default=1.5,
                   help="Power for score_power / capped weighting.")
    p.add_argument("--weight-cap", type=float, default=0.45,
                   help="Per-name weight cap (default 0.45 for concentrated mode).")
    p.add_argument("--concentrated-power", type=float, default=1.1,
                   help="Rank-decay power for concentrated mode "
                        "(1.0 -> top ~37pct, 1.1 -> ~41pct, 1.2 -> ~45pct).")
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

                # Feature alignment — load the training-time feature_cols list
                # so that inference uses the EXACT same columns in the same
                # order. Missing features in `enriched` are zero-filled rather
                # than silently dropped (which previously triggered
                # "Feature N is present in model but not in pool").
                feat_cols = _load_classifier_feature_cols(classifier_path)
                if feat_cols is None:
                    from kr_multibagger_classifier import select_feature_columns
                    feat_cols = select_feature_columns(enriched)
                    log("[picks] no feature_cols metadata -> selecting from live "
                        f"features ({len(feat_cols)} cols, may misalign)",
                        level="WARN")

                aligned = pd.DataFrame(0.0, index=enriched.index,
                                        columns=feat_cols)
                shared = [c for c in feat_cols if c in enriched.columns]
                aligned[shared] = enriched[shared].astype(float).fillna(0.0)
                missing = [c for c in feat_cols if c not in enriched.columns]
                if missing:
                    log(f"[picks] {len(missing)}/{len(feat_cols)} features "
                        f"missing in inference (zero-filled): "
                        f"{missing[:5]}...", level="WARN")

                X = aligned.values
                proba = model.predict_proba(X)[:, 1]
                enriched["p_pre_surge"] = proba
                log(f"[picks] classifier scored {len(enriched)} rows from "
                    f"{classifier_path.name} ({len(feat_cols)} features, "
                    f"{len(shared)} shared, {len(missing)} zero-filled)")
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

    # Mcap LOWER bound — strip micro-caps that produce fake multibaggers
    # (1억 -> 30억 = "30x" but company is still junk). Aligns live universe
    # with multibagger training distribution.
    if args.min_mcap_krw and args.min_mcap_krw > 0 and "market_cap" in enriched.columns:
        n_pre = len(enriched)
        min_mcap = float(args.min_mcap_krw)
        enriched = enriched[
            enriched["market_cap"].fillna(0).astype(float) >= min_mcap
        ].copy()
        log(f"[picks] mcap floor (>= {min_mcap:,.0f} KRW): "
            f"{n_pre} -> {len(enriched)}")
    # Optional mcap cap (off by default; mega-caps allowed).
    if args.max_mcap_krw is not None and "market_cap" in enriched.columns:
        n_pre = len(enriched)
        max_mcap = float(args.max_mcap_krw)
        enriched = enriched[
            enriched["market_cap"].fillna(0).astype(float) <= max_mcap
        ].copy()
        log(f"[picks] mcap cap (<= {max_mcap:,.0f} KRW): "
            f"{n_pre} -> {len(enriched)}")

    # Composite score = alpha (classifier) + momentum z. The two halves are
    # rank-normalised within the universe so they have comparable scale even
    # when classifier probabilities are clustered around the prevalence
    # (~1.8%) and momentum z is wide.
    aw = float(args.composite_alpha_weight)
    mw = float(args.composite_momentum_weight)
    if (aw + mw) <= 0:
        aw, mw = 1.0, 0.0
    rank_alpha = enriched["adjusted_score"].rank(pct=True, method="average")
    if "ret_12_1m_z" in enriched.columns:
        mom_raw = enriched["ret_12_1m_z"].fillna(0)
    elif "ret_12_1m" in enriched.columns:
        mom_raw = enriched["ret_12_1m"].fillna(0)
    else:
        mom_raw = enriched.get("p0_momentum_score",
                                pd.Series(0.0, index=enriched.index)).fillna(0)
    rank_mom = mom_raw.rank(pct=True, method="average")
    enriched["composite_score"] = (
        (aw * rank_alpha + mw * rank_mom) / max(aw + mw, 1e-9)
    )
    log(f"[picks] composite_score = {aw:.2f}*rank(p_pre_surge) + "
        f"{mw:.2f}*rank(momentum_z), univ {len(enriched)}")

    enriched = enriched.sort_values("composite_score", ascending=False)
    picks = enriched.head(args.top_n).copy()

    # Weighting (defaults to `capped` for differentiated allocation; previously
    # equal-weight gave every name 5%, which felt unsatisfying when picks have
    # similar p_pre_surge values). Uses composite_score so high-conviction
    # picks (where alpha and momentum agree) get larger weight.
    weighting = args.weighting
    score_for_weighting = picks["composite_score"]
    if len(picks) == 0:
        picks["weight"] = 0.0
    elif weighting == "equal":
        picks["weight"] = 1.0 / len(picks)
    elif weighting == "score":
        s = score_for_weighting.clip(lower=1e-9)
        picks["weight"] = s / s.sum()
    elif weighting == "score_power":
        s = score_for_weighting.clip(lower=1e-9) ** float(args.score_power)
        picks["weight"] = s / s.sum()
    elif weighting == "capped":
        s = score_for_weighting.clip(lower=1e-9) ** float(args.score_power)
        w = s / s.sum()
        cap = float(args.weight_cap)
        # Iterative water-filling so cap-constrained weights still sum to 1.
        for _ in range(20):
            over = w > cap
            if not over.any():
                break
            slack = float((w[over] - cap).sum())
            w = w.where(~over, cap)
            others = ~over
            if others.any() and slack > 0:
                pool = float(w[others].sum())
                if pool > 0:
                    w = w.where(~others, w + slack * w / pool)
        picks["weight"] = w
    elif weighting == "concentrated":
        # High-conviction concentration: rank-power decay so the top pick
        # carries 35-45pct of the book. Power 1.0 -> top ~37pct,
        # 1.1 -> ~41pct, 1.2 -> ~45pct (for top_n=8).
        # Sorted picks already by composite_score descending, so rank 1
        # is the strongest signal.
        n = len(picks)
        ranks = np.arange(1, n + 1, dtype=float)
        raw = ranks ** (-float(args.concentrated_power))
        w = raw / raw.sum()
        # Hard cap at --weight-cap (default 0.45) to bound single-name risk
        cap = float(args.weight_cap)
        w = np.minimum(w, cap)
        if w.sum() > 0:
            w = w / w.sum()
        picks["weight"] = w
    log(f"[picks] weighting='{weighting}' "
        f"(top {len(picks)} picks): top weight = "
        f"{float(picks['weight'].max() if len(picks) else 0):.3f}, "
        f"bottom = {float(picks['weight'].min() if len(picks) else 0):.3f}")

    # Apply governance per-name cap on top of the weighting (further cap risky
    # names; renormalize to sum 1).
    if "governance_risk_score" in picks.columns:
        picks["weight"] = [
            governance_weight_cap(float(r), float(w))
            for r, w in zip(picks["governance_risk_score"].fillna(0),
                             picks["weight"])
        ]
        tot = float(picks["weight"].sum())
        if tot > 0:
            picks["weight"] = picks["weight"] / tot

    # 6. Persist outputs
    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = rd.strftime("%Y-%m-%d")
    csv_path = out_dir / f"live_portfolio_{stamp}.csv"
    latest_path = out_dir / "live_portfolio_latest.csv"
    keep_cols = [
        c for c in (
            "rebalance_date", "ticker", "name", "exchange", "market_cap",
            "p_pre_surge", "adjusted_score", "composite_score",
            "ret_12_1m", "ret_3m",
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


def _load_classifier_feature_cols(classifier_path: Path) -> list[str] | None:
    """Locate the feature_cols list saved alongside the classifier.

    Looks for (in order):
      1. <models>/classifier_latest_metrics.json
      2. <models>/classifier_metrics_<YYYY-MM>.json (most recent)
    Returns the feature_cols list, or None when neither exists / lacks the key.
    """
    models_dir = classifier_path.parent
    candidates = [
        models_dir / "classifier_latest_metrics.json",
    ] + sorted(
        models_dir.glob("classifier_metrics_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in candidates:
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        cols = meta.get("feature_cols")
        if cols and isinstance(cols, list):
            return list(cols)
    return None


if __name__ == "__main__":
    sys.exit(main())
