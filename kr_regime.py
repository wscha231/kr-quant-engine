"""kr_regime — 8-state Korean market regime classifier (P3.3).

Combines macro (P3.2) + flow (P2.5) + derivatives (P2.6) + technicals (P3.1)
into a single regime label and sleeve weight multipliers per row.

Regime taxonomy:
  bull_trending   : KOSPI > MA200, foreign cumulative net buy +, VKOSPI < 18
  bull_peaking    : VKOSPI 18-25, foreign net sell, KOSPI > MA200 but breadth weak
  bear_falling    : KOSPI < MA200, VKOSPI > 25, foreign sell, USD/KRW rising
  bear_bottoming  : VKOSPI > 30 + foreign sell slowing, KOSPI ≈ 52w low
  recovery        : KOSPI MA200 reclaim + foreign net buy resume + PMI < 50
  sideways        : default (no condition strongly fires)
  stagflation_kr  : PMI < 48 + USD/KRW zscore > 1 + BOK hike
  won_crisis      : USD/KRW > 1400 + foreign sell large + KOSPI -10% in 5d

Sleeve multipliers (r1000 Phase 4 ported):
  Each regime maps to {core, future, early} multipliers — applied to sleeve
  weights pre-clip downstream.

Public API:
  classify_regime(macro_snap, flow_snap, deriv_snap, kospi_state) -> str
  get_regime_sleeve_multipliers(regime: str) -> dict[sleeve, multiplier]
  add_regime_signals(universe, rebalance_date, panels) -> DataFrame
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from kr_config import PHASE3_REGIME_COLUMNS
from kr_helpers import log, phase_is_enabled


# ===========================================================================
# 1. Regime sleeve multipliers (r1000 Phase 4 etc.)
# ===========================================================================
REGIME_SLEEVE_MULTIPLIERS = {
    "bull_trending": {"core": 1.00, "future": 1.30, "early": 1.20},
    "bull_peaking":  {"core": 1.10, "future": 0.85, "early": 0.80},
    "bear_falling":  {"core": 1.20, "future": 0.50, "early": 0.40},
    "bear_bottoming":{"core": 0.90, "future": 1.10, "early": 1.30},
    "recovery":      {"core": 1.00, "future": 1.20, "early": 1.40},
    "sideways":      {"core": 1.00, "future": 1.00, "early": 1.00},
    "stagflation_kr":{"core": 1.30, "future": 0.70, "early": 0.50},
    "won_crisis":    {"core": 0.60, "future": 0.30, "early": 0.20},   # de-risk
    "unknown":       {"core": 1.00, "future": 1.00, "early": 1.00},
}

REGIME_SLEEVE_MULTIPLIER_CLAMP = (0.30, 1.50)


# ===========================================================================
# 2. Classifier
# ===========================================================================
def classify_regime(
    macro_snap: dict,
    flow_snap: dict,
    derivatives_snap: dict,
    kospi_above_ma200: bool = True,
    kospi_5d_return: float = 0.0,
) -> str:
    """8-state regime classifier from snapshot dicts.

    Args:
        macro_snap: dict from kr_macro.get_macro_snapshot
        flow_snap: dict from kr_flow.get_market_flow_snapshot
        derivatives_snap: dict from kr_derivatives.get_derivatives_snapshot
        kospi_above_ma200: pre-computed boolean (from kr_technicals or
                           comparing index OHLCV)
        kospi_5d_return: 5d return of KOSPI for crisis detection

    Returns: regime label (one of REGIME_SLEEVE_MULTIPLIERS keys).
    """
    # Pull values defensively (None / NaN → safe defaults)
    def _g(d, k, default=np.nan):
        v = d.get(k, default) if d else default
        return default if (v is None or (isinstance(v, float) and pd.isna(v))) else v

    vkospi = _g(derivatives_snap, "vkospi_level", 18.0)
    vkospi_z = _g(derivatives_snap, "vkospi_zscore_60d", 0.0)
    usd_krw = _g(macro_snap, "macro_usd_krw", 1300.0)
    usd_krw_z = _g(macro_snap, "macro_usd_krw_zscore_60d", 0.0)
    bok_change = _g(macro_snap, "macro_bok_rate_change_60d", 0.0)
    pmi = _g(macro_snap, "macro_kr_pmi", 50.0)
    foreign_cum = _g(flow_snap, "market_foreign_cumulative_60d", 0.0)
    foreign_20d = _g(flow_snap, "market_foreign_net_buy_20d_kospi", 0.0)

    # 1. won_crisis (highest priority — overrides others)
    if (usd_krw > 1400 or usd_krw_z > 2.5) and foreign_cum < -1e13 \
            and kospi_5d_return < -0.10:
        return "won_crisis"

    # 2. stagflation_kr (PMI weak + KRW weak + BOK hawkish)
    if pmi < 48 and usd_krw_z > 1.0 and bok_change > 0:
        return "stagflation_kr"

    # 3. Bear regimes (KOSPI below MA200)
    if not kospi_above_ma200:
        if vkospi > 30 and foreign_20d > -1e12:
            return "bear_bottoming"  # VKOSPI peak + foreign sell slowing
        if vkospi > 25 or foreign_cum < -5e12:
            return "bear_falling"

    # 4. Recovery (above MA200, foreign returning, but PMI still recovering)
    if kospi_above_ma200 and foreign_cum > 0 and pmi < 50:
        return "recovery"

    # 5. Bull regimes (above MA200)
    if kospi_above_ma200:
        if vkospi < 18 and foreign_cum > 5e12:
            return "bull_trending"
        if vkospi > 20 and foreign_20d < 0:
            return "bull_peaking"
        if foreign_cum > 0:
            return "bull_trending"  # mild bull

    # Default: sideways (or insufficient data)
    return "sideways"


def get_regime_sleeve_multipliers(regime: str) -> dict:
    """Return {core, future, early} multipliers for given regime.

    Multipliers are clamped to REGIME_SLEEVE_MULTIPLIER_CLAMP.
    Unknown regime returns identity (1.0, 1.0, 1.0).
    """
    base = REGIME_SLEEVE_MULTIPLIERS.get(regime, REGIME_SLEEVE_MULTIPLIERS["unknown"])
    lo, hi = REGIME_SLEEVE_MULTIPLIER_CLAMP
    return {sleeve: float(np.clip(mult, lo, hi)) for sleeve, mult in base.items()}


# ===========================================================================
# 3. Universe integration
# ===========================================================================
def add_regime_signals(
    universe: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    macro_panel: Optional[pd.DataFrame] = None,
    market_flow_panel: Optional[pd.DataFrame] = None,
    derivatives_panel: Optional[pd.DataFrame] = None,
    kospi_above_ma200: Optional[bool] = None,
    kospi_5d_return: float = 0.0,
) -> pd.DataFrame:
    """Classify regime at rebalance_date + broadcast to all rows.

    Output columns (PHASE3_REGIME_COLUMNS):
      - vkospi_zscore_63d (carried from derivatives)
      - usd_krw_change_20d (carried from macro)
      - foreign_kospi_cumulative_5d (carried from flow)
      - bok_rate_change_60d (carried from macro)
      - kospi_above_ma200 (passed in or default True)
      - regime_label_kr
      - regime_sleeve_multiplier_{core, future, early}

    Phase toggle: PHASE_PHASE3_REGIME_ENABLED (default OFF).
    """
    out = universe.copy()
    if out.empty:
        for col in PHASE3_REGIME_COLUMNS:
            if col == "regime_label_kr":
                out[col] = "unknown"
            else:
                out[col] = np.nan
        return out

    if not phase_is_enabled("phase3_regime", default=False):
        log("[features] phase3_regime DISABLED -> default fill", level="INFO")
        for col in PHASE3_REGIME_COLUMNS:
            if col == "regime_label_kr":
                out[col] = "unknown"
            elif "multiplier" in col:
                out[col] = 1.0
            else:
                out[col] = np.nan
        return out

    # Build snapshots
    macro_snap = {}
    flow_snap = {}
    deriv_snap = {}
    if macro_panel is not None and not macro_panel.empty:
        from kr_macro import get_macro_snapshot
        macro_snap = get_macro_snapshot(macro_panel, rebalance_date)
    if market_flow_panel is not None and not market_flow_panel.empty:
        from kr_flow import get_market_flow_snapshot
        flow_snap = get_market_flow_snapshot(market_flow_panel, rebalance_date)
    if derivatives_panel is not None and not derivatives_panel.empty:
        from kr_derivatives import get_derivatives_snapshot
        deriv_snap = get_derivatives_snapshot(derivatives_panel, rebalance_date)

    # Pre-compute kospi_above_ma200 if not passed
    if kospi_above_ma200 is None:
        # Try via macro panel: compare KOSPI close to MA200 if available
        # (yfinance kospi_yf is in macro_panel via fetch_yfinance_macro_wide)
        kospi_above_ma200 = True   # default optimistic; caller should override

    # Classify
    regime = classify_regime(macro_snap, flow_snap, deriv_snap,
                              kospi_above_ma200=kospi_above_ma200,
                              kospi_5d_return=kospi_5d_return)
    multipliers = get_regime_sleeve_multipliers(regime)

    log(f"[regime] {rebalance_date.strftime('%Y-%m-%d')}: regime = {regime}, "
        f"multipliers = core/future/early = "
        f"{multipliers['core']:.2f}/{multipliers['future']:.2f}/{multipliers['early']:.2f}")

    # Broadcast to all rows
    out["regime_label_kr"] = regime
    out["regime_sleeve_multiplier_core"] = multipliers["core"]
    out["regime_sleeve_multiplier_future"] = multipliers["future"]
    out["regime_sleeve_multiplier_early"] = multipliers["early"]
    out["kospi_above_ma200"] = bool(kospi_above_ma200)

    # Carry-forward signals (PIT-safe)
    out["vkospi_zscore_63d"] = deriv_snap.get("vkospi_zscore_60d", np.nan)
    out["usd_krw_change_20d"] = macro_snap.get("macro_usd_krw_change_20d", np.nan)
    out["foreign_kospi_cumulative_5d"] = flow_snap.get(
        "market_foreign_cumulative_60d", np.nan)   # full window, not 5d (placeholder)
    out["bok_rate_change_60d"] = macro_snap.get("macro_bok_rate_change_60d", np.nan)

    return out


# ===========================================================================
# 4. Diagnostics
# ===========================================================================
def regime_summary(panel_with_regimes: pd.DataFrame) -> dict:
    """Summary stats: regime occurrence frequency over time."""
    if panel_with_regimes.empty or "regime_label_kr" not in panel_with_regimes.columns:
        return {"total": 0}
    counts = panel_with_regimes["regime_label_kr"].value_counts().to_dict()
    return {
        "total_rows": len(panel_with_regimes),
        "regime_distribution": counts,
        "unique_regimes_seen": len(counts),
    }
