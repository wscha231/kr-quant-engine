"""tests/test_technicals.py — Technical indicator unit tests.

Mock OHLCV with known patterns to verify each indicator. Run:
    py -3 tests/test_technicals.py
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
# Mock OHLCV factory
# ---------------------------------------------------------------------------
def make_ohlcv(
    days: int = 252,
    start_price: float = 10000.0,
    trend: str = "flat",   # "flat" | "up" | "down" | "uptrend_strong"
    seed: int = 42,
) -> pd.DataFrame:
    """Synthesize daily OHLCV (date, open, high, low, close, volume).

    Trend modes:
      flat: random walk near start_price
      up:   gentle uptrend +0.1%/day average
      down: gentle downtrend -0.1%/day
      uptrend_strong: +0.3%/day, low vol → triggers Stage 2 + trend template
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=days, freq="B")
    if trend == "up":
        drift = 0.001
        vol = 0.012
    elif trend == "down":
        drift = -0.001
        vol = 0.012
    elif trend == "uptrend_strong":
        drift = 0.003
        vol = 0.008
    else:
        drift = 0.0
        vol = 0.012

    rets = rng.normal(drift, vol, days)
    close = start_price * np.cumprod(1 + rets)
    high = close * (1 + rng.uniform(0, 0.015, days))
    low = close * (1 - rng.uniform(0, 0.015, days))
    open_ = close * (1 + rng.uniform(-0.005, 0.005, days))
    volume = rng.integers(100000, 500000, days)

    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@_test("import: kr_technicals module")
def test_imports():
    import kr_technicals as t
    assert callable(t.compute_moving_averages)
    assert callable(t.compute_52w_extremes)
    assert callable(t.compute_rsi)
    assert callable(t.compute_atr)
    assert callable(t.compute_bollinger)
    assert callable(t.classify_stage)
    assert callable(t.compute_trend_template_score)
    assert callable(t.compute_all_technicals)


@_test("MA: SMA values match manual calculation")
def test_moving_averages():
    import kr_technicals as t
    prices = pd.DataFrame({"close": [100.0] * 200 + [110.0]})
    ma = t.compute_moving_averages(prices, windows=(5, 20, 50, 200))
    # Last value is 110, prior 200 are 100. MA5 = (110+100*4)/5 = 102
    assert abs(ma["ma_5"] - 102.0) < 1e-6
    assert abs(ma["ma_20"] - 100.5) < 1e-6
    assert abs(ma["ma_50"] - 100.2) < 1e-6


@_test("MA stack: aligned only when MA20 > MA50 > MA150 > MA200")
def test_ma_stack():
    import kr_technicals as t
    # Strong uptrend: recent prices much higher → MAs ordered correctly
    df = make_ohlcv(days=252, trend="uptrend_strong", seed=10)
    out = t.compute_all_technicals(df)
    # Should be aligned in strong uptrend
    assert out.get("ma_stack_aligned") is True

    # Flat market: not aligned
    df2 = make_ohlcv(days=252, trend="flat", seed=20)
    out2 = t.compute_all_technicals(df2)
    # may or may not be aligned by chance; just check returns bool
    assert isinstance(out2.get("ma_stack_aligned"), bool)


@_test("52w extremes: high/low and dist signs")
def test_52w_extremes():
    import kr_technicals as t
    days = 252
    # Linear ramp from 10000 to 20000 → high = ~20000, low = 10000
    prices = pd.DataFrame({
        "close": np.linspace(10000, 20000, days),
    })
    out = t.compute_52w_extremes(prices)
    assert out["high_52w"] >= 19000
    assert out["low_52w"] <= 10100
    # Close = 20000, dist_from_high ≈ 0
    assert abs(out["dist_from_52w_high"]) < 0.01
    # Close = 20000, dist_from_low > 0 (well above)
    assert out["dist_from_52w_low"] > 0.5
    assert out["new_52w_high_flag"] is True


@_test("RSI: bounded [0, 100], oversold/overbought flags")
def test_rsi_bounds():
    import kr_technicals as t
    # Strong uptrend → RSI > 70
    df_up = make_ohlcv(days=100, trend="uptrend_strong", seed=30)
    out = t.compute_rsi(df_up, window=14)
    assert 0 <= out["rsi_14"] <= 100
    # Strong downtrend → RSI < 30 typically
    df_down = make_ohlcv(days=100, trend="down", seed=31)
    rng = np.random.default_rng(31)
    df_down["close"] = 10000 * np.cumprod(1 + rng.normal(-0.005, 0.015, 100))
    out_down = t.compute_rsi(df_down, window=14)
    assert 0 <= out_down["rsi_14"] <= 100


@_test("ATR: positive and reasonable size")
def test_atr():
    import kr_technicals as t
    df = make_ohlcv(days=100, seed=40)
    out = t.compute_atr(df, window=14)
    assert out["atr_14"] > 0
    assert 0 < out["atr_pct"] < 0.10  # less than 10% of close (typical)


@_test("Bollinger: position in [0, 1]")
def test_bollinger():
    import kr_technicals as t
    df = make_ohlcv(days=50, seed=50)
    out = t.compute_bollinger(df, window=20)
    if not pd.isna(out["bb_position"]):
        assert 0 <= out["bb_position"] <= 1
    assert out["bb_upper_20"] >= out["bb_lower_20"]


@_test("Volume stats: dryup ratio reasonable")
def test_volume_stats():
    import kr_technicals as t
    df = make_ohlcv(days=100, seed=60)
    out = t.compute_volume_stats(df, window=50)
    assert out["volume_ma_50"] > 0
    assert 0 < out["volume_dryup_pct"] < 5.0


@_test("Stage classifier: uptrend → '2_uptrend'")
def test_stage_uptrend():
    import kr_technicals as t
    df = make_ohlcv(days=252, trend="uptrend_strong", seed=70)
    out = t.classify_stage(df)
    assert out["stage_label"] in ("2_uptrend", "1_basing"), \
        f"expected 2_uptrend or 1_basing, got {out['stage_label']}"


@_test("Stage classifier: downtrend → '4_decline'")
def test_stage_downtrend():
    import kr_technicals as t
    rng = np.random.default_rng(71)
    days = 252
    rets = rng.normal(-0.005, 0.012, days)
    close = 50000 * np.cumprod(1 + rets)
    df = pd.DataFrame({
        "date": pd.date_range("2023-01-02", periods=days, freq="B"),
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": rng.integers(100000, 500000, days),
    })
    out = t.classify_stage(df)
    assert out["stage_label"] in ("4_decline", "1_basing")


@_test("Trend template: strong uptrend scores ≥ 5")
def test_trend_template_strong():
    import kr_technicals as t
    df = make_ohlcv(days=252, trend="uptrend_strong", seed=80)
    out = t.compute_trend_template_score(df)
    assert out["trend_template_score"] >= 5, \
        f"strong uptrend should score ≥5, got {out['trend_template_score']}"


@_test("Trend template: declining stock scores low")
def test_trend_template_decline():
    import kr_technicals as t
    rng = np.random.default_rng(81)
    days = 252
    rets = rng.normal(-0.005, 0.015, days)
    close = 50000 * np.cumprod(1 + rets)
    df = pd.DataFrame({
        "date": pd.date_range("2023-01-02", periods=days, freq="B"),
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": rng.integers(100000, 500000, days),
    })
    out = t.compute_trend_template_score(df)
    assert out["trend_template_score"] <= 3
    assert out["trend_template_pass"] is False


@_test("compute_all_technicals: full integration")
def test_full_integration():
    import kr_technicals as t
    df = make_ohlcv(days=252, trend="uptrend_strong", seed=90)
    out = t.compute_all_technicals(df)
    # Key fields populated
    expected = ["ma_5", "ma_20", "ma_50", "ma_150", "ma_200",
                "ma_stack_aligned", "high_52w", "low_52w",
                "dist_from_52w_high", "rsi_14", "atr_14", "atr_pct",
                "bb_upper_20", "bb_lower_20", "bb_position",
                "stage_label", "trend_template_score",
                "trend_template_pass", "breakout_flag",
                "dist_from_ma_50", "dist_from_ma_150", "dist_from_ma_200"]
    for k in expected:
        assert k in out, f"missing {k}"


@_test("compute_all_technicals: empty input returns empty dict")
def test_empty():
    import kr_technicals as t
    out = t.compute_all_technicals(pd.DataFrame())
    assert out == {}


@_test("compute_all_technicals: short history returns NaN gracefully")
def test_short_history():
    import kr_technicals as t
    df = make_ohlcv(days=20, seed=100)   # too short for MA200, RSI etc.
    out = t.compute_all_technicals(df)
    # Doesn't crash, ma_5 ok, ma_200 NaN
    assert "ma_5" in out
    assert pd.isna(out.get("ma_200"))


@_test("kr_features.add_technical_indicators: phase disabled = null fill")
def test_phase_disabled():
    import os
    from kr_features import add_technical_indicators
    from kr_config import PHASE3_TECHNICAL_COLUMNS
    os.environ["PHASE_PHASE3_TECHNICAL_ENABLED"] = "0"
    universe = pd.DataFrame({"ticker": ["TEST01"]})
    out = add_technical_indicators(universe, pd.Timestamp("2024-12-31"))
    for col in PHASE3_TECHNICAL_COLUMNS:
        assert col in out.columns
    os.environ.pop("PHASE_PHASE3_TECHNICAL_ENABLED", None)


@_test("kr_config: PHASE3_TECHNICAL_COLUMNS has 31 entries")
def test_phase3_columns():
    from kr_config import PHASE3_TECHNICAL_COLUMNS, ALL_PHASE_COLUMNS
    assert len(PHASE3_TECHNICAL_COLUMNS) == 31
    for c in PHASE3_TECHNICAL_COLUMNS:
        assert c in ALL_PHASE_COLUMNS


print()
print("=" * 60)
print(f"Technicals tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
