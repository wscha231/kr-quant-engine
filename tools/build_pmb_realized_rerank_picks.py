"""Build PIT-safe P_MB picks reranked by realized holding-return history.

This is a research sidecar for the KR1000 broker-ledger gate. It starts from
purged P_MB OOS picks, then reranks each month using only earlier realized
next-rebalance outcomes. The output keeps the same sparse picks schema accepted
by tools/run_kr1000_backtest.py.

Example:
    py -3 tools/build_pmb_realized_rerank_picks.py \
        --pmb-rows outputs/pmb_oos_quality_realized_preentry_2018_20260604/pmb_rows.parquet \
        --target-start 2018-01-01 \
        --target-end 2026-06-04 \
        --out outputs/p_mb_oos_picks_realized_rerank_latest.csv
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
from tools.build_pmb_oos_picks import audit_pmb_oos_coverage  # noqa: E402


DEFAULT_PMB_ROWS = PROJECT_ROOT / "outputs" / "pmb_oos_quality_realized_preentry_2018_20260604" / "pmb_rows.parquet"
DEFAULT_OUT = DATA_ROOT / "outputs" / "p_mb_oos_picks_realized_rerank_latest.csv"

FEATURE_CANDIDATES = (
    "p_pre_entry",
    "p_continuation",
    "p_risk",
    "p_combined",
    "p_pre_surge",
    "pmb_oos_rank",
    "market_cap",
    "avg_trading_value_60d",
    "rs_1m",
    "rs_3m",
    "rs_6m",
    "rs_score",
    "technical_score",
    "trend_template_score",
    "trend_template_pass",
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "bench_ret_1m",
    "bench_ret_3m",
    "bench_ret_6m",
    "flow_score",
    "quality_growth_score",
    "valuation_score",
    "theme_sector_score",
    "event_governance_score",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build realized-history reranked P_MB OOS picks")
    p.add_argument("--pmb-rows", default=str(DEFAULT_PMB_ROWS),
                   help="Parquet/CSV from tools/analyze_pmb_oos_quality.py with realized returns.")
    p.add_argument("--target-start", default="2018-01-01")
    p.add_argument("--target-end", default="2026-06-04")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--audit-json", default=None,
                   help="Default=<out>.coverage.json")
    p.add_argument("--k-per-month", type=int, default=30)
    p.add_argument("--min-picks-per-month", type=int, default=20)
    p.add_argument("--embargo-months", type=int, default=3)
    p.add_argument("--min-train-rows", type=int, default=240)
    p.add_argument("--risk-penalty", type=float, default=0.12)
    p.add_argument("--fail-on-coverage-gap", action="store_true")
    return p.parse_args()


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p, dtype={"ticker": str})
    return pd.read_parquet(p)


def _normalise_ticker(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def _feature_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for col in FEATURE_CANDIDATES:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().sum() >= 20 and s.nunique(dropna=True) > 1:
            cols.append(col)
    return cols


def _fit_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    feature_cols: list[str],
    risk_penalty: float,
) -> pd.DataFrame:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import RobustScaler

    out = test.copy()
    usable_cols = []
    for col in feature_cols:
        s = pd.to_numeric(train[col], errors="coerce")
        if s.notna().sum() >= 10 and s.nunique(dropna=True) > 1:
            usable_cols.append(col)
    if not usable_cols:
        out["p_realized_pred_return"] = 0.0
        out["p_realized_pred_loss"] = 0.0
        out["p_realized_score_raw"] = pd.to_numeric(out.get("p_pre_surge"), errors="coerce").fillna(0.0)
        return out

    x_train = train[usable_cols].apply(pd.to_numeric, errors="coerce")
    x_test = test[usable_cols].apply(pd.to_numeric, errors="coerce")
    y_ret = pd.to_numeric(train["analysis_return"], errors="coerce").clip(-0.35, 0.60)
    y_min = pd.to_numeric(train.get("analysis_min_return"), errors="coerce")
    y_loss = ((y_ret <= -0.10) | (y_min <= -0.15)).astype(int)

    ret_model = make_pipeline(
        SimpleImputer(strategy="median"),
        RobustScaler(with_centering=True, quantile_range=(10, 90)),
        Ridge(alpha=5.0),
    )
    ret_model.fit(x_train, y_ret)
    pred_ret = ret_model.predict(x_test)

    if y_loss.nunique() > 1 and int(y_loss.sum()) >= 10:
        loss_model = make_pipeline(
            SimpleImputer(strategy="median"),
            RobustScaler(with_centering=True, quantile_range=(10, 90)),
            LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs"),
        )
        loss_model.fit(x_train, y_loss)
        pred_loss = loss_model.predict_proba(x_test)[:, 1]
    else:
        pred_loss = np.full(len(test), float(y_loss.mean()) if len(y_loss) else 0.0)

    out["p_realized_pred_return"] = pred_ret
    out["p_realized_pred_loss"] = pred_loss
    out["p_realized_score_raw"] = pred_ret - float(risk_penalty) * pred_loss
    return out


def build_realized_rerank_picks(
    pmb_rows: pd.DataFrame,
    *,
    target_start: str | pd.Timestamp,
    target_end: str | pd.Timestamp,
    k_per_month: int = 30,
    embargo_months: int = 3,
    min_train_rows: int = 240,
    risk_penalty: float = 0.12,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    df = pmb_rows.copy()
    if df.empty:
        return pd.DataFrame(), {"reason": "empty_pmb_rows"}
    df["rebalance_date"] = pd.to_datetime(df["rebalance_date"], errors="coerce").dt.normalize()
    df["ticker"] = _normalise_ticker(df["ticker"])
    df = df.dropna(subset=["rebalance_date", "ticker"]).copy()
    start = pd.Timestamp(target_start).normalize()
    end = pd.Timestamp(target_end).normalize()
    df = df[(df["rebalance_date"] >= start) & (df["rebalance_date"] <= end)].copy()
    for col in ("analysis_return", "analysis_min_return", "p_pre_surge", "p_pre_entry", "p_continuation", "p_risk", "p_combined"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    feature_cols = _feature_columns(df)

    rows: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    months = sorted(df["rebalance_date"].dropna().unique())
    for month in months:
        test_day = pd.Timestamp(month).normalize()
        train_end = test_day - pd.DateOffset(months=int(embargo_months))
        train = df[
            (df["rebalance_date"] <= train_end)
            & pd.to_numeric(df["analysis_return"], errors="coerce").notna()
        ].copy()
        test = df[df["rebalance_date"] == test_day].copy()
        if test.empty:
            continue
        if len(train) >= int(min_train_rows):
            scored = _fit_predict(train, test, feature_cols, risk_penalty)
            mode = "realized_model"
        else:
            scored = test.copy()
            scored["p_realized_pred_return"] = np.nan
            scored["p_realized_pred_loss"] = np.nan
            scored["p_realized_score_raw"] = pd.to_numeric(scored.get("p_pre_surge"), errors="coerce").fillna(0.0)
            mode = "fallback_p_pre_surge"
        scored["p_realized_score_raw"] = pd.to_numeric(scored["p_realized_score_raw"], errors="coerce").replace(
            [np.inf, -np.inf], np.nan,
        ).fillna(-999.0)
        scored = scored.sort_values(
            ["p_realized_score_raw", "p_pre_surge", "market_cap"],
            ascending=[False, False, False],
        ).head(int(k_per_month)).copy()
        if scored.empty:
            continue
        score = scored["p_realized_score_raw"]
        if score.nunique(dropna=True) <= 1:
            scored["p_pre_surge"] = 1.0
        else:
            scored["p_pre_surge"] = score.rank(pct=True, method="average")
        scored["rank_in_month"] = np.arange(1, len(scored) + 1)
        scored["rerank_mode"] = mode
        rows.append(scored)
        fold_rows.append({
            "rebalance_date": str(test_day.date()),
            "mode": mode,
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
            "selected_rows": int(len(scored)),
            "train_end": str(pd.Timestamp(train_end).date()),
        })

    if not rows:
        return pd.DataFrame(), {
            "reason": "no_months_selected",
            "feature_cols": feature_cols,
            "folds": fold_rows,
        }
    out = pd.concat(rows, ignore_index=True)
    keep = [
        "rebalance_date",
        "ticker",
        "name",
        "market_cap",
        "p_pre_entry",
        "p_continuation",
        "p_risk",
        "p_combined",
        "p_realized_pred_return",
        "p_realized_pred_loss",
        "p_realized_score_raw",
        "p_pre_surge",
        "rank_in_month",
        "rerank_mode",
    ]
    keep = [c for c in keep if c in out.columns]
    audit = {
        "engine_version": KR_ENGINE_REUSE_VERSION,
        "feature_count": int(len(feature_cols)),
        "feature_cols": feature_cols,
        "k_per_month": int(k_per_month),
        "embargo_months": int(embargo_months),
        "min_train_rows": int(min_train_rows),
        "risk_penalty": float(risk_penalty),
        "folds": fold_rows,
    }
    return out[keep].sort_values(["rebalance_date", "rank_in_month"]).reset_index(drop=True), audit


def main() -> int:
    args = parse_args()
    pmb_rows = _read_table(args.pmb_rows)
    picks, build_audit = build_realized_rerank_picks(
        pmb_rows,
        target_start=args.target_start,
        target_end=args.target_end,
        k_per_month=args.k_per_month,
        embargo_months=args.embargo_months,
        min_train_rows=args.min_train_rows,
        risk_penalty=args.risk_penalty,
    )
    out_path = Path(args.out)
    audit_path = Path(args.audit_json) if args.audit_json else out_path.with_suffix(".coverage.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    if picks.empty:
        pd.DataFrame(columns=["rebalance_date", "ticker", "p_pre_surge", "rank_in_month"]).to_csv(out_path, index=False)
    else:
        picks.to_csv(out_path, index=False, encoding="utf-8-sig")
    coverage = audit_pmb_oos_coverage(
        picks,
        target_start=args.target_start,
        target_end=args.target_end,
        min_picks_per_month=args.min_picks_per_month,
    )
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pmb_rows": str(args.pmb_rows),
        "out": str(out_path),
        "build": build_audit,
        "coverage": coverage,
    }
    audit_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print("P_MB realized rerank picks")
    print(f"  rows:      {len(picks)}")
    if not picks.empty:
        print(f"  window:    {pd.to_datetime(picks['rebalance_date']).min().date()} .. {pd.to_datetime(picks['rebalance_date']).max().date()}")
        print(f"  modes:     {picks['rerank_mode'].value_counts().to_dict() if 'rerank_mode' in picks.columns else {}}")
    print(f"  features:  {build_audit.get('feature_count')}")
    print(f"  coverage:  {coverage.get('status')} ({coverage.get('covered_months')}/{coverage.get('expected_months')} months)")
    print(f"  out:       {out_path}")
    print(f"  audit:     {audit_path}")
    if args.fail_on_coverage_gap and not coverage.get("pass"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
