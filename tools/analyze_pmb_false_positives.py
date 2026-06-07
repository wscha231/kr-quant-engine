"""Analyze P_MB false positives with leakage-safe walk-forward diagnostics.

This tool consumes the `pmb_rows.parquet` output from
`tools/analyze_pmb_oos_quality.py`. It does not produce official CAGR/MDD
metrics; it checks whether the current P_MB candidate set has a learnable,
PIT-safe false-positive signal before that signal is allowed into the broker
ledger.

Example:
    py -3 tools/analyze_pmb_false_positives.py \
        --pmb-rows outputs/pmb_oos_quality_realized_preentry_2018_20260604/pmb_rows.parquet \
        --out-dir outputs/pmb_false_positive_audit_2018_20260604
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


DEFAULT_PMB_ROWS = DATA_ROOT / "outputs" / "pmb_oos_quality_realized_preentry_2018_20260604" / "pmb_rows.parquet"
DEFAULT_OUT_DIR = DATA_ROOT / "outputs" / "pmb_false_positive_audit"

LEAKY_PREFIXES = (
    "forward_",
    "future_",
    "target_",
    "realized_",
    "analysis_",
)

LEAKY_COLUMNS = {
    "ticker",
    "name",
    "exchange",
    "rebalance_date",
    "corp_code",
    "exclude_reason",
    "fundamentals_period_end",
    "fundamentals_rcept_dt",
    "analysis_return_source",
    "rank_bucket",
    "score_profile",
    "pmb_oos_source",
    "pmb_oos_fold_id",
    "engine_version",
    "snapshot_build_mode",
    "year",
    "good_trade",
    "bad_trade",
    "is_pre_surge",
    "is_pre_entry",
    "is_continuation",
    "is_risk",
    "is_risk_observed",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze P_MB false positives")
    p.add_argument("--pmb-rows", default=str(DEFAULT_PMB_ROWS))
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--target-start", default="2018-01-01")
    p.add_argument("--target-end", default="2026-06-04")
    p.add_argument("--embargo-months", type=int, default=6)
    p.add_argument("--min-train-rows", type=int, default=500)
    p.add_argument("--nan-threshold", type=float, default=0.50)
    p.add_argument("--min-observations", type=int, default=200)
    return p.parse_args()


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p, dtype={"ticker": str})
    return pd.read_parquet(p)


def add_trade_outcome_labels(
    rows: pd.DataFrame,
    *,
    good_return_threshold: float = 0.10,
    good_min_return_floor: float = -0.12,
    bad_return_threshold: float = -0.10,
    bad_min_return_threshold: float = -0.15,
) -> pd.DataFrame:
    """Add realized good/bad trade labels used only for diagnostics."""
    out = rows.copy()
    ret = pd.to_numeric(out.get("analysis_return"), errors="coerce")
    mn = pd.to_numeric(out.get("analysis_min_return"), errors="coerce")
    observed = ret.notna() & mn.notna()
    out = out[observed].copy()
    ret = pd.to_numeric(out["analysis_return"], errors="coerce")
    mn = pd.to_numeric(out["analysis_min_return"], errors="coerce")
    out["good_trade"] = ((ret >= good_return_threshold) & (mn >= good_min_return_floor)).astype(int)
    out["bad_trade"] = ((ret <= bad_return_threshold) | (mn <= bad_min_return_threshold)).astype(int)
    return out


def select_false_positive_features(
    rows: pd.DataFrame,
    *,
    nan_threshold: float = 0.50,
    min_observations: int = 200,
) -> list[str]:
    """Select numeric diagnostic features while excluding labels/outcomes.

    This is intentionally stricter than ordinary feature selection because the
    input table contains realized-return columns and temporary diagnostic
    labels. Accidentally admitting `bad_trade` or `analysis_return` can produce
    perfect but invalid scores.
    """
    cols: list[str] = []
    for col in rows.columns:
        low = col.lower()
        if col in LEAKY_COLUMNS:
            continue
        if low.endswith("_trade") or low.startswith("is_"):
            continue
        if any(low.startswith(prefix) for prefix in LEAKY_PREFIXES):
            continue
        s = rows[col]
        if not (pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s)):
            continue
        if int(s.notna().sum()) < int(min_observations):
            continue
        if float(s.isna().mean()) > float(nan_threshold):
            continue
        if s.nunique(dropna=True) <= 1:
            continue
        cols.append(col)
    forbidden = [c for c in cols if c in LEAKY_COLUMNS or c.lower().startswith(LEAKY_PREFIXES)]
    if forbidden:
        raise AssertionError(f"leaky false-positive features selected: {forbidden}")
    return cols


def _fit_predict_fold(
    train: pd.DataFrame,
    test: pd.DataFrame,
    feature_cols: list[str],
    target: str,
) -> np.ndarray:
    from sklearn.ensemble import ExtraTreesClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline

    y = pd.to_numeric(train[target], errors="coerce").fillna(0).astype(int)
    if y.nunique() < 2 or int(y.sum()) < 20:
        return np.full(len(test), np.nan)
    model = make_pipeline(
        SimpleImputer(strategy="median"),
        ExtraTreesClassifier(
            n_estimators=300,
            max_depth=5,
            min_samples_leaf=25,
            class_weight="balanced",
            random_state=42 if target == "good_trade" else 43,
            n_jobs=-1,
        ),
    )
    x_train = train[feature_cols].apply(pd.to_numeric, errors="coerce")
    x_test = test[feature_cols].apply(pd.to_numeric, errors="coerce")
    model.fit(x_train, y)
    return model.predict_proba(x_test)[:, 1]


def walk_forward_false_positive_scores(
    rows: pd.DataFrame,
    feature_cols: list[str],
    *,
    target_start: str | pd.Timestamp,
    target_end: str | pd.Timestamp,
    embargo_months: int = 6,
    min_train_rows: int = 500,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Produce OOS good/bad probabilities for each month."""
    if rows.empty:
        return rows.copy(), pd.DataFrame()
    out = rows.copy()
    out["rebalance_date"] = pd.to_datetime(out["rebalance_date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["rebalance_date"]).sort_values(["rebalance_date", "ticker"]).reset_index(drop=True)
    start = pd.Timestamp(target_start).normalize()
    end = pd.Timestamp(target_end).normalize()
    out = out[(out["rebalance_date"] >= start) & (out["rebalance_date"] <= end)].copy()
    out["p_good_oos"] = np.nan
    out["p_bad_oos"] = np.nan

    folds: list[dict[str, Any]] = []
    for day in sorted(out["rebalance_date"].unique()):
        test_day = pd.Timestamp(day).normalize()
        train_end = test_day - pd.DateOffset(months=int(embargo_months))
        train = out[out["rebalance_date"] <= train_end].copy()
        test_mask = out["rebalance_date"] == test_day
        test = out[test_mask].copy()
        if len(train) < int(min_train_rows) or test.empty:
            folds.append({
                "rebalance_date": str(test_day.date()),
                "mode": "insufficient_history",
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "train_end": str(train_end.date()),
            })
            continue
        good = _fit_predict_fold(train, test, feature_cols, "good_trade")
        bad = _fit_predict_fold(train, test, feature_cols, "bad_trade")
        out.loc[test_mask, "p_good_oos"] = good
        out.loc[test_mask, "p_bad_oos"] = bad
        folds.append({
            "rebalance_date": str(test_day.date()),
            "mode": "extra_trees_oos",
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
            "train_end": str(train_end.date()),
            "good_predicted_rows": int(np.isfinite(good).sum()),
            "bad_predicted_rows": int(np.isfinite(bad).sum()),
        })
    return out, pd.DataFrame(folds)


def feature_gap_table(rows: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """Summarize feature means for good, bad, and neutral P_MB candidates."""
    records: list[dict[str, Any]] = []
    good = pd.to_numeric(rows.get("good_trade"), errors="coerce").fillna(0).astype(bool)
    bad = pd.to_numeric(rows.get("bad_trade"), errors="coerce").fillna(0).astype(bool)
    neutral = ~(good | bad)
    for col in feature_cols:
        s = pd.to_numeric(rows[col], errors="coerce")
        if s.notna().sum() < 10:
            continue
        pooled_std = float(s.std()) if float(s.std()) > 0 else np.nan
        good_mean = float(s[good].mean()) if good.any() else np.nan
        bad_mean = float(s[bad].mean()) if bad.any() else np.nan
        records.append({
            "feature": col,
            "observed_rows": int(s.notna().sum()),
            "good_mean": good_mean,
            "bad_mean": bad_mean,
            "neutral_mean": float(s[neutral].mean()) if neutral.any() else np.nan,
            "good_minus_bad": good_mean - bad_mean if np.isfinite(good_mean) and np.isfinite(bad_mean) else np.nan,
            "effect_size": (good_mean - bad_mean) / pooled_std if np.isfinite(pooled_std) else np.nan,
            "bad_missing_rate": float(s[bad].isna().mean()) if bad.any() else np.nan,
        })
    return pd.DataFrame(records).sort_values("effect_size", key=lambda x: x.abs(), ascending=False)


def model_metric_table(scored: pd.DataFrame) -> pd.DataFrame:
    from sklearn.metrics import roc_auc_score

    rows = []
    for score_col, target_col in (
        ("p_good_oos", "good_trade"),
        ("p_bad_oos", "bad_trade"),
        ("p_pre_surge", "good_trade"),
        ("p_pre_surge", "bad_trade"),
        ("p_risk", "bad_trade"),
    ):
        if score_col not in scored.columns or target_col not in scored.columns:
            continue
        score = pd.to_numeric(scored[score_col], errors="coerce")
        target = pd.to_numeric(scored[target_col], errors="coerce").fillna(0).astype(int)
        valid = score.notna()
        if int(valid.sum()) < 100 or target[valid].nunique() < 2:
            continue
        top_mask = valid & (score >= score[valid].quantile(0.75))
        bottom_mask = valid & (score <= score[valid].quantile(0.25))
        rows.append({
            "score": score_col,
            "target": target_col,
            "observed_rows": int(valid.sum()),
            "auc": float(roc_auc_score(target[valid], score[valid])),
            "top_quartile_mean_return": float(pd.to_numeric(scored.loc[top_mask, "analysis_return"], errors="coerce").mean()),
            "bottom_quartile_mean_return": float(pd.to_numeric(scored.loc[bottom_mask, "analysis_return"], errors="coerce").mean()),
            "top_quartile_target_rate": float(target[top_mask].mean()) if int(top_mask.sum()) else np.nan,
            "bottom_quartile_target_rate": float(target[bottom_mask].mean()) if int(bottom_mask.sum()) else np.nan,
        })
    return pd.DataFrame(rows)


def build_false_positive_audit(
    pmb_rows: pd.DataFrame,
    *,
    target_start: str | pd.Timestamp,
    target_end: str | pd.Timestamp,
    embargo_months: int = 6,
    min_train_rows: int = 500,
    nan_threshold: float = 0.50,
    min_observations: int = 200,
) -> dict[str, Any]:
    rows = add_trade_outcome_labels(pmb_rows)
    if rows.empty:
        return {
            "rows": rows,
            "feature_gaps": pd.DataFrame(),
            "oos_scores": pd.DataFrame(),
            "folds": pd.DataFrame(),
            "model_metrics": pd.DataFrame(),
            "summary": {"reason": "no_observed_pmb_rows"},
        }
    feature_cols = select_false_positive_features(
        rows,
        nan_threshold=nan_threshold,
        min_observations=min_observations,
    )
    scored, folds = walk_forward_false_positive_scores(
        rows,
        feature_cols,
        target_start=target_start,
        target_end=target_end,
        embargo_months=embargo_months,
        min_train_rows=min_train_rows,
    )
    metrics = model_metric_table(scored)
    summary = {
        "engine_version": KR_ENGINE_REUSE_VERSION,
        "rows": int(len(rows)),
        "months": int(pd.to_datetime(rows["rebalance_date"]).dt.to_period("M").nunique()),
        "good_trade_rate": float(pd.to_numeric(rows["good_trade"], errors="coerce").mean()),
        "bad_trade_rate": float(pd.to_numeric(rows["bad_trade"], errors="coerce").mean()),
        "feature_count": int(len(feature_cols)),
        "feature_cols": feature_cols,
        "oos_scored_rows": int(pd.to_numeric(scored.get("p_bad_oos"), errors="coerce").notna().sum()),
        "oos_scored_months": int(pd.to_datetime(scored.loc[pd.to_numeric(scored.get("p_bad_oos"), errors="coerce").notna(), "rebalance_date"]).dt.to_period("M").nunique())
        if "p_bad_oos" in scored.columns else 0,
    }
    return {
        "rows": rows,
        "feature_gaps": feature_gap_table(rows, feature_cols),
        "oos_scores": scored,
        "folds": folds,
        "model_metrics": metrics,
        "summary": summary,
    }


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pmb_rows = _read_table(args.pmb_rows)
    audit = build_false_positive_audit(
        pmb_rows,
        target_start=args.target_start,
        target_end=args.target_end,
        embargo_months=args.embargo_months,
        min_train_rows=args.min_train_rows,
        nan_threshold=args.nan_threshold,
        min_observations=args.min_observations,
    )
    paths = {
        "feature_gaps": out_dir / "feature_gaps.csv",
        "oos_scores": out_dir / "oos_scores.parquet",
        "folds": out_dir / "folds.csv",
        "model_metrics": out_dir / "model_metrics.csv",
        "summary": out_dir / "summary.json",
    }
    audit["feature_gaps"].to_csv(paths["feature_gaps"], index=False, encoding="utf-8-sig")
    audit["oos_scores"].to_parquet(paths["oos_scores"], index=False)
    audit["folds"].to_csv(paths["folds"], index=False, encoding="utf-8-sig")
    audit["model_metrics"].to_csv(paths["model_metrics"], index=False, encoding="utf-8-sig")
    payload = dict(audit["summary"])
    payload.update({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pmb_rows": str(args.pmb_rows),
        "out_dir": str(out_dir),
        "outputs": {k: str(v) for k, v in paths.items()},
    })
    paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    if not audit["model_metrics"].empty:
        print(audit["model_metrics"].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
