"""Quarantine mcap cache snapshots that look like historical current-list leaks.

The data-integrity audit flags distant identical `mktcap_ALL_YYYYMMDD.parquet`
snapshots without explicit carry-forward provenance. Those files should not be
used for PIT universe membership. This tool moves the suspect cache files into
`DATA_ROOT/data_quarantine/...`, optionally moves mktcap-proxy avg-value caches
derived from the same suspect dates, and rebuilds PIT membership artifacts from
the remaining cache files.

Run:
    py -3 tools/quarantine_suspect_mcap_caches.py --dry-run
    py -3 tools/quarantine_suspect_mcap_caches.py
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402

try:  # noqa: E402
    import pyarrow.parquet as pq
except Exception:  # pragma: no cover - pandas parquet fallback still works.
    pq = None


VALUE_COLS = ["ticker", "market_cap", "listed_shares", "volume", "value", "market"]
MCAP_PATTERN = re.compile(r"^mktcap_ALL_(\d{8})\.parquet$")
AVG_VALUE_PATTERN = re.compile(r"^avg_value_\d+d_(\d{8})\.parquet$")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Quarantine suspect duplicate mcap caches")
    p.add_argument("--max-span-days", type=int, default=370,
                   help="Flag identical snapshots whose first/last date span exceeds this many days.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-derived-avg-value", action="store_true")
    p.add_argument("--skip-rebuild-pit", action="store_true")
    p.add_argument("--quarantine-dir", default=None,
                   help="Optional explicit quarantine directory.")
    p.add_argument("--out-dir", default=None)
    return p.parse_args()


def _yyyymmdd(day: pd.Timestamp | None = None) -> str:
    return pd.Timestamp(day or pd.Timestamp.now()).strftime("%Y%m%d")


def _read_mcap_for_fingerprint(path: Path) -> pd.DataFrame:
    wanted = VALUE_COLS + [
        "mcap_snapshot_source",
        "mcap_snapshot_true_source_date",
    ]
    if pq is not None:
        try:
            names = set(pq.ParquetFile(path).schema_arrow.names)
            cols = [c for c in wanted if c in names]
            return pd.read_parquet(path, columns=cols)
        except Exception:
            pass
    try:
        df = pd.read_parquet(path, columns=wanted)
    except Exception:
        df = pd.read_parquet(path)
        df = df[[c for c in wanted if c in df.columns]]
    return df


def _fingerprint_mcap(df: pd.DataFrame) -> int | None:
    cols = [c for c in VALUE_COLS if c in df.columns]
    if df.empty or "ticker" not in df.columns or len(cols) < 3:
        return None
    work = df[cols].copy()
    work["ticker"] = work["ticker"].astype(str).str.zfill(6)
    work = work.sort_values("ticker").reset_index(drop=True)
    return int(pd.util.hash_pandas_object(work, index=False).sum())


def _snapshot_source(df: pd.DataFrame) -> str:
    if "mcap_snapshot_source" not in df.columns:
        return ""
    vals = df["mcap_snapshot_source"].dropna().astype(str)
    return vals.mode().iloc[0] if not vals.empty else ""


def _true_source_date(df: pd.DataFrame) -> str | None:
    if "mcap_snapshot_true_source_date" not in df.columns:
        return None
    vals = pd.to_datetime(df["mcap_snapshot_true_source_date"], errors="coerce").dropna()
    return str(vals.min().date()) if not vals.empty else None


def scan_suspect_mcap_groups(
    cache_dir: Path,
    *,
    max_span_days: int = 370,
) -> list[dict[str, Any]]:
    """Return full suspect mcap duplicate groups with file paths."""
    groups: dict[int, list[dict[str, Any]]] = {}
    if not cache_dir.exists():
        return []
    for path in sorted(cache_dir.glob("mktcap_ALL_*.parquet")):
        match = MCAP_PATTERN.match(path.name)
        if not match:
            continue
        try:
            df = _read_mcap_for_fingerprint(path)
        except Exception:
            continue
        fp = _fingerprint_mcap(df)
        if fp is None:
            continue
        groups.setdefault(fp, []).append({
            "date": pd.Timestamp(match.group(1)).normalize(),
            "date_key": match.group(1),
            "path": path,
            "name": path.name,
            "rows": int(len(df)),
            "source": _snapshot_source(df),
            "true_source_date": _true_source_date(df),
            "fingerprint": fp,
        })

    suspect: list[dict[str, Any]] = []
    for fp, items in groups.items():
        if len(items) < 2:
            continue
        dates = sorted(pd.Timestamp(x["date"]).normalize() for x in items)
        span_days = int((dates[-1] - dates[0]).days)
        if span_days <= int(max_span_days):
            continue
        all_carried = all(str(x.get("source") or "") == "carry_forward" for x in items)
        if all_carried:
            continue
        suspect.append({
            "fingerprint": fp,
            "first_date": str(dates[0].date()),
            "last_date": str(dates[-1].date()),
            "span_days": span_days,
            "snapshot_count": int(len(items)),
            "rows": int(max(int(x.get("rows", 0)) for x in items)),
            "items": sorted(items, key=lambda x: x["date"]),
        })
    return sorted(suspect, key=lambda g: (-int(g["snapshot_count"]), g["first_date"]))


def _avg_value_uses_suspect_mcap(path: Path, suspect_dates: set[str]) -> bool:
    """True only for avg-value caches explicitly derived from suspect mcap."""
    match = AVG_VALUE_PATTERN.match(path.name)
    if not match:
        return False
    if match.group(1) not in suspect_dates:
        return False
    try:
        df = pd.read_parquet(path, columns=["avg_value_source"])
    except Exception:
        return False
    if df.empty or "avg_value_source" not in df.columns:
        return False
    src = df["avg_value_source"].dropna().astype(str)
    if src.empty:
        return False
    for value in src.unique():
        if not value.startswith("mktcap_value_proxy:"):
            continue
        source_date = value.rsplit(":", 1)[-1]
        if source_date in suspect_dates or match.group(1) in suspect_dates:
            return True
    return False


def find_derived_avg_value_caches(cache_misc: Path, suspect_dates: set[str]) -> list[Path]:
    if not cache_misc.exists():
        return []
    out: list[Path] = []
    for path in sorted(cache_misc.glob("avg_value_*d_*.parquet")):
        if _avg_value_uses_suspect_mcap(path, suspect_dates):
            out.append(path)
    return out


def _safe_target(base: Path, rel: Path) -> Path:
    target = base / rel
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for i in range(1, 10_000):
        candidate = target.with_name(f"{stem}.{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not allocate quarantine target for {target}")


def _move_file(path: Path, quarantine_root: Path, rel_parent: str, dry_run: bool) -> dict[str, Any]:
    rel = Path(rel_parent) / path.name
    target = _safe_target(quarantine_root, rel)
    item = {"source": str(path), "target": str(target), "moved": False}
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        item["moved"] = True
    return item


def _backup_pit_files(quarantine_root: Path, dry_run: bool) -> list[dict[str, Any]]:
    pit_dir = DATA_ROOT / "data_pit"
    out: list[dict[str, Any]] = []
    for name in ("historical_mcap.parquet", "listed_history.parquet"):
        path = pit_dir / name
        if not path.exists():
            continue
        target = _safe_target(quarantine_root, Path("data_pit_pre_rebuild") / name)
        item = {"source": str(path), "target": str(target), "copied": False}
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            item["copied"] = True
        out.append(item)
    return out


def quarantine_suspect_caches(
    *,
    max_span_days: int = 370,
    quarantine_dir: Path | None = None,
    dry_run: bool = False,
    include_derived_avg_value: bool = True,
    rebuild_pit: bool = True,
) -> dict[str, Any]:
    cache_pykrx = DATA_ROOT / "cache_pykrx"
    cache_misc = DATA_ROOT / "cache_misc"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    quarantine_root = quarantine_dir or (DATA_ROOT / "data_quarantine" / f"mcap_duplicate_snapshots_{stamp}")
    groups = scan_suspect_mcap_groups(cache_pykrx, max_span_days=max_span_days)
    suspect_dates = {
        str(item["date_key"])
        for group in groups
        for item in group.get("items", [])
    }
    avg_value_files = (
        find_derived_avg_value_caches(cache_misc, suspect_dates)
        if include_derived_avg_value else []
    )
    payload: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": bool(dry_run),
        "max_span_days": int(max_span_days),
        "quarantine_root": str(quarantine_root),
        "suspect_group_count": int(len(groups)),
        "suspect_snapshot_count": int(sum(len(g.get("items", [])) for g in groups)),
        "suspect_dates": sorted(suspect_dates),
        "groups": [
            {
                **{k: v for k, v in group.items() if k != "items"},
                "items": [
                    {
                        "date": str(pd.Timestamp(item["date"]).date()),
                        "path": str(item["path"]),
                        "source": item.get("source") or "",
                        "true_source_date": item.get("true_source_date"),
                        "rows": int(item.get("rows", 0)),
                    }
                    for item in group.get("items", [])
                ],
            }
            for group in groups
        ],
        "mcap_moves": [],
        "avg_value_moves": [],
        "pit_backups": [],
        "pit_rebuild": {"skipped": True},
    }

    for group in groups:
        for item in group.get("items", []):
            payload["mcap_moves"].append(
                _move_file(Path(item["path"]), quarantine_root, "cache_pykrx", dry_run)
            )
    for path in avg_value_files:
        payload["avg_value_moves"].append(
            _move_file(path, quarantine_root, "cache_misc", dry_run)
        )

    if rebuild_pit and groups:
        payload["pit_backups"] = _backup_pit_files(quarantine_root, dry_run)
        if not dry_run:
            from kr_pit_universe import (
                build_historical_mcap_panel,
                build_listed_history_from_cache,
                invalidate_pit_caches,
            )

            invalidate_pit_caches()
            listed = build_listed_history_from_cache(save=True)
            hist = build_historical_mcap_panel(save=True)
            payload["pit_rebuild"] = {
                "skipped": False,
                "listed_history_rows": int(len(listed)),
                "historical_mcap_rows": int(len(hist)),
                "historical_mcap_snapshots": int(hist["snapshot_date"].nunique()) if not hist.empty else 0,
                "historical_mcap_min": str(pd.to_datetime(hist["snapshot_date"]).min().date()) if not hist.empty else None,
                "historical_mcap_max": str(pd.to_datetime(hist["snapshot_date"]).max().date()) if not hist.empty else None,
            }
        else:
            payload["pit_rebuild"] = {"skipped": False, "dry_run": True}
    return payload


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = quarantine_suspect_caches(
        max_span_days=int(args.max_span_days),
        quarantine_dir=Path(args.quarantine_dir) if args.quarantine_dir else None,
        dry_run=bool(args.dry_run),
        include_derived_avg_value=not bool(args.skip_derived_avg_value),
        rebuild_pit=not bool(args.skip_rebuild_pit),
    )
    out_path = out_dir / f"mcap_quarantine_{_yyyymmdd()}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("KR1000 suspect mcap quarantine")
    print(f"  dry_run:          {payload['dry_run']}")
    print(f"  groups:           {payload['suspect_group_count']}")
    print(f"  mcap snapshots:   {payload['suspect_snapshot_count']}")
    print(f"  avg-value caches: {len(payload['avg_value_moves'])}")
    print(f"  quarantine:       {payload['quarantine_root']}")
    print(f"  manifest:         {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
