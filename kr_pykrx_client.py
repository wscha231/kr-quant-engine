"""kr_pykrx_client — pykrx wrapper + parquet caching layer.

Provides:
- fetch_listing(date) — KOSPI + KOSDAQ ticker list + market cap snapshot
- fetch_daily_ohlcv_market(date, market) — daily OHLCV for all tickers in a market
- fetch_per_pbr_eps_bps(date, market) — fundamentals snapshot
- fetch_foreign_inst_flow(date, market) — 외인/기관 매매대금
- fetch_index_ohlcv(ticker, start, end) — 지수 일별 (KOSPI200 등)
- fetch_listed_history(start, end) — 상장/상폐 이벤트

Caching strategy: per-day parquet files in cache_pykrx/ keyed by
(YYYY-MM-DD, market, table). TTL controlled by cfg["pykrx_refresh_days"].

Defensive: if pykrx not installed, all fetch functions raise RuntimeError
with install instructions. Cache reads still work without pykrx.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from kr_config import DATA_ROOT
from kr_helpers import log, safe_float

# Lazy import pykrx — defer error until actual fetch
try:
    from pykrx import stock as _pykrx_stock
    PYKRX_AVAILABLE = True
except ImportError:
    _pykrx_stock = None
    PYKRX_AVAILABLE = False


CACHE_DIR = DATA_ROOT / "cache_pykrx"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _require_pykrx() -> None:
    if not PYKRX_AVAILABLE:
        raise RuntimeError(
            "pykrx not installed. Run: py -3 -m pip install pykrx>=1.0.45"
        )


def _yyyymmdd(date: str | datetime | pd.Timestamp) -> str:
    """Coerce to YYYYMMDD format (pykrx convention)."""
    if isinstance(date, str):
        # Accept "2024-01-15" or "20240115"
        d = date.replace("-", "")
        if len(d) == 8 and d.isdigit():
            return d
        return pd.Timestamp(date).strftime("%Y%m%d")
    return pd.Timestamp(date).strftime("%Y%m%d")


def _cache_path(name: str, date: str, market: str = "ALL") -> Path:
    d = _yyyymmdd(date)
    return CACHE_DIR / f"{name}_{market}_{d}.parquet"


def _load_cached(path: Path, refresh_days: int = 1) -> Optional[pd.DataFrame]:
    """Load parquet if exists and within refresh_days TTL. Else None."""
    if not path.exists():
        return None
    age_seconds = time.time() - path.stat().st_mtime
    if age_seconds > refresh_days * 86400:
        return None
    try:
        return pd.read_parquet(path)
    except Exception as e:
        log(f"[pykrx_client] cache read fail {path.name}: {e}", level="WARN")
        return None


def _save_cache(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_parquet(path, index=False)
    except Exception as e:
        log(f"[pykrx_client] cache write fail {path.name}: {e}", level="WARN")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_listing(date: str, market: str = "ALL", refresh_days: int = 1) -> pd.DataFrame:
    """Return DataFrame of all listed tickers as of `date`.

    Columns: ticker (6-digit code), name (한글명), market (KOSPI/KOSDAQ).
    """
    cache = _cache_path("listing", date, market)
    cached = _load_cached(cache, refresh_days)
    if cached is not None:
        return cached

    _require_pykrx()
    rows = []
    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    for mkt in markets:
        tickers = _pykrx_stock.get_market_ticker_list(_yyyymmdd(date), market=mkt)
        for tk in tickers:
            try:
                name = _pykrx_stock.get_market_ticker_name(tk)
            except Exception:
                name = ""
            rows.append({"ticker": tk, "name": name, "market": mkt})
    df = pd.DataFrame(rows)
    _save_cache(df, cache)
    log(f"[pykrx_client] fetch_listing({date}, {market}) -> {len(df)} rows")
    return df


def fetch_daily_ohlcv_market(date: str, market: str = "ALL", refresh_days: int = 1) -> pd.DataFrame:
    """Daily OHLCV snapshot for all tickers in a market on `date`.

    Columns: ticker, open, high, low, close, volume, value (거래대금), change_pct.
    pykrx call: stock.get_market_ohlcv_by_ticker(date, market).
    """
    cache = _cache_path("ohlcv", date, market)
    cached = _load_cached(cache, refresh_days)
    if cached is not None:
        return cached

    _require_pykrx()
    frames = []
    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    for mkt in markets:
        d = _yyyymmdd(date)
        df = _pykrx_stock.get_market_ohlcv_by_ticker(d, market=mkt)
        if df is None or df.empty:
            log(f"[pykrx_client] empty OHLCV for {mkt} on {d}", level="WARN")
            continue
        df = df.reset_index().rename(columns={
            "티커": "ticker", "시가": "open", "고가": "high", "저가": "low",
            "종가": "close", "거래량": "volume", "거래대금": "value", "등락률": "change_pct",
        })
        df["market"] = mkt
        frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not out.empty:
        out["date"] = pd.Timestamp(date).normalize()
    _save_cache(out, cache)
    log(f"[pykrx_client] fetch_daily_ohlcv_market({date}, {market}) -> {len(out)} rows")
    return out


def fetch_market_cap_market(date: str, market: str = "ALL", refresh_days: int = 1) -> pd.DataFrame:
    """Market cap + 주식수 snapshot.

    Columns: ticker, market_cap, listed_shares, market.
    """
    cache = _cache_path("mktcap", date, market)
    cached = _load_cached(cache, refresh_days)
    if cached is not None:
        return cached

    _require_pykrx()
    frames = []
    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    for mkt in markets:
        d = _yyyymmdd(date)
        df = _pykrx_stock.get_market_cap_by_ticker(d, market=mkt)
        if df is None or df.empty:
            continue
        df = df.reset_index().rename(columns={
            "티커": "ticker", "시가총액": "market_cap", "거래량": "volume",
            "거래대금": "value", "상장주식수": "listed_shares",
        })
        df["market"] = mkt
        frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not out.empty:
        out["date"] = pd.Timestamp(date).normalize()
    _save_cache(out, cache)
    log(f"[pykrx_client] fetch_market_cap_market({date}, {market}) -> {len(out)} rows")
    return out


def fetch_per_pbr_eps_bps(date: str, market: str = "ALL", refresh_days: int = 1) -> pd.DataFrame:
    """PER / PBR / EPS / BPS / DPS / DivYld snapshot.

    Note: pykrx returns trailing PER/PBR (TTM), 0 for null.
    """
    cache = _cache_path("perpbr", date, market)
    cached = _load_cached(cache, refresh_days)
    if cached is not None:
        return cached

    _require_pykrx()
    frames = []
    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    for mkt in markets:
        d = _yyyymmdd(date)
        df = _pykrx_stock.get_market_fundamental_by_ticker(d, market=mkt)
        if df is None or df.empty:
            continue
        df = df.reset_index().rename(columns={
            "티커": "ticker", "BPS": "bps", "PER": "per", "PBR": "pbr",
            "EPS": "eps", "DIV": "dividend_yield_pct", "DPS": "dps",
        })
        df["market"] = mkt
        frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not out.empty:
        out["date"] = pd.Timestamp(date).normalize()
        # pykrx returns 0 for null PER/PBR — convert to NaN
        for col in ("per", "pbr", "eps", "dividend_yield_pct"):
            if col in out.columns:
                out.loc[out[col] == 0, col] = pd.NA
    _save_cache(out, cache)
    log(f"[pykrx_client] fetch_per_pbr_eps_bps({date}, {market}) -> {len(out)} rows")
    return out


def fetch_foreign_inst_flow(date: str, market: str = "ALL", refresh_days: int = 1) -> pd.DataFrame:
    """외국인/기관 일별 net 매매 (거래대금 기준).

    pykrx 함수: stock.get_market_net_purchases_of_equities()
    Columns: ticker, foreign_net_buy_value, inst_net_buy_value,
             individual_net_buy_value, market.
    """
    cache = _cache_path("flow", date, market)
    cached = _load_cached(cache, refresh_days)
    if cached is not None:
        return cached

    _require_pykrx()
    frames = []
    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    for mkt in markets:
        d = _yyyymmdd(date)
        try:
            df = _pykrx_stock.get_market_net_purchases_of_equities(d, d, market=mkt)
        except Exception as e:
            log(f"[pykrx_client] flow fetch fail {mkt} {d}: {e}", level="WARN")
            continue
        if df is None or df.empty:
            continue
        df = df.reset_index().rename(columns={
            "티커": "ticker",
            "종목명": "name",
            "외국인합계": "foreign_net_buy_value",
            "기관합계": "inst_net_buy_value",
            "개인": "individual_net_buy_value",
        })
        df["market"] = mkt
        frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not out.empty:
        out["date"] = pd.Timestamp(date).normalize()
    _save_cache(out, cache)
    log(f"[pykrx_client] fetch_foreign_inst_flow({date}, {market}) -> {len(out)} rows")
    return out


def fetch_index_ohlcv(ticker: str, start: str, end: str, refresh_days: int = 1) -> pd.DataFrame:
    """KRX index daily OHLCV (KOSPI 1001, KOSPI200 1028, KOSDAQ 2001 등).

    Returns date-indexed DataFrame: open, high, low, close, volume.
    """
    cache_name = f"index_{ticker}_{_yyyymmdd(start)}_{_yyyymmdd(end)}"
    path = CACHE_DIR / f"{cache_name}.parquet"
    cached = _load_cached(path, refresh_days)
    if cached is not None:
        return cached

    _require_pykrx()
    df = _pykrx_stock.get_index_ohlcv_by_date(_yyyymmdd(start), _yyyymmdd(end), ticker)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.reset_index().rename(columns={
        "날짜": "date", "시가": "open", "고가": "high", "저가": "low",
        "종가": "close", "거래량": "volume", "거래대금": "value",
    })
    df["index_ticker"] = ticker
    _save_cache(df, path)
    log(f"[pykrx_client] fetch_index_ohlcv({ticker}, {start}, {end}) -> {len(df)} rows")
    return df


def fetch_ticker_history(ticker: str, start: str, end: str, refresh_days: int = 1) -> pd.DataFrame:
    """Single-ticker daily OHLCV (long history). Used for momentum lookback.

    Cache key includes ticker + date range. Stale > refresh_days → refetch.
    """
    cache_name = f"ticker_{ticker}_{_yyyymmdd(start)}_{_yyyymmdd(end)}"
    path = CACHE_DIR / f"{cache_name}.parquet"
    cached = _load_cached(path, refresh_days)
    if cached is not None:
        return cached

    _require_pykrx()
    df = _pykrx_stock.get_market_ohlcv_by_date(_yyyymmdd(start), _yyyymmdd(end), ticker)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.reset_index().rename(columns={
        "날짜": "date", "시가": "open", "고가": "high", "저가": "low",
        "종가": "close", "거래량": "volume", "거래대금": "value", "등락률": "change_pct",
    })
    df["ticker"] = ticker
    _save_cache(df, path)
    return df


def fetch_business_days(start: str, end: str) -> list[pd.Timestamp]:
    """KRX business days between start and end (inclusive).

    Uses KOSPI index OHLCV as proxy (KRX trading day calendar).
    """
    df = fetch_index_ohlcv("1001", start, end, refresh_days=30)
    if df.empty:
        return []
    return [pd.Timestamp(d).normalize() for d in df["date"]]


def fetch_month_end_business_days(start: str, end: str) -> list[pd.Timestamp]:
    """Last KRX business day of each month between start and end."""
    days = fetch_business_days(start, end)
    if not days:
        return []
    s = pd.Series(days, name="date")
    df = pd.DataFrame({"date": s})
    df["ym"] = df["date"].dt.to_period("M")
    return df.groupby("ym")["date"].max().tolist()


# ---------------------------------------------------------------------------
# Sanity test (run as `py -3 kr_pykrx_client.py`)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    print(f"PYKRX_AVAILABLE: {PYKRX_AVAILABLE}")
    if not PYKRX_AVAILABLE:
        print("Install: py -3 -m pip install pykrx>=1.0.45")
        sys.exit(0)
    print("Fetching listing for today...")
    today = datetime.now().strftime("%Y%m%d")
    # Try yesterday in case today's market hasn't closed
    yest = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    df = fetch_listing(yest, "ALL")
    print(f"Listed: {len(df)} (KOSPI {(df['market']=='KOSPI').sum()}, KOSDAQ {(df['market']=='KOSDAQ').sum()})")
    print(df.head(5))
