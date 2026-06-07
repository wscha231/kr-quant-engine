"""tests/test_pmb_false_positive_audit.py -- P_MB false-positive diagnostics.

Run:
    py -3 tests/test_pmb_false_positive_audit.py
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


def _sample_rows(months: int = 18, tickers: int = 40) -> pd.DataFrame:
    rows = []
    for day in pd.date_range("2020-01-31", periods=months, freq="ME"):
        for i in range(tickers):
            good = i % 5 == 0
            bad = i % 7 == 0
            ret = 0.16 if good else (-0.14 if bad else 0.01)
            mn = -0.04 if good else (-0.18 if bad else -0.06)
            rows.append({
                "rebalance_date": day,
                "ticker": f"{i:06d}",
                "name": f"N{i}",
                "analysis_return": ret,
                "analysis_min_return": mn,
                "forward_return_1m": ret,
                "realized_holding_return": ret,
                "bad_trade": int(bad),
                "year": int(day.year),
                "pmb_oos_fold_id": 3,
                "feature_good": 1.0 if good else 0.0,
                "feature_bad": 1.0 if bad else 0.0,
                "feature_noise": float(i),
                "p_pre_surge": 0.4 + i * 0.001,
                "p_risk": 0.1 if bad else 0.0,
            })
    return pd.DataFrame(rows)


@_test("false-positive feature selector excludes realized labels and generated outcomes")
def test_feature_selector_excludes_leakage():
    from tools.analyze_pmb_false_positives import add_trade_outcome_labels, select_false_positive_features

    rows = add_trade_outcome_labels(_sample_rows())
    features = select_false_positive_features(rows, min_observations=20)
    assert "feature_good" in features
    assert "feature_bad" in features
    assert "analysis_return" not in features
    assert "analysis_min_return" not in features
    assert "forward_return_1m" not in features
    assert "realized_holding_return" not in features
    assert "bad_trade" not in features
    assert "good_trade" not in features
    assert "year" not in features
    assert "pmb_oos_fold_id" not in features


@_test("walk-forward false-positive audit respects embargo and writes OOS scores")
def test_false_positive_walk_forward_embargo():
    from tools.analyze_pmb_false_positives import (
        add_trade_outcome_labels,
        select_false_positive_features,
        walk_forward_false_positive_scores,
    )

    rows = add_trade_outcome_labels(_sample_rows(months=20, tickers=50))
    features = select_false_positive_features(rows, min_observations=20)
    scored, folds = walk_forward_false_positive_scores(
        rows,
        features,
        target_start="2020-01-01",
        target_end="2021-08-31",
        embargo_months=3,
        min_train_rows=150,
    )
    modeled = folds[folds["mode"] == "extra_trees_oos"]
    assert not modeled.empty
    for _, fold in modeled.iterrows():
        assert pd.Timestamp(fold["train_end"]) <= pd.Timestamp(fold["rebalance_date"]) - pd.DateOffset(months=3)
    assert scored["p_bad_oos"].notna().sum() > 0
    assert scored["p_good_oos"].notna().sum() > 0


if __name__ == "__main__":
    print("=" * 60)
    print("P_MB false-positive audit tests")
    print("=" * 60)
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
