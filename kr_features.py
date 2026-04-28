"""kr_features — feature engineering for kr_quant_engine.

P0 scope:
- add_basic_momentum: 1m/3m/6m/12m return + RS_kospi/RS_kosdaq + 12-1m skip
- add_basic_value: PER/PBR/dividend yield from pykrx (TTM)
- add_universe_features: orchestrator (P0 + P1)

P1 scope (DART 도착 후 — 2026-04-28):
- prepare_pit_fundamentals_panel: bulk DART quarterly fetch via fnlttMultiAcnt
- add_pit_fundamentals: PIT-safe join of universe + DART panel at rebal_date
- compute_value_score, compute_quality_score, compute_turnaround_score
- compute_p1_score: composite of P0 momentum + P1 fundamentals

Phase toggle pattern (r1000):
- phase_is_enabled("phase0_momentum") gate per signal block
- phase_is_enabled("phase1_fundamental") gate for P1 block
- disabled → zero-fill columns (preserve schema, downstream KeyError 방지)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from kr_config import (
    BENCHMARK_KOSPI200,
    BENCHMARK_KOSDAQ150,
    PHASE0_MOMENTUM_COLUMNS,
    PHASE1_FUNDAMENTAL_COLUMNS,
)
from kr_helpers import (
    cross_sectional_robust_z,
    log,
    percentile_rank,
    phase_is_enabled,
)
from kr_pykrx_client import (
    fetch_index_ohlcv,
    fetch_per_pbr_eps_bps,
    fetch_ticker_history,
)


# ---------------------------------------------------------------------------
# Momentum & RS
# ---------------------------------------------------------------------------
def _ticker_close_at_or_before(prices: pd.DataFrame, target_date: pd.Timestamp) -> Optional[float]:
    """Last close at or before target_date. None if no data."""
    if prices.empty or "date" not in prices.columns:
        return None
    sub = prices[prices["date"] <= target_date]
    if sub.empty:
        return None
    return float(sub.iloc[-1]["close"])


def _compute_return_window(
    prices: pd.DataFrame, end_date: pd.Timestamp, months: int, skip_recent_months: int = 0
) -> Optional[float]:
    """Return over `months` months ending at end_date.

    skip_recent_months > 0 → 12-1m style (skip recent N months, measure earlier window).
    """
    end = end_date - pd.DateOffset(months=skip_recent_months)
    start = end - pd.DateOffset(months=months)
    p_end = _ticker_close_at_or_before(prices, end)
    p_start = _ticker_close_at_or_before(prices, start)
    if p_end is None or p_start is None or p_start <= 0:
        return None
    return p_end / p_start - 1.0


def add_basic_momentum_for_ticker(
    ticker: str,
    rebalance_date: pd.Timestamp,
    history_lookback_days: int = 540,
) -> dict:
    """Compute 1m/3m/6m/12m + 12-1m return for a single ticker."""
    start = (rebalance_date - timedelta(days=history_lookback_days)).strftime("%Y%m%d")
    end = rebalance_date.strftime("%Y%m%d")
    prices = fetch_ticker_history(ticker, start, end, refresh_days=7)
    if prices.empty:
        return {c: np.nan for c in ("ret_1m", "ret_3m", "ret_6m", "ret_12m", "ret_12_1m")}
    return {
        "ret_1m":    _compute_return_window(prices, rebalance_date, 1),
        "ret_3m":    _compute_return_window(prices, rebalance_date, 3),
        "ret_6m":    _compute_return_window(prices, rebalance_date, 6),
        "ret_12m":   _compute_return_window(prices, rebalance_date, 12),
        "ret_12_1m": _compute_return_window(prices, rebalance_date, 11, skip_recent_months=1),
    }


def add_basic_momentum(
    universe: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    history_lookback_days: int = 540,
) -> pd.DataFrame:
    """Add momentum + RS columns to universe panel for one rebalance date.

    Args:
        universe: rows for one rebalance_date (post-filter eligible+ineligible)
        rebalance_date: the snapshot date
    """
    if not phase_is_enabled("phase0_momentum", default=True):
        log("[features] phase0_momentum DISABLED -> zero-fill momentum cols", level="INFO")
        out = universe.copy()
        for col in PHASE0_MOMENTUM_COLUMNS:
            out[col] = 0.0
        return out

    out = universe.copy()
    if out.empty:
        return out

    # Per-ticker return windows
    rows = []
    tickers = out["ticker"].astype(str).tolist()
    for i, tk in enumerate(tickers):
        if i % 200 == 0 and i > 0:
            log(f"[features] momentum {i}/{len(tickers)}")
        rets = add_basic_momentum_for_ticker(tk, rebalance_date, history_lookback_days)
        rets["ticker"] = tk
        rows.append(rets)

    rets_df = pd.DataFrame(rows)
    out = out.merge(rets_df, on="ticker", how="left")

    # Benchmark returns for RS
    start = (rebalance_date - timedelta(days=history_lookback_days)).strftime("%Y%m%d")
    end = rebalance_date.strftime("%Y%m%d")
    kospi200 = fetch_index_ohlcv(BENCHMARK_KOSPI200, start, end, refresh_days=30)
    kosdaq150 = fetch_index_ohlcv(BENCHMARK_KOSDAQ150, start, end, refresh_days=30)

    def _bench_return(idx_df: pd.DataFrame, months: int) -> float:
        return _compute_return_window(idx_df.rename(columns={"close": "close"}), rebalance_date, months) or 0.0

    bench_ret = {
        "kospi_3m": _bench_return(kospi200, 3),
        "kospi_12m": _bench_return(kospi200, 12),
        "kosdaq_3m": _bench_return(kosdaq150, 3),
        "kosdaq_12m": _bench_return(kosdaq150, 12),
    }

    out["rs_kospi_3m"] = out["ret_3m"] - bench_ret["kospi_3m"]
    out["rs_kospi_12m"] = out["ret_12m"] - bench_ret["kospi_12m"]
    out["rs_kosdaq_3m"] = out["ret_3m"] - bench_ret["kosdaq_3m"]
    out["rs_kosdaq_12m"] = out["ret_12m"] - bench_ret["kosdaq_12m"]

    # Hard sanitize: NaN -> 0.0 for downstream sleeve composites
    for c in PHASE0_MOMENTUM_COLUMNS:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)

    return out


# ---------------------------------------------------------------------------
# Basic value (P0 from pykrx, P1 from DART)
# ---------------------------------------------------------------------------
def add_basic_value(
    universe: pd.DataFrame,
    rebalance_date: pd.Timestamp,
) -> pd.DataFrame:
    """Add PER, PBR, dividend yield from pykrx fundamental snapshot.

    P0 placeholder — use trailing TTM from pykrx. P1 will replace with DART
    point-in-time XBRL.
    """
    out = universe.copy()
    if out.empty:
        return out

    fund = fetch_per_pbr_eps_bps(rebalance_date.strftime("%Y%m%d"), market="ALL", refresh_days=30)
    if fund.empty:
        for c in ("per", "pbr", "eps", "bps", "dividend_yield_pct"):
            out[c] = np.nan
        return out

    keep = ["ticker", "per", "pbr", "eps", "bps", "dividend_yield_pct"]
    keep = [c for c in keep if c in fund.columns]
    out = out.merge(fund[keep], on="ticker", how="left")
    return out


# ---------------------------------------------------------------------------
# Cross-sectional ranks (P0 simple, P3 will add winsorize)
# ---------------------------------------------------------------------------
def add_cross_sectional_ranks(universe: pd.DataFrame) -> pd.DataFrame:
    """Add percentile_rank columns for momentum signals.

    Used by Top-N selection logic.
    """
    out = universe.copy()
    if out.empty:
        return out
    for col in ("ret_12_1m", "ret_6m", "ret_3m", "rs_kospi_12m", "rs_kosdaq_12m"):
        if col in out.columns:
            out[f"{col}_rank"] = percentile_rank(out[col])
            out[f"{col}_z"] = cross_sectional_robust_z(out[col])
    return out


# ---------------------------------------------------------------------------
# P0 composite score
# ---------------------------------------------------------------------------
def compute_p0_score(universe: pd.DataFrame) -> pd.DataFrame:
    """Simple Top-N momentum score: weighted sum of z-scores.

    P0 weights (intentional simple — tune in P2+):
       0.50 * ret_12_1m_z       (12-1m momentum, primary)
       0.30 * ret_6m_z          (intermediate momentum)
       0.20 * rs_kospi_12m_z    (vs KOSPI200 RS)
    """
    out = universe.copy()
    if out.empty:
        return out

    w = {"ret_12_1m_z": 0.50, "ret_6m_z": 0.30, "rs_kospi_12m_z": 0.20}
    score = pd.Series(0.0, index=out.index)
    total_w = 0.0
    for col, weight in w.items():
        if col in out.columns:
            score += out[col].fillna(0.0) * weight
            total_w += weight
    if total_w > 0:
        score /= total_w
    out["p0_momentum_score"] = score
    return out


# ===========================================================================
# P1 — Point-in-time fundamentals (DART)
# ===========================================================================

def prepare_pit_fundamentals_panel(
    tickers: list[str],
    start_year: int = 2014,
    end_year: Optional[int] = None,
) -> pd.DataFrame:
    """Build PIT-safe quarterly fundamentals panel for given tickers.

    Returns long-format DataFrame:
      ticker, corp_code, bsns_year, reprt_code, rcept_dt, period_end,
      revenue, operating_income, net_income, total_assets, total_equity,
      total_liabilities

    Heavy operation — first run ~3-5min for ~2,000 corps over 8 years.
    Cached at cache_dart/multi/* per (year, reprt_code, batch_hash).

    Two-year lookback in start_year enables YoY computation at the start of
    backtest window.
    """
    from kr_dart_client import (
        build_universe_quarterly_panel,
        fetch_corp_to_ticker_map,
    )

    log(f"[features] prepare_pit_fundamentals_panel for {len(tickers)} tickers, "
        f"{start_year}~{end_year or 'now'}")

    # Map ticker -> corp_code
    corp_map = fetch_corp_to_ticker_map()
    corp_map_dict = dict(zip(corp_map["ticker"], corp_map["corp_code"]))

    needed_corps = [corp_map_dict[t] for t in tickers if t in corp_map_dict]
    log(f"[features] corp_code mapped: {len(needed_corps)}/{len(tickers)} tickers")

    if not needed_corps:
        return pd.DataFrame()

    panel = build_universe_quarterly_panel(needed_corps, start_year=start_year,
                                            end_year=end_year)

    if panel.empty:
        return pd.DataFrame()

    # Reverse map corp_code -> ticker
    rev_map = dict(zip(corp_map["corp_code"], corp_map["ticker"]))
    panel["ticker"] = panel["corp_code"].map(rev_map)

    return panel


def _compute_ttm_from_panel(corp_panel: pd.DataFrame, as_of: pd.Timestamp) -> dict:
    """Given a single-corp PIT-filtered panel sorted by period_end,
    compute trailing-twelve-month aggregates for flow accounts (revenue,
    operating_income, net_income, operating_cash_flow) and latest stock
    accounts (total_assets, total_equity, total_liabilities).

    Returns dict of TTM values + latest balance sheet + period metadata.

    Algorithm for TTM flow:
      1. If latest filing is annual (11011) within last 12 months: use thstrm_amount
         (which IS the annual cumulative).
      2. Else: TTM = sum of last 4 distinct quarters' single-quarter amounts.
         For 11013 (Q1): single = thstrm_amount
         For 11012 (semi/H1): single = thstrm_amount - prev_q1
         For 11014 (Q3): single = thstrm_amount - prev_semi
         For 11011 (annual): single = thstrm_amount - prev_q3
    """
    out: dict = {}
    if corp_panel.empty:
        return out

    p = corp_panel.sort_values(["bsns_year", "reprt_code"]).copy()
    latest = p.iloc[-1]

    # Latest balance sheet (always from most recent filing)
    for col in ("total_assets", "total_equity", "total_liabilities"):
        if col in p.columns and pd.notna(latest.get(col)):
            out[col] = float(latest[col])

    # TTM flow: simple approach — use the last available "annual" amount,
    # or sum of last 4 quarters' incremental amounts. For P1 simple, take
    # the last 4 calendar quarters and compute their incremental amounts.
    # Fallback: use latest annual (11011) if recent enough.

    flow_cols = ("revenue", "operating_income", "net_income")

    # Try: most recent annual report
    annual = p[p["reprt_code"] == "11011"].sort_values(["bsns_year"])
    if not annual.empty:
        last_annual = annual.iloc[-1]
        # If filings beyond annual exist, prefer rolling TTM
        post_annual = p[p["period_end"] > last_annual["period_end"]]
        if post_annual.empty:
            for c in flow_cols:
                if pd.notna(last_annual.get(c)):
                    out[f"{c}_ttm"] = float(last_annual[c])

    if not any(f"{c}_ttm" in out for c in flow_cols):
        # Compute TTM from 4 most recent filings as approximation:
        # For simplicity, sum incremental quarter amounts
        # P1 simple: just use latest cumulative (annual style)
        # then derive from period structure
        last4 = p.tail(4)
        if len(last4) >= 1:
            # Naive but defensive: take latest cumulative as TTM proxy if available
            for c in flow_cols:
                vals = last4[c].dropna() if c in last4.columns else pd.Series(dtype=float)
                if len(vals) > 0:
                    # Use the most recent filing's cumulative (often annual or semi annualized x2)
                    latest_v = float(vals.iloc[-1])
                    latest_reprt = last4.iloc[-1].get("reprt_code", "11011")
                    # Annualization factor based on reprt_code
                    factor = {"11013": 4.0, "11012": 2.0, "11014": 4/3, "11011": 1.0}.get(latest_reprt, 1.0)
                    out[f"{c}_ttm"] = latest_v * factor

    out["fundamentals_period_end"] = latest["period_end"]
    out["fundamentals_rcept_dt"] = latest.get("rcept_dt")
    out["fundamentals_bsns_year"] = int(latest.get("bsns_year") or 0)
    out["fundamentals_reprt_code"] = latest.get("reprt_code")
    return out


def _compute_yoy_from_panel(corp_panel: pd.DataFrame) -> dict:
    """Compute YoY growth for revenue + operating_income + net_income.

    Uses same-quarter prior year matching (e.g., 2024 Q1 vs 2023 Q1).
    """
    out: dict = {}
    if corp_panel.empty:
        return out

    p = corp_panel.sort_values(["bsns_year", "reprt_code"]).copy()
    if len(p) < 2:
        return out
    latest = p.iloc[-1]

    # Find same-quarter prior year
    same_q_prev = p[(p["bsns_year"] == latest["bsns_year"] - 1)
                    & (p["reprt_code"] == latest["reprt_code"])]
    if same_q_prev.empty:
        return out
    prev = same_q_prev.iloc[-1]

    for c in ("revenue", "operating_income", "net_income"):
        cur = latest.get(c)
        prv = prev.get(c)
        if pd.notna(cur) and pd.notna(prv) and abs(prv) > 1e-3:
            out[f"{c}_growth_yoy"] = float(cur - prv) / abs(float(prv))
    return out


def add_pit_fundamentals(
    universe: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    fund_panel: pd.DataFrame,
) -> pd.DataFrame:
    """PIT-safe join of universe + fundamentals at rebalance_date.

    Args:
        universe: rows for one rebalance_date
        rebalance_date: snapshot
        fund_panel: full historical DART panel (ticker, corp_code, period_end,
                    rcept_dt, revenue, operating_income, net_income,
                    total_assets, total_equity, total_liabilities)

    Output adds columns:
      - revenue_ttm, operating_income_ttm, net_income_ttm
      - total_assets, total_equity, total_liabilities (latest)
      - revenue_growth_yoy, operating_income_growth_yoy, net_income_growth_yoy
      - roe, roa, operating_margin, net_margin, debt_to_equity, fcf_yield
      - value_score, quality_score, turnaround_score (composites)
      - fundamentals_period_end, fundamentals_rcept_dt (PIT audit trail)
    """
    if not phase_is_enabled("phase1_fundamental", default=False):
        log("[features] phase1_fundamental DISABLED -> zero-fill", level="INFO")
        out = universe.copy()
        zero_fill_cols = [
            "revenue_ttm", "operating_income_ttm", "net_income_ttm",
            "total_assets", "total_equity", "total_liabilities",
            "revenue_growth_yoy", "operating_income_growth_yoy", "net_income_growth_yoy",
            "roe", "roa", "operating_margin", "net_margin", "debt_to_equity",
            "value_score", "quality_score", "turnaround_score",
        ]
        for c in zero_fill_cols:
            out[c] = 0.0
        out["fundamentals_period_end"] = pd.NaT
        out["fundamentals_rcept_dt"] = pd.NaT
        return out

    out = universe.copy()
    if out.empty or fund_panel.empty:
        return out

    # PIT filter: only rcept_dt <= rebalance_date
    pit = fund_panel[fund_panel["rcept_dt"].notna()
                     & (fund_panel["rcept_dt"] <= rebalance_date)].copy()
    if pit.empty:
        log(f"[features] no PIT-visible fundamentals at {rebalance_date}", level="WARN")

    # Per-ticker compute TTM + YoY + ratios
    rows = []
    for ticker in out["ticker"].astype(str):
        corp_panel = pit[pit["ticker"] == ticker]
        ttm = _compute_ttm_from_panel(corp_panel, rebalance_date)
        yoy = _compute_yoy_from_panel(corp_panel)
        merged = {"ticker": ticker, **ttm, **yoy}
        rows.append(merged)

    fund_df = pd.DataFrame(rows)
    out = out.merge(fund_df, on="ticker", how="left")

    # Derived ratios
    rev = out.get("revenue_ttm", pd.Series(dtype=float))
    opi = out.get("operating_income_ttm", pd.Series(dtype=float))
    ni  = out.get("net_income_ttm", pd.Series(dtype=float))
    eq  = out.get("total_equity", pd.Series(dtype=float))
    asset = out.get("total_assets", pd.Series(dtype=float))
    liab  = out.get("total_liabilities", pd.Series(dtype=float))

    safe_div = lambda a, b: (a / b.where(b.abs() > 1e-3))
    out["roe"] = safe_div(ni, eq)
    out["roa"] = safe_div(ni, asset)
    out["operating_margin"] = safe_div(opi, rev)
    out["net_margin"] = safe_div(ni, rev)
    out["debt_to_equity"] = safe_div(liab, eq)
    out["fcf_yield"] = pd.NA   # placeholder until OCF available

    # Composite signals
    out = compute_value_score(out)
    out = compute_quality_score(out)
    out = compute_turnaround_score(out)

    # Sanitize inf/NaN -> 0 for downstream sleeve composites
    sanitize_cols = [
        "roe", "roa", "operating_margin", "net_margin", "debt_to_equity",
        "revenue_growth_yoy", "operating_income_growth_yoy", "net_income_growth_yoy",
        "value_score", "quality_score", "turnaround_score",
    ]
    for c in sanitize_cols:
        if c in out.columns:
            s = pd.to_numeric(out[c], errors="coerce")
            s = s.replace([np.inf, -np.inf], np.nan).fillna(0.0)
            out[c] = s.astype(float)
    return out


def compute_value_score(df: pd.DataFrame) -> pd.DataFrame:
    """Composite value score: low PER + low PBR + high dividend + earnings recovery.

    Z-score blend (cross-sectional).
    """
    out = df.copy()
    parts = []
    weights = []

    if "per" in out.columns:
        # Lower PER = better → invert
        per = pd.to_numeric(out["per"], errors="coerce")
        # Cap extreme values (PER > 200 = unreliable)
        per = per.where((per > 0) & (per < 200))
        parts.append(-cross_sectional_robust_z(per))   # negate so high z = cheap
        weights.append(0.40)

    if "pbr" in out.columns:
        pbr = pd.to_numeric(out["pbr"], errors="coerce")
        pbr = pbr.where((pbr > 0) & (pbr < 50))
        parts.append(-cross_sectional_robust_z(pbr))
        weights.append(0.30)

    if "dividend_yield_pct" in out.columns:
        dy = pd.to_numeric(out["dividend_yield_pct"], errors="coerce")
        parts.append(cross_sectional_robust_z(dy))
        weights.append(0.20)

    if "operating_income_growth_yoy" in out.columns:
        # Earnings recovery boosts value score (low PER + recovering = inflection)
        og = pd.to_numeric(out["operating_income_growth_yoy"], errors="coerce")
        parts.append(cross_sectional_robust_z(og))
        weights.append(0.10)

    if not parts:
        out["value_score"] = 0.0
        return out

    w = np.array(weights) / sum(weights)
    score = sum(p.fillna(0.0) * weight for p, weight in zip(parts, w))
    out["value_score"] = score
    return out


def compute_quality_score(df: pd.DataFrame) -> pd.DataFrame:
    """Composite quality: high ROE + high operating margin + low leverage.

    Z-score blend.
    """
    out = df.copy()
    parts = []
    weights = []

    if "roe" in out.columns:
        roe = pd.to_numeric(out["roe"], errors="coerce")
        # Cap extreme (ROE > 100% = data issue)
        roe = roe.where((roe > -1.0) & (roe < 1.0))
        parts.append(cross_sectional_robust_z(roe))
        weights.append(0.40)

    if "operating_margin" in out.columns:
        om = pd.to_numeric(out["operating_margin"], errors="coerce")
        om = om.where((om > -1.0) & (om < 1.0))
        parts.append(cross_sectional_robust_z(om))
        weights.append(0.35)

    if "debt_to_equity" in out.columns:
        de = pd.to_numeric(out["debt_to_equity"], errors="coerce")
        de = de.where(de > 0)
        # Lower D/E = better → invert
        parts.append(-cross_sectional_robust_z(de))
        weights.append(0.25)

    if not parts:
        out["quality_score"] = 0.0
        return out

    w = np.array(weights) / sum(weights)
    score = sum(p.fillna(0.0) * weight for p, weight in zip(parts, w))
    out["quality_score"] = score
    return out


def compute_turnaround_score(df: pd.DataFrame) -> pd.DataFrame:
    """Turnaround signal: prior loss → current profit + accelerating growth.

    r1000 Phase 1 등가. Catches inflection points where loss-making firm
    flips to profit (high asymmetric upside).
    """
    out = df.copy()
    parts = []
    weights = []

    if "operating_income_ttm" in out.columns:
        opi = pd.to_numeric(out["operating_income_ttm"], errors="coerce")
        # Sign flip detection: loss → profit
        parts.append((opi > 0).astype(float))   # binary "currently profitable"
        weights.append(0.40)

    if "net_income_ttm" in out.columns and "operating_income_ttm" in out.columns:
        ni = pd.to_numeric(out["net_income_ttm"], errors="coerce")
        opi = pd.to_numeric(out["operating_income_ttm"], errors="coerce")
        # Loss-narrowing: NI improving while OPI also improving
        # (proxy via current NI > 0)
        parts.append((ni > 0).astype(float))
        weights.append(0.20)

    if "operating_income_growth_yoy" in out.columns:
        og = pd.to_numeric(out["operating_income_growth_yoy"], errors="coerce")
        # Accelerating growth (>50% YoY)
        parts.append((og > 0.5).astype(float) - 0.5)   # center
        weights.append(0.40)

    if not parts:
        out["turnaround_score"] = 0.0
        return out

    w = np.array(weights) / sum(weights)
    score = sum(p.fillna(0.0) * weight for p, weight in zip(parts, w))
    out["turnaround_score"] = score
    return out


# ---------------------------------------------------------------------------
# P1 composite score
# ---------------------------------------------------------------------------
def compute_p1_score(universe: pd.DataFrame) -> pd.DataFrame:
    """P0 momentum + P1 fundamentals blended composite.

    Weights:
      0.40 * p0_momentum_score   (carry forward P0)
      0.25 * value_score
      0.25 * quality_score
      0.10 * turnaround_score    (inflection alpha)
    """
    out = universe.copy()
    if out.empty:
        return out
    w = {"p0_momentum_score": 0.40, "value_score": 0.25,
         "quality_score": 0.25, "turnaround_score": 0.10}
    score = pd.Series(0.0, index=out.index)
    total_w = 0.0
    for col, weight in w.items():
        if col in out.columns:
            score += out[col].fillna(0.0) * weight
            total_w += weight
    if total_w > 0:
        score /= total_w
    out["p1_blended_score"] = score
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def add_universe_features(
    universe_snapshot: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    fund_panel: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Add all enabled-phase features to a single rebalance_date snapshot.

    Args:
        universe_snapshot: rows for one rebalance_date (post-filter)
        rebalance_date: snapshot date
        fund_panel: pre-built DART quarterly panel (full history, all tickers).
                    If None and phase1_fundamental enabled, will skip P1.
    """
    log(f"[features] add_universe_features for {rebalance_date.strftime('%Y-%m-%d')} "
        f"({len(universe_snapshot)} rows)")

    df = universe_snapshot.copy()
    df = add_basic_momentum(df, rebalance_date)
    df = add_basic_value(df, rebalance_date)

    # P1: PIT fundamentals (default OFF — explicit env enable)
    if phase_is_enabled("phase1_fundamental", default=False):
        if fund_panel is None or fund_panel.empty:
            log("[features] phase1 enabled but fund_panel empty -> skip", level="WARN")
        else:
            df = add_pit_fundamentals(df, rebalance_date, fund_panel)
    else:
        # Zero-fill P1 columns to preserve schema
        df = add_pit_fundamentals(df, rebalance_date, pd.DataFrame())

    df = add_cross_sectional_ranks(df)
    df = compute_p0_score(df)
    df = compute_p1_score(df)
    return df
