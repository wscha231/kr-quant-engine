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


@_test("index cache sanity audit flags implausible benchmark jumps")
def test_index_cache_sanity_flags_implausible_benchmark_jump():
    from tools.audit_data_integrity import _audit_index_cache_sanity

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dates = pd.date_range("2025-01-02", periods=260, freq="B")
        stable = pd.DataFrame({
            "date": dates,
            "close": [300.0 + i * 0.05 for i in range(len(dates))],
        })
        stable.to_parquet(root / "index_1028_20250102_20251231.parquet", index=False)
        passed = _audit_index_cache_sanity(root, ticker="1028", name="KOSPI200")
        assert passed["status"] == "passed"
        assert not passed["issues"]

        broken = stable.copy()
        broken.loc[broken.index[-2], "close"] = 500.0
        broken.loc[broken.index[-1], "close"] = 2200.0
        broken.to_parquet(root / "index_1028_20250102_20260102.parquet", index=False)
        failed = _audit_index_cache_sanity(root, ticker="1028", name="KOSPI200")
        assert failed["status"] == "failed"
        messages = [x["message"] for x in failed["issues"]]
        assert any("daily returns" in x for x in messages)
        assert any("252-trading-day returns" in x for x in messages)


def _write_mcap_snapshot(path: Path, *, source: str | None = None) -> None:
    data = {
        "ticker": ["000001", "000002"],
        "market_cap": [100.0, 200.0],
        "listed_shares": [10.0, 20.0],
        "volume": [1.0, 2.0],
        "value": [1000.0, 2000.0],
        "market": ["KOSPI", "KOSDAQ"],
    }
    if source is not None:
        data["mcap_snapshot_source"] = [source, source]
    pd.DataFrame(data).to_parquet(path, index=False)


@_test("mcap quarantine dry-run detects suspect mcap and derived avg-value caches")
def test_quarantine_suspect_mcap_caches_dry_run():
    import tools.quarantine_suspect_mcap_caches as tool

    with tempfile.TemporaryDirectory() as tmp:
        old_root = tool.DATA_ROOT
        try:
            tool.DATA_ROOT = Path(tmp)
            cache_pykrx = tool.DATA_ROOT / "cache_pykrx"
            cache_misc = tool.DATA_ROOT / "cache_misc"
            cache_pykrx.mkdir(parents=True, exist_ok=True)
            cache_misc.mkdir(parents=True, exist_ok=True)
            _write_mcap_snapshot(cache_pykrx / "mktcap_ALL_20180131.parquet")
            _write_mcap_snapshot(cache_pykrx / "mktcap_ALL_20250131.parquet")
            pd.DataFrame({
                "ticker": ["000001"],
                "avg_value_60d": [1000.0],
                "avg_value_source": ["mktcap_value_proxy:20180131"],
            }).to_parquet(cache_misc / "avg_value_60d_20180131.parquet", index=False)
            pd.DataFrame({
                "ticker": ["000001"],
                "avg_value_60d": [1000.0],
            }).to_parquet(cache_misc / "avg_value_60d_20250131.parquet", index=False)

            payload = tool.quarantine_suspect_caches(
                max_span_days=370,
                quarantine_dir=tool.DATA_ROOT / "quarantine",
                dry_run=True,
                include_derived_avg_value=True,
                rebuild_pit=False,
            )
        finally:
            tool.DATA_ROOT = old_root

        assert payload["dry_run"] is True
        assert payload["suspect_group_count"] == 1
        assert payload["suspect_snapshot_count"] == 2
        assert len(payload["mcap_moves"]) == 2
        assert len(payload["avg_value_moves"]) == 1
        assert all(x["moved"] is False for x in payload["mcap_moves"])
        assert all(x["moved"] is False for x in payload["avg_value_moves"])
        assert (Path(tmp) / "cache_pykrx" / "mktcap_ALL_20180131.parquet").exists()
        assert (Path(tmp) / "cache_misc" / "avg_value_60d_20180131.parquet").exists()


@_test("mcap quarantine moves suspect caches but preserves price-derived avg-value")
def test_quarantine_suspect_mcap_caches_moves_files():
    import tools.quarantine_suspect_mcap_caches as tool

    with tempfile.TemporaryDirectory() as tmp:
        old_root = tool.DATA_ROOT
        try:
            tool.DATA_ROOT = Path(tmp)
            cache_pykrx = tool.DATA_ROOT / "cache_pykrx"
            cache_misc = tool.DATA_ROOT / "cache_misc"
            pit_dir = tool.DATA_ROOT / "data_pit"
            cache_pykrx.mkdir(parents=True, exist_ok=True)
            cache_misc.mkdir(parents=True, exist_ok=True)
            pit_dir.mkdir(parents=True, exist_ok=True)
            _write_mcap_snapshot(cache_pykrx / "mktcap_ALL_20180131.parquet")
            _write_mcap_snapshot(cache_pykrx / "mktcap_ALL_20250131.parquet")
            pd.DataFrame({"ticker": ["000001"]}).to_parquet(pit_dir / "historical_mcap.parquet", index=False)
            pd.DataFrame({"ticker": ["000001"]}).to_parquet(pit_dir / "listed_history.parquet", index=False)
            pd.DataFrame({
                "ticker": ["000001"],
                "avg_value_60d": [1000.0],
                "avg_value_source": ["mktcap_value_proxy:20180131"],
            }).to_parquet(cache_misc / "avg_value_60d_20180131.parquet", index=False)
            pd.DataFrame({
                "ticker": ["000001"],
                "avg_value_60d": [1000.0],
                "avg_value_source": ["price_ohlcv:20250131"],
            }).to_parquet(cache_misc / "avg_value_60d_20250131.parquet", index=False)

            payload = tool.quarantine_suspect_caches(
                max_span_days=370,
                quarantine_dir=tool.DATA_ROOT / "quarantine",
                dry_run=False,
                include_derived_avg_value=True,
                rebuild_pit=False,
            )
        finally:
            tool.DATA_ROOT = old_root

        assert payload["dry_run"] is False
        assert len(payload["mcap_moves"]) == 2
        assert len(payload["avg_value_moves"]) == 1
        assert len(payload["pit_backups"]) == 0
        assert all(x["moved"] is True for x in payload["mcap_moves"])
        assert all(x["moved"] is True for x in payload["avg_value_moves"])
        assert not (Path(tmp) / "cache_pykrx" / "mktcap_ALL_20180131.parquet").exists()
        assert not (Path(tmp) / "cache_misc" / "avg_value_60d_20180131.parquet").exists()
        assert (Path(tmp) / "cache_misc" / "avg_value_60d_20250131.parquet").exists()
        assert (Path(tmp) / "quarantine" / "cache_pykrx" / "mktcap_ALL_20180131.parquet").exists()
        assert (Path(tmp) / "quarantine" / "cache_misc" / "avg_value_60d_20180131.parquet").exists()


@_test("marcap yearly materializer writes PIT monthly mktcap snapshots")
def test_materialize_mcap_from_marcap_yearly():
    import tools.materialize_mcap_from_marcap_yearly as tool

    with tempfile.TemporaryDirectory() as tmp:
        old_root = tool.DATA_ROOT
        try:
            tool.DATA_ROOT = Path(tmp)
            cache_pykrx = tool.DATA_ROOT / "cache_pykrx"
            cache_pykrx.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({
                "Code": ["1", "2", "3", "1", "2", "4"],
                "Name": ["A", "B", "C", "A", "B", "D"],
                "Marcap": [100.0, 200.0, 300.0, 110.0, 210.0, 410.0],
                "Stocks": [10, 20, 30, 10, 20, 40],
                "Volume": [1, 2, 3, 4, 5, 6],
                "Amount": [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0],
                "Market": ["KOSPI", "KOSDAQ", "KONEX", "KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"],
                "Date": pd.to_datetime([
                    "2025-01-02", "2025-01-02", "2025-01-02",
                    "2025-01-31", "2025-01-31", "2025-01-31",
                ]),
            }).to_parquet(cache_pykrx / "marcap_2025.parquet", index=False)

            payload = tool.materialize_mcap_from_marcap_years(
                2025,
                2025,
                overwrite=False,
                dry_run=False,
                rebuild_pit=False,
            )
        finally:
            tool.DATA_ROOT = old_root

        assert len(payload["written"]) == 1
        out = pd.read_parquet(Path(tmp) / "cache_pykrx" / "mktcap_ALL_20250131.parquet")
        assert set(out["ticker"]) == {"000001", "000002", "000004"}
        assert set(out["market"]) == {"KOSPI", "KOSDAQ"}
        assert float(out.loc[out["ticker"] == "000001", "market_cap"].iloc[0]) == 110.0
        assert out["mcap_snapshot_source"].eq("marcap_yearly").all()
        assert pd.Timestamp(out["mcap_snapshot_true_source_date"].iloc[0]) == pd.Timestamp("2025-01-31")


@_test("marcap yearly materializer respects max-date as-of guard")
def test_materialize_mcap_from_marcap_yearly_max_date():
    import tools.materialize_mcap_from_marcap_yearly as tool

    with tempfile.TemporaryDirectory() as tmp:
        old_root = tool.DATA_ROOT
        try:
            tool.DATA_ROOT = Path(tmp)
            cache_pykrx = tool.DATA_ROOT / "cache_pykrx"
            cache_pykrx.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({
                "Code": ["1", "1", "1"],
                "Name": ["A", "A", "A"],
                "Marcap": [100.0, 110.0, 120.0],
                "Stocks": [10, 10, 10],
                "Volume": [1, 2, 3],
                "Amount": [1000.0, 2000.0, 3000.0],
                "Market": ["KOSPI", "KOSPI", "KOSPI"],
                "Date": pd.to_datetime(["2026-01-30", "2026-02-27", "2026-06-05"]),
            }).to_parquet(cache_pykrx / "marcap_2026.parquet", index=False)

            payload = tool.materialize_mcap_from_marcap_years(
                2026,
                2026,
                overwrite=False,
                dry_run=False,
                rebuild_pit=False,
                max_date="2026-06-04",
            )
        finally:
            tool.DATA_ROOT = old_root

        assert len(payload["written"]) == 2
        assert payload["max_date"] == "2026-06-04"
        assert (Path(tmp) / "cache_pykrx" / "mktcap_ALL_20260130.parquet").exists()
        assert (Path(tmp) / "cache_pykrx" / "mktcap_ALL_20260227.parquet").exists()
        assert not (Path(tmp) / "cache_pykrx" / "mktcap_ALL_20260605.parquet").exists()


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


@_test("daily broker check skips liquidity-only latest snapshots")
def test_daily_broker_check_skips_non_actionable_latest_snapshot():
    from tools.run_kr1000_daily_broker_check import (
        non_actionable_snapshot_reasons,
        select_actionable_signal_snapshot,
    )

    raw = pd.DataFrame({
        "rebalance_date": pd.to_datetime(["2026-05-29", "2026-06-04"]),
        "ticker": ["005930", "005930"],
        "snapshot_build_mode": ["monthly_full_feature", "latest_fast_liquidity_only"],
        "technical_score": [1.0, 0.0],
    })
    reasons = non_actionable_snapshot_reasons(raw[raw["rebalance_date"] == pd.Timestamp("2026-06-04")])
    assert reasons == ["non_actionable_snapshot_build_mode:latest_fast_liquidity_only"]

    signal_date, latest, skipped, visible_latest = select_actionable_signal_snapshot(
        raw,
        "rebalance_date",
        pd.Timestamp("2026-06-04"),
    )
    assert signal_date == pd.Timestamp("2026-05-29")
    assert visible_latest == pd.Timestamp("2026-06-04")
    assert len(latest) == 1
    assert latest["snapshot_build_mode"].iloc[0] == "monthly_full_feature"
    assert skipped == [{
        "signal_date": "2026-06-04",
        "reasons": ["non_actionable_snapshot_build_mode:latest_fast_liquidity_only"],
    }]


@_test("cash benchmark sleeve diagnostic includes cost and stays non-official")
def test_cash_benchmark_sleeve_diagnostic_metrics():
    from tools.analyze_kr1000_cash_benchmark_sleeve import simulate_cash_benchmark_sleeve

    dates = pd.date_range("2024-01-01", periods=30, freq="B")
    nav = pd.DataFrame({
        "date": dates,
        "nav": [100.0 + i * 0.1 for i in range(len(dates))],
        "cash": [50.0 for _ in dates],
    })
    benchmark = pd.Series(
        [100.0 + i * 0.2 for i in range(len(dates))],
        index=dates,
    )
    daily, metrics = simulate_cash_benchmark_sleeve(
        nav,
        benchmark,
        fraction=0.5,
        guard_21d=None,
        cost_bp=10.0,
    )
    assert len(daily) == len(nav)
    assert metrics["diagnostic_only"] is True
    assert metrics["official_broker_ledger_metric"] is False
    assert metrics["total_rebalance_cost_krw"] > 0
    assert metrics["avg_benchmark_sleeve_weight"] > 0


@_test("weekly price overlay builds PIT carried dynamic scores")
def test_weekly_price_overlay_builds_dynamic_scores():
    from tools.build_kr1000_weekly_price_overlay import (
        _signal_dates,
        add_trailing_price_features,
        apply_weekly_overlay_score,
        build_weekly_overlay_panel,
    )

    monthly = pd.DataFrame({
        "rebalance_date": pd.to_datetime(["2024-01-31"] * 3),
        "ticker": ["000001", "000002", "000003"],
        "market_cap": [300.0, 200.0, 100.0],
        "avg_trading_value_60d": [300.0, 200.0, 100.0],
        "valuation_score": [1.0, 0.5, 0.1],
        "flow_score": [0.2, 0.1, 0.0],
        "risk_veto_flag": [0, 0, 0],
        "hard_exit_flag": [0, 0, 0],
    })
    dates = pd.date_range("2023-10-02", periods=100, freq="B")
    prices = []
    for ticker, base, step in [("000001", 100.0, 1.0), ("000002", 100.0, 0.6), ("000003", 100.0, -0.1)]:
        for i, day in enumerate(dates):
            close = base + i * step
            prices.append({
                "date": day,
                "ticker": ticker,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000.0,
                "value": close * 1000.0,
            })
    price_features = add_trailing_price_features(pd.DataFrame(prices))
    signal_dates = _signal_dates(pd.Series(dates), pd.Timestamp("2024-02-01"), pd.Timestamp("2024-02-29"), "W-FRI", 2)
    benchmark = pd.Series([100.0 + i * 0.2 for i in range(len(dates))], index=dates)
    overlay = build_weekly_overlay_panel(monthly, price_features, signal_dates, benchmark)
    scored = apply_weekly_overlay_score(overlay, max_weight=0.10)
    technical_value = apply_weekly_overlay_score(overlay, max_weight=0.10, mode="technical_value")
    assert set(scored["rebalance_date"].dt.normalize().unique()) == set(signal_dates)
    assert "source_monthly_rebalance_date" in scored.columns
    assert pd.to_numeric(scored["leader_rank"], errors="coerce").notna().any()
    assert scored.loc[scored["ticker"] == "000001", "leader_score"].max() > 0
    assert "weekly_price_technical_value_overlay" in set(technical_value["score_profile"])
    assert pd.to_numeric(technical_value["leader_rank"], errors="coerce").notna().any()


@_test("challenger loss-month diagnostic computes benchmark active returns")
def test_challenger_loss_month_diagnostic_active_returns():
    from tools.analyze_kr1000_challenger_loss_months import (
        build_monthly_loss_panel,
        summarize_loss_panel,
    )

    dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
    nav = pd.DataFrame({
        "date": dates,
        "nav": [100.0 + i * 0.1 for i in range(len(dates))],
        "cash": [20.0 for _ in dates],
        "n_holdings": [3 for _ in dates],
        "portfolio_drawdown": [0.0 for _ in dates],
    })
    nav.loc[nav["date"].dt.month == 2, "nav"] -= 8.0
    benchmark = pd.Series([100.0 + i * 0.2 for i in range(len(dates))], index=dates)
    holdings = pd.DataFrame({
        "date": [dates[0], dates[1], dates[-1]],
        "ticker": ["000001", "000002", "000001"],
        "weight": [0.10, 0.08, 0.12],
    })
    trades = pd.DataFrame({
        "execution_date": [dates[3], dates[20]],
        "ticker": ["000001", "000002"],
        "side": ["BUY", "SELL"],
        "trade_value": [1000.0, -500.0],
        "fee_krw": [1.0, 2.0],
        "reason_code": ["BUY_TOP_SCORE", "SELL_RANK_BREAK"],
    })
    monthly = build_monthly_loss_panel(nav, benchmark, holdings, trades)
    summary = summarize_loss_panel(monthly, worst_count=2)
    assert len(monthly) == 3
    assert "active_return" in monthly.columns
    assert summary["months"] == 3
    assert summary["underperform_months"] >= 1
    assert summary["worst_months"][0]["active_return"] == monthly["active_return"].min()
    assert monthly["top_holdings"].astype(str).str.contains("000001").any()


@_test("rank-risk diagnostic compares high and low feature bins")
def test_rank_risk_diagnostic_feature_bins():
    from tools.analyze_kr1000_rank_risk import (
        build_rank_risk_panel,
        summarize_conditions,
        summarize_feature_bins,
        summarize_rank_risk,
    )

    rows = []
    for i in range(30):
        rows.append({
            "rebalance_date": pd.Timestamp("2024-01-31") + pd.offsets.MonthEnd(i // 10),
            "ticker": f"{i + 1:06d}",
            "leader_rank": (i % 10) + 1,
            "forward_return_1m": -0.05 + 0.01 * i,
            "ret_1m": float(i),
            "ret_3m": float(i) / 2.0,
            "rsi_14": 40.0 + i,
            "dist_from_52w_high": -0.20 + 0.01 * i,
            "volume_zscore_50": float(i) / 10.0,
        })
    panel = build_rank_risk_panel(pd.DataFrame(rows), top_n=10)
    bins = summarize_feature_bins(panel, ["ret_1m"], quantile=0.80)
    conditions = summarize_conditions(panel)
    summary = summarize_rank_risk(panel, bins, conditions)

    assert len(panel) == 30
    assert bins.loc[0, "feature"] == "ret_1m"
    assert bins.loc[0, "high_mean_forward_return"] > bins.loc[0, "low_mean_forward_return"]
    assert summary["diagnostic_only"] is True
    assert summary["official_broker_ledger_metric"] is False
    assert "ret_1m_gt_50pct" in set(conditions["condition"])


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
