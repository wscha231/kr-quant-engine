"""tests/test_p2_events.py — P2 DART event scoring v2 verification.

Mock event panels with controlled magnitudes to verify:
- KRW-amount-based scoring (not count-based)
- Per-event-category scoring modes (amount_pct_mcap, computed_dilution,
  binary, insider_net_buy, stkrt_change)
- PIT filter (rcept_dt <= as_of)
- Mcap normalization (large-cap event = small score, small-cap event = full)
- Insider count noise immunity (Samsung 2,614 row case)
- compute_p2_score blending

Run: py -3 tests/test_p2_events.py
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
# Mock event panel factory
# ---------------------------------------------------------------------------
def make_event(
    category: str,
    rcept_dt: pd.Timestamp,
    ticker: str = "TEST01",
    **fields,
) -> dict:
    """Create one event row dict."""
    base = {
        "rcept_dt": rcept_dt,
        "rcept_no": rcept_dt.strftime("%Y%m%d") + "000001",
        "event_category": category,
        "ticker": ticker,
        "corp_code": "00000001",
    }
    base.update(fields)
    return base


def make_panel(events: list[dict]) -> pd.DataFrame:
    """Create event panel from list of event dicts."""
    if not events:
        return pd.DataFrame()
    return pd.DataFrame(events)


# ---------------------------------------------------------------------------
# Tests — score helpers
# ---------------------------------------------------------------------------

@_test("import: kr_dart_client + kr_features P2")
def test_imports():
    from kr_dart_client import (
        DART_EVENT_CATALOG,
        compute_event_score_for_corp,
        _score_amount_pct_mcap,
        _score_computed_dilution,
        _score_binary,
        _score_insider_net_buy,
        _score_stkrt_change,
    )
    from kr_features import (
        prepare_event_panel,
        add_disclosure_event_signal,
        compute_p2_score,
    )
    assert "treasury_buyback" in DART_EVENT_CATALOG
    meta = DART_EVENT_CATALOG["treasury_buyback"]
    assert meta["scoring_mode"] == "amount_pct_mcap"
    assert meta["alpha_weight"] == 0.50
    assert meta["full_weight_pct"] == 0.02


@_test("score: treasury_buyback 2% mcap = full +0.50")
def test_buyback_full_weight():
    from kr_dart_client import _score_amount_pct_mcap
    panel = pd.DataFrame([
        {"aqpln_prc_ostk": 2e10},  # 200억
    ])
    mcap = 1e12  # 1조 → 200억 / 1조 = 2%
    score = _score_amount_pct_mcap(panel, mcap, "aqpln_prc_ostk", weight=0.50, full_pct=0.02)
    assert abs(score - 0.50) < 1e-6, f"expected ~0.50, got {score}"


@_test("score: treasury_buyback 0.5% mcap = quarter weight")
def test_buyback_partial_weight():
    from kr_dart_client import _score_amount_pct_mcap
    panel = pd.DataFrame([{"aqpln_prc_ostk": 5e9}])  # 50억
    mcap = 1e12  # 50억/1조 = 0.5% → 0.5/2 = 0.25 magnitude
    score = _score_amount_pct_mcap(panel, mcap, "aqpln_prc_ostk", weight=0.50, full_pct=0.02)
    assert abs(score - 0.125) < 1e-6, f"expected 0.125, got {score}"


@_test("score: amount > full_pct caps at full weight (no over-weighting)")
def test_buyback_cap():
    from kr_dart_client import _score_amount_pct_mcap
    panel = pd.DataFrame([{"aqpln_prc_ostk": 1e11}])  # 1,000억
    mcap = 1e12  # 10% → way over 2% threshold, should cap at 0.50
    score = _score_amount_pct_mcap(panel, mcap, "aqpln_prc_ostk", weight=0.50, full_pct=0.02)
    assert abs(score - 0.50) < 1e-6, f"capped at 0.50, got {score}"


@_test("score: empty events / zero mcap returns 0")
def test_empty_events():
    from kr_dart_client import _score_amount_pct_mcap, _score_binary
    assert _score_amount_pct_mcap(pd.DataFrame(), 1e12, "x", 0.5, 0.02) == 0.0
    assert _score_amount_pct_mcap(pd.DataFrame([{"x": 1}]), 0, "x", 0.5, 0.02) == 0.0
    assert _score_binary(pd.DataFrame(), 0.30) == 0.0


@_test("score: binary fires once regardless of count")
def test_binary_no_stack():
    from kr_dart_client import _score_binary
    one_event = pd.DataFrame([{"x": 1}])
    five_events = pd.DataFrame([{"x": i} for i in range(5)])
    assert _score_binary(one_event, 0.30) == 0.30
    assert _score_binary(five_events, 0.30) == 0.30  # NOT 1.50


@_test("score: capital_increase dilution (qty × price / mcap)")
def test_capital_increase():
    from kr_dart_client import _score_computed_dilution
    panel = pd.DataFrame([
        {"nstk_ostk_qy": 1_000_000, "bdis_pric": 50_000},  # 500억 발행
    ])
    mcap = 1e12  # 1조 → 5% dilution = full -0.40
    score = _score_computed_dilution(panel, mcap, weight=-0.40, full_pct=0.05)
    assert abs(score - (-0.40)) < 1e-6, f"expected -0.40, got {score}"


@_test("score: insider net buy signed by delta_qty × trade_uv")
def test_insider_net_buy_positive():
    from kr_dart_client import _score_insider_net_buy
    panel = pd.DataFrame([
        # Insider 1 buys 1,000 @ 10,000 = 10M
        {"bsis_stkqy": 0, "aft_stkqy": 1000, "trade_uv": 10000},
        # Insider 2 buys 4,000 @ 10,000 = 40M
        {"bsis_stkqy": 0, "aft_stkqy": 4000, "trade_uv": 10000},
    ])
    # net buy = 50M, mcap = 10B → 0.5% = full +0.30
    score = _score_insider_net_buy(panel, mcap=10e9, weight=0.30, full_pct=0.005)
    assert abs(score - 0.30) < 1e-6, f"expected +0.30, got {score}"


@_test("score: insider net SELL gives negative score")
def test_insider_net_sell():
    from kr_dart_client import _score_insider_net_buy
    panel = pd.DataFrame([
        {"bsis_stkqy": 5000, "aft_stkqy": 0, "trade_uv": 10000},  # sell 50M
    ])
    score = _score_insider_net_buy(panel, mcap=10e9, weight=0.30, full_pct=0.005)
    assert score < 0
    assert abs(score - (-0.30)) < 1e-6, f"expected -0.30, got {score}"


@_test("score: insider count noise immunity (Samsung 2,614 case)")
def test_insider_count_immunity():
    """If 2,614 insiders all have delta_qty ≈ 0 (just reporting), score ≈ 0.

    This is the regression test for the Samsung 2024 +43.10 bug."""
    from kr_dart_client import _score_insider_net_buy
    n = 2614
    panel = pd.DataFrame([
        # Each row: very small delta (admin reporting, not real buy/sell)
        {"bsis_stkqy": 100 + i, "aft_stkqy": 100 + i + np.random.choice([-1, 0, 1]),
         "trade_uv": 50000}
        for i in range(n)
    ])
    score = _score_insider_net_buy(panel, mcap=6e14, weight=0.30, full_pct=0.005)
    # Small random net delta ÷ 600조 mcap should be tiny
    assert abs(score) < 0.05, f"expected near-zero, got {score} (regression!)"


@_test("score: stkrt_change sums signed % changes")
def test_stkrt_change():
    from kr_dart_client import _score_stkrt_change
    panel = pd.DataFrame([
        {"stkrt_irds": 0.5},   # +0.5% holding increase
        {"stkrt_irds": 0.3},   # +0.3% increase
        {"stkrt_irds": -0.2},  # -0.2% decrease
    ])
    # net = +0.6%, threshold 1.0% → 0.6 magnitude × 0.25 = 0.15
    score = _score_stkrt_change(panel, weight=0.25)
    assert abs(score - 0.15) < 1e-6, f"expected 0.15, got {score}"


@_test("compute_event_score_for_corp: full integration")
def test_full_corp_score():
    from kr_dart_client import compute_event_score_for_corp
    events = pd.DataFrame([
        # Buyback 1% mcap → +0.25 (half of 0.50 weight)
        {"event_category": "treasury_buyback",
         "rcept_dt": pd.Timestamp("2024-06-15"),
         "aqpln_prc_ostk": 1e10},
        # Insider buy 0.5% mcap → +0.30 full
        {"event_category": "insider_holdings",
         "rcept_dt": pd.Timestamp("2024-08-01"),
         "bsis_stkqy": 0, "aft_stkqy": 5000, "trade_uv": 10000},
        # Bonus issue → binary +0.30
        {"event_category": "bonus_issue",
         "rcept_dt": pd.Timestamp("2024-09-01")},
    ])
    scores = compute_event_score_for_corp(
        events, as_of=pd.Timestamp("2024-12-31"),
        lookback_days=365, mcap=1e12,
    )
    assert "total_score" in scores
    assert scores["treasury_buyback"] > 0
    assert scores["insider_holdings"] > 0
    assert scores["bonus_issue"] == 0.30   # binary
    assert scores["total_score"] > 0.50
    assert scores["capital_increase"] == 0   # not in events


@_test("compute_event_score_for_corp: PIT filter respects rcept_dt")
def test_pit_filter():
    from kr_dart_client import compute_event_score_for_corp
    events = pd.DataFrame([
        # Future event — should be excluded
        {"event_category": "treasury_buyback",
         "rcept_dt": pd.Timestamp("2025-01-15"),
         "aqpln_prc_ostk": 1e11},
    ])
    scores = compute_event_score_for_corp(
        events, as_of=pd.Timestamp("2024-12-31"),
        lookback_days=365, mcap=1e12,
    )
    assert scores["total_score"] == 0.0
    assert scores["treasury_buyback"] == 0.0


@_test("compute_event_score_for_corp: lookback window excludes old events")
def test_lookback_window():
    from kr_dart_client import compute_event_score_for_corp
    events = pd.DataFrame([
        # 6 months ago — within 90d? NO
        {"event_category": "treasury_buyback",
         "rcept_dt": pd.Timestamp("2024-06-01"),
         "aqpln_prc_ostk": 1e11},
    ])
    scores = compute_event_score_for_corp(
        events, as_of=pd.Timestamp("2024-12-31"),
        lookback_days=90, mcap=1e12,
    )
    assert scores["treasury_buyback"] == 0.0


@_test("add_disclosure_event_signal: zero-fill when phase disabled")
def test_phase_disabled_zero_fill():
    import os
    from kr_features import add_disclosure_event_signal
    from kr_config import PHASE2_DART_EVENT_COLUMNS
    os.environ["PHASE_PHASE2_DART_EVENTS_ENABLED"] = "0"
    universe = pd.DataFrame({
        "ticker": ["TEST01", "TEST02"],
        "market_cap": [1e12, 5e11],
    })
    out = add_disclosure_event_signal(
        universe, pd.Timestamp("2024-12-31"),
        event_panel=pd.DataFrame(),
    )
    for col in PHASE2_DART_EVENT_COLUMNS:
        assert col in out.columns
        assert (out[col] == 0.0).all()
    os.environ.pop("PHASE_PHASE2_DART_EVENTS_ENABLED", None)


@_test("add_disclosure_event_signal: PIT join with mcap normalization")
def test_pit_join_mcap_norm():
    import os
    from kr_features import add_disclosure_event_signal
    os.environ["PHASE_PHASE2_DART_EVENTS_ENABLED"] = "1"

    universe = pd.DataFrame({
        "ticker": ["BIG", "SMALL"],
        "market_cap": [1e13, 1e11],   # 10조 vs 1,000억
    })
    # Both tickers have same buyback amount (10억)
    panel = pd.DataFrame([
        {"event_category": "treasury_buyback",
         "rcept_dt": pd.Timestamp("2024-12-01"),
         "ticker": "BIG", "aqpln_prc_ostk": 1e9},
        {"event_category": "treasury_buyback",
         "rcept_dt": pd.Timestamp("2024-12-01"),
         "ticker": "SMALL", "aqpln_prc_ostk": 1e9},
    ])
    out = add_disclosure_event_signal(
        universe, pd.Timestamp("2024-12-31"),
        event_panel=panel,
    )
    big_score = out[out["ticker"] == "BIG"]["event_treasury_buyback_score"].iloc[0]
    small_score = out[out["ticker"] == "SMALL"]["event_treasury_buyback_score"].iloc[0]
    assert small_score > big_score, \
        f"small-cap should score higher: BIG={big_score}, SMALL={small_score}"
    os.environ.pop("PHASE_PHASE2_DART_EVENTS_ENABLED", None)


@_test("compute_p2_score: blends P0 + P1 + P2 with proper weights")
def test_p2_blended_score():
    from kr_features import compute_p2_score
    universe = pd.DataFrame({
        "ticker": ["A", "B"],
        "p0_momentum_score": [1.0, 0.0],
        "value_score": [0.5, 0.5],
        "quality_score": [0.5, 0.5],
        "turnaround_score": [0.0, 0.0],
        "disclosure_event_total_score": [0.5, 0.0],
    })
    out = compute_p2_score(universe)
    assert "p2_blended_score" in out.columns
    # A: stronger momentum + events → higher score
    assert out.iloc[0]["p2_blended_score"] > out.iloc[1]["p2_blended_score"]


@_test("kr_config: PHASE2_DART_EVENT_COLUMNS has 12 entries")
def test_phase2_columns_count():
    from kr_config import PHASE2_DART_EVENT_COLUMNS, PHASE2_KOREA_ALPHA_COLUMNS
    assert len(PHASE2_DART_EVENT_COLUMNS) == 12
    # Must include total_score + 11 individual event types
    assert "disclosure_event_total_score" in PHASE2_DART_EVENT_COLUMNS
    assert "event_treasury_buyback_score" in PHASE2_DART_EVENT_COLUMNS
    # Aggregate
    for col in PHASE2_DART_EVENT_COLUMNS:
        assert col in PHASE2_KOREA_ALPHA_COLUMNS


print()
print("=" * 60)
print(f"P2 events tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
