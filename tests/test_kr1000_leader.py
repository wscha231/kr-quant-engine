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
        "p_pre_entry": [0.9, 0.1],
        "p_continuation": [0.1, 0.8],
        "p_risk": [0.0, 0.2],
        "pmb_oos_rank": [8, 1],
        "bench_ret_3m": [0.05, 0.05],
        "market_cap": [1000.0, 900.0],
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

    pre_entry = apply_kr1000_score_profile(candidates, "pmb_pre_entry")
    assert pre_entry.loc[0, "leader_score"] > pre_entry.loc[1, "leader_score"]

    pre_def = apply_kr1000_score_profile(candidates, "pmb_pre_entry_defensive")
    assert pre_def.loc[0, "leader_score"] > 0
    assert pre_def.loc[1, "leader_score"] == 0
    assert bool(pre_def.loc[0, "score_profile_eligible_flag"]) is True
    assert bool(pre_def.loc[1, "score_profile_eligible_flag"]) is False

    pre_blend = apply_kr1000_score_profile(
        candidates.assign(p_combined=[0.7, 0.2]),
        "pmb_pre_entry_blend",
    )
    assert pre_blend.loc[0, "leader_score"] > pre_blend.loc[1, "leader_score"]

    pre_blend_regime = apply_kr1000_score_profile(
        candidates.assign(p_combined=[0.7, 0.2], bench_ret_3m=[0.05, -0.05]),
        "pmb_pre_entry_blend_regime",
    )
    assert pre_blend_regime.loc[0, "leader_score"] > 0
    assert pre_blend_regime.loc[1, "leader_score"] == 0
    assert bool(pre_blend_regime.loc[1, "score_profile_eligible_flag"]) is False

    pullback_regime = apply_kr1000_score_profile(
        candidates.assign(
            p_combined=[0.7, 0.2],
            bench_ret_1m=[-0.02, 0.03],
            bench_ret_3m=[0.05, 0.05],
        ),
        "pmb_pullback_recovery_regime",
    )
    assert pullback_regime.loc[0, "leader_score"] > 0
    assert pullback_regime.loc[1, "leader_score"] == 0
    assert bool(pullback_regime.loc[0, "score_profile_eligible_flag"]) is True
    assert bool(pullback_regime.loc[1, "score_profile_eligible_flag"]) is False

    recovery_trend_value = apply_kr1000_score_profile(
        candidates.assign(
            p_combined=[0.7, 0.2],
            bench_ret_1m=[0.04, 0.03],
            bench_ret_3m=[0.06, 0.06],
            valuation_score=[0.8, -0.2],
        ),
        "pmb_recovery_trend_value_regime",
    )
    assert recovery_trend_value.loc[0, "leader_score"] > 0
    assert recovery_trend_value.loc[1, "leader_score"] == 0
    assert bool(recovery_trend_value.loc[0, "score_profile_eligible_flag"]) is True
    assert bool(recovery_trend_value.loc[1, "score_profile_eligible_flag"]) is False

    kr_technical = apply_kr1000_score_profile(
        pd.DataFrame({
            "ticker": ["000001", "000002", "000003", "000004", "000005"],
            "bench_ret_3m": [0.05, -0.01, 0.05, 0.05, 0.05],
            "technical_score": [1.0, 2.0, 3.0, 4.0, -1.0],
            "market_cap": [1000.0, 1000.0, 100.0, 1000.0, 900.0],
            "avg_trading_value_60d": [1000.0, 1000.0, 1000.0, 100.0, 900.0],
        }),
        "kr1000_technical_mcap_regime",
    )
    assert kr_technical.loc[0, "leader_score"] > 0
    assert bool(kr_technical.loc[0, "score_profile_eligible_flag"]) is True
    assert kr_technical.loc[1, "leader_score"] == 0
    assert kr_technical.loc[2, "leader_score"] == 0
    assert kr_technical.loc[3, "leader_score"] == 0
    assert kr_technical.loc[4, "leader_score"] == 0

    kr_technical_rs3 = apply_kr1000_score_profile(
        pd.DataFrame({
            "ticker": ["000001", "000002"],
            "bench_ret_3m": [0.05, 0.05],
            "technical_score": [1.0, 2.0],
            "rs_3m": [0.05, -0.01],
            "market_cap": [1000.0, 1000.0],
            "avg_trading_value_60d": [1000.0, 1000.0],
        }),
        "kr1000_technical_rs3_mcap_regime",
    )
    assert kr_technical_rs3.loc[0, "leader_score"] > 0
    assert kr_technical_rs3.loc[1, "leader_score"] == 0
    assert bool(kr_technical_rs3.loc[1, "score_profile_eligible_flag"]) is False

    kr_technical_value = apply_kr1000_score_profile(
        pd.DataFrame({
            "ticker": ["000001", "000002", "000003"],
            "bench_ret_3m": [0.05, 0.05, 0.05],
            "technical_score": [1.0, 1.0, -1.0],
            "valuation_score": [0.1, 2.0, 5.0],
            "market_cap": [1000.0, 1000.0, 1000.0],
            "avg_trading_value_60d": [1000.0, 1000.0, 1000.0],
        }),
        "kr1000_technical_value_mcap_regime",
    )
    assert kr_technical_value.loc[1, "leader_score"] > kr_technical_value.loc[0, "leader_score"]
    assert kr_technical_value.loc[2, "leader_score"] == 0

    kr_technical_value06 = apply_kr1000_score_profile(
        pd.DataFrame({
            "ticker": ["000001", "000002", "000003"],
            "bench_ret_3m": [0.05, 0.05, 0.05],
            "technical_score": [1.0, 1.0, -1.0],
            "valuation_score": [0.1, 2.0, 5.0],
            "market_cap": [1000.0, 1000.0, 1000.0],
            "avg_trading_value_60d": [1000.0, 1000.0, 1000.0],
        }),
        "kr1000_technical_value06_mcap_regime",
    )
    assert kr_technical_value06.loc[1, "leader_score"] > kr_technical_value06.loc[0, "leader_score"]
    assert kr_technical_value06.loc[2, "leader_score"] < -1000

    mid_rank = apply_kr1000_score_profile(candidates, "pmb_mid_rank_7_23")
    assert mid_rank.loc[0, "leader_score"] > 0
    assert mid_rank.loc[1, "leader_score"] == 0

    regime = apply_kr1000_score_profile(
        candidates.assign(bench_ret_3m=[0.05, -0.02]),
        "pmb_mid_rank_regime",
    )
    assert regime.loc[0, "leader_score"] > 0
    assert regime.loc[0, "score_profile_eligible_flag"] is True or bool(regime.loc[0, "score_profile_eligible_flag"])
    assert regime.loc[1, "leader_score"] == 0
    assert bool(regime.loc[1, "score_profile_eligible_flag"]) is False

    hybrid = apply_kr1000_score_profile(candidates, "hybrid_pmb_rs")
    assert hybrid["score_profile"].eq("hybrid_pmb_rs").all()
    assert not hybrid["leader_score"].isna().any()


@_test("stale zero component placeholders are recomputed from live sources")
def test_zero_component_placeholders_recomputed():
    from kr1000_leader import apply_kr1000_score_profile

    candidates = pd.DataFrame({
        "ticker": ["000001", "000002", "000003", "000004", "000005"],
        "rs_score": [0.0] * 5,
        "technical_score": [0.0] * 5,
        "flow_score": [0.0] * 5,
        "rs_1m": [0.10, -0.05, 0.02, 0.03, -0.01],
        "rs_3m": [0.30, -0.20, 0.05, 0.04, -0.03],
        "rs_6m": [0.50, -0.30, 0.10, 0.06, -0.05],
        "trend_template_score": [8.0, 0.0, 4.0, 3.0, 1.0],
        "breakout_flag": [1, 0, 0, 0, 0],
        "dist_from_52w_high": [0.0, -0.5, -0.1, -0.2, -0.4],
        "volume_zscore_50": [2.0, -1.0, 0.5, 0.2, -0.5],
        "atr_pct": [0.03, 0.05, 0.04, 0.04, 0.06],
        "trend_template_pass": [1, 0, 1, 0, 0],
    })
    rs_only = apply_kr1000_score_profile(candidates, "rs_only")
    tech = apply_kr1000_score_profile(candidates, "rs_flow_technical")

    assert rs_only["rs_score"].std() > 0
    assert tech["technical_score"].std() > 0
    assert rs_only.loc[0, "leader_score"] > rs_only.loc[1, "leader_score"]
    assert tech.loc[0, "leader_score"] > rs_only.loc[0, "leader_score"]


@_test("P_MB mid-rank profile admits only OOS ranks 7 through 23")
def test_pmb_mid_rank_profile_window():
    from kr1000_leader import apply_kr1000_score_profile

    candidates = pd.DataFrame({
        "ticker": ["000001", "000002", "000003", "000004", "000005"],
        "p_pre_surge": [0.99, 0.95, 0.50, 0.60, 0.70],
        "pmb_oos_rank": [1, 6, 7, 23, 24],
    })
    scored = apply_kr1000_score_profile(candidates, "pmb_mid_rank_7_23")
    by_ticker = scored.set_index("ticker")
    assert by_ticker.loc["000001", "leader_score"] == 0
    assert by_ticker.loc["000002", "leader_score"] == 0
    assert by_ticker.loc["000003", "leader_score"] > 0
    assert by_ticker.loc["000004", "leader_score"] > 0
    assert by_ticker.loc["000005", "leader_score"] == 0
    assert by_ticker.loc["000004", "leader_score"] > by_ticker.loc["000003", "leader_score"]


@_test("P_MB regime profiles make filtered-out rows ineligible")
def test_pmb_regime_profile_eligibility():
    from kr1000_leader import compute_leader_scores

    candidates = pd.DataFrame({
        "ticker": ["000001", "000002", "000003"],
        "p_pre_surge": [0.80, 0.70, 0.60],
        "pmb_oos_rank": [8, 9, 10],
        "bench_ret_3m": [0.03, -0.02, 0.04],
        "technical_score": [0.50, 0.50, -0.20],
        "avg_trading_value_60d": [100.0, 90.0, 80.0],
        "market_cap": [1000.0, 900.0, 800.0],
        "in_kr1000": [True, True, True],
        "eligible_final": [True, True, True],
    })

    regime = compute_leader_scores(candidates, {"score_profile": "pmb_mid_rank_regime"})
    by_ticker = regime.set_index("ticker")
    assert bool(by_ticker.loc["000001", "eligible_final"]) is True
    assert bool(by_ticker.loc["000002", "eligible_final"]) is False
    assert bool(by_ticker.loc["000003", "eligible_final"]) is True

    tech_regime = compute_leader_scores(candidates, {"score_profile": "pmb_mid_tech_regime"})
    by_ticker = tech_regime.set_index("ticker")
    assert bool(by_ticker.loc["000001", "eligible_final"]) is True
    assert bool(by_ticker.loc["000002", "eligible_final"]) is False
    assert bool(by_ticker.loc["000003", "eligible_final"]) is False


@_test("sparse P_MB OOS probabilities rank above zero non-picks")
def test_sparse_pmb_oos_ranking():
    from kr1000_leader import compute_leader_scores

    candidates = pd.DataFrame({
        "ticker": [f"{i:06d}" for i in range(20)],
        "p_pre_surge": [0.0] * 19 + [0.8],
        "avg_trading_value_60d": list(range(20, 0, -1)),
        "market_cap": list(range(20, 0, -1)),
        "in_kr1000": [True] * 20,
        "eligible_final": [True] * 20,
    })
    scored = compute_leader_scores(candidates, {"score_profile": "pmb_pre_surge"})
    top = scored.iloc[0]
    positive = scored.loc[scored["p_pre_surge"] > 0].iloc[0]
    assert top["ticker"] == "000019"
    assert positive["leader_rank"] == 1
    assert positive["leader_score"] > 0


@_test("NaN eligible_final falls back to in_kr1000 after schema union")
def test_nan_eligible_final_falls_back_to_in_kr1000():
    from kr1000_leader import compute_leader_scores

    candidates = pd.DataFrame({
        "ticker": ["000001", "000002"],
        "p_pre_surge": [0.8, 0.0],
        "avg_trading_value_60d": [10e9, 9e9],
        "market_cap": [1e12, 8e11],
        "in_kr1000": [True, True],
        "eligible_final": [np.nan, np.nan],
    })
    scored = compute_leader_scores(candidates, {"score_profile": "pmb_pre_surge"})
    assert scored["leader_rank"].notna().sum() == 2
    assert scored.loc[scored["ticker"] == "000001", "leader_rank"].iloc[0] == 1
    assert bool(scored.loc[scored["ticker"] == "000001", "eligible_final"].iloc[0]) is True


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


@_test("trade plan returns empty schema when no holdings or targets exist")
def test_trade_plan_empty_schema():
    from kr1000_leader import generate_trade_plan

    plan = generate_trade_plan(
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        cfg={},
        as_of_date="2024-04-01",
    )
    assert plan.empty
    assert "reason_code" in plan.columns
    assert "action" in plan.columns


@_test("trade plan weights holdings against account NAV when cash is present")
def test_trade_plan_uses_account_nav_for_cash_weighting():
    from kr1000_leader import generate_trade_plan

    current = pd.DataFrame({
        "ticker": ["000001"],
        "name": ["Cash Diluted Holding"],
        "shares": [5],
        "avg_cost": [1000],
        "last_price": [1000],
        "market_value": [5000],
    })
    target = pd.DataFrame({
        "ticker": ["000001"],
        "name": ["Cash Diluted Holding"],
        "target_weight": [0.50],
    })
    candidates = pd.DataFrame({
        "ticker": ["000001"],
        "name": ["Cash Diluted Holding"],
        "leader_rank": [1],
        "leader_score": [1.0],
        "risk_veto_flag": [0],
        "hard_exit_flag": [0],
    })
    plan = generate_trade_plan(
        current,
        target,
        candidates,
        cfg={"min_notional_krw": 1},
        as_of_date="2024-04-01",
        account_nav=100_000,
    )
    row = plan.iloc[0]
    assert row["action"] == "ADD"
    assert np.isclose(row["current_weight"], 0.05)
    assert np.isclose(row["estimated_trade_krw"], 45_000)


@_test("portfolio drawdown ladder maps observed DD to exposure scale")
def test_portfolio_drawdown_ladder_scale():
    from kr1000_leader import portfolio_drawdown_exposure_scale

    assert np.isclose(portfolio_drawdown_exposure_scale(-0.01), 1.0)
    assert np.isclose(portfolio_drawdown_exposure_scale(-0.08), 0.85)
    assert np.isclose(portfolio_drawdown_exposure_scale(-0.16), 0.65)
    assert np.isclose(portfolio_drawdown_exposure_scale(-0.30), 0.40)


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


@_test("event-driven backtest records portfolio drawdown ladder metrics")
def test_event_backtester_drawdown_ladder_metrics():
    from kr1000_leader import run_event_driven_backtest

    dates = pd.bdate_range("2024-01-01", periods=12)
    price = pd.DataFrame({
        "date": dates,
        "ticker": "000001",
        "open": [100, 100, 100, 90, 85, 80, 82, 84, 86, 88, 90, 92],
        "close": [100, 100, 100, 90, 85, 80, 82, 84, 86, 88, 90, 92],
    })
    scored = pd.DataFrame({
        "date": [dates[0], dates[5]],
        "ticker": ["000001", "000001"],
        "leader_score": [2.0, 2.0],
        "leader_rank": [1, 1],
        "eligible_final": [True, True],
        "risk_veto_flag": [0, 0],
        "hard_exit_flag": [0, 0],
        "max_weight": [1.0, 1.0],
        "avg_trading_value_60d": [1e9, 1e9],
        "market_cap": [1e12, 1e12],
    })
    result = run_event_driven_backtest(
        scored,
        price,
        cfg={
            "top_holdings": 1,
            "min_notional_krw": 1,
            "gross_exposure": 1.0,
            "gross_exposure_min": 0.0,
            "portfolio_drawdown_ladder_enabled": True,
            "portfolio_drawdown_ladder_thresholds": [-0.08, -0.15],
            "portfolio_drawdown_ladder_scales": [0.70, 0.40],
            "daily_hard_exit_enabled": False,
        },
        initial_cash=10_000,
    )
    assert result.metrics["portfolio_drawdown_ladder_enabled"] is True
    assert result.metrics["min_gross_exposure_effective"] < 1.0
    assert "portfolio_drawdown" in result.daily_nav.columns


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
