"""kr_flow — Foreign / institutional / individual flow signals (P2.5).

Korean market alpha most-strongly correlates with foreign + institutional
net-buying flow. This module fetches and processes:

  1. Market-level flow (KOSPI / KOSDAQ aggregated) — `fetch_market_flow_panel`
  2. Ticker-level flow (per-stock daily net buy) — `fetch_ticker_flow_panel`
  3. Foreign ownership pct (per-ticker, daily) — `fetch_foreign_holding_pct`

Then derives signals (matching kr_config.PHASE2_FLOW_COLUMNS):
  - foreign_net_buy_{5,20,60}d_zscore (per-ticker rolling)
  - inst_net_buy_{5,20,60}d_zscore
  - individual_net_buy_20d_zscore
  - foreign_holding_pct + foreign_holding_change_20d
  - foreign_buying_streak_days, inst_buying_streak_days
  - market_foreign_net_buy_20d_{kospi,kosdaq} (broadcast)
  - market_inst_net_buy_20d_{kospi,kosdaq}
  - market_foreign_cumulative_60d
  - foreign_inst_combined_zscore_20d (composite)

Caching: per-day per-market parquet at cache_pykrx/.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from kr_config import (
    DATA_ROOT,
    DEFAULT_CFG,
    KR_ENGINE_REUSE_VERSION,
    PHASE2_FLOW_COLUMNS,
)
from kr_helpers import log


# ---------------------------------------------------------------------------
# 1. Market-level flow (KOSPI / KOSDAQ aggregated)
# ---------------------------------------------------------------------------
def fetch_market_flow_daily(date: str, market: str = "KOSPI",
                              refresh_days: int = 30) -> pd.DataFrame:
    """Daily market-level flow by investor class for one date+market.

    pykrx: stock.get_market_trading_value_by_investor(date, date, market).

    Returns row with: date, market, foreign_net, inst_net, individual_net,
    pension_net, inv_trust_net, private_equity_net, etc.
    """
    cache_dir = DATA_ROOT / "cache_pykrx"
    cache_dir.mkdir(parents=True, exist_ok=True)
    d = pd.Timestamp(date).strftime("%Y%m%d")
    cache_path = cache_dir / f"market_flow_{market}_{d}.parquet"

    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass

    try:
        from pykrx import stock as _stock
    except ImportError:
        log("[flow] pykrx not installed", level="ERROR")
        return pd.DataFrame()

    try:
        df = _stock.get_market_trading_value_by_investor(d, d, market=market)
    except Exception as e:
        log(f"[flow] market flow fetch fail {market} {d}: {e}", level="WARN")
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.reset_index()
    # Pivot to single-row format
    if "투자자구분" in df.columns:
        # Build dict: investor → 순매수
        net_col = "순매수" if "순매수" in df.columns else None
        if net_col is None:
            return pd.DataFrame()
        flow_dict = dict(zip(df["투자자구분"], df[net_col]))
        row = {
            "date": pd.Timestamp(d),
            "market": market,
            "foreign_net": flow_dict.get("외국인", 0),
            "inst_net": flow_dict.get("기관합계", 0),
            "individual_net": flow_dict.get("개인", 0),
            "pension_net": flow_dict.get("연기금등", 0),
            "inv_trust_net": flow_dict.get("투신", 0),
            "private_equity_net": flow_dict.get("사모", 0),
            "bank_net": flow_dict.get("은행", 0),
            "insurance_net": flow_dict.get("보험", 0),
            "etc_net": flow_dict.get("기타금융", 0),
        }
        out = pd.DataFrame([row])
    else:
        return pd.DataFrame()

    try:
        out.to_parquet(cache_path, index=False)
    except Exception:
        pass
    return out


def fetch_market_flow_panel(
    start: str,
    end: str,
    markets: tuple[str, ...] = ("KOSPI", "KOSDAQ"),
    refresh_days: int = 30,
    polite_sleep_s: float = 0.05,
) -> pd.DataFrame:
    """Bulk market-level daily flow for KOSPI + KOSDAQ.

    Returns long-format: date, market, foreign_net, inst_net, individual_net, ...
    """
    from kr_pykrx_client import fetch_business_days
    log(f"[flow] market flow panel {start}~{end}, markets={markets}")
    bdays = fetch_business_days(
        pd.Timestamp(start).strftime("%Y%m%d"),
        pd.Timestamp(end).strftime("%Y%m%d"),
    )
    if not bdays:
        return pd.DataFrame()

    frames = []
    for i, d in enumerate(bdays, 1):
        if i % 50 == 0:
            log(f"[flow] market_flow {i}/{len(bdays)}")
        for mkt in markets:
            df = fetch_market_flow_daily(d.strftime("%Y%m%d"), market=mkt,
                                            refresh_days=refresh_days)
            if not df.empty:
                frames.append(df)
            time.sleep(polite_sleep_s)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["date", "market"])


# ---------------------------------------------------------------------------
# 2. Ticker-level flow (per-stock net buy)
# ---------------------------------------------------------------------------
def fetch_ticker_flow_for_date_range(
    ticker: str, start: str, end: str, refresh_days: int = 30
) -> pd.DataFrame:
    """Per-ticker daily foreign + inst + individual net-buy values (KRW).

    Tries pykrx first (broken in 1.2.7 vs current KRX site), then falls back
    to Naver Finance scrape (kr_naver_flow.fetch_ticker_flow_range).

    Returns DataFrame: date, ticker, foreign_net, inst_net, [individual_net,
    foreign_held_qty, foreign_holding_pct].
    """
    cache_dir = DATA_ROOT / "cache_pykrx"
    cache_dir.mkdir(parents=True, exist_ok=True)
    s = pd.Timestamp(start).strftime("%Y%m%d")
    e = pd.Timestamp(end).strftime("%Y%m%d")
    cache_path = cache_dir / f"ticker_flow_{ticker}_{s}_{e}.parquet"

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
    # 1. Try pykrx (may return empty due to KRX 2025 site changes)
    try:
        from pykrx import stock as _stock
        try:
            df = _stock.get_market_trading_value_by_date(s, e, ticker)
        except Exception:
            df = None
        if df is not None and not df.empty:
            df = df.reset_index()
            rename = {"날짜": "date", "외국인합계": "foreign_net",
                      "기관합계": "inst_net", "개인": "individual_net"}
            df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
            df["ticker"] = ticker
            keep = ["date", "ticker"] + [
                c for c in ("foreign_net", "inst_net", "individual_net")
                if c in df.columns
            ]
            df = df[keep]
        else:
            df = pd.DataFrame()
    except ImportError:
        df = pd.DataFrame()

    # 2. Naver Finance fallback (more reliable post-KRX-2025-redesign)
    if df.empty:
        try:
            from kr_naver_flow import fetch_ticker_flow_range
            naver_df = fetch_ticker_flow_range(ticker, start, end,
                                                  refresh_days=refresh_days)
            if not naver_df.empty:
                # Naver returns: date, close, volume, inst_net_qty, foreign_net_qty,
                # foreign_held_qty, foreign_holding_pct, ticker, foreign_net_value, inst_net_value
                df = naver_df.copy()
                # Map to our standard schema
                if "foreign_net_value" in df.columns:
                    df["foreign_net"] = df["foreign_net_value"]
                if "inst_net_value" in df.columns:
                    df["inst_net"] = df["inst_net_value"]
                # Individual = -(foreign + inst) approximation (KRW value-based)
                if "foreign_net" in df.columns and "inst_net" in df.columns:
                    df["individual_net"] = -(df["foreign_net"].fillna(0)
                                              + df["inst_net"].fillna(0))
                keep = ["date", "ticker"] + [
                    c for c in ("foreign_net", "inst_net", "individual_net",
                                 "foreign_net_qty", "inst_net_qty",
                                 "foreign_held_qty", "foreign_holding_pct")
                    if c in df.columns
                ]
                df = df[keep]
        except Exception as ex:
            log(f"[flow] Naver fallback fail {ticker}: {ex}", level="WARN")

    if df.empty:
        return pd.DataFrame()

    try:
        df.to_parquet(cache_path, index=False)
    except Exception:
        pass
    return df


# ---------------------------------------------------------------------------
# 3. Foreign holding %
# ---------------------------------------------------------------------------
def fetch_foreign_holding_for_date(date: str, market: str = "ALL",
                                      refresh_days: int = 30) -> pd.DataFrame:
    """Per-ticker foreign ownership % snapshot.

    pykrx: stock.get_exhaustion_rates_of_foreign_investment_by_ticker(date, market).
    Returns: ticker, foreign_holding_pct.
    """
    cache_dir = DATA_ROOT / "cache_pykrx"
    cache_dir.mkdir(parents=True, exist_ok=True)
    d = pd.Timestamp(date).strftime("%Y%m%d")
    cache_path = cache_dir / f"foreign_holding_{market}_{d}.parquet"

    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass

    try:
        from pykrx import stock as _stock
    except ImportError:
        return pd.DataFrame()

    frames = []
    markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
    for mkt in markets:
        try:
            df = _stock.get_exhaustion_rates_of_foreign_investment_by_ticker(d, market=mkt)
        except Exception as ex:
            log(f"[flow] foreign holding fail {mkt} {d}: {ex}", level="WARN")
            continue
        if df is None or df.empty:
            continue
        df = df.reset_index()
        # pykrx returns columns: 티커, 상장주식수, 보유수량, 지분율, 한도수량, 한도소진율
        if "티커" in df.columns:
            df = df.rename(columns={"티커": "ticker", "지분율": "foreign_holding_pct"})
        df["market"] = mkt
        df["date"] = pd.Timestamp(d)
        keep = ["date", "ticker", "foreign_holding_pct", "market"]
        keep = [c for c in keep if c in df.columns]
        frames.append(df[keep])

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    try:
        out.to_parquet(cache_path, index=False)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# 4. Signal computation helpers (numeric, no fetch)
# ---------------------------------------------------------------------------
def _zscore(series: pd.Series, window: int) -> pd.Series:
    """Rolling z-score (ddof=0)."""
    mu = series.rolling(window=window, min_periods=max(3, window // 3)).mean()
    sd = series.rolling(window=window, min_periods=max(3, window // 3)).std()
    return (series - mu) / sd.replace(0, np.nan)


def _streak_days(series: pd.Series) -> pd.Series:
    """For each row, count consecutive days of positive values ending at that row.

    e.g. [+, +, -, +, +, +] → [1, 2, 0, 1, 2, 3]
    """
    sign = (series > 0).astype(int)
    grouper = (sign != sign.shift()).cumsum()
    return sign.groupby(grouper).cumcount() * sign + sign


def compute_ticker_flow_signals(
    ticker_flow: pd.DataFrame, ticker: str
) -> pd.DataFrame:
    """For one ticker's daily flow series, compute rolling z-scores + streaks.

    ticker_flow expected columns: date, foreign_net, inst_net, individual_net.
    Returns same DataFrame plus computed columns.
    """
    if ticker_flow.empty:
        return ticker_flow
    df = ticker_flow.sort_values("date").copy()
    if "foreign_net" in df.columns:
        df["foreign_net_buy_5d_zscore"] = _zscore(df["foreign_net"], 5)
        df["foreign_net_buy_20d_zscore"] = _zscore(df["foreign_net"], 20)
        df["foreign_net_buy_60d_zscore"] = _zscore(df["foreign_net"], 60)
        df["foreign_buying_streak_days"] = _streak_days(df["foreign_net"])
    if "inst_net" in df.columns:
        df["inst_net_buy_5d_zscore"] = _zscore(df["inst_net"], 5)
        df["inst_net_buy_20d_zscore"] = _zscore(df["inst_net"], 20)
        df["inst_net_buy_60d_zscore"] = _zscore(df["inst_net"], 60)
        df["inst_buying_streak_days"] = _streak_days(df["inst_net"])
    if "individual_net" in df.columns:
        df["individual_net_buy_20d_zscore"] = _zscore(df["individual_net"], 20)

    # Composite: combined z-score (foreign weight 0.6, inst 0.4)
    if "foreign_net_buy_20d_zscore" in df.columns and "inst_net_buy_20d_zscore" in df.columns:
        df["foreign_inst_combined_zscore_20d"] = (
            0.6 * df["foreign_net_buy_20d_zscore"].fillna(0)
            + 0.4 * df["inst_net_buy_20d_zscore"].fillna(0)
        )
    return df


def compute_market_flow_signals(market_panel: pd.DataFrame) -> pd.DataFrame:
    """Compute market-level rolling sums (20d, 60d cumulative).

    market_panel: long-format with date, market, foreign_net, inst_net, ...
    Returns: pivot per market with rolling features.
    """
    if market_panel.empty:
        return pd.DataFrame()
    df = market_panel.sort_values(["market", "date"]).copy()
    out_frames = []
    for mkt, grp in df.groupby("market"):
        g = grp.copy()
        if "foreign_net" in g.columns:
            g[f"market_foreign_net_buy_20d_{mkt.lower()}"] = (
                g["foreign_net"].rolling(20, min_periods=5).sum()
            )
            g[f"market_foreign_cumulative_60d_{mkt.lower()}"] = (
                g["foreign_net"].rolling(60, min_periods=10).sum()
            )
        if "inst_net" in g.columns:
            g[f"market_inst_net_buy_20d_{mkt.lower()}"] = (
                g["inst_net"].rolling(20, min_periods=5).sum()
            )
        out_frames.append(g)
    if not out_frames:
        return pd.DataFrame()
    return pd.concat(out_frames, ignore_index=True)


# ---------------------------------------------------------------------------
# 5. Top-level orchestrators
# ---------------------------------------------------------------------------
def build_market_flow_panel(
    start: str,
    end: str,
    refresh_days: int = 30,
    polite_sleep_s: float = 0.05,
) -> pd.DataFrame:
    """Fetch + compute market-level flow signals for KOSPI + KOSDAQ."""
    raw = fetch_market_flow_panel(start, end, refresh_days=refresh_days,
                                    polite_sleep_s=polite_sleep_s)
    if raw.empty:
        return pd.DataFrame()
    return compute_market_flow_signals(raw)


def build_ticker_flow_panel(
    tickers: list[str],
    start: str,
    end: str,
    refresh_days: int = 30,
    polite_sleep_s: float = 0.05,
) -> pd.DataFrame:
    """Bulk fetch ticker-level flow for universe + compute signals."""
    log(f"[flow] build_ticker_flow_panel for {len(tickers)} tickers, {start}~{end}")
    frames = []
    for i, tk in enumerate(tickers, 1):
        if i % 100 == 0:
            log(f"[flow] ticker_flow {i}/{len(tickers)}")
        df = fetch_ticker_flow_for_date_range(tk, start, end, refresh_days=refresh_days)
        if df.empty:
            continue
        signals = compute_ticker_flow_signals(df, tk)
        frames.append(signals)
        time.sleep(polite_sleep_s)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def get_market_flow_snapshot(
    market_panel: pd.DataFrame, as_of: pd.Timestamp
) -> dict:
    """PIT lookup for market-level signals (broadcast)."""
    out = {
        "market_foreign_net_buy_20d_kospi": np.nan,
        "market_foreign_net_buy_20d_kosdaq": np.nan,
        "market_inst_net_buy_20d_kospi": np.nan,
        "market_inst_net_buy_20d_kosdaq": np.nan,
        "market_foreign_cumulative_60d": np.nan,
    }
    if market_panel.empty or "date" not in market_panel.columns:
        return out
    sub = market_panel[market_panel["date"] <= as_of]
    if sub.empty:
        return out
    # Latest per-market snapshot
    for mkt_lower in ("kospi", "kosdaq"):
        m_sub = sub[sub["market"].str.lower() == mkt_lower]
        if m_sub.empty:
            continue
        last = m_sub.iloc[-1]
        for col in (f"market_foreign_net_buy_20d_{mkt_lower}",
                    f"market_inst_net_buy_20d_{mkt_lower}"):
            if col in last.index and pd.notna(last[col]):
                out[col] = float(last[col])
        # 60d cumulative — use KOSPI as primary
        if mkt_lower == "kospi":
            cum_col = f"market_foreign_cumulative_60d_{mkt_lower}"
            if cum_col in last.index and pd.notna(last[cum_col]):
                out["market_foreign_cumulative_60d"] = float(last[cum_col])
    return out


def get_ticker_flow_snapshot(
    ticker_panel: pd.DataFrame, ticker: str, as_of: pd.Timestamp
) -> dict:
    """PIT lookup of ticker-level flow signals at as_of."""
    out = {
        c: np.nan for c in (
            "foreign_net_buy_5d_zscore", "foreign_net_buy_20d_zscore",
            "foreign_net_buy_60d_zscore", "inst_net_buy_5d_zscore",
            "inst_net_buy_20d_zscore", "inst_net_buy_60d_zscore",
            "individual_net_buy_20d_zscore",
            "foreign_buying_streak_days", "inst_buying_streak_days",
            "foreign_inst_combined_zscore_20d",
        )
    }
    if ticker_panel.empty:
        return out
    sub = ticker_panel[
        (ticker_panel["ticker"].astype(str) == str(ticker))
        & (ticker_panel["date"] <= as_of)
    ]
    if sub.empty:
        return out
    last = sub.sort_values("date").iloc[-1]
    for col in out.keys():
        if col in last.index and pd.notna(last[col]):
            out[col] = float(last[col])
    return out


def get_foreign_holding_snapshot(
    holding_panel: pd.DataFrame, ticker: str, as_of: pd.Timestamp,
    lookback_days_for_change: int = 20,
) -> dict:
    """foreign_holding_pct + change_20d for one ticker at as_of."""
    out = {"foreign_holding_pct": np.nan, "foreign_holding_change_20d": np.nan}
    if holding_panel.empty:
        return out
    sub = holding_panel[
        (holding_panel["ticker"].astype(str) == str(ticker))
        & (holding_panel["date"] <= as_of)
    ].sort_values("date")
    if sub.empty:
        return out
    cur = float(sub.iloc[-1]["foreign_holding_pct"]) \
        if pd.notna(sub.iloc[-1]["foreign_holding_pct"]) else np.nan
    out["foreign_holding_pct"] = cur

    cutoff = as_of - pd.Timedelta(days=lookback_days_for_change)
    prior = sub[sub["date"] <= cutoff]
    if not prior.empty and not pd.isna(cur):
        prev = float(prior.iloc[-1]["foreign_holding_pct"]) \
            if pd.notna(prior.iloc[-1]["foreign_holding_pct"]) else np.nan
        if not pd.isna(prev):
            out["foreign_holding_change_20d"] = cur - prev
    return out


def load_or_build_market_flow_panel(
    cfg: Optional[dict] = None, refresh: bool = False
) -> pd.DataFrame:
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    start = cfg.get("start_date", "2016-01-01")
    end = cfg.get("end_date") or datetime.now().strftime("%Y-%m-%d")
    cache_dir = DATA_ROOT / "feature_store"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / (
        f"market_flow_panel_{start}_{end}_{KR_ENGINE_REUSE_VERSION}.parquet"
    )
    if not refresh and cache_path.exists():
        log(f"[flow] reuse market panel: {cache_path.name}")
        return pd.read_parquet(cache_path)
    panel = build_market_flow_panel(start, end)
    if not panel.empty:
        try:
            panel.to_parquet(cache_path, index=False)
        except Exception as e:
            log(f"[flow] market panel save fail: {e}", level="WARN")
    return panel
