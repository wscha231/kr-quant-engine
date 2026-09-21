from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.kr_strict_market_snapshot_v1 import (
    BENCHMARK_BY_MARKET,
    KrStrictMarketError,
    RETURN_BASIS,
    assert_no_zero_imputation,
    compute_snapshot,
    normalized_frame_sha256,
)


def frame(growth: float, periods: int = 281) -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-09-18", periods=periods)
    closes = [100.0 * math.exp(growth * i) for i in range(periods)]
    return pd.DataFrame({"date": dates, "close": closes})


class KrStrictMarketSnapshotV1Tests(unittest.TestCase):
    def test_local_gold_subset_is_pinned_to_upstream_registry(self):
        cfg = json.loads((ROOT / "research" / "cross_market_gold_set_kr_v1.json").read_text())
        self.assertEqual(cfg["schema"], "cross-market-gold-set-kr-v1")
        self.assertEqual(cfg["authority"], "RESEARCH_VALIDATION_ONLY")
        self.assertEqual(len(cfg["candidates"]), 16)
        self.assertEqual(
            cfg["upstream"]["blob_sha"],
            "26006cade37cf5ce37c74abd77d665be50986875",
        )
        self.assertEqual(len({row["asset_id"] for row in cfg["candidates"]}), 16)
        forbidden = {"score", "weight", "rank", "expected_return", "buy", "sell"}
        for row in cfg["candidates"]:
            self.assertTrue(forbidden.isdisjoint(row))

    def test_kospi_uses_kospi200_and_exact_log_rs(self):
        out = compute_snapshot(
            frame(0.0015),
            frame(0.0007),
            asset_id="KR:267260",
            ticker="267260",
            market="KOSPI",
            session_date="2026-09-18",
            available_at="2026-09-18T07:00:00+00:00",
            source_identity="SYNTHETIC_TEST_ONLY",
        )
        self.assertEqual(out["benchmark_ticker"], "1028")
        self.assertEqual(out["benchmark_id"], "KR:KOSPI200")
        self.assertEqual(out["return_basis"], RETURN_BASIS)
        self.assertEqual(out["status"], "UNREVIEWED_CLOSE_PROXY")
        self.assertFalse(out["selector_eligible"])
        self.assertEqual(out["portfolio_weight_effect"], 0.0)
        for h in (20, 60, 120, 240):
            expected = math.log1p(out[f"return_{h}d"]) - math.log1p(out[f"benchmark_return_{h}d"])
            self.assertAlmostEqual(out[f"rs_{h}d"], expected, places=12)
        assert_no_zero_imputation(out)

    def test_kosdaq_uses_kosdaq150(self):
        out = compute_snapshot(
            frame(0.001),
            frame(0.0005),
            asset_id="KR:357780",
            ticker="357780",
            market="KOSDAQ",
            session_date="2026-09-18",
            available_at="2026-09-18T07:00:00+00:00",
            source_identity="SYNTHETIC_TEST_ONLY",
        )
        self.assertEqual(BENCHMARK_BY_MARKET["KOSDAQ"], "2203")
        self.assertEqual(out["benchmark_id"], "KR:KOSDAQ150")

    def test_missing_asset_session_fails_closed(self):
        asset = frame(0.0015)
        missing = asset.drop(index=asset.index[-50]).reset_index(drop=True)
        with self.assertRaisesRegex(KrStrictMarketError, "asset_incomplete_241_session_grid"):
            compute_snapshot(
                missing, frame(0.0007),
                asset_id="KR:267260", ticker="267260", market="KOSPI",
                session_date="2026-09-18",
                available_at="2026-09-18T07:00:00+00:00",
                source_identity="SYNTHETIC_TEST_ONLY",
            )

    def test_insufficient_benchmark_history_fails_closed(self):
        with self.assertRaisesRegex(KrStrictMarketError, "benchmark_insufficient_240d"):
            compute_snapshot(
                frame(0.0015), frame(0.0007, periods=200),
                asset_id="KR:267260", ticker="267260", market="KOSPI",
                session_date="2026-09-18",
                available_at="2026-09-18T07:00:00+00:00",
                source_identity="SYNTHETIC_TEST_ONLY",
            )

    def test_latest_session_must_match_requested_completed_session(self):
        asset = frame(0.0015)
        asset = asset.iloc[:-1].copy()
        with self.assertRaisesRegex(KrStrictMarketError, "asset_latest_session"):
            compute_snapshot(
                asset, frame(0.0007),
                asset_id="KR:267260", ticker="267260", market="KOSPI",
                session_date="2026-09-18",
                available_at="2026-09-18T07:00:00+00:00",
                source_identity="SYNTHETIC_TEST_ONLY",
            )

    def test_duplicate_date_fails_closed(self):
        asset = frame(0.0015)
        asset = pd.concat([asset, asset.iloc[[-1]]], ignore_index=True)
        with self.assertRaisesRegex(KrStrictMarketError, "duplicate_date"):
            compute_snapshot(
                asset, frame(0.0007),
                asset_id="KR:267260", ticker="267260", market="KOSPI",
                session_date="2026-09-18",
                available_at="2026-09-18T07:00:00+00:00",
                source_identity="SYNTHETIC_TEST_ONLY",
            )

    def test_normalized_hash_is_deterministic_and_sensitive(self):
        first = frame(0.0015)
        second = first.copy()
        self.assertEqual(normalized_frame_sha256(first), normalized_frame_sha256(second))
        second.loc[0, "close"] += 1.0
        self.assertNotEqual(normalized_frame_sha256(first), normalized_frame_sha256(second))

    def test_missing_market_field_is_not_neutral_filled(self):
        out = compute_snapshot(
            frame(0.0015), frame(0.0007),
            asset_id="KR:267260", ticker="267260", market="KOSPI",
            session_date="2026-09-18",
            available_at="2026-09-18T07:00:00+00:00",
            source_identity="SYNTHETIC_TEST_ONLY",
        )
        out.pop("rs_120d")
        with self.assertRaisesRegex(KrStrictMarketError, "missing_market_value"):
            assert_no_zero_imputation(out)


if __name__ == "__main__":
    unittest.main()
