"""tests/test_pmb_oos_quality.py -- P_MB OOS diagnostic helpers.

Run:
    py -3 tests/test_pmb_oos_quality.py
"""
from __future__ import annotations

import sys
from pathlib import Path

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
            print(f"PASS {name}")
        except Exception as e:
            FAILED += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
        return fn
    return deco


@_test("realized holding returns use next-close entry and next-rebalance exit")
def test_add_realized_holding_returns():
    from tools.analyze_pmb_oos_quality import _add_realized_holding_returns

    pmb = pd.DataFrame({
        "rebalance_date": pd.to_datetime(["2024-01-31", "2024-02-29"]),
        "ticker": ["000001", "000001"],
        "p_pre_surge": [0.8, 0.7],
    })
    price_panel = pd.DataFrame({
        "date": pd.to_datetime([
            "2024-01-31",
            "2024-02-01",
            "2024-02-15",
            "2024-03-01",
            "2024-03-15",
        ]),
        "ticker": ["000001"] * 5,
        "close": [95.0, 100.0, 90.0, 120.0, 130.0],
    })

    out = _add_realized_holding_returns(pmb, price_panel)
    first = out.iloc[0]
    second = out.iloc[1]
    assert first["realized_entry_date"] == pd.Timestamp("2024-02-01")
    assert first["realized_exit_date"] == pd.Timestamp("2024-03-01")
    assert abs(first["realized_holding_return"] - 0.20) < 1e-9
    assert abs(first["realized_min_return"] - (-0.10)) < 1e-9
    assert pd.isna(second.get("realized_holding_return"))


@_test("analysis source prefers realized returns over sparse forward labels")
def test_analysis_source_prefers_realized_returns():
    from tools.analyze_pmb_oos_quality import _choose_analysis_return_source

    rows = pd.DataFrame({
        "realized_holding_return": [0.25, None],
        "realized_min_return": [-0.05, None],
        "forward_return_1m": [-0.50, -0.20],
        "forward_min_return_1m": [-0.60, -0.30],
    })

    out = _choose_analysis_return_source(rows)
    assert out["analysis_return_source"].eq("realized_next_rebalance").all()
    assert abs(float(out["analysis_return"].iloc[0]) - 0.25) < 1e-9
    assert pd.isna(out["analysis_return"].iloc[1])


@_test("analysis source falls back to forward labels when realized returns are absent")
def test_analysis_source_falls_back_to_forward_labels():
    from tools.analyze_pmb_oos_quality import _choose_analysis_return_source

    rows = pd.DataFrame({
        "forward_return_1m": [0.10, -0.20],
        "forward_min_return_1m": [-0.05, -0.30],
    })

    out = _choose_analysis_return_source(rows)
    assert out["analysis_return_source"].eq("forward_label_1m").all()
    assert abs(float(out["analysis_return"].iloc[0]) - 0.10) < 1e-9
    assert abs(float(out["analysis_min_return"].iloc[1]) - (-0.30)) < 1e-9


if __name__ == "__main__":
    print("=" * 60)
    print("P_MB OOS quality tests")
    print("=" * 60)
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
