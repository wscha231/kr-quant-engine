"""tests/test_kr1000_data_store.py -- KR1000 data-store setup invariants.

Run:
    py -3 tests/test_kr1000_data_store.py
"""
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


@_test("data-store setup creates required directories and example holdings only")
def test_setup_creates_required_dirs():
    from tools.setup_kr1000_data_store import (
        PRIVATE_STATE_FILES,
        REQUIRED_DATA_DIRS,
        build_data_store_manifest,
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "kr_quant_engine_data"
        manifest = build_data_store_manifest(root, create=True)
        assert not manifest["missing_dirs"]
        for name in REQUIRED_DATA_DIRS:
            assert (root / name).is_dir(), name
        assert (root / "state" / "current_holdings.example.csv").exists()
        for name in PRIVATE_STATE_FILES:
            assert not (root / name).exists(), f"must not create private file {name}"


@_test("data-store check-only reports missing directories without creating them")
def test_check_only_missing_dirs():
    from tools.setup_kr1000_data_store import REQUIRED_DATA_DIRS, build_data_store_manifest

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "missing_store"
        manifest = build_data_store_manifest(root, create=False)
        assert set(manifest["missing_dirs"]) == set(REQUIRED_DATA_DIRS)
        assert not root.exists()


@_test("avg-value fallback uses only prior PIT-safe cache within date limit")
def test_avg_value_prior_cache_selection():
    from kr_universe import find_prior_avg_value_cache

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        prior = root / "avg_value_60d_20260529.parquet"
        future = root / "avg_value_60d_20260606.parquet"
        stale = root / "avg_value_60d_20260430.parquet"
        prior.touch()
        future.touch()
        stale.touch()

        selected = find_prior_avg_value_cache(
            pd.Timestamp("2026-06-04"),
            lookback_days=60,
            fallback_max_days=10,
            cache_dir=root,
        )
        assert selected == prior
        assert find_prior_avg_value_cache(
            pd.Timestamp("2026-06-04"),
            lookback_days=60,
            fallback_max_days=3,
            cache_dir=root,
        ) is None


@_test("scored-panel incremental cache ignores future or different-start panels")
def test_incremental_scored_panel_cache_selection():
    from kr_config import KR_ENGINE_REUSE_VERSION
    from kr_pipeline import find_incremental_scored_panel_cache

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        expected = root / f"scored_panel_v0_2016-01-01_2024-12-31_{KR_ENGINE_REUSE_VERSION}.parquet"
        future = root / f"scored_panel_v0_2016-01-01_2026-12-31_{KR_ENGINE_REUSE_VERSION}.parquet"
        wrong_start = root / f"scored_panel_v0_2019-01-01_2026-06-04_{KR_ENGINE_REUSE_VERSION}.parquet"
        older = root / f"scored_panel_v0_2016-01-01_2023-12-31_{KR_ENGINE_REUSE_VERSION}.parquet"
        for path in (expected, future, wrong_start, older):
            path.touch()

        selected = find_incremental_scored_panel_cache(
            "2016-01-01",
            "2026-06-04",
            feature_store=root,
        )
        assert selected == expected


@_test("mktcap value proxy fallback is PIT-safe and date limited")
def test_mktcap_value_proxy_fallback():
    from kr_universe import compute_avg_value_proxy_from_mktcap_cache

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pd.DataFrame({
            "ticker": ["1", "000002"],
            "value": [10_000_000.0, 20_000_000.0],
        }).to_parquet(root / "mktcap_ALL_20260529.parquet", index=False)
        pd.DataFrame({
            "ticker": ["000001"],
            "value": [999_000_000.0],
        }).to_parquet(root / "mktcap_ALL_20260606.parquet", index=False)

        out = compute_avg_value_proxy_from_mktcap_cache(
            pd.Timestamp("2026-06-04"),
            tickers=["000001"],
            fallback_max_days=10,
            cache_dir=root,
        )
        assert len(out) == 1
        assert out.iloc[0]["ticker"] == "000001"
        assert out.iloc[0]["avg_trading_value"] == 10_000_000.0
        assert out.iloc[0]["days_observed"] == 1
        assert compute_avg_value_proxy_from_mktcap_cache(
            pd.Timestamp("2026-06-04"),
            fallback_max_days=3,
            cache_dir=root,
        ).empty


@_test("latest snapshot carries classifier features from prior full rows only")
def test_latest_snapshot_classifier_feature_carry_is_pit_safe():
    from tools.build_latest_kr1000_scored_snapshot import _carry_forward_classifier_features

    latest = pd.DataFrame({
        "rebalance_date": [pd.Timestamp("2026-06-04")],
        "ticker": ["000001"],
        "market_cap": [123.0],
    })
    base = pd.DataFrame({
        "rebalance_date": [
            pd.Timestamp("2024-12-30"),
            pd.Timestamp("2026-06-04"),
        ],
        "ticker": ["000001", "000001"],
        "market_cap": [999.0, 9999.0],
        "roe": [0.20, 0.99],
    })
    out, meta = _carry_forward_classifier_features(
        latest,
        base,
        pd.Timestamp("2026-06-04"),
        ["market_cap", "roe"],
    )
    assert float(out.loc[0, "market_cap"]) == 123.0
    assert float(out.loc[0, "roe"]) == 0.20
    assert meta["carry_source_max_date"] == "2024-12-30"
    assert meta["carried_feature_count"] >= 1


if __name__ == "__main__":
    print(f"kr1000 data-store tests: {PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
