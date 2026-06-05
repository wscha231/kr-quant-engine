"""tests/test_kr1000_validation_gate.py -- official KR1000 gate invariants.

Run:
    py -3 tests/test_kr1000_validation_gate.py
"""
from __future__ import annotations

import argparse
import sys
import tempfile
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


@_test("official metric gate passes only broker-ledger target metrics")
def test_official_metric_gate():
    from kr_config import kr1000_leader_alpha_cfg
    from tools.run_kr1000_validation_gate import evaluate_backtest_metrics

    cfg = kr1000_leader_alpha_cfg()
    metrics = {
        "years": 8.25,
        "cagr": 0.36,
        "mdd": -0.24,
        "excess_cagr": 0.01,
        "sharpe": 1.05,
        "information_ratio": 0.55,
        "metric_mode": "broker_ledger_next_close",
        "fill_mode": "next_close",
        "valid_for_production_metric": True,
    }
    gate = evaluate_backtest_metrics(metrics, cfg)
    assert gate["all_pass"] is True

    weak = dict(metrics)
    weak["mdd"] = -0.30
    weak_gate = evaluate_backtest_metrics(weak, cfg)
    assert weak_gate["all_pass"] is False
    assert weak_gate["checks"]["mdd"]["pass"] is False


@_test("planned validation jobs include official component A/B without duplicating stress profiles")
def test_planned_component_ab_jobs():
    from tools.run_kr1000_validation_gate import _planned_backtests

    args = argparse.Namespace(
        periods="official_8y,stress_2020_2022",
        profiles="full",
        component_ab=True,
        initial_cash=100_000_000.0,
        top_holdings=20,
        max_rank_for_prices=20,
        scored_panel=None,
        price_panel=None,
    )
    jobs = _planned_backtests(args, pd.Timestamp("2026-06-04"), PROJECT_ROOT / "outputs" / "test_gate")
    official = [j for j in jobs if j["period"] == "official_8y"]
    stress = [j for j in jobs if j["period"] == "stress_2020_2022"]
    assert {j["profile"] for j in official} == {
        "full",
        "rs_only",
        "rs_flow",
        "rs_flow_technical",
        "legacy_p1_blended",
        "pmb_pre_surge",
        "hybrid_pmb_rs",
    }
    assert {j["profile"] for j in stress} == {"full"}
    assert all("--score-profile" in j["cmd"] for j in jobs)
    assert all(j["start"] <= j["end"] for j in jobs)


@_test("KR1000 backtest runner merges PIT-safe P_MB OOS probabilities")
def test_merge_pmb_oos_predictions():
    from tools.run_kr1000_backtest import merge_pmb_oos_predictions

    panel = pd.DataFrame({
        "rebalance_date": pd.to_datetime(["2024-01-31", "2024-01-31"]),
        "ticker": ["000001", "2"],
        "p_pre_surge": [0.0, 0.0],
    })
    with tempfile.TemporaryDirectory() as tmp:
        picks_path = Path(tmp) / "picks.csv"
        pd.DataFrame({
            "rebalance_date": ["2024-01-31"],
            "ticker": ["000002"],
            "p_pre_surge": [0.77],
            "rank_in_month": [3],
        }).to_csv(picks_path, index=False)
        out = merge_pmb_oos_predictions(panel, picks_path)
    by_ticker = out.set_index("ticker")
    assert by_ticker.loc["000002", "p_pre_surge"] == 0.77
    assert by_ticker.loc["000002", "pmb_oos_rank"] == 3
    assert by_ticker.loc["000001", "p_pre_surge"] == 0.0


if __name__ == "__main__":
    print(f"kr1000 validation gate tests: {PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
