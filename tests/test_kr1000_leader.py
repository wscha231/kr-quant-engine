"""tests/test_kr1000_leader.py -- KR1000 Leader Alpha invariants.

Run:
    py -3 tests/test_kr1000_leader.py
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
            print(f"PASS {name}")
        except Exception as e:
            FAILED += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
        return fn
    return deco


@_test("kr1000 universe ranks eligible names by liquidity with mcap tiebreak")
def test_kr1000_universe_rank():
    from kr1000_leader import build_kr1000_universe

    snap = pd.DataFrame({
        "rebalance_date": pd.Timestamp("2024-01-31"),
        "ticker": ["000001", "000002", "000003", "000004", "0011T0"],
        "name": ["A", "B", "C", "D", "E"],
        "exchange": ["KOSPI", "KOSPI", "KOSDAQ", "KOSDAQ", "KOSPI"],
        "market_cap": [10, 50, 20, 30, 40],
        "avg_trading_value_60d": [100, 100, 80, 0, 200],
        "eligible": [True, True, True, True, False],
        "exclude_reason": ["", "", "", "", "preferred"],
    })
    out = build_kr1000_universe(
        "2024-01-31",
        cfg={"kr1000_size": 3},
        snapshot=snap,
        include_discovery=True,
    )
    selected = out[out["in_kr1000"]]["ticker"].tolist()
    assert selected == ["000002", "000001", "000003"]
    assert int(out["in_kr1000"].sum()) == 3
    assert out["ticker"].is_unique
    assert "0011T0" not in selected


@_test("relative strength vs KOSPI200 computes 1m/3m/6m and rank direction")
def test_relative_strength_math():
    from kr1000_leader import compute_kospi200_relative_strength

    dates = pd.bdate_range("2024-01-01", periods=150)
    bench = pd.DataFrame({"date": dates, "close": np.linspace(100, 110, len(dates))})
    price = pd.concat([
        pd.DataFrame({"date": dates, "ticker": "000001", "close": np.linspace(100, 150, len(dates))}),
        pd.DataFrame({"date": dates, "ticker": "000002", "close": np.linspace(100, 95, len(dates))}),
    ], ignore_index=True)
    out = compute_kospi200_relative_strength(price, bench, as_of=dates[-1])
    a = out.set_index("ticker").loc["000001"]
    b = out.set_index("ticker").loc["000002"]
    assert a["rs_1m"] > b["rs_1m"]
    assert a["rs_3m"] > 0
    assert b["rs_6m"] < 0
    assert a["rs_score"] > b["rs_score"]


@_test("score profiles isolate RS, flow, and technical components")
def test_score_profiles():
    from kr1000_leader import apply_kr1000_score_profile

    candidates = pd.DataFrame({
        "ticker": ["000001", "000002"],
        "rs_score": [2.0, -1.0],
        "flow_score": [-5.0, 5.0],
        "technical_score": [-4.0, 4.0],
        "quality_growth_score": [3.0, 3.0],
        "valuation_score": [2.0, 2.0],
        "theme_sector_score": [1.0, 1.0],
        "event_governance_score": [0.5, 0.5],
        "p0_momentum_score": [0.2, 0.8],
        "p1_blended_score": [0.1, 0.9],
        "p_pre_surge": [0.7, 0.2],
    })
    rs_only = apply_kr1000_score_profile(candidates, "rs_only")
    assert rs_only["score_profile"].eq("rs_only").all()
    assert rs_only["flow_score"].eq(0.0).all()
    assert rs_only["technical_score"].eq(0.0).all()
    assert np.isclose(rs_only.loc[0, "leader_score"], 0.35 * 2.0)

    rs_flow = apply_kr1000_score_profile(candidates, "rs_flow")
    assert rs_flow["technical_score"].eq(0.0).all()
    assert np.isclose(rs_flow.loc[1, "leader_score"], 0.35 * -1.0 + 0.20 * 5.0)

    full = apply_kr1000_score_profile(candidates, "full")
    assert full["flow_score"].iloc[0] == -5.0
    assert full["technical_score"].iloc[1] == 4.0

    legacy = apply_kr1000_score_profile(candidates, "legacy_p1_blended")
    assert legacy.loc[1, "leader_score"] > legacy.loc[0, "leader_score"]

    pmb = apply_kr1000_score_profile(candidates, "pmb_pre_surge")
    assert pmb.loc[0, "leader_score"] > pmb.loc[1, "leader_score"]

    hybrid = apply_kr1000_score_profile(candidates, "hybrid_pmb_rs")
    assert hybrid["score_profile"].eq("hybrid_pmb_rs").all()
    assert not hybrid["leader_score"].isna().any()


@_test("trade plan keeps every current holding with reason_code")
def test_trade_plan_current_holdings_reconciled():
    from kr1000_leader import (
        build_target_portfolio,
        compute_leader_scores,
        generate_trade_plan,
    )

    candidates = pd.DataFrame({
        "ticker": ["000001", "000002"],
        "name": ["Leader", "Lagging Holding"],
        "in_kr1000": [True, True],
        "avg_trading_value_60d": [10e9, 9e9],
        "market_cap": [1e12, 9e11],
        "rs_score": [2.0, -2.0],
        "flow_score": [1.0, -1.5],
        "leader_rank": [1, 60],
        "eligible_final": [True, True],
        "risk_veto_flag": [0, 0],
        "hard_exit_flag": [0, 0],
        "max_weight": [0.07, 0.07],
    })
    current = pd.DataFrame({
        "ticker": ["000002"],
        "name": ["Lagging Holding"],
        "shares": [10],
        "avg_cost": [10000],
        "last_price": [9000],
        "market_value": [90000],
        "manual_lock": [False],
    })
    scored = compute_leader_scores(candidates)
    target = build_target_portfolio(scored, cfg={"top_holdings": 1}, as_of_date="2024-04-01")
    plan = generate_trade_plan(current, target, scored, cfg={"min_notional_krw": 1}, as_of_date="2024-04-01")
    assert "000002" in set(plan["ticker"])
    assert plan.loc[plan["ticker"] == "000002", "action"].iloc[0] == "SELL"
    assert plan["reason_code"].astype(str).str.len().gt(0).all()
    assert set(plan["action"]).issubset({"BUY", "ADD", "HOLD", "TRIM", "SELL", "BLOCKED", "NO_TRADE"})


@_test("event-driven backtest writes ledger rows and reason codes")
def test_event_backtester_ledgers():
    from kr1000_leader import run_event_driven_backtest

    dates = pd.bdate_range("2024-01-01", periods=12)
    price = pd.concat([
        pd.DataFrame({"date": dates, "ticker": "000001", "open": 100 + np.arange(len(dates)), "close": 101 + np.arange(len(dates))}),
        pd.DataFrame({"date": dates, "ticker": "000002", "open": 100 - np.arange(len(dates)), "close": 99 - np.arange(len(dates))}),
    ], ignore_index=True)
    snapshots = []
    for d in dates[::5]:
        snapshots.append(pd.DataFrame({
            "date": [d, d],
            "ticker": ["000001", "000002"],
            "leader_score": [2.0, -1.0],
            "leader_rank": [1, 2],
            "eligible_final": [True, True],
            "risk_veto_flag": [0, 0],
            "hard_exit_flag": [0, 0],
            "max_weight": [0.07, 0.07],
            "avg_trading_value_60d": [1e9, 1e9],
            "market_cap": [1e12, 1e12],
        }))
    scored = pd.concat(snapshots, ignore_index=True)
    result = run_event_driven_backtest(
        scored,
        price,
        cfg={"top_holdings": 1, "min_notional_krw": 1, "gross_exposure_min": 0.0},
        initial_cash=1_000_000,
    )
    assert "cagr" in result.metrics
    assert result.metrics["metric_mode"] == "broker_ledger_next_close"
    assert result.metrics["fill_mode"] == "next_close"
    assert not result.daily_nav.empty
    assert not result.orders.empty
    assert result.orders["reason_code"].astype(str).str.len().gt(0).all()
    assert result.trades["fill_mode"].eq("next_close").all()


@_test("event-driven backtest applies daily broker hard-stop before next close")
def test_event_backtester_daily_hard_stop():
    from kr1000_leader import run_event_driven_backtest

    dates = pd.bdate_range("2024-01-01", periods=5)
    price = pd.DataFrame({
        "date": dates,
        "ticker": "000001",
        "open": [100, 84, 80, 79, 78],
        "close": [100, 84, 80, 79, 78],
    })
    scored = pd.DataFrame({
        "date": [dates[-1]],
        "ticker": ["000001"],
        "leader_score": [2.0],
        "leader_rank": [1],
        "eligible_final": [True],
        "risk_veto_flag": [0],
        "hard_exit_flag": [0],
        "max_weight": [0.07],
        "avg_trading_value_60d": [1e9],
        "market_cap": [1e12],
    })
    holdings = pd.DataFrame({
        "ticker": ["000001"],
        "shares": [10],
        "avg_cost": [100],
        "last_price": [100],
        "market_value": [1000],
    })
    result = run_event_driven_backtest(
        scored,
        price,
        cfg={"top_holdings": 1, "min_notional_krw": 1, "hard_stop_loss_pct": 0.15},
        initial_cash=100_000,
        initial_holdings=holdings,
    )
    assert "SELL_HARD_STOP_DAILY" in set(result.orders["reason_code"])
    sell_trades = result.trades[result.trades["reason_code"] == "SELL_HARD_STOP_DAILY"]
    assert not sell_trades.empty
    assert pd.Timestamp(sell_trades["execution_date"].iloc[0]) > dates[1]


if __name__ == "__main__":
    print(f"kr1000 leader tests: {PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
