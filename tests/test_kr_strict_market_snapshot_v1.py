from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kr_pykrx_client as kr_client

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


    def test_kosdaq150_uses_exact_krx_code_fdr_path_and_source_segregated_cache(self):
        class FakeFdr:
            calls = []

            @classmethod
            def DataReader(cls, symbol, start, end):
                cls.calls.append((symbol, start, end))
                self.assertEqual(symbol, "KRX-INDEX:2203")
                dates = pd.bdate_range(start=start, end=end)
                frame = pd.DataFrame(
                    {
                        "Open": range(100, 100 + len(dates)),
                        "High": range(101, 101 + len(dates)),
                        "Low": range(99, 99 + len(dates)),
                        "Close": range(100, 100 + len(dates)),
                        "Volume": [1000] * len(dates),
                    },
                    index=dates,
                )
                frame.index.name = "Date"
                return frame

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(kr_client, "CACHE_DIR", Path(tmp)),
                patch.object(kr_client, "PYKRX_AVAILABLE", False),
                patch.object(kr_client, "FDR_AVAILABLE", True),
                patch.object(kr_client, "_fdr", FakeFdr),
            ):
                out = kr_client.fetch_index_ohlcv(
                    "2203", "2026-09-01", "2026-09-18", refresh_days=1
                )
                self.assertEqual(
                    out.attrs["source_identity"],
                    "FINANCE_DATAREADER_KRX_INDEX_MDCSTAT00301_2203_NORMALIZED",
                )
                self.assertEqual(len(FakeFdr.calls), 1)
                cache = kr_client.index_cache_path(
                    "2203", "2026-09-01", "2026-09-18"
                )
                self.assertIn("krx-direct-v1", cache.name)
                self.assertTrue(cache.is_file())
                self.assertTrue(kr_client.index_source_meta_path(cache).is_file())

                with patch.object(
                    FakeFdr,
                    "DataReader",
                    side_effect=AssertionError("provider should not be called"),
                ):
                    replay = kr_client.fetch_index_ohlcv(
                        "2203", "2026-09-01", "2026-09-18", refresh_days=1
                    )
                self.assertEqual(
                    replay.attrs["source_identity"],
                    "FINANCE_DATAREADER_KRX_INDEX_MDCSTAT00301_2203_NORMALIZED",
                )

                legacy = Path(tmp) / "index_2203_20260901_20260918.parquet"
                self.assertNotEqual(cache, legacy)

    def test_kosdaq150_falls_back_to_official_krx_openapi_after_fdr_logout(self):
        class LogoutFdr:
            @staticmethod
            def DataReader(_symbol, _start, _end):
                raise ValueError("LOGOUT")

        dates = pd.bdate_range("2026-09-01", "2026-09-18")
        official = pd.DataFrame(
            {
                "date": dates,
                "open": [100.0] * len(dates),
                "high": [101.0] * len(dates),
                "low": [99.0] * len(dates),
                "close": [100.5] * len(dates),
                "volume": [1000.0] * len(dates),
                "value": [100000.0] * len(dates),
                "index_ticker": ["2203"] * len(dates),
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(kr_client, "CACHE_DIR", Path(tmp)),
                patch.object(kr_client, "PYKRX_AVAILABLE", False),
                patch.object(kr_client, "FDR_AVAILABLE", True),
                patch.object(kr_client, "_fdr", LogoutFdr),
                patch.object(
                    kr_client,
                    "_fetch_kosdaq150_openapi",
                    return_value=official,
                ) as fallback,
            ):
                out = kr_client.fetch_index_ohlcv(
                    "2203", "2026-09-01", "2026-09-18", refresh_days=1
                )

        fallback.assert_called_once_with("2026-09-01", "2026-09-18")
        self.assertFalse(out.empty)
        self.assertEqual(
            out.attrs["source_identity"],
            "KRX_OPENAPI_KOSDAQ_DAILY_2203_NORMALIZED",
        )

    def test_ticker_history_requests_pykrx_adjusted_true_and_persists_source_receipt(self):
        class FakeStock:
            calls = []

            @classmethod
            def get_market_ohlcv_by_date(cls, start, end, ticker, adjusted=True):
                cls.calls.append((start, end, ticker, adjusted))
                dates = pd.bdate_range("2026-09-01", "2026-09-18")
                return pd.DataFrame(
                    {
                        "시가": [100] * len(dates),
                        "고가": [101] * len(dates),
                        "저가": [99] * len(dates),
                        "종가": [100] * len(dates),
                        "거래량": [1000] * len(dates),
                        "거래대금": [100000] * len(dates),
                    },
                    index=pd.Index(dates, name="날짜"),
                )

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(kr_client, "CACHE_DIR", Path(tmp)),
                patch.object(kr_client, "PYKRX_AVAILABLE", True),
                patch.object(kr_client, "_pykrx_stock", FakeStock),
                patch.object(kr_client, "FDR_AVAILABLE", False),
            ):
                out = kr_client.fetch_ticker_history(
                    "267260", "2026-09-01", "2026-09-18", refresh_days=1
                )
                self.assertEqual(FakeStock.calls[-1][-1], True)
                self.assertEqual(
                    out.attrs["source_identity"],
                    "PYKRX_ADJUSTED_TRUE_NORMALIZED",
                )
                cache = kr_client.ticker_cache_path(
                    "267260", "2026-09-01", "2026-09-18"
                )
                self.assertIn("adjusted-proxy-v1", cache.name)
                self.assertTrue(cache.is_file())
                self.assertTrue(kr_client.ticker_source_meta_path(cache).is_file())
                legacy = Path(tmp) / "ticker_267260_20260901_20260918.parquet"
                self.assertNotEqual(cache, legacy)

    def test_ticker_history_fdr_fallback_is_explicit_naver_and_remains_proxy(self):
        class FakeFdr:
            calls = []

            @classmethod
            def DataReader(cls, symbol, start, end):
                cls.calls.append((symbol, start, end))
                self.assertEqual(symbol, "NAVER:267260")
                dates = pd.bdate_range(start=start, end=end)
                frame = pd.DataFrame(
                    {
                        "Open": [100] * len(dates),
                        "High": [101] * len(dates),
                        "Low": [99] * len(dates),
                        "Close": [100] * len(dates),
                        "Volume": [1000] * len(dates),
                    },
                    index=dates,
                )
                frame.index.name = "Date"
                return frame

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(kr_client, "CACHE_DIR", Path(tmp)),
                patch.object(kr_client, "PYKRX_AVAILABLE", False),
                patch.object(kr_client, "FDR_AVAILABLE", True),
                patch.object(kr_client, "_fdr", FakeFdr),
            ):
                out = kr_client.fetch_ticker_history(
                    "267260", "2026-09-01", "2026-09-18", refresh_days=1
                )
                self.assertEqual(
                    out.attrs["source_identity"],
                    "FINANCE_DATAREADER_NAVER_CLOSE_PROXY_NORMALIZED",
                )
                meta = json.loads(
                    kr_client.ticker_source_meta_path(
                        kr_client.ticker_cache_path(
                            "267260", "2026-09-01", "2026-09-18"
                        )
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    meta["adjustment_semantics"],
                    "NAVER_CLOSE_PROXY_UNREVIEWED",
                )
                self.assertFalse(meta["a3_reviewed"])
                self.assertFalse(meta["raw_source_claimed"])

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
