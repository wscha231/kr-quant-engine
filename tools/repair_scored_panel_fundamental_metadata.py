"""Repair stale DART period metadata in an existing scored panel.

This is a bridge for cached panels built before
`kr_features.sanitize_fundamental_period_metadata()` was added. It only repairs
audit-trail period metadata using the filing date as the PIT authority; it does
not claim to replace a full feature rebuild.

Run:
    py -3 tools/repair_scored_panel_fundamental_metadata.py --dry-run
    py -3 tools/repair_scored_panel_fundamental_metadata.py
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
from kr_dart_client import infer_report_period_end  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Repair scored-panel DART period metadata")
    p.add_argument("--input", default=None, help="Input scored_panel parquet. Default=latest scored_panel_v0.")
    p.add_argument("--out", default=None, help="Output parquet. Default=<input>_fundmeta_repaired.parquet.")
    p.add_argument("--audit-json", default=None)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def latest_scored_panel(feature_store: Path | None = None) -> Path | None:
    root = feature_store if feature_store is not None else DATA_ROOT / "feature_store"
    files = sorted(root.glob("scored_panel_v0_*.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def default_output_path(input_path: Path) -> Path:
    suffix = "_fundmeta_repaired"
    if input_path.stem.endswith(suffix):
        return input_path
    return input_path.with_name(f"{input_path.stem}{suffix}{input_path.suffix}")


def repair_scored_panel_fundamental_metadata(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {
        "fundamentals_period_end",
        "fundamentals_rcept_dt",
        "fundamentals_bsns_year",
        "fundamentals_reprt_code",
    }
    missing = sorted(required - set(df.columns))
    out = df.copy()
    if missing:
        return out, {
            "rows": int(len(out)),
            "missing_columns": missing,
            "before_period_after_rcept": 0,
            "before_period_after_signal": 0,
            "adjusted_rows": 0,
            "after_period_after_rcept": 0,
            "after_period_after_signal": 0,
        }

    date_col = "rebalance_date" if "rebalance_date" in out.columns else "date"
    if date_col not in out.columns:
        raise ValueError("scored panel needs rebalance_date or date column")

    period = pd.to_datetime(out["fundamentals_period_end"], errors="coerce")
    rcept = pd.to_datetime(out["fundamentals_rcept_dt"], errors="coerce")
    signal = pd.to_datetime(out[date_col], errors="coerce")
    bad = period.notna() & rcept.notna() & (period > rcept)
    before_signal = period.notna() & signal.notna() & (period > signal)

    if "fundamentals_period_end_adjusted_from_calendar" not in out.columns:
        out["fundamentals_period_end_adjusted_from_calendar"] = False
    if "fundamentals_period_end_repair_source" not in out.columns:
        out["fundamentals_period_end_repair_source"] = ""

    adjusted = 0
    for idx, row in out.loc[bad, [
        "fundamentals_bsns_year",
        "fundamentals_reprt_code",
        "fundamentals_rcept_dt",
    ]].iterrows():
        try:
            fixed, did_adjust = infer_report_period_end(
                int(float(row["fundamentals_bsns_year"])),
                str(row["fundamentals_reprt_code"]),
                pd.Timestamp(row["fundamentals_rcept_dt"]),
            )
        except Exception:
            continue
        if pd.isna(fixed):
            continue
        if pd.Timestamp(fixed) != period.loc[idx]:
            adjusted += 1
        out.at[idx, "fundamentals_period_end"] = pd.Timestamp(fixed)
        if did_adjust:
            out.at[idx, "fundamentals_period_end_adjusted_from_calendar"] = True
            out.at[idx, "fundamentals_period_end_repair_source"] = "infer_report_period_end"

    period_after = pd.to_datetime(out["fundamentals_period_end"], errors="coerce")
    rcept_after = pd.to_datetime(out["fundamentals_rcept_dt"], errors="coerce")
    signal_after = pd.to_datetime(out[date_col], errors="coerce")
    after_rcept = period_after.notna() & rcept_after.notna() & (period_after > rcept_after)
    after_signal = period_after.notna() & signal_after.notna() & (period_after > signal_after)

    stats = {
        "rows": int(len(out)),
        "date_col": date_col,
        "missing_columns": [],
        "before_period_after_rcept": int(bad.sum()),
        "before_period_after_signal": int(before_signal.sum()),
        "adjusted_rows": int(adjusted),
        "after_period_after_rcept": int(after_rcept.sum()),
        "after_period_after_signal": int(after_signal.sum()),
    }
    return out, stats


def main() -> int:
    args = parse_args()
    input_path = Path(args.input) if args.input else latest_scored_panel()
    if input_path is None:
        raise FileNotFoundError("No scored_panel_v0 parquet found")
    out_path = Path(args.out) if args.out else default_output_path(input_path)
    if out_path.exists() and not args.overwrite and out_path != input_path and not args.dry_run:
        raise FileExistsError(f"Output exists; pass --overwrite: {out_path}")

    df = pd.read_parquet(input_path)
    repaired, stats = repair_scored_panel_fundamental_metadata(df)
    payload = {
        "input": str(input_path),
        "out": str(out_path),
        "dry_run": bool(args.dry_run),
        "overwrite": bool(args.overwrite),
        **stats,
    }

    if not args.dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        repaired.to_parquet(out_path, index=False)

    audit_path = Path(args.audit_json) if args.audit_json else DATA_ROOT / "outputs" / "scored_panel_fundmeta_repair.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print("KR1000 scored-panel fundamental metadata repair")
    print(f"  input:  {input_path}")
    print(f"  out:    {out_path}")
    print(f"  before period>rcept: {payload['before_period_after_rcept']}")
    print(f"  before period>signal: {payload['before_period_after_signal']}")
    print(f"  adjusted:            {payload['adjusted_rows']}")
    print(f"  after period>rcept:  {payload['after_period_after_rcept']}")
    print(f"  after period>signal: {payload['after_period_after_signal']}")
    print(f"  audit:  {audit_path}")
    return 0 if payload["after_period_after_rcept"] == 0 and payload["after_period_after_signal"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
