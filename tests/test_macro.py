"""tests/test_macro.py — P3.2 Macro panel verification.

Mock data + derived signal logic + PIT snapshot retrieval.

Run: py -3 tests/test_macro.py
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
# Mock raw macro panel (date + a few series)
# ---------------------------------------------------------------------------
def make_mock_macro(days: int = 365) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=days, freq="D")
    return pd.DataFrame({
        "date": dates,
        "bok_base_rate": np.full(days, 3.5),
        "ktb_3y_yield": 3.0 + rng.normal(0, 0.05, days),
        "ktb_10y_yield": 3.5 + rng.normal(0, 0.05, days),
        "usd_krw": 1300 + rng.normal(0, 20, days).cumsum() / 5,
        "kr_pmi": 50 + rng.normal(0, 1.5, days),
        "consumer_sentiment": 95 + rng.normal(0, 3, days),
        "business_sentiment": 80 + rng.normal(0, 4, days),
        "kr_industrial_prod": 100 + rng.normal(0, 2, days),
        "kr_export_yoy": 5 + rng.normal(0, 5, days),
        "us_10y": 4.0 + rng.normal(0, 0.1, days),
        "us_2y": 4.5 + rng.normal(0, 0.1, days),
        "vix": 15 + rng.normal(0, 3, days).clip(0),
        "dxy": 105 + rng.normal(0, 2, days),
        "wti_close": 75 + rng.normal(0, 5, days),
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@_test("import: kr_macro module")
def test_imports():
    import kr_macro
    assert callable(kr_macro.build_macro_panel)
    assert callable(kr_macro.load_or_build_macro_panel)
    assert callable(kr_macro.get_macro_snapshot)
    assert callable(kr_macro.add_derived_macro_signals)
    assert callable(kr_macro._zscore_rolling)


@_test("kr_config: PHASE3_MACRO_COLUMNS = 23")
def test_phase3_macro_columns():
    from kr_config import PHASE3_MACRO_COLUMNS, ALL_PHASE_COLUMNS
    assert len(PHASE3_MACRO_COLUMNS) == 23
    expected = ["macro_bok_base_rate", "macro_usd_krw_zscore_60d",
                "macro_kr_pmi_diffusion", "macro_export_yoy_3m_avg",
                "macro_us_10y_2y_spread", "macro_dxy_zscore_60d",
                "macro_vix", "macro_wti_zscore_60d"]
    for col in expected:
        assert col in PHASE3_MACRO_COLUMNS, f"missing {col}"
    for col in PHASE3_MACRO_COLUMNS:
        assert col in ALL_PHASE_COLUMNS


@_test("derived: bok_rate_change_60d compute correctly")
def test_bok_rate_change():
    from kr_macro import add_derived_macro_signals
    days = 100
    panel = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=days, freq="D"),
        "bok_base_rate": [3.0] * 60 + [3.5] * 40,  # +50bp jump at day 60
    })
    out = add_derived_macro_signals(panel)
    # Day 70: rate is 3.5, rate 60d ago was 3.0 → 16.7% change
    assert "macro_bok_rate_change_60d" in out.columns
    val = out["macro_bok_rate_change_60d"].iloc[70]
    assert abs(val - (3.5 / 3.0 - 1.0)) < 0.01


@_test("derived: yield curve spread (US 10y-2y)")
def test_yield_spread():
    from kr_macro import add_derived_macro_signals
    panel = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=10, freq="D"),
        "us_10y": [4.0] * 10,
        "us_2y": [4.5] * 10,
    })
    out = add_derived_macro_signals(panel)
    assert "macro_us_10y_2y_spread" in out.columns
    assert (out["macro_us_10y_2y_spread"] == -0.5).all()  # inverted


@_test("derived: USD/KRW zscore reflects recent abnormality")
def test_usdkrw_zscore():
    from kr_macro import add_derived_macro_signals
    days = 100
    panel = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=days, freq="D"),
        "usd_krw": [1300.0] * 80 + [1500.0] * 20,  # jump higher
    })
    out = add_derived_macro_signals(panel)
    # Last day: USD/KRW = 1500, zscore should be very high
    last_z = out["macro_usd_krw_zscore_60d"].iloc[-1]
    assert last_z > 1.0  # spike


@_test("derived: PMI diffusion = PMI - 50")
def test_pmi_diffusion():
    from kr_macro import add_derived_macro_signals
    panel = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=10, freq="D"),
        "kr_pmi": [48, 49, 50, 51, 52, 53, 54, 50, 49, 50],
    })
    out = add_derived_macro_signals(panel)
    expected = [-2, -1, 0, 1, 2, 3, 4, 0, -1, 0]
    for i, exp in enumerate(expected):
        assert abs(out["macro_kr_pmi_diffusion"].iloc[i] - exp) < 1e-9


@_test("derived: export YoY 3m avg smoothing")
def test_export_yoy_avg():
    from kr_macro import add_derived_macro_signals
    panel = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=10, freq="D"),
        "kr_export_yoy": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    })
    out = add_derived_macro_signals(panel)
    # Day 3: avg of 10, 20, 30 = 20
    assert abs(out["macro_export_yoy_3m_avg"].iloc[2] - 20.0) < 1e-9
    # Day 6: avg of 50, 60, 70 = 60
    assert abs(out["macro_export_yoy_3m_avg"].iloc[5] - 50.0) < 1e-9


@_test("get_macro_snapshot: PIT lookup at-or-before as_of")
def test_macro_snapshot():
    from kr_macro import add_derived_macro_signals, get_macro_snapshot
    panel = make_mock_macro(60)
    enriched = add_derived_macro_signals(panel)
    snap = get_macro_snapshot(enriched, pd.Timestamp("2024-02-15"))
    assert "macro_bok_base_rate" in snap
    assert pd.notna(snap["macro_bok_base_rate"])
    # Future date: no data → should still return all keys (NaN)
    snap_future = get_macro_snapshot(enriched, pd.Timestamp("2030-01-01"))
    assert all(k in snap_future for k in ["macro_bok_base_rate", "macro_us_10y", "macro_vix"])


@_test("get_macro_snapshot: empty panel returns all-NaN dict")
def test_macro_snapshot_empty():
    from kr_macro import get_macro_snapshot
    from kr_config import PHASE3_MACRO_COLUMNS
    snap = get_macro_snapshot(pd.DataFrame(), pd.Timestamp("2024-01-01"))
    for col in PHASE3_MACRO_COLUMNS:
        assert col in snap
        assert pd.isna(snap[col])


@_test("get_macro_snapshot: date before panel start = all NaN")
def test_macro_snapshot_before_start():
    from kr_macro import add_derived_macro_signals, get_macro_snapshot
    panel = make_mock_macro(30)   # 2024-01-01 ~
    enriched = add_derived_macro_signals(panel)
    snap = get_macro_snapshot(enriched, pd.Timestamp("2020-01-01"))
    # All NaN since date < panel start
    assert pd.isna(snap.get("macro_bok_base_rate"))


@_test("add_derived: missing source columns => fill NaN gracefully")
def test_missing_source():
    from kr_macro import add_derived_macro_signals
    from kr_config import PHASE3_MACRO_COLUMNS
    minimal = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=10, freq="D"),
        "bok_base_rate": [3.5] * 10,
    })
    out = add_derived_macro_signals(minimal)
    # All PHASE3_MACRO_COLUMNS exist, missing ones NaN
    for col in PHASE3_MACRO_COLUMNS:
        assert col in out.columns


@_test("kr_features.add_macro_signals: phase disabled = NaN fill")
def test_features_phase_disabled():
    import os
    from kr_features import add_macro_signals
    from kr_config import PHASE3_MACRO_COLUMNS
    os.environ["PHASE_PHASE3_MACRO_ENABLED"] = "0"
    universe = pd.DataFrame({"ticker": ["TEST01", "TEST02"]})
    out = add_macro_signals(universe, pd.Timestamp("2024-12-31"),
                              macro_panel=pd.DataFrame())
    for col in PHASE3_MACRO_COLUMNS:
        assert col in out.columns
        assert out[col].isna().all()
    os.environ.pop("PHASE_PHASE3_MACRO_ENABLED", None)


@_test("kr_features.add_macro_signals: phase enabled broadcasts to all rows")
def test_features_broadcast():
    import os
    from kr_features import add_macro_signals
    from kr_macro import add_derived_macro_signals
    os.environ["PHASE_PHASE3_MACRO_ENABLED"] = "1"
    universe = pd.DataFrame({"ticker": ["A", "B", "C"]})
    raw_panel = make_mock_macro(60)
    panel = add_derived_macro_signals(raw_panel)
    out = add_macro_signals(universe, pd.Timestamp("2024-02-15"), macro_panel=panel)
    # All rows should have the same macro values
    assert out["macro_bok_base_rate"].nunique() == 1
    assert pd.notna(out["macro_bok_base_rate"].iloc[0])
    assert out["macro_us_10y"].nunique() == 1
    os.environ.pop("PHASE_PHASE3_MACRO_ENABLED", None)


@_test("kr_features.add_macro_signals: uses PIT publication lag")
def test_features_macro_pit_lag():
    import os
    from kr_features import add_macro_signals

    os.environ["PHASE_PHASE3_MACRO_ENABLED"] = "1"
    universe = pd.DataFrame({"ticker": ["A", "B"]})
    panel = pd.DataFrame({
        "date": pd.to_datetime(["2024-06-30", "2024-07-01"]),
        "macro_kr_pmi": [52.5, np.nan],
    })

    # PMI has a 1-day publication lag. At 2024-06-30 it must not be visible.
    out_same_day = add_macro_signals(universe, pd.Timestamp("2024-06-30"), macro_panel=panel)
    assert out_same_day["macro_kr_pmi"].isna().all()

    out_after_lag = add_macro_signals(universe, pd.Timestamp("2024-07-01"), macro_panel=panel)
    assert (out_after_lag["macro_kr_pmi"] == 52.5).all()
    os.environ.pop("PHASE_PHASE3_MACRO_ENABLED", None)


print()
print("=" * 60)
print(f"Macro tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
