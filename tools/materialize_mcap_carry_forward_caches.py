"""Materialize PIT-safe carried-forward mcap snapshots for cache gaps.

When pykrx cannot return a historical month-end mcap snapshot, using a later
snapshot would leak future membership and size information. This tool fills
missing `mktcap_ALL_YYYYMMDD.parquet` files by carrying forward the latest
available prior snapshot only, with explicit provenance columns.

Run:
    py -3 tools/materialize_mcap_carry_forward_caches.py --start 2016-01-01 --end 2026-06-04 --dry-run
    py -3 tools/materialize_mcap_carry_forward_caches.py --start 2016-01-01 --end 2026-06-04
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
from kr_pit_universe import (  # noqa: E402
    build_historical_mcap_panel,
    build_listed_history_from_cache,
    invalidate_pit_caches,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Materialize PIT-safe carried-forward mcap caches")
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--max-carry-days", type=int, default=240)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-rebuild-pit", action="store_true")
    p.add_argument("--out-dir", default=None)
    return p.parse_args()


def _yyyymmdd(day: pd.Timestamp) -> str:
    return pd.Timestamp(day).strftime("%Y%m%d")


def _cache_path(day: pd.Timestamp) -> Path:
    return DATA_ROOT / "cache_pykrx" / f"mktcap_ALL_{_yyyymmdd(day)}.parquet"


def _business_month_ends(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    months = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), freq="MS")
    out: list[pd.Timestamp] = []
    for month_start in months:
        day = pd.Timestamp(month_start + pd.offsets.BMonthEnd(0)).normalize()
        if day <= end:
            out.append(day)
    return out


def _existing_mcap_dates() -> list[pd.Timestamp]:
    cache_dir = DATA_ROOT / "cache_pykrx"
    dates: list[pd.Timestamp] = []
    if not cache_dir.exists():
        return dates
    for path in cache_dir.glob("mktcap_ALL_*.parquet"):
        try:
            dates.append(pd.Timestamp(path.stem.rsplit("_", 1)[-1]))
        except Exception:
            continue
    return sorted(set(dates))


def _true_source_date(df: pd.DataFrame, source_date: pd.Timestamp) -> pd.Timestamp:
    if "mcap_snapshot_true_source_date" in df.columns:
        vals = pd.to_datetime(df["mcap_snapshot_true_source_date"], errors="coerce").dropna()
        if not vals.empty:
            return pd.Timestamp(vals.min()).normalize()
    if "mcap_snapshot_source_date" in df.columns:
        vals = pd.to_datetime(df["mcap_snapshot_source_date"], errors="coerce").dropna()
        if not vals.empty:
            return pd.Timestamp(vals.min()).normalize()
    return pd.Timestamp(source_date).normalize()


def _latest_prior_source(target: pd.Timestamp, existing_dates: list[pd.Timestamp]) -> pd.Timestamp | None:
    prior = [d for d in existing_dates if d < target]
    return max(prior) if prior else None


def materialize_carry_forward_caches(
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    max_carry_days: int = 240,
    overwrite: bool = False,
    dry_run: bool = False,
    rebuild_pit: bool = True,
) -> dict[str, Any]:
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    existing_dates = _existing_mcap_dates()
    payload: dict[str, Any] = {
        "start": str(start.date()),
        "end": str(end.date()),
        "max_carry_days": int(max_carry_days),
        "dry_run": bool(dry_run),
        "overwrite": bool(overwrite),
        "rebuild_pit": bool(rebuild_pit),
        "planned": [],
        "written": [],
        "skipped": [],
        "failed": [],
    }
    source_frames: dict[pd.Timestamp, pd.DataFrame] = {}

    for day in _business_month_ends(start, end):
        path = _cache_path(day)
        if path.exists() and not overwrite:
            payload["skipped"].append({"date": str(day.date()), "reason": "cache_exists", "path": str(path)})
            continue

        source_date = _latest_prior_source(day, existing_dates)
        if source_date is None:
            payload["failed"].append({"date": str(day.date()), "reason": "no_prior_pit_mcap_snapshot"})
            continue

        source_path = _cache_path(source_date)
        if dry_run:
            carry_days = int((day - source_date).days)
            if carry_days > int(max_carry_days):
                payload["failed"].append({
                    "date": str(day.date()),
                    "reason": "carry_window_exceeded",
                    "source_date": str(source_date.date()),
                    "true_source_date": str(source_date.date()),
                    "carry_days": carry_days,
                })
                continue
            payload["planned"].append({
                "date": str(day.date()),
                "rows": None,
                "source_date": str(source_date.date()),
                "true_source_date": str(source_date.date()),
                "carry_days": carry_days,
                "path": str(path),
            })
            continue

        try:
            source = source_frames.get(source_date)
            if source is None:
                source = pd.read_parquet(source_path)
                source_frames[source_date] = source
        except Exception as exc:
            payload["failed"].append({
                "date": str(day.date()),
                "reason": "source_read_failed",
                "source_date": str(source_date.date()),
                "error": str(exc),
            })
            continue
        if source.empty:
            payload["failed"].append({
                "date": str(day.date()),
                "reason": "source_empty",
                "source_date": str(source_date.date()),
            })
            continue

        true_source_date = _true_source_date(source, source_date)
        carry_days = int((day - true_source_date).days)
        if carry_days > int(max_carry_days):
            payload["failed"].append({
                "date": str(day.date()),
                "reason": "carry_window_exceeded",
                "source_date": str(source_date.date()),
                "true_source_date": str(true_source_date.date()),
                "carry_days": carry_days,
            })
            continue

        item = {
            "date": str(day.date()),
            "rows": int(len(source)),
            "source_date": str(source_date.date()),
            "true_source_date": str(true_source_date.date()),
            "carry_days": carry_days,
            "path": str(path),
        }
        payload["planned"].append(item)

        out = source.copy()
        if "ticker" in out.columns:
            out["ticker"] = out["ticker"].astype(str).str.zfill(6)
        out["date"] = day
        out["mcap_snapshot_source"] = "carry_forward"
        out["mcap_snapshot_source_date"] = source_date
        out["mcap_snapshot_true_source_date"] = true_source_date
        out["mcap_snapshot_carry_days"] = carry_days
        path.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(path, index=False)
        payload["written"].append(item)
        source_frames[day] = out
        existing_dates = sorted(set(existing_dates + [day]))

    if payload["written"] and rebuild_pit and not dry_run:
        invalidate_pit_caches()
        listed = build_listed_history_from_cache(save=True)
        hist = build_historical_mcap_panel(save=True)
        payload["rebuilt"] = {
            "listed_history_rows": int(len(listed)),
            "historical_mcap_rows": int(len(hist)),
            "historical_mcap_snapshots": int(hist["snapshot_date"].nunique()) if not hist.empty else 0,
        }

    return payload


def main() -> int:
    args = parse_args()
    start = pd.Timestamp(args.start).normalize()
    end = pd.Timestamp(args.end).normalize() if args.end else pd.Timestamp.today().normalize()
    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = materialize_carry_forward_caches(
        start,
        end,
        max_carry_days=int(args.max_carry_days),
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
        rebuild_pit=not bool(args.skip_rebuild_pit),
    )
    out_path = out_dir / f"mcap_carry_forward_cache_materialize_{_yyyymmdd(end)}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print("KR1000 mcap carry-forward cache materialize")
    print(f"  planned: {len(payload['planned'])}")
    print(f"  written: {len(payload['written'])}")
    print(f"  skipped: {len(payload['skipped'])}")
    print(f"  failed:  {len(payload['failed'])}")
    print(f"  out:     {out_path}")
    return 0 if not payload["failed"] else 2


if __name__ == "__main__":
    sys.exit(main())
