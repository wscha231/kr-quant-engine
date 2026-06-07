"""Rerank P_MB OOS picks using broad KR1000 realized outcome history.

This sidecar trains return/loss models on the full scored KR1000 panel with
realized next-rebalance outcomes, then scores only the PIT-safe P_MB OOS
candidates for each month. It is stricter than selected-only reranking because
the loss model sees broad false-positive/ordinary-stock examples.

Example:
    py -3 tools/build_pmb_broad_realized_rerank_picks.py \
        --scored-panel <scored_panel.parquet> \
        --price-panel <daily_price_panel.parquet> \
        --pmb-oos-picks <p_mb_oos_picks.csv> \
        --target-start 2018-01-01 \
        --target-end 2026-06-04 \
        --out outputs/p_mb_oos_picks_broad_realized_rerank_latest.csv
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


DEFAULT_SCORED_PANEL = DATA_ROOT / "outputs" / "scored_panel_v0_2016_20260604_forward_labels.parquet"
DEFAULT_PRICE_PANEL = DATA_ROOT / "outputs" / "kr1000_bt_2018_20260604_component_ab_top20_price_panel_after_component_fix.parquet"
DEFAULT_PMB_OOS = DATA_ROOT / "outputs" / "p_mb_oos_picks_purged_3sleeve_2018_20260604_latest.csv"
DEFAULT_OUT = DATA_ROOT / "outputs" / "p_mb_oos_picks_broad_realized_rerank_latest.csv"

BROAD_FEATURE_CANDIDATES = (
    "market_cap",
    "avg_trading_value_60d",
    "kr1000_liquidity_rank",
    "kr1000_market_cap_rank",
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "ret_12m",
    "ret_12_1m",
    "rs_1m",
    "rs_3m",
    "rs_6m",
    "rs_kospi_3m",
    "rs_kospi_12m",
    "p0_momentum_score",
    "p1_blended_score",
    "flow_score",
    "technical_score",
    "trend_template_score",
    "trend_template_pass",
    "breakout_flag",
    "dist_from_52w_high",
    "volume_zscore_50",
    "atr_pct",
    "quality_growth_score",
    "valuation_score",
    "theme_sector_score",
    "event_governance_score",
    "governance_risk_score",
    "owner_dilution_risk_score",
    "vkospi_level",
    "vkospi_zscore_60d",
    "macro_usd_krw_zscore_60d",
    "foreign_inst_combined_zscore_20d",
    "individual_net_buy_20d_zscore",
)

PMB_OUTPUT_COLUMNS = (
    "p_pre_entry",
    "p_continuation",
    "p_risk",
    "p_combined",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build broad-realized-history reranked P_MB OOS picks")
    p.add_argument("--scored-panel", default=str(DEFAULT_SCORED_PANEL))
    p.add_argument("--price-panel", default=str(DEFAULT_PRICE_PANEL))
    p.add_argument("--pmb-oos-picks", default=str(DEFAULT_PMB_OOS))
    p.add_argument("--target-start", default="2018-01-01")
    p.add_argument("--target-end", default="2026-06-04")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--audit-json", default=None,
                   help="Default=<out>.coverage.json")
    p.add_argument("--k-per-month", type=int, default=30)
    p.add_argument("--min-picks-per-month", type=int, default=20)
    p.add_argument("--embargo-months", type=int, default=3)
    p.add_argument("--min-train-rows", type=int, default=5000)
    p.add_argument("--pmb-weight", type=float, default=0.04)
    p.add_argument("--return-weight", type=float, default=1.0)
    p.add_argument("--risk-penalty", type=float, default=0.10)
    p.add_argument("--fail-on-coverage-gap", action="store_true")
    return p.parse_args()


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p, dtype={"ticker": str})
    return pd.read_parquet(p)


def _normalise_ticker(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def _coerce_bool_mask(s: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(s):
        return s.fillna(False).astype(bool)
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce").fillna(0).ne(0)
    text = s.astype(str).str.strip().str.lower()
    return text.isin({"1", "true", "t", "yes", "y"})


def _prepare_scored_panel(scored_panel: pd.DataFrame) -> pd.DataFrame:
    df = scored_panel.copy()
    date_col = "rebalance_date" if "rebalance_date" in df.columns else "date"
    if date_col not in df.columns:
        raise ValueError("scored_panel must include rebalance_date or date")
    df = df.rename(columns={date_col: "rebalance_date"})
    df["rebalance_date"] = pd.to_datetime(df["rebalance_date"], errors="coerce").dt.normalize()
    df["ticker"] = _normalise_ticker(df["ticker"])
    df = df.dropna(subset=["rebalance_date", "ticker"]).copy()
    if "in_kr1000" in df.columns:
        mask = _coerce_bool_mask(df["in_kr1000"])
        total_months = max(int(df["rebalance_date"].nunique()), 1)
        true_months = int(df.loc[mask, "rebalance_date"].nunique())
        if mask.any() and true_months >= max(3, int(total_months * 0.5)):
            df = df[mask].copy()
    return df.sort_values(["rebalance_date", "ticker"]).reset_index(drop=True)


def _prepare_pmb_picks(pmb_oos_picks: pd.DataFrame) -> pd.DataFrame:
    picks = pmb_oos_picks.copy()
    picks["rebalance_date"] = pd.to_datetime(picks["rebalance_date"], errors="coerce").dt.normalize()
    picks["ticker"] = _normalise_ticker(picks["ticker"])
    for col in ("p_pre_surge", "rank_in_month", "fold_id") + PMB_OUTPUT_COLUMNS:
        if col in picks.columns:
            picks[col] = pd.to_numeric(picks[col], errors="coerce")
    return picks.dropna(subset=["rebalance_date", "ticker"]).copy()


def add_realized_next_rebalance_returns(panel: pd.DataFrame, price_panel: pd.DataFrame) -> pd.DataFrame:
    """Attach next-rebalance realized return and min return without lookahead features.

    Outcomes are labels for model training only. For a signal at month-end D,
    entry is the next available close after D and exit is the next available
    close after the next signal month-end.
    """
    if panel.empty or price_panel.empty:
        return panel.copy()
    out = panel.copy()
    px = price_panel.copy()
    px["ticker"] = _normalise_ticker(px["ticker"])
    px["date"] = pd.to_datetime(px["date"], errors="coerce").dt.normalize()
    px["close"] = pd.to_numeric(px.get("close"), errors="coerce")
    px = px.dropna(subset=["date", "ticker", "close"])
    px = px[px["close"] > 0].sort_values(["ticker", "date"])

    signal_days = sorted(out["rebalance_date"].dropna().unique())
    next_signal = {
        pd.Timestamp(day).normalize(): (
            pd.Timestamp(signal_days[i + 1]).normalize() if i + 1 < len(signal_days) else None
        )
        for i, day in enumerate(signal_days)
    }
    by_ticker = {tk: g[["date", "close"]].sort_values("date").reset_index(drop=True) for tk, g in px.groupby("ticker")}

    realized_return = np.full(len(out), np.nan)
    realized_min = np.full(len(out), np.nan)
    holding_days = np.full(len(out), np.nan)
    grouped = out.groupby("ticker", sort=False).groups
    for ticker, idx in grouped.items():
        hist = by_ticker.get(str(ticker))
        if hist is None or hist.empty:
            continue
        dates = hist["date"].to_numpy(dtype="datetime64[ns]")
        closes = hist["close"].to_numpy(dtype=float)
        for row_pos in idx:
            signal_day = pd.Timestamp(out.at[row_pos, "rebalance_date"]).normalize()
            exit_signal = next_signal.get(signal_day)
            if exit_signal is None:
                continue
            entry_i = int(np.searchsorted(dates, np.datetime64(signal_day), side="right"))
            exit_i = int(np.searchsorted(dates, np.datetime64(exit_signal), side="right"))
            if entry_i >= len(dates) or exit_i >= len(dates) or exit_i < entry_i:
                continue
            entry_close = float(closes[entry_i])
            exit_close = float(closes[exit_i])
            if entry_close <= 0 or exit_close <= 0:
                continue
            window = closes[entry_i:exit_i + 1]
            realized_return[row_pos] = exit_close / entry_close - 1.0
            realized_min[row_pos] = float(np.nanmin(window)) / entry_close - 1.0
            holding_days[row_pos] = int((pd.Timestamp(dates[exit_i]) - pd.Timestamp(dates[entry_i])).days)

    out["analysis_return"] = realized_return
    out["analysis_min_return"] = realized_min
    out["analysis_holding_days"] = holding_days
    return out


def _feature_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for col in BROAD_FEATURE_CANDIDATES:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().sum() >= 100 and s.nunique(dropna=True) > 1:
            cols.append(col)
    return cols


def _rank_pct(s: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if x.notna().sum() <= 1:
        return pd.Series(0.0, index=s.index, dtype=float)
    return x.rank(pct=True, method="average").fillna(0.0)


def _fit_predict_broad(train: pd.DataFrame, test: pd.DataFrame, feature_cols: list[str]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import RobustScaler

    usable = []
    for col in feature_cols:
        s = pd.to_numeric(train[col], errors="coerce")
        if s.notna().sum() >= 100 and s.nunique(dropna=True) > 1:
            usable.append(col)
    if not usable:
        return np.zeros(len(test)), np.zeros(len(test)), []

    x_train = train[usable].apply(pd.to_numeric, errors="coerce")
    x_test = test[usable].apply(pd.to_numeric, errors="coerce")
    y_ret = pd.to_numeric(train["analysis_return"], errors="coerce").clip(-0.35, 0.60)
    y_min = pd.to_numeric(train["analysis_min_return"], errors="coerce")
    y_loss = ((y_ret <= -0.10) | (y_min <= -0.15)).astype(int)

    ret_model = make_pipeline(
        SimpleImputer(strategy="median"),
        RobustScaler(with_centering=True, quantile_range=(10, 90)),
        Ridge(alpha=10.0),
    )
    ret_model.fit(x_train, y_ret)
    pred_ret = ret_model.predict(x_test)

    if y_loss.nunique() > 1 and int(y_loss.sum()) >= 50:
        loss_model = make_pipeline(
            SimpleImputer(strategy="median"),
            RobustScaler(with_centering=True, quantile_range=(10, 90)),
            LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs"),
        )
        loss_model.fit(x_train, y_loss)
        pred_loss = loss_model.predict_proba(x_test)[:, 1]
    else:
        pred_loss = np.full(len(test), float(y_loss.mean()) if len(y_loss) else 0.0)
    return pred_ret, pred_loss, usable


def build_broad_realized_rerank_picks(
    scored_panel: pd.DataFrame,
    price_panel: pd.DataFrame,
    pmb_oos_picks: pd.DataFrame,
    *,
    target_start: str | pd.Timestamp,
    target_end: str | pd.Timestamp,
    k_per_month: int = 30,
    embargo_months: int = 3,
    min_train_rows: int = 5000,
    pmb_weight: float = 0.04,
    return_weight: float = 1.0,
    risk_penalty: float = 0.10,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    start = pd.Timestamp(target_start).normalize()
    end = pd.Timestamp(target_end).normalize()
    panel = _prepare_scored_panel(scored_panel)
    panel = panel[(panel["rebalance_date"] >= start - pd.DateOffset(years=2)) & (panel["rebalance_date"] <= end)].copy()
    panel = panel.reset_index(drop=True)
    panel = add_realized_next_rebalance_returns(panel, price_panel)
    pmb = _prepare_pmb_picks(pmb_oos_picks)
    pmb = pmb[(pmb["rebalance_date"] >= start) & (pmb["rebalance_date"] <= end)].copy()
    if panel.empty or pmb.empty:
        return pd.DataFrame(), {"reason": "empty_panel_or_pmb"}

    feature_cols = _feature_columns(panel)
    rows: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    used_features: set[str] = set()
    months = sorted(pmb["rebalance_date"].dropna().unique())
    for month in months:
        test_day = pd.Timestamp(month).normalize()
        train_end = test_day - pd.DateOffset(months=int(embargo_months))
        train = panel[
            (panel["rebalance_date"] <= train_end)
            & pd.to_numeric(panel["analysis_return"], errors="coerce").notna()
        ].copy()
        test_keys = pmb[pmb["rebalance_date"] == test_day].copy()
        if test_keys.empty:
            continue
        test = test_keys.merge(
            panel.drop(columns=[c for c in PMB_OUTPUT_COLUMNS if c in panel.columns], errors="ignore"),
            on=["rebalance_date", "ticker"],
            how="left",
            suffixes=("", "_panel"),
        )
        if len(train) >= int(min_train_rows):
            pred_ret, pred_loss, usable = _fit_predict_broad(train, test, feature_cols)
            used_features.update(usable)
            mode = "broad_realized_model"
        else:
            pred_ret = np.zeros(len(test))
            pred_loss = np.zeros(len(test))
            mode = "fallback_p_pre_surge"
        test["p_broad_pred_return"] = pred_ret
        test["p_broad_pred_loss"] = pred_loss
        pmb_rank = _rank_pct(test.get("p_pre_surge", pd.Series(0.0, index=test.index)))
        test["p_broad_score_raw"] = (
            float(pmb_weight) * pmb_rank
            + float(return_weight) * pd.Series(pred_ret, index=test.index)
            - float(risk_penalty) * pd.Series(pred_loss, index=test.index)
        )
        test = test.sort_values(
            ["p_broad_score_raw", "p_pre_surge", "market_cap"],
            ascending=[False, False, False],
        ).head(int(k_per_month)).copy()
        if test.empty:
            continue
        raw = test["p_broad_score_raw"]
        test["p_pre_surge"] = raw.rank(pct=True, method="average") if raw.nunique(dropna=True) > 1 else 1.0
        test["rank_in_month"] = np.arange(1, len(test) + 1)
        test["rerank_mode"] = mode
        rows.append(test)
        folds.append({
            "rebalance_date": str(test_day.date()),
            "mode": mode,
            "train_rows": int(len(train)),
            "test_rows": int(len(test_keys)),
            "selected_rows": int(len(test)),
            "train_end": str(pd.Timestamp(train_end).date()),
        })

    if not rows:
        return pd.DataFrame(), {
            "reason": "no_months_selected",
            "feature_cols": feature_cols,
            "folds": folds,
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
        "p_broad_pred_return",
        "p_broad_pred_loss",
        "p_broad_score_raw",
        "p_pre_surge",
        "rank_in_month",
        "rerank_mode",
    ]
    keep = [c for c in keep if c in out.columns]
    audit = {
        "engine_version": KR_ENGINE_REUSE_VERSION,
        "feature_count": int(len(feature_cols)),
        "feature_cols": feature_cols,
        "used_feature_count": int(len(used_features)),
        "used_features": sorted(used_features),
        "k_per_month": int(k_per_month),
        "embargo_months": int(embargo_months),
        "min_train_rows": int(min_train_rows),
        "pmb_weight": float(pmb_weight),
        "return_weight": float(return_weight),
        "risk_penalty": float(risk_penalty),
        "broad_rows": int(len(panel)),
        "broad_observed_rows": int(pd.to_numeric(panel.get("analysis_return"), errors="coerce").notna().sum()),
        "folds": folds,
    }
    return out[keep].sort_values(["rebalance_date", "rank_in_month"]).reset_index(drop=True), audit


def main() -> int:
    args = parse_args()
    scored = _read_table(args.scored_panel)
    prices = _read_table(args.price_panel)
    pmb = _read_table(args.pmb_oos_picks)
    picks, build_audit = build_broad_realized_rerank_picks(
        scored,
        prices,
        pmb,
        target_start=args.target_start,
        target_end=args.target_end,
        k_per_month=args.k_per_month,
        embargo_months=args.embargo_months,
        min_train_rows=args.min_train_rows,
        pmb_weight=args.pmb_weight,
        return_weight=args.return_weight,
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
        "scored_panel": str(args.scored_panel),
        "price_panel": str(args.price_panel),
        "pmb_oos_picks": str(args.pmb_oos_picks),
        "out": str(out_path),
        "build": build_audit,
        "coverage": coverage,
    }
    audit_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("P_MB broad realized rerank picks")
    print(f"  rows:       {len(picks)}")
    if not picks.empty:
        print(f"  window:     {pd.to_datetime(picks['rebalance_date']).min().date()} .. {pd.to_datetime(picks['rebalance_date']).max().date()}")
        print(f"  modes:      {picks['rerank_mode'].value_counts().to_dict() if 'rerank_mode' in picks.columns else {}}")
    print(f"  broad obs:  {build_audit.get('broad_observed_rows')}/{build_audit.get('broad_rows')}")
    print(f"  features:   {build_audit.get('used_feature_count')}/{build_audit.get('feature_count')}")
    print(f"  coverage:   {coverage.get('status')} ({coverage.get('covered_months')}/{coverage.get('expected_months')} months)")
    print(f"  out:        {out_path}")
    print(f"  audit:      {audit_path}")
    if args.fail_on_coverage_gap and not coverage.get("pass"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
