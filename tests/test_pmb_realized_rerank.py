"""tests/test_pmb_realized_rerank.py -- realized-history P_MB reranker.

Run:
    py -3 tests/test_pmb_realized_rerank.py
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


def _sample_rows() -> pd.DataFrame:
    rows = []
    for mi, day in enumerate(pd.date_range("2020-01-31", periods=14, freq="ME")):
        for i in range(30):
            good = i < 10
            rows.append({
                "rebalance_date": day,
                "ticker": f"{i:06d}",
                "name": f"N{i}",
                "market_cap": 1_000_000_000 + i,
                "p_pre_entry": 0.9 if good else 0.1,
                "p_continuation": 0.1 if good else 0.8,
                "p_risk": 0.05 if good else 0.4,
                "p_combined": 0.8 if good else 0.2,
                "p_pre_surge": 0.8 if good else 0.2,
                "pmb_oos_rank": i + 1,
                "rs_3m": 0.1 if good else -0.1,
                "bench_ret_3m": 0.05,
                "technical_score": 0.2 if good else -0.2,
                "analysis_return": 0.08 if good else -0.08,
                "analysis_min_return": -0.03 if good else -0.18,
            })
    return pd.DataFrame(rows)


@_test("realized reranker uses only pre-embargo training rows")
def test_reranker_uses_pre_embargo_rows():
    from tools.build_pmb_realized_rerank_picks import build_realized_rerank_picks

    picks, audit = build_realized_rerank_picks(
        _sample_rows(),
        target_start="2020-01-01",
        target_end="2021-02-28",
        k_per_month=10,
        embargo_months=2,
        min_train_rows=60,
        risk_penalty=0.10,
    )
    assert not picks.empty
    modeled = [f for f in audit["folds"] if f["mode"] == "realized_model"]
    assert modeled
    for fold in modeled:
        train_end = pd.Timestamp(fold["train_end"])
        test_day = pd.Timestamp(fold["rebalance_date"])
        assert train_end <= test_day - pd.DateOffset(months=2)
    latest = picks[picks["rebalance_date"] == picks["rebalance_date"].max()]
    assert latest["rank_in_month"].max() == 10
    assert latest["p_pre_surge"].between(0, 1).all()


@_test("realized reranker falls back before enough training history")
def test_reranker_fallback_before_history():
    from tools.build_pmb_realized_rerank_picks import build_realized_rerank_picks

    picks, audit = build_realized_rerank_picks(
        _sample_rows(),
        target_start="2020-01-01",
        target_end="2020-04-30",
        k_per_month=5,
        embargo_months=3,
        min_train_rows=500,
        risk_penalty=0.10,
    )
    assert not picks.empty
    assert {f["mode"] for f in audit["folds"]} == {"fallback_p_pre_surge"}
    assert set(picks["rerank_mode"]) == {"fallback_p_pre_surge"}


if __name__ == "__main__":
    print("=" * 60)
    print("P_MB realized rerank tests")
    print("=" * 60)
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
