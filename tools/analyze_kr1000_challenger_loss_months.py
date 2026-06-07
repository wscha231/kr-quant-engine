"""Analyze months where the KR1000 production challenger loses to KOSPI200.

The active gap is signal quality, not the broker ledger. This diagnostic keeps
that work concrete by listing underperforming months with cash exposure,
holdings concentration, turnover, fees, and benchmark-relative return.
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

from tools.run_kr1000_backtest import _benchmark_returns  # noqa: E402


DEFAULT_RUN_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "kr1000_bt_technical_value_mcap_regime_top10_cap10_g100_no_ladder_official_runner_2018_20260604"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze KR1000 challenger benchmark loss months")
    p.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR),
                   help="Backtest output directory containing daily NAV/holdings/trades.")
    p.add_argument("--daily-nav", default=None)
    p.add_argument("--holdings-daily", default=None)
    p.add_argument("--trades", default=None)
    p.add_argument("--out-dir", default="outputs/kr1000_challenger_loss_months")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--worst-count", type=int, default=20)
    p.add_argument("--refresh-days", type=int, default=30)
    return p.parse_args()


def _read_csv(path: str | Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p)


def _month_end(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.to_period("M").dt.to_timestamp("M")


def _top_holdings_by_month(holdings: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    if holdings.empty or not {"date", "ticker", "weight"}.issubset(holdings.columns):
        return pd.DataFrame(columns=["month", "top_holdings"])
    h = holdings.copy()
    h["date"] = pd.to_datetime(h["date"], errors="coerce")
    h["ticker"] = h["ticker"].astype(str).str.extract(r"(\d+)", expand=False).fillna("").str.zfill(6)
    h["month"] = _month_end(h["date"])
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce").fillna(0.0)
    grouped = (
        h.groupby(["month", "ticker"], as_index=False)["weight"]
        .mean()
        .sort_values(["month", "weight"], ascending=[True, False])
    )
    rows: list[dict[str, Any]] = []
    for month, g in grouped.groupby("month"):
        items = [f"{r['ticker']}:{float(r['weight']):.1%}" for _, r in g.head(int(top_n)).iterrows()]
        rows.append({"month": month, "top_holdings": "; ".join(items)})
    return pd.DataFrame(rows)


def _trade_stats_by_month(trades: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "month", "n_trades", "n_buys", "n_sells", "trade_value_abs",
        "fees_krw", "top_trade_reasons",
    ]
    if trades.empty or "execution_date" not in trades.columns:
        return pd.DataFrame(columns=cols)
    t = trades.copy()
    t["execution_date"] = pd.to_datetime(t["execution_date"], errors="coerce")
    t = t.dropna(subset=["execution_date"])
    if t.empty:
        return pd.DataFrame(columns=cols)
    t["month"] = _month_end(t["execution_date"])
    t["trade_value"] = pd.to_numeric(t.get("trade_value", 0.0), errors="coerce").fillna(0.0)
    t["fee_krw"] = pd.to_numeric(t.get("fee_krw", 0.0), errors="coerce").fillna(0.0)
    t["side"] = t.get("side", "").astype(str).str.upper()
    rows: list[dict[str, Any]] = []
    for month, g in t.groupby("month"):
        reasons = g.get("reason_code", pd.Series(dtype=str)).astype(str).value_counts().head(3)
        rows.append({
            "month": month,
            "n_trades": int(len(g)),
            "n_buys": int((g["side"] == "BUY").sum()),
            "n_sells": int((g["side"] == "SELL").sum()),
            "trade_value_abs": float(g["trade_value"].abs().sum()),
            "fees_krw": float(g["fee_krw"].sum()),
            "top_trade_reasons": "; ".join(f"{k}:{int(v)}" for k, v in reasons.items()),
        })
    return pd.DataFrame(rows, columns=cols)


def build_monthly_loss_panel(
    daily_nav: pd.DataFrame,
    benchmark_close: pd.Series,
    holdings_daily: pd.DataFrame | None = None,
    trades: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if daily_nav.empty or not {"date", "nav"}.issubset(daily_nav.columns):
        raise ValueError("daily_nav must include date and nav columns")
    nav = daily_nav.copy()
    nav["date"] = pd.to_datetime(nav["date"], errors="coerce").dt.normalize()
    nav["nav"] = pd.to_numeric(nav["nav"], errors="coerce")
    nav = nav.dropna(subset=["date", "nav"]).sort_values("date")
    if nav.empty:
        raise ValueError("daily_nav has no valid rows")
    nav["cash"] = pd.to_numeric(nav.get("cash", 0.0), errors="coerce").fillna(0.0)
    nav["n_holdings"] = pd.to_numeric(nav.get("n_holdings", 0.0), errors="coerce").fillna(0.0)
    nav["portfolio_drawdown"] = pd.to_numeric(nav.get("portfolio_drawdown", 0.0), errors="coerce").fillna(0.0)
    nav["month"] = _month_end(nav["date"])

    month_last = nav.groupby("month", as_index=False).tail(1)[["month", "date", "nav"]]
    month_last["strategy_return"] = month_last["nav"].pct_change().fillna(0.0)
    exposure = nav.assign(cash_weight=np.where(nav["nav"] > 0, nav["cash"] / nav["nav"], np.nan)).groupby("month").agg(
        avg_cash_weight=("cash_weight", "mean"),
        avg_n_holdings=("n_holdings", "mean"),
        min_portfolio_drawdown=("portfolio_drawdown", "min"),
    ).reset_index()

    bench = benchmark_close.dropna().sort_index()
    bench = bench.reindex(pd.DatetimeIndex(nav["date"].unique())).ffill().dropna()
    bench_month = bench.groupby(bench.index.to_period("M").to_timestamp("M")).last().rename("benchmark_close").reset_index()
    bench_month = bench_month.rename(columns={"index": "month"})
    bench_month["benchmark_return"] = bench_month["benchmark_close"].pct_change().fillna(0.0)

    out = month_last.merge(bench_month, on="month", how="left").merge(exposure, on="month", how="left")
    out["active_return"] = pd.to_numeric(out["strategy_return"], errors="coerce").fillna(0.0) - pd.to_numeric(out["benchmark_return"], errors="coerce").fillna(0.0)
    out["underperformed"] = out["active_return"] < 0.0

    top_holdings = _top_holdings_by_month(holdings_daily if holdings_daily is not None else pd.DataFrame())
    trade_stats = _trade_stats_by_month(trades if trades is not None else pd.DataFrame())
    if not top_holdings.empty:
        out = out.merge(top_holdings, on="month", how="left")
    if not trade_stats.empty:
        out = out.merge(trade_stats, on="month", how="left")
    for col in ["top_holdings", "top_trade_reasons"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("")
    for col in ["n_trades", "n_buys", "n_sells", "trade_value_abs", "fees_krw"]:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    return out.sort_values("month").reset_index(drop=True)


def summarize_loss_panel(monthly: pd.DataFrame, worst_count: int = 20) -> dict[str, Any]:
    active = pd.to_numeric(monthly["active_return"], errors="coerce").fillna(0.0)
    under = monthly[monthly["underperformed"]].copy()
    worst = monthly.sort_values("active_return").head(int(worst_count))
    return {
        "months": int(len(monthly)),
        "underperform_months": int(len(under)),
        "underperform_rate": float(len(under) / len(monthly)) if len(monthly) else 0.0,
        "mean_active_return": float(active.mean()) if len(monthly) else 0.0,
        "median_active_return": float(active.median()) if len(monthly) else 0.0,
        "worst_active_return": float(active.min()) if len(monthly) else 0.0,
        "best_active_return": float(active.max()) if len(monthly) else 0.0,
        "avg_cash_weight_underperform": float(pd.to_numeric(under.get("avg_cash_weight", pd.Series(dtype=float)), errors="coerce").mean()) if not under.empty else 0.0,
        "avg_cash_weight_outperform": float(pd.to_numeric(monthly.loc[~monthly["underperformed"], "avg_cash_weight"], errors="coerce").mean()) if len(monthly) != len(under) else 0.0,
        "worst_months": [
            {
                "month": str(pd.Timestamp(r["month"]).date()),
                "strategy_return": float(r["strategy_return"]),
                "benchmark_return": float(r["benchmark_return"]),
                "active_return": float(r["active_return"]),
                "avg_cash_weight": float(r.get("avg_cash_weight", 0.0) or 0.0),
                "top_holdings": str(r.get("top_holdings", "")),
                "top_trade_reasons": str(r.get("top_trade_reasons", "")),
            }
            for _, r in worst.iterrows()
        ],
    }


def write_report(summary: dict[str, Any], monthly: pd.DataFrame, path: Path) -> None:
    lines = [
        "# KR1000 Challenger Loss-Month Diagnostic",
        "",
        "## Summary",
        "",
        f"- Months: `{summary['months']}`",
        f"- Underperform months: `{summary['underperform_months']}`",
        f"- Underperform rate: `{summary['underperform_rate']:.1%}`",
        f"- Mean active return: `{summary['mean_active_return']:.2%}`",
        f"- Median active return: `{summary['median_active_return']:.2%}`",
        f"- Worst active return: `{summary['worst_active_return']:.2%}`",
        f"- Best active return: `{summary['best_active_return']:.2%}`",
        f"- Avg cash weight, underperform months: `{summary['avg_cash_weight_underperform']:.1%}`",
        f"- Avg cash weight, outperform months: `{summary['avg_cash_weight_outperform']:.1%}`",
        "",
        "## Worst Months",
        "",
    ]
    for item in summary["worst_months"]:
        lines.append(
            f"- `{item['month']}` active `{item['active_return']:.2%}` "
            f"(strategy `{item['strategy_return']:.2%}`, benchmark `{item['benchmark_return']:.2%}`, "
            f"cash `{item['avg_cash_weight']:.1%}`)"
        )
        if item["top_holdings"]:
            lines.append(f"  - top holdings: `{item['top_holdings']}`")
        if item["top_trade_reasons"]:
            lines.append(f"  - trade reasons: `{item['top_trade_reasons']}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir)
    daily_nav_path = Path(args.daily_nav) if args.daily_nav else run_dir / "leader_backtest_daily_nav.csv"
    holdings_path = Path(args.holdings_daily) if args.holdings_daily else run_dir / "leader_backtest_holdings_daily.csv"
    trades_path = Path(args.trades) if args.trades else run_dir / "leader_backtest_trades.csv"

    nav = _read_csv(daily_nav_path)
    if nav.empty:
        raise RuntimeError(f"daily NAV not found or empty: {daily_nav_path}")
    nav["date"] = pd.to_datetime(nav["date"], errors="coerce")
    start = pd.Timestamp(args.start).normalize() if args.start else pd.Timestamp(nav["date"].min()).normalize()
    end = pd.Timestamp(args.end).normalize() if args.end else pd.Timestamp(nav["date"].max()).normalize()
    nav = nav[(nav["date"] >= start) & (nav["date"] <= end)].copy()
    holdings = _read_csv(holdings_path)
    trades = _read_csv(trades_path)
    _, benchmark_close = _benchmark_returns(start, end, refresh_days=args.refresh_days)

    monthly = build_monthly_loss_panel(nav, benchmark_close, holdings, trades)
    summary = summarize_loss_panel(monthly, worst_count=args.worst_count)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    monthly_path = out_dir / "challenger_monthly_active_returns.csv"
    worst_path = out_dir / "challenger_worst_months.csv"
    summary_path = out_dir / "challenger_loss_month_summary.json"
    report_path = out_dir / "challenger_loss_month_report.md"
    monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    monthly.sort_values("active_return").head(int(args.worst_count)).to_csv(worst_path, index=False, encoding="utf-8-sig")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, monthly, report_path)

    print("KR1000 challenger loss-month diagnostic")
    print(f"  months:              {summary['months']}")
    print(f"  underperform months: {summary['underperform_months']} ({summary['underperform_rate']:.1%})")
    print(f"  mean active return:  {summary['mean_active_return']:.2%}")
    print(f"  worst active return: {summary['worst_active_return']:.2%}")
    print(f"  monthly:             {monthly_path}")
    print(f"  report:              {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
