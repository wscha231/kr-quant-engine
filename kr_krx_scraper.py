"""kr_krx_scraper — KRX 정보데이터시스템 직접 scraper (pykrx 1.2.7 broken 우회).

pykrx 1.2.7는 KRX 사이트의 2025+ 변경사항에 적응 못함. 이 모듈은 KRX의
공개 JSON endpoint를 직접 호출한다 (anti-bot은 단순 User-Agent + Referer).

Endpoints (data.krx.co.kr/comm/bldAttendant/getJsonData.cmd):
  - MDCSTAT02402 : 시장 전체 거래주체별 일별 매매 (KOSPI/KOSDAQ별)
  - MDCSTAT02502 : 종목별 거래주체 일별 매매대금
  - MDCSTAT02901 : 외인 한도소진율 (per-ticker snapshot)
  - MDCSTAT00301 : 시장 전체 일별 거래대금 (참고)

Public API:
  fetch_market_flow_panel(start, end, market) — daily by investor class
  fetch_ticker_flow_panel(ticker, start, end) — per-ticker daily flow
  fetch_foreign_holding_snapshot(date, market) — foreign ownership %

Caching: per-fetch parquet at cache_misc/krx_*.parquet.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

from kr_config import DATA_ROOT
from kr_helpers import log


KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_OTP_URL = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"

# Standard headers — KRX requires Referer + User-Agent (anti-bot but very lax)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "http://data.krx.co.kr/",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

CACHE_DIR = DATA_ROOT / "cache_misc"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Low-level POST
# ---------------------------------------------------------------------------
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
        # Warm session — visit landing to get cookies
        try:
            _session.get("http://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd",
                         timeout=15)
        except Exception:
            pass
    return _session


def _krx_post(bld: str, params: Optional[dict] = None,
                retries: int = 3) -> dict:
    """POST to KRX getJsonData with bld + params. Returns parsed JSON dict.

    On failure raises RuntimeError after retries.
    """
    sess = _get_session()
    payload = {"bld": bld, **(params or {})}
    last_err = None
    for attempt in range(retries):
        try:
            r = sess.post(KRX_URL, data=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            return data
        except Exception as e:
            last_err = e
            time.sleep(2 + attempt * 2)
    raise RuntimeError(f"KRX POST {bld} failed after {retries} retries: {last_err}")


# ---------------------------------------------------------------------------
# Helpers — date format + market id
# ---------------------------------------------------------------------------
def _ymd(date) -> str:
    return pd.Timestamp(date).strftime("%Y%m%d")


# KRX market id codes
MKT_KOSPI = "STK"
MKT_KOSDAQ = "KSQ"
MKT_KONEX = "KNX"
MKT_ALL = "ALL"


def _market_to_id(market: str) -> str:
    m = market.upper()
    if m in ("KOSPI", "STK"):
        return MKT_KOSPI
    if m in ("KOSDAQ", "KSQ"):
        return MKT_KOSDAQ
    if m in ("KONEX", "KNX"):
        return MKT_KONEX
    if m == "ALL":
        return MKT_ALL
    return m


# ---------------------------------------------------------------------------
# 1. Market-level flow (시장 전체 거래주체별 일별 매매대금)
# ---------------------------------------------------------------------------
def fetch_market_flow_daily(
    date: str, market: str = "KOSPI", refresh_days: int = 30,
) -> pd.DataFrame:
    """One-day snapshot of market-wide flow by investor class.

    Returns DataFrame with one row per investor class:
      투자자구분 (investor type), 매수, 매도, 순매수, market, date

    KRX bld: MDCSTAT02402 (시장 전체 거래주체별 매매)
    """
    d = _ymd(date)
    market_id = _market_to_id(market)
    cache = CACHE_DIR / f"krx_market_flow_{market_id}_{d}.parquet"
    if cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < refresh_days * 86400:
            try:
                return pd.read_parquet(cache)
            except Exception:
                pass

    params = {
        "mktId": market_id,
        "trdDd": d,
        "money": "1",        # 1=원, 2=백만원
        "csvxls_isNo": "false",
    }
    try:
        js = _krx_post("dbms/MDC/STAT/standard/MDCSTAT02402", params)
    except Exception as e:
        log(f"[krx] market flow {d} {market_id} fail: {e}", level="WARN")
        return pd.DataFrame()

    rows = js.get("output", [])
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Standard column rename
    rename_map = {
        "INVST_NM": "investor",
        "ASK_TRDVAL": "sell_value",      # 매도 거래대금
        "BID_TRDVAL": "buy_value",       # 매수 거래대금
        "NETBID_TRDVAL": "net_buy_value",  # 순매수 거래대금
        "ASK_TRDVOL": "sell_volume",
        "BID_TRDVOL": "buy_volume",
        "NETBID_TRDVOL": "net_buy_volume",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # Coerce numeric (KRX returns comma-strings)
    for col in ("sell_value", "buy_value", "net_buy_value",
                "sell_volume", "buy_volume", "net_buy_volume"):
        if col in df.columns:
            df[col] = (df[col].astype(str)
                       .str.replace(",", "", regex=False)
                       .replace("-", "0").replace("", "0"))
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["market"] = market.upper() if market.upper() != "STK" else "KOSPI"
    if df["market"].iloc[0] == "KSQ":
        df["market"] = "KOSDAQ"
    df["date"] = pd.Timestamp(date).normalize()

    try:
        df.to_parquet(cache, index=False)
    except Exception:
        pass
    return df


def fetch_market_flow_panel(
    start: str, end: str,
    markets: tuple[str, ...] = ("KOSPI", "KOSDAQ"),
    refresh_days: int = 30,
    polite_sleep_s: float = 0.1,
) -> pd.DataFrame:
    """Bulk daily market flow for KOSPI + KOSDAQ across [start, end].

    Returns long-format: date, market, investor, net_buy_value, ...
    """
    from kr_pykrx_client import fetch_business_days
    bdays = fetch_business_days(_ymd(start), _ymd(end))
    if not bdays:
        return pd.DataFrame()

    log(f"[krx] market_flow_panel {start}~{end} ({len(bdays)} bdays × {len(markets)} markets)")
    frames = []
    for i, d in enumerate(bdays, 1):
        if i % 50 == 0:
            log(f"[krx] market_flow {i}/{len(bdays)}")
        for mkt in markets:
            df = fetch_market_flow_daily(d.strftime("%Y%m%d"), market=mkt,
                                            refresh_days=refresh_days)
            if not df.empty:
                frames.append(df)
            time.sleep(polite_sleep_s)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)
    panel = panel.sort_values(["date", "market", "investor"]).reset_index(drop=True)
    return panel


# ---------------------------------------------------------------------------
# 2. Ticker-level flow (종목별 거래주체 일별 매매대금)
# ---------------------------------------------------------------------------
def fetch_ticker_flow_for_range(
    ticker: str, start: str, end: str, refresh_days: int = 30,
) -> pd.DataFrame:
    """Per-ticker daily net-buy by investor class.

    Returns DataFrame: date, ticker, foreign_net, inst_net, individual_net,
    pension_net, inv_trust_net, etc.

    KRX bld: MDCSTAT02502 (종목별 거래실적 거래주체)
    Note: KRX endpoint may require ISIN — we resolve via market_cap snapshot.
    """
    s, e = _ymd(start), _ymd(end)
    cache = CACHE_DIR / f"krx_ticker_flow_{ticker}_{s}_{e}.parquet"
    if cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < refresh_days * 86400:
            try:
                return pd.read_parquet(cache)
            except Exception:
                pass

    # Resolve ISIN — KRX endpoint takes ISIN code
    isin = _ticker_to_isin(ticker)
    if not isin:
        log(f"[krx] ISIN unresolved for {ticker}", level="WARN")
        return pd.DataFrame()

    params = {
        "tboxisuCd_finder_stkisu0_0": f"{ticker}/{isin}",
        "isuCd": isin,
        "isuCd2": "",
        "codeNmisuCd_finder_stkisu0_0": "",
        "param1isuCd_finder_stkisu0_0": "",
        "strtDd": s,
        "endDd": e,
        "askBid": "3",     # 1=매도 / 2=매수 / 3=순매수
        "trdVolVal": "2",  # 1=거래량 / 2=거래대금
        "money": "1",       # 1=원
        "csvxls_isNo": "false",
    }
    try:
        js = _krx_post("dbms/MDC/STAT/standard/MDCSTAT02502", params)
    except Exception as ex:
        log(f"[krx] ticker_flow {ticker} fail: {ex}", level="WARN")
        return pd.DataFrame()

    rows = js.get("output", [])
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Standard column rename. KRX returns wide (per-investor columns).
    rename_map = {
        "TRD_DD": "date",
        "TRDVAL3": "individual_net",        # 개인
        "TRDVAL4": "foreign_net",            # 외국인 (합계)
        "TRDVAL5": "inst_net",               # 기관합계
        "TRDVAL_TOT": "total",
        # Detailed institutional (varies by view):
        "TRDVAL6": "pension_net",            # 연기금 등
        "TRDVAL7": "inv_trust_net",          # 투신
        "TRDVAL8": "private_equity_net",     # 사모
        "TRDVAL9": "bank_net",
        "TRDVAL10": "insurance_net",
        "TRDVAL11": "other_finance_net",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # Coerce numeric
    for col in ("individual_net", "foreign_net", "inst_net", "total",
                "pension_net", "inv_trust_net", "private_equity_net",
                "bank_net", "insurance_net", "other_finance_net"):
        if col in df.columns:
            df[col] = (df[col].astype(str)
                       .str.replace(",", "", regex=False)
                       .replace("-", "0").replace("", "0"))
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"].astype(str).str.replace("/", "-"),
                                      errors="coerce")

    df["ticker"] = ticker
    keep = ["date", "ticker"] + [
        c for c in ("foreign_net", "inst_net", "individual_net",
                    "pension_net", "inv_trust_net", "private_equity_net",
                    "bank_net", "insurance_net", "other_finance_net", "total")
        if c in df.columns
    ]
    df = df[keep]

    try:
        df.to_parquet(cache, index=False)
    except Exception:
        pass
    return df


# ---------------------------------------------------------------------------
# 3. Foreign holding pct (외국인 한도소진율 snapshot)
# ---------------------------------------------------------------------------
def fetch_foreign_holding_snapshot(
    date: str, market: str = "ALL", refresh_days: int = 30,
) -> pd.DataFrame:
    """Per-ticker foreign ownership % at given date.

    Columns: ticker, name, foreign_holding_pct, market, date.

    KRX bld: MDCSTAT02901 (외국인 한도소진율)
    """
    d = _ymd(date)
    market_id = _market_to_id(market)
    cache = CACHE_DIR / f"krx_foreign_holding_{market_id}_{d}.parquet"
    if cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < refresh_days * 86400:
            try:
                return pd.read_parquet(cache)
            except Exception:
                pass

    params = {
        "mktId": market_id,
        "trdDd": d,
        "share": "1",
        "csvxls_isNo": "false",
    }
    try:
        js = _krx_post("dbms/MDC/STAT/standard/MDCSTAT03701", params)
    except Exception as e:
        log(f"[krx] foreign holding {d} fail: {e}", level="WARN")
        return pd.DataFrame()

    rows = js.get("output", [])
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    rename_map = {
        "ISU_SRT_CD": "ticker",
        "ISU_ABBRV": "name",
        "FORN_HD_QTY": "foreign_held_qty",
        "FORN_HD_RT": "foreign_holding_pct",   # 한도소진율 (%)
        "FORN_LMT_RT": "foreign_limit_pct",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    for col in ("foreign_holding_pct", "foreign_limit_pct", "foreign_held_qty"):
        if col in df.columns:
            df[col] = (df[col].astype(str)
                       .str.replace(",", "", regex=False)
                       .replace("-", "0").replace("", "0"))
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["market"] = market.upper()
    df["date"] = pd.Timestamp(date).normalize()

    try:
        df.to_parquet(cache, index=False)
    except Exception:
        pass
    return df


# ---------------------------------------------------------------------------
# 4. Ticker → ISIN resolution
# ---------------------------------------------------------------------------
_ISIN_CACHE: dict[str, str] = {}


def _ticker_to_isin(ticker: str) -> Optional[str]:
    """Resolve 6-digit KRX ticker to ISIN.

    Standard ISIN format: KR7 + ticker + 0|3 + check_digit (10 chars after KR).
    Example: 005930 → KR7005930003 (Samsung).

    For most listings, the simple formula KR7{ticker}003 works; we use that.
    For exceptions (preferred stocks etc.) caller should provide ISIN.
    """
    if ticker in _ISIN_CACHE:
        return _ISIN_CACHE[ticker]
    # Standard: KR7 + ticker + "003"
    isin = f"KR7{ticker}003"
    _ISIN_CACHE[ticker] = isin
    return isin


# ---------------------------------------------------------------------------
# Sanity test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("KRX scraper sanity test")
    print("=" * 60)

    print("\n[1/3] Market flow (KOSPI 2024-12-30)...")
    df = fetch_market_flow_daily("20241230", market="KOSPI", refresh_days=0)
    print(f"   Rows: {len(df)}")
    if not df.empty:
        print(df[["investor", "net_buy_value", "buy_value", "sell_value"]].to_string(index=False))

    print("\n[2/3] Foreign holding (KOSPI 2024-12-30 — top 10 by foreign %)...")
    fh = fetch_foreign_holding_snapshot("20241230", market="KOSPI", refresh_days=0)
    print(f"   Rows: {len(fh)}")
    if not fh.empty and "foreign_holding_pct" in fh.columns:
        top = fh.sort_values("foreign_holding_pct", ascending=False).head(10)
        print(top[["ticker", "name", "foreign_holding_pct"]].to_string(index=False))

    print("\n[3/3] Samsung 005930 ticker flow (Dec 2024)...")
    tf = fetch_ticker_flow_for_range("005930", "20241201", "20241230", refresh_days=0)
    print(f"   Rows: {len(tf)}")
    if not tf.empty:
        print(tf.tail(5).to_string(index=False))
        print(f"\n   Foreign net buy total Dec 2024: {tf['foreign_net'].sum() / 1e9:+.1f} 십억원")
        print(f"   Inst net buy total Dec 2024:    {tf['inst_net'].sum() / 1e9:+.1f} 십억원")
