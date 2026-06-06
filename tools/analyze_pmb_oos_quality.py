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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze KR1000 P_MB OOS pick quality")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--end", default="2026-06-04")
    p.add_argument("--scored-panel", default=str(DEFAULT_SCORED_PANEL))
    p.add_argument("--pmb-oos-picks", default=str(DEFAULT_PMB_OOS))
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


def _summarise(g: pd.DataFrame) -> dict[str, Any]:
    r = pd.to_numeric(g.get("forward_return_1m"), errors="coerce")
    m = pd.to_numeric(g.get("forward_min_return_1m"), errors="coerce")
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


def _group_summary(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, g in df.groupby(group_cols, observed=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {col: key for col, key in zip(group_cols, keys)}
        row.update(_summarise(g))
        rows.append(row)
    return pd.DataFrame(rows)


def _factor_correlations(df: pd.DataFrame) -> pd.DataFrame:
    target = pd.to_numeric(df.get("forward_return_1m"), errors="coerce")
    cols = [
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
            "corr_forward_return_1m": float(s[valid].corr(target[valid])),
            "top_quartile_mean_1m": float(target[valid & (s >= s[valid].quantile(0.75))].mean()),
            "bottom_quartile_mean_1m": float(target[valid & (s <= s[valid].quantile(0.25))].mean()),
        })
    return pd.DataFrame(rows)


def _filter_summary(df: pd.DataFrame) -> pd.DataFrame:
    rank = pd.to_numeric(df.get("pmb_oos_rank"), errors="coerce")
    rs3 = pd.to_numeric(df.get("rs_3m"), errors="coerce").fillna(0.0)
    tech = pd.to_numeric(df.get("technical_score"), errors="coerce").fillna(0.0)
    bench3 = pd.to_numeric(df.get("bench_ret_3m"), errors="coerce").fillna(0.0)
    trend_pass = pd.to_numeric(df.get("trend_template_pass"), errors="coerce").fillna(0.0) > 0
    mcap = pd.to_numeric(df.get("market_cap"), errors="coerce")
    large = mcap >= mcap.median()
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
    }
    rows = []
    for name, mask in filters.items():
        row = {"filter": name}
        row.update(_summarise(df[mask]))
        rows.append(row)
    return pd.DataFrame(rows)


def build_quality_report(
    scored_panel: pd.DataFrame,
    pmb_oos_picks: str | Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
    refresh_days: int,
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
    report = build_quality_report(raw, args.pmb_oos_picks, start, end, args.refresh_days)
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
        "pmb_rows": int(len(report["pmb_rows"])),
        "observed_forward_rows": int(pd.to_numeric(report["pmb_rows"].get("forward_return_1m"), errors="coerce").notna().sum()),
        "months": int(pd.to_datetime(report["pmb_rows"]["rebalance_date"]).dt.to_period("M").nunique()),
        "outputs": paths,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
