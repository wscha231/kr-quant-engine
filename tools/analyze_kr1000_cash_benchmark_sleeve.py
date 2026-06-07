"""Analyze a KOSPI200 proxy sleeve for idle KR1000 broker cash.

This is a research diagnostic, not an official broker-ledger pass. It starts
from an existing stock-only broker backtest daily NAV and estimates what would
happen if a configurable fraction of idle cash were allocated to a KOSPI200
proxy, with a simple trailing benchmark guard and one-way rebalance cost.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.run_kr1000_backtest import _benchmark_returns  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze idle-cash KOSPI200 proxy sleeve")
    p.add_argument("--daily-nav", required=True,
                   help="leader_backtest_daily_nav.csv from a KR1000 broker run.")
    p.add_argument("--out-dir", default="outputs/kr1000_cash_benchmark_sleeve",
                   help="Output directory for metrics/grid/daily CSVs.")
    p.add_argument("--fractions", default="0.25,0.50,0.75,1.00",
                   help="Comma-separated idle-cash fractions to test.")
    p.add_argument("--guards", default="-0.03,-0.05,none",
                   help="Comma-separated 21d KOSPI200 guard thresholds; use none for no guard.")
    p.add_argument("--cost-bp", default="0,2,5,10,31",
                   help="Comma-separated one-way rebalance costs in basis points.")
    p.add_argument("--selected-fraction", type=float, default=0.75)
    p.add_argument("--selected-guard", default="-0.03")
    p.add_argument("--selected-cost-bp", type=float, default=5.0)
    return p.parse_args()


def _parse_float_list(text: str) -> list[float]:
    return [float(x.strip()) for x in str(text).split(",") if x.strip()]


def _parse_guard_list(text: str) -> list[float | None]:
    out: list[float | None] = []
    for token in str(text).split(","):
        t = token.strip().lower()
        if not t:
            continue
        out.append(None if t in {"none", "nan", "off"} else float(t))
    return out


def _max_drawdown(nav: pd.Series) -> float:
    peak = nav.cummax()
    return float((nav / peak - 1.0).min())


def _metrics(daily: pd.DataFrame, benchmark_close: pd.Series) -> dict[str, Any]:
    ret = daily["nav"].pct_change().fillna(0.0)
    years = max((daily["date"].iloc[-1] - daily["date"].iloc[0]).days / 365.25, 1e-9)
    cagr = (float(daily["nav"].iloc[-1]) / float(daily["nav"].iloc[0])) ** (1 / years) - 1.0
    sharpe = float(ret.mean() / ret.std() * math.sqrt(252)) if ret.std() > 0 else 0.0
    bench = benchmark_close.reindex(pd.DatetimeIndex(daily["date"])).ffill().bfill().reset_index(drop=True)
    bench_ret = bench.pct_change().fillna(0.0)
    bench_cagr = (float(bench.iloc[-1]) / float(bench.iloc[0])) ** (1 / years) - 1.0
    active = ret.reset_index(drop=True) - bench_ret
    ir = float(active.mean() / active.std() * math.sqrt(252)) if active.std() > 0 else 0.0
    return {
        "start_date": str(daily["date"].iloc[0].date()),
        "end_date": str(daily["date"].iloc[-1].date()),
        "years": float(years),
        "start_nav": float(daily["nav"].iloc[0]),
        "final_nav": float(daily["nav"].iloc[-1]),
        "cagr": float(cagr),
        "mdd": _max_drawdown(daily["nav"]),
        "benchmark_cagr": float(bench_cagr),
        "excess_cagr": float(cagr - bench_cagr),
        "sharpe": sharpe,
        "information_ratio": ir,
    }


def simulate_cash_benchmark_sleeve(
    base_nav: pd.DataFrame,
    benchmark_close: pd.Series,
    *,
    fraction: float,
    guard_21d: float | None,
    cost_bp: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    n = base_nav.sort_values("date").copy()
    n["date"] = pd.to_datetime(n["date"], errors="coerce").dt.normalize()
    n = n.dropna(subset=["date", "nav", "cash"]).reset_index(drop=True)
    if n.empty:
        raise ValueError("daily NAV is empty")
    bench = benchmark_close.reindex(pd.DatetimeIndex(n["date"])).ffill().bfill().reset_index(drop=True)
    bench_ret = bench.pct_change().fillna(0.0)
    base_ret = pd.to_numeric(n["nav"], errors="coerce").pct_change().fillna(0.0)
    cash_weight = (
        pd.to_numeric(n["cash"], errors="coerce").fillna(0.0)
        / pd.to_numeric(n["nav"], errors="coerce").replace(0, np.nan)
    ).fillna(0.0)

    value = float(n["nav"].iloc[0])
    sleeve_weight = 0.0
    total_rebalance_cost = 0.0
    rows: list[dict[str, Any]] = []
    for idx, row in n.iterrows():
        value *= 1.0 + float(base_ret.iloc[idx]) + sleeve_weight * float(bench_ret.iloc[idx])
        desired = float(cash_weight.iloc[idx]) * float(fraction)
        if guard_21d is not None:
            if idx < 21:
                desired = 0.0
            else:
                bench_21d = float(bench.iloc[idx] / bench.iloc[idx - 21] - 1.0)
                if bench_21d < float(guard_21d):
                    desired = 0.0
        change = abs(desired - sleeve_weight)
        rebalance_cost = value * change * float(cost_bp) / 10000.0
        value -= rebalance_cost
        total_rebalance_cost += rebalance_cost
        sleeve_weight = desired
        rows.append({
            "date": row["date"],
            "nav": value,
            "base_nav": float(row["nav"]),
            "base_cash_weight": float(cash_weight.iloc[idx]),
            "benchmark_sleeve_weight": sleeve_weight,
            "benchmark_return": float(bench_ret.iloc[idx]),
            "rebalance_cost_krw": float(rebalance_cost),
        })
    daily = pd.DataFrame(rows)
    metrics = _metrics(daily, benchmark_close)
    metrics.update({
        "cash_benchmark_fraction": float(fraction),
        "guard_21d": None if guard_21d is None else float(guard_21d),
        "cost_bp": float(cost_bp),
        "avg_benchmark_sleeve_weight": float(daily["benchmark_sleeve_weight"].mean()),
        "total_rebalance_cost_krw": float(total_rebalance_cost),
        "diagnostic_only": True,
        "official_broker_ledger_metric": False,
    })
    return daily, metrics


def main() -> int:
    args = parse_args()
    nav_path = Path(args.daily_nav)
    nav = pd.read_csv(nav_path, parse_dates=["date"])
    _, benchmark_close = _benchmark_returns(nav["date"].min(), nav["date"].max(), refresh_days=30)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    grid_rows: list[dict[str, Any]] = []
    for fraction in _parse_float_list(args.fractions):
        for guard in _parse_guard_list(args.guards):
            for cost_bp in _parse_float_list(args.cost_bp):
                _, metrics = simulate_cash_benchmark_sleeve(
                    nav,
                    benchmark_close,
                    fraction=fraction,
                    guard_21d=guard,
                    cost_bp=cost_bp,
                )
                grid_rows.append(metrics)
    grid = pd.DataFrame(grid_rows).sort_values("cagr", ascending=False)
    grid_path = out_dir / "cash_benchmark_sleeve_grid.csv"
    grid.to_csv(grid_path, index=False, encoding="utf-8-sig")

    selected_guard = None if str(args.selected_guard).strip().lower() in {"none", "nan", "off"} else float(args.selected_guard)
    daily, selected = simulate_cash_benchmark_sleeve(
        nav,
        benchmark_close,
        fraction=args.selected_fraction,
        guard_21d=selected_guard,
        cost_bp=args.selected_cost_bp,
    )
    daily_path = out_dir / "cash_benchmark_sleeve_daily.csv"
    metrics_path = out_dir / "cash_benchmark_sleeve_metrics.json"
    daily.to_csv(daily_path, index=False, encoding="utf-8-sig")
    metrics_path.write_text(json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8")

    print("KR1000 cash benchmark sleeve diagnostic")
    print(f"  grid:       {grid_path}")
    print(f"  daily:      {daily_path}")
    print(f"  metrics:    {metrics_path}")
    print(f"  selected:   CAGR={selected['cagr']:.2%}, MDD={selected['mdd']:.2%}, IR={selected['information_ratio']:.3f}")
    print("  status:     diagnostic_only_not_official_broker_ledger")
    return 0


if __name__ == "__main__":
    sys.exit(main())
