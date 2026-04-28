"""tests/test_multibagger_classifier.py — P_MB.2 classifier logic.

Currently focused on the label-deconfliction guard (Issue C, 2026-04-28):
overlapping pre-surge windows on the same ticker must not double-label rows.

Run: py -3 tests/test_multibagger_classifier.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from kr_multibagger_classifier import (
    deduplicate_overlapping_episodes,
    label_pre_surge,
)

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


@_test("dedup: non-overlapping episodes preserved")
def test_dedup_non_overlapping():
    eps = pd.DataFrame({
        "ticker": ["005930", "005930"],
        "surge_start_date": [pd.Timestamp("2020-01-31"), pd.Timestamp("2022-01-31")],
    })
    out = deduplicate_overlapping_episodes(eps, pre_surge_months=6, post_surge_months=3)
    assert len(out) == 2, f"expected 2 episodes, got {len(out)}"


@_test("dedup: overlapping episodes collapsed to first")
def test_dedup_overlapping():
    # Episode 1 window: 2020-01-31 - 6m = 2019-07-31 .. 2020-04-30
    # Episode 2 surge_start = 2020-03-15 → window_start = 2019-09-15
    # Overlaps with Episode 1's window → drop Episode 2.
    eps = pd.DataFrame({
        "ticker": ["005930", "005930"],
        "surge_start_date": [pd.Timestamp("2020-01-31"), pd.Timestamp("2020-03-15")],
    })
    out = deduplicate_overlapping_episodes(eps, pre_surge_months=6, post_surge_months=3)
    assert len(out) == 1, f"expected 1 episode (overlap dropped), got {len(out)}"
    assert out.iloc[0]["surge_start_date"] == pd.Timestamp("2020-01-31"), \
        "first episode (earlier surge_start) must be the one kept"


@_test("dedup: per-ticker isolation (no cross-ticker collapse)")
def test_dedup_cross_ticker():
    # Same dates, different tickers → both kept.
    eps = pd.DataFrame({
        "ticker": ["005930", "000660"],
        "surge_start_date": [pd.Timestamp("2020-01-31"), pd.Timestamp("2020-03-15")],
    })
    out = deduplicate_overlapping_episodes(eps, pre_surge_months=6, post_surge_months=3)
    assert len(out) == 2, f"expected 2 episodes (different tickers), got {len(out)}"


@_test("dedup: chained overlaps drop subsequent only")
def test_dedup_chain():
    # Three close surges: keep first, drop second (overlaps first),
    # drop third only if it overlaps the FIRST kept window.
    eps = pd.DataFrame({
        "ticker": ["005930"] * 3,
        "surge_start_date": [
            pd.Timestamp("2020-01-31"),
            pd.Timestamp("2020-02-15"),  # overlaps window 1
            pd.Timestamp("2020-08-01"),  # window: 2020-02-01..2020-11-01 — doesn't overlap window 1's end (2020-04-30)
        ],
    })
    out = deduplicate_overlapping_episodes(eps, pre_surge_months=6, post_surge_months=3)
    assert len(out) == 2, f"expected 2 (1st and 3rd kept), got {len(out)}"
    kept = out["surge_start_date"].dt.strftime("%Y-%m-%d").tolist()
    assert kept == ["2020-01-31", "2020-08-01"], f"unexpected kept: {kept}"


@_test("dedup: NaN surge_start dropped")
def test_dedup_nan():
    eps = pd.DataFrame({
        "ticker": ["005930", "005930"],
        "surge_start_date": [pd.Timestamp("2020-01-31"), pd.NaT],
    })
    out = deduplicate_overlapping_episodes(eps, pre_surge_months=6, post_surge_months=3)
    assert len(out) == 1, f"NaN row must be dropped, got {len(out)}"


@_test("label_pre_surge: row in window labeled 1")
def test_label_in_window():
    scored = pd.DataFrame({
        "ticker": ["005930", "005930", "005930"],
        "rebalance_date": [
            pd.Timestamp("2019-12-31"),  # within window
            pd.Timestamp("2020-06-30"),  # outside (post + 3m = 2020-04-30)
            pd.Timestamp("2019-06-30"),  # outside (pre - 6m = 2019-07-31)
        ],
    })
    eps = pd.DataFrame({
        "ticker": ["005930"],
        "surge_start_date": [pd.Timestamp("2020-01-31")],
    })
    out = label_pre_surge(scored, eps, pre_surge_months=6, post_surge_months=3)
    assert out["is_pre_surge"].tolist() == [1, 0, 0], \
        f"expected [1,0,0], got {out['is_pre_surge'].tolist()}"


@_test("label_pre_surge: overlapping eps don't double-label")
def test_label_no_double():
    # Two episodes that would each label the same row — after dedup, only
    # one window survives, but that row is still labeled 1 (not 2).
    scored = pd.DataFrame({
        "ticker": ["005930"],
        "rebalance_date": [pd.Timestamp("2020-01-15")],
    })
    eps = pd.DataFrame({
        "ticker": ["005930", "005930"],
        "surge_start_date": [pd.Timestamp("2020-01-31"), pd.Timestamp("2020-03-15")],
    })
    out = label_pre_surge(scored, eps, pre_surge_months=6, post_surge_months=3)
    assert out["is_pre_surge"].tolist() == [1], \
        f"expected [1] (overlap dedup'd), got {out['is_pre_surge'].tolist()}"


if __name__ == "__main__":
    print("=" * 60)
    print("kr_multibagger_classifier — label deconfliction tests")
    print("=" * 60)
    # Trigger registration via decorator side-effects above
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
