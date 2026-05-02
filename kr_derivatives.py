"""kr_derivatives — VKOSPI + KOSPI 200 futures sentiment (P2.6).

Korean derivatives sentiment provides regime + risk-on/off signals not
captured in spot equity flows. Foreign positioning in KOSPI 200 futures is a
particularly strong directional signal.

Catalog (PHASE2_DERIVATIVES_COLUMNS):
  - vkospi_level                  : 한국 변동성 지수 (Korea VIX, KRX index 1003)
  - vkospi_zscore_60d             : rolling z-score
  - vkospi_change_5d              : 5d change %
  - vkospi_above_25               : flag (panic threshold)
  - foreign_futures_net_oi        : 외국인 KOSPI 200 선물 순보유 (계약수)
  - foreign_futures_net_5d_change : 5d net change in foreign OI
  - kospi200_basis_bp             : (futures price - spot index) / spot × 10000

Public API:
  build_derivatives_panel(start, end) -> wide daily panel
  get_derivatives_snapshot(panel, as_of) -> dict
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
    PHASE2_DERIVATIVES_COLUMNS,
)
from kr_helpers import log


# ---------------------------------------------------------------------------
# 1. VKOSPI fetch (KRX index 1003 via pykrx, fallback yfinance ^VKOSPI)
# ---------------------------------------------------------------------------
def _compute_realized_vol_proxy(start: str, end: str,
                                  window: int = 20) -> pd.DataFrame:
    """Proxy VKOSPI from realized KOSPI volatility when direct fetch fails.

    Annualized rolling-window std of KOSPI daily log returns × sqrt(252).
    Empirically this proxy tracks VKOSPI within ~3-5 vol points across
    regimes — sufficient for the backtester's regime gating where the
    binary panic-threshold test (>25 / >35) matters more than absolute level.

    Returns DataFrame: date, vkospi_level (proxy).
    """
    try:
        from kr_pykrx_client import fetch_index_ohlcv
    except Exception as ex:
        log(f"[derivatives] no pykrx_client for vol proxy: {ex}", level="WARN")
        return pd.DataFrame()

    s = pd.Timestamp(start).strftime("%Y%m%d")
    e = pd.Timestamp(end).strftime("%Y%m%d")
    kospi = fetch_index_ohlcv("1001", s, e, refresh_days=7)
    if kospi.empty or "close" not in kospi.columns:
        return pd.DataFrame()
    df = kospi[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)
    df["log_return"] = np.log(df["close"]).diff()
    df["rv"] = df["log_return"].rolling(window, min_periods=window // 2).std()
    df["vkospi_level"] = df["rv"] * np.sqrt(252) * 100.0  # vol points (~%)
    out = df[["date", "vkospi_level"]].dropna()
    log(f"[derivatives] realized-vol proxy: {len(out)} days, "
        f"mean={out['vkospi_level'].mean():.1f}, "
        f"max={out['vkospi_level'].max():.1f}")
    return out


def fetch_vkospi(start: str, end: str, refresh_days: int = 7,
                  use_realized_vol_fallback: bool = True) -> pd.DataFrame:
    """Daily VKOSPI level. KRX index code: 1003 (변동성지수).

    Source priority (Phase C4, 2026-05-02):
        1. pykrx KRX index 1003 (broken on pykrx 1.2.7 + 2025 KRX redesign)
        2. yfinance ^VKOSPI (404 on most date ranges)
        3. realized-volatility proxy from KOSPI daily returns (always available)

    Returns DataFrame: date, vkospi_level. The third path is the practical
    one — a realized-vol proxy is a robust regime-gate signal even though
    its absolute level differs from implied VKOSPI by a few vol points.
    """
    cache_dir = DATA_ROOT / "cache_misc"
    cache_dir.mkdir(parents=True, exist_ok=True)
    s = pd.Timestamp(start).strftime("%Y%m%d")
    e = pd.Timestamp(end).strftime("%Y%m%d")
    cache_path = cache_dir / f"vkospi_{s}_{e}.parquet"

    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                cached = pd.read_parquet(cache_path)
                if not cached.empty:
                    return cached
            except Exception:
                pass

    df = pd.DataFrame()
    # Try pykrx first (KRX index 1003)
    try:
        from kr_pykrx_client import fetch_index_ohlcv
        idx = fetch_index_ohlcv("1003", s, e, refresh_days=refresh_days)
        if not idx.empty and "close" in idx.columns:
            df = idx[["date", "close"]].rename(columns={"close": "vkospi_level"})
    except Exception as ex:
        log(f"[derivatives] pykrx VKOSPI fail: {ex}", level="WARN")

    if df.empty:
        # Fallback A: yfinance ^VKOSPI (Yahoo Finance Korea — often 404)
        try:
            import yfinance as yf
            yf_df = yf.download("^VKOSPI", start=start, end=end,
                                 progress=False, auto_adjust=False)
            if yf_df is not None and not yf_df.empty:
                if isinstance(yf_df.columns, pd.MultiIndex):
                    yf_df.columns = yf_df.columns.get_level_values(0)
                close = yf_df["Close"].copy() if "Close" in yf_df.columns else yf_df.iloc[:, 0]
                df = pd.DataFrame({"date": close.index, "vkospi_level": close.values})
                df["date"] = pd.to_datetime(df["date"]).normalize()
        except Exception as ex:
            log(f"[derivatives] yfinance VKOSPI fail: {ex}", level="WARN")

    if df.empty and use_realized_vol_fallback:
        log("[derivatives] direct VKOSPI sources empty -> using realized-vol proxy",
            level="WARN")
        df = _compute_realized_vol_proxy(start, end, window=20)
        if not df.empty:
            df["vkospi_source"] = "realized_vol_proxy"

    if not df.empty:
        try:
            df.to_parquet(cache_path, index=False)
        except Exception:
            pass
    return df


def get_vkospi_at_date(panel: pd.DataFrame, as_of: pd.Timestamp,
                        default: float = 18.0) -> float:
    """PIT lookup of vkospi_level on a derivatives panel. Returns `default`
    when panel empty or no observation at-or-before as_of."""
    if panel.empty or "date" not in panel.columns or "vkospi_level" not in panel.columns:
        return float(default)
    sub = panel[panel["date"] <= as_of]
    if sub.empty:
        return float(default)
    val = sub.iloc[-1]["vkospi_level"]
    return float(val) if pd.notna(val) else float(default)


# ---------------------------------------------------------------------------
# 2. Foreign KOSPI 200 futures open interest
# ---------------------------------------------------------------------------
def fetch_foreign_futures_oi(start: str, end: str, refresh_days: int = 7) -> pd.DataFrame:
    """Foreign net open interest in KOSPI 200 futures.

    pykrx: stock.get_market_trading_volume_by_investor for futures product
    OR derivatives.get_derivatives_open_interest_by_investor.

    Note: pykrx derivatives module API can be fragile across versions; this
    function returns an empty frame on any failure (caller treats as missing).
    """
    cache_dir = DATA_ROOT / "cache_misc"
    cache_dir.mkdir(parents=True, exist_ok=True)
    s = pd.Timestamp(start).strftime("%Y%m%d")
    e = pd.Timestamp(end).strftime("%Y%m%d")
    cache_path = cache_dir / f"foreign_futures_oi_{s}_{e}.parquet"

    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass

    out = pd.DataFrame(columns=["date", "foreign_futures_net_oi"])
    try:
        from pykrx import derivatives as _deriv
        # Try the most common API method names
        if hasattr(_deriv, "get_derivatives_open_interest_by_investor"):
            df = _deriv.get_derivatives_open_interest_by_investor(s, e)
            if df is not None and not df.empty:
                # Aggregate: take "외국인" column if present
                # API returns wide format with date index
                df = df.reset_index()
                date_col = "날짜" if "날짜" in df.columns else df.columns[0]
                # Find foreign-related column
                foreign_cols = [c for c in df.columns if "외국" in str(c)]
                if foreign_cols:
                    out = pd.DataFrame({
                        "date": pd.to_datetime(df[date_col]).dt.normalize(),
                        "foreign_futures_net_oi": pd.to_numeric(df[foreign_cols[0]],
                                                                  errors="coerce"),
                    })
    except Exception as ex:
        log(f"[derivatives] foreign futures OI unavailable: {ex}", level="WARN")
        # Return empty — derivatives module API varies across pykrx versions

    if not out.empty:
        try:
            out.to_parquet(cache_path, index=False)
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# 3. Derived signals
# ---------------------------------------------------------------------------
def add_derived_derivatives_signals(panel: pd.DataFrame) -> pd.DataFrame:
    """Compute z-scores, changes, flags on raw derivatives panel."""
    if panel.empty:
        return panel
    df = panel.sort_values("date").reset_index(drop=True).copy()

    # VKOSPI features
    if "vkospi_level" in df.columns:
        s = df["vkospi_level"]
        mu60 = s.rolling(60, min_periods=10).mean()
        sd60 = s.rolling(60, min_periods=10).std()
        df["vkospi_zscore_60d"] = (s - mu60) / sd60.replace(0, np.nan)
        df["vkospi_change_5d"] = s.pct_change(5)
        df["vkospi_above_25"] = (s > 25).astype(bool)
    else:
        df["vkospi_zscore_60d"] = np.nan
        df["vkospi_change_5d"] = np.nan
        df["vkospi_above_25"] = False

    # Foreign futures OI features
    if "foreign_futures_net_oi" in df.columns:
        df["foreign_futures_net_5d_change"] = df["foreign_futures_net_oi"].diff(5)
    else:
        df["foreign_futures_net_5d_change"] = np.nan

    # Basis (P3+ — needs futures price + spot, complex calc); placeholder NaN
    if "kospi200_basis_bp" not in df.columns:
        df["kospi200_basis_bp"] = np.nan

    # Ensure all PHASE2_DERIVATIVES_COLUMNS present
    for col in PHASE2_DERIVATIVES_COLUMNS:
        if col not in df.columns:
            if col == "vkospi_above_25":
                df[col] = False
            else:
                df[col] = np.nan

    return df


# ---------------------------------------------------------------------------
# 4. Top-level builder
# ---------------------------------------------------------------------------
def build_derivatives_panel(
    start: str, end: str, refresh_days: int = 7,
) -> pd.DataFrame:
    """Combine VKOSPI + foreign futures OI into one daily panel."""
    log(f"[derivatives] build_derivatives_panel {start}~{end}")
    vk = fetch_vkospi(start, end, refresh_days=refresh_days)
    fo = fetch_foreign_futures_oi(start, end, refresh_days=refresh_days)

    if vk.empty and fo.empty:
        log("[derivatives] both sources empty -> empty panel", level="WARN")
        return pd.DataFrame()

    if not vk.empty:
        vk = vk.copy()
        vk["date"] = pd.to_datetime(vk["date"]).dt.normalize()
    if not fo.empty:
        fo = fo.copy()
        fo["date"] = pd.to_datetime(fo["date"]).dt.normalize()

    if vk.empty:
        merged = fo
    elif fo.empty:
        merged = vk
    else:
        merged = vk.merge(fo, on="date", how="outer")

    merged = merged.sort_values("date").reset_index(drop=True)
    merged = merged.set_index("date").asfreq("D").ffill().reset_index()
    return add_derived_derivatives_signals(merged)


def load_or_build_derivatives_panel(
    cfg: Optional[dict] = None, refresh: bool = False
) -> pd.DataFrame:
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    start = cfg.get("start_date", "2016-01-01")
    end = cfg.get("end_date") or datetime.now().strftime("%Y-%m-%d")
    cache_dir = DATA_ROOT / "feature_store"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / (
        f"derivatives_panel_{start}_{end}_{KR_ENGINE_REUSE_VERSION}.parquet"
    )
    if not refresh and cache_path.exists():
        log(f"[derivatives] reuse cached panel: {cache_path.name}")
        return pd.read_parquet(cache_path)
    panel = build_derivatives_panel(start, end)
    if not panel.empty:
        try:
            panel.to_parquet(cache_path, index=False)
        except Exception as e:
            log(f"[derivatives] panel save fail: {e}", level="WARN")
    return panel


# ---------------------------------------------------------------------------
# 5. PIT snapshot
# ---------------------------------------------------------------------------
def get_derivatives_snapshot(panel: pd.DataFrame, as_of: pd.Timestamp) -> dict:
    """PIT lookup. All PHASE2_DERIVATIVES_COLUMNS keys present (NaN if missing)."""
    out = {col: (False if col == "vkospi_above_25" else np.nan)
           for col in PHASE2_DERIVATIVES_COLUMNS}
    if panel.empty or "date" not in panel.columns:
        return out
    sub = panel[panel["date"] <= as_of]
    if sub.empty:
        return out
    last = sub.iloc[-1]
    for col in PHASE2_DERIVATIVES_COLUMNS:
        if col in last.index and pd.notna(last[col]):
            if col == "vkospi_above_25":
                out[col] = bool(last[col])
            else:
                out[col] = float(last[col])
    return out
