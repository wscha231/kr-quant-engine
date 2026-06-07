"""Materialize PIT-safe avg-value proxy caches from mktcap snapshots.

The expensive true `avg_value_60d_YYYYMMDD.parquet` cache requires per-ticker
OHLCV history. When that cache is missing, the universe builder can already
fall back to `mktcap_ALL_YYYYMMDD.value` as a PIT-safe liquidity proxy. This
tool writes that proxy to cache_misc so validation and rebuild jobs have an
explicit artifact with provenance in `avg_value_source`.

Run:
    py -3 tools/materialize_avg_value_proxy_caches.py --start 2025-01-01 --end 2026-06-04 --dry-run
    py -3 tools/materialize_avg_value_proxy_caches.py --start 2025-01-01 --end 2026-06-04
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402
from kr_universe import compute_avg_value_proxy_from_mktcap_cache  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Materialize avg-value proxy caches")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--lookback-days", type=int, default=60)
    p.add_argument("--fallback-max-days", type=int, default=240)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out-dir", default=None)
    return p.parse_args()


def _yyyymmdd(day: pd.Timestamp) -> str:
    return pd.Timestamp(day).strftime("%Y%m%d")


def _cache_path(day: pd.Timestamp, lookback_days: int) -> Path:
    return DATA_ROOT / "cache_misc" / f"avg_value_{int(lookback_days)}d_{_yyyymmdd(day)}.parquet"


def _business_month_ends(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    months = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), freq="MS")
    out = []
    for month_start in months:
        day = pd.Timestamp(month_start + pd.offsets.BMonthEnd(0)).normalize()
        if day <= end:
            out.append(day)
    return out


def main() -> int:
    args = parse_args()
    start = pd.Timestamp(args.start).normalize()
    end = pd.Timestamp(args.end).normalize() if args.end else pd.Timestamp.today().normalize()
    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "start": str(start.date()),
        "end": str(end.date()),
        "lookback_days": int(args.lookback_days),
        "fallback_max_days": int(args.fallback_max_days),
        "dry_run": bool(args.dry_run),
        "overwrite": bool(args.overwrite),
        "planned": [],
        "written": [],
        "skipped": [],
        "failed": [],
    }

    for day in _business_month_ends(start, end):
        path = _cache_path(day, args.lookback_days)
        if path.exists() and not args.overwrite:
            payload["skipped"].append({"date": str(day.date()), "reason": "cache_exists", "path": str(path)})
            continue

        proxy = compute_avg_value_proxy_from_mktcap_cache(
            day,
            fallback_max_days=int(args.fallback_max_days),
        )
        if proxy.empty:
            payload["failed"].append({"date": str(day.date()), "reason": "no_pit_mktcap_value_proxy"})
            continue

        source = str(proxy["avg_value_source"].iloc[0]) if "avg_value_source" in proxy.columns else ""
        item = {"date": str(day.date()), "rows": int(len(proxy)), "source": source, "path": str(path)}
        payload["planned"].append(item)
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            proxy.to_parquet(path, index=False)
            payload["written"].append(item)

    out_path = out_dir / f"avg_value_proxy_cache_materialize_{_yyyymmdd(end)}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("KR1000 avg-value proxy cache materialize")
    print(f"  planned: {len(payload['planned'])}")
    print(f"  written: {len(payload['written'])}")
    print(f"  skipped: {len(payload['skipped'])}")
    print(f"  failed:  {len(payload['failed'])}")
    print(f"  out:     {out_path}")
    return 0 if not payload["failed"] else 2


if __name__ == "__main__":
    sys.exit(main())
