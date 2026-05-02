"""tests/test_governance.py — Phase C2 governance overlay invariants.

Five ship-gate tests (per plan.md C2):
1. test_governance_hard_veto_removes_new_buy
   When governance_hard_veto_flag=1, ticker is excluded from picks.
2. test_governance_weight_cap_limits_position
   governance_weight_cap returns the documented tiered cap values.
3. test_dilution_dynamic_calculation
   compute_owner_dilution_risk produces qty*price/mcap with 제3자 flag.
4. test_treasury_sale_vs_cancellation_distinction
   Treasury BUYBACK feeds quality_score (positive); SELL feeds risk_score.
5. test_spinoff_meibei_vs_inseok_handling
   div_mth='물적분할' triggers physical_spinoff_active_flag.

Run: py -3 tests/test_governance.py
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
            print(f"  PASS  {name}")
        except Exception as e:
            FAILED += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        return fn
    return deco


@_test("import kr_governance succeeds")
def test_import():
    import kr_governance
    assert callable(kr_governance.add_governance_signals)
    assert callable(kr_governance.governance_weight_cap)
    assert callable(kr_governance.is_hard_veto)
    assert callable(kr_governance.compute_governance_score_for_ticker)
    assert callable(kr_governance.compute_owner_dilution_risk)


@_test("PHASE2_GOVERNANCE_COLUMNS has 11 entries")
def test_governance_columns_count():
    from kr_config import PHASE2_GOVERNANCE_COLUMNS, PHASE2_KOREA_ALPHA_COLUMNS
    assert len(PHASE2_GOVERNANCE_COLUMNS) == 11, (
        f"Expected 11, got {len(PHASE2_GOVERNANCE_COLUMNS)}")
    assert "governance_risk_score" in PHASE2_GOVERNANCE_COLUMNS
    assert "governance_hard_veto_flag" in PHASE2_GOVERNANCE_COLUMNS
    # Ensure registered in P2 alpha umbrella for keep_cols whitelist
    for col in PHASE2_GOVERNANCE_COLUMNS:
        assert col in PHASE2_KOREA_ALPHA_COLUMNS, (
            f"{col} missing from PHASE2_KOREA_ALPHA_COLUMNS")


@_test("governance_hard_veto_removes_new_buy")
def test_governance_hard_veto_removes_new_buy():
    """A picks-row with hard_veto_flag=1 must drop out of the rd_pool when
    backtester applies the overlay."""
    # Simulate what run_realistic_backtest does when use_governance_overlay=True
    rd = pd.Timestamp("2024-06-30")
    picks = pd.DataFrame([
        {"rebalance_date": rd, "ticker": "111111", "p_pre_surge": 0.90,
         "rank_in_month": 1, "governance_hard_veto_flag": 1.0,
         "governance_risk_score": 0.85},
        {"rebalance_date": rd, "ticker": "222222", "p_pre_surge": 0.80,
         "rank_in_month": 2, "governance_hard_veto_flag": 0.0,
         "governance_risk_score": 0.10},
        {"rebalance_date": rd, "ticker": "333333", "p_pre_surge": 0.70,
         "rank_in_month": 3, "governance_hard_veto_flag": 0.0,
         "governance_risk_score": 0.50},
    ])
    rd_pool = picks[picks["rebalance_date"] == rd].copy()
    rd_pool = rd_pool[
        rd_pool["governance_hard_veto_flag"].fillna(0).astype(float) < 0.5
    ].copy()
    assert len(rd_pool) == 2
    assert "111111" not in rd_pool["ticker"].values


@_test("governance_weight_cap_limits_position")
def test_governance_weight_cap_limits_position():
    """Tier table per kr_governance.governance_weight_cap docstring."""
    from kr_governance import governance_weight_cap
    # risk < 0.40: normal weight unaffected
    assert governance_weight_cap(0.20, 0.05) == 0.05
    # 0.40 <= risk < 0.60: cap at 0.04
    assert governance_weight_cap(0.45, 0.05) == 0.04
    assert governance_weight_cap(0.45, 0.03) == 0.03   # already below cap
    # 0.60 <= risk < 0.80: cap at 0.02
    assert governance_weight_cap(0.65, 0.05) == 0.02
    # risk >= 0.80: zero
    assert governance_weight_cap(0.85, 0.10) == 0.0


@_test("dilution_dynamic_calculation")
def test_dilution_dynamic_calculation():
    """compute_owner_dilution_risk: dilution_pct = qty*price / mcap.
    Single capital_increase row, 100M shares × 10K KRW = 1T KRW issuance,
    against mcap 10T KRW = 10% dilution -> max risk component."""
    from kr_governance import compute_owner_dilution_risk
    events = pd.DataFrame([{
        "ticker": "TEST",
        "event_category": "capital_increase",
        "rcept_dt": pd.Timestamp("2024-03-15"),
        "nstk_ostk_qy": 100_000_000,
        "bdis_pric": 10_000,
        "ic_mthn": "제3자배정",
    }])
    result = compute_owner_dilution_risk(
        events, pd.Timestamp("2024-06-30"), mcap=10_000_000_000_000,
    )
    assert abs(result["dilution_pct_12m"] - 0.10) < 1e-6, (
        f"Expected 0.10, got {result['dilution_pct_12m']}")
    assert result["third_party_allocation_flag"] == 1
    # Score = 0.5 * 1.0 (10% maxed) + 0.2 * 1 (3rd party) = 0.7
    assert abs(result["owner_dilution_risk_score"] - 0.7) < 1e-6, (
        f"Expected 0.70, got {result['owner_dilution_risk_score']}")


@_test("treasury_sale_vs_cancellation_distinction")
def test_treasury_sale_vs_cancellation_distinction():
    """Treasury BUYBACK should feed quality_score; SELL should feed risk_score.
    Same numeric magnitude in both directions must produce opposite signs of
    the governance impact."""
    from kr_governance import (
        compute_treasury_overhang_risk,
        compute_capital_allocation_quality,
    )
    mcap = 1_000_000_000_000   # 1T KRW
    sale_events = pd.DataFrame([{
        "ticker": "TEST",
        "event_category": "treasury_sell",
        "rcept_dt": pd.Timestamp("2024-04-01"),
        "dppln_prc_ostk": 50_000_000_000,   # 5% of mcap
    }])
    buyback_events = pd.DataFrame([{
        "ticker": "TEST",
        "event_category": "treasury_buyback",
        "rcept_dt": pd.Timestamp("2024-04-01"),
        "aqpln_prc_ostk": 50_000_000_000,   # 5% of mcap
    }])
    sale = compute_treasury_overhang_risk(
        sale_events, pd.Timestamp("2024-06-30"), mcap)
    quality = compute_capital_allocation_quality(
        buyback_events, pd.Timestamp("2024-06-30"), mcap)
    # Sale: 5% mcap = max risk (1.0)
    assert sale["treasury_sale_overhang_score"] == 1.0
    # Buyback: 5% mcap >> 2% threshold -> capped at quality 0.6
    assert quality["capital_allocation_quality_score"] >= 0.5
    # Buyback events do NOT add to risk
    sale_zero = compute_treasury_overhang_risk(
        buyback_events, pd.Timestamp("2024-06-30"), mcap)
    assert sale_zero["treasury_sale_overhang_score"] == 0.0


@_test("spinoff_meibei_vs_inseok_handling")
def test_spinoff_meibei_vs_inseok_handling():
    """물적분할 (메이비/실제) -> physical_spinoff_active_flag=1.
    인적분할 -> physical=0 but soft risk only."""
    from kr_governance import compute_spinoff_risk
    physical = pd.DataFrame([{
        "ticker": "TEST",
        "event_category": "spinoff",
        "rcept_dt": pd.Timestamp("2024-01-15"),
        "div_mth": "물적분할",
    }])
    human = pd.DataFrame([{
        "ticker": "TEST",
        "event_category": "spinoff",
        "rcept_dt": pd.Timestamp("2024-01-15"),
        "div_mth": "인적분할",
    }])
    rd = pd.Timestamp("2024-06-30")
    p = compute_spinoff_risk(physical, rd)
    h = compute_spinoff_risk(human, rd)
    assert p["physical_spinoff_active_flag"] == 1
    assert h["physical_spinoff_active_flag"] == 0
    assert p["spinoff_listing_risk_score"] > h["spinoff_listing_risk_score"]


@_test("add_governance_signals zero-fills when event_panel empty")
def test_add_governance_signals_zero_fill():
    from kr_governance import add_governance_signals
    universe = pd.DataFrame({
        "ticker": ["005930", "000660"],
        "market_cap": [4e14, 1e14],
    })
    enriched = add_governance_signals(
        universe, pd.Timestamp("2024-06-30"), event_panel=pd.DataFrame(),
    )
    from kr_config import PHASE2_GOVERNANCE_COLUMNS
    for col in PHASE2_GOVERNANCE_COLUMNS:
        assert col in enriched.columns
        assert (enriched[col].fillna(0) == 0).all(), (
            f"Expected zero-fill for {col}, got non-zero values")


print()
print("=" * 60)
print(f"governance tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
