"""Strict Korean market/RS research snapshot helpers.

Separate from kr_features.add_basic_momentum(): no month-offset windows,
no simple-return RS, and no missing-to-zero fill. Output is UNREVIEWED close
proxy research until corporate-action/return-basis provenance is independently
approved.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pandas as pd

HORIZONS = (20, 60, 120, 240)
SCHEMA = "kr-strict-market-snapshot-v1"
RETURN_BASIS = "KRX_OR_FDR_CLOSE_PROXY_UNREVIEWED"
BENCHMARK_BY_MARKET = {"KOSPI": "1028", "KOSDAQ": "2203"}
BENCHMARK_ID_BY_MARKET = {"KOSPI": "KR:KOSPI200", "KOSDAQ": "KR:KOSDAQ150"}


class KrStrictMarketError(ValueError):
    pass


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise KrStrictMarketError(code)


def _prepare(df: pd.DataFrame, code: str) -> pd.DataFrame:
    _require(isinstance(df, pd.DataFrame) and not df.empty, f"{code}:empty")
    _require("date" in df.columns and "close" in df.columns, f"{code}:schema")
    out = df[["date", "close"]].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    _require(not out["date"].isna().any(), f"{code}:date")
    _require(not out["close"].isna().any(), f"{code}:close")
    _require((out["close"] > 0).all(), f"{code}:positive_close")
    _require(not out["date"].duplicated().any(), f"{code}:duplicate_date")
    out = out.sort_values("date").reset_index(drop=True)
    return out


def normalized_frame_sha256(df: pd.DataFrame) -> str:
    work = _prepare(df, "hash")
    payload = [
        {"date": row.date.date().isoformat(), "close": float(row.close)}
        for row in work.itertuples(index=False)
    ]
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_snapshot(
    ticker_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    *,
    asset_id: str,
    ticker: str,
    market: str,
    session_date: str,
    available_at: str,
    source_identity: str,
) -> dict[str, Any]:
    _require(market in BENCHMARK_BY_MARKET, "market")
    asset = _prepare(ticker_df, "asset")
    bench = _prepare(benchmark_df, "benchmark")
    session = pd.Timestamp(session_date).normalize()

    asset = asset[asset["date"] <= session].copy()
    bench = bench[bench["date"] <= session].copy()
    _require(not asset.empty and not bench.empty, "session_missing")
    _require(asset.iloc[-1]["date"] == session, "asset_latest_session")
    _require(bench.iloc[-1]["date"] == session, "benchmark_latest_session")

    benchmark_sessions = bench["date"].tolist()
    _require(len(benchmark_sessions) >= 241, "benchmark_insufficient_240d")
    required = benchmark_sessions[-241:]
    asset_map = dict(zip(asset["date"], asset["close"]))
    bench_map = dict(zip(bench["date"], bench["close"]))
    _require(all(day in asset_map for day in required), "asset_incomplete_241_session_grid")

    end = required[-1]
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "UNREVIEWED_CLOSE_PROXY",
        "research_only": True,
        "review_required": True,
        "asset_id": asset_id,
        "ticker": ticker,
        "market": market,
        "benchmark_ticker": BENCHMARK_BY_MARKET[market],
        "benchmark_id": BENCHMARK_ID_BY_MARKET[market],
        "completed_session": True,
        "session_date": end.date().isoformat(),
        "available_at": available_at,
        "price": float(asset_map[end]),
        "currency": "KRW",
        "return_basis": RETURN_BASIS,
        "rs_method": "LOG_RELATIVE_RETURN",
        "source_identity": source_identity,
        "asset_normalized_sha256": normalized_frame_sha256(asset),
        "benchmark_normalized_sha256": normalized_frame_sha256(bench),
        "corporate_action_status": "UNREVIEWED",
        "selector_eligible": False,
        "portfolio_weight_effect": 0.0,
        "validated_er_available": False,
        "target_book_write_allowed": False,
        "orders_allowed": False,
        "production_authority": False,
    }

    for horizon in HORIZONS:
        start = required[-1 - horizon]
        asset_ret = float(asset_map[end] / asset_map[start] - 1.0)
        bench_ret = float(bench_map[end] / bench_map[start] - 1.0)
        _require(asset_ret > -1.0 and bench_ret > -1.0, f"total_loss_invalid:{horizon}")
        rs = math.log1p(asset_ret) - math.log1p(bench_ret)
        result[f"return_{horizon}d"] = asset_ret
        result[f"benchmark_return_{horizon}d"] = bench_ret
        result[f"rs_{horizon}d"] = rs

    return result


def assert_no_zero_imputation(snapshot: dict[str, Any]) -> None:
    """Structural guard: required market values must exist as real numbers.

    Legitimate zero returns are allowed; missing keys/None/nonfinite values are not.
    """
    for horizon in HORIZONS:
        for prefix in ("return", "benchmark_return", "rs"):
            key = f"{prefix}_{horizon}d"
            value = snapshot.get(key)
            _require(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value)),
                "missing_market_value:" + key,
            )


__all__ = [
    "BENCHMARK_BY_MARKET",
    "BENCHMARK_ID_BY_MARKET",
    "HORIZONS",
    "KrStrictMarketError",
    "RETURN_BASIS",
    "SCHEMA",
    "assert_no_zero_imputation",
    "compute_snapshot",
    "normalized_frame_sha256",
]
