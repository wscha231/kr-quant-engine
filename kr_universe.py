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
from kr_pykrx_client import (
    FDR_AVAILABLE,
    fetch_business_days,
    fetch_listing,
    fetch_market_cap_market,
    fetch_month_end_business_days,
    fetch_daily_ohlcv_market,
    fetch_ticker_history,
)

try:
    import FinanceDataReader as _fdr
except ImportError:
    _fdr = None


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
) -> pd.DataFrame:
    """For all KOSPI+KOSDAQ tickers, compute avg 거래대금 over last N business days
    ending on rebalance_date.

    Returns DataFrame: ticker, avg_trading_value, days_observed.

    Uses pykrx daily OHLCV per market for the lookback window.
    Caches per (rebalance_date, lookback_days) at cache_misc/.
    """
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

    # Determine business day window
    end = rebalance_date
    start = end - timedelta(days=int(lookback_days * 1.6))   # generous, holidays
    bdays = fetch_business_days(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    bdays = sorted(d for d in bdays if d <= end)[-lookback_days:]
    if len(bdays) < 5:
        log(f"[universe] insufficient bdays for {end}: {len(bdays)}", level="WARN")
        return pd.DataFrame(columns=["ticker", "avg_trading_value", "days_observed"])

    # Aggregate daily value per ticker
    accum: dict[str, list[float]] = {}
    for d in bdays:
        df = fetch_daily_ohlcv_market(d.strftime("%Y%m%d"), market="ALL", refresh_days=30)
        if df.empty or "value" not in df.columns:
            continue
        for tk, val in zip(df["ticker"].astype(str), df["value"].astype(float)):
            accum.setdefault(tk, []).append(val)

    rows = []
    for tk, vals in accum.items():
        rows.append({
            "ticker": tk,
            "avg_trading_value": float(sum(vals) / max(1, len(vals))),
            "days_observed": len(vals),
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        try:
            out.to_parquet(cache_path, index=False)
        except Exception as e:
            log(f"[universe] cache write fail {cache_path.name}: {e}", level="WARN")
    return out


# ---------------------------------------------------------------------------
# Listing age
# ---------------------------------------------------------------------------
_LISTING_DATE_CACHE: dict[str, pd.Timestamp] = {}


def _resolve_listing_date(ticker: str) -> Optional[pd.Timestamp]:
    """Return a ticker's listing date (KRX 상장일) using FDR's StockListing
    when available, else the earliest available OHLCV date as fallback.

    Cached in-process so monthly snapshots don't re-fetch.
    """
    tk = str(ticker).zfill(6)
    if tk in _LISTING_DATE_CACHE:
        return _LISTING_DATE_CACHE[tk]

    listing_date: Optional[pd.Timestamp] = None

    # 1. FDR StockListing (has 'ListingDate' on KRX/KOSPI/KOSDAQ feeds)
    if _fdr is not None:
        try:
            for mkt in ("KRX", "KOSPI", "KOSDAQ"):
                try:
                    df = _fdr.StockListing(mkt)
                except Exception:
                    continue
                if df is None or df.empty:
                    continue
                if "Code" not in df.columns:
                    continue
                df = df.copy()
                df["Code"] = df["Code"].astype(str).str.zfill(6)
                hit = df[df["Code"] == tk]
                if hit.empty:
                    continue
                # FDR uses 'ListingDate' (varies by feed); fall back to common alts
                for col in ("ListingDate", "Listed", "ListingDay"):
                    if col in hit.columns and pd.notna(hit.iloc[0][col]):
                        listing_date = pd.Timestamp(hit.iloc[0][col]).normalize()
                        break
                if listing_date is not None:
                    break
        except Exception:
            listing_date = None

    # 2. Fallback: earliest OHLCV record (cached) — bounded lookback
    if listing_date is None:
        try:
            history = fetch_ticker_history(tk, "20000101",
                                            datetime.now().strftime("%Y%m%d"),
                                            refresh_days=30)
            if history is not None and not history.empty and "date" in history.columns:
                listing_date = pd.Timestamp(history["date"].min()).normalize()
        except Exception:
            pass

    _LISTING_DATE_CACHE[tk] = listing_date
    return listing_date


def compute_listed_months(
    rebalance_date: pd.Timestamp,
    tickers: list[str],
    cache_path: Optional[Path] = None,
) -> pd.DataFrame:
    """Compute listed_months as (rebalance_date - listing_date) in months.

    Sources, in order:
      1. FDR StockListing (KRX/KOSPI/KOSDAQ) — official 상장일 when available
      2. Earliest OHLCV record from cached pykrx/FDR history — fallback proxy
      3. None → listed_months = 999 (let downstream features filter on history)

    The previous P0 stub returned 999 for every ticker, silently disabling
    the `min_listed_months=12` filter. This version drives the filter for
    real on the first month-end build, then reuses an in-process cache.
    """
    rd = pd.Timestamp(rebalance_date).normalize()
    rows = []
    unresolved = 0
    for tk in tickers:
        listing_date = _resolve_listing_date(tk)
        if listing_date is None:
            rows.append({"ticker": tk, "listed_months": 999,
                          "listing_date_resolved": False})
            unresolved += 1
            continue
        delta_days = max(0, (rd - listing_date).days)
        listed_months = int(delta_days // 30)
        rows.append({"ticker": tk, "listed_months": listed_months,
                      "listing_date_resolved": True,
                      "listing_date": listing_date})
    out = pd.DataFrame(rows)
    if unresolved:
        log(f"[universe] compute_listed_months: {unresolved}/{len(tickers)} "
            f"tickers unresolved (FDR + history both empty) -> 999 fallback",
            level="WARN")
    return out


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
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    rd = pd.Timestamp(rebalance_date).normalize()
    log(f"[universe] build snapshot for {rd.strftime('%Y-%m-%d')}")

    # 1. Listing (KOSPI + KOSDAQ)
    listing = fetch_listing(rd.strftime("%Y%m%d"), market="ALL", refresh_days=30)
    if listing.empty:
        log(f"[universe] empty listing for {rd}", level="WARN")
        return pd.DataFrame()

    # Filter exchanges per cfg
    exchanges = [e.upper() for e in cfg.get("exchanges", list(EXCHANGES))]
    listing = listing[listing["market"].str.upper().isin(exchanges)].copy()

    # 2. Market cap snapshot (already merged in mktcap fetch)
    mktcap = fetch_market_cap_market(rd.strftime("%Y%m%d"), market="ALL", refresh_days=30)
    if mktcap.empty:
        log(f"[universe] empty mktcap for {rd}", level="WARN")
        return pd.DataFrame()

    # Keep only KOSPI/KOSDAQ tickers
    mktcap = mktcap[mktcap["market"].str.upper().isin(exchanges)].copy()

    # 3. 60d avg trading value
    avg_val = compute_avg_trading_value_60d(rd, lookback_days=60)

    # 4. Listed months (P0 stub)
    listed = compute_listed_months(rd, listing["ticker"].astype(str).tolist())

    # 5. Merge
    df = listing.merge(
        mktcap[["ticker", "market_cap", "listed_shares"]],
        on="ticker", how="left",
    )
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
