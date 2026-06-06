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
import re
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

# FinanceDataReader fallback — pykrx 1.2.7 is broken vs current KRX site
# (2025 KRX UI redesign impact). FDR is the working source for listing + prices.
try:
    import FinanceDataReader as _fdr
    FDR_AVAILABLE = True
except ImportError:
    _fdr = None
    FDR_AVAILABLE = False


CACHE_DIR = DATA_ROOT / "cache_pykrx"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
_TICKER_HISTORY_CACHE_INDEX: dict[str, list[tuple[int, int, Path]]] | None = None
_INDEX_HISTORY_CACHE_INDEX: dict[str, list[tuple[int, int, Path]]] | None = None
_MARCAP_YEAR_CACHE: dict[int, pd.DataFrame] = {}
_MARCAP_YEAR_BY_TICKER_CACHE: dict[int, dict[str, pd.DataFrame]] = {}


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
        global _TICKER_HISTORY_CACHE_INDEX, _INDEX_HISTORY_CACHE_INDEX
        if path.name.startswith("ticker_"):
            _TICKER_HISTORY_CACHE_INDEX = None
        if path.name.startswith("index_"):
            _INDEX_HISTORY_CACHE_INDEX = None
    except Exception as e:
        log(f"[pykrx_client] cache write fail {path.name}: {e}", level="WARN")


def _ticker_history_cache_index() -> dict[str, list[tuple[int, int, Path]]]:
    """Index ticker history caches by ticker and covered date range."""
    global _TICKER_HISTORY_CACHE_INDEX
    if _TICKER_HISTORY_CACHE_INDEX is not None:
        return _TICKER_HISTORY_CACHE_INDEX
    pattern = re.compile(r"^ticker_(\d{6})_(\d{8})_(\d{8})\.parquet$")
    index: dict[str, list[tuple[int, int, Path]]] = {}
    for path in CACHE_DIR.glob("ticker_*.parquet"):
        match = pattern.match(path.name)
        if not match:
            continue
        try:
            start_i = int(match.group(2))
            end_i = int(match.group(3))
        except ValueError:
            continue
        index.setdefault(match.group(1), []).append((start_i, end_i, path))
    _TICKER_HISTORY_CACHE_INDEX = index
    return index


def _index_history_cache_index() -> dict[str, list[tuple[int, int, Path]]]:
    """Index cached index OHLCV files by index ticker and covered date range."""
    global _INDEX_HISTORY_CACHE_INDEX
    if _INDEX_HISTORY_CACHE_INDEX is not None:
        return _INDEX_HISTORY_CACHE_INDEX
    pattern = re.compile(r"^index_([^_]+)_(\d{8})_(\d{8})\.parquet$")
    index: dict[str, list[tuple[int, int, Path]]] = {}
    for path in CACHE_DIR.glob("index_*.parquet"):
        match = pattern.match(path.name)
        if not match:
            continue
        try:
            start_i = int(match.group(2))
            end_i = int(match.group(3))
        except ValueError:
            continue
        index.setdefault(match.group(1), []).append((start_i, end_i, path))
    _INDEX_HISTORY_CACHE_INDEX = index
    return index


def _load_covering_ticker_history_cache(
    ticker: str,
    start: str,
    end: str,
) -> Optional[pd.DataFrame]:
    """Load the narrowest existing ticker cache that covers start..end.

    Historical feature rebuilds request many overlapping windows. Exact cache
    keys make those rebuilds unnecessarily slow; broad cached histories are
    immutable for already-observed dates and can be sliced PIT-safely.
    """
    wanted_start = int(_yyyymmdd(start))
    wanted_end = int(_yyyymmdd(end))
    best: tuple[int, Path] | None = None
    for cache_start, cache_end, path in _ticker_history_cache_index().get(str(ticker).zfill(6), []):
        if cache_start <= wanted_start and cache_end >= wanted_end:
            span = cache_end - cache_start
            if best is None or span < best[0]:
                best = (span, path)
    if best is None:
        return None
    try:
        df = pd.read_parquet(best[1])
    except Exception as e:
        log(f"[pykrx_client] covering ticker cache read fail {best[1].name}: {e}", level="WARN")
        return None
    if df.empty or "date" not in df.columns:
        return None
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])
    out = out[
        (out["date"] >= pd.Timestamp(start))
        & (out["date"] <= pd.Timestamp(end))
    ].copy()
    if out.empty:
        return None
    if "ticker" not in out.columns:
        out["ticker"] = str(ticker).zfill(6)
    return out


def _load_covering_index_history_cache(
    ticker: str,
    start: str,
    end: str,
) -> Optional[pd.DataFrame]:
    wanted_start = int(_yyyymmdd(start))
    wanted_end = int(_yyyymmdd(end))
    best: tuple[int, Path] | None = None
    for cache_start, cache_end, path in _index_history_cache_index().get(str(ticker), []):
        if cache_start <= wanted_start and cache_end >= wanted_end:
            span = cache_end - cache_start
            if best is None or span < best[0]:
                best = (span, path)
    if best is None:
        return None
    try:
        df = pd.read_parquet(best[1])
    except Exception as e:
        log(f"[pykrx_client] covering index cache read fail {best[1].name}: {e}", level="WARN")
        return None
    if df.empty or "date" not in df.columns:
        return None
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])
    out = out[
        (out["date"] >= pd.Timestamp(start))
        & (out["date"] <= pd.Timestamp(end))
    ].copy()
    return out if not out.empty else None


def _load_marcap_year(year: int) -> pd.DataFrame:
    """Load local yearly marcap cache in ticker-history format."""
    year = int(year)
    if year in _MARCAP_YEAR_CACHE:
        return _MARCAP_YEAR_CACHE[year]
    path = CACHE_DIR / f"marcap_{year}.parquet"
    if not path.exists():
        _MARCAP_YEAR_CACHE[year] = pd.DataFrame()
        return _MARCAP_YEAR_CACHE[year]
    cols = [
        "Code", "Date", "Open", "High", "Low", "Close", "Volume",
        "Amount", "ChangesRatio", "Market",
    ]
    try:
        df = pd.read_parquet(path, columns=cols)
    except Exception:
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            log(f"[pykrx_client] marcap cache read fail {path.name}: {e}", level="WARN")
            _MARCAP_YEAR_CACHE[year] = pd.DataFrame()
            return _MARCAP_YEAR_CACHE[year]
        df = df[[c for c in cols if c in df.columns]]
    if df.empty or not {"Code", "Date", "Close"}.issubset(df.columns):
        _MARCAP_YEAR_CACHE[year] = pd.DataFrame()
        return _MARCAP_YEAR_CACHE[year]
    out = df.rename(columns={
        "Code": "ticker",
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "Amount": "value",
        "ChangesRatio": "change_pct",
        "Market": "market",
    }).copy()
    out["ticker"] = out["ticker"].astype(str).str.zfill(6)
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["date", "close"])
    if "market" in out.columns:
        out["market"] = out["market"].astype(str).str.upper()
        out = out[out["market"].isin(["KOSPI", "KOSDAQ"])].copy()
    _MARCAP_YEAR_CACHE[year] = out.sort_values(["ticker", "date"]).reset_index(drop=True)
    _MARCAP_YEAR_BY_TICKER_CACHE[year] = {
        str(tk): group.reset_index(drop=True)
        for tk, group in _MARCAP_YEAR_CACHE[year].groupby("ticker", sort=False)
    }
    return _MARCAP_YEAR_CACHE[year]


def _load_ticker_history_from_marcap_cache(
    ticker: str,
    start: str,
    end: str,
) -> Optional[pd.DataFrame]:
    """Return ticker OHLCV from local marcap_YYYY caches when available."""
    tk = str(ticker).zfill(6)
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    frames = []
    for year in range(int(start_ts.year), int(end_ts.year) + 1):
        year_df = _load_marcap_year(year)
        if year_df.empty:
            continue
        ticker_df = _MARCAP_YEAR_BY_TICKER_CACHE.get(year, {}).get(tk)
        if ticker_df is None or ticker_df.empty:
            continue
        sub = ticker_df[
            (ticker_df["date"] >= start_ts)
            & (ticker_df["date"] <= end_ts)
        ].copy()
        if not sub.empty:
            frames.append(sub)
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if "value" not in out.columns and {"close", "volume"}.issubset(out.columns):
        out["value"] = (
            pd.to_numeric(out["close"], errors="coerce").fillna(0)
            * pd.to_numeric(out["volume"], errors="coerce").fillna(0)
        )
    return out


def _marcap_year_cache_covers(start: str, end: str) -> bool:
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    for year in range(int(start_ts.year), int(end_ts.year) + 1):
        if not (CACHE_DIR / f"marcap_{year}.parquet").exists():
            return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _fetch_listing_via_pykrx(date: str, market: str) -> pd.DataFrame:
    """Original pykrx implementation. May return 0 rows if pykrx broken."""
    if not PYKRX_AVAILABLE:
        return pd.DataFrame()
    rows = []
    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    for mkt in markets:
        try:
            tickers = _pykrx_stock.get_market_ticker_list(_yyyymmdd(date), market=mkt)
        except Exception:
            tickers = []
        for tk in tickers:
            try:
                name = _pykrx_stock.get_market_ticker_name(tk)
            except Exception:
                name = ""
            rows.append({"ticker": tk, "name": name, "market": mkt})
    return pd.DataFrame(rows)


def _fetch_listing_via_fdr(market: str) -> pd.DataFrame:
    """FinanceDataReader fallback. Note: FDR returns CURRENT listing only,
    not historical. For PIT membership, caller needs additional logic.
    Includes market_cap, listed_shares as bonus columns."""
    if not FDR_AVAILABLE:
        return pd.DataFrame()
    rows = []
    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    for mkt in markets:
        try:
            df = _fdr.StockListing(mkt)
            if df is None or df.empty:
                continue
            df = df.copy()
            # Standardize column names
            rename_map = {
                "Code": "ticker", "Name": "name", "Market": "market_src",
                "Marcap": "market_cap", "Stocks": "listed_shares",
                "Close": "close", "Volume": "volume", "Amount": "value",
            }
            df = df.rename(columns={k: v for k, v in rename_map.items()
                                      if k in df.columns})
            df["market"] = mkt
            keep = [c for c in ("ticker", "name", "market", "market_cap",
                                 "listed_shares", "close", "volume", "value")
                    if c in df.columns]
            df = df[keep]
            # Ensure ticker is 6-digit string
            if "ticker" in df.columns:
                df["ticker"] = df["ticker"].astype(str).str.zfill(6)
            rows.append(df)
        except Exception as e:
            log(f"[pykrx_client] FDR fail for {mkt}: {e}", level="WARN")
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def fetch_listing(date: str, market: str = "ALL", refresh_days: int = 1) -> pd.DataFrame:
    """Return DataFrame of all listed tickers.

    Columns: ticker (6-digit code), name (한글명), market (KOSPI/KOSDAQ).
    Bonus from FDR fallback: market_cap, listed_shares, close, volume, value.

    Tries pykrx first (returns 0 rows on KRX site failure as of 2025+),
    falls back to FinanceDataReader. Note: FDR returns CURRENT listing,
    so historical PIT membership requires explicit listing-history file.
    """
    cache = _cache_path("listing", date, market)
    cached = _load_cached(cache, refresh_days)
    if cached is not None and not cached.empty:
        return cached

    df = pd.DataFrame()
    if PYKRX_AVAILABLE:
        df = _fetch_listing_via_pykrx(date, market)
    if df.empty and FDR_AVAILABLE:
        log(f"[pykrx_client] pykrx empty, falling back to FDR for listing", level="WARN")
        df = _fetch_listing_via_fdr(market)
    if df.empty:
        if not PYKRX_AVAILABLE and not FDR_AVAILABLE:
            raise RuntimeError("Neither pykrx nor FinanceDataReader available")

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


def fetch_market_cap_market(
    date: str,
    market: str = "ALL",
    refresh_days: int = 1,
    allow_fdr_fallback: bool = True,
) -> pd.DataFrame:
    """Market cap + 주식수 snapshot.

    Columns: ticker, market_cap, listed_shares, volume, value, market, date.

    Tries pykrx first; falls back to FDR StockListing on failure.
    NOTE: FDR returns CURRENT mcap only (not historical). For backtest use
    cases needing historical mcap, this returns current snapshot — acceptable
    only if `date` is recent. Historical-PIT mcap requires monthly snapshots
    over time and reusing them.
    """
    cache = _cache_path("mktcap", date, market)
    cached = _load_cached(cache, refresh_days)
    if cached is not None and not cached.empty:
        return cached

    out = pd.DataFrame()
    if PYKRX_AVAILABLE:
        frames = []
        markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
        for mkt in markets:
            d = _yyyymmdd(date)
            try:
                df = _pykrx_stock.get_market_cap_by_ticker(d, market=mkt)
            except Exception:
                df = None
            if df is None or df.empty:
                continue
            df = df.reset_index().rename(columns={
                "티커": "ticker", "시가총액": "market_cap", "거래량": "volume",
                "거래대금": "value", "상장주식수": "listed_shares",
            })
            df["market"] = mkt
            frames.append(df)
        if frames:
            out = pd.concat(frames, ignore_index=True)

    if out.empty and FDR_AVAILABLE and allow_fdr_fallback:
        log(f"[pykrx_client] pykrx empty, falling back to FDR for mcap", level="WARN")
        out = _fetch_listing_via_fdr(market)
        # FDR returns: ticker, name, market, market_cap, listed_shares, close, volume, value
        if not out.empty:
            keep = [c for c in ("ticker", "market_cap", "listed_shares",
                                 "volume", "value", "market")
                    if c in out.columns]
            out = out[keep]

    if not out.empty:
        out["date"] = pd.Timestamp(date).normalize()
    _save_cache(out, cache)
    log(f"[pykrx_client] fetch_market_cap_market({date}, {market}) -> {len(out)} rows")
    return out


def fetch_per_pbr_eps_bps(date: str, market: str = "ALL", refresh_days: int = 1) -> pd.DataFrame:
    """PER / PBR / EPS / BPS / DPS / DivYld snapshot.

    Note: pykrx returns trailing PER/PBR (TTM), 0 for null.
    Returns EMPTY DataFrame on pykrx failure (KRX site changes 2025+).
    Caller (kr_features.add_basic_value) handles empty by zero-filling cols.
    """
    cache = _cache_path("perpbr", date, market)
    cached = _load_cached(cache, refresh_days)
    if cached is not None:
        return cached

    if not PYKRX_AVAILABLE:
        # Empty result — caller handles
        return pd.DataFrame()

    frames = []
    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    for mkt in markets:
        d = _yyyymmdd(date)
        try:
            df = _pykrx_stock.get_market_fundamental_by_ticker(d, market=mkt)
        except Exception as e:
            log(f"[pykrx_client] fundamental fetch fail {mkt} {d}: "
                f"{type(e).__name__} (KRX site changed?)", level="WARN")
            df = None
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


_FDR_INDEX_MAP = {
    "1001": "KS11",      # KOSPI
    "1028": "KS200",     # KOSPI 200
    "2001": "KQ11",      # KOSDAQ
    "2203": "KQ11",      # KOSDAQ150 fallback proxy; FDR has no stable KQ150 symbol.
    "1003": "VKOSPI",    # KRX VKOSPI (FDR may not have)
}


def fetch_index_ohlcv(ticker: str, start: str, end: str, refresh_days: int = 1) -> pd.DataFrame:
    """KRX index daily OHLCV (KOSPI 1001, KOSPI200 1028, KOSDAQ 2001 등).

    Returns date-indexed DataFrame: date, open, high, low, close, volume.
    Tries pykrx first; falls back to FDR mapping.
    """
    cache_name = f"index_{ticker}_{_yyyymmdd(start)}_{_yyyymmdd(end)}"
    path = CACHE_DIR / f"{cache_name}.parquet"
    cached = _load_cached(path, refresh_days)
    if cached is not None and not cached.empty:
        return cached
    covering = _load_covering_index_history_cache(ticker, start, end)
    if covering is not None and not covering.empty:
        return covering

    df = pd.DataFrame()
    if PYKRX_AVAILABLE:
        try:
            df = _pykrx_stock.get_index_ohlcv_by_date(
                _yyyymmdd(start), _yyyymmdd(end), ticker,
            )
            if df is not None and not df.empty:
                df = df.reset_index().rename(columns={
                    "날짜": "date", "시가": "open", "고가": "high",
                    "저가": "low", "종가": "close", "거래량": "volume",
                    "거래대금": "value",
                })
                df["index_ticker"] = ticker
            else:
                df = pd.DataFrame()
        except Exception:
            df = pd.DataFrame()

    if df.empty and FDR_AVAILABLE:
        fdr_code = _FDR_INDEX_MAP.get(ticker, ticker)
        try:
            s = pd.Timestamp(start).strftime("%Y-%m-%d")
            e = pd.Timestamp(end).strftime("%Y-%m-%d")
            fdr_df = _fdr.DataReader(fdr_code, s, e)
            if fdr_df is not None and not fdr_df.empty:
                fdr_df = fdr_df.reset_index().rename(columns={
                    "Date": "date", "Open": "open", "High": "high",
                    "Low": "low", "Close": "close", "Volume": "volume",
                })
                fdr_df["index_ticker"] = ticker
                df = fdr_df
        except Exception as e:
            log(f"[pykrx_client] FDR index fail {ticker} ({fdr_code}): {e}",
                level="WARN")

    if df.empty:
        return pd.DataFrame()

    _save_cache(df, path)
    log(f"[pykrx_client] fetch_index_ohlcv({ticker}, {start}, {end}) -> {len(df)} rows")
    return df


def fetch_ticker_history(ticker: str, start: str, end: str, refresh_days: int = 1) -> pd.DataFrame:
    """Single-ticker daily OHLCV (long history). Used for momentum lookback.

    Tries pykrx first, falls back to FDR (FinanceDataReader.DataReader).
    Cache key includes ticker + date range. Stale > refresh_days → refetch.
    """
    cache_name = f"ticker_{ticker}_{_yyyymmdd(start)}_{_yyyymmdd(end)}"
    path = CACHE_DIR / f"{cache_name}.parquet"
    marcap = _load_ticker_history_from_marcap_cache(ticker, start, end)
    if marcap is not None and not marcap.empty:
        if pd.to_datetime(marcap["date"], errors="coerce").max() >= pd.Timestamp(end).normalize():
            return marcap
    if _marcap_year_cache_covers(start, end):
        return pd.DataFrame()

    cached = _load_cached(path, refresh_days)
    if cached is not None and not cached.empty:
        return cached
    covering = _load_covering_ticker_history_cache(ticker, start, end)
    if covering is not None and not covering.empty:
        return covering

    df = pd.DataFrame()
    if PYKRX_AVAILABLE:
        try:
            df = _pykrx_stock.get_market_ohlcv_by_date(
                _yyyymmdd(start), _yyyymmdd(end), ticker,
            )
            if df is not None and not df.empty:
                df = df.reset_index().rename(columns={
                    "날짜": "date", "시가": "open", "고가": "high", "저가": "low",
                    "종가": "close", "거래량": "volume", "거래대금": "value",
                    "등락률": "change_pct",
                })
                df["ticker"] = ticker
            else:
                df = pd.DataFrame()
        except Exception:
            df = pd.DataFrame()

    if df.empty and FDR_AVAILABLE:
        try:
            s = pd.Timestamp(start).strftime("%Y-%m-%d")
            e = pd.Timestamp(end).strftime("%Y-%m-%d")
            fdr_df = _fdr.DataReader(ticker, s, e)
            if fdr_df is not None and not fdr_df.empty:
                fdr_df = fdr_df.reset_index().rename(columns={
                    "Date": "date", "Open": "open", "High": "high",
                    "Low": "low", "Close": "close", "Volume": "volume",
                    "Change": "change_pct",
                })
                fdr_df["ticker"] = ticker
                df = fdr_df
        except Exception as e:
            log(f"[pykrx_client] FDR ticker history fail {ticker}: {e}", level="WARN")

    if df.empty:
        return pd.DataFrame()

    # Universal post-processing: derive `value` (거래대금 in KRW) if missing.
    # Both pykrx 1.2.7 (KRX broken) and FDR omit `value`. We compute it as
    # close × volume (close-based proxy; pykrx's actual value uses VWAP — small
    # difference for our universe filter use case).
    if "value" not in df.columns and "close" in df.columns and "volume" in df.columns:
        df["value"] = (
            pd.to_numeric(df["close"], errors="coerce").fillna(0)
            * pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        )

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
