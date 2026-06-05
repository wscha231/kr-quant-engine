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
    PHASE2_DART_EVENT_COLUMNS,
    PHASE2_DERIVATIVES_COLUMNS,
    PHASE2_FLOW_COLUMNS,
    PHASE3_MACRO_COLUMNS,
    PHASE3_TECHNICAL_COLUMNS,
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
# AQR-style sector neutralization (Section 6.1 of plan.md)
# ---------------------------------------------------------------------------
def _sector_group_key(row: pd.Series) -> str:
    """Sector proxy when GICS / KRX 업종지수 코드가 없을 때.

    Uses (exchange, market_cap quintile) as a coarse but PIT-safe sector proxy.
    Real sector codes (DART induty_code or KRX 업종) can replace this when
    populated; the calling functions fall back to this proxy automatically.
    """
    exchange = str(row.get("exchange") or row.get("market") or "UNK")
    mc = row.get("market_cap")
    if pd.isna(mc) or mc <= 0:
        bucket = 0
    elif mc < 5e11:        # < 5,000억
        bucket = 1
    elif mc < 2e12:        # < 2조
        bucket = 2
    elif mc < 1e13:        # < 10조
        bucket = 3
    else:
        bucket = 4
    return f"{exchange}_q{bucket}"


def add_sector_neutral_ranks(
    universe: pd.DataFrame,
    columns: tuple[str, ...] = (
        "ret_12_1m", "ret_6m", "p_pre_surge",
    ),
    sector_col: Optional[str] = None,
) -> pd.DataFrame:
    """Within-sector percentile rank for the requested signals.

    Adds `<col>_sector_rank` columns. When `sector_col` is provided AND
    present in the universe, that column is used as the sector key; otherwise
    falls back to `_sector_group_key` (exchange + mcap-quintile proxy).

    PIT-safe: operates on a single rebalance_date snapshot so no cross-time
    leakage. Caller should pass the snapshot AFTER add_basic_momentum +
    p_pre_surge are computed.
    """
    out = universe.copy()
    if out.empty:
        return out
    if sector_col and sector_col in out.columns:
        keys = out[sector_col].astype(str)
    else:
        keys = out.apply(_sector_group_key, axis=1)
    out["_sector_key"] = keys
    for col in columns:
        if col not in out.columns:
            continue
        ranks = out.groupby("_sector_key")[col].rank(
            method="average", pct=True, na_option="keep",
        )
        out[f"{col}_sector_rank"] = ranks
    out = out.drop(columns=["_sector_key"], errors="ignore")
    return out


# ---------------------------------------------------------------------------
# Minervini-style trend gate (Section 6.5 of plan.md)
# ---------------------------------------------------------------------------
def compute_trend_gate_multiplier(
    universe: pd.DataFrame,
    mode: str = "pre_entry",
) -> pd.Series:
    """Trend-template gate as a multiplier in [0.0, 1.0].

    Used downstream to multiply final_rank_score so trend signals act as a
    GATE rather than alpha (per Minervini / O'Neil). Multiple-stage modes:

      mode="pre_entry":
          gate=1 if (vol_contraction OR rsi_oversold) AND price near 52w-low/high zone.
          Lets value/turnaround names through; blocks momentum-late names.
      mode="continuation":
          gate=1 if trend_template_pass AND ma_stack_aligned AND new_52w_high_flag.
          Lets trend-up names through; blocks rolled-over names.
      mode="exit":
          gate=0 if MA50 break AND volume spike AND trend_template fail.

    Missing column → treated as neutral (gate value 1.0). The function NEVER
    raises; callers can safely apply the result regardless of upstream phase
    coverage.
    """
    n = len(universe)
    if n == 0:
        return pd.Series([], dtype=float)
    if mode == "pre_entry":
        vc = universe.get("vol_contraction", pd.Series(False, index=universe.index))
        rsi_os = universe.get("rsi_oversold", pd.Series(False, index=universe.index))
        # Distance from 52w high < 25% (within striking range)
        dist_high = universe.get("dist_from_52w_high",
                                  pd.Series(0.0, index=universe.index))
        near_high = pd.to_numeric(dist_high, errors="coerce").fillna(0).abs() < 0.25
        gate = (vc.fillna(False).astype(bool) | rsi_os.fillna(False).astype(bool)) & near_high
        return gate.astype(float)
    elif mode == "continuation":
        tt = universe.get("trend_template_pass",
                           pd.Series(False, index=universe.index))
        ma = universe.get("ma_stack_aligned",
                           pd.Series(False, index=universe.index))
        new_high = universe.get("new_52w_high_flag",
                                  pd.Series(False, index=universe.index))
        gate = (tt.fillna(False).astype(bool)
                & ma.fillna(False).astype(bool)
                | new_high.fillna(False).astype(bool))
        return gate.astype(float)
    elif mode == "exit":
        ma_break = universe.get("dist_from_ma_50",
                                  pd.Series(0.0, index=universe.index))
        below_ma50 = pd.to_numeric(ma_break, errors="coerce").fillna(0) < -0.05
        vol_spike = universe.get("volume_zscore_50",
                                   pd.Series(0.0, index=universe.index))
        spike = pd.to_numeric(vol_spike, errors="coerce").fillna(0) > 2.0
        tt_fail = ~universe.get("trend_template_pass",
                                  pd.Series(False, index=universe.index)).fillna(False).astype(bool)
        # exit signal active -> gate goes to 0; otherwise neutral 1.0
        exit_active = below_ma50 & spike & tt_fail
        return (1.0 - exit_active.astype(float)).clip(0.0, 1.0)
    else:
        return pd.Series([1.0] * n, index=universe.index)


def apply_trend_gate(
    universe: pd.DataFrame,
    score_col: str = "p_pre_surge",
    out_col: str = "score_trend_gated",
    mode: str = "pre_entry",
    soft_gate: float = 0.5,
) -> pd.DataFrame:
    """Multiply `score_col` by trend gate, write `out_col`. Soft-gate: rows
    failing the trend template still receive `soft_gate` × score (default
    0.5) so the gate is dampening rather than binary-blocking — a single
    binary cut produced too few candidates in low-trend regimes.
    """
    out = universe.copy()
    if score_col not in out.columns or out.empty:
        out[out_col] = 0.0
        return out
    gate_raw = compute_trend_gate_multiplier(out, mode=mode)
    gate = gate_raw + soft_gate * (1.0 - gate_raw)
    out[out_col] = pd.to_numeric(out[score_col], errors="coerce").fillna(0) * gate
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


# ===========================================================================
# P2 — Korean-specific corporate disclosure events (DART)
# ===========================================================================
def prepare_event_panel(
    tickers: list[str],
    start_date: str,
    end_date: str,
    event_types: Optional[list[str]] = None,
    polite_sleep_s: float = 0.15,
) -> pd.DataFrame:
    """Bulk fetch DART events for all tickers in [start_date, end_date].

    Returns long-format panel with all event categories interleaved (rcept_dt,
    event_category, ticker, corp_code + endpoint-specific fields).

    Args:
        event_types: subset of DART_EVENT_CATALOG keys. Default = priority 5
                     (treasury_buyback, capital_increase, bonus_issue,
                     insider_holdings, major_holders).

    First-time cost: ~5,000 calls for 1,000 corps × 5 events / decade.
    Subsequent: cache hits at cache_dart/events/{endpoint}/{corp}_{date}.parquet.
    """
    from kr_dart_client import (
        DART_EVENT_CATALOG,
        fetch_all_events_for_corp,
        fetch_corp_to_ticker_map,
    )

    # Default to priority-5 (★★★) — most informative, manageable API budget
    if event_types is None:
        event_types = ["treasury_buyback", "capital_increase", "bonus_issue",
                       "insider_holdings", "major_holders"]

    log(f"[features] prepare_event_panel for {len(tickers)} tickers, "
        f"events={event_types}, {start_date}~{end_date}")

    corp_map = fetch_corp_to_ticker_map()
    corp_map_dict = dict(zip(corp_map["ticker"], corp_map["corp_code"]))
    rev_map = dict(zip(corp_map["corp_code"], corp_map["ticker"]))

    bgn = str(start_date).replace("-", "")
    end = str(end_date).replace("-", "")

    frames = []
    n_tickers = len(tickers)
    for i, tk in enumerate(tickers, 1):
        if i % 50 == 0:
            log(f"[features] event_panel {i}/{n_tickers}")
        if tk not in corp_map_dict:
            continue
        corp = corp_map_dict[tk]
        df = fetch_all_events_for_corp(corp, bgn, end, event_types=event_types,
                                        polite_sleep_s=polite_sleep_s)
        if df.empty:
            continue
        df = df.copy()
        df["ticker"] = tk
        frames.append(df)

    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True, sort=False)
    log(f"[features] event_panel: {len(panel)} rows, "
        f"{panel['ticker'].nunique()} tickers, "
        f"categories={panel['event_category'].value_counts().to_dict()}")
    return panel


def add_disclosure_event_signal(
    universe: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    event_panel: Optional[pd.DataFrame] = None,
    lookback_days: int = 90,
) -> pd.DataFrame:
    """PIT join: for each ticker in universe, compute event scores using
    only events with rcept_dt <= rebalance_date.

    Args:
        universe: snapshot DataFrame with ticker + market_cap (for scoring)
        rebalance_date: PIT cutoff
        event_panel: pre-built event panel (long-format) from prepare_event_panel.
                     If None or empty, all event scores zero-filled.
        lookback_days: rolling window for event score aggregation (default 90d)

    Adds 12 columns from PHASE2_DART_EVENT_COLUMNS to universe.
    """
    out = universe.copy()
    if out.empty:
        for col in PHASE2_DART_EVENT_COLUMNS:
            out[col] = 0.0
        return out

    # Phase toggle
    if not phase_is_enabled("phase2_dart_events", default=False):
        log("[features] phase2_dart_events DISABLED -> zero-fill event cols",
            level="INFO")
        for col in PHASE2_DART_EVENT_COLUMNS:
            out[col] = 0.0
        return out

    # No event panel = zero-fill (don't crash)
    if event_panel is None or event_panel.empty:
        log("[features] phase2_dart_events: empty event_panel -> zero-fill",
            level="WARN")
        for col in PHASE2_DART_EVENT_COLUMNS:
            out[col] = 0.0
        return out

    # PIT filter
    pit_panel = event_panel[
        event_panel["rcept_dt"].notna()
        & (event_panel["rcept_dt"] <= rebalance_date)
    ].copy()
    if pit_panel.empty:
        for col in PHASE2_DART_EVENT_COLUMNS:
            out[col] = 0.0
        return out

    # Compute scores per ticker
    from kr_dart_client import compute_event_score_for_corp

    # Cap scores per ticker — extract event subset, mcap, then score
    scores_rows = []
    for ticker, mcap in zip(out["ticker"].astype(str), out.get("market_cap", 0)):
        mcap_val = float(mcap) if pd.notna(mcap) and mcap is not None else 0.0
        ticker_events = pit_panel[pit_panel["ticker"] == ticker]
        scores = compute_event_score_for_corp(
            ticker_events, rebalance_date,
            lookback_days=lookback_days, mcap=mcap_val,
        )
        scores["ticker"] = ticker
        scores_rows.append(scores)

    scores_df = pd.DataFrame(scores_rows)

    # Map event_category scores to PHASE2_DART_EVENT_COLUMNS naming convention
    column_map = {
        "total_score": "disclosure_event_total_score",
        "treasury_buyback": "event_treasury_buyback_score",
        "capital_increase": "event_capital_increase_score",
        "bonus_issue": "event_bonus_issue_score",
        "treasury_sell": "event_treasury_sell_score",
        "convertible_bond": "event_convertible_bond_score",
        "warrant_bond": "event_warrant_bond_score",
        "insider_holdings": "event_insider_holdings_score",
        "major_holders": "event_major_holders_score",
        "merger": "event_merger_score",
        "spinoff": "event_spinoff_score",
        "capital_reduction": "event_capital_reduction_score",
    }
    scores_df = scores_df.rename(columns=column_map)

    # Merge into universe
    keep_cols = ["ticker"] + [c for c in column_map.values() if c in scores_df.columns]
    out = out.merge(scores_df[keep_cols], on="ticker", how="left")

    # Zero-fill any missing
    for col in PHASE2_DART_EVENT_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
        else:
            out[col] = 0.0

    nonzero_count = int((out["disclosure_event_total_score"].abs() > 0.001).sum())
    log(f"[features] phase2 events: {nonzero_count}/{len(out)} tickers "
        f"with non-trivial event score")
    return out


# ---------------------------------------------------------------------------
# P2 composite score
# ---------------------------------------------------------------------------
def compute_p2_score(universe: pd.DataFrame) -> pd.DataFrame:
    """P0 + P1 + P2 (events) blended composite.

    Weights:
      0.30 * p0_momentum_score
      0.20 * value_score
      0.20 * quality_score
      0.10 * turnaround_score
      0.20 * disclosure_event_total_score   (event alpha — strongest single signal)

    Note: disclosure_event_total_score is already in [-1, +1] scale, so
    pre-normalization is not needed. Other scores are robust-z, so weighting
    composes naturally.
    """
    out = universe.copy()
    if out.empty:
        return out
    w = {
        "p0_momentum_score": 0.30,
        "value_score": 0.20,
        "quality_score": 0.20,
        "turnaround_score": 0.10,
        "disclosure_event_total_score": 0.20,
    }
    score = pd.Series(0.0, index=out.index)
    total_w = 0.0
    for col, weight in w.items():
        if col in out.columns:
            score += out[col].fillna(0.0) * weight
            total_w += weight
    if total_w > 0:
        score /= total_w
    out["p2_blended_score"] = score
    return out


# ===========================================================================
# P3 — Technical indicators (kr_technicals.py)
# ===========================================================================
def add_technical_indicators(
    universe: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    history_lookback_days: int = 540,
) -> pd.DataFrame:
    """For each ticker in universe, compute the full P3 technical indicator
    set from daily OHLCV history.

    Adds 31 columns from PHASE3_TECHNICAL_COLUMNS.

    Phase toggle: PHASE_PHASE3_TECHNICAL_ENABLED (default OFF).
    """
    out = universe.copy()
    if out.empty:
        for col in PHASE3_TECHNICAL_COLUMNS:
            out[col] = np.nan if col != "stage_label" else "unknown"
        return out

    if not phase_is_enabled("phase3_technical", default=False):
        log("[features] phase3_technical DISABLED -> zero/null-fill technical cols",
            level="INFO")
        for col in PHASE3_TECHNICAL_COLUMNS:
            if col in ("stage_label",):
                out[col] = "unknown"
            elif col.endswith("_flag") or col == "trend_template_pass" \
                    or col == "ma_stack_aligned" \
                    or col in ("rsi_overbought", "rsi_oversold"):
                out[col] = False
            else:
                out[col] = np.nan
        return out

    from kr_technicals import compute_all_technicals
    from kr_pykrx_client import fetch_index_ohlcv, fetch_ticker_history

    # Pre-fetch benchmark for RS rating in trend_template
    start = (rebalance_date - timedelta(days=history_lookback_days)).strftime("%Y%m%d")
    end = rebalance_date.strftime("%Y%m%d")
    bench_df = fetch_index_ohlcv(BENCHMARK_KOSPI200, start, end, refresh_days=30)
    if not bench_df.empty:
        bench_df = bench_df.sort_values("date").rename(columns={"close": "close"})

    rows = []
    tickers = out["ticker"].astype(str).tolist()
    for i, tk in enumerate(tickers):
        if i % 200 == 0 and i > 0:
            log(f"[features] technicals {i}/{len(tickers)}")
        prices = fetch_ticker_history(tk, start, end, refresh_days=7)
        if prices.empty:
            tech = {}
        else:
            prices = prices.sort_values("date")
            tech = compute_all_technicals(prices, bench_df if not bench_df.empty else None)
        tech["ticker"] = tk
        rows.append(tech)

    tech_df = pd.DataFrame(rows)
    keep = [c for c in PHASE3_TECHNICAL_COLUMNS if c in tech_df.columns]
    keep_cols = ["ticker"] + keep
    out = out.merge(tech_df[keep_cols], on="ticker", how="left")

    # Fill missing booleans + numerics
    for col in PHASE3_TECHNICAL_COLUMNS:
        if col not in out.columns:
            if col == "stage_label":
                out[col] = "unknown"
            elif col.endswith("_flag") or col in (
                "trend_template_pass", "ma_stack_aligned",
                "rsi_overbought", "rsi_oversold"):
                out[col] = False
            else:
                out[col] = np.nan
    return out


# ===========================================================================
# P2.5 — Flow signals (foreign / inst / individual supply-demand)
# ===========================================================================
def add_flow_signals(
    universe: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    market_flow_panel: Optional[pd.DataFrame] = None,
    ticker_flow_panel: Optional[pd.DataFrame] = None,
    foreign_holding_panel: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Add foreign/institutional flow signals to universe at rebalance_date.

    Three input panels (PIT-filtered downstream):
      market_flow_panel: KOSPI/KOSDAQ-level foreign/inst flow rolling sums
      ticker_flow_panel: per-ticker daily net buy z-scores + streaks
      foreign_holding_panel: per-ticker foreign ownership % daily

    All PHASE2_FLOW_COLUMNS populated (NaN if data missing).
    Phase toggle: PHASE_PHASE2_FLOW_ENABLED (default OFF).
    """
    out = universe.copy()
    if out.empty:
        for col in PHASE2_FLOW_COLUMNS:
            out[col] = np.nan
        return out

    if not phase_is_enabled("phase2_flow", default=False):
        log("[features] phase2_flow DISABLED -> NaN-fill flow cols", level="INFO")
        for col in PHASE2_FLOW_COLUMNS:
            out[col] = np.nan
        return out

    from kr_flow import (
        get_market_flow_snapshot, get_ticker_flow_snapshot,
        get_foreign_holding_snapshot,
    )

    # Market-level (broadcast)
    if market_flow_panel is not None and not market_flow_panel.empty:
        mkt_snap = get_market_flow_snapshot(market_flow_panel, rebalance_date)
    else:
        mkt_snap = {}
    for col in ("market_foreign_net_buy_20d_kospi", "market_foreign_net_buy_20d_kosdaq",
                "market_inst_net_buy_20d_kospi", "market_inst_net_buy_20d_kosdaq",
                "market_foreign_cumulative_60d"):
        out[col] = mkt_snap.get(col, np.nan)

    # Ticker-level (per-ticker)
    ticker_signal_cols = [
        "foreign_net_buy_5d_zscore", "foreign_net_buy_20d_zscore",
        "foreign_net_buy_60d_zscore", "inst_net_buy_5d_zscore",
        "inst_net_buy_20d_zscore", "inst_net_buy_60d_zscore",
        "individual_net_buy_20d_zscore",
        "foreign_buying_streak_days", "inst_buying_streak_days",
        "foreign_inst_combined_zscore_20d",
    ]

    if ticker_flow_panel is not None and not ticker_flow_panel.empty:
        rows = []
        for tk in out["ticker"].astype(str):
            snap = get_ticker_flow_snapshot(ticker_flow_panel, tk, rebalance_date)
            snap["ticker"] = tk
            rows.append(snap)
        ticker_df = pd.DataFrame(rows)
        keep = ["ticker"] + [c for c in ticker_signal_cols if c in ticker_df.columns]
        out = out.merge(ticker_df[keep], on="ticker", how="left")
    else:
        for col in ticker_signal_cols:
            out[col] = np.nan

    # Foreign holding pct + change
    if foreign_holding_panel is not None and not foreign_holding_panel.empty:
        rows = []
        for tk in out["ticker"].astype(str):
            snap = get_foreign_holding_snapshot(foreign_holding_panel, tk, rebalance_date)
            snap["ticker"] = tk
            rows.append(snap)
        hold_df = pd.DataFrame(rows)
        out = out.merge(
            hold_df[["ticker", "foreign_holding_pct", "foreign_holding_change_20d"]],
            on="ticker", how="left", suffixes=("_old", ""),
        )
        # Drop any duplicate columns from prior phases
        for c in ("foreign_holding_pct_old", "foreign_holding_change_20d_old"):
            if c in out.columns:
                out = out.drop(columns=[c])
    else:
        out["foreign_holding_pct"] = np.nan
        out["foreign_holding_change_20d"] = np.nan

    # Final: ensure all PHASE2_FLOW_COLUMNS present
    for col in PHASE2_FLOW_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    return out


# ===========================================================================
# P2.6 — Derivatives sentiment (VKOSPI + foreign futures)
# ===========================================================================
def add_derivatives_signals(
    universe: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    derivatives_panel: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Add derivatives sentiment as broadcast columns.

    Phase toggle: PHASE_PHASE2_DERIVATIVES_ENABLED (default OFF).
    """
    out = universe.copy()
    if out.empty:
        for col in PHASE2_DERIVATIVES_COLUMNS:
            out[col] = False if col == "vkospi_above_25" else np.nan
        return out

    if not phase_is_enabled("phase2_derivatives", default=False):
        log("[features] phase2_derivatives DISABLED -> NaN-fill", level="INFO")
        for col in PHASE2_DERIVATIVES_COLUMNS:
            out[col] = False if col == "vkospi_above_25" else np.nan
        return out

    if derivatives_panel is None or derivatives_panel.empty:
        log("[features] phase2_derivatives: empty panel -> NaN-fill", level="WARN")
        for col in PHASE2_DERIVATIVES_COLUMNS:
            out[col] = False if col == "vkospi_above_25" else np.nan
        return out

    from kr_derivatives import get_derivatives_snapshot
    snap = get_derivatives_snapshot(derivatives_panel, rebalance_date)
    for col in PHASE2_DERIVATIVES_COLUMNS:
        out[col] = snap.get(col,
                              False if col == "vkospi_above_25" else np.nan)
    return out


# ===========================================================================
# P3.2 — Macro layer (kr_macro.py): broadcast snapshot to all rows
# ===========================================================================
def add_macro_signals(
    universe: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    macro_panel: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Add macro snapshot at rebalance_date as broadcast columns.

    Every row gets identical macro values (regime indicators apply universally).
    Sector-specific multipliers come at P3.3 (regime classifier).

    Phase toggle: PHASE_PHASE3_MACRO_ENABLED (default OFF).
    """
    out = universe.copy()
    if out.empty:
        for col in PHASE3_MACRO_COLUMNS:
            out[col] = np.nan
        return out

    if not phase_is_enabled("phase3_macro", default=False):
        log("[features] phase3_macro DISABLED -> NaN-fill macro cols", level="INFO")
        for col in PHASE3_MACRO_COLUMNS:
            out[col] = np.nan
        return out

    if macro_panel is None or macro_panel.empty:
        log("[features] phase3_macro: empty macro_panel -> NaN-fill", level="WARN")
        for col in PHASE3_MACRO_COLUMNS:
            out[col] = np.nan
        return out

    from kr_macro import get_macro_snapshot_pit

    snap = get_macro_snapshot_pit(macro_panel, rebalance_date)
    for col in PHASE3_MACRO_COLUMNS:
        out[col] = snap.get(col, np.nan)

    nonnan = sum(1 for c in PHASE3_MACRO_COLUMNS if pd.notna(snap.get(c)))
    log(f"[features] phase3_macro: {nonnan}/{len(PHASE3_MACRO_COLUMNS)} signals available "
        f"at {rebalance_date.strftime('%Y-%m-%d')}")
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def add_universe_features(
    universe_snapshot: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    fund_panel: Optional[pd.DataFrame] = None,
    event_panel: Optional[pd.DataFrame] = None,
    macro_panel: Optional[pd.DataFrame] = None,
    market_flow_panel: Optional[pd.DataFrame] = None,
    ticker_flow_panel: Optional[pd.DataFrame] = None,
    foreign_holding_panel: Optional[pd.DataFrame] = None,
    derivatives_panel: Optional[pd.DataFrame] = None,
    event_lookback_days: int = 90,
) -> pd.DataFrame:
    """Add all enabled-phase features to a single rebalance_date snapshot.

    Args:
        universe_snapshot: rows for one rebalance_date (post-filter)
        rebalance_date: snapshot date
        fund_panel: pre-built DART quarterly panel.
        event_panel: pre-built DART events panel from prepare_event_panel.
        macro_panel: pre-built macro panel from kr_macro.build_macro_panel.
        market_flow_panel: market-level flow rolling features (KOSPI/KOSDAQ).
        ticker_flow_panel: per-ticker daily flow signals.
        foreign_holding_panel: per-ticker foreign ownership %.
        event_lookback_days: rolling window for P2 event score aggregation.
    """
    log(f"[features] add_universe_features for {rebalance_date.strftime('%Y-%m-%d')} "
        f"({len(universe_snapshot)} rows)")

    df = universe_snapshot.copy()
    df = add_basic_momentum(df, rebalance_date)
    df = add_basic_value(df, rebalance_date)

    # P1: PIT fundamentals (default OFF)
    if phase_is_enabled("phase1_fundamental", default=False):
        if fund_panel is None or fund_panel.empty:
            log("[features] phase1 enabled but fund_panel empty -> skip", level="WARN")
        else:
            df = add_pit_fundamentals(df, rebalance_date, fund_panel)
    else:
        df = add_pit_fundamentals(df, rebalance_date, pd.DataFrame())

    # P2: DART corporate events (default OFF)
    df = add_disclosure_event_signal(
        df, rebalance_date, event_panel, lookback_days=event_lookback_days,
    )

    # P2 Governance overlay (Phase C2 — risk side, complements DART events)
    # Always on when event_panel present; zero-fills 11 columns otherwise.
    from kr_governance import add_governance_signals
    df = add_governance_signals(df, rebalance_date, event_panel=event_panel)

    # P2.5: Flow signals (default OFF)
    df = add_flow_signals(
        df, rebalance_date,
        market_flow_panel=market_flow_panel,
        ticker_flow_panel=ticker_flow_panel,
        foreign_holding_panel=foreign_holding_panel,
    )

    # P2.6: Derivatives sentiment (default OFF)
    df = add_derivatives_signals(df, rebalance_date, derivatives_panel)

    # P3.1: Technical indicators (default OFF)
    df = add_technical_indicators(df, rebalance_date)

    # P3.2: Macro layer (default OFF)
    df = add_macro_signals(df, rebalance_date, macro_panel)

    # P3.3: Regime classifier (default OFF — combines all P2.5/P2.6/P3.2)
    from kr_regime import add_regime_signals
    # kospi_above_ma200 derived from technicals if available
    kospi_ma_flag = True
    if "ma_stack_aligned" in df.columns:
        # Use majority of universe (or first non-null) as proxy for market regime
        kospi_ma_flag = bool(df["ma_stack_aligned"].fillna(False).mean() > 0.3)
    df = add_regime_signals(
        df, rebalance_date,
        macro_panel=macro_panel,
        market_flow_panel=market_flow_panel,
        derivatives_panel=derivatives_panel,
        kospi_above_ma200=kospi_ma_flag,
    )

    df = add_cross_sectional_ranks(df)
    df = compute_p0_score(df)
    df = compute_p1_score(df)
    df = compute_p2_score(df)
    return df
