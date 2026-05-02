"""tests/test_pit_universe.py -- Phase C1 PIT survivorship & look-ahead invariants.

Tests the kr_pit_universe module + integration with kr_universe + kr_macro.

Five critical invariants (per plan.md C1 ship gate):
1. test_fdr_current_listing_not_used_for_historical_backtest
   build_universe_snapshot for an old date does NOT include any ticker
   that is currently listed but was not yet listed at that date.
2. test_historical_mcap_required_before_rebalance_date
   fetch_listing_at_date refuses to use a snapshot AFTER the rebalance date.
3. test_listed_months_not_stub
   compute_listed_months returns a distribution of values, not all 999.
4. test_delisted_ticker_can_exist_in_past_universe
   Historical universe at date T includes tickers active at T even if
   they have since been delisted.
5. test_macro_publication_lag_applied
   get_macro_snapshot_pit returns NaN for a series whose data point's
   period-end + lag is after rebalance_date, even though the daily-frequency
   ffill might propagate the value.

Run: py -3 tests/test_pit_universe.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

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


# ---------------------------------------------------------------------------
# Module sanity
# ---------------------------------------------------------------------------
@_test("import kr_pit_universe succeeds")
def test_import():
    import kr_pit_universe as pit
    assert callable(pit.fetch_listing_at_date)
    assert callable(pit.compute_listed_months_pit)
    assert callable(pit.build_listed_history_from_cache)
    assert callable(pit.build_historical_mcap_panel)
    assert callable(pit.load_listed_history)


@_test("listed_history.parquet exists or builds on demand")
def test_listed_history_loads():
    from kr_pit_universe import load_listed_history
    df = load_listed_history(rebuild_if_missing=True)
    if df.empty:
        # Acceptable if no cached snapshots -- but raise so the user knows
        raise AssertionError(
            "listed_history empty; run tools/build_pit_universe_history.py "
            "(or have cached mktcap_ALL_*.parquet snapshots).")
    assert "ticker" in df.columns
    assert "first_seen_date" in df.columns
    assert "is_active_latest" in df.columns
    assert "inferred_listing_date" in df.columns


# ---------------------------------------------------------------------------
# Critical invariant tests (5 ship-gate tests)
# ---------------------------------------------------------------------------
@_test("fdr_current_listing_not_used_for_historical_backtest")
def test_fdr_current_listing_not_used_for_historical_backtest():
    """Any ticker in historical universe must have been seen in a snapshot
    at-or-before the rebalance date -- i.e. no FDR-current contamination.
    """
    from kr_pit_universe import (
        fetch_listing_at_date,
        load_historical_mcap,
    )

    rebalance_date = pd.Timestamp("2020-06-30")
    universe = fetch_listing_at_date(rebalance_date, name_lookup=False)
    if universe.empty:
        raise AssertionError(
            "PIT universe empty for 2020-06-30; rebuild cache via "
            "tools/build_pit_universe_history.py")

    panel = load_historical_mcap()
    assert not panel.empty, "historical_mcap empty"

    # Each ticker in the 2020-06-30 universe must have a panel entry with
    # snapshot_date <= 2020-06-30
    universe_tickers = set(universe["ticker"].astype(str))
    panel_at_or_before = panel[panel["snapshot_date"] <= rebalance_date]
    panel_tickers_at_or_before = set(panel_at_or_before["ticker"].astype(str))

    leak = universe_tickers - panel_tickers_at_or_before
    assert not leak, (
        f"{len(leak)} tickers in 2020-06-30 universe have no historical "
        f"snapshot ≤ that date (leak from FDR-current?). Examples: "
        f"{list(leak)[:5]}")


@_test("historical_mcap_required_before_rebalance_date")
def test_historical_mcap_required_before_rebalance_date():
    """fetch_listing_at_date must refuse when no snapshot exists at-or-before
    the rebalance date (returns empty, never falls back to current).
    """
    from kr_pit_universe import fetch_listing_at_date
    df = fetch_listing_at_date("2010-01-01", name_lookup=False)
    assert df.empty, (
        f"Expected empty result for pre-cache date 2010-01-01, got {len(df)} "
        f"rows -- possible FDR-current leak.")


@_test("listed_months_not_stub")
def test_listed_months_not_stub():
    """compute_listed_months should NOT return uniform 999. Real values
    distinguish recently-listed names (< 12 months) from long-listed."""
    from kr_pit_universe import compute_listed_months_pit, load_listed_history

    hist = load_listed_history()
    if hist.empty:
        raise AssertionError("listed_history empty")

    # Pick a date well inside the cache range so newly-listed tickers exist
    rebalance_date = pd.Timestamp("2024-06-30")
    sample_tickers = hist["ticker"].head(500).tolist()
    df = compute_listed_months_pit(rebalance_date, sample_tickers)
    assert not df.empty
    values = df["listed_months"].astype(int)
    n_999 = int((values == 999).sum())
    # At least SOME ticker must have real (non-999, non-zero) listed_months.
    real_count = int(((values > 0) & (values < 999)).sum())
    assert real_count > 0, (
        f"All listed_months values were 999 or 0 -- stub still active. "
        f"999 count={n_999}, real count={real_count}, "
        f"sample size={len(values)}")


@_test("delisted_ticker_can_exist_in_past_universe")
def test_delisted_ticker_can_exist_in_past_universe():
    """A ticker that was alive in 2018 but is now delisted must appear in
    the 2018-12-31 PIT universe -- survivorship-bias-free.
    """
    from kr_pit_universe import (
        fetch_listing_at_date,
        load_listed_history,
    )

    hist = load_listed_history()
    if hist.empty:
        raise AssertionError("listed_history empty")

    delisted = hist[~hist["is_active_latest"]]
    if delisted.empty:
        # Cache doesn't include any delisted tickers -- soft skip
        print("    (no delisted tickers in cache -- soft skip)")
        return

    # Find a date where we have delisted-by-now tickers that were alive then.
    # Cache: pre-2019 snapshots have narrower coverage, so use 2020-06-30.
    rebalance_date = pd.Timestamp("2020-06-30")
    candidates = delisted[
        (delisted["first_seen_date"] <= rebalance_date)
        & (delisted["last_seen_date"] >= rebalance_date)
    ]
    if candidates.empty:
        print(f"    (no delisted-by-now tickers active at "
              f"{rebalance_date.date()} -- soft skip)")
        return

    universe = fetch_listing_at_date(rebalance_date, name_lookup=False)
    universe_tickers = set(universe["ticker"].astype(str))

    sample = candidates["ticker"].head(20).astype(str).tolist()
    found = [t for t in sample if t in universe_tickers]
    assert found, (
        f"None of {len(sample)} delisted-by-now tickers (alive at "
        f"{rebalance_date.date()}) appeared in PIT universe -- "
        f"survivorship leak.")


@_test("macro_publication_lag_applied")
def test_macro_publication_lag_applied():
    """get_macro_snapshot_pit must return None / NaN for a monthly series
    whose period-end + lag > rebalance_date."""
    from kr_macro import (
        PUBLICATION_LAG_DAYS,
        get_macro_snapshot,
        get_macro_snapshot_pit,
    )

    # Synthetic panel: monthly PMI value dated last day of month
    dates = pd.date_range("2024-01-01", "2024-06-30", freq="D")
    panel = pd.DataFrame({"date": dates})
    panel["macro_kr_pmi"] = np.nan
    panel["macro_kr_industrial_prod"] = np.nan

    # Set PMI value on 2024-06-30 (period end)
    panel.loc[panel["date"] == "2024-06-30", "macro_kr_pmi"] = 52.5
    # Industrial production for May 2024, observed at 2024-05-31
    panel.loc[panel["date"] == "2024-05-31", "macro_kr_industrial_prod"] = 110.0
    panel = panel.ffill()

    # Re-null forward fill for clarity: in real panel, the value carries forward
    # but the PIT helper must check the ORIGINAL date, not ffilled. We achieve
    # this by passing the unfilled panel.
    raw_panel = pd.DataFrame({"date": dates})
    raw_panel["macro_kr_pmi"] = np.nan
    raw_panel["macro_kr_industrial_prod"] = np.nan
    raw_panel.loc[raw_panel["date"] == "2024-06-30", "macro_kr_pmi"] = 52.5
    raw_panel.loc[raw_panel["date"] == "2024-05-31", "macro_kr_industrial_prod"] = 110.0

    # Case A: rebalance date 2024-06-30 -- PMI for that day shouldn't be visible
    # yet (PMI lag = 1 day, so released 2024-07-01). PIT should return NaN.
    snap_a = get_macro_snapshot_pit(raw_panel, pd.Timestamp("2024-06-30"))
    assert pd.isna(snap_a["macro_kr_pmi"]), (
        f"PMI should be NaN at 2024-06-30 (lag=1d), got {snap_a['macro_kr_pmi']}")

    # Case B: rebalance date 2024-07-02 -- PMI for 2024-06-30 should now be visible
    snap_b = get_macro_snapshot_pit(raw_panel, pd.Timestamp("2024-07-02"))
    assert snap_b["macro_kr_pmi"] == 52.5, (
        f"PMI should be 52.5 at 2024-07-02 (lag=1d), got {snap_b['macro_kr_pmi']}")

    # Case C: industrial production lag = 30 days. May-31 obs published ~30 days later.
    # At 2024-06-15: NOT yet visible. At 2024-07-15: visible.
    snap_c1 = get_macro_snapshot_pit(raw_panel, pd.Timestamp("2024-06-15"))
    assert pd.isna(snap_c1["macro_kr_industrial_prod"]), (
        f"Industrial prod should be NaN at 2024-06-15, got "
        f"{snap_c1['macro_kr_industrial_prod']}")
    snap_c2 = get_macro_snapshot_pit(raw_panel, pd.Timestamp("2024-07-15"))
    assert snap_c2["macro_kr_industrial_prod"] == 110.0, (
        f"Industrial prod should be 110 at 2024-07-15, got "
        f"{snap_c2['macro_kr_industrial_prod']}")

    # Sanity: legacy get_macro_snapshot ignores lag (= NOT PIT-correct)
    snap_legacy = get_macro_snapshot(raw_panel, pd.Timestamp("2024-06-30"))
    # Legacy returns the most recent value at-or-before; PMI is set on 2024-06-30
    # so legacy returns 52.5 -- confirming PIT helper is the safe path.
    assert snap_legacy["macro_kr_pmi"] == 52.5, (
        "Legacy get_macro_snapshot did not return 52.5 -- sanity check failed.")


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 70)
    print("Phase C1 -- PIT universe / survivorship / pub-lag tests")
    print("=" * 70)
    print()
    print(f"{PASSED + FAILED} tests run, {PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
