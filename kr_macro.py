"""kr_macro — Korean + global macro panel builder (P3.2).

Combines BOK ECOS (Korean macro), FRED (US treasury, DXY, VIX), and yfinance
(WTI, KRX indices, KRW spot) into a unified daily-frequency wide panel.

Key signals (matched to kr_config.PHASE3_MACRO_COLUMNS):
  - 금리: bok_base_rate, ktb 3Y/10Y, US 10Y/2Y, spreads
  - 환율: USD/KRW + zscore + 20d change, DXY + zscore
  - KR 경기: PMI, consumer/business sentiment, industrial production, export YoY
  - 글로벌: VIX, WTI

Public API:
  build_macro_panel(start, end, refresh_days=7) -> DataFrame[date, <signals>]
  load_or_build_macro_panel(cfg) -> DataFrame                    (cached)
  get_macro_snapshot(panel, as_of) -> dict[col, value]           (PIT lookup)

Caching: feature_store/macro_panel_<start>_<end>_<version>.parquet
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from kr_config import (
    DATA_ROOT,
    DEFAULT_CFG,
    KR_ENGINE_REUSE_VERSION,
    MACRO_BOK_SERIES,
    MACRO_FRED_SERIES,
    MACRO_YF_TICKERS,
    PHASE3_MACRO_COLUMNS,
)
from kr_helpers import log


# ---------------------------------------------------------------------------
# 1. BOK panel
# ---------------------------------------------------------------------------
def fetch_bok_macro_wide(start: str, end: str, refresh_days: int = 7) -> pd.DataFrame:
    """Use kr_bok_client to build wide BOK macro panel.

    Returns date-indexed frame with columns matching MACRO_BOK_SERIES keys.
    """
    from kr_bok_client import fetch_macro_panel

    series_keys = list(MACRO_BOK_SERIES.keys())
    panel = fetch_macro_panel(start, end, series_keys=series_keys,
                               refresh_days=refresh_days)
    if panel.empty:
        return pd.DataFrame()
    return panel


# ---------------------------------------------------------------------------
# 2. FRED panel
# ---------------------------------------------------------------------------
def fetch_fred_macro_wide(start: str, end: str, refresh_days: int = 7) -> pd.DataFrame:
    """FRED-via-fredapi if available; else yfinance fallback for VIX/treasury.

    Returns wide panel: date, us_10y, us_2y, vix, dxy.

    Cached at cache_macro/fred_<start>_<end>.parquet.
    """
    cache_dir = DATA_ROOT / "cache_macro"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"fred_{start.replace('-','')}_{end.replace('-','')}.parquet"

    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                return pd.read_parquet(cache_path)
            except Exception as e:
                log(f"[macro] fred cache read fail: {e}", level="WARN")

    # Use yfinance for everything FRED would provide (no API key needed)
    try:
        import yfinance as yf
    except ImportError:
        log("[macro] yfinance unavailable", level="WARN")
        return pd.DataFrame()

    yf_map = {
        "us_10y": "^TNX",       # 10Y treasury yield (in %)
        "us_2y": "^FVX",        # 5Y as proxy if 2Y unavailable; or use ^IRX (3M)
        "vix": "^VIX",
        "dxy": "DX-Y.NYB",      # Dollar Index
    }
    frames = []
    for col, tk in yf_map.items():
        try:
            df = yf.download(tk, start=start, end=end, progress=False, auto_adjust=False)
            if df is None or df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            close = df["Close"].copy() if "Close" in df.columns else df.iloc[:, 0]
            s = close.rename(col)
            s.index = pd.to_datetime(s.index).normalize()
            s.index.name = "date"
            frames.append(s)
        except Exception as e:
            log(f"[macro] yf fetch fail {col} ({tk}): {e}", level="WARN")

    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, axis=1).reset_index()
    try:
        panel.to_parquet(cache_path, index=False)
    except Exception as e:
        log(f"[macro] fred cache write fail: {e}", level="WARN")
    return panel


# ---------------------------------------------------------------------------
# 3. yfinance commodities + KRX index
# ---------------------------------------------------------------------------
def fetch_yfinance_macro_wide(start: str, end: str, refresh_days: int = 7) -> pd.DataFrame:
    """WTI + KRX indices + KRW spot from yfinance.

    Returns wide panel: date, wti_close, kospi_yf, kosdaq_yf, usdkrw_yf, vkospi.
    """
    cache_dir = DATA_ROOT / "cache_macro"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"yfmacro_{start.replace('-','')}_{end.replace('-','')}.parquet"

    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass

    try:
        import yfinance as yf
    except ImportError:
        log("[macro] yfinance unavailable", level="WARN")
        return pd.DataFrame()

    yf_map = {
        "wti_close": "CL=F",            # WTI futures front month
        **MACRO_YF_TICKERS,             # vkospi, usdkrw_yf, kospi_yf, kosdaq_yf
    }
    frames = []
    for col, tk in yf_map.items():
        try:
            df = yf.download(tk, start=start, end=end, progress=False, auto_adjust=False)
            if df is None or df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            close = df["Close"].copy() if "Close" in df.columns else df.iloc[:, 0]
            s = close.rename(col)
            s.index = pd.to_datetime(s.index).normalize()
            s.index.name = "date"
            frames.append(s)
        except Exception as e:
            log(f"[macro] yf fetch fail {col} ({tk}): {e}", level="WARN")

    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, axis=1).reset_index()
    try:
        panel.to_parquet(cache_path, index=False)
    except Exception as e:
        log(f"[macro] yfmacro cache write fail: {e}", level="WARN")
    return panel


# ---------------------------------------------------------------------------
# 4. Derived signals
# ---------------------------------------------------------------------------
def _zscore_rolling(s: pd.Series, window: int) -> pd.Series:
    """Rolling z-score (mean / std)."""
    mu = s.rolling(window=window, min_periods=max(5, window // 4)).mean()
    sd = s.rolling(window=window, min_periods=max(5, window // 4)).std()
    return (s - mu) / sd.replace(0, np.nan)


def _change_pct(s: pd.Series, periods: int) -> pd.Series:
    """Percentage change over N periods."""
    return s.pct_change(periods=periods)


def _yoy_3m_avg(monthly_yoy: pd.Series) -> pd.Series:
    """3-month rolling average of YoY series."""
    return monthly_yoy.rolling(window=3, min_periods=2).mean()


def add_derived_macro_signals(panel: pd.DataFrame) -> pd.DataFrame:
    """Compute derived signals (zscore, spread, change) on raw macro panel.

    Output column names match PHASE3_MACRO_COLUMNS naming convention.
    """
    if panel.empty:
        return panel
    df = panel.copy()
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)

    # Map raw → standardized names
    rename_map = {
        "bok_base_rate":     "macro_bok_base_rate",
        "ktb_3y_yield":      "macro_ktb_3y_yield",
        "ktb_10y_yield":     "macro_ktb_10y_yield",
        "usd_krw":           "macro_usd_krw",
        "kr_pmi":            "macro_kr_pmi",
        "consumer_sentiment":"macro_consumer_sentiment",
        "business_sentiment":"macro_business_sentiment",
        "kr_industrial_prod":"macro_kr_industrial_prod",
        "kr_export_yoy":     "macro_export_yoy",
        "us_10y":            "macro_us_10y",
        "us_2y":             "macro_us_2y",
        "vix":               "macro_vix",
        "dxy":               "macro_dxy",
        "wti_close":         "macro_wti_close",
    }
    df = df.rename(columns=rename_map)

    # Derived: rate change 60d
    if "macro_bok_base_rate" in df.columns:
        df["macro_bok_rate_change_60d"] = _change_pct(df["macro_bok_base_rate"], 60)
    else:
        df["macro_bok_rate_change_60d"] = np.nan

    # Spreads
    if "macro_ktb_10y_yield" in df.columns and "macro_ktb_3y_yield" in df.columns:
        df["macro_ktb_10y_3y_spread"] = (
            df["macro_ktb_10y_yield"] - df["macro_ktb_3y_yield"]
        )
    else:
        df["macro_ktb_10y_3y_spread"] = np.nan

    if "macro_us_10y" in df.columns and "macro_us_2y" in df.columns:
        df["macro_us_10y_2y_spread"] = df["macro_us_10y"] - df["macro_us_2y"]
    else:
        df["macro_us_10y_2y_spread"] = np.nan

    # USD/KRW signals
    if "macro_usd_krw" in df.columns:
        df["macro_usd_krw_zscore_60d"] = _zscore_rolling(df["macro_usd_krw"], 60)
        df["macro_usd_krw_change_20d"] = _change_pct(df["macro_usd_krw"], 20)
    else:
        df["macro_usd_krw_zscore_60d"] = np.nan
        df["macro_usd_krw_change_20d"] = np.nan

    # PMI diffusion
    if "macro_kr_pmi" in df.columns:
        df["macro_kr_pmi_diffusion"] = df["macro_kr_pmi"] - 50.0
    else:
        df["macro_kr_pmi_diffusion"] = np.nan

    # Export YoY 3M avg
    if "macro_export_yoy" in df.columns:
        df["macro_export_yoy_3m_avg"] = _yoy_3m_avg(df["macro_export_yoy"])
    else:
        df["macro_export_yoy_3m_avg"] = np.nan

    # DXY zscore
    if "macro_dxy" in df.columns:
        df["macro_dxy_zscore_60d"] = _zscore_rolling(df["macro_dxy"], 60)
    else:
        df["macro_dxy_zscore_60d"] = np.nan

    # WTI zscore
    if "macro_wti_close" in df.columns:
        df["macro_wti_zscore_60d"] = _zscore_rolling(df["macro_wti_close"], 60)
    else:
        df["macro_wti_zscore_60d"] = np.nan

    # Ensure all PHASE3_MACRO_COLUMNS exist (zero-fill missing)
    for col in PHASE3_MACRO_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan

    return df


# ---------------------------------------------------------------------------
# 5. Top-level builder
# ---------------------------------------------------------------------------
def build_macro_panel(
    start_date: str,
    end_date: str,
    refresh_days: int = 7,
) -> pd.DataFrame:
    """Build unified macro panel: BOK + FRED + yfinance, daily-frequency.

    Returns date-indexed DataFrame with PHASE3_MACRO_COLUMNS.

    Step 1: Fetch each source (BOK monthly+daily, FRED daily, yfinance daily).
    Step 2: Outer-join on date.
    Step 3: Forward-fill (monthly series propagated to daily).
    Step 4: Compute derived signals (zscore, spread, change).
    """
    log(f"[macro] build_macro_panel {start_date} → {end_date}")

    bok = fetch_bok_macro_wide(start_date, end_date, refresh_days=refresh_days)
    fred = fetch_fred_macro_wide(start_date, end_date, refresh_days=refresh_days)
    yfm = fetch_yfinance_macro_wide(start_date, end_date, refresh_days=refresh_days)

    panels = []
    for name, p in [("bok", bok), ("fred", fred), ("yfmacro", yfm)]:
        if p is None or p.empty:
            log(f"[macro] {name} panel empty", level="WARN")
            continue
        if "date" not in p.columns:
            log(f"[macro] {name} no date column", level="WARN")
            continue
        p = p.copy()
        p["date"] = pd.to_datetime(p["date"]).dt.normalize()
        panels.append(p)

    if not panels:
        log("[macro] all sources empty -> empty panel", level="WARN")
        return pd.DataFrame(columns=["date"] + list(PHASE3_MACRO_COLUMNS))

    # Outer-join via merge
    merged = panels[0]
    for p in panels[1:]:
        merged = merged.merge(p, on="date", how="outer")
    merged = merged.sort_values("date").reset_index(drop=True)
    merged = merged.set_index("date").asfreq("D").ffill().reset_index()

    enriched = add_derived_macro_signals(merged)

    # Order columns: date first, then PHASE3_MACRO_COLUMNS, then any extras
    cols = ["date"] + [c for c in PHASE3_MACRO_COLUMNS if c in enriched.columns]
    cols += [c for c in enriched.columns if c not in cols]
    return enriched[cols]


def load_or_build_macro_panel(
    cfg: Optional[dict] = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Cached entry: load from feature_store if exists else build.

    Cache key: (start_date, end_date, KR_ENGINE_REUSE_VERSION).
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    start_date = cfg.get("start_date", "2016-01-01")
    end_date = cfg.get("end_date") or datetime.now().strftime("%Y-%m-%d")

    cache_dir = DATA_ROOT / "feature_store"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / (
        f"macro_panel_{start_date}_{end_date}_{KR_ENGINE_REUSE_VERSION}.parquet"
    )

    if not refresh and cache_path.exists():
        log(f"[macro] reuse cached panel: {cache_path.name}")
        return pd.read_parquet(cache_path)

    panel = build_macro_panel(start_date, end_date,
                               refresh_days=int(cfg.get("macro_refresh_days", 7)))
    if not panel.empty:
        try:
            panel.to_parquet(cache_path, index=False)
            log(f"[macro] saved panel ({len(panel)} rows) -> {cache_path.name}")
        except Exception as e:
            log(f"[macro] panel save fail: {e}", level="WARN")
    return panel


# ---------------------------------------------------------------------------
# 6. PIT snapshot lookup
# ---------------------------------------------------------------------------
def get_macro_snapshot(panel: pd.DataFrame, as_of: pd.Timestamp) -> dict:
    """Return latest macro values at-or-before as_of.

    All PHASE3_MACRO_COLUMNS keys present (NaN if no data).
    """
    out = {col: np.nan for col in PHASE3_MACRO_COLUMNS}
    if panel.empty or "date" not in panel.columns:
        return out
    sub = panel[panel["date"] <= as_of]
    if sub.empty:
        return out
    latest = sub.iloc[-1]
    for col in PHASE3_MACRO_COLUMNS:
        if col in latest.index:
            v = latest[col]
            out[col] = float(v) if pd.notna(v) else np.nan
    return out


# ---------------------------------------------------------------------------
# Sanity test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("kr_macro sanity test (BOK + FRED-via-yfinance + yfinance)")
    print("=" * 60)
    panel = build_macro_panel("2024-01-01", "2024-12-31", refresh_days=0)
    if panel.empty:
        print("FAIL: empty panel")
    else:
        print(f"\nPanel: {panel.shape[0]} days × {panel.shape[1]} cols")
        print(f"Coverage (non-null per column):")
        for col in PHASE3_MACRO_COLUMNS:
            if col in panel.columns:
                pct = float(panel[col].notna().mean())
                print(f"  {col:35s}: {pct:.1%}")

        snap = get_macro_snapshot(panel, pd.Timestamp("2024-12-15"))
        print(f"\nSnapshot 2024-12-15:")
        for k, v in snap.items():
            if pd.notna(v):
                print(f"  {k:35s} = {v:>10.4f}")
