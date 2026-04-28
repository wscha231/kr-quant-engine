"""kr_bok_client — Bank of Korea ECOS API wrapper + parquet caching.

ECOS API docs: https://ecos.bok.or.kr/api/

Endpoint: /api/StatisticSearch/{API_KEY}/json/kr/{start}/{end}/{stat_code}/{cycle}/{start_date}/{end_date}/{item_code1}

Provides:
- fetch_bok_series(stat_code, item_code, cycle, start, end) — single series
- fetch_macro_panel(start, end, series_keys) — multi-series wide panel
- list_macro_keys() — list configured keys from kr_config.MACRO_BOK_SERIES

Caching: per-series parquet at cache_macro/bok_{key}.parquet, refresh by
cfg["macro_refresh_days"] (default 7).
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from kr_config import DATA_ROOT, MACRO_BOK_SERIES
from kr_helpers import load_dotenv_if_present, log


CACHE_DIR = DATA_ROOT / "cache_macro"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

ECOS_BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"
DEFAULT_RESPONSE_LIMIT = 10000   # ECOS max rows per call


def _get_api_key() -> str:
    """Load BOK_ECOS_API_KEY from .env or env. Raise if missing."""
    load_dotenv_if_present()
    key = os.environ.get("BOK_ECOS_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "BOK_ECOS_API_KEY not set. "
            "Get free key at https://ecos.bok.or.kr/api and add to .env"
        )
    return key


def _format_date_for_cycle(date: str, cycle: str) -> str:
    """ECOS date format depends on cycle:
       - 'A' (annual)    : YYYY (e.g. '2024')
       - 'Q' (quarterly) : YYYYQ#  (e.g. '2024Q1')  → ECOS uses 'YYYYNQ' wire format
       - 'M' (monthly)   : YYYYMM (e.g. '202401')
       - 'D' (daily)     : YYYYMMDD (e.g. '20240101')
    """
    ts = pd.Timestamp(date)
    cycle = cycle.upper()
    if cycle == "A":
        return ts.strftime("%Y")
    if cycle == "Q":
        return f"{ts.year}Q{(ts.month - 1) // 3 + 1}"
    if cycle == "M":
        return ts.strftime("%Y%m")
    if cycle == "D":
        return ts.strftime("%Y%m%d")
    raise ValueError(f"Unknown cycle: {cycle}")


def fetch_bok_series(
    stat_code: str,
    item_code: str,
    cycle: str,
    start: str,
    end: str,
    cache_key: Optional[str] = None,
    refresh_days: int = 7,
) -> pd.DataFrame:
    """Fetch a single BOK ECOS time series.

    Returns DataFrame: date, value, stat_code, item_code, cycle.

    Args:
        stat_code: ECOS 통계표 코드 (e.g. "722Y001")
        item_code: 항목 코드 (e.g. "0101000")
        cycle: "A" | "Q" | "M" | "D"
        start, end: date-like strings
        cache_key: optional friendly name for cache file (else stat+item)
        refresh_days: cache TTL
    """
    if cache_key is None:
        cache_key = f"{stat_code}_{item_code}"
    cache_path = CACHE_DIR / f"bok_{cache_key}.parquet"

    # Cache check
    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                df = pd.read_parquet(cache_path)
                # Ensure cached range covers requested range
                if not df.empty:
                    cached_min = pd.Timestamp(df["date"].min())
                    cached_max = pd.Timestamp(df["date"].max())
                    if cached_min <= pd.Timestamp(start) and cached_max >= pd.Timestamp(end):
                        return df[(df["date"] >= start) & (df["date"] <= end)].copy()
            except Exception as e:
                log(f"[bok_client] cache read fail {cache_path.name}: {e}", level="WARN")

    api_key = _get_api_key()
    s = _format_date_for_cycle(start, cycle)
    e = _format_date_for_cycle(end, cycle)

    # ECOS URL pattern: {base}/{key}/json/kr/{startRow}/{endRow}/{stat}/{cycle}/{start}/{end}/{item1}
    url = f"{ECOS_BASE_URL}/{api_key}/json/kr/1/{DEFAULT_RESPONSE_LIMIT}/{stat_code}/{cycle}/{s}/{e}/{item_code}"

    log(f"[bok_client] GET {stat_code}/{item_code} cycle={cycle} {s}~{e}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # ECOS error structure: {"RESULT": {"CODE": "INFO-...", "MESSAGE": "..."}}
    if "RESULT" in data:
        result = data["RESULT"]
        code = result.get("CODE", "")
        msg = result.get("MESSAGE", "")
        if "INFO-200" in code:    # 데이터 없음
            log(f"[bok_client] no data for {stat_code}/{item_code}", level="WARN")
            return pd.DataFrame(columns=["date", "value", "stat_code", "item_code", "cycle"])
        raise RuntimeError(f"ECOS API error {code}: {msg}")

    # Success: data["StatisticSearch"]["row"] is list of dicts
    payload = data.get("StatisticSearch", {})
    rows = payload.get("row", [])
    if not rows:
        return pd.DataFrame(columns=["date", "value", "stat_code", "item_code", "cycle"])

    df = pd.DataFrame(rows)
    # Standard fields: TIME (period string), DATA_VALUE (string number)
    df = df.rename(columns={"TIME": "time_raw", "DATA_VALUE": "value_raw"})
    df["value"] = pd.to_numeric(df["value_raw"], errors="coerce")

    # Convert TIME (e.g. "202401" or "20240115") to date
    def _parse_time(t: str, cyc: str) -> pd.Timestamp:
        cyc = cyc.upper()
        try:
            if cyc == "A":
                return pd.Timestamp(year=int(t), month=12, day=31)
            if cyc == "Q":
                yr = int(t[:4])
                q = int(t[-1])
                month = q * 3
                return pd.Timestamp(year=yr, month=month, day=1) + pd.offsets.MonthEnd(0)
            if cyc == "M":
                yr = int(t[:4])
                mo = int(t[4:6])
                return pd.Timestamp(year=yr, month=mo, day=1) + pd.offsets.MonthEnd(0)
            if cyc == "D":
                return pd.Timestamp(t)
        except Exception:
            return pd.NaT
        return pd.NaT

    df["date"] = df["time_raw"].apply(lambda t: _parse_time(str(t), cycle))
    df["stat_code"] = stat_code
    df["item_code"] = item_code
    df["cycle"] = cycle

    out = df[["date", "value", "stat_code", "item_code", "cycle"]].copy()
    out = out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    try:
        out.to_parquet(cache_path, index=False)
    except Exception as ex:
        log(f"[bok_client] cache write fail {cache_path.name}: {ex}", level="WARN")

    return out


def fetch_macro_panel(
    start: str,
    end: str,
    series_keys: Optional[list[str]] = None,
    refresh_days: int = 7,
) -> pd.DataFrame:
    """Fetch multiple BOK series and return wide-format panel indexed by date.

    Columns: date, <key1>, <key2>, ...

    Different cycles are aligned to monthly (forward-fill last value).
    """
    keys = list(series_keys) if series_keys else list(MACRO_BOK_SERIES.keys())
    frames = []
    for key in keys:
        if key not in MACRO_BOK_SERIES:
            log(f"[bok_client] unknown series key: {key}", level="WARN")
            continue
        stat_code, item_code, cycle, name = MACRO_BOK_SERIES[key]
        try:
            df = fetch_bok_series(
                stat_code, item_code, cycle, start, end,
                cache_key=key, refresh_days=refresh_days,
            )
        except Exception as e:
            log(f"[bok_client] fetch fail {key}: {e}", level="WARN")
            continue
        if df.empty:
            continue
        s = df.set_index("date")["value"].rename(key)
        frames.append(s)
        # Polite throttle
        time.sleep(0.2)

    if not frames:
        return pd.DataFrame()

    panel = pd.concat(frames, axis=1).sort_index()
    # Forward-fill to daily, then reset index
    panel = panel.resample("D").ffill().reset_index()
    return panel


def list_macro_keys() -> list[tuple[str, str]]:
    """Return [(key, friendly_name), ...] from kr_config.MACRO_BOK_SERIES."""
    return [(k, v[3]) for k, v in MACRO_BOK_SERIES.items()]


# ---------------------------------------------------------------------------
# Sanity test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Available BOK series:")
    for k, name in list_macro_keys():
        print(f"  {k:25s} — {name}")
    print()
    print("Test fetch: BOK base rate (722Y001/0101000), 2024 monthly")
    try:
        df = fetch_bok_series("722Y001", "0101000", "M", "20240101", "20241231",
                              cache_key="bok_base_rate_test", refresh_days=0)
        print(f"-> {len(df)} rows")
        print(df.tail(5))
    except Exception as e:
        print(f"FAIL: {e}")
