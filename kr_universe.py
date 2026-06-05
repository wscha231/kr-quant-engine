"""kr_universe — KOSPI + KOSDAQ 통합 universe builder.

build_universe_monthly_v0(cfg) returns a long-format DataFrame:
   columns: rebalance_date, ticker, name, exchange, market_cap,
            avg_trading_value_60d, listed_months, eligible (bool)

The eligibility flag applies hard exclusions + soft filters per `cfg`. Eligible
rows are the tradeable universe at that month-end. Snapshot is PIT — uses
data available as of `rebalance_date`.

P0 scope: price + market cap + listing only. PER/PBR enrichment via
kr_features.add_basic_value_signals (so universe stays cheap).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from kr_config import (
    DEFAULT_CFG,
    DEFAULT_MIN_LISTED_MONTHS,
    DEFAULT_MIN_MARKET_CAP_KRW,
    DEFAULT_MIN_TRADING_VALUE_60D_KRW,
    EXCHANGES,
    DATA_ROOT,
)
from kr_helpers import log
from kr_pit_universe import (
    compute_listed_months_pit,
    fetch_listing_at_date,
)
from kr_pykrx_client import (
    fetch_business_days,
    fetch_listing,
    fetch_market_cap_market,
    fetch_month_end_business_days,
    fetch_daily_ohlcv_market,
)


# ---------------------------------------------------------------------------
# Hard exclusion regex / rules
# ---------------------------------------------------------------------------
PREFERRED_SUFFIX_PATTERN = re.compile(r"^\d{5}[579]$")   # 6-digit, last digit 5/7/9
SPAC_NAME_PATTERN = re.compile(r"스팩|SPAC", re.IGNORECASE)
REIT_NAME_PATTERN = re.compile(r"리츠|REIT", re.IGNORECASE)


def is_preferred(ticker: str) -> bool:
    """True if ticker is a preferred stock (우선주). 005935 (삼성전자우) → True."""
    return bool(PREFERRED_SUFFIX_PATTERN.match(str(ticker).strip()))


def is_spac(name: str) -> bool:
    return bool(SPAC_NAME_PATTERN.search(str(name or "")))


def is_reit(name: str) -> bool:
    return bool(REIT_NAME_PATTERN.search(str(name or "")))


# ---------------------------------------------------------------------------
# Trading value (60-day average)
# ---------------------------------------------------------------------------
def compute_avg_trading_value_60d(
    rebalance_date: pd.Timestamp,
    lookback_days: int = 60,
    refresh_days: int = 7,
    tickers: Optional[list[str]] = None,
    fallback_max_days: int = 0,
) -> pd.DataFrame:
    """For each ticker, compute avg 거래대금 over last N business days ending
    on rebalance_date.

    Strategy:
        1. If tickers given, use those; else fetch listing at rebalance_date.
        2. For each ticker, fetch daily OHLCV history via fetch_ticker_history
           (uses FDR fallback if pykrx broken).
        3. Aggregate last N business days of `value` column.

    Returns DataFrame: ticker, avg_trading_value, days_observed.

    Caches per (rebalance_date, lookback_days) at cache_misc/. When
    fallback_max_days > 0 and the exact cache is unavailable, the latest prior
    cache within that date gap may be reused. Future caches are never used.
    """
    from kr_pykrx_client import fetch_listing, fetch_ticker_history

    cache_path = DATA_ROOT / "cache_misc" / (
        f"avg_value_{lookback_days}d_{rebalance_date.strftime('%Y%m%d')}.parquet"
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                return pd.read_parquet(cache_path)
            except Exception as e:
                log(f"[universe] cache read fail {cache_path.name}: {e}", level="WARN")

    if fallback_max_days > 0 and refresh_days != 0:
        fallback_path = find_prior_avg_value_cache(
            rebalance_date,
            lookback_days=lookback_days,
            fallback_max_days=fallback_max_days,
        )
        if fallback_path is not None:
            try:
                prior = pd.read_parquet(fallback_path)
                if tickers is not None and "ticker" in prior.columns:
                    allowed = {str(t).zfill(6) for t in tickers}
                    prior = prior[prior["ticker"].astype(str).str.zfill(6).isin(allowed)].copy()
                log(
                    f"[universe] reuse PIT-safe prior avg_value cache "
                    f"{fallback_path.name} for {rebalance_date.strftime('%Y-%m-%d')}"
                )
                return prior
            except Exception as e:
                log(f"[universe] prior avg_value cache read fail {fallback_path.name}: {e}", level="WARN")

    if tickers is None:
        listing = fetch_listing(rebalance_date.strftime("%Y%m%d"), market="ALL",
                                  refresh_days=30)
        if listing.empty:
            return pd.DataFrame(columns=["ticker", "avg_trading_value", "days_observed"])
        tickers = listing["ticker"].astype(str).tolist()

    end = rebalance_date
    # Pull a slightly longer window than lookback*1.5 to ensure we have N
    # business days even with holidays.
    start = end - timedelta(days=int(lookback_days * 1.7))
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")

    log(f"[universe] compute_avg_trading_value_60d for {len(tickers)} tickers, "
        f"window {start_str}~{end_str}")

    rows = []
    for i, tk in enumerate(tickers, 1):
        if i % 200 == 0:
            log(f"[universe] avg_value {i}/{len(tickers)}")
        hist = fetch_ticker_history(tk, start_str, end_str, refresh_days=30)
        if hist.empty or "value" not in hist.columns:
            continue
        # Take last N rows (≈ lookback_days business days)
        recent = hist.sort_values("date").tail(lookback_days)
        vals = pd.to_numeric(recent["value"], errors="coerce").dropna()
        if len(vals) == 0:
            continue
        rows.append({
            "ticker": str(tk),
            "avg_trading_value": float(vals.mean()),
            "days_observed": int(len(vals)),
        })

    out = pd.DataFrame(rows)
    if not out.empty:
        try:
            out.to_parquet(cache_path, index=False)
        except Exception as e:
            log(f"[universe] cache write fail {cache_path.name}: {e}", level="WARN")
    log(f"[universe] avg_value: {len(out)} tickers with valid data")
    return out


def find_prior_avg_value_cache(
    rebalance_date: pd.Timestamp,
    lookback_days: int = 60,
    fallback_max_days: int = 0,
    cache_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Return latest prior avg_value cache within fallback_max_days.

    This helper is intentionally date-based rather than mtime-based because
    avg trading value snapshots are historical facts once computed. It is
    PIT-safe: only cache files dated on or before rebalance_date are eligible.
    """
    if fallback_max_days <= 0:
        return None
    root = Path(cache_dir) if cache_dir is not None else DATA_ROOT / "cache_misc"
    if not root.exists():
        return None
    rd = pd.Timestamp(rebalance_date).normalize()
    pattern = re.compile(rf"^avg_value_{int(lookback_days)}d_(\d{{8}})\.parquet$")
    candidates: list[tuple[pd.Timestamp, Path]] = []
    for path in root.glob(f"avg_value_{int(lookback_days)}d_*.parquet"):
        match = pattern.match(path.name)
        if not match:
            continue
        cache_date = pd.Timestamp(match.group(1)).normalize()
        if cache_date > rd:
            continue
        if (rd - cache_date).days > int(fallback_max_days):
            continue
        candidates.append((cache_date, path))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


# ---------------------------------------------------------------------------
# Listing age (P0 conservative: assume listed long ago if first OHLCV exists)
# ---------------------------------------------------------------------------
def compute_listed_months(
    rebalance_date: pd.Timestamp,
    tickers: list[str],
    cache_path: Optional[Path] = None,
) -> pd.DataFrame:
    """Listed months derived from PIT historical mcap snapshots.

    Replaces the legacy 999-stub with real values via kr_pit_universe.
    Tickers whose listing predates the earliest cached snapshot are returned
    as 999 (treated as "long-listed, exact unknown" so the
    min_listed_months ≥ 12 filter passes them).

    Tickers NOT present in listed_history get listed_months = 0 — meaning
    they will fail the min_listed_months filter (correct behaviour: never
    seen in the historical mcap snapshots ⇒ not eligible).
    """
    df = compute_listed_months_pit(rebalance_date, tickers)
    return df[["ticker", "listed_months"]].copy()


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------
def build_universe_snapshot(
    rebalance_date: str | pd.Timestamp,
    cfg: Optional[dict] = None,
) -> pd.DataFrame:
    """Build universe membership snapshot at one rebalance_date.

    Returns DataFrame: ticker, name, exchange, rebalance_date, market_cap,
    listed_shares, avg_trading_value_60d, listed_months, eligible, exclude_reason.

    PIT-correct path (Phase C1, 2026-05-02): listing membership is sourced
    strictly from cached monthly mktcap snapshots via fetch_listing_at_date.
    The FDR-current StockListing fallback is NOT used for historical dates —
    that path leaked currently-listed names into past universes (survivorship
    bias). For dates earlier than the earliest cached snapshot, builder
    returns empty DataFrame so the caller fails loudly rather than silently
    using stale current data.
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    rd = pd.Timestamp(rebalance_date).normalize()
    log(f"[universe] build snapshot for {rd.strftime('%Y-%m-%d')}")

    exchanges = [e.upper() for e in cfg.get("exchanges", list(EXCHANGES))]

    # 1+2. PIT listing + mcap from cached snapshot at-or-before rd
    pit_listing = fetch_listing_at_date(rd, market="ALL", name_lookup=True)
    if pit_listing.empty:
        log(f"[universe] PIT snapshot missing for {rd.strftime('%Y-%m-%d')} — "
            f"caller must rebuild cache or pick a later start date.", level="WARN")
        return pd.DataFrame()

    if "market" in pit_listing.columns:
        pit_listing = pit_listing[
            pit_listing["market"].str.upper().isin(exchanges)
        ].copy()

    # Split into listing (ticker, name, market) and mktcap (ticker, market_cap,
    # listed_shares) views so the rest of the merge code keeps its shape.
    listing_keep = [c for c in ("ticker", "name", "market") if c in pit_listing.columns]
    if "name" not in pit_listing.columns:
        pit_listing["name"] = ""
        listing_keep = ["ticker", "name", "market"]
    listing = pit_listing[listing_keep].copy()

    mcap_keep_src = [c for c in ("ticker", "market_cap", "listed_shares")
                     if c in pit_listing.columns]
    mktcap = pit_listing[mcap_keep_src + (["market"] if "market" in pit_listing.columns else [])].copy()

    # 3. 60d avg trading value — pass PIT tickers explicitly so the function
    # does not fall back to FDR-current via its `tickers=None` branch.
    pit_tickers = listing["ticker"].astype(str).tolist()
    avg_val = compute_avg_trading_value_60d(
        rd,
        lookback_days=60,
        refresh_days=int(cfg.get("avg_value_refresh_days", 7)),
        tickers=pit_tickers,
        fallback_max_days=int(cfg.get("avg_value_fallback_max_days", 0)),
    )

    # 4. Listed months (PIT-correct from kr_pit_universe)
    listed = compute_listed_months(rd, pit_tickers)

    # 5. Merge — mcap fields from mktcap only
    mcap_keep = [c for c in ("ticker", "market_cap", "listed_shares")
                  if c in mktcap.columns]
    df = listing.merge(mktcap[mcap_keep], on="ticker", how="left")
    df = df.merge(avg_val[["ticker", "avg_trading_value", "days_observed"]],
                  on="ticker", how="left")
    df = df.merge(listed, on="ticker", how="left")
    df["rebalance_date"] = rd
    df = df.rename(columns={
        "avg_trading_value": "avg_trading_value_60d",
        "market": "exchange",
    })

    # 6. Apply exclusions
    excl_reason = pd.Series([""] * len(df), index=df.index)

    if cfg.get("exclude_preferred", True):
        mask = df["ticker"].astype(str).map(is_preferred)
        excl_reason.loc[mask & (excl_reason == "")] = "preferred"
    if cfg.get("exclude_spac", True):
        mask = df["name"].map(is_spac)
        excl_reason.loc[mask & (excl_reason == "")] = "spac"
    if cfg.get("exclude_reits", True):
        mask = df["name"].map(is_reit)
        excl_reason.loc[mask & (excl_reason == "")] = "reit"
    # ETF/ETN: pykrx get_market_ticker_list market="KOSPI" 이미 일반 종목만 반환하므로
    # 별도 제외 불필요. 안전 차원으로 이름 패턴 체크는 P1에서 추가.

    # 7. Apply soft filters
    min_val = float(cfg.get("min_trading_value_60d_krw", DEFAULT_MIN_TRADING_VALUE_60D_KRW))
    min_cap = float(cfg.get("min_market_cap_krw", DEFAULT_MIN_MARKET_CAP_KRW))
    min_lm = int(cfg.get("min_listed_months", DEFAULT_MIN_LISTED_MONTHS))

    mask_low_value = (df["avg_trading_value_60d"].fillna(0) < min_val)
    mask_low_cap = (df["market_cap"].fillna(0) < min_cap)
    mask_too_new = (df["listed_months"].fillna(0) < min_lm)

    excl_reason.loc[mask_low_value & (excl_reason == "")] = "low_trading_value"
    excl_reason.loc[mask_low_cap & (excl_reason == "")] = "low_market_cap"
    excl_reason.loc[mask_too_new & (excl_reason == "")] = "too_new"

    df["exclude_reason"] = excl_reason
    df["eligible"] = (excl_reason == "")

    # 8. Order columns
    df = df[[
        "rebalance_date", "ticker", "name", "exchange", "market_cap",
        "listed_shares", "avg_trading_value_60d", "days_observed",
        "listed_months", "eligible", "exclude_reason",
    ]].sort_values(["eligible", "market_cap"], ascending=[False, False]).reset_index(drop=True)

    n_eligible = int(df["eligible"].sum())
    n_total = len(df)
    log(f"[universe] {rd.strftime('%Y-%m-%d')}: {n_eligible}/{n_total} eligible "
        f"(KOSPI {((df['exchange']=='KOSPI') & df['eligible']).sum()}, "
        f"KOSDAQ {((df['exchange']=='KOSDAQ') & df['eligible']).sum()})")
    return df


def build_universe_monthly_v0(
    start_date: str = "2016-01-01",
    end_date: Optional[str] = None,
    cfg: Optional[dict] = None,
) -> pd.DataFrame:
    """Build full historical universe panel, monthly snapshots.

    Returns long-format DataFrame indexed by (rebalance_date, ticker).
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    end_date = end_date or datetime.now().strftime("%Y-%m-%d")

    log(f"[universe] build_universe_monthly_v0 {start_date} → {end_date}")
    month_ends = fetch_month_end_business_days(
        pd.Timestamp(start_date).strftime("%Y%m%d"),
        pd.Timestamp(end_date).strftime("%Y%m%d"),
    )
    log(f"[universe] {len(month_ends)} month-ends to process")

    frames = []
    for i, me in enumerate(month_ends, 1):
        log(f"[universe] [{i}/{len(month_ends)}] snapshot {me.strftime('%Y-%m-%d')}")
        snap = build_universe_snapshot(me, cfg=cfg)
        if not snap.empty:
            frames.append(snap)

    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)

    # Cache full panel
    cache_path = DATA_ROOT / "feature_store" / "universe_panel_v0.parquet"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        panel.to_parquet(cache_path, index=False)
        log(f"[universe] saved panel ({len(panel)} rows) -> {cache_path}")
    except Exception as e:
        log(f"[universe] panel save fail: {e}", level="WARN")

    return panel


# ---------------------------------------------------------------------------
# Sanity test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    # Single recent month-end snapshot
    yest = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    print(f"Building universe snapshot for {yest}...")
    df = build_universe_snapshot(yest)
    if df.empty:
        print("FAIL: empty result")
        sys.exit(1)
    print(f"\nTotal rows: {len(df)}")
    print(f"Eligible: {df['eligible'].sum()}")
    print(f"By exchange:\n{df.groupby(['exchange', 'eligible']).size()}")
    print(f"\nExclude reasons:\n{df['exclude_reason'].value_counts()}")
    print(f"\nTop 10 eligible by market cap:")
    print(df[df['eligible']].head(10)[['ticker', 'name', 'exchange', 'market_cap']])
