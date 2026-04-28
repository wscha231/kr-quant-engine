"""kr_multibagger — Episode discovery + retrospective signal mining.

Korean multibagger episode mining system. Modeled on r1000 phase11
(`tmp_r1000_quant_engine/research/phase11_episodes_v2.py`).

Goal: identify all (ticker, t0, t1) windows where forward_return >= 300% within
24 months, filter to mid+ cap quality (mcap >= 5,000억원), and extract
pre-surge signals so we can predict future episodes.

Pipeline:
  build_price_panel(start, end)            # ticker × month_end close
       ↓
  define_multibagger_episodes(panel, ...)  # finds (entry, peak) pairs
       ↓
  identify_surge_start(panel, episodes)    # accumulation → explosive
       ↓
  quality_filter(episodes, mktcap, value)  # mcap/liquidity filter at entry
       ↓
  research/multibagger_episodes_v0.csv     # ~50-150 episodes per year (KR mid+)

Then (Stage 2):
  add_multibagger_pre_signals(...)         # extract pre-surge features
  train classifier (CatBoost binary)        # P(pre_surge | features)
  live picks per month                      # top-K candidate sleeve
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from kr_config import (
    DEFAULT_CFG,
    KR_ENGINE_REUSE_VERSION,
    DATA_ROOT,
)
from kr_helpers import log
from kr_pykrx_client import (
    fetch_market_cap_market,
    fetch_month_end_business_days,
    fetch_ticker_history,
)


# ---------------------------------------------------------------------------
# Default config (override via cfg)
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD = 3.0           # 300% return (4x peak/entry)
DEFAULT_WINDOW_MONTHS = 24
DEFAULT_MIN_MCAP_KRW = 5e11        # 5,000억원
DEFAULT_MIN_TRADING_VALUE_KRW = 5e8
DEFAULT_MIN_LISTED_MONTHS = 12
DEFAULT_PRE_SURGE_LOOKBACK = 6
DEFAULT_POST_SURGE_INCLUDE = 3
DEFAULT_SURGE_BREAKOUT_PCT = 0.20


# ---------------------------------------------------------------------------
# 1. Price panel builder
# ---------------------------------------------------------------------------
def build_price_panel(
    tickers: list[str],
    start_date: str,
    end_date: str,
    refresh_days: int = 30,
) -> pd.DataFrame:
    """For each ticker, fetch history and pivot to wide month-end panel.

    Returns DataFrame indexed by month_end date, columns = tickers,
    values = close price.

    NaN where ticker not yet listed or already delisted.
    """
    log(f"[multibagger] build_price_panel for {len(tickers)} tickers, "
        f"{start_date} ~ {end_date}")

    # Get target month-ends (KRX business days)
    month_ends = fetch_month_end_business_days(
        pd.Timestamp(start_date).strftime("%Y%m%d"),
        pd.Timestamp(end_date).strftime("%Y%m%d"),
    )
    if not month_ends:
        log("[multibagger] no business days in range", level="WARN")
        return pd.DataFrame()

    panel = pd.DataFrame(index=month_ends, dtype=float)

    s = (pd.Timestamp(start_date) - timedelta(days=30)).strftime("%Y%m%d")
    e = (pd.Timestamp(end_date) + timedelta(days=10)).strftime("%Y%m%d")

    for i, tk in enumerate(tickers, 1):
        if i % 200 == 0:
            log(f"[multibagger] price panel {i}/{len(tickers)}")
        df = fetch_ticker_history(tk, s, e, refresh_days=refresh_days)
        if df.empty or "close" not in df.columns:
            continue
        df = df.sort_values("date").set_index("date")
        # For each month_end, take close at-or-before
        closes = []
        for me in month_ends:
            sub = df.loc[df.index <= me]
            closes.append(float(sub["close"].iloc[-1]) if not sub.empty else np.nan)
        panel[tk] = closes

    log(f"[multibagger] price panel: {panel.shape[0]} months × {panel.shape[1]} tickers")
    return panel


# ---------------------------------------------------------------------------
# 2. Episode definition
# ---------------------------------------------------------------------------
def find_episodes_for_ticker(
    prices: pd.Series,
    threshold: float = DEFAULT_THRESHOLD,
    window_months: int = DEFAULT_WINDOW_MONTHS,
) -> list[dict]:
    """Find all multibagger episodes for one ticker's monthly close series.

    Algorithm:
      For each candidate entry_date t0:
        - Compute max close within (t0, t0 + window_months]
        - If max_close / close_at_t0 - 1 >= threshold → episode candidate
      Then collapse overlapping candidates: keep the one with earliest entry_date
      whose peak_date is within the window. This gives non-overlapping episodes.

    Returns list of dicts with keys:
      entry_date, entry_price, peak_date, peak_price, max_return, months_to_peak

    Args:
        prices: month-end indexed close prices (sorted ascending)
        threshold: e.g. 3.0 = 300% gain
        window_months: max time from entry to peak

    Returns:
        list of episode dicts (possibly empty)
    """
    s = prices.dropna()
    if len(s) < window_months + 1:
        return []

    candidates: list[dict] = []
    for i, (t0, p0) in enumerate(s.items()):
        if p0 <= 0 or pd.isna(p0):
            continue
        # forward window indices (1..window_months ahead, inclusive)
        end_idx = min(i + window_months, len(s) - 1)
        if end_idx <= i:
            continue
        forward = s.iloc[i + 1: end_idx + 1]
        if forward.empty:
            continue
        peak_idx_local = forward.idxmax()
        peak_price = forward.loc[peak_idx_local]
        if pd.isna(peak_price) or peak_price <= 0:
            continue
        gain = peak_price / p0 - 1.0
        if gain >= threshold:
            # months_to_peak: integer month count via panel index distance
            # (panel is month-end indexed, so idx diff = exact month count)
            peak_pos = s.index.get_loc(peak_idx_local)
            months_to_peak = int(peak_pos - i)
            candidates.append({
                "entry_date": pd.Timestamp(t0),
                "entry_price": float(p0),
                "peak_date": pd.Timestamp(peak_idx_local),
                "peak_price": float(peak_price),
                "max_return": float(gain),
                "months_to_peak": int(months_to_peak),
            })

    if not candidates:
        return []

    # Collapse overlapping episodes: greedy from earliest entry_date.
    # Two candidates overlap if (entry_a < peak_b) and (entry_b < peak_a).
    candidates.sort(key=lambda d: d["entry_date"])
    kept: list[dict] = []
    last_peak = pd.Timestamp("1900-01-01")
    for c in candidates:
        if c["entry_date"] > last_peak:
            kept.append(c)
            last_peak = c["peak_date"]
        # else: overlapping with previous kept episode, skip

    return kept


def define_multibagger_episodes(
    price_panel: pd.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
    window_months: int = DEFAULT_WINDOW_MONTHS,
) -> pd.DataFrame:
    """For each ticker in panel, find multibagger episodes.

    Returns long-format DataFrame:
      ticker, entry_date, entry_price, peak_date, peak_price, max_return, months_to_peak

    Empty DataFrame if no episodes found.
    """
    if price_panel.empty:
        return pd.DataFrame(columns=["ticker", "entry_date", "entry_price",
                                      "peak_date", "peak_price", "max_return",
                                      "months_to_peak"])

    rows = []
    for tk in price_panel.columns:
        episodes = find_episodes_for_ticker(price_panel[tk], threshold, window_months)
        for ep in episodes:
            rows.append({"ticker": str(tk), **ep})

    if not rows:
        return pd.DataFrame(columns=["ticker", "entry_date", "entry_price",
                                      "peak_date", "peak_price", "max_return",
                                      "months_to_peak"])

    df = pd.DataFrame(rows).sort_values(
        ["ticker", "entry_date"]
    ).reset_index(drop=True)
    log(f"[multibagger] define_multibagger_episodes: {len(df)} episodes "
        f"({df['ticker'].nunique()} unique tickers)")
    return df


# ---------------------------------------------------------------------------
# 3. Surge start identification
# ---------------------------------------------------------------------------
def identify_surge_start(
    prices: pd.Series,
    entry_date: pd.Timestamp,
    peak_date: pd.Timestamp,
    breakout_pct: float = DEFAULT_SURGE_BREAKOUT_PCT,
) -> Optional[pd.Timestamp]:
    """Find surge_start_date = first month after entry_date where:
       price >= entry_price * (1 + breakout_pct)
       AND price > entry_price (forward, not retracing)

    This splits "accumulation" period (entry → surge_start) from
    "explosive" period (surge_start → peak).

    Returns None if no clear breakout (rare).
    """
    s = prices.dropna()
    if entry_date not in s.index:
        return None
    entry_price = s.loc[entry_date]
    if pd.isna(entry_price) or entry_price <= 0:
        return None

    target = entry_price * (1 + breakout_pct)

    # Window: (entry_date, peak_date]
    window = s.loc[(s.index > entry_date) & (s.index <= peak_date)]
    if window.empty:
        return None

    above = window[window >= target]
    if above.empty:
        # No clean breakout — use halfway point as fallback
        midpoint = window[window >= entry_price * 1.10]
        if midpoint.empty:
            return None
        return pd.Timestamp(midpoint.index[0])

    return pd.Timestamp(above.index[0])


def add_surge_start_dates(
    episodes: pd.DataFrame,
    price_panel: pd.DataFrame,
    breakout_pct: float = DEFAULT_SURGE_BREAKOUT_PCT,
) -> pd.DataFrame:
    """Add surge_start_date column to episodes DataFrame."""
    if episodes.empty:
        episodes["surge_start_date"] = pd.NaT
        episodes["accumulation_months"] = np.nan
        return episodes

    surge_starts = []
    for _, row in episodes.iterrows():
        tk = str(row["ticker"])
        if tk not in price_panel.columns:
            surge_starts.append(pd.NaT)
            continue
        ssd = identify_surge_start(
            price_panel[tk], row["entry_date"], row["peak_date"], breakout_pct,
        )
        surge_starts.append(ssd)

    out = episodes.copy()
    out["surge_start_date"] = surge_starts
    out["accumulation_months"] = (
        out["surge_start_date"] - out["entry_date"]
    ).dt.total_seconds() / (86400 * 30.4375)
    return out


# ---------------------------------------------------------------------------
# 4. Quality filter
# ---------------------------------------------------------------------------
def fetch_mcap_at_dates(
    tickers: list[str],
    dates: list[pd.Timestamp],
    refresh_days: int = 30,
) -> dict[tuple[str, pd.Timestamp], float]:
    """Fetch market_cap for (ticker, date) pairs.

    Uses pykrx market_cap snapshot per date. Returns dict {(ticker, date): mcap}.

    Cached at the daily snapshot level (already cached in fetch_market_cap_market).
    """
    out: dict[tuple[str, pd.Timestamp], float] = {}
    unique_dates = sorted(set(dates))

    for d in unique_dates:
        snap = fetch_market_cap_market(d.strftime("%Y%m%d"), market="ALL",
                                        refresh_days=refresh_days)
        if snap.empty or "ticker" not in snap.columns:
            continue
        ts = pd.Timestamp(d)
        mcap_lookup = dict(zip(snap["ticker"].astype(str),
                                snap["market_cap"].astype(float)))
        for tk in tickers:
            if tk in mcap_lookup:
                out[(tk, ts)] = mcap_lookup[tk]

    return out


def quality_filter_episodes(
    episodes: pd.DataFrame,
    min_mcap_krw: float = DEFAULT_MIN_MCAP_KRW,
    refresh_days: int = 30,
) -> pd.DataFrame:
    """Filter episodes by mcap >= min_mcap at entry_date.

    Adds columns:
      mcap_at_entry, quality_pass

    Returns DataFrame with all rows; quality_pass=True for keep, False for drop.
    Caller can filter to passed rows.
    """
    if episodes.empty:
        return episodes

    log(f"[multibagger] quality_filter: min_mcap = {min_mcap_krw:,.0f} KRW")
    tickers = episodes["ticker"].astype(str).tolist()
    dates = episodes["entry_date"].tolist()
    mcap_lookup = fetch_mcap_at_dates(tickers, dates, refresh_days=refresh_days)

    out = episodes.copy()
    mcap_at_entry = []
    for _, row in out.iterrows():
        key = (str(row["ticker"]), pd.Timestamp(row["entry_date"]))
        mcap_at_entry.append(mcap_lookup.get(key, np.nan))
    out["mcap_at_entry"] = mcap_at_entry
    out["quality_pass"] = (
        out["mcap_at_entry"].notna()
        & (out["mcap_at_entry"] >= min_mcap_krw)
    )
    n_pass = int(out["quality_pass"].sum())
    log(f"[multibagger] quality_filter: {n_pass}/{len(out)} episodes pass mcap "
        f">= {min_mcap_krw:,.0f}")
    return out


# ---------------------------------------------------------------------------
# 5. Top-level orchestrator
# ---------------------------------------------------------------------------
def build_episode_panel(
    price_panel: pd.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
    window_months: int = DEFAULT_WINDOW_MONTHS,
    min_mcap_krw: float = DEFAULT_MIN_MCAP_KRW,
    breakout_pct: float = DEFAULT_SURGE_BREAKOUT_PCT,
    apply_quality_filter: bool = True,
) -> pd.DataFrame:
    """Run full episode discovery pipeline on a price panel.

    Steps:
      1. find episodes (rolling forward max gain)
      2. add surge_start_date
      3. add mcap_at_entry + quality_pass

    Returns episodes DataFrame (all rows, with quality_pass flag).
    """
    eps = define_multibagger_episodes(price_panel, threshold, window_months)
    if eps.empty:
        return eps
    eps = add_surge_start_dates(eps, price_panel, breakout_pct)
    if apply_quality_filter:
        eps = quality_filter_episodes(eps, min_mcap_krw)
    return eps


def load_or_build_episode_panel(
    cfg: Optional[dict] = None,
    tickers: Optional[list[str]] = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Cached top-level entry: load from feature_store if exists, else build.

    Cache key: (start_date, end_date, threshold, window, mcap, version).
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    start_date = cfg.get("start_date", "2016-01-01")
    end_date = cfg.get("end_date") or datetime.now().strftime("%Y-%m-%d")
    threshold = float(cfg.get("multibagger_return_threshold", DEFAULT_THRESHOLD))
    window = int(cfg.get("multibagger_window_months", DEFAULT_WINDOW_MONTHS))
    min_mcap = float(cfg.get("multibagger_min_mcap_krw", DEFAULT_MIN_MCAP_KRW))
    breakout = float(cfg.get("multibagger_surge_breakout_pct", DEFAULT_SURGE_BREAKOUT_PCT))

    cache_dir = DATA_ROOT / "feature_store"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / (
        f"multibagger_episodes_v0_{start_date}_{end_date}_"
        f"t{threshold:.1f}_w{window}m_mc{int(min_mcap):.0e}_"
        f"{KR_ENGINE_REUSE_VERSION}.parquet"
    )

    if not refresh and cache_path.exists():
        log(f"[multibagger] reuse cached episodes: {cache_path.name}")
        return pd.read_parquet(cache_path)

    if tickers is None:
        # Discover universe from latest mcap snapshot
        log("[multibagger] discovering universe from latest mcap snapshot")
        snap = fetch_market_cap_market(
            pd.Timestamp(end_date).strftime("%Y%m%d"), market="ALL",
        )
        if snap.empty:
            log("[multibagger] empty mcap snapshot, cannot determine universe",
                level="ERROR")
            return pd.DataFrame()
        # Pre-filter: only tickers that ever had mcap >= min_mcap (saves price fetch)
        tickers = sorted(snap[snap["market_cap"] >= min_mcap]["ticker"]
                         .astype(str).unique().tolist())
        log(f"[multibagger] universe (latest mcap >= {min_mcap:,.0f}): {len(tickers)}")

    panel = build_price_panel(tickers, start_date, end_date)
    if panel.empty:
        log("[multibagger] empty price panel", level="ERROR")
        return pd.DataFrame()

    eps = build_episode_panel(panel, threshold, window, min_mcap, breakout)

    try:
        eps.to_parquet(cache_path, index=False)
        log(f"[multibagger] saved episodes -> {cache_path}")
    except Exception as e:
        log(f"[multibagger] cache save fail: {e}", level="WARN")

    return eps


# ---------------------------------------------------------------------------
# 6. Summary stats
# ---------------------------------------------------------------------------
def episode_summary_stats(episodes: pd.DataFrame) -> dict:
    """Distribution analysis of episodes."""
    if episodes.empty:
        return {"count": 0}

    if "quality_pass" in episodes.columns:
        pass_eps = episodes[episodes["quality_pass"].astype(bool)]
    else:
        pass_eps = episodes
    out = {
        "total_episodes": int(len(episodes)),
        "quality_pass_episodes": int(len(pass_eps)),
        "unique_tickers": int(episodes["ticker"].nunique()),
        "unique_pass_tickers": int(pass_eps["ticker"].nunique()),
    }

    if not pass_eps.empty:
        out["return_p25"] = float(pass_eps["max_return"].quantile(0.25))
        out["return_p50"] = float(pass_eps["max_return"].quantile(0.50))
        out["return_p75"] = float(pass_eps["max_return"].quantile(0.75))
        out["return_p95"] = float(pass_eps["max_return"].quantile(0.95))
        out["return_max"] = float(pass_eps["max_return"].max())
        out["months_to_peak_median"] = float(pass_eps["months_to_peak"].median())
        if "mcap_at_entry" in pass_eps.columns:
            out["mcap_at_entry_median"] = float(pass_eps["mcap_at_entry"].median())
            out["mcap_at_entry_p95"] = float(pass_eps["mcap_at_entry"].quantile(0.95))

        # Episode count by year (entry_year)
        if "entry_date" in pass_eps.columns:
            ey = pass_eps["entry_date"].dt.year
            out["episodes_by_year"] = ey.value_counts().sort_index().to_dict()

    return out


# ---------------------------------------------------------------------------
# Sanity test (mock data)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Synthetic price panel with planted multibagger
    print("=" * 60)
    print("Multibagger sanity test (mock data)")
    print("=" * 60)

    np.random.seed(42)
    months = pd.date_range("2018-01-31", "2024-12-31", freq="ME")
    panel = pd.DataFrame(index=months)
    # Boring ticker (random walk)
    panel["BORING"] = 10000 * np.cumprod(1 + np.random.randn(len(months)) * 0.05)
    # Multibagger: month 12 → month 30, 5x gain
    bagger = np.ones(len(months))
    bagger[:12] = 1.0
    bagger[12:30] = np.linspace(1.0, 5.0, 18)   # 1x to 5x
    bagger[30:] = 5.0 * np.cumprod(1 + np.random.randn(len(months) - 30) * 0.03)
    panel["BAGGER"] = 10000 * bagger

    eps = define_multibagger_episodes(panel, threshold=3.0, window_months=24)
    print(f"\nEpisodes found: {len(eps)}")
    if not eps.empty:
        print(eps.to_string(index=False))

    eps2 = add_surge_start_dates(eps, panel, breakout_pct=0.20)
    if not eps2.empty:
        print(f"\nSurge start dates:")
        print(eps2[["ticker", "entry_date", "surge_start_date", "peak_date",
                    "max_return", "accumulation_months"]].to_string(index=False))

    stats = episode_summary_stats(eps2)
    print(f"\nSummary stats:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print()
