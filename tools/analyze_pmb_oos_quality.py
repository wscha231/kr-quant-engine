"""Analyze PIT-safe P_MB OOS pick quality by year, rank, and filters.

This is a diagnostic companion to the KR1000 broker-ledger gate. It does not
produce official CAGR/MDD metrics; it explains whether the purged P_MB picks
have forward-return edge before portfolio construction.

Example:
    py -3 tools/analyze_pmb_oos_quality.py --start 2018-01-01 --end 2026-06-04
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, kr1000_leader_alpha_cfg  # noqa: E402
from tools.run_kr1000_backtest import prepare_kr1000_scored_panel  # noqa: E402


DEFAULT_SCORED_PANEL = DATA_ROOT / "outputs" / "scored_panel_v0_2016_20260604_forward_labels.parquet"
DEFAULT_PMB_OOS = DATA_ROOT / "outputs" / "p_mb_oos_picks_purged_3sleeve_2018_20260604_latest.csv"
DEFAULT_PRICE_PANEL = (
    DATA_ROOT
    / "outputs"
    / "kr1000_bt_2018_20260604_component_ab_top20_price_panel_after_component_fix.parquet"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze KR1000 P_MB OOS pick quality")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--end", default="2026-06-04")
    p.add_argument("--scored-panel", default=str(DEFAULT_SCORED_PANEL))
    p.add_argument("--pmb-oos-picks", default=str(DEFAULT_PMB_OOS))
    p.add_argument(
        "--price-panel",
        default=str(DEFAULT_PRICE_PANEL) if DEFAULT_PRICE_PANEL.exists() else "",
        help=(
            "Optional daily price panel. When provided, the report uses "
            "next-rebalance realized returns instead of sparse forward labels."
        ),
    )
    p.add_argument("--out-dir", default=str(DATA_ROOT / "outputs" / "pmb_oos_quality"))
    p.add_argument("--refresh-days", type=int, default=3650)
    return p.parse_args()


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p, dtype={"ticker": str})
    return pd.read_parquet(p)


def _normalise_ticker(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def _summarise(
    g: pd.DataFrame,
    return_col: str = "analysis_return",
    min_col: str = "analysis_min_return",
) -> dict[str, Any]:
    if return_col not in g.columns:
        return_col = "forward_return_1m"
    if min_col not in g.columns:
        min_col = "forward_min_return_1m"
    r = pd.to_numeric(g.get(return_col), errors="coerce")
    m = pd.to_numeric(g.get(min_col), errors="coerce")
    return {
        "rows": int(len(g)),
        "observed_rows": int(r.notna().sum()),
        "mean_1m": float(r.mean()) if r.notna().any() else None,
        "median_1m": float(r.median()) if r.notna().any() else None,
        "hit_gt0": float((r > 0).mean()) if r.notna().any() else None,
        "hit_gt10": float((r > 0.10).mean()) if r.notna().any() else None,
        "loss_lt10": float((r < -0.10).mean()) if r.notna().any() else None,
        "avg_min_1m": float(m.mean()) if m.notna().any() else None,
        "worst_min_1m": float(m.min()) if m.notna().any() else None,
        "avg_p_pre_surge": float(pd.to_numeric(g.get("p_pre_surge"), errors="coerce").mean()),
        "avg_rs_3m": float(pd.to_numeric(g.get("rs_3m"), errors="coerce").mean()),
        "avg_rs_score": float(pd.to_numeric(g.get("rs_score"), errors="coerce").mean()),
        "avg_technical_score": float(pd.to_numeric(g.get("technical_score"), errors="coerce").mean()),
    }


def _group_summary(
    df: pd.DataFrame,
    group_cols: list[str],
    return_col: str = "analysis_return",
    min_col: str = "analysis_min_return",
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, g in df.groupby(group_cols, observed=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {col: key for col, key in zip(group_cols, keys)}
        row.update(_summarise(g, return_col, min_col))
        rows.append(row)
    return pd.DataFrame(rows)


def _factor_correlations(df: pd.DataFrame, return_col: str = "analysis_return") -> pd.DataFrame:
    if return_col not in df.columns:
        return_col = "forward_return_1m"
    target = pd.to_numeric(df.get(return_col), errors="coerce")
    cols = [
        "p_pre_entry",
        "p_continuation",
        "p_risk",
        "p_combined",
        "p_pre_surge",
        "pmb_oos_rank",
        "rs_1m",
        "rs_3m",
        "rs_6m",
        "rs_score",
        "technical_score",
        "trend_template_score",
        "ret_3m",
        "ret_6m",
        "market_cap",
        "avg_trading_value_60d",
        "p0_momentum_score",
        "p1_blended_score",
    ]
    rows = []
    for col in cols:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        valid = s.notna() & target.notna()
        if valid.sum() < 10:
            continue
        rows.append({
            "factor": col,
            "observed_rows": int(valid.sum()),
            f"corr_{return_col}": float(s[valid].corr(target[valid])),
            f"top_quartile_mean_{return_col}": float(target[valid & (s >= s[valid].quantile(0.75))].mean()),
            f"bottom_quartile_mean_{return_col}": float(target[valid & (s <= s[valid].quantile(0.25))].mean()),
        })
    return pd.DataFrame(rows)


def _filter_summary(
    df: pd.DataFrame,
    return_col: str = "analysis_return",
    min_col: str = "analysis_min_return",
) -> pd.DataFrame:
    def num(col: str, default: float = np.nan) -> pd.Series:
        if col not in df.columns:
            return pd.Series(default, index=df.index, dtype=float)
        return pd.to_numeric(df[col], errors="coerce")

    rank = num("pmb_oos_rank")
    pre_entry = num("p_pre_entry", 0.0).fillna(0.0)
    continuation = num("p_continuation", 0.0).fillna(0.0).clip(lower=0.0, upper=0.95)
    risk = num("p_risk", 0.0).fillna(0.0).clip(lower=0.0, upper=0.95)
    defensive_pre_entry = pre_entry * (1.0 - continuation) * (1.0 - risk)
    rs3 = num("rs_3m", 0.0).fillna(0.0)
    tech = num("technical_score", 0.0).fillna(0.0)
    bench3 = num("bench_ret_3m", 0.0).fillna(0.0)
    trend_pass = num("trend_template_pass", 0.0).fillna(0.0) > 0
    mcap = num("market_cap")
    large = mcap >= mcap.median()
    pre_hi = pre_entry >= pre_entry[pre_entry > 0].median()
    def_hi = defensive_pre_entry >= defensive_pre_entry[defensive_pre_entry > 0].median()
    filters = {
        "all_pmb_oos": pd.Series(True, index=df.index),
        "rank_7_23": rank.between(7, 23, inclusive="both"),
        "bench_3m_pos": bench3 > 0,
        "rank_7_23_bench_3m_pos": rank.between(7, 23, inclusive="both") & (bench3 > 0),
        "rs_3m_pos": rs3 > 0,
        "rs_3m_nonpos": rs3 <= 0,
        "technical_pos": tech > 0,
        "trend_template_pass": trend_pass,
        "largecap_half": large.fillna(False),
        "rank_7_23_largecap_half": rank.between(7, 23, inclusive="both") & large.fillna(False),
        "pre_entry_high": pre_hi.fillna(False),
        "pre_entry_high_largecap_half": pre_hi.fillna(False) & large.fillna(False),
        "pre_entry_defensive_high": def_hi.fillna(False),
        "pre_entry_defensive_high_bench_3m_pos": def_hi.fillna(False) & (bench3 > 0),
        "pre_entry_defensive_high_large_bench_3m_pos": def_hi.fillna(False) & large.fillna(False) & (bench3 > 0),
    }
    rows = []
    for name, mask in filters.items():
        row = {"filter": name}
        row.update(_summarise(df[mask], return_col, min_col))
        rows.append(row)
    return pd.DataFrame(rows)


def _add_realized_holding_returns(pmb: pd.DataFrame, price_panel: pd.DataFrame) -> pd.DataFrame:
    """Add next-rebalance realized returns using broker-like next-close timing.

    Signal date is after close. Entry is the next available close for the
    ticker after `rebalance_date`; exit is the next available close after the
    next monthly signal date. This is a signal-quality diagnostic, not the
    official portfolio ledger.
    """
    if pmb.empty or price_panel.empty:
        return pmb.copy()
    out = pmb.copy()
    px = price_panel.copy()
    px["ticker"] = _normalise_ticker(px["ticker"])
    px["date"] = pd.to_datetime(px["date"], errors="coerce").dt.normalize()
    px["close"] = pd.to_numeric(px.get("close"), errors="coerce")
    px = px.dropna(subset=["date", "ticker", "close"]).sort_values(["ticker", "date"])
    signal_days = sorted(pd.to_datetime(out["rebalance_date"], errors="coerce").dropna().dt.normalize().unique())
    next_signal = {
        pd.Timestamp(day).normalize(): (
            pd.Timestamp(signal_days[i + 1]).normalize() if i + 1 < len(signal_days) else None
        )
        for i, day in enumerate(signal_days)
    }
    by_ticker = {tk: g[["date", "close"]].sort_values("date").reset_index(drop=True) for tk, g in px.groupby("ticker")}

    rows: list[dict[str, Any]] = []
    for _, row in out.iterrows():
        ticker = str(row["ticker"])
        signal_day = pd.Timestamp(row["rebalance_date"]).normalize()
        exit_signal = next_signal.get(signal_day)
        hist = by_ticker.get(ticker)
        if hist is None or hist.empty or exit_signal is None:
            rows.append({})
            continue
        entry = hist[hist["date"] > signal_day]
        exit_ = hist[hist["date"] > exit_signal]
        if entry.empty or exit_.empty:
            rows.append({})
            continue
        entry_row = entry.iloc[0]
        exit_row = exit_.iloc[0]
        entry_date = pd.Timestamp(entry_row["date"]).normalize()
        exit_date = pd.Timestamp(exit_row["date"]).normalize()
        entry_close = float(entry_row["close"])
        exit_close = float(exit_row["close"])
        if entry_close <= 0 or exit_close <= 0 or exit_date < entry_date:
            rows.append({})
            continue
        window = hist[(hist["date"] >= entry_date) & (hist["date"] <= exit_date)]
        min_close = float(window["close"].min()) if not window.empty else exit_close
        max_close = float(window["close"].max()) if not window.empty else exit_close
        rows.append({
            "realized_entry_date": entry_date,
            "realized_exit_date": exit_date,
            "realized_entry_close": entry_close,
            "realized_exit_close": exit_close,
            "realized_holding_return": exit_close / entry_close - 1.0,
            "realized_min_return": min_close / entry_close - 1.0,
            "realized_max_return": max_close / entry_close - 1.0,
            "realized_holding_days": int((exit_date - entry_date).days),
        })
    realized = pd.DataFrame(rows, index=out.index)
    for col in realized.columns:
        out[col] = realized[col]
    return out


def _choose_analysis_return_source(pmb: pd.DataFrame) -> pd.DataFrame:
    out = pmb.copy()

    def num(col: str) -> pd.Series:
        if col not in out.columns:
            return pd.Series(np.nan, index=out.index, dtype=float)
        return pd.to_numeric(out[col], errors="coerce")

    realized_obs = num("realized_holding_return").notna()
    if realized_obs.any():
        out["analysis_return"] = num("realized_holding_return")
        out["analysis_min_return"] = num("realized_min_return")
        out["analysis_return_source"] = "realized_next_rebalance"
    else:
        out["analysis_return"] = num("forward_return_1m")
        out["analysis_min_return"] = num("forward_min_return_1m")
        out["analysis_return_source"] = "forward_label_1m"
    return out


def build_quality_report(
    scored_panel: pd.DataFrame,
    pmb_oos_picks: str | Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
    refresh_days: int,
    price_panel: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    cfg = kr1000_leader_alpha_cfg({"score_profile": "pmb_pre_surge"})
    scored, _ = prepare_kr1000_scored_panel(
        scored_panel,
        start,
        end,
        cfg,
        refresh_days=refresh_days,
        pmb_oos_picks=pmb_oos_picks,
    )
    pmb = scored[pd.to_numeric(scored.get("p_pre_surge"), errors="coerce").fillna(0.0) > 0].copy()
    pmb["year"] = pd.to_datetime(pmb["rebalance_date"]).dt.year
    pmb["rank_bucket"] = pd.cut(
        pd.to_numeric(pmb.get("pmb_oos_rank"), errors="coerce"),
        bins=[0, 5, 10, 20, 30],
        labels=["r01_05", "r06_10", "r11_20", "r21_30"],
    )
    if price_panel is not None and not price_panel.empty:
        pmb = _add_realized_holding_returns(pmb, price_panel)
    pmb = _choose_analysis_return_source(pmb)
    return {
        "pmb_rows": pmb,
        "by_year": _group_summary(pmb, ["year"]),
        "by_rank_bucket": _group_summary(pmb, ["rank_bucket"]),
        "by_year_rank_bucket": _group_summary(pmb, ["year", "rank_bucket"]),
        "filters": _filter_summary(pmb),
        "factor_correlations": _factor_correlations(pmb),
    }


def main() -> int:
    args = parse_args()
    start = pd.Timestamp(args.start).normalize()
    end = pd.Timestamp(args.end).normalize()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = _read_table(args.scored_panel)
    if "ticker" in raw.columns:
        raw["ticker"] = _normalise_ticker(raw["ticker"])
    price_panel = pd.DataFrame()
    if args.price_panel:
        price_path = Path(args.price_panel)
        if price_path.exists():
            price_panel = _read_table(price_path)
    report = build_quality_report(raw, args.pmb_oos_picks, start, end, args.refresh_days, price_panel)
    paths = {}
    for name, df in report.items():
        if name == "pmb_rows":
            path = out_dir / f"{name}.parquet"
            df.to_parquet(path, index=False)
        else:
            path = out_dir / f"{name}.csv"
            df.to_csv(path, index=False, encoding="utf-8-sig")
        paths[name] = str(path)
    summary = {
        "start": str(start.date()),
        "end": str(end.date()),
        "scored_panel": str(args.scored_panel),
        "pmb_oos_picks": str(args.pmb_oos_picks),
        "price_panel": str(args.price_panel or ""),
        "pmb_rows": int(len(report["pmb_rows"])),
        "observed_forward_rows": int(pd.to_numeric(report["pmb_rows"].get("forward_return_1m"), errors="coerce").notna().sum()),
        "observed_realized_rows": int(pd.to_numeric(report["pmb_rows"].get("realized_holding_return"), errors="coerce").notna().sum()),
        "analysis_return_source": (
            str(report["pmb_rows"]["analysis_return_source"].dropna().iloc[0])
            if "analysis_return_source" in report["pmb_rows"].columns and report["pmb_rows"]["analysis_return_source"].notna().any()
            else ""
        ),
        "months": int(pd.to_datetime(report["pmb_rows"]["rebalance_date"]).dt.to_period("M").nunique()),
        "outputs": paths,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
