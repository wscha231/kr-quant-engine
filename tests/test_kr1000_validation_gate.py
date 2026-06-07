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
        "cagr": 0.31,
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
    weak["cagr"] = 0.29
    weak_gate = evaluate_backtest_metrics(weak, cfg)
    assert weak_gate["all_pass"] is False
    assert weak_gate["checks"]["cagr"]["pass"] is False


@_test("P_MB production job requires OOS coverage gate")
def test_pmb_job_requires_oos_coverage():
    from kr_config import kr1000_leader_alpha_cfg
    from tools.run_kr1000_validation_gate import evaluate_job_metrics

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
    job = {
        "period": "official_8y",
        "profile": "pmb_pre_surge",
        "strategy_preset": "pmb_defensive_mdd_gate",
    }
    failed = evaluate_job_metrics(metrics, cfg, job, {"status": "failed", "pass": False})
    assert failed["all_pass"] is False
    assert failed["checks"]["pmb_oos_coverage"]["pass"] is False
    passed = evaluate_job_metrics(metrics, cfg, job, {"status": "passed", "pass": True})
    assert passed["all_pass"] is True


@_test("data gate blocks PIT mcap and avg-value coverage gaps")
def test_data_gate_blocks_official_cache_gaps():
    from tools.run_kr1000_validation_gate import _data_gate_blockers

    audit = {
        "summary": {"critical": 0, "high": 3},
        "issues": [
            {"severity": "HIGH", "message": "mktcap cache has month-level gaps >45 days"},
            {"severity": "HIGH", "message": "avg_trading_value cache has gaps >45 days"},
            {"severity": "HIGH", "message": "workflow skipped optional artifact sync"},
        ],
    }
    blockers = _data_gate_blockers(audit)
    assert "data_integrity_mktcap_cache_gap" in blockers
    assert "data_integrity_avg_value_cache_gap" in blockers
    assert "workflow skipped optional artifact sync" not in blockers

    critical = {"summary": {"critical": 1}, "issues": []}
    assert _data_gate_blockers(critical) == ["data_integrity_audit_has_critical"]


@_test("planned validation jobs include official component A/B without duplicating stress profiles")
def test_planned_component_ab_jobs():
    from tools.run_kr1000_validation_gate import _planned_backtests

    args = argparse.Namespace(
        periods="official_8y,stress_2020_2022",
        profiles="full",
        component_ab=True,
        strategy_ab=True,
        initial_cash=100_000_000.0,
        top_holdings=20,
        max_rank_for_prices=20,
        scored_panel=None,
        price_panel=None,
        pmb_oos_picks="research/06_walkforward_baselines/p_mb_v1_oos_picks.csv",
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
        "pmb_pre_entry",
        "pmb_pre_entry_defensive",
        "pmb_pre_entry_blend",
        "pmb_pre_entry_blend_regime",
        "pmb_pullback_recovery_regime",
        "pmb_recovery_trend_value_regime",
        "pmb_mid_rank_7_23",
        "pmb_mid_rank_regime",
        "pmb_mid_tech_regime",
        "hybrid_pmb_rs",
    }
    strategy_jobs = [j for j in official if j.get("strategy_preset") == "pmb_defensive_mdd_gate"]
    assert len(strategy_jobs) == 1
    assert "--portfolio-dd-ladder" in strategy_jobs[0]["cmd"]
    assert "--gross-exposure" in strategy_jobs[0]["cmd"]
    assert "--hard-stop-loss-pct" in strategy_jobs[0]["cmd"]
    mid_rank_jobs = [j for j in official if j.get("strategy_preset") == "pmb_mid_rank_no_leverage_mdd_gate"]
    assert len(mid_rank_jobs) == 1
    assert mid_rank_jobs[0]["profile"] == "pmb_mid_rank_7_23"
    assert "--gross-exposure" in mid_rank_jobs[0]["cmd"]
    assert "1.0" in mid_rank_jobs[0]["cmd"]
    pre_entry_jobs = [j for j in official if j.get("strategy_preset") == "pmb_pre_entry_defensive_mdd_gate"]
    assert len(pre_entry_jobs) == 1
    assert pre_entry_jobs[0]["profile"] == "pmb_pre_entry_defensive"
    assert "--top-holdings" in pre_entry_jobs[0]["cmd"]
    assert "15" in pre_entry_jobs[0]["cmd"]
    assert {j["profile"] for j in stress} == {"full"}
    assert all("--score-profile" in j["cmd"] for j in jobs)
    assert all("--pmb-oos-picks" in j["cmd"] for j in jobs)
    assert all(j["start"] <= j["end"] for j in jobs)


@_test("workflow audit treats full validation gate as broker backtest automation")
def test_workflow_summary_counts_validation_gate_backtests():
    from tools.audit_data_integrity import _workflow_summary

    workflows = _workflow_summary()
    assert any(v.get("runs_kr1000_validation_gate") for v in workflows.values())
    assert any(v.get("runs_kr1000_backtest_via_validation_gate") for v in workflows.values())
    assert any(v.get("runs_kr1000_backtest") for v in workflows.values())


@_test("P_MB OOS coverage gate fails when official 8y months are missing")
def test_pmb_oos_coverage_gate():
    from tools.build_pmb_oos_picks import audit_pmb_oos_coverage

    rows = []
    for rd in pd.date_range("2020-01-31", "2024-12-31", freq="ME"):
        for i in range(20):
            rows.append({
                "rebalance_date": rd,
                "ticker": f"{i:06d}",
                "p_pre_surge": 0.5,
            })
    gate = audit_pmb_oos_coverage(
        pd.DataFrame(rows),
        target_start="2018-01-01",
        target_end="2026-06-04",
        min_covered_years=8.0,
        min_coverage_ratio=0.95,
        min_picks_per_month=20,
    )
    assert gate["pass"] is False
    assert gate["covered_months"] == 60
    assert gate["expected_months"] > gate["covered_months"]
    assert "2018-01" in gate["missing_months"]


@_test("KR1000 backtest runner merges PIT-safe P_MB OOS probabilities")
def test_merge_pmb_oos_predictions():
    from tools.run_kr1000_backtest import merge_pmb_oos_predictions

    panel = pd.DataFrame({
        "rebalance_date": pd.to_datetime(["2024-01-31", "2024-01-31"]),
        "ticker": ["000001", "2"],
        "p_pre_surge": [0.99, 0.0],
        "p_pre_entry": [0.99, 0.0],
    })
    with tempfile.TemporaryDirectory() as tmp:
        picks_path = Path(tmp) / "picks.csv"
        pd.DataFrame({
            "rebalance_date": ["2024-01-31"],
            "ticker": ["000002"],
            "p_pre_surge": [0.77],
            "p_pre_entry": [0.88],
            "p_continuation": [0.11],
            "p_risk": [0.22],
            "p_combined": [0.55],
            "fold_id": [4],
            "rank_in_month": [3],
        }).to_csv(picks_path, index=False)
        out = merge_pmb_oos_predictions(panel, picks_path)
    by_ticker = out.set_index("ticker")
    assert by_ticker.loc["000002", "p_pre_surge"] == 0.77
    assert by_ticker.loc["000002", "p_pre_entry"] == 0.88
    assert by_ticker.loc["000002", "p_continuation"] == 0.11
    assert by_ticker.loc["000002", "p_risk"] == 0.22
    assert by_ticker.loc["000002", "p_combined"] == 0.55
    assert by_ticker.loc["000002", "pmb_oos_rank"] == 3
    assert by_ticker.loc["000002", "pmb_oos_fold_id"] == 4
    assert by_ticker.loc["000001", "p_pre_surge"] == 0.0
    assert by_ticker.loc["000001", "p_pre_entry"] == 0.0


@_test("scored-panel window gate fails on missing monthly signals")
def test_scored_panel_window_gate_requires_monthly_continuity():
    from tools.run_kr1000_validation_gate import _audit_scored_panel_window

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scored.parquet"
        pd.DataFrame({
            "rebalance_date": pd.to_datetime(["2018-01-31", "2018-03-30"]),
            "ticker": ["000001", "000001"],
        }).to_parquet(path, index=False)
        gate = _audit_scored_panel_window(
            path,
            pd.Timestamp("2018-01-01"),
            pd.Timestamp("2018-03-30"),
        )

    assert gate["pass"] is False
    assert gate["reason"] == "scored_panel_missing_months"
    assert gate["missing_month_count"] == 1
    assert gate["missing_months"] == ["2018-02"]


@_test("scored-panel rebuild start defaults to latest cache start in quick mode")
def test_infer_scored_panel_start_date():
    from tools.run_kr1000_validation_gate import _infer_scored_panel_start_date

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = root / "scored_panel_v0_2016-01-01_2024-12-31_old.parquet"
        latest = root / "scored_panel_v0_2019-01-01_2024-12-31_old.parquet"
        old.touch()
        latest.touch()
        assert _infer_scored_panel_start_date(root) == "2019-01-01"


@_test("validation rebuild command widens late cache start to official 2018")
def test_validation_dry_run_widens_rebuild_start():
    from tools.run_kr1000_validation_gate import main as gate_main

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        old_argv = sys.argv[:]
        try:
            sys.argv = [
                "run_kr1000_validation_gate.py",
                "--as-of", "2026-06-04",
                "--rebuild-scored-panel",
                "--dry-run",
                "--out-dir", str(out_dir),
            ]
            rc = gate_main()
        finally:
            sys.argv = old_argv
        assert rc == 0
        import json

        payload = json.loads((out_dir / "kr1000_validation_gate.json").read_text(encoding="utf-8"))
        rebuild = [c for c in payload["planned_commands"] if c["step"] == "rebuild_scored_panel"][0]
        cmd = rebuild["cmd"]
        assert "--start-date" in cmd
        assert cmd[cmd.index("--start-date") + 1] <= "2018-01-01"
        assert "--incremental-max-new-months" not in cmd
        assert "--panel-only" in cmd
        assert "--no-forward-labels" in cmd


if __name__ == "__main__":
    print(f"kr1000 validation gate tests: {PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
