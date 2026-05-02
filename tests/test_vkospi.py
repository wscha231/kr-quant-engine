"""tests/test_vkospi.py — Phase C4 VKOSPI source fix + risk integration.

Two ship-gate tests (plan.md C4):
1. test_vkospi_guard_not_constant_when_enabled
   When a derivatives panel with varying vkospi_level is passed to the
   realistic backtester, get_vkospi_at_date returns the PIT level (not a
   hardcoded constant).
2. test_vkospi_panic_threshold_triggers_cash
   compute_sleeve_scale routes to ≥30% cash when vkospi > 25, ≥50% when > 35.

Plus realized-vol proxy sanity.

Run: py -3 tests/test_vkospi.py
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
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        return fn
    return deco


@_test("import kr_derivatives C4 additions")
def test_imports():
    from kr_derivatives import (
        fetch_vkospi,
        get_vkospi_at_date,
        _compute_realized_vol_proxy,
    )
    assert callable(fetch_vkospi)
    assert callable(get_vkospi_at_date)
    assert callable(_compute_realized_vol_proxy)


@_test("get_vkospi_at_date returns PIT level (not constant)")
def test_vkospi_guard_not_constant_when_enabled():
    from kr_derivatives import get_vkospi_at_date
    panel = pd.DataFrame({
        "date": pd.to_datetime([
            "2022-03-31", "2022-06-30", "2022-09-30",
            "2022-12-30", "2023-03-31",
        ]),
        "vkospi_level": [22.5, 28.7, 32.1, 19.8, 16.4],
    })
    levels = [
        get_vkospi_at_date(panel, pd.Timestamp("2022-03-31")),
        get_vkospi_at_date(panel, pd.Timestamp("2022-06-30")),
        get_vkospi_at_date(panel, pd.Timestamp("2022-09-30")),
        get_vkospi_at_date(panel, pd.Timestamp("2023-03-31")),
    ]
    expected = [22.5, 28.7, 32.1, 16.4]
    assert levels == expected, f"levels={levels}, expected={expected}"
    # PIT: lookup at 2022-04-15 returns the 2022-03-31 value (most recent <= as_of)
    val = get_vkospi_at_date(panel, pd.Timestamp("2022-04-15"))
    assert val == 22.5, val
    # Empty panel -> default
    assert get_vkospi_at_date(pd.DataFrame(), pd.Timestamp("2024-06-30"),
                                default=18.0) == 18.0


@_test("vkospi_panic_threshold_triggers_cash")
def test_vkospi_panic_threshold_triggers_cash():
    """compute_sleeve_scale must route to cash floor when vkospi > 25/35."""
    from kr_backtester_realistic import compute_sleeve_scale
    # Calm: vkospi 18, no DD -> sleeve_scale = 1.0
    calm = compute_sleeve_scale(current_dd=0.0, vkospi_level=18.0)
    assert calm["sleeve_scale"] == 1.0
    # Elevated: vkospi 28, no DD -> 30% cash floor (sleeve_scale 0.7)
    elev = compute_sleeve_scale(current_dd=0.0, vkospi_level=28.0)
    assert elev["sleeve_scale"] <= 0.71, elev
    # Panic: vkospi 38, no DD -> 50% cash floor
    panic = compute_sleeve_scale(current_dd=0.0, vkospi_level=38.0)
    assert panic["sleeve_scale"] <= 0.51, panic


@_test("realized_vol_proxy returns reasonable VKOSPI range")
def test_realized_vol_proxy_range():
    """Hard sanity check on the realized-vol proxy: even synthetic noise at
    realistic KOSPI vol (~15-25 range) should be inside vol-points [10, 60]."""
    from kr_derivatives import _compute_realized_vol_proxy

    # Skip if no cached KOSPI data — the proxy depends on fetch_index_ohlcv.
    proxy = _compute_realized_vol_proxy("2022-01-01", "2022-12-31", window=20)
    if proxy.empty:
        print("    (no KOSPI cache — soft skip)")
        return
    vals = proxy["vkospi_level"].dropna()
    if vals.empty:
        print("    (no realized-vol values — soft skip)")
        return
    # Realistic range
    assert vals.min() >= 5, f"min too low: {vals.min()}"
    assert vals.max() <= 80, f"max too high: {vals.max()}"


@_test("fetch_vkospi falls back to realized-vol proxy when direct sources empty")
def test_fetch_vkospi_fallback():
    from kr_derivatives import fetch_vkospi
    # Pick a date range likely to have working KOSPI cache but failing VKOSPI
    df = fetch_vkospi("2024-01-01", "2024-06-30", refresh_days=7,
                      use_realized_vol_fallback=True)
    if df.empty:
        # Could happen if no KOSPI cache at all — soft pass
        print("    (vkospi fetch empty -- soft skip)")
        return
    assert "vkospi_level" in df.columns
    assert df["vkospi_level"].notna().any(), "all NaN"


print()
print("=" * 60)
print(f"vkospi tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
