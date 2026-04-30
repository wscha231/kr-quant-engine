"""kr_naver_flow — Naver Finance scrape for daily foreign/inst flow + ownership.

KRX 사이트가 2025 redesign으로 직접 endpoint broken (pykrx 1.2.7 + 우리 KRX
scraper 모두 400). Naver Finance의 종목별 외인/기관 일별 매매 page는 안정적
공개 page → HTML scrape으로 우회.

URL pattern:
  https://finance.naver.com/item/frgn.naver?code={ticker}&page={page}

Each page has 10 daily rows:
  날짜 | 종가 | 전일비 | 등락률 | 거래량 |
  기관 순매매(주) | 외국인 순매매(주) | 외국인 보유주수 | 외국인 보유율(%)

Public API:
  fetch_ticker_flow_naver(ticker, days=60) — last N days flow
  fetch_ticker_flow_range(ticker, start, end) — explicit date range

Note: Naver is per-ticker only. Market-level aggregation requires summing
across universe (or use Naver's investorDeal.naver page separately).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from kr_config import DATA_ROOT
from kr_helpers import log


CACHE_DIR = DATA_ROOT / "cache_misc"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

NAVER_BASE = "https://finance.naver.com/item/frgn.naver"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}


def _coerce_int(s: str) -> float:
    """'1,234,567' / '+1,234' / '-' / '55.50%' → numeric (NaN if invalid)."""
    if s is None:
        return np.nan
    t = str(s).strip().replace(",", "").replace(" ", "").replace("%", "")
    if t in ("", "-", "n/a", "N/A"):
        return np.nan
    # Strip leading +
    if t.startswith("+"):
        t = t[1:]
    try:
        return float(t)
    except Exception:
        return np.nan


def _parse_naver_table(html: str) -> pd.DataFrame:
    """Extract daily flow rows from Naver frgn.naver HTML."""
    soup = BeautifulSoup(html, "lxml") if "lxml" in [p for p in ("lxml",)] else \
           BeautifulSoup(html, "html.parser")
    # Find the flow table (class 'type2')
    tables = soup.find_all("table", class_="type2")
    if not tables:
        return pd.DataFrame()

    rows_out = []
    # First table is usually the data
    for table in tables:
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 9:
                continue
            cells = [td.get_text(strip=True) for td in tds]
            # Expected: [date, close, prev_diff, change_rate, volume,
            #            inst_net_qty, foreign_net_qty, foreign_held_qty,
            #            foreign_holding_pct]
            date_str = cells[0]
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_str):
                continue
            try:
                date = pd.Timestamp(date_str.replace(".", "-"))
            except Exception:
                continue
            row = {
                "date": date,
                "close": _coerce_int(cells[1]),
                "volume": _coerce_int(cells[4]) if len(cells) > 4 else np.nan,
                "inst_net_qty": _coerce_int(cells[5]) if len(cells) > 5 else np.nan,
                "foreign_net_qty": _coerce_int(cells[6]) if len(cells) > 6 else np.nan,
                "foreign_held_qty": _coerce_int(cells[7]) if len(cells) > 7 else np.nan,
                "foreign_holding_pct": _coerce_int(cells[8]) if len(cells) > 8 else np.nan,
            }
            rows_out.append(row)
    if not rows_out:
        return pd.DataFrame()
    return pd.DataFrame(rows_out).drop_duplicates(subset=["date"]).sort_values("date")


def fetch_ticker_flow_naver(
    ticker: str, pages: int = 10, refresh_days: int = 7,
    polite_sleep_s: float = 0.3,
) -> pd.DataFrame:
    """Fetch last `pages * 10` daily rows of foreign/inst flow + ownership.

    Returns DataFrame: date, close, volume, inst_net_qty, foreign_net_qty,
    foreign_held_qty, foreign_holding_pct, ticker.

    Args:
        ticker: 6-digit code
        pages: max 10 rows per page (~ 2 weeks per page)
        refresh_days: cache TTL
        polite_sleep_s: throttle between page fetches
    """
    cache_path = CACHE_DIR / f"naver_flow_{ticker}_p{pages}.parquet"
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < refresh_days * 86400:
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass

    frames = []
    for page in range(1, pages + 1):
        url = f"{NAVER_BASE}?code={ticker}&page={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            df = _parse_naver_table(r.text)
            if df.empty:
                # End of available history
                break
            df["ticker"] = ticker
            frames.append(df)
        except Exception as e:
            log(f"[naver] page {page} fetch fail {ticker}: {e}", level="WARN")
            break
        time.sleep(polite_sleep_s)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["date"])
    out = out.sort_values("date").reset_index(drop=True)

    # Convert quantities to KRW values (close × qty for net buy)
    if "close" in out.columns and "foreign_net_qty" in out.columns:
        out["foreign_net_value"] = out["close"] * out["foreign_net_qty"]
    if "close" in out.columns and "inst_net_qty" in out.columns:
        out["inst_net_value"] = out["close"] * out["inst_net_qty"]

    try:
        out.to_parquet(cache_path, index=False)
    except Exception as e:
        log(f"[naver] cache write fail {ticker}: {e}", level="WARN")
    return out


def fetch_ticker_flow_range(
    ticker: str, start: str, end: str, refresh_days: int = 7,
) -> pd.DataFrame:
    """Naver returns latest first. Fetch enough pages to cover [start, end].

    ~10 rows per page → ceil((today - start_date) / 10) pages.
    """
    today = pd.Timestamp.now().normalize()
    target_start = pd.Timestamp(start)
    days_back = max(1, (today - target_start).days)
    pages = min(50, (days_back // 10) + 2)  # cap at 50 pages = ~500 days
    df = fetch_ticker_flow_naver(ticker, pages=pages, refresh_days=refresh_days)
    if df.empty:
        return df
    return df[(df["date"] >= start) & (df["date"] <= end)].copy()


# ---------------------------------------------------------------------------
# Bulk panel build
# ---------------------------------------------------------------------------
def build_naver_flow_panel(
    tickers: list[str], start: str, end: str,
    refresh_days: int = 7, polite_sleep_s: float = 0.5,
) -> pd.DataFrame:
    """Bulk fetch Naver flow for universe."""
    log(f"[naver] flow panel for {len(tickers)} tickers, {start}~{end}")
    frames = []
    for i, tk in enumerate(tickers, 1):
        if i % 50 == 0:
            log(f"[naver] flow {i}/{len(tickers)}")
        df = fetch_ticker_flow_range(tk, start, end, refresh_days=refresh_days)
        if not df.empty:
            frames.append(df)
        time.sleep(polite_sleep_s)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Sanity test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Naver flow scraper sanity test")
    print("=" * 60)

    print("\n[1/2] Samsung 005930 last 100 days...")
    df = fetch_ticker_flow_naver("005930", pages=10, refresh_days=0)
    print(f"   Rows: {len(df)}")
    if not df.empty:
        print(f"   Columns: {list(df.columns)}")
        print(df.head(5).to_string(index=False))
        print()
        print(f"   Recent foreign net qty (5 days):")
        print(df.tail(5)[["date", "close", "foreign_net_qty",
                          "inst_net_qty", "foreign_holding_pct"]].to_string(index=False))

    print("\n[2/2] SK Hynix 000660 last 100 days...")
    df2 = fetch_ticker_flow_naver("000660", pages=10, refresh_days=0)
    print(f"   Rows: {len(df2)}")
    if not df2.empty:
        net_60d = df2.tail(60)["foreign_net_qty"].sum()
        print(f"   Foreign 60d cumulative net qty: {net_60d:+.0f} 주")
        latest_pct = df2.iloc[-1].get("foreign_holding_pct", "N/A")
        print(f"   Latest foreign holding %:       {latest_pct}%")
