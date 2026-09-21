#!/usr/bin/env python3
"""Capture strict Korean Gold Set market/RS research snapshots.

Outputs are UNREVIEWED normalized-client evidence. pykrx/FDR provider identity
is unresolved by the existing client, so the exact normalized parquet cache
bytes are copied and hashed instead of being mislabeled as provider raw bytes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kr_config import BENCHMARK_KOSDAQ150, BENCHMARK_KOSPI200
from kr_pykrx_client import CACHE_DIR, fetch_business_days, fetch_index_ohlcv, fetch_ticker_history
from research.kr_strict_market_snapshot_v1 import (
    assert_no_zero_imputation,
    compute_snapshot,
    normalized_frame_sha256,
)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".kr-gold-market-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def repository_bytes(path: Path, repository_path: str, code_sha: str) -> bytes:
    raw = path.read_bytes()
    expected = subprocess.check_output(
        ["git", "show", f"{code_sha}:{repository_path}"],
        cwd=ROOT,
        stderr=subprocess.DEVNULL,
    )
    require(raw == expected, "configuration_differs_from_repository_head")
    return raw


def ymd(value) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def cache_path(kind: str, ticker: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"{kind}_{ticker}_{ymd(start)}_{ymd(end)}.parquet"


def hash_and_copy_cache(path: Path, attempt: Path) -> dict:
    require(path.is_file(), "normalized_cache_missing:" + path.name)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    target = attempt / "normalized_cache" / (digest + ".parquet")
    exclusive(target, raw)
    return {
        "cache_file": path.name,
        "cache_sha256": digest,
        "cache_bytes": len(raw),
        "archived_name": target.name,
    }


def normalized_rows_bytes(df: pd.DataFrame) -> bytes:
    work = df[["date", "close"]].copy()
    work["date"] = pd.to_datetime(work["date"]).dt.strftime("%Y-%m-%d")
    work["close"] = pd.to_numeric(work["close"], errors="raise").astype(float)
    rows = work.sort_values("date").to_dict(orient="records")
    return encoded(rows)


def resolve_completed_session(explicit: str | None) -> pd.Timestamp:
    if explicit:
        candidate = pd.Timestamp(explicit).normalize()
    else:
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        candidate = pd.Timestamp(now.date())
        # Keep a conservative buffer after regular cash close.
        if (now.hour, now.minute) < (16, 30):
            candidate -= pd.Timedelta(days=1)
    start = candidate - pd.Timedelta(days=20)
    days = fetch_business_days(start.strftime("%Y%m%d"), candidate.strftime("%Y%m%d"))
    require(bool(days), "krx_business_calendar_unavailable")
    eligible = [pd.Timestamp(day).normalize() for day in days if pd.Timestamp(day).normalize() <= candidate]
    require(bool(eligible), "no_completed_krx_session")
    return max(eligible)


def source_hashes(code_sha: str) -> dict[str, str]:
    paths = [
        ROOT / "research/kr_strict_market_snapshot_v1.py",
        ROOT / "tools/capture_gold_set_kr_market_v1.py",
        ROOT / "kr_pykrx_client.py",
        ROOT / "kr_config.py",
    ]
    out = {}
    for path in paths:
        relative = str(path.relative_to(ROOT))
        raw = path.read_bytes()
        expected = subprocess.check_output(
            ["git", "show", f"{code_sha}:{relative}"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
        require(raw == expected, "source_differs_from_claimed_commit:" + relative)
        out[relative] = hashlib.sha256(raw).hexdigest()
    return out


def capture(registry: dict, attempt: Path, code_sha: str, explicit_session: str | None) -> dict:
    require(registry.get("schema") == "cross-market-gold-set-kr-v1", "registry_schema")
    candidates = registry.get("candidates")
    require(isinstance(candidates, list) and len(candidates) == 16, "registry_candidate_count")
    session = resolve_completed_session(explicit_session)
    end = session.strftime("%Y%m%d")
    start = (session - pd.Timedelta(days=550)).strftime("%Y%m%d")

    benchmark_frames = {}
    benchmark_receipts = {}
    for benchmark in (BENCHMARK_KOSPI200, BENCHMARK_KOSDAQ150):
        df = fetch_index_ohlcv(benchmark, start, end, refresh_days=1)
        require(not df.empty, "benchmark_fetch_empty:" + benchmark)
        cp = cache_path("index", benchmark, start, end)
        cache_info = hash_and_copy_cache(cp, attempt)
        norm = normalized_rows_bytes(df)
        norm_sha = hashlib.sha256(norm).hexdigest()
        exclusive(attempt / "normalized" / (norm_sha + ".json"), norm)
        benchmark_frames[benchmark] = df
        benchmark_receipts[benchmark] = {
            "benchmark_ticker": benchmark,
            "provider_identity": "UNRESOLVED_PYKRX_OR_FDR",
            "normalized_client": "kr_pykrx_client.fetch_index_ohlcv",
            "normalized_sha256": normalized_frame_sha256(df),
            **cache_info,
        }

    snapshots = []
    receipts = []
    for row in candidates:
        ticker = row["ticker"]
        df = fetch_ticker_history(ticker, start, end, refresh_days=1)
        require(not df.empty, "ticker_fetch_empty:" + ticker)
        cp = cache_path("ticker", ticker, start, end)
        cache_info = hash_and_copy_cache(cp, attempt)
        norm = normalized_rows_bytes(df)
        norm_sha = hashlib.sha256(norm).hexdigest()
        exclusive(attempt / "normalized" / (norm_sha + ".json"), norm)

        benchmark = row["benchmark_ticker"]
        require(benchmark in benchmark_frames, "unknown_benchmark_ticker")
        snapshot = compute_snapshot(
            df,
            benchmark_frames[benchmark],
            asset_id=row["asset_id"],
            ticker=ticker,
            market=row["market"],
            session_date=session.date().isoformat(),
            available_at=datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
            source_identity="KR_CLIENT_NORMALIZED_CACHE_UNRESOLVED_PROVIDER",
        )
        assert_no_zero_imputation(snapshot)
        snapshots.append(snapshot)
        receipts.append({
            "asset_id": row["asset_id"],
            "ticker": ticker,
            "market": row["market"],
            "provider_identity": "UNRESOLVED_PYKRX_OR_FDR",
            "normalized_client": "kr_pykrx_client.fetch_ticker_history",
            "normalized_sha256": normalized_frame_sha256(df),
            **cache_info,
        })

    manifest = {
        "schema": "gold-set-kr-market-capture-v1",
        "status": "COMPLETE_UNREVIEWED_NORMALIZED",
        "research_only": True,
        "review_required": True,
        "session_date": session.date().isoformat(),
        "captured_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "registry_upstream": registry["upstream"],
        "provider_identity": "UNRESOLVED_PYKRX_OR_FDR",
        "return_basis": "KRX_OR_FDR_CLOSE_PROXY_UNREVIEWED",
        "snapshots": snapshots,
        "ticker_receipts": receipts,
        "benchmark_receipts": benchmark_receipts,
        "code_sha": code_sha,
        "code_file_sha256": source_hashes(code_sha),
        "selector_eligible": False,
        "portfolio_weight_effect": 0.0,
        "validated_er_available": False,
        "target_book_write_allowed": False,
        "orders_allowed": False,
        "production_authority": False,
    }
    require(len({row["asset_id"] for row in snapshots}) == 16, "incomplete_gold_set_capture")
    exclusive(attempt / "capture_manifest.json", encoded(manifest))
    exclusive(attempt / "ticker_receipts.json", encoded(receipts))
    exclusive(attempt / "benchmark_receipts.json", encoded(benchmark_receipts))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=ROOT / "research/cross_market_gold_set_kr_v1.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--session", help="Explicit completed KRX session YYYY-MM-DD")
    args = parser.parse_args()
    require(args.attempt_id and all(ch.isalnum() or ch in "-_" for ch in args.attempt_id), "attempt_id")

    code_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    raw_registry = repository_bytes(args.registry, "research/cross_market_gold_set_kr_v1.json", code_sha)
    registry = json.loads(raw_registry)
    attempt = args.output_dir / "attempts" / args.attempt_id
    attempt.mkdir(parents=True, exist_ok=False)
    try:
        manifest = capture(registry, attempt, code_sha, args.session)
        receipt = {
            "schema": "gold-set-kr-market-capture-receipt-v1",
            "attempt_id": args.attempt_id,
            "status": manifest["status"],
            "session_date": manifest["session_date"],
            "manifest_sha256": hashlib.sha256((attempt / "capture_manifest.json").read_bytes()).hexdigest(),
            "review_required": True,
            "consumable_as_reviewed_a3_market_snapshot": False,
        }
        exclusive(attempt / "receipt.json", encoded(receipt))
        atomic(args.output_dir / "latest_attempt.json", encoded(receipt))
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        blocked = {
            "schema": "gold-set-kr-market-capture-receipt-v1",
            "attempt_id": args.attempt_id,
            "status": "BLOCKED",
            "reason": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__,
            "review_required": True,
            "consumable_as_reviewed_a3_market_snapshot": False,
        }
        atomic(args.output_dir / "latest_attempt.json", encoded(blocked))
        print(json.dumps(blocked, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
