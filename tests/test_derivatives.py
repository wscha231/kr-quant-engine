"""tests/test_derivatives.py — P2.6 VKOSPI + foreign futures verification.

Run: py -3 tests/test_derivatives.py
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


def make_mock_derivatives(days=100, seed=42, with_panic=False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=days, freq="D")
    vkospi = 18 + rng.normal(0, 2, days)
    if with_panic:
        # Inject panic spike at end
        vkospi[-5:] = 35
    return pd.DataFrame({
        "date": dates,
        "vkospi_level": vkospi,
        "foreign_futures_net_oi": rng.normal(0, 50000, days).cumsum(),
    })


@_test("import: kr_derivatives module")
def test_imports():
    import kr_derivatives
    assert callable(kr_derivatives.fetch_vkospi)
    assert callable(kr_derivatives.fetch_foreign_futures_oi)
    assert callable(kr_derivatives.add_derived_derivatives_signals)
    assert callable(kr_derivatives.build_derivatives_panel)
    assert callable(kr_derivatives.get_derivatives_snapshot)


@_test("kr_config: PHASE2_DERIVATIVES_COLUMNS = 7")
def test_phase2_deriv_columns():
    from kr_config import PHASE2_DERIVATIVES_COLUMNS, ALL_PHASE_COLUMNS
    assert len(PHASE2_DERIVATIVES_COLUMNS) == 7
    expected = ["vkospi_level", "vkospi_zscore_60d", "vkospi_change_5d",
                "vkospi_above_25", "foreign_futures_net_oi",
                "foreign_futures_net_5d_change", "kospi200_basis_bp"]
    for c in expected:
        assert c in PHASE2_DERIVATIVES_COLUMNS
    for c in PHASE2_DERIVATIVES_COLUMNS:
        assert c in ALL_PHASE_COLUMNS


@_test("derived: vkospi_zscore_60d reflects panic spike")
def test_vkospi_zscore_panic():
    import kr_derivatives as kd
    panel = make_mock_derivatives(days=80, with_panic=True)
    out = kd.add_derived_derivatives_signals(panel)
    assert out["vkospi_zscore_60d"].iloc[-1] > 1.5  # panic spike high z


@_test("derived: vkospi_above_25 flag fires on panic")
def test_vkospi_above_25():
    import kr_derivatives as kd
    panel = make_mock_derivatives(days=80, with_panic=True)
    out = kd.add_derived_derivatives_signals(panel)
    assert out["vkospi_above_25"].iloc[-1] is True or out["vkospi_above_25"].iloc[-1] == True


@_test("derived: vkospi_above_25 false in calm market")
def test_vkospi_calm():
    import kr_derivatives as kd
    panel = make_mock_derivatives(days=80, with_panic=False, seed=10)
    out = kd.add_derived_derivatives_signals(panel)
    assert (out["vkospi_above_25"] == False).all()


@_test("derived: vkospi_change_5d compute correctly")
def test_vkospi_change():
    import kr_derivatives as kd
    panel = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=10, freq="D"),
        "vkospi_level": [20, 21, 22, 23, 24, 25, 26, 27, 28, 30],
    })
    out = kd.add_derived_derivatives_signals(panel)
    # Day 5: 25/20 - 1 = 0.25
    assert abs(out["vkospi_change_5d"].iloc[5] - 0.25) < 1e-6


@_test("derived: foreign_futures_net_5d_change diff")
def test_futures_change():
    import kr_derivatives as kd
    panel = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=10, freq="D"),
        "foreign_futures_net_oi": [100, 200, 300, 400, 500, 700, 800, 900, 1000, 1200],
    })
    out = kd.add_derived_derivatives_signals(panel)
    # Day 5: 700 - 100 = 600
    assert out["foreign_futures_net_5d_change"].iloc[5] == 600


@_test("derived: missing data fills all PHASE2_DERIVATIVES_COLUMNS")
def test_missing_fill():
    import kr_derivatives as kd
    from kr_config import PHASE2_DERIVATIVES_COLUMNS
    panel = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=10, freq="D"),
        # No vkospi_level, no foreign_futures_net_oi
    })
    out = kd.add_derived_derivatives_signals(panel)
    for col in PHASE2_DERIVATIVES_COLUMNS:
        assert col in out.columns


@_test("get_derivatives_snapshot: PIT lookup")
def test_snapshot_pit():
    import kr_derivatives as kd
    panel = make_mock_derivatives(days=60)
    enriched = kd.add_derived_derivatives_signals(panel)
    snap = kd.get_derivatives_snapshot(enriched, pd.Timestamp("2024-02-15"))
    assert "vkospi_level" in snap
    assert pd.notna(snap["vkospi_level"])


@_test("get_derivatives_snapshot: empty returns sentinels")
def test_snapshot_empty():
    import kr_derivatives as kd
    from kr_config import PHASE2_DERIVATIVES_COLUMNS
    snap = kd.get_derivatives_snapshot(pd.DataFrame(), pd.Timestamp("2024-01-01"))
    for col in PHASE2_DERIVATIVES_COLUMNS:
        assert col in snap
        if col == "vkospi_above_25":
            assert snap[col] is False
        else:
            assert pd.isna(snap[col])


@_test("kr_features.add_derivatives_signals: phase disabled")
def test_features_disabled():
    import os
    from kr_features import add_derivatives_signals
    from kr_config import PHASE2_DERIVATIVES_COLUMNS
    os.environ["PHASE_PHASE2_DERIVATIVES_ENABLED"] = "0"
    universe = pd.DataFrame({"ticker": ["A", "B"]})
    out = add_derivatives_signals(universe, pd.Timestamp("2024-12-31"))
    for col in PHASE2_DERIVATIVES_COLUMNS:
        assert col in out.columns
    os.environ.pop("PHASE_PHASE2_DERIVATIVES_ENABLED", None)


@_test("kr_features.add_derivatives_signals: enabled broadcasts")
def test_features_broadcast():
    import os
    import kr_derivatives as kd
    from kr_features import add_derivatives_signals
    os.environ["PHASE_PHASE2_DERIVATIVES_ENABLED"] = "1"
    universe = pd.DataFrame({"ticker": ["A", "B", "C"]})
    panel = make_mock_derivatives(days=60, with_panic=False)
    enriched = kd.add_derived_derivatives_signals(panel)
    out = add_derivatives_signals(universe, pd.Timestamp("2024-02-15"), enriched)
    assert pd.notna(out["vkospi_level"].iloc[0])
    assert out["vkospi_level"].nunique() == 1   # all 3 rows same value
    os.environ.pop("PHASE_PHASE2_DERIVATIVES_ENABLED", None)


print()
print("=" * 60)
print(f"Derivatives tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
