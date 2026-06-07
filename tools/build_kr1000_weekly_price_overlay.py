"""Build a KR1000-wide weekly price/RS overlay from cached ticker histories.

This is a research bridge between monthly PIT scored panels and a future daily
feature store. It keeps monthly fundamentals/flow/governance data point-in-time
by carrying the latest prior monthly row, but refreshes trailing price,
relative-strength, and technical features on weekly signal dates.

It is intentionally explicit about being research output. Official production
metrics still come from the standard broker-ledger backtest over the locked
production scored panel unless this overlay is promoted later.
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
from kr1000_leader import run_event_driven_backtest  # noqa: E402
from tools.run_kr1000_backtest import (  # noqa: E402
    _benchmark_returns,
    _build_ticker_cache_index,
    _read_table,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build KR1000 weekly price overlay panel")
    p.add_argument("--scored-panel", default=None,
                   help="Monthly PIT scored panel. Default=latest DATA_ROOT feature_store scored_panel_v0.")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--out-dir", default="outputs/kr1000_weekly_price_overlay_full_universe")
    p.add_argument("--frequency", default="W-FRI",
                   help="Pandas period frequency used to choose signal dates.")
    p.add_argument("--max-dates", type=int, default=None,
                   help="Optional cap on weekly signal dates for fast research runs.")
    p.add_argument("--max-tickers", type=int, default=None,
                   help="Optional cap on tickers by average liquidity for fast research runs.")
    p.add_argument("--max-cache-files-per-ticker", type=int, default=1,
                   help="Read only the best N overlapping cache files per ticker.")
    p.add_argument("--top-holdings", type=int, default=10)
    p.add_argument("--hold-rank-threshold", type=int, default=20)
    p.add_argument("--single-stock-max-weight", type=float, default=0.10)
    p.add_argument("--gross-exposure", type=float, default=1.0)
    p.add_argument("--hard-stop-loss-pct", type=float, default=0.15)
    p.add_argument("--run-backtest", action="store_true")
    return p.parse_args()


def latest_scored_panel_path() -> Path:
    fs = DATA_ROOT / "feature_store"
    files = sorted(fs.glob("scored_panel_v0_*.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No scored_panel_v0_*.parquet found in {fs}")
    return files[0]


def _normalise_ticker(series: pd.Series) -> pd.Series:
    return series.astype(str).str.extract(r"(\d+)", expand=False).fillna("").str.zfill(6)


def _rank_pct(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").rank(pct=True, method="average").fillna(0.0)


def _numeric_series(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(default, index=frame.index, dtype=float)


def _last_value_at_or_before(series: pd.Series, day: pd.Timestamp) -> float:
    sub = series.loc[series.index <= day]
    if sub.empty:
        return float("nan")
    return float(sub.iloc[-1])


def _trailing_return(series: pd.Series, day: pd.Timestamp, periods: int) -> float:
    sub = series.loc[series.index <= day]
    if len(sub) <= periods:
        return float("nan")
    end = float(sub.iloc[-1])
    start = float(sub.iloc[-1 - periods])
    if not np.isfinite(end) or not np.isfinite(start) or start <= 0:
        return float("nan")
    return end / start - 1.0


def _signal_dates(price_dates: pd.Series, start: pd.Timestamp, end: pd.Timestamp, frequency: str, max_dates: int | None) -> list[pd.Timestamp]:
    dates = pd.Series(pd.to_datetime(price_dates, errors="coerce").dropna().unique()).sort_values()
    dates = dates[(dates >= start) & (dates <= end)]
    if dates.empty:
        return []
    weekly = dates.groupby(dates.dt.to_period(frequency)).max().tolist()
    out = [pd.Timestamp(x).normalize() for x in weekly if pd.notna(x)]
    if max_dates is not None and max_dates > 0:
        out = out[-int(max_dates):]
    return out


def load_cached_price_panel(
    tickers: list[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    max_cache_files_per_ticker: int = 1,
) -> tuple[pd.DataFrame, list[str]]:
    """Load ticker histories from existing cache only."""
    cache_index = _build_ticker_cache_index()
    lookback_start = start - pd.Timedelta(days=280)
    frames: list[pd.DataFrame] = []
    missing: list[str] = []
    wanted_start = int(lookback_start.strftime("%Y%m%d"))
    wanted_end = int(end.strftime("%Y%m%d"))
    for tk in tickers:
        parts: list[pd.DataFrame] = []
        candidates: list[tuple[int, int, Path]] = []
        for cache_start, cache_end, path in cache_index.get(tk, []):
            if cache_end < wanted_start or cache_start > wanted_end:
                continue
            overlap_start = max(cache_start, wanted_start)
            overlap_end = min(cache_end, wanted_end)
            overlap = max(0, overlap_end - overlap_start)
            span = max(1, cache_end - cache_start)
            covers = int(cache_start <= wanted_start and cache_end >= wanted_end)
            # Prefer full coverage, then largest overlap, then shortest span.
            score = covers * 10_000_000_000 + overlap * 10_000 - span
            candidates.append((score, span, path))
        candidates = sorted(candidates, key=lambda x: (x[0], -x[1]), reverse=True)
        for _, _, path in candidates[: max(1, int(max_cache_files_per_ticker))]:
            try:
                part = pd.read_parquet(path)
            except Exception:
                continue
            if part.empty or "date" not in part.columns:
                continue
            parts.append(part)
        hist = pd.concat(parts, ignore_index=True, sort=False) if parts else pd.DataFrame()
        if hist.empty:
            missing.append(tk)
            continue
        h = hist.copy()
        h["ticker"] = tk
        h["date"] = pd.to_datetime(h["date"], errors="coerce").dt.normalize()
        for col in ("open", "high", "low", "close", "volume", "value"):
            if col in h.columns:
                h[col] = pd.to_numeric(h[col], errors="coerce")
        keep = [c for c in ("date", "ticker", "open", "high", "low", "close", "volume", "value") if c in h.columns]
        frames.append(h[keep])
    if not frames:
        return pd.DataFrame(), missing
    out = pd.concat(frames, ignore_index=True).dropna(subset=["date", "ticker", "close"])
    out = out[(out["date"] >= lookback_start) & (out["date"] <= end)].copy()
    out = out.drop_duplicates(["date", "ticker"], keep="last")
    return out.sort_values(["ticker", "date"]).reset_index(drop=True), missing


def add_trailing_price_features(price_panel: pd.DataFrame) -> pd.DataFrame:
    p = price_panel.sort_values(["ticker", "date"]).copy()
    g = p.groupby("ticker", group_keys=False)
    p["ret_1m_dyn"] = g["close"].pct_change(21)
    p["ret_3m_dyn"] = g["close"].pct_change(63)
    p["ret_6m_dyn"] = g["close"].pct_change(126)
    p["ma20_dyn"] = g["close"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    p["ma60_dyn"] = g["close"].transform(lambda s: s.rolling(60, min_periods=30).mean())
    p["ma120_dyn"] = g["close"].transform(lambda s: s.rolling(120, min_periods=60).mean())
    p["high60_dyn"] = g["close"].transform(lambda s: s.rolling(60, min_periods=30).max())
    p["vol20_dyn"] = g["close"].pct_change().groupby(p["ticker"]).transform(lambda s: s.rolling(20, min_periods=10).std())
    p["avg_value20_dyn"] = g["value"].transform(lambda s: s.rolling(20, min_periods=10).mean()) if "value" in p.columns else np.nan
    return p


def build_weekly_overlay_panel(
    monthly_panel: pd.DataFrame,
    price_features: pd.DataFrame,
    signal_dates: list[pd.Timestamp],
    benchmark_close: pd.Series,
) -> pd.DataFrame:
    base = monthly_panel.copy()
    base["rebalance_date"] = pd.to_datetime(base["rebalance_date"], errors="coerce").dt.normalize()
    base["ticker"] = _normalise_ticker(base["ticker"])
    base = base.sort_values(["rebalance_date", "ticker"])
    monthly_dates = sorted(base["rebalance_date"].dropna().unique())
    by_month = {pd.Timestamp(d).normalize(): g.copy() for d, g in base.groupby("rebalance_date")}
    by_price_date = {pd.Timestamp(d).normalize(): g.copy() for d, g in price_features.groupby("date")}
    rows: list[pd.DataFrame] = []
    month_idx = 0
    current_month: pd.DataFrame | None = None
    close = benchmark_close.dropna().sort_index()
    for day in signal_dates:
        while month_idx < len(monthly_dates) and pd.Timestamp(monthly_dates[month_idx]).normalize() <= day:
            current_month = by_month[pd.Timestamp(monthly_dates[month_idx]).normalize()]
            month_idx += 1
        if current_month is None or day not in by_price_date:
            continue
        snap = current_month.merge(by_price_date[day], on="ticker", how="inner", suffixes=("", "_px"))
        if snap.empty:
            continue
        b1 = _trailing_return(close, day, 21)
        b3 = _trailing_return(close, day, 63)
        b6 = _trailing_return(close, day, 126)
        snap["source_monthly_rebalance_date"] = snap["rebalance_date"]
        snap["rebalance_date"] = day
        snap["bench_ret_1m"] = b1
        snap["bench_ret_3m"] = b3
        snap["bench_ret_6m"] = b6
        snap["rs_1m"] = pd.to_numeric(snap["ret_1m_dyn"], errors="coerce") - b1
        snap["rs_3m"] = pd.to_numeric(snap["ret_3m_dyn"], errors="coerce") - b3
        snap["rs_6m"] = pd.to_numeric(snap["ret_6m_dyn"], errors="coerce") - b6

        above20 = (snap["close"] > snap["ma20_dyn"]).astype(float)
        above60 = (snap["close"] > snap["ma60_dyn"]).astype(float)
        above120 = (snap["close"] > snap["ma120_dyn"]).astype(float)
        ma_stack = (snap["ma20_dyn"] > snap["ma60_dyn"]).astype(float)
        near_high = (snap["close"] / snap["high60_dyn"] - 1.0).replace([np.inf, -np.inf], np.nan)
        vol_penalty = _rank_pct(snap["vol20_dyn"]).fillna(0.5)
        snap["dyn_technical_score"] = (
            0.20 * above20
            + 0.20 * above60
            + 0.10 * above120
            + 0.15 * ma_stack
            + 0.20 * _rank_pct(snap["ret_3m_dyn"])
            + 0.15 * _rank_pct(near_high)
            - 0.10 * vol_penalty
        )
        snap["dyn_rs_score"] = (
            0.25 * _rank_pct(snap["rs_1m"])
            + 0.35 * _rank_pct(snap["rs_3m"])
            + 0.40 * _rank_pct(snap["rs_6m"])
        )
        snap["technical_score"] = snap["dyn_technical_score"]
        snap["rs_score"] = snap["dyn_rs_score"]
        rows.append(snap)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True, sort=False)


def apply_weekly_overlay_score(panel: pd.DataFrame, *, max_weight: float = 0.10) -> pd.DataFrame:
    def score_one(g: pd.DataFrame) -> pd.DataFrame:
        out = g.copy()
        mcap = pd.to_numeric(out.get("market_cap"), errors="coerce")
        liq = pd.to_numeric(out.get("avg_trading_value_60d"), errors="coerce")
        tech = pd.to_numeric(out.get("dyn_technical_score"), errors="coerce").fillna(0.0)
        rs = pd.to_numeric(out.get("dyn_rs_score"), errors="coerce").fillna(0.0)
        val = pd.to_numeric(out.get("valuation_score"), errors="coerce").fillna(0.0)
        flow = pd.to_numeric(out.get("flow_score"), errors="coerce").fillna(0.0)
        bench3 = pd.to_numeric(out.get("bench_ret_3m"), errors="coerce").fillna(0.0)
        risk = _numeric_series(out, "risk_veto_flag", 0).fillna(0).astype(int)
        hard = _numeric_series(out, "hard_exit_flag", 0).fillna(0).astype(int)
        dilution = _numeric_series(out, "dilution_risk_flag", 0).fillna(0).astype(int)
        mask = (
            (bench3 > 0.0)
            & (tech > 0.50)
            & (rs > 0.55)
            & (mcap >= mcap.quantile(0.60))
            & (liq >= liq.quantile(0.60))
            & (risk == 0)
            & (hard == 0)
        )
        score = 0.65 * _rank_pct(tech) + 0.75 * _rank_pct(rs) + 0.25 * _rank_pct(val) + 0.15 * _rank_pct(flow)
        out["leader_score"] = score.where(mask, 0.0)
        pos = out["leader_score"] > 0
        if bool(pos.any()):
            out.loc[pos, "leader_score"] = out.loc[pos, "leader_score"].rank(pct=True, method="average")
        out["eligible_final"] = mask.astype(bool)
        out["score_profile_eligible_flag"] = mask.astype(bool)
        out["risk_veto_flag"] = risk.astype(int)
        out["hard_exit_flag"] = hard.astype(int)
        out["dilution_risk_flag"] = dilution.astype(int)
        out["max_weight"] = float(max_weight)
        out.loc[(risk == 1) | (hard == 1), "max_weight"] = 0.0
        out["leader_rank"] = np.nan
        ranked = out[out["eligible_final"]].sort_values(
            ["leader_score", "avg_value20_dyn", "market_cap"],
            ascending=[False, False, False],
        )
        out.loc[ranked.index, "leader_rank"] = np.arange(1, len(ranked) + 1)
        out["score_profile"] = "weekly_price_rs_overlay"
        return out

    if panel.empty:
        return panel.copy()
    scored_parts = [score_one(g) for _, g in panel.groupby("rebalance_date", sort=True)]
    return pd.concat(scored_parts, ignore_index=True, sort=False)


def _benchmark_nav(benchmark_close: pd.Series, dates: pd.Series, initial_cash: float) -> pd.Series:
    b = benchmark_close.reindex(pd.DatetimeIndex(pd.to_datetime(dates).sort_values().unique())).ffill().dropna()
    return b / float(b.iloc[0]) * float(initial_cash) if not b.empty else pd.Series(dtype=float)


def main() -> int:
    args = parse_args()
    start = pd.Timestamp(args.start).normalize()
    scored_path = Path(args.scored_panel) if args.scored_panel else latest_scored_panel_path()
    monthly = _read_table(scored_path)
    monthly["rebalance_date"] = pd.to_datetime(monthly["rebalance_date"], errors="coerce").dt.normalize()
    monthly["ticker"] = _normalise_ticker(monthly["ticker"])
    end = pd.Timestamp(args.end).normalize() if args.end else pd.Timestamp(monthly["rebalance_date"].max()).normalize()
    monthly = monthly[(monthly["rebalance_date"] >= start) & (monthly["rebalance_date"] <= end)].copy()
    if monthly.empty:
        raise RuntimeError(f"No monthly scored rows inside {start.date()}..{end.date()}: {scored_path}")

    if args.max_tickers is not None and args.max_tickers > 0:
        keep = (
            monthly.groupby("ticker")["avg_trading_value_60d"]
            .mean()
            .sort_values(ascending=False)
            .head(int(args.max_tickers))
            .index
        )
        monthly = monthly[monthly["ticker"].isin(keep)].copy()

    tickers = sorted(monthly["ticker"].dropna().unique().tolist())
    price, missing = load_cached_price_panel(
        tickers,
        start,
        end,
        max_cache_files_per_ticker=args.max_cache_files_per_ticker,
    )
    if price.empty:
        raise RuntimeError("No cached ticker histories found for overlay run")
    price_features = add_trailing_price_features(price)
    signal_dates = _signal_dates(price_features["date"], start, end, args.frequency, args.max_dates)
    _, benchmark_close = _benchmark_returns(start, end, refresh_days=30)
    overlay = build_weekly_overlay_panel(monthly, price_features, signal_dates, benchmark_close)
    scored = apply_weekly_overlay_score(overlay, max_weight=args.single_stock_max_weight)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    price_path = out_dir / "weekly_overlay_price_panel.parquet"
    raw_path = out_dir / "weekly_overlay_raw_panel.parquet"
    scored_path_out = out_dir / "weekly_overlay_scored_panel.parquet"
    manifest_path = out_dir / "weekly_overlay_manifest.json"
    price_features.to_parquet(price_path, index=False)
    overlay.to_parquet(raw_path, index=False)
    scored.to_parquet(scored_path_out, index=False)

    manifest: dict[str, Any] = {
        "scored_panel": str(scored_path),
        "start": str(start.date()),
        "end": str(end.date()),
        "frequency": args.frequency,
        "signal_dates": int(scored["rebalance_date"].nunique()) if not scored.empty else 0,
        "rows": int(len(scored)),
        "tickers_requested": int(len(tickers)),
        "tickers_loaded": int(price["ticker"].nunique()),
        "tickers_missing_cache": int(len(missing)),
        "max_dates": args.max_dates,
        "max_tickers": args.max_tickers,
        "research_only": True,
        "official_broker_ledger_metric": False,
    }

    if args.run_backtest:
        cfg = kr1000_leader_alpha_cfg({
            "top_holdings": args.top_holdings,
            "portfolio_size": args.top_holdings,
            "buy_rank_threshold": args.top_holdings,
            "hold_rank_threshold": args.hold_rank_threshold,
            "single_stock_max_weight": args.single_stock_max_weight,
            "gross_exposure": args.gross_exposure,
            "gross_exposure_min": 0.0,
            "gross_exposure_max": 1.0,
            "hard_stop_loss_pct": args.hard_stop_loss_pct,
            "portfolio_drawdown_ladder_enabled": False,
        })
        backtest_prices = price_features[price_features["date"] >= start].copy()
        bench_nav = _benchmark_nav(benchmark_close, backtest_prices["date"], 100_000_000.0)
        bt = run_event_driven_backtest(scored, backtest_prices, cfg=cfg, initial_cash=100_000_000.0, benchmark_nav=bench_nav)
        metrics = dict(bt.metrics)
        metrics.update({
            "research_only": True,
            "official_broker_ledger_metric": False,
            "valid_for_production_metric": False,
        })
        metrics_path = out_dir / "weekly_overlay_backtest_metrics.json"
        metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
        bt.daily_nav.to_csv(out_dir / "weekly_overlay_daily_nav.csv", index=False, encoding="utf-8-sig")
        bt.trades.to_csv(out_dir / "weekly_overlay_trades.csv", index=False, encoding="utf-8-sig")
        manifest["backtest_metrics"] = str(metrics_path)
        manifest["backtest_summary"] = {
            "cagr": metrics.get("cagr"),
            "mdd": metrics.get("mdd"),
            "excess_cagr": metrics.get("excess_cagr"),
            "sharpe": metrics.get("sharpe"),
            "information_ratio": metrics.get("information_ratio"),
        }

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print("KR1000 weekly price overlay")
    print(f"  rows:             {manifest['rows']}")
    print(f"  signal dates:     {manifest['signal_dates']}")
    print(f"  tickers loaded:   {manifest['tickers_loaded']}/{manifest['tickers_requested']}")
    print(f"  missing caches:   {manifest['tickers_missing_cache']}")
    if "backtest_summary" in manifest:
        s = manifest["backtest_summary"]
        print(
            "  backtest:         "
            f"CAGR={float(s.get('cagr') or 0):.2%}, "
            f"MDD={float(s.get('mdd') or 0):.2%}, "
            f"excess={float(s.get('excess_cagr') or 0):.2%}"
        )
    print(f"  manifest:         {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
