"""Run a KR1000 Leader Alpha ledger backtest from stored scored panels.

This is the execution bridge for the first real performance check:
- load the latest monthly scored panel,
- rebuild PIT KR1000 membership by 60-day trading value on each signal date,
- compute KOSPI200 relative-strength columns,
- score/rank candidates by date,
- fetch only the price histories needed by ever-top ranked names, and
- run the event-driven order/cash/position ledger.

Examples:
    py -3 tools/run_kr1000_backtest.py --start 2019-01-01 --end 2024-12-31
    py -3 tools/run_kr1000_backtest.py --start 2018-05-30 --end 2026-04-30
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
from kr_helpers import log  # noqa: E402
from kr_pykrx_client import fetch_index_ohlcv, fetch_ticker_history  # noqa: E402
from kr1000_leader import (  # noqa: E402
    KR1000_SCORE_PROFILES,
    build_target_portfolio,
    compute_leader_scores,
    generate_trade_plan,
    load_current_holdings,
    run_event_driven_backtest,
    write_leader_outputs,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KR1000 Leader Alpha backtest runner")
    p.add_argument("--start", default="2018-01-01",
                   help="Backtest start date. Default is the official 8y+ gate start.")
    p.add_argument("--end", default="2026-04-30",
                   help="Backtest end date. Default uses latest known PIT snapshot month.")
    p.add_argument("--scored-panel", default=None,
                   help="Optional scored_panel parquet/csv path. Defaults to latest scored_panel_v0 parquet.")
    p.add_argument("--out-dir", default=None,
                   help="Output directory. Defaults under DATA_ROOT/outputs.")
    p.add_argument("--initial-cash", type=float, default=100_000_000.0)
    p.add_argument("--top-holdings", type=int, default=20)
    p.add_argument("--max-rank-for-prices", type=int, default=20,
                   help="Fetch histories for names that can be bought. Increase for initial holdings stress tests.")
    p.add_argument("--refresh-days", type=int, default=3650,
                   help="Cache TTL for historical prices/index data.")
    p.add_argument("--current-holdings", default=None,
                   help="Optional initial holdings CSV for the first day.")
    p.add_argument("--price-panel", default=None,
                   help="Optional prebuilt price panel parquet/csv to skip ticker fetches.")
    p.add_argument("--save-scored-panel", action="store_true",
                   help="Persist the dated KR1000 scored panel used by the backtest.")
    p.add_argument("--score-profile", default="full",
                   choices=sorted(KR1000_SCORE_PROFILES),
                   help="Component A/B profile. Official production profile is full.")
    p.add_argument("--gross-exposure", type=float, default=None,
                   help="Optional fixed gross exposure override, e.g. 0.60 for defensive runs.")
    p.add_argument("--hard-stop-loss-pct", type=float, default=None,
                   help="Optional daily hard-stop threshold as a decimal, e.g. 0.10.")
    p.add_argument("--buy-rank-threshold", type=int, default=None,
                   help="Optional buy rank threshold override. Defaults to --top-holdings.")
    p.add_argument("--hold-rank-threshold", type=int, default=None,
                   help="Optional hold rank threshold override. Defaults to config.")
    p.add_argument("--min-notional-krw", type=float, default=None,
                   help="Optional minimum order notional override.")
    p.add_argument("--slippage-bp", type=float, default=None,
                   help="Optional one-way slippage in basis points.")
    p.add_argument("--disable-daily-hard-exit", action="store_true",
                   help="Disable the daily hard-exit monitor for sensitivity tests.")
    p.add_argument("--portfolio-dd-ladder", action="store_true",
                   help="Enable portfolio drawdown ladder gross-exposure scaling.")
    p.add_argument("--portfolio-dd-thresholds", default=None,
                   help="Comma-separated drawdown thresholds, e.g. -0.08,-0.15,-0.25.")
    p.add_argument("--portfolio-dd-scales", default=None,
                   help="Comma-separated gross scales, e.g. 0.85,0.65,0.40.")
    p.add_argument("--pmb-oos-picks", default=None,
                   help=(
                       "Optional PIT-safe P_MB OOS picks CSV with p_pre_surge. "
                       "Defaults to research/06_walkforward_baselines/p_mb_v1_oos_picks.csv when present."
                   ))
    return p.parse_args()


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype={"ticker": str})
    return pd.read_parquet(path)


def _latest_scored_panel_path() -> Path:
    fs = DATA_ROOT / "feature_store"
    files = sorted(fs.glob("scored_panel_v0_*.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No scored_panel_v0_*.parquet found in {fs}")
    return files[0]


def _normalise_ticker(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def _parse_float_list(raw: str | None) -> list[float] | None:
    if raw is None or str(raw).strip() == "":
        return None
    return [float(x.strip()) for x in str(raw).split(",") if x.strip()]


def _default_pmb_oos_picks_path() -> Path:
    return PROJECT_ROOT / "research" / "06_walkforward_baselines" / "p_mb_v1_oos_picks.csv"


def _yyyymmdd(day: pd.Timestamp) -> str:
    return pd.Timestamp(day).strftime("%Y%m%d")


def _build_ticker_cache_index() -> dict[str, list[tuple[int, int, Path]]]:
    cache_dir = DATA_ROOT / "cache_pykrx"
    index: dict[str, list[tuple[int, int, Path]]] = {}
    for path in cache_dir.glob("ticker_*.parquet"):
        parts = path.stem.split("_")
        if len(parts) < 4:
            continue
        try:
            cache_start = int(parts[-2])
            cache_end = int(parts[-1])
        except ValueError:
            continue
        index.setdefault(parts[1], []).append((cache_start, cache_end, path))
    return index


def _cached_ticker_history_covering(
    ticker: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cache_index: dict[str, list[tuple[int, int, Path]]],
) -> pd.DataFrame:
    """Load an existing ticker cache that covers the requested window.

    fetch_ticker_history keys by exact start/end, but this project already has
    many broad caches such as ticker_005930_20000101_20260429.parquet. Reusing
    those is much faster than refetching every name for a new long window.
    """
    wanted_start = int(_yyyymmdd(start))
    wanted_end = int(_yyyymmdd(end))
    best: tuple[int, Path] | None = None
    for cache_start, cache_end, path in cache_index.get(ticker, []):
        if cache_start <= wanted_start and cache_end >= wanted_end:
            span = cache_end - cache_start
            if best is None or span < best[0]:
                best = (span, path)
    if best is None:
        return pd.DataFrame()
    try:
        df = pd.read_parquet(best[1])
    except Exception as exc:
        log(f"[kr1000-bt] cached history read fail {best[1].name}: {exc}", level="WARN")
        return pd.DataFrame()
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["ticker"] = ticker
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    return out[(out["date"] >= start) & (out["date"] <= end)].copy()


def _last_close_at_or_before(close: pd.Series, day: pd.Timestamp) -> float:
    sub = close.loc[close.index <= day]
    if sub.empty:
        return float("nan")
    return float(sub.iloc[-1])


def _month_return(close: pd.Series, day: pd.Timestamp, months: int) -> float:
    end_px = _last_close_at_or_before(close, day)
    start_px = _last_close_at_or_before(close, day - pd.DateOffset(months=months))
    if not np.isfinite(end_px) or not np.isfinite(start_px) or start_px <= 0:
        return float("nan")
    return end_px / start_px - 1.0


def _benchmark_returns(start: pd.Timestamp, end: pd.Timestamp, refresh_days: int) -> pd.DataFrame:
    lookback_start = (start - pd.DateOffset(months=8)).strftime("%Y%m%d")
    bench = fetch_index_ohlcv("1028", lookback_start, end.strftime("%Y%m%d"), refresh_days=refresh_days)
    if bench.empty:
        raise RuntimeError("Could not load KOSPI200 benchmark series")
    bench = bench.copy()
    bench["date"] = pd.to_datetime(bench["date"]).dt.normalize()
    bench["close"] = pd.to_numeric(bench["close"], errors="coerce")
    close = bench.dropna(subset=["date", "close"]).sort_values("date").set_index("date")["close"]
    rows = []
    for day in pd.date_range(start, end, freq="D"):
        rows.append({
            "rebalance_date": day.normalize(),
            "bench_ret_1m": _month_return(close, day, 1),
            "bench_ret_3m": _month_return(close, day, 3),
            "bench_ret_6m": _month_return(close, day, 6),
        })
    return pd.DataFrame(rows), close


def _add_kr1000_membership(panel: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    out = panel.copy()
    out["ticker"] = _normalise_ticker(out["ticker"])
    out["avg_trading_value_60d"] = pd.to_numeric(out.get("avg_trading_value_60d"), errors="coerce")
    out["market_cap"] = pd.to_numeric(out.get("market_cap"), errors="coerce")
    if "eligible" not in out.columns:
        out["eligible"] = True
    eligible = out["eligible"].fillna(False).astype(bool)
    common_code = out["ticker"].str.match(r"^\d{6}$", na=False)
    tradable = eligible & common_code & out["avg_trading_value_60d"].notna() & (out["avg_trading_value_60d"] > 0)
    out["in_kr1000"] = False
    out["kr1000_liquidity_rank"] = np.nan
    n = int(cfg.get("kr1000_size", 1000))

    def rank_one(g: pd.DataFrame) -> pd.DataFrame:
        ranked = g.loc[tradable.reindex(g.index).fillna(False)].sort_values(
            ["avg_trading_value_60d", "market_cap", "ticker"],
            ascending=[False, False, True],
        )
        g.loc[ranked.index, "kr1000_liquidity_rank"] = np.arange(1, len(ranked) + 1)
        g.loc[ranked.head(n).index, "in_kr1000"] = True
        return g

    return out.groupby("rebalance_date", group_keys=False).apply(rank_one).reset_index(drop=True)


def merge_pmb_oos_predictions(
    panel: pd.DataFrame,
    picks_path: str | Path | None = None,
) -> pd.DataFrame:
    """Merge PIT-safe P_MB OOS probabilities into a scored panel.

    The default OOS picks file is generated by the walk-forward multibagger
    classifier. It contains only selected high-probability rows, so missing
    rows are assigned p_pre_surge=0 and rank=NaN. This is intentionally not the
    same as scoring history with the latest classifier, which would leak.
    """
    if panel.empty:
        return panel.copy()
    path = Path(picks_path) if picks_path else _default_pmb_oos_picks_path()
    out = panel.copy()
    if "p_pre_surge" not in out.columns:
        out["p_pre_surge"] = 0.0
    if not path.exists():
        out["pmb_oos_source"] = ""
        return out

    picks = _read_table(path)
    required = {"rebalance_date", "ticker", "p_pre_surge"}
    if not required.issubset(picks.columns):
        out["pmb_oos_source"] = ""
        return out

    p = picks.copy()
    p["rebalance_date"] = pd.to_datetime(p["rebalance_date"], errors="coerce").dt.normalize()
    p["ticker"] = _normalise_ticker(p["ticker"])
    p["p_pre_surge"] = pd.to_numeric(p["p_pre_surge"], errors="coerce").fillna(0.0)
    keep = ["rebalance_date", "ticker", "p_pre_surge"]
    if "rank_in_month" in p.columns:
        p["pmb_oos_rank"] = pd.to_numeric(p["rank_in_month"], errors="coerce")
        keep.append("pmb_oos_rank")
    p = p[keep].dropna(subset=["rebalance_date", "ticker"]).drop_duplicates(
        ["rebalance_date", "ticker"],
        keep="first",
    )

    out["ticker"] = _normalise_ticker(out["ticker"])
    out["rebalance_date"] = pd.to_datetime(out["rebalance_date"], errors="coerce").dt.normalize()
    out = out.drop(columns=[c for c in ("pmb_oos_rank",) if c in out.columns])
    out = out.merge(p, on=["rebalance_date", "ticker"], how="left", suffixes=("", "_oos"))
    if "p_pre_surge_oos" in out.columns:
        out["p_pre_surge"] = pd.to_numeric(out["p_pre_surge_oos"], errors="coerce").fillna(
            pd.to_numeric(out["p_pre_surge"], errors="coerce").fillna(0.0)
        )
        out = out.drop(columns=["p_pre_surge_oos"])
    out["p_pre_surge"] = pd.to_numeric(out["p_pre_surge"], errors="coerce").fillna(0.0)
    if "pmb_oos_rank" not in out.columns:
        out["pmb_oos_rank"] = np.nan
    out["pmb_oos_source"] = str(path)
    return out


def prepare_kr1000_scored_panel(
    scored_panel: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cfg: dict[str, Any],
    refresh_days: int,
    pmb_oos_picks: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    panel = scored_panel.copy()
    date_col = "rebalance_date" if "rebalance_date" in panel.columns else "date"
    panel = panel.rename(columns={date_col: "rebalance_date"})
    panel["rebalance_date"] = pd.to_datetime(panel["rebalance_date"]).dt.normalize()
    panel = panel[(panel["rebalance_date"] >= start) & (panel["rebalance_date"] <= end)].copy()
    if panel.empty:
        raise RuntimeError(f"No scored panel rows inside {start.date()} to {end.date()}")
    panel["ticker"] = _normalise_ticker(panel["ticker"])

    bench_rets, bench_close = _benchmark_returns(panel["rebalance_date"].min(), panel["rebalance_date"].max(), refresh_days)
    panel = panel.merge(bench_rets, on="rebalance_date", how="left")
    for horizon in ("1m", "3m", "6m"):
        ret_col = f"ret_{horizon}"
        bench_col = f"bench_ret_{horizon}"
        rs_col = f"rs_{horizon}"
        if ret_col in panel.columns:
            panel[rs_col] = pd.to_numeric(panel[ret_col], errors="coerce") - pd.to_numeric(panel[bench_col], errors="coerce")
        else:
            panel[rs_col] = np.nan
    if "rs_kospi_3m" in panel.columns:
        panel["rs_3m"] = pd.to_numeric(panel["rs_kospi_3m"], errors="coerce").fillna(panel["rs_3m"])

    panel = _add_kr1000_membership(panel, cfg)
    panel = panel[panel["in_kr1000"]].copy()
    if panel.empty:
        raise RuntimeError("KR1000 filter produced no rows")
    panel = merge_pmb_oos_predictions(panel, pmb_oos_picks)

    scored = panel.groupby("rebalance_date", group_keys=False).apply(
        lambda g: compute_leader_scores(g, cfg)
    ).reset_index(drop=True)
    return scored, bench_close


def _load_or_build_price_panel(
    scored: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    max_rank: int,
    refresh_days: int,
    price_panel_path: str | None = None,
) -> pd.DataFrame:
    if price_panel_path:
        p = Path(price_panel_path)
        if not p.exists():
            raise FileNotFoundError(p)
        out = _read_table(p)
        out["ticker"] = _normalise_ticker(out["ticker"])
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
        return out

    ranked = scored[pd.to_numeric(scored["leader_rank"], errors="coerce") <= max_rank].copy()
    tickers = sorted(ranked["ticker"].dropna().astype(str).unique())
    if not tickers:
        raise RuntimeError("No ranked tickers selected for price fetch")
    log(f"[kr1000-bt] fetching price histories for {len(tickers)} ever-top-{max_rank} tickers")
    cache_index = _build_ticker_cache_index()
    log(f"[kr1000-bt] indexed cached histories for {len(cache_index)} tickers")

    frames = []
    fetch_start_day = start - pd.Timedelta(days=10)
    fetch_end_day = end
    fetch_start = fetch_start_day.strftime("%Y%m%d")
    fetch_end = fetch_end_day.strftime("%Y%m%d")
    for i, tk in enumerate(tickers, start=1):
        if i == 1 or i % 50 == 0 or i == len(tickers):
            log(f"[kr1000-bt] price fetch {i}/{len(tickers)}")
        hist = _cached_ticker_history_covering(tk, fetch_start_day, fetch_end_day, cache_index)
        if hist.empty:
            hist = fetch_ticker_history(tk, fetch_start, fetch_end, refresh_days=refresh_days)
        if hist is None or hist.empty:
            continue
        h = hist.copy()
        h["ticker"] = tk
        h["date"] = pd.to_datetime(h["date"]).dt.normalize()
        for col in ("open", "high", "low", "close", "volume", "value"):
            if col in h.columns:
                h[col] = pd.to_numeric(h[col], errors="coerce")
        keep = [c for c in ("date", "ticker", "open", "high", "low", "close", "volume", "value") if c in h.columns]
        frames.append(h[keep])
    if not frames:
        raise RuntimeError("No ticker price histories loaded")
    out = pd.concat(frames, ignore_index=True).dropna(subset=["date", "ticker", "close"])
    out = out[(out["date"] >= start) & (out["date"] <= end)].copy()
    return out.sort_values(["date", "ticker"]).reset_index(drop=True)


def _benchmark_nav(bench_close: pd.Series, start: pd.Timestamp, end: pd.Timestamp, initial_cash: float) -> pd.Series:
    b = bench_close[(bench_close.index >= start) & (bench_close.index <= end)].dropna()
    if b.empty:
        return pd.Series(dtype=float)
    return b / float(b.iloc[0]) * float(initial_cash)


def main() -> int:
    args = parse_args()
    start = pd.Timestamp(args.start).normalize()
    end = pd.Timestamp(args.end).normalize()
    cfg_overrides: dict[str, Any] = {
        "top_holdings": args.top_holdings,
        "portfolio_size": args.top_holdings,
        "buy_rank_threshold": args.buy_rank_threshold or args.top_holdings,
        "avg_value_refresh_days": args.refresh_days,
        "score_profile": args.score_profile,
    }
    if args.gross_exposure is not None:
        cfg_overrides.update({
            "gross_exposure": args.gross_exposure,
            "gross_exposure_min": 0.0,
            "gross_exposure_max": 1.0,
        })
    if args.hard_stop_loss_pct is not None:
        cfg_overrides["hard_stop_loss_pct"] = args.hard_stop_loss_pct
    if args.hold_rank_threshold is not None:
        cfg_overrides["hold_rank_threshold"] = args.hold_rank_threshold
    if args.min_notional_krw is not None:
        cfg_overrides["min_notional_krw"] = args.min_notional_krw
    if args.slippage_bp is not None:
        cfg_overrides["slippage_bp"] = args.slippage_bp
    if args.disable_daily_hard_exit:
        cfg_overrides["daily_hard_exit_enabled"] = False
    if args.portfolio_dd_ladder:
        cfg_overrides["portfolio_drawdown_ladder_enabled"] = True
    dd_thresholds = _parse_float_list(args.portfolio_dd_thresholds)
    dd_scales = _parse_float_list(args.portfolio_dd_scales)
    if dd_thresholds is not None:
        cfg_overrides["portfolio_drawdown_ladder_thresholds"] = dd_thresholds
    if dd_scales is not None:
        cfg_overrides["portfolio_drawdown_ladder_scales"] = dd_scales
    cfg = kr1000_leader_alpha_cfg(cfg_overrides)
    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs" / f"kr1000_leader_backtest_{start.date()}_{end.date()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    scored_path = Path(args.scored_panel) if args.scored_panel else _latest_scored_panel_path()
    log(f"[kr1000-bt] loading scored panel: {scored_path}")
    raw = _read_table(scored_path)
    scored, bench_close = prepare_kr1000_scored_panel(raw, start, end, cfg, args.refresh_days, args.pmb_oos_picks)
    actual_start = pd.Timestamp(scored["rebalance_date"].min()).normalize()
    actual_end = pd.Timestamp(scored["rebalance_date"].max()).normalize()
    if actual_start > start or actual_end < end:
        log(
            f"[kr1000-bt] requested {start.date()}..{end.date()}, "
            f"available scored-panel window {actual_start.date()}..{actual_end.date()}",
            level="WARN",
        )

    prices = _load_or_build_price_panel(
        scored,
        actual_start,
        actual_end,
        args.max_rank_for_prices,
        args.refresh_days,
        args.price_panel,
    )
    if prices.empty:
        raise RuntimeError("empty price panel")
    if not args.price_panel:
        price_out = out_dir / "leader_price_panel.parquet"
        prices.to_parquet(price_out, index=False)
        log(f"[kr1000-bt] saved price panel: {price_out}")

    nav_start = max(actual_start, pd.Timestamp(prices["date"].min()).normalize())
    nav_end = min(actual_end, pd.Timestamp(prices["date"].max()).normalize())
    bench_nav = _benchmark_nav(bench_close, nav_start, nav_end, args.initial_cash)
    holdings = load_current_holdings(args.current_holdings) if args.current_holdings else None

    bt = run_event_driven_backtest(
        scored_panel=scored,
        price_panel=prices,
        cfg=cfg,
        initial_cash=args.initial_cash,
        initial_holdings=holdings,
        benchmark_nav=bench_nav,
    )

    latest_day = scored["rebalance_date"].max()
    latest_scored = scored[scored["rebalance_date"] == latest_day].copy()
    latest_target = build_target_portfolio(latest_scored, cfg, as_of_date=latest_day)
    latest_plan = generate_trade_plan(
        load_current_holdings(args.current_holdings),
        latest_target,
        latest_scored,
        cfg,
        as_of_date=latest_day,
    )

    paths = write_leader_outputs(latest_scored, latest_target, latest_plan, backtest=bt, output_dir=out_dir)
    if args.save_scored_panel:
        scored_out = out_dir / "leader_scored_panel.parquet"
        scored.to_parquet(scored_out, index=False)
        paths["scored_panel"] = scored_out
    manifest = {
        "requested_start": str(start.date()),
        "requested_end": str(end.date()),
        "actual_signal_start": str(actual_start.date()),
        "actual_signal_end": str(actual_end.date()),
        "scored_panel": str(scored_path),
        "score_profile": args.score_profile,
        "strategy_overrides": {
            k: cfg_overrides[k]
            for k in sorted(cfg_overrides)
            if k not in {"avg_value_refresh_days", "portfolio_size", "score_profile", "top_holdings"}
        },
        "pmb_oos_picks": str(Path(args.pmb_oos_picks) if args.pmb_oos_picks else _default_pmb_oos_picks_path()),
        "pmb_oos_rows_scored_positive": int((pd.to_numeric(scored.get("p_pre_surge", 0.0), errors="coerce").fillna(0.0) > 0).sum()),
        "price_rows": int(len(prices)),
        "unique_price_tickers": int(prices["ticker"].nunique()),
        "outputs": {k: str(v) for k, v in paths.items()},
        "metrics": bt.metrics,
    }
    (out_dir / "leader_backtest_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    print("KR1000 Leader Alpha backtest")
    print(f"  Requested: {start.date()} .. {end.date()}")
    print(f"  Signals:   {actual_start.date()} .. {actual_end.date()} ({scored['rebalance_date'].nunique()} snapshots)")
    print(f"  Prices:    {prices['date'].min().date()} .. {prices['date'].max().date()} ({prices['ticker'].nunique()} tickers)")
    print(f"  CAGR:      {bt.metrics.get('cagr', 0.0):.2%}")
    print(f"  KOSPI200:  {bt.metrics.get('benchmark_cagr', 0.0):.2%}")
    print(f"  Excess:    {bt.metrics.get('excess_cagr', 0.0):+.2%}")
    print(f"  MDD:       {bt.metrics.get('mdd', 0.0):.2%}")
    print(f"  Sharpe:    {bt.metrics.get('sharpe', 0.0):.2f}")
    print(f"  Profile:   {args.score_profile}")
    print(f"  Trades:    {bt.metrics.get('n_trades', 0)}")
    print(f"  Output:    {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
