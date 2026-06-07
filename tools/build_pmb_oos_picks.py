"""Build PIT-safe purged P_MB OOS picks for KR1000 broker backtests.

This is the bridge between the multibagger classifier research code and the
official KR1000 broker-ledger harness. It deliberately writes a separate OOS
picks file instead of overwriting the legacy research CSV, so older evidence is
not silently redefined.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, KR_ENGINE_REUSE_VERSION  # noqa: E402
from kr_helpers import log  # noqa: E402


LABEL_COLS = {
    "is_pre_surge",
    "is_pre_entry",
    "is_continuation",
    "is_risk",
}

EXTRA_LEAKAGE_EXCLUDES = {
    "leader_rank",
    "leader_score",
    "is_risk_observed",
    "rank_in_month",
    "p_pre_surge",
    "p_pre_entry",
    "p_continuation",
    "p_risk",
    "p_combined",
    "p_balanced",
    "p_clean_pre_entry",
    "pmb_oos_rank",
}

LEAKY_PREFIXES = (
    "forward_",
    "future_",
    "target_",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build purged P_MB OOS picks")
    p.add_argument("--panel", default=None,
                   help="Input scored_panel parquet/csv. Default=latest feature_store/scored_panel_v0_*.parquet.")
    p.add_argument("--episodes", default=None,
                   help="Input multibagger episodes parquet/csv. Default=latest feature_store/multibagger_episodes_v0_*.parquet.")
    p.add_argument("--start", default=None,
                   help="Optional scored-panel start filter.")
    p.add_argument("--end", default=None,
                   help="Optional scored-panel end filter.")
    p.add_argument("--target-start", default="2018-01-01",
                   help="Coverage gate start. Default=official 8y start.")
    p.add_argument("--target-end", default=None,
                   help="Coverage gate end. Default=max pick date when available.")
    p.add_argument("--out", default=None,
                   help="Output picks CSV. Default=DATA_ROOT/outputs/p_mb_oos_picks_purged_3sleeve_latest.csv.")
    p.add_argument("--coverage-json", default=None,
                   help="Coverage/audit JSON path. Default=<out>.coverage.json.")
    p.add_argument("--k-per-month", type=int, default=30)
    p.add_argument("--min-picks-per-month", type=int, default=20)
    p.add_argument("--n-folds", type=int, default=5)
    p.add_argument("--embargo-months", type=int, default=9)
    p.add_argument("--nan-threshold", type=float, default=0.50)
    p.add_argument("--iterations", type=int, default=400,
                   help="CatBoost iterations for each fold/label model.")
    p.add_argument("--pre-surge-months", type=int, default=6,
                   help="Months before surge_start included in the pre-entry label window.")
    p.add_argument("--pre-buffer-months", type=int, default=1,
                   help="Months immediately before surge_start excluded from pre-entry labels.")
    p.add_argument("--post-surge-months", type=int, default=3,
                   help="Months after surge_start included in continuation labels.")
    p.add_argument("--risk-drawdown-threshold", type=float, default=-0.20,
                   help="Forward 1m drawdown threshold for is_risk labels.")
    p.add_argument("--score-mode", default="balanced",
                   choices=[
                       "balanced",
                       "pre_entry_focus",
                       "strict_pre_entry",
                       "pre_entry_risk_only",
                       "continuation_focus",
                       "no_risk_balanced",
                   ],
                   help="Composite used for p_combined/p_pre_surge ranking.")
    p.add_argument("--min-covered-years", type=float, default=8.0)
    p.add_argument("--min-coverage-ratio", type=float, default=0.95)
    p.add_argument("--fail-on-coverage-gap", action="store_true",
                   help="Exit non-zero when coverage does not meet the official OOS gate.")
    return p.parse_args()


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype={"ticker": str})
    return pd.read_parquet(path)


def _find_latest(pattern: str, root: Path, exclude: tuple[str, ...] = ()) -> Path | None:
    if not root.exists():
        return None
    paths = [
        p for p in root.glob(pattern)
        if not any(token in p.name for token in exclude)
    ]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[0] if paths else None


def _latest_scored_panel_path() -> Path:
    path = _find_latest("scored_panel_v0_*.parquet", DATA_ROOT / "feature_store",
                        exclude=("mini",))
    if path is None:
        raise FileNotFoundError(f"No scored_panel_v0_*.parquet found in {DATA_ROOT / 'feature_store'}")
    return path


def _latest_episodes_path() -> Path:
    path = _find_latest("multibagger_episodes_v0_*.parquet", DATA_ROOT / "feature_store")
    if path is None:
        raise FileNotFoundError(f"No multibagger_episodes_v0_*.parquet found in {DATA_ROOT / 'feature_store'}")
    return path


def _normalise_ticker(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def normalise_scored_panel(panel: pd.DataFrame, start: str | None = None,
                           end: str | None = None) -> pd.DataFrame:
    """Return a scored panel with canonical ticker/date columns and filters."""
    if panel.empty:
        return panel.copy()
    out = panel.copy()
    date_col = "rebalance_date" if "rebalance_date" in out.columns else "date"
    if date_col not in out.columns:
        raise ValueError("scored panel must include rebalance_date or date")
    out = out.rename(columns={date_col: "rebalance_date"})
    out["rebalance_date"] = pd.to_datetime(out["rebalance_date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["rebalance_date"]).copy()
    if "ticker" not in out.columns:
        raise ValueError("scored panel must include ticker")
    out["ticker"] = _normalise_ticker(out["ticker"])
    if start:
        out = out[out["rebalance_date"] >= pd.Timestamp(start).normalize()]
    if end:
        out = out[out["rebalance_date"] <= pd.Timestamp(end).normalize()]
    return out.sort_values(["rebalance_date", "ticker"]).reset_index(drop=True)


def select_pmb_feature_columns(panel: pd.DataFrame,
                               nan_threshold: float = 0.50) -> list[str]:
    """Select numeric classifier features while excluding labels/targets.

    The base selector already removes IDs and the legacy `is_pre_surge` label.
    This wrapper additionally removes 3-sleeve labels, generated probabilities,
    leader ranks, and any forward/future/target-prefixed columns that would
    leak realized outcomes into the classifier.
    """
    from kr_multibagger_classifier import select_feature_columns

    extra = set(LABEL_COLS) | EXTRA_LEAKAGE_EXCLUDES
    candidates = select_feature_columns(
        panel,
        nan_threshold=nan_threshold,
        extra_excludes=extra,
    )
    clean: list[str] = []
    for col in candidates:
        low = col.lower()
        if low in extra:
            continue
        if any(low.startswith(prefix) for prefix in LEAKY_PREFIXES):
            continue
        clean.append(col)
    return clean


def label_three_sleeve_panel(
    panel: pd.DataFrame,
    episodes: pd.DataFrame,
    *,
    pre_surge_months: int = 6,
    pre_buffer_months: int = 1,
    post_surge_months: int = 3,
    risk_drawdown_threshold: float = -0.20,
) -> pd.DataFrame:
    """Attach pre-entry, continuation, and risk labels to the scored panel."""
    from kr_multibagger_classifier import (
        label_continuation,
        label_pre_entry,
        label_risk,
    )

    labeled = label_pre_entry(
        panel,
        episodes,
        pre_surge_months=int(pre_surge_months),
        pre_buffer_months=int(pre_buffer_months),
    )
    labeled = label_continuation(
        labeled,
        episodes,
        post_surge_months=int(post_surge_months),
    )
    labeled = label_risk(
        labeled,
        horizon_months=1,
        drawdown_threshold=float(risk_drawdown_threshold),
    )
    for col in LABEL_COLS:
        if col in labeled.columns:
            labeled[col] = pd.to_numeric(labeled[col], errors="coerce").fillna(0).astype(int)
    return labeled


def audit_purged_splits(panel: pd.DataFrame, n_folds: int,
                        embargo_months: int) -> dict[str, Any]:
    """Summarize purged walk-forward split gaps and label prevalence."""
    from kr_multibagger_classifier import walk_forward_splits_purged

    if panel.empty:
        return {"n_folds": 0, "folds": []}
    splits = walk_forward_splits_purged(
        panel, n_folds=n_folds, embargo_months=embargo_months,
    )
    folds = []
    reset = panel.reset_index(drop=True)
    for i, (tr_idx, te_idx) in enumerate(splits, 1):
        train_dates = reset.iloc[tr_idx]["rebalance_date"]
        test_dates = reset.iloc[te_idx]["rebalance_date"]
        train_max = pd.Timestamp(train_dates.max()).normalize()
        test_min = pd.Timestamp(test_dates.min()).normalize()
        gap_months = int((test_min.to_period("M") - train_max.to_period("M")).n)
        row = {
            "fold_id": i,
            "train_rows": int(len(tr_idx)),
            "test_rows": int(len(te_idx)),
            "train_start": str(pd.Timestamp(train_dates.min()).date()),
            "train_end": str(train_max.date()),
            "test_start": str(test_min.date()),
            "test_end": str(pd.Timestamp(test_dates.max()).date()),
            "gap_months": gap_months,
        }
        for label in ("is_pre_entry", "is_continuation", "is_risk"):
            if label in reset.columns:
                row[f"train_{label}_pos"] = int(pd.to_numeric(
                    reset.iloc[tr_idx][label], errors="coerce").fillna(0).sum())
                row[f"test_{label}_pos"] = int(pd.to_numeric(
                    reset.iloc[te_idx][label], errors="coerce").fillna(0).sum())
        folds.append(row)
    return {
        "n_folds": int(len(splits)),
        "embargo_months": int(embargo_months),
        "min_gap_months": int(min((f["gap_months"] for f in folds), default=0)),
        "folds": folds,
    }


def audit_pmb_oos_coverage(
    picks: pd.DataFrame,
    target_start: str | pd.Timestamp = "2018-01-01",
    target_end: str | pd.Timestamp | None = None,
    min_covered_years: float = 8.0,
    min_coverage_ratio: float = 0.95,
    min_picks_per_month: int = 20,
) -> dict[str, Any]:
    """Check whether OOS picks cover the official broker-test window."""
    start = pd.Timestamp(target_start).normalize()
    out: dict[str, Any] = {
        "target_start": str(start.date()),
        "min_covered_years": float(min_covered_years),
        "min_coverage_ratio": float(min_coverage_ratio),
        "min_picks_per_month": int(min_picks_per_month),
        "rows": int(len(picks)),
    }
    if picks.empty or "rebalance_date" not in picks.columns:
        out.update({
            "status": "missing",
            "pass": False,
            "reason": "empty_or_missing_rebalance_date",
        })
        return out

    df = picks.copy()
    df["rebalance_date"] = pd.to_datetime(df["rebalance_date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["rebalance_date"])
    if df.empty:
        out.update({
            "status": "missing",
            "pass": False,
            "reason": "no_valid_rebalance_dates",
        })
        return out

    end = pd.Timestamp(target_end).normalize() if target_end else pd.Timestamp(df["rebalance_date"].max()).normalize()
    if end < start:
        end = start
    out["target_end"] = str(end.date())

    expected = pd.period_range(start.to_period("M"), end.to_period("M"), freq="M")
    by_month = df.groupby(df["rebalance_date"].dt.to_period("M")).size()
    covered = [p for p in expected if int(by_month.get(p, 0)) >= int(min_picks_per_month)]
    missing = [str(p) for p in expected if int(by_month.get(p, 0)) < int(min_picks_per_month)]
    first_pick = pd.Timestamp(df["rebalance_date"].min()).normalize()
    last_pick = pd.Timestamp(df["rebalance_date"].max()).normalize()
    covered_years = (
        (pd.Timestamp(covered[-1].end_time).normalize() - pd.Timestamp(covered[0].start_time).normalize()).days / 365.25
        if covered else 0.0
    )
    coverage_ratio = float(len(covered) / max(len(expected), 1))
    ok = covered_years >= float(min_covered_years) and coverage_ratio >= float(min_coverage_ratio)
    out.update({
        "status": "passed" if ok else "failed",
        "pass": bool(ok),
        "first_pick_date": str(first_pick.date()),
        "last_pick_date": str(last_pick.date()),
        "expected_months": int(len(expected)),
        "covered_months": int(len(covered)),
        "coverage_ratio": coverage_ratio,
        "covered_years": float(covered_years),
        "missing_months": missing[:36],
        "missing_months_truncated": bool(len(missing) > 36),
        "min_picks_in_covered_months": int(min((int(by_month.get(p, 0)) for p in covered), default=0)),
    })
    return out


def build_purged_oos_picks(
    panel: pd.DataFrame,
    episodes: pd.DataFrame,
    *,
    k_per_month: int = 30,
    n_folds: int = 5,
    embargo_months: int = 9,
    nan_threshold: float = 0.50,
    iterations: int = 400,
    pre_surge_months: int = 6,
    pre_buffer_months: int = 1,
    post_surge_months: int = 3,
    risk_drawdown_threshold: float = -0.20,
    score_mode: str = "balanced",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Label, feature-select, and generate purged 3-sleeve OOS picks."""
    from kr_backtester_realistic import generate_oos_picks_purged_3sleeve

    labeled = label_three_sleeve_panel(
        panel,
        episodes,
        pre_surge_months=pre_surge_months,
        pre_buffer_months=pre_buffer_months,
        post_surge_months=post_surge_months,
        risk_drawdown_threshold=risk_drawdown_threshold,
    )
    feature_cols = select_pmb_feature_columns(labeled, nan_threshold=nan_threshold)
    split_audit = audit_purged_splits(labeled, n_folds=n_folds, embargo_months=embargo_months)
    cb_params = {"iterations": int(iterations)}
    picks = generate_oos_picks_purged_3sleeve(
        labeled,
        feature_cols,
        n_folds=n_folds,
        embargo_months=embargo_months,
        k_per_month=k_per_month,
        score_mode=score_mode,
        cb_params=cb_params,
    )
    build_audit = {
        "engine_version": KR_ENGINE_REUSE_VERSION,
        "panel_rows": int(len(panel)),
        "panel_months": int(panel["rebalance_date"].nunique()) if "rebalance_date" in panel.columns else 0,
        "episodes": int(len(episodes)),
        "feature_count": int(len(feature_cols)),
        "feature_cols": feature_cols,
        "label_counts": {
            label: int(pd.to_numeric(labeled.get(label, 0), errors="coerce").fillna(0).sum())
            for label in ("is_pre_entry", "is_continuation", "is_risk")
        },
        "split_audit": split_audit,
        "k_per_month": int(k_per_month),
        "n_folds": int(n_folds),
        "embargo_months": int(embargo_months),
        "label_window": {
            "pre_surge_months": int(pre_surge_months),
            "pre_buffer_months": int(pre_buffer_months),
            "post_surge_months": int(post_surge_months),
            "risk_drawdown_threshold": float(risk_drawdown_threshold),
        },
        "score_mode": str(score_mode),
    }
    return picks, build_audit


def main() -> int:
    args = parse_args()
    panel_path = Path(args.panel) if args.panel else _latest_scored_panel_path()
    episodes_path = Path(args.episodes) if args.episodes else _latest_episodes_path()
    out_path = Path(args.out) if args.out else DATA_ROOT / "outputs" / "p_mb_oos_picks_purged_3sleeve_latest.csv"
    coverage_path = Path(args.coverage_json) if args.coverage_json else out_path.with_suffix(".coverage.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_path.parent.mkdir(parents=True, exist_ok=True)

    log(f"[pmb-oos] panel = {panel_path}")
    log(f"[pmb-oos] episodes = {episodes_path}")
    panel = normalise_scored_panel(_read_table(panel_path), start=args.start, end=args.end)
    episodes = _read_table(episodes_path)
    if "ticker" in episodes.columns:
        episodes = episodes.copy()
        episodes["ticker"] = _normalise_ticker(episodes["ticker"])
    if "surge_start_date" in episodes.columns:
        episodes["surge_start_date"] = pd.to_datetime(
            episodes["surge_start_date"], errors="coerce",
        )

    picks, build_audit = build_purged_oos_picks(
        panel,
        episodes,
        k_per_month=args.k_per_month,
        n_folds=args.n_folds,
        embargo_months=args.embargo_months,
        nan_threshold=args.nan_threshold,
        iterations=args.iterations,
        pre_surge_months=args.pre_surge_months,
        pre_buffer_months=args.pre_buffer_months,
        post_surge_months=args.post_surge_months,
        risk_drawdown_threshold=args.risk_drawdown_threshold,
        score_mode=args.score_mode,
    )
    if not picks.empty:
        picks.to_csv(out_path, index=False)
    else:
        pd.DataFrame(columns=[
            "rebalance_date", "ticker", "p_pre_entry", "p_continuation",
            "p_risk", "p_balanced", "p_clean_pre_entry", "p_combined",
            "p_pre_surge", "rank_in_month", "pmb_score_mode",
        ]).to_csv(out_path, index=False)

    target_end = args.target_end or (str(panel["rebalance_date"].max().date()) if not panel.empty else None)
    coverage = audit_pmb_oos_coverage(
        picks,
        target_start=args.target_start,
        target_end=target_end,
        min_covered_years=args.min_covered_years,
        min_coverage_ratio=args.min_coverage_ratio,
        min_picks_per_month=args.min_picks_per_month,
    )
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "panel_path": str(panel_path),
        "episodes_path": str(episodes_path),
        "out": str(out_path),
        "build": build_audit,
        "coverage": coverage,
    }
    coverage_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print("P_MB purged OOS picks")
    print(f"  rows:       {len(picks)}")
    if not picks.empty:
        print(f"  window:     {pd.to_datetime(picks['rebalance_date']).min().date()} .. {pd.to_datetime(picks['rebalance_date']).max().date()}")
    print(f"  features:   {build_audit['feature_count']}")
    print(f"  score mode: {build_audit['score_mode']}")
    print(f"  split gap:  {build_audit['split_audit'].get('min_gap_months')} months")
    print(f"  coverage:   {coverage.get('status')} ({coverage.get('covered_months')}/{coverage.get('expected_months')} months)")
    print(f"  out:        {out_path}")
    print(f"  audit:      {coverage_path}")
    if args.fail_on_coverage_gap and not coverage.get("pass"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
