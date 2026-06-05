"""Build and append a fast latest KR1000 scored snapshot.

This is the daily-readiness bridge when a full monthly scored-panel backfill is
too expensive. It builds the latest PIT KR1000 universe, computes cached
KOSPI200 relative strength where broad ticker caches are available, leaves
unavailable components neutral, and materializes a fresh scored_panel_v0 cache
so the broker check can evaluate the latest observable close.
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

from kr_config import DATA_ROOT, KR_ENGINE_REUSE_VERSION, kr1000_leader_alpha_cfg  # noqa: E402
from kr_helpers import log  # noqa: E402
from kr_universe import build_universe_snapshot  # noqa: E402
from kr1000_leader import build_kr1000_universe, compute_leader_scores  # noqa: E402
from tools.run_kr1000_backtest import (  # noqa: E402
    _benchmark_returns,
    _month_return,
)
from tools.run_kr1000_validation_gate import _infer_scored_panel_start_date  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build latest KR1000 scored snapshot")
    p.add_argument("--as-of", required=True, help="Latest observable close date, YYYY-MM-DD.")
    p.add_argument("--start-date", default=None,
                   help="Output scored-panel start date. Default=infer from latest cache.")
    p.add_argument("--base-panel", default=None,
                   help="Optional base scored_panel_v0 parquet/csv. Default=latest by mtime.")
    p.add_argument("--out", default=None,
                   help="Optional output parquet path. Default=feature_store/scored_panel_v0_<start>_<as_of>_<engine>.parquet.")
    p.add_argument("--refresh-days", type=int, default=3650)
    p.add_argument("--fetch-missing-prices", action="store_true",
                   help="Allow network/provider fetch when a covering ticker cache is missing.")
    p.add_argument("--no-rs", action="store_true",
                   help="Skip price/RS calculation and rank the latest snapshot by neutral score + liquidity.")
    p.add_argument("--out-dir", default=None)
    return p.parse_args()


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype={"ticker": str})
    return pd.read_parquet(path)


def _latest_scored_panel_path() -> Path:
    files = sorted(
        (DATA_ROOT / "feature_store").glob("scored_panel_v0_*.parquet"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError(f"No scored_panel_v0_*.parquet found in {DATA_ROOT / 'feature_store'}")
    return files[0]


def _normalise_ticker(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def _last_close(close: pd.Series, day: pd.Timestamp) -> float:
    sub = close.loc[close.index <= day]
    if sub.empty:
        return float("nan")
    return float(sub.iloc[-1])


def _read_exact_ticker_cache(ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    path = DATA_ROOT / "cache_pykrx" / f"ticker_{ticker}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.parquet"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        log(f"[latest-snapshot] exact ticker cache read fail {path.name}: {exc}", level="WARN")
        return pd.DataFrame()
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["ticker"] = ticker
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    return out[(out["date"] >= start) & (out["date"] <= end)].copy()


def _build_rs_features(candidates: pd.DataFrame, as_of: pd.Timestamp, refresh_days: int,
                       fetch_missing: bool) -> pd.DataFrame:
    from kr_pykrx_client import fetch_ticker_history

    out = candidates.copy()
    out["ticker"] = _normalise_ticker(out["ticker"])
    bench_rets, _ = _benchmark_returns(as_of, as_of, refresh_days)
    bench_row = bench_rets.iloc[-1].to_dict() if not bench_rets.empty else {}
    start = as_of - pd.DateOffset(months=8)
    exact_start = pd.Timestamp("2024-12-11")
    rows: list[dict[str, Any]] = []
    fetch_start = exact_start.strftime("%Y%m%d")
    fetch_end = as_of.strftime("%Y%m%d")
    for i, ticker in enumerate(out["ticker"].tolist(), start=1):
        if i == 1 or i % 100 == 0 or i == len(out):
            log(f"[latest-snapshot] RS {i}/{len(out)}")
        hist = _read_exact_ticker_cache(ticker, exact_start, as_of)
        if hist.empty and fetch_missing:
            hist = fetch_ticker_history(ticker, fetch_start, fetch_end, refresh_days=refresh_days)
        row: dict[str, Any] = {"ticker": ticker}
        if hist is None or hist.empty or "date" not in hist.columns or "close" not in hist.columns:
            for horizon in ("1m", "3m", "6m"):
                row[f"ret_{horizon}"] = np.nan
                row[f"rs_{horizon}"] = np.nan
            rows.append(row)
            continue
        h = hist.copy()
        h["date"] = pd.to_datetime(h["date"], errors="coerce").dt.normalize()
        h["close"] = pd.to_numeric(h["close"], errors="coerce")
        close = h.dropna(subset=["date", "close"]).sort_values("date").set_index("date")["close"]
        for horizon, months in (("1m", 1), ("3m", 3), ("6m", 6)):
            ret = _month_return(close, as_of, months)
            row[f"ret_{horizon}"] = ret
            bench = float(bench_row.get(f"bench_ret_{horizon}", np.nan))
            row[f"rs_{horizon}"] = ret - bench if np.isfinite(ret) and np.isfinite(bench) else np.nan
        row["last_price"] = _last_close(close, as_of)
        rows.append(row)
    rs = pd.DataFrame(rows)
    out = out.merge(rs, on="ticker", how="left")
    out["p0_momentum_score"] = (
        0.35 * pd.to_numeric(out.get("ret_6m"), errors="coerce").fillna(0.0)
        + 0.30 * pd.to_numeric(out.get("ret_3m"), errors="coerce").fillna(0.0)
        + 0.20 * pd.to_numeric(out.get("rs_3m"), errors="coerce").fillna(0.0)
        + 0.15 * pd.to_numeric(out.get("rs_6m"), errors="coerce").fillna(0.0)
    )
    out["p1_blended_score"] = out["p0_momentum_score"]
    return out


def build_latest_snapshot(as_of: pd.Timestamp, refresh_days: int, fetch_missing_prices: bool,
                          no_rs: bool = False) -> pd.DataFrame:
    cfg = kr1000_leader_alpha_cfg({"universe_name_lookup": False})
    snapshot = build_universe_snapshot(as_of, cfg=cfg)
    universe = build_kr1000_universe(as_of, cfg=cfg, snapshot=snapshot, include_discovery=False)
    if universe.empty:
        raise RuntimeError(f"KR1000 universe is empty at {as_of.date()}")
    if no_rs:
        latest = universe.copy()
        for horizon in ("1m", "3m", "6m"):
            latest[f"ret_{horizon}"] = 0.0
            latest[f"rs_{horizon}"] = 0.0
        latest["p0_momentum_score"] = 0.0
        latest["p1_blended_score"] = 0.0
    else:
        latest = _build_rs_features(universe, as_of, refresh_days, fetch_missing_prices)
    latest["rebalance_date"] = as_of.normalize()
    latest["eligible_final"] = latest.get("in_kr1000", True)
    latest["p_pre_surge"] = 0.0
    for col in (
        "flow_score", "technical_score", "quality_growth_score", "valuation_score",
        "theme_sector_score", "event_governance_score",
    ):
        if col not in latest.columns:
            latest[col] = 0.0
    latest = compute_leader_scores(latest, cfg)
    latest["snapshot_build_mode"] = "latest_fast_liquidity_only" if no_rs else "latest_fast_cached_rs"
    latest["engine_version"] = KR_ENGINE_REUSE_VERSION
    return latest


def main() -> int:
    args = parse_args()
    as_of = pd.Timestamp(args.as_of).normalize()
    base_path = Path(args.base_panel) if args.base_panel else _latest_scored_panel_path()
    base = _read_table(base_path)
    if "rebalance_date" not in base.columns:
        raise ValueError(f"base panel must include rebalance_date: {base_path}")
    base["rebalance_date"] = pd.to_datetime(base["rebalance_date"], errors="coerce").dt.normalize()
    base["ticker"] = _normalise_ticker(base["ticker"])

    start_date = args.start_date or _infer_scored_panel_start_date()
    out_path = Path(args.out) if args.out else DATA_ROOT / "feature_store" / (
        f"scored_panel_v0_{start_date}_{as_of.date()}_{KR_ENGINE_REUSE_VERSION}.parquet"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    latest = build_latest_snapshot(as_of, args.refresh_days, args.fetch_missing_prices, no_rs=args.no_rs)
    combined = pd.concat([base, latest], ignore_index=True, sort=False)
    combined["rebalance_date"] = pd.to_datetime(combined["rebalance_date"], errors="coerce").dt.normalize()
    combined["ticker"] = _normalise_ticker(combined["ticker"])
    combined = combined.drop_duplicates(["rebalance_date", "ticker"], keep="last")
    combined = combined.sort_values(["rebalance_date", "ticker"]).reset_index(drop=True)
    combined.to_parquet(out_path, index=False)

    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "as_of": str(as_of.date()),
        "base_panel": str(base_path),
        "output_panel": str(out_path),
        "base_rows": int(len(base)),
        "latest_rows": int(len(latest)),
        "combined_rows": int(len(combined)),
        "combined_min_signal": str(combined["rebalance_date"].min().date()),
        "combined_max_signal": str(combined["rebalance_date"].max().date()),
        "engine_version": KR_ENGINE_REUSE_VERSION,
        "build_mode": "latest_fast_liquidity_only" if args.no_rs else "latest_fast_cached_rs",
        "no_rs": bool(args.no_rs),
    }
    manifest_path = out_dir / f"kr1000_latest_scored_snapshot_{as_of.strftime('%Y%m%d')}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print("KR1000 latest scored snapshot")
    print(f"  as_of:       {as_of.date()}")
    print(f"  latest rows: {len(latest)}")
    print(f"  output:      {out_path}")
    print(f"  manifest:    {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
