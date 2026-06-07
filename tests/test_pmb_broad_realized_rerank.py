"""tests/test_pmb_broad_realized_rerank.py -- broad realized P_MB reranker.

Run:
    py -3 tests/test_pmb_broad_realized_rerank.py
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


def _sample_panel(months: int = 14, tickers: int = 60) -> pd.DataFrame:
    rows = []
    for day in pd.date_range("2020-01-31", periods=months, freq="ME"):
        for i in range(tickers):
            good = i < tickers // 3
            rows.append({
                "rebalance_date": day,
                "ticker": f"{i:06d}",
                "name": f"N{i}",
                "market_cap": 1_000_000_000 + i * 1000,
                "avg_trading_value_60d": 100_000_000 + i,
                "in_kr1000": True,
                "ret_1m": 0.05 if good else -0.02,
                "ret_3m": 0.10 if good else -0.05,
                "ret_6m": 0.15 if good else -0.10,
                "rs_3m": 0.04 if good else -0.04,
                "technical_score": 0.5 if good else -0.5,
                "flow_score": 0.2 if good else -0.2,
            })
    return pd.DataFrame(rows)


def _sample_prices(months: int = 16, tickers: int = 60) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=months * 23)
    rows = []
    for i in range(tickers):
        good = i < tickers // 3
        px = 100.0 + i
        for d in dates:
            px *= 1.0015 if good else 0.9985
            rows.append({"date": d, "ticker": f"{i:06d}", "close": px})
    return pd.DataFrame(rows)


@_test("broad realized labels use next close and next signal exit")
def test_add_realized_next_rebalance_returns():
    from tools.build_pmb_broad_realized_rerank_picks import add_realized_next_rebalance_returns

    panel = pd.DataFrame({
        "rebalance_date": pd.to_datetime(["2024-01-31", "2024-02-29"]),
        "ticker": ["000001", "000001"],
    })
    prices = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-31", "2024-02-01", "2024-02-15", "2024-03-01"]),
        "ticker": ["000001"] * 4,
        "close": [90.0, 100.0, 80.0, 120.0],
    })
    out = add_realized_next_rebalance_returns(panel, prices)
    assert abs(float(out.loc[0, "analysis_return"]) - 0.20) < 1e-9
    assert abs(float(out.loc[0, "analysis_min_return"]) - (-0.20)) < 1e-9
    assert pd.isna(out.loc[1, "analysis_return"])


@_test("broad reranker trains only on pre-embargo broad rows")
def test_broad_reranker_uses_pre_embargo_broad_rows():
    from tools.build_pmb_broad_realized_rerank_picks import build_broad_realized_rerank_picks

    panel = _sample_panel()
    prices = _sample_prices()
    pmb = panel[panel["ticker"].astype(int) < 30][["rebalance_date", "ticker", "name", "market_cap"]].copy()
    pmb["p_pre_surge"] = 0.5
    pmb["p_pre_entry"] = 0.5
    pmb["p_continuation"] = 0.1
    pmb["p_risk"] = 0.1
    pmb["p_combined"] = 0.5
    pmb["rank_in_month"] = pmb.groupby("rebalance_date").cumcount() + 1

    picks, audit = build_broad_realized_rerank_picks(
        panel,
        prices,
        pmb,
        target_start="2020-01-01",
        target_end="2021-02-28",
        k_per_month=10,
        embargo_months=2,
        min_train_rows=120,
        pmb_weight=0.02,
        risk_penalty=0.05,
    )
    assert not picks.empty
    modeled = [f for f in audit["folds"] if f["mode"] == "broad_realized_model"]
    assert modeled
    for fold in modeled:
        train_end = pd.Timestamp(fold["train_end"])
        test_day = pd.Timestamp(fold["rebalance_date"])
        assert train_end <= test_day - pd.DateOffset(months=2)
    assert picks.groupby("rebalance_date")["rank_in_month"].max().max() <= 10
    assert audit["broad_observed_rows"] > len(picks)


if __name__ == "__main__":
    print("=" * 60)
    print("P_MB broad realized rerank tests")
    print("=" * 60)
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
