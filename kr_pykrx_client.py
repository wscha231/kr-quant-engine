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

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

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

INDEX_SOURCE_META_SCHEMA = "kr-index-cache-source-v1"
TICKER_SOURCE_META_SCHEMA = "kr-ticker-cache-source-v1"
_KOSDAQ150_INDEX_TICKER = "2203"
_KOSDAQ150_FDR_DIRECT = "KRX-INDEX:2203"
_KOSDAQ150_SOURCE_IDENTITIES = {
    "PYKRX_KRX_INDEX_2203_NORMALIZED",
    "FINANCE_DATAREADER_KRX_INDEX_MDCSTAT00301_2203_NORMALIZED",
    "KRX_OPENAPI_KOSDAQ_DAILY_2203_NORMALIZED",
}


_TICKER_HISTORY_SOURCE_IDENTITIES = {
    "PYKRX_ADJUSTED_TRUE_NORMALIZED",
    "FINANCE_DATAREADER_NAVER_CLOSE_PROXY_NORMALIZED",
}


def ticker_cache_path(ticker: str, start: str, end: str) -> Path:
    """Source-segregated research cache for per-security long history."""
    return CACHE_DIR / (
        f"ticker_{ticker}_adjusted-proxy-v1_"
        f"{_yyyymmdd(start)}_{_yyyymmdd(end)}.parquet"
    )


def ticker_source_meta_path(cache_path: Path) -> Path:
    return Path(str(cache_path) + ".source.json")


def _load_ticker_cached(
    path: Path,
    *,
    ticker: str,
    refresh_days: int,
) -> Optional[pd.DataFrame]:
    cached = _load_cached(path, refresh_days)
    if cached is None or cached.empty:
        return cached
    meta_path = ticker_source_meta_path(path)
    if not meta_path.exists():
        log(
            f"[pykrx_client] ticker cache missing source receipt: {meta_path.name}",
            level="WARN",
        )
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(
            f"[pykrx_client] ticker source receipt unreadable: {type(exc).__name__}",
            level="WARN",
        )
        return None
    source_identity = meta.get("source_identity")
    if (
        meta.get("schema") != TICKER_SOURCE_META_SCHEMA
        or meta.get("ticker") != ticker
        or meta.get("cache_file") != path.name
        or source_identity not in _TICKER_HISTORY_SOURCE_IDENTITIES
    ):
        log(f"[pykrx_client] ticker source receipt mismatch: {ticker}", level="WARN")
        return None
    cached.attrs["source_identity"] = source_identity
    cached.attrs["source_receipt_path"] = str(meta_path)
    cached.attrs["ticker"] = ticker
    cached.attrs["raw_source_claimed"] = False
    return cached


def _save_ticker_cache(
    df: pd.DataFrame,
    path: Path,
    *,
    ticker: str,
    source_identity: str,
    adjustment_semantics: str,
) -> None:
    if source_identity not in _TICKER_HISTORY_SOURCE_IDENTITIES:
        raise RuntimeError("unapproved_ticker_history_source_identity")
    _save_cache(df, path)
    if not path.exists():
        return
    meta = {
        "schema": TICKER_SOURCE_META_SCHEMA,
        "ticker": ticker,
        "cache_file": path.name,
        "source_identity": source_identity,
        "adjustment_semantics": adjustment_semantics,
        "raw_source_claimed": False,
        "normalized_only": True,
        "a3_reviewed": False,
    }
    meta_path = ticker_source_meta_path(path)
    meta_path.write_text(
        json.dumps(meta, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    df.attrs["source_identity"] = source_identity
    df.attrs["source_receipt_path"] = str(meta_path)
    df.attrs["ticker"] = ticker
    df.attrs["raw_source_claimed"] = False


def index_cache_path(ticker: str, start: str, end: str) -> Path:
    """Return the cache path for an index history.

    KOSDAQ150 uses a source-segregated v1 cache so a former KQ150/Yahoo cache
    cannot be reused as direct KRX-code evidence.
    """
    if ticker == _KOSDAQ150_INDEX_TICKER:
        return CACHE_DIR / (
            f"index_{ticker}_krx-direct-v1_{_yyyymmdd(start)}_{_yyyymmdd(end)}.parquet"
        )
    return CACHE_DIR / f"index_{ticker}_{_yyyymmdd(start)}_{_yyyymmdd(end)}.parquet"


def index_source_meta_path(cache_path: Path) -> Path:
    return Path(str(cache_path) + ".source.json")


def _load_index_cached(
    path: Path,
    *,
    ticker: str,
    refresh_days: int,
) -> Optional[pd.DataFrame]:
    cached = _load_cached(path, refresh_days)
    if cached is None or cached.empty:
        return cached
    if ticker != _KOSDAQ150_INDEX_TICKER:
        return cached

    meta_path = index_source_meta_path(path)
    if not meta_path.exists():
        log(
            f"[pykrx_client] KOSDAQ150 cache missing source receipt: {meta_path.name}",
            level="WARN",
        )
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(
            f"[pykrx_client] KOSDAQ150 source receipt unreadable: {type(exc).__name__}",
            level="WARN",
        )
        return None
    source_identity = meta.get("source_identity")
    if (
        meta.get("schema") != INDEX_SOURCE_META_SCHEMA
        or meta.get("index_ticker") != ticker
        or meta.get("cache_file") != path.name
        or source_identity not in _KOSDAQ150_SOURCE_IDENTITIES
    ):
        log("[pykrx_client] KOSDAQ150 source receipt mismatch", level="WARN")
        return None

    cached.attrs["source_identity"] = source_identity
    cached.attrs["source_receipt_path"] = str(meta_path)
    cached.attrs["index_ticker"] = ticker
    return cached


def _save_index_cache(
    df: pd.DataFrame,
    path: Path,
    *,
    ticker: str,
    source_identity: str,
) -> None:
    _save_cache(df, path)
    if not path.exists():
        return
    df.attrs["source_identity"] = source_identity
    df.attrs["index_ticker"] = ticker
    if ticker != _KOSDAQ150_INDEX_TICKER:
        return
    if source_identity not in _KOSDAQ150_SOURCE_IDENTITIES:
        raise RuntimeError("unapproved_kosdaq150_source_identity")
    meta = {
        "schema": INDEX_SOURCE_META_SCHEMA,
        "index_ticker": ticker,
        "cache_file": path.name,
        "source_identity": source_identity,
        "raw_source_claimed": False,
        "normalized_only": True,
    }
    meta_path = index_source_meta_path(path)
    meta_path.write_text(
        json.dumps(meta, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    df.attrs["source_receipt_path"] = str(meta_path)


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


def fetch_market_cap_market(date: str, market: str = "ALL", refresh_days: int = 1) -> pd.DataFrame:
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

    if out.empty and FDR_AVAILABLE:
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
    "2203": "KRX-INDEX:2203",  # exact KRX index code; bypass KQ150/Yahoo
    "1003": "VKOSPI",    # KRX VKOSPI (FDR may not have)
}


_KRX_OPENAPI_KOSDAQ_URL = (
    "https://data-dbg.krx.co.kr/svc/apis/idx/kosdaq_dd_trd.json"
)


def _fetch_kosdaq150_openapi(start: str, end: str) -> pd.DataFrame:
    """Official KRX Open API fallback for KOSDAQ150 (index code 2203).

    The endpoint is daily and returns all KOSDAQ-series indices for one session.
    We walk the already-admitted KOSPI trading-session grid, select the exact
    normalized name '코스닥150', and never substitute another index.
    """
    api_key = os.environ.get("KRX_API_KEY", "").strip()
    if not api_key:
        log("[pykrx_client] KRX_API_KEY absent; official 2203 fallback unavailable", level="WARN")
        return pd.DataFrame()

    calendar = fetch_index_ohlcv("1001", start, end)
    if calendar is None or calendar.empty or "date" not in calendar.columns:
        log("[pykrx_client] KRX calendar unavailable for official 2203 fallback", level="WARN")
        return pd.DataFrame()

    session_days = sorted(pd.to_datetime(calendar["date"]).dt.strftime("%Y%m%d").unique())
    rows: list[dict] = []
    with requests.Session() as session:
        for day in session_days:
            try:
                response = session.get(
                    _KRX_OPENAPI_KOSDAQ_URL,
                    params={"basDd": day},
                    headers={"AUTH_KEY": api_key, "User-Agent": "kr-quant-engine"},
                    timeout=20,
                )
            except requests.RequestException:
                log("[pykrx_client] KRX Open API transport failure for 2203", level="WARN")
                return pd.DataFrame()
            if response.status_code != 200:
                log(
                    f"[pykrx_client] KRX Open API HTTP {response.status_code} for 2203",
                    level="WARN",
                )
                return pd.DataFrame()
            try:
                body = response.json()
            except ValueError:
                log("[pykrx_client] KRX Open API non-JSON response for 2203", level="WARN")
                return pd.DataFrame()
            if not isinstance(body, dict):
                return pd.DataFrame()
            if body.get("respCode"):
                log(
                    f"[pykrx_client] KRX Open API vendor code {body.get('respCode')} for 2203",
                    level="WARN",
                )
                return pd.DataFrame()
            blocks = [
                value for key, value in body.items()
                if str(key).startswith("OutBlock_") and isinstance(value, list)
            ]
            if len(blocks) != 1:
                log("[pykrx_client] KRX Open API unexpected block shape for 2203", level="WARN")
                return pd.DataFrame()
            matches = [
                row for row in blocks[0]
                if isinstance(row, dict)
                and str(row.get("IDX_NM", "")).replace(" ", "") == "코스닥150"
            ]
            if len(matches) != 1:
                log(
                    f"[pykrx_client] KRX Open API 2203 identity count={len(matches)} date={day}",
                    level="WARN",
                )
                return pd.DataFrame()
            row = matches[0]
            try:
                rows.append({
                    "date": pd.Timestamp(str(row.get("BAS_DD") or day)),
                    "open": float(str(row["OPNPRC_IDX"]).replace(",", "")),
                    "high": float(str(row["HGPRC_IDX"]).replace(",", "")),
                    "low": float(str(row["LWPRC_IDX"]).replace(",", "")),
                    "close": float(str(row["CLSPRC_IDX"]).replace(",", "")),
                    "volume": float(str(row.get("ACC_TRDVOL") or "0").replace(",", "")),
                    "value": float(str(row.get("ACC_TRDVAL") or "0").replace(",", "")),
                    "index_ticker": _KOSDAQ150_INDEX_TICKER,
                })
            except (KeyError, TypeError, ValueError):
                log("[pykrx_client] KRX Open API numeric parse failure for 2203", level="WARN")
                return pd.DataFrame()
    return pd.DataFrame(rows)


def fetch_index_ohlcv(ticker: str, start: str, end: str, refresh_days: int = 1) -> pd.DataFrame:
    """KRX index daily OHLCV (KOSPI 1001, KOSPI200 1028, KOSDAQ 2001 등).

    Returns date-indexed DataFrame: date, open, high, low, close, volume.
    Tries pykrx first; falls back to FDR mapping.
    """
    path = index_cache_path(ticker, start, end)
    cached = _load_index_cached(path, ticker=ticker, refresh_days=refresh_days)
    if cached is not None and not cached.empty:
        return cached

    df = pd.DataFrame()
    source_identity: Optional[str] = None
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
                source_identity = (
                    "PYKRX_KRX_INDEX_2203_NORMALIZED"
                    if ticker == _KOSDAQ150_INDEX_TICKER
                    else "PYKRX_KRX_INDEX_NORMALIZED"
                )
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
                source_identity = (
                    "FINANCE_DATAREADER_KRX_INDEX_MDCSTAT00301_2203_NORMALIZED"
                    if ticker == _KOSDAQ150_INDEX_TICKER
                    and fdr_code == _KOSDAQ150_FDR_DIRECT
                    else f"FINANCE_DATAREADER_NORMALIZED:{fdr_code}"
                )
        except Exception as e:
            log(f"[pykrx_client] FDR index fail {ticker} ({fdr_code}): {e}",
                level="WARN")

    if df.empty and ticker == _KOSDAQ150_INDEX_TICKER:
        df = _fetch_kosdaq150_openapi(start, end)
        if df is not None and not df.empty:
            source_identity = "KRX_OPENAPI_KOSDAQ_DAILY_2203_NORMALIZED"

    if df.empty:
        return pd.DataFrame()

    if source_identity is None:
        source_identity = "UNRESOLVED_INDEX_PROVIDER_NORMALIZED"
    _save_index_cache(
        df,
        path,
        ticker=ticker,
        source_identity=source_identity,
    )
    df.attrs["source_identity"] = source_identity
    log(
        f"[pykrx_client] fetch_index_ohlcv({ticker}, {start}, {end}) "
        f"-> {len(df)} rows source={source_identity}"
    )
    return df


def fetch_ticker_history(ticker: str, start: str, end: str, refresh_days: int = 1) -> pd.DataFrame:
    """Single-ticker daily OHLCV with explicit research-source provenance.

    The research request is split/corporate-action aware when pykrx can serve
    adjusted=True. If that path is unavailable, FinanceDataReader is called
    explicitly through NAVER:<ticker>; that fallback remains an unreviewed
    close proxy and is never relabeled as reviewed A3/ER evidence.

    Legacy unsuffixed ticker caches are intentionally not reused.
    """
    path = ticker_cache_path(ticker, start, end)
    cached = _load_ticker_cached(
        path,
        ticker=ticker,
        refresh_days=refresh_days,
    )
    if cached is not None and not cached.empty:
        return cached

    df = pd.DataFrame()
    source_identity: Optional[str] = None
    adjustment_semantics: Optional[str] = None

    if PYKRX_AVAILABLE:
        try:
            df = _pykrx_stock.get_market_ohlcv_by_date(
                _yyyymmdd(start),
                _yyyymmdd(end),
                ticker,
                adjusted=True,
            )
            if df is not None and not df.empty:
                df = df.reset_index().rename(columns={
                    "날짜": "date", "시가": "open", "고가": "high", "저가": "low",
                    "종가": "close", "거래량": "volume", "거래대금": "value",
                    "등락률": "change_pct",
                })
                df["ticker"] = ticker
                source_identity = "PYKRX_ADJUSTED_TRUE_NORMALIZED"
                adjustment_semantics = "PYKRX_REQUEST_ADJUSTED_TRUE"
            else:
                df = pd.DataFrame()
        except Exception as exc:
            log(
                f"[pykrx_client] pykrx adjusted ticker history fail {ticker}: "
                f"{type(exc).__name__}",
                level="WARN",
            )
            df = pd.DataFrame()

    if df.empty and FDR_AVAILABLE:
        try:
            s = pd.Timestamp(start).strftime("%Y-%m-%d")
            e = pd.Timestamp(end).strftime("%Y-%m-%d")
            fdr_df = _fdr.DataReader(f"NAVER:{ticker}", s, e)
            if fdr_df is not None and not fdr_df.empty:
                fdr_df = fdr_df.reset_index().rename(columns={
                    "Date": "date", "Open": "open", "High": "high",
                    "Low": "low", "Close": "close", "Volume": "volume",
                    "Change": "change_pct",
                })
                fdr_df["ticker"] = ticker
                df = fdr_df
                source_identity = "FINANCE_DATAREADER_NAVER_CLOSE_PROXY_NORMALIZED"
                adjustment_semantics = "NAVER_CLOSE_PROXY_UNREVIEWED"
        except Exception as exc:
            log(
                f"[pykrx_client] FDR NAVER ticker history fail {ticker}: {exc}",
                level="WARN",
            )

    if df.empty:
        return pd.DataFrame()

    if "value" not in df.columns and "close" in df.columns and "volume" in df.columns:
        df["value"] = (
            pd.to_numeric(df["close"], errors="coerce").fillna(0)
            * pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        )

    if source_identity is None or adjustment_semantics is None:
        raise RuntimeError("ticker_history_source_identity_unresolved")
    _save_ticker_cache(
        df,
        path,
        ticker=ticker,
        source_identity=source_identity,
        adjustment_semantics=adjustment_semantics,
    )
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
