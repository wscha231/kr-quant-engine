"""tests/test_flow.py — P2.5 Flow signal verification.

Mock flow panels + signal compute logic + PIT snapshot retrieval.

Run: py -3 tests/test_flow.py
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
# Mock factories
# ---------------------------------------------------------------------------
def make_ticker_flow(ticker="TEST01", days=100, seed=42, drift=0.0) -> pd.DataFrame:
    """Mock ticker-level daily flow series."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=days, freq="B")
    foreign = rng.normal(drift * 1e9, 5e8, days)
    inst = rng.normal(drift * 5e8, 3e8, days)
    individual = -(foreign + inst)
    return pd.DataFrame({
        "date": dates, "ticker": ticker,
        "foreign_net": foreign, "inst_net": inst, "individual_net": individual,
    })


def make_market_flow(days=100, seed=42) -> pd.DataFrame:
    """Mock market-level flow for both KOSPI + KOSDAQ."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=days, freq="B")
    frames = []
    for mkt in ("KOSPI", "KOSDAQ"):
        df = pd.DataFrame({
            "date": dates,
            "market": mkt,
            "foreign_net": rng.normal(0, 1e10, days),
            "inst_net": rng.normal(0, 5e9, days),
            "individual_net": rng.normal(0, 8e9, days),
        })
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def make_foreign_holding(tickers, days=30, seed=42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=days, freq="B")
    rows = []
    for tk in tickers:
        base_pct = rng.uniform(5, 50)
        for i, d in enumerate(dates):
            rows.append({
                "date": d, "ticker": tk,
                "foreign_holding_pct": base_pct + rng.normal(0, 0.1),
                "market": "KOSPI",
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests — signal compute helpers
# ---------------------------------------------------------------------------

@_test("import: kr_flow module")
def test_imports():
    import kr_flow
    assert callable(kr_flow.compute_ticker_flow_signals)
    assert callable(kr_flow.compute_market_flow_signals)
    assert callable(kr_flow.get_market_flow_snapshot)
    assert callable(kr_flow.get_ticker_flow_snapshot)
    assert callable(kr_flow.get_foreign_holding_snapshot)
    assert callable(kr_flow._zscore)
    assert callable(kr_flow._streak_days)


@_test("kr_config: PHASE2_FLOW_COLUMNS = 17")
def test_phase2_flow_columns():
    from kr_config import PHASE2_FLOW_COLUMNS, PHASE2_KOREA_ALPHA_COLUMNS
    assert len(PHASE2_FLOW_COLUMNS) == 17
    expected = ["foreign_net_buy_20d_zscore", "inst_net_buy_20d_zscore",
                "foreign_holding_pct", "foreign_holding_change_20d",
                "foreign_buying_streak_days",
                "market_foreign_net_buy_20d_kospi",
                "market_foreign_cumulative_60d",
                "foreign_inst_combined_zscore_20d"]
    for c in expected:
        assert c in PHASE2_FLOW_COLUMNS, f"missing {c}"
    for c in PHASE2_FLOW_COLUMNS:
        assert c in PHASE2_KOREA_ALPHA_COLUMNS


@_test("zscore: rolling mean/std normalization")
def test_zscore():
    import kr_flow
    # Spike at very end: last value much larger than rolling mean
    s = pd.Series([0]*15 + [100])
    z = kr_flow._zscore(s, window=10)
    assert z.iloc[-1] > 1.5   # spike → high z
    # Constant zero-variance window → NaN (std=0 protected)
    s_const = pd.Series([5.0]*20)
    z_const = kr_flow._zscore(s_const, window=10)
    assert pd.isna(z_const.iloc[-1])


@_test("streak_days: counts consecutive positive days")
def test_streak():
    import kr_flow
    s = pd.Series([1.0, 2.0, -1.0, 3.0, 4.0, 5.0, -1.0, 1.0])
    out = kr_flow._streak_days(s)
    expected = [1, 2, 0, 1, 2, 3, 0, 1]
    for i, e in enumerate(expected):
        assert out.iloc[i] == e, f"idx {i}: {out.iloc[i]} != {e}"


@_test("compute_ticker_flow_signals: z-scores + streaks populated")
def test_ticker_signals():
    import kr_flow
    df = make_ticker_flow(days=80, seed=1)
    out = kr_flow.compute_ticker_flow_signals(df, "TEST01")
    expected_cols = ["foreign_net_buy_5d_zscore", "foreign_net_buy_20d_zscore",
                     "foreign_net_buy_60d_zscore", "inst_net_buy_5d_zscore",
                     "inst_net_buy_20d_zscore", "individual_net_buy_20d_zscore",
                     "foreign_buying_streak_days", "inst_buying_streak_days",
                     "foreign_inst_combined_zscore_20d"]
    for c in expected_cols:
        assert c in out.columns, f"missing {c}"
    assert pd.notna(out["foreign_net_buy_20d_zscore"].iloc[-1])


@_test("compute_market_flow_signals: rolling sums per market")
def test_market_signals():
    import kr_flow
    df = make_market_flow(days=80, seed=2)
    out = kr_flow.compute_market_flow_signals(df)
    expected = ["market_foreign_net_buy_20d_kospi",
                "market_foreign_net_buy_20d_kosdaq",
                "market_foreign_cumulative_60d_kospi",
                "market_inst_net_buy_20d_kospi"]
    for c in expected:
        assert c in out.columns, f"missing {c}"


@_test("get_market_flow_snapshot: PIT lookup returns dict")
def test_market_snapshot():
    import kr_flow
    df = make_market_flow(days=60, seed=3)
    enriched = kr_flow.compute_market_flow_signals(df)
    snap = kr_flow.get_market_flow_snapshot(enriched, pd.Timestamp("2024-02-01"))
    keys = ["market_foreign_net_buy_20d_kospi", "market_foreign_net_buy_20d_kosdaq",
            "market_inst_net_buy_20d_kospi", "market_inst_net_buy_20d_kosdaq",
            "market_foreign_cumulative_60d"]
    for k in keys:
        assert k in snap


@_test("get_ticker_flow_snapshot: PIT respects rebalance_date")
def test_ticker_snapshot_pit():
    import kr_flow
    df = make_ticker_flow("TEST01", days=80, seed=4)
    enriched = kr_flow.compute_ticker_flow_signals(df, "TEST01")
    snap = kr_flow.get_ticker_flow_snapshot(enriched, "TEST01",
                                                pd.Timestamp("2024-02-15"))
    assert "foreign_net_buy_20d_zscore" in snap
    # Future date: should return all-NaN dict
    snap_future = kr_flow.get_ticker_flow_snapshot(enriched, "UNKNOWN_TICKER",
                                                       pd.Timestamp("2024-02-15"))
    for v in snap_future.values():
        assert pd.isna(v)


@_test("get_foreign_holding_snapshot: pct + 20d change")
def test_holding_snapshot():
    import kr_flow
    panel = make_foreign_holding(["TEST01"], days=40, seed=5)
    snap = kr_flow.get_foreign_holding_snapshot(panel, "TEST01",
                                                    pd.Timestamp("2024-02-15"))
    assert "foreign_holding_pct" in snap
    assert "foreign_holding_change_20d" in snap


@_test("get_foreign_holding_snapshot: empty panel returns NaN")
def test_holding_empty():
    import kr_flow
    snap = kr_flow.get_foreign_holding_snapshot(pd.DataFrame(), "X",
                                                    pd.Timestamp("2024-01-01"))
    assert pd.isna(snap["foreign_holding_pct"])
    assert pd.isna(snap["foreign_holding_change_20d"])


@_test("kr_features.add_flow_signals: phase disabled = NaN fill")
def test_features_phase_disabled():
    import os
    from kr_features import add_flow_signals
    from kr_config import PHASE2_FLOW_COLUMNS
    os.environ["PHASE_PHASE2_FLOW_ENABLED"] = "0"
    universe = pd.DataFrame({"ticker": ["A", "B"]})
    out = add_flow_signals(universe, pd.Timestamp("2024-12-31"))
    for col in PHASE2_FLOW_COLUMNS:
        assert col in out.columns
        assert out[col].isna().all()
    os.environ.pop("PHASE_PHASE2_FLOW_ENABLED", None)


@_test("kr_features.add_flow_signals: enabled with mock panels")
def test_features_with_panels():
    import os
    import kr_flow
    from kr_features import add_flow_signals
    os.environ["PHASE_PHASE2_FLOW_ENABLED"] = "1"

    universe = pd.DataFrame({"ticker": ["TEST01", "TEST02"]})

    market_raw = make_market_flow(days=80, seed=10)
    market_panel = kr_flow.compute_market_flow_signals(market_raw)

    ticker_panels = []
    for tk in universe["ticker"]:
        tdf = make_ticker_flow(tk, days=80, seed=hash(tk) % 1000)
        ticker_panels.append(kr_flow.compute_ticker_flow_signals(tdf, tk))
    ticker_panel = pd.concat(ticker_panels, ignore_index=True)

    holding_panel = make_foreign_holding(universe["ticker"].tolist(), days=80, seed=11)

    out = add_flow_signals(
        universe, pd.Timestamp("2024-03-01"),
        market_flow_panel=market_panel,
        ticker_flow_panel=ticker_panel,
        foreign_holding_panel=holding_panel,
    )
    # Market signals should be present (broadcast — same value for both rows)
    assert pd.notna(out["market_foreign_net_buy_20d_kospi"].iloc[0])
    assert (out["market_foreign_net_buy_20d_kospi"] ==
            out["market_foreign_net_buy_20d_kospi"].iloc[0]).all()
    # Ticker-level should differ
    assert pd.notna(out["foreign_net_buy_20d_zscore"].iloc[0])
    # Foreign holding present
    assert pd.notna(out["foreign_holding_pct"].iloc[0])

    os.environ.pop("PHASE_PHASE2_FLOW_ENABLED", None)


@_test("composite: foreign_inst_combined_zscore = 0.6×fzscore + 0.4×izscore")
def test_composite():
    import kr_flow
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=80, freq="B"),
        "ticker": "X",
        "foreign_net": [1e9] * 80,   # constant → zscore NaN
        "inst_net": [5e8] * 80,
    })
    out = kr_flow.compute_ticker_flow_signals(df, "X")
    # composite present
    assert "foreign_inst_combined_zscore_20d" in out.columns


@_test("PHASE2_FLOW_COLUMNS counted in PHASE2_KOREA_ALPHA_COLUMNS")
def test_phase2_aggregation():
    from kr_config import (PHASE2_FLOW_COLUMNS, PHASE2_DART_EVENT_COLUMNS,
                           PHASE2_THEME_SAFETY_COLUMNS, PHASE2_KOREA_ALPHA_COLUMNS)
    expected_total = (len(PHASE2_DART_EVENT_COLUMNS)
                      + len(PHASE2_FLOW_COLUMNS)
                      + len(PHASE2_THEME_SAFETY_COLUMNS))
    assert len(PHASE2_KOREA_ALPHA_COLUMNS) == expected_total


print()
print("=" * 60)
print(f"Flow tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
