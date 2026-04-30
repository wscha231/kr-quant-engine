"""tools/build_scored_panel_mini.py — Build scored panel for P_MB classifier.

Universe: top 100 KOSPI mcap + 100 KOSDAQ mcap + all 278 quality-pass episode
tickers (deduped, expected ~400-450).
Months: 2020-01 to 2024-12 (60 month-ends).
Features: P0 momentum + cross-sectional ranks (already cached).

Output: feature_store/scored_panel_p_mb_mini_{KR_ENGINE_REUSE_VERSION}.parquet

Run: py -3 tools/build_scored_panel_mini.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, KR_ENGINE_REUSE_VERSION
from kr_helpers import log
from kr_features import add_basic_momentum, add_cross_sectional_ranks, compute_p0_score
from kr_pykrx_client import fetch_listing


def build_universe(target_size_per_market: int = 100) -> pd.DataFrame:
    """Top N KOSPI + Top N KOSDAQ + all quality-pass episode tickers."""
    # 1. Top mcap from each market
    rows = []
    for mkt in ("KOSPI", "KOSDAQ"):
        listing = fetch_listing("20241230", market=mkt, refresh_days=30)
        if "market_cap" not in listing.columns:
            log(f"[mini] {mkt} no mcap col", level="WARN")
            continue
        top = listing.sort_values("market_cap", ascending=False).head(target_size_per_market)
        top["exchange"] = mkt
        rows.append(top)
    universe = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if universe.empty:
        return universe
    universe = universe.drop_duplicates(subset=["ticker"])

    # 2. Add all episode tickers
    fs_dir = DATA_ROOT / "feature_store"
    ep_files = sorted(fs_dir.glob("multibagger_episodes_v0_*.parquet"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if ep_files:
        eps = pd.read_parquet(ep_files[0])
        if "quality_pass" in eps.columns:
            eps = eps[eps["quality_pass"]]
        ep_tickers = set(eps["ticker"].astype(str).tolist())
        existing = set(universe["ticker"].astype(str).tolist())
        new_tickers = ep_tickers - existing
        if new_tickers:
            # Add via FDR listing (already cached)
            full_listing = fetch_listing("20241230", market="ALL", refresh_days=30)
            extra = full_listing[full_listing["ticker"].astype(str).isin(new_tickers)].copy()
            if "market" in extra.columns:
                extra["exchange"] = extra["market"]
            else:
                extra["exchange"] = "?"
            universe = pd.concat([universe, extra], ignore_index=True)
            universe = universe.drop_duplicates(subset=["ticker"])

    universe["eligible"] = True
    log(f"[mini] universe: {len(universe)} tickers ({len(rows)} sources merged)")
    return universe


def build_panel(start_year: int = 2020, end_year: int = 2024) -> pd.DataFrame:
    """Build (rebalance_date, ticker, features) panel for P_MB training."""
    universe = build_universe(target_size_per_market=100)
    if universe.empty:
        log("[mini] empty universe", level="ERROR")
        return pd.DataFrame()

    # Monthly rebalance dates: end of each month
    month_ends = pd.date_range(f"{start_year}-01-31", f"{end_year}-12-31", freq="ME")
    log(f"[mini] {len(month_ends)} month-ends, {len(universe)} tickers "
        f"= {len(month_ends) * len(universe)} rows max")

    frames = []
    for i, rd in enumerate(month_ends, 1):
        if i % 6 == 0:
            log(f"[mini] month {i}/{len(month_ends)}: {rd.strftime('%Y-%m')}")
        df = universe.copy()
        df["rebalance_date"] = rd
        df = add_basic_momentum(df, rd, history_lookback_days=400)
        df = add_cross_sectional_ranks(df)
        df = compute_p0_score(df)
        # Keep only features we need for classifier
        keep = [c for c in (
            "rebalance_date", "ticker", "name", "exchange", "market_cap",
            "ret_1m", "ret_3m", "ret_6m", "ret_12m", "ret_12_1m",
            "rs_kospi_3m", "rs_kospi_12m", "rs_kosdaq_3m", "rs_kosdaq_12m",
            "ret_12_1m_rank", "ret_6m_rank", "ret_3m_rank",
            "rs_kospi_12m_rank", "rs_kosdaq_12m_rank",
            "ret_12_1m_z", "ret_6m_z", "ret_3m_z",
            "rs_kospi_12m_z", "rs_kosdaq_12m_z",
            "p0_momentum_score",
        ) if c in df.columns]
        frames.append(df[keep].copy())

    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)

    # Save
    out_path = DATA_ROOT / "feature_store" / (
        f"scored_panel_p_mb_mini_{KR_ENGINE_REUSE_VERSION}.parquet"
    )
    panel.to_parquet(out_path, index=False)
    log(f"[mini] saved {len(panel)} rows -> {out_path.name}")
    return panel


if __name__ == "__main__":
    panel = build_panel(start_year=2020, end_year=2024)
    print()
    print("=" * 60)
    print(f"Scored panel built: {panel.shape}")
    print("=" * 60)
    print(f"  rebalance_date range: {panel['rebalance_date'].min()} ~ {panel['rebalance_date'].max()}")
    print(f"  unique tickers: {panel['ticker'].nunique()}")
    print(f"  P0 score coverage (non-NaN): {panel['p0_momentum_score'].notna().mean():.1%}")
