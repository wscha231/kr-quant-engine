"""Materialize monthly mktcap snapshots from yearly marcap parquet files.

`cache_pykrx/marcap_YYYY.parquet` contains daily Korean market-cap rows with
the actual observation date. This tool converts those PIT daily rows into the
standard monthly `cache_pykrx/mktcap_ALL_YYYYMMDD.parquet` cache format used by
the KR1000 universe builder.

Run:
    py -3 tools/materialize_mcap_from_marcap_yearly.py --start-year 2018 --end-year 2025 --dry-run
    py -3 tools/materialize_mcap_from_marcap_yearly.py --start-year 2018 --end-year 2025
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


MARCAP_REQUIRED_COLUMNS = {
    "Code",
    "Marcap",
    "Stocks",
    "Volume",
    "Amount",
    "Market",
    "Date",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Materialize mktcap monthly caches from marcap yearly files")
    p.add_argument("--start-year", type=int, default=2018)
    p.add_argument("--end-year", type=int, default=None)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-rebuild-pit", action="store_true")
    p.add_argument("--max-date", default=None,
                   help="Optional latest source Date to materialize, preventing as-of leakage for partial current-year files.")
    p.add_argument("--out-dir", default=None)
    return p.parse_args()


def _yyyymmdd(day: pd.Timestamp) -> str:
    return pd.Timestamp(day).strftime("%Y%m%d")


def _marcap_path(year: int) -> Path:
    return DATA_ROOT / "cache_pykrx" / f"marcap_{int(year)}.parquet"


def _mktcap_path(day: pd.Timestamp) -> Path:
    return DATA_ROOT / "cache_pykrx" / f"mktcap_ALL_{_yyyymmdd(day)}.parquet"


def _standardize_marcap_snapshot(df: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    work = df.copy()
    market = work["Market"].astype(str).str.upper().str.strip()
    market = market.where(~market.str.startswith("KOSDAQ"), "KOSDAQ")
    out = pd.DataFrame({
        "ticker": work["Code"].astype(str).str.zfill(6),
        "name": work["Name"].astype(str) if "Name" in work.columns else "",
        "market_cap": pd.to_numeric(work["Marcap"], errors="coerce"),
        "listed_shares": pd.to_numeric(work["Stocks"], errors="coerce"),
        "volume": pd.to_numeric(work["Volume"], errors="coerce"),
        "value": pd.to_numeric(work["Amount"], errors="coerce"),
        "market": market,
        "date": pd.Timestamp(day).normalize(),
    })
    out = out[out["market"].isin(["KOSPI", "KOSDAQ"])].copy()
    out = out.dropna(subset=["ticker", "market_cap"])
    out = out[out["market_cap"] > 0].copy()
    out["mcap_snapshot_source"] = "marcap_yearly"
    out["mcap_snapshot_source_date"] = pd.Timestamp(day).normalize()
    out["mcap_snapshot_true_source_date"] = pd.Timestamp(day).normalize()
    return out.reset_index(drop=True)


def snapshots_from_marcap_year(
    year: int,
    max_date: pd.Timestamp | str | None = None,
) -> list[tuple[pd.Timestamp, pd.DataFrame]]:
    path = _marcap_path(year)
    if not path.exists():
        return []
    df = pd.read_parquet(path)
    missing = sorted(MARCAP_REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"{path.name} missing required columns: {missing}")
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    if max_date is not None:
        max_ts = pd.Timestamp(max_date).normalize()
        df = df[df["Date"].dt.normalize() <= max_ts].copy()
    if df.empty:
        return []
    df["month"] = df["Date"].dt.to_period("M")
    out: list[tuple[pd.Timestamp, pd.DataFrame]] = []
    for _, group in df.groupby("month", sort=True):
        day = pd.Timestamp(group["Date"].max()).normalize()
        snap = _standardize_marcap_snapshot(group[group["Date"] == day], day)
        if not snap.empty:
            out.append((day, snap))
    return out


def materialize_mcap_from_marcap_years(
    start_year: int,
    end_year: int,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
    rebuild_pit: bool = True,
    max_date: pd.Timestamp | str | None = None,
) -> dict[str, Any]:
    max_ts = pd.Timestamp(max_date).normalize() if max_date is not None else None
    payload: dict[str, Any] = {
        "start_year": int(start_year),
        "end_year": int(end_year),
        "max_date": str(max_ts.date()) if max_ts is not None else None,
        "dry_run": bool(dry_run),
        "overwrite": bool(overwrite),
        "planned": [],
        "written": [],
        "skipped": [],
        "failed": [],
    }
    for year in range(int(start_year), int(end_year) + 1):
        path = _marcap_path(year)
        if not path.exists():
            payload["failed"].append({"year": int(year), "reason": "marcap_year_file_missing", "path": str(path)})
            continue
        try:
            snapshots = snapshots_from_marcap_year(year, max_date=max_ts)
        except Exception as exc:
            payload["failed"].append({"year": int(year), "reason": f"{type(exc).__name__}: {exc}", "path": str(path)})
            continue
        for day, snap in snapshots:
            out_path = _mktcap_path(day)
            item = {
                "year": int(year),
                "date": str(day.date()),
                "rows": int(len(snap)),
                "path": str(out_path),
                "source": str(path),
            }
            if out_path.exists() and not overwrite:
                payload["skipped"].append({**item, "reason": "cache_exists"})
                continue
            payload["planned"].append(item)
            if not dry_run:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                snap.to_parquet(out_path, index=False)
                payload["written"].append(item)

    if payload["written"] and rebuild_pit and not dry_run:
        invalidate_pit_caches()
        listed = build_listed_history_from_cache(save=True)
        hist = build_historical_mcap_panel(save=True)
        payload["rebuilt"] = {
            "listed_history_rows": int(len(listed)),
            "historical_mcap_rows": int(len(hist)),
            "historical_mcap_snapshots": int(hist["snapshot_date"].nunique()) if not hist.empty else 0,
            "historical_mcap_min": str(pd.to_datetime(hist["snapshot_date"]).min().date()) if not hist.empty else None,
            "historical_mcap_max": str(pd.to_datetime(hist["snapshot_date"]).max().date()) if not hist.empty else None,
        }
    return payload


def main() -> int:
    args = parse_args()
    end_year = int(args.end_year or pd.Timestamp.today().year)
    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = materialize_mcap_from_marcap_years(
        int(args.start_year),
        end_year,
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
        rebuild_pit=not bool(args.skip_rebuild_pit),
        max_date=args.max_date,
    )
    out_path = out_dir / f"mcap_from_marcap_yearly_{int(args.start_year)}_{end_year}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("KR1000 mcap from marcap yearly materialize")
    print(f"  planned: {len(payload['planned'])}")
    print(f"  written: {len(payload['written'])}")
    print(f"  skipped: {len(payload['skipped'])}")
    print(f"  failed:  {len(payload['failed'])}")
    print(f"  out:     {out_path}")
    return 0 if not payload["failed"] else 2


if __name__ == "__main__":
    sys.exit(main())
