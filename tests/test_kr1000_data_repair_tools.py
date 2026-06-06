"""tests/test_kr1000_data_repair_tools.py -- PIT-safe data repair tool tests."""
from __future__ import annotations

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


@_test("mcap carry-forward materialization uses only prior PIT snapshot")
def test_mcap_carry_forward_no_future_source():
    import tools.materialize_mcap_carry_forward_caches as tool

    with tempfile.TemporaryDirectory() as tmp:
        old_root = tool.DATA_ROOT
        try:
            tool.DATA_ROOT = Path(tmp)
            cache = tool.DATA_ROOT / "cache_pykrx"
            cache.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({
                "ticker": ["123"],
                "market": ["KOSPI"],
                "market_cap": [100.0],
                "listed_shares": [10.0],
                "volume": [1.0],
                "value": [1000.0],
                "date": [pd.Timestamp("2025-01-31")],
            }).to_parquet(cache / "mktcap_ALL_20250131.parquet", index=False)

            payload = tool.materialize_carry_forward_caches(
                pd.Timestamp("2025-01-01"),
                pd.Timestamp("2025-02-28"),
                max_carry_days=45,
                dry_run=False,
                rebuild_pit=False,
            )
        finally:
            tool.DATA_ROOT = old_root

        assert len(payload["written"]) == 1
        out = pd.read_parquet(Path(tmp) / "cache_pykrx" / "mktcap_ALL_20250228.parquet")
        assert out.loc[0, "ticker"] == "000123"
        assert pd.Timestamp(out.loc[0, "date"]) == pd.Timestamp("2025-02-28")
        assert out.loc[0, "mcap_snapshot_source"] == "carry_forward"
        assert pd.Timestamp(out.loc[0, "mcap_snapshot_source_date"]) == pd.Timestamp("2025-01-31")
        assert pd.Timestamp(out.loc[0, "mcap_snapshot_true_source_date"]) == pd.Timestamp("2025-01-31")
        assert int(out.loc[0, "mcap_snapshot_carry_days"]) == 28


@_test("mcap carry-forward refuses source outside carry window")
def test_mcap_carry_forward_max_window():
    import tools.materialize_mcap_carry_forward_caches as tool

    with tempfile.TemporaryDirectory() as tmp:
        old_root = tool.DATA_ROOT
        try:
            tool.DATA_ROOT = Path(tmp)
            cache = tool.DATA_ROOT / "cache_pykrx"
            cache.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({
                "ticker": ["000001"],
                "market_cap": [100.0],
            }).to_parquet(cache / "mktcap_ALL_20250131.parquet", index=False)
            payload = tool.materialize_carry_forward_caches(
                pd.Timestamp("2025-01-01"),
                pd.Timestamp("2025-03-31"),
                max_carry_days=30,
                dry_run=False,
                rebuild_pit=False,
            )
        finally:
            tool.DATA_ROOT = old_root

        assert len(payload["written"]) == 1
        assert any(x["date"] == "2025-03-31" and x["reason"] == "carry_window_exceeded" for x in payload["failed"])


@_test("scored-panel fundamental metadata repair clears future period audit rows")
def test_repair_scored_panel_fundamental_metadata():
    from tools.repair_scored_panel_fundamental_metadata import repair_scored_panel_fundamental_metadata

    df = pd.DataFrame({
        "rebalance_date": [pd.Timestamp("2019-05-31"), pd.Timestamp("2020-03-31")],
        "ticker": ["030960", "000001"],
        "fundamentals_period_end": [pd.Timestamp("2019-09-30"), pd.Timestamp("2019-12-31")],
        "fundamentals_rcept_dt": [pd.Timestamp("2019-05-15"), pd.Timestamp("2020-03-15")],
        "fundamentals_bsns_year": [2019, 2019],
        "fundamentals_reprt_code": ["11014", "11011"],
    })
    repaired, stats = repair_scored_panel_fundamental_metadata(df)

    assert stats["before_period_after_rcept"] == 1
    assert stats["before_period_after_signal"] == 1
    assert stats["adjusted_rows"] == 1
    assert stats["after_period_after_rcept"] == 0
    assert stats["after_period_after_signal"] == 0
    assert pd.Timestamp(repaired.loc[0, "fundamentals_period_end"]) == pd.Timestamp("2019-03-31")
    assert bool(repaired.loc[0, "fundamentals_period_end_adjusted_from_calendar"]) is True
    assert repaired.loc[0, "fundamentals_period_end_repair_source"] == "infer_report_period_end"
    assert pd.Timestamp(repaired.loc[1, "fundamentals_period_end"]) == pd.Timestamp("2019-12-31")


@_test("mcap duplicate audit flags distant non-provenanced snapshots")
def test_mcap_duplicate_snapshot_audit_flags_leaky_current_fallback():
    from tools.audit_data_integrity import _audit_mcap_duplicate_snapshots

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = pd.DataFrame({
            "ticker": ["000001", "000002"],
            "market_cap": [100.0, 200.0],
            "listed_shares": [10.0, 20.0],
            "volume": [1.0, 2.0],
            "value": [1000.0, 2000.0],
            "market": ["KOSPI", "KOSDAQ"],
        })
        base.to_parquet(root / "mktcap_ALL_20180131.parquet", index=False)
        base.to_parquet(root / "mktcap_ALL_20250131.parquet", index=False)
        audit = _audit_mcap_duplicate_snapshots(root, max_allowed_span_days=370)
        assert audit["status"] == "failed"
        assert audit["duplicate_groups"]
        assert audit["duplicate_groups"][0]["span_days"] > 370


@_test("mcap duplicate audit allows explicit carry-forward snapshots")
def test_mcap_duplicate_snapshot_audit_allows_carry_forward():
    from tools.audit_data_integrity import _audit_mcap_duplicate_snapshots

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = pd.DataFrame({
            "ticker": ["000001", "000002"],
            "market_cap": [100.0, 200.0],
            "listed_shares": [10.0, 20.0],
            "volume": [1.0, 2.0],
            "value": [1000.0, 2000.0],
            "market": ["KOSPI", "KOSDAQ"],
            "mcap_snapshot_source": ["carry_forward", "carry_forward"],
            "mcap_snapshot_true_source_date": [pd.Timestamp("2025-01-31"), pd.Timestamp("2025-01-31")],
        })
        base.to_parquet(root / "mktcap_ALL_20250131.parquet", index=False)
        base.to_parquet(root / "mktcap_ALL_20260227.parquet", index=False)
        audit = _audit_mcap_duplicate_snapshots(root, max_allowed_span_days=30)
        assert audit["status"] == "passed"


@_test("daily broker readiness blocks missing actual holdings evidence")
def test_daily_broker_check_requires_holdings_file():
    from tools.run_kr1000_daily_broker_check import current_holdings_blockers

    missing = Path("Z:/definitely_missing_current_holdings.csv")
    blockers = current_holdings_blockers(missing, pd.DataFrame(), allow_empty=False)
    assert "current_holdings_file_missing" in blockers
    assert "current_holdings_empty" in blockers
    assert current_holdings_blockers(missing, pd.DataFrame(), allow_empty=True) == []
    zero_share = pd.DataFrame({"ticker": ["000001"], "shares": [0]})
    blockers = current_holdings_blockers(missing, zero_share, allow_empty=False)
    assert "current_holdings_no_positive_shares" in blockers


@_test("current holdings resolver prefers DATA_ROOT state over project fallback")
def test_current_holdings_resolver_prefers_data_root_state():
    from kr1000_leader import resolve_current_holdings_path

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_root = root / "data"
        project_root = root / "project"
        data_state = data_root / "state"
        project_state = project_root / "state"
        data_state.mkdir(parents=True)
        project_state.mkdir(parents=True)
        data_file = data_state / "current_holdings.csv"
        project_file = project_state / "current_holdings.csv"

        assert resolve_current_holdings_path(data_root=data_root, project_root=project_root) == data_file
        project_file.write_text("ticker\n000001\n", encoding="utf-8")
        assert resolve_current_holdings_path(data_root=data_root, project_root=project_root) == project_file
        data_file.write_text("ticker\n000002\n", encoding="utf-8")
        assert resolve_current_holdings_path(data_root=data_root, project_root=project_root) == data_file
        explicit = root / "custom.csv"
        assert resolve_current_holdings_path(explicit, data_root=data_root, project_root=project_root) == explicit


@_test("current holdings import normalizes Korean broker headers")
def test_import_current_holdings_normalizes_korean_headers():
    from tools.import_current_holdings import normalize_holdings_frame

    raw = pd.DataFrame({
        "종목코드": ["005930", "000660", "CASH"],
        "종목명": ["삼성전자", "SK하이닉스", "현금"],
        "보유수량": ["10", "2", ""],
        "매입단가": ["70,000", "180,000", ""],
        "현재가": ["72,000", "190,000", ""],
        "평가금액": ["720,000", "380,000", ""],
        "수익률": ["2.86%", "5.56%", ""],
    })
    out, audit = normalize_holdings_frame(raw, as_of_date="2026-06-04", account_id="main")
    assert audit["summary"]["critical"] == 0
    assert audit["source_rows"] == 3
    assert audit["output_rows"] == 2
    assert set(out["ticker"]) == {"005930", "000660"}
    assert out.loc[out["ticker"] == "005930", "account_id"].iloc[0] == "main"
    assert float(out.loc[out["ticker"] == "005930", "weight"].iloc[0]) > 0.0
    assert abs(float(out.loc[out["ticker"] == "005930", "unrealized_pnl_pct"].iloc[0]) - 0.0286) < 1e-6


@_test("current holdings import fails empty or non-position source")
def test_import_current_holdings_rejects_empty_positions():
    from tools.import_current_holdings import normalize_holdings_frame

    raw = pd.DataFrame({
        "종목코드": ["005930"],
        "보유수량": ["0"],
    })
    out, audit = normalize_holdings_frame(raw, as_of_date="2026-06-04")
    assert out.empty
    assert audit["summary"]["critical"] > 0
    assert any(x["message"] == "current_holdings_empty" for x in audit["issues"])


if __name__ == "__main__":
    print(f"kr1000 data repair tool tests: {PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
