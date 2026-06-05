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


if __name__ == "__main__":
    print(f"kr1000 data-store tests: {PASSED} passed, {FAILED} failed")
    sys.exit(0 if FAILED == 0 else 1)
