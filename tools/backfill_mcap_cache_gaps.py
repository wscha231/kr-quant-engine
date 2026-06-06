"""Backfill missing monthly PIT mcap snapshots without current-list fallback.

This tool is intentionally narrower than `refresh_kr1000_daily_data.py`.
It fills historical `cache_pykrx/mktcap_ALL_YYYYMMDD.parquet` gaps using pykrx
only, then rebuilds `data_pit/historical_mcap.parquet` and
`data_pit/listed_history.parquet` from the cache set.

Run:
    py -3 tools/backfill_mcap_cache_gaps.py --start 2016-01-01 --end 2026-06-04 --dry-run
    py -3 tools/backfill_mcap_cache_gaps.py --start 2016-01-01 --end 2026-06-04
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
from kr_helpers import log  # noqa: E402
from kr_pit_universe import (  # noqa: E402
    build_historical_mcap_panel,
    build_listed_history_from_cache,
    invalidate_pit_caches,
)
from kr_pykrx_client import fetch_market_cap_market  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill historical mcap cache gaps")
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--refresh-days", type=int, default=3650)
    p.add_argument("--max-dates", type=int, default=0,
                   help="Limit fetch count for smoke/debug. 0=all missing month-ends.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out-dir", default=None)
    return p.parse_args()


def _yyyymmdd(day: pd.Timestamp) -> str:
    return pd.Timestamp(day).strftime("%Y%m%d")


def _cache_path(day: pd.Timestamp) -> Path:
    return DATA_ROOT / "cache_pykrx" / f"mktcap_ALL_{_yyyymmdd(day)}.parquet"


def _business_month_end(month_start: pd.Timestamp) -> pd.Timestamp:
    month_start = pd.Timestamp(month_start).normalize()
    return pd.Timestamp(month_start + pd.offsets.BMonthEnd(0)).normalize()


def missing_month_end_dates(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    months = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), freq="MS")
    dates = []
    for month_start in months:
        bme = _business_month_end(month_start)
        if bme > end:
            continue
        if not _cache_path(bme).exists():
            dates.append(bme)
    return dates


def main() -> int:
    args = parse_args()
    start = pd.Timestamp(args.start).normalize()
    end = pd.Timestamp(args.end).normalize() if args.end else pd.Timestamp.today().normalize()
    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    missing = missing_month_end_dates(start, end)
    if args.max_dates and args.max_dates > 0:
        missing = missing[: int(args.max_dates)]

    payload: dict[str, Any] = {
        "start": str(start.date()),
        "end": str(end.date()),
        "dry_run": bool(args.dry_run),
        "missing_dates": [str(d.date()) for d in missing],
        "fetched": [],
        "failed": [],
    }

    if not args.dry_run:
        for day in missing:
            cache = _cache_path(day)
            log(f"[mcap-backfill] fetching {day.date()} with FDR fallback disabled")
            df = fetch_market_cap_market(
                _yyyymmdd(day),
                market="ALL",
                refresh_days=int(args.refresh_days),
                allow_fdr_fallback=False,
            )
            if df.empty:
                if cache.exists():
                    cache.unlink()
                payload["failed"].append({"date": str(day.date()), "reason": "empty_pykrx_result"})
                continue
            payload["fetched"].append({"date": str(day.date()), "rows": int(len(df))})

        if payload["fetched"]:
            invalidate_pit_caches()
            listed = build_listed_history_from_cache(save=True)
            hist = build_historical_mcap_panel(save=True)
            payload["rebuilt"] = {
                "listed_history_rows": int(len(listed)),
                "historical_mcap_rows": int(len(hist)),
                "historical_mcap_snapshots": int(hist["snapshot_date"].nunique()) if not hist.empty else 0,
            }

    out_path = out_dir / f"mcap_cache_gap_backfill_{_yyyymmdd(end)}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("KR1000 mcap cache gap backfill")
    print(f"  missing: {len(missing)}")
    print(f"  fetched: {len(payload['fetched'])}")
    print(f"  failed:  {len(payload['failed'])}")
    print(f"  out:     {out_path}")
    return 0 if not payload["failed"] else 2


if __name__ == "__main__":
    sys.exit(main())
