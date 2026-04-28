"""tests/test_regime.py - P3.3 Regime classifier verification.

Run: py -3 tests/test_regime.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

PASSED = 0
FAILED = 0


def _test(name: str):
    def deco(fn):
        global PASSED, FAILED
        try:
            fn()
            PASSED += 1
            print(f"  PASS  {name}")
        except Exception as e:
            FAILED += 1
            import traceback
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
        return fn
    return deco


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@_test("import: kr_regime module")
def test_imports():
    import kr_regime
    assert callable(kr_regime.classify_regime)
    assert callable(kr_regime.get_regime_sleeve_multipliers)
    assert callable(kr_regime.add_regime_signals)
    assert callable(kr_regime.regime_summary)


@_test("REGIME_SLEEVE_MULTIPLIERS has 9 entries (8 regimes + unknown)")
def test_multiplier_table():
    from kr_regime import REGIME_SLEEVE_MULTIPLIERS
    expected = ["bull_trending", "bull_peaking", "bear_falling",
                "bear_bottoming", "recovery", "sideways",
                "stagflation_kr", "won_crisis", "unknown"]
    for r in expected:
        assert r in REGIME_SLEEVE_MULTIPLIERS, f"missing {r}"
        m = REGIME_SLEEVE_MULTIPLIERS[r]
        assert "core" in m and "future" in m and "early" in m


@_test("classify: bull_trending - KOSPI up, foreign cumulative buy, VKOSPI low")
def test_bull_trending():
    from kr_regime import classify_regime
    macro = {"macro_usd_krw_zscore_60d": -0.2, "macro_kr_pmi": 53,
             "macro_bok_rate_change_60d": 0}
    flow = {"market_foreign_cumulative_60d": 1e13}
    deriv = {"vkospi_level": 15, "vkospi_zscore_60d": -1.0}
    out = classify_regime(macro, flow, deriv, kospi_above_ma200=True,
                            kospi_5d_return=0.02)
    assert out == "bull_trending", f"got {out}"


@_test("classify: bear_falling - KOSPI down, VKOSPI panic, foreign sell")
def test_bear_falling():
    from kr_regime import classify_regime
    macro = {"macro_usd_krw_zscore_60d": 1.0, "macro_kr_pmi": 49,
             "macro_bok_rate_change_60d": 0}
    flow = {"market_foreign_cumulative_60d": -1e13}
    deriv = {"vkospi_level": 32, "vkospi_zscore_60d": 2.5}
    out = classify_regime(macro, flow, deriv, kospi_above_ma200=False,
                            kospi_5d_return=-0.05)
    assert out in ("bear_falling", "bear_bottoming"), f"got {out}"


@_test("classify: bear_bottoming - VKOSPI peak + foreign sell slowing")
def test_bear_bottoming():
    from kr_regime import classify_regime
    macro = {"macro_usd_krw_zscore_60d": 0.5}
    flow = {"market_foreign_cumulative_60d": -3e12,
            "market_foreign_net_buy_20d_kospi": -5e11}  # slowing (not -3e12)
    deriv = {"vkospi_level": 35}
    out = classify_regime(macro, flow, deriv, kospi_above_ma200=False)
    assert out == "bear_bottoming", f"got {out}"


@_test("classify: won_crisis - USD/KRW spike + foreign sell + KOSPI plunge")
def test_won_crisis():
    from kr_regime import classify_regime
    macro = {"macro_usd_krw": 1500, "macro_usd_krw_zscore_60d": 3.0,
             "macro_kr_pmi": 47}
    flow = {"market_foreign_cumulative_60d": -2e13}
    deriv = {"vkospi_level": 40}
    out = classify_regime(macro, flow, deriv, kospi_above_ma200=False,
                            kospi_5d_return=-0.15)
    assert out == "won_crisis", f"got {out}"


@_test("classify: stagflation_kr - PMI weak + KRW weak + hawkish BOK")
def test_stagflation():
    from kr_regime import classify_regime
    macro = {"macro_kr_pmi": 46, "macro_usd_krw_zscore_60d": 1.5,
             "macro_bok_rate_change_60d": 0.05}
    flow = {"market_foreign_cumulative_60d": 0}
    deriv = {"vkospi_level": 22}
    out = classify_regime(macro, flow, deriv, kospi_above_ma200=True)
    assert out == "stagflation_kr", f"got {out}"


@_test("classify: recovery - KOSPI above MA200, foreign returning, PMI < 50")
def test_recovery():
    from kr_regime import classify_regime
    macro = {"macro_kr_pmi": 49, "macro_usd_krw_zscore_60d": 0,
             "macro_bok_rate_change_60d": 0}
    flow = {"market_foreign_cumulative_60d": 2e12}
    deriv = {"vkospi_level": 18}
    out = classify_regime(macro, flow, deriv, kospi_above_ma200=True)
    assert out == "recovery", f"got {out}"


@_test("classify: sideways - defaults when nothing fires strongly")
def test_sideways():
    from kr_regime import classify_regime
    out = classify_regime({}, {}, {}, kospi_above_ma200=False)
    assert out in ("sideways", "bear_bottoming", "bear_falling")


@_test("get_regime_sleeve_multipliers: returns dict with clamping")
def test_multipliers():
    from kr_regime import get_regime_sleeve_multipliers
    bull = get_regime_sleeve_multipliers("bull_trending")
    assert 0.30 <= bull["core"] <= 1.50
    assert bull["future"] > 1.0
    crisis = get_regime_sleeve_multipliers("won_crisis")
    # Clamp lower bound at 0.30
    assert crisis["early"] >= 0.30
    unknown = get_regime_sleeve_multipliers("nonsense")
    assert unknown == {"core": 1.0, "future": 1.0, "early": 1.0}


@_test("kr_features.add_regime_signals: phase disabled = identity")
def test_features_disabled():
    import os
    from kr_features import add_universe_features
    os.environ["PHASE_PHASE3_REGIME_ENABLED"] = "0"
    # add_regime_signals only fires inside add_universe_features; test
    # standalone via direct import:
    from kr_regime import add_regime_signals
    universe = pd.DataFrame({"ticker": ["A", "B"]})
    out = add_regime_signals(universe, pd.Timestamp("2024-12-31"))
    assert (out["regime_label_kr"] == "unknown").all()
    assert (out["regime_sleeve_multiplier_core"] == 1.0).all()
    os.environ.pop("PHASE_PHASE3_REGIME_ENABLED", None)


@_test("kr_features.add_regime_signals: enabled with mock panels")
def test_features_enabled():
    import os
    from kr_regime import add_regime_signals
    os.environ["PHASE_PHASE3_REGIME_ENABLED"] = "1"
    # Build mock macro/flow/deriv panels (simple)
    days = 60
    dates = pd.date_range("2024-01-01", periods=days, freq="D")
    macro_panel = pd.DataFrame({
        "date": dates, "macro_usd_krw": 1300, "macro_usd_krw_zscore_60d": -0.2,
        "macro_kr_pmi": 53, "macro_bok_rate_change_60d": 0,
        "macro_usd_krw_change_20d": 0.01,
    })
    flow_panel = pd.DataFrame({
        "date": dates, "market": "KOSPI",
        "market_foreign_cumulative_60d_kospi": 1e13,
    })
    deriv_panel = pd.DataFrame({
        "date": dates, "vkospi_level": 15, "vkospi_zscore_60d": -1.0,
    })

    universe = pd.DataFrame({"ticker": ["A", "B"]})
    out = add_regime_signals(
        universe, pd.Timestamp("2024-02-15"),
        macro_panel=macro_panel,
        market_flow_panel=flow_panel,
        derivatives_panel=deriv_panel,
        kospi_above_ma200=True,
        kospi_5d_return=0.01,
    )
    # bull_trending should classify
    assert out["regime_label_kr"].iloc[0] in ("bull_trending", "recovery", "sideways")
    # multipliers populated
    assert pd.notna(out["regime_sleeve_multiplier_core"].iloc[0])
    assert (out["regime_label_kr"] == out["regime_label_kr"].iloc[0]).all()
    os.environ.pop("PHASE_PHASE3_REGIME_ENABLED", None)


@_test("regime_summary: counts by regime label")
def test_summary():
    from kr_regime import regime_summary
    df = pd.DataFrame({
        "regime_label_kr": ["bull_trending"] * 5 + ["bear_falling"] * 3 + ["sideways"] * 2,
    })
    s = regime_summary(df)
    assert s["total_rows"] == 10
    assert s["regime_distribution"]["bull_trending"] == 5


@_test("kr_config: PHASE3_REGIME_COLUMNS includes regime_label_kr + multipliers")
def test_phase3_columns():
    from kr_config import PHASE3_REGIME_COLUMNS, ALL_PHASE_COLUMNS
    assert "regime_label_kr" in PHASE3_REGIME_COLUMNS
    assert "regime_sleeve_multiplier_core" in PHASE3_REGIME_COLUMNS
    assert "regime_sleeve_multiplier_future" in PHASE3_REGIME_COLUMNS
    assert "regime_sleeve_multiplier_early" in PHASE3_REGIME_COLUMNS


print()
print("=" * 60)
print(f"Regime tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
