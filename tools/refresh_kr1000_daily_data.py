"""Refresh latest KR1000 daily data and increment PIT universe artifacts.

This addresses the stale-data blocker found by data_integrity_audit:
- fetch latest market-cap snapshot into cache_pykrx/mktcap_ALL_YYYYMMDD.parquet
- optionally compute avg_value_60d cache for the same date
- append the new mcap snapshot into data_pit/historical_mcap.parquet
- rebuild data_pit/listed_history.parquet from the updated historical_mcap

Run:
    py -3 tools/refresh_kr1000_daily_data.py --as-of 2026-06-04 --skip-avg-value
    py -3 tools/refresh_kr1000_daily_data.py --dry-run
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
    HISTORICAL_MCAP_PATH,
    LISTED_HISTORY_PATH,
    invalidate_pit_caches,
)
from kr_pykrx_client import fetch_business_days, fetch_market_cap_market  # noqa: E402
from kr_universe import compute_avg_trading_value_60d  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refresh KR1000 daily market data")
    p.add_argument("--as-of", default=None,
                   help="Snapshot date. Default=previous KRX close before today.")
    p.add_argument("--run-date", default=None,
                   help="Run date used to infer previous close when --as-of is omitted.")
    p.add_argument("--lookback-days", type=int, default=60)
    p.add_argument("--refresh-days", type=int, default=0,
                   help="0 forces provider/cache refresh.")
    p.add_argument("--skip-avg-value", action="store_true",
                   help="Only refresh mcap/PIT artifacts.")
    p.add_argument("--skip-pit-update", action="store_true")
    p.add_argument("--max-tickers", type=int, default=0,
                   help="Dev throttle for avg-value computation. 0=all tickers.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out-dir", default=None)
    return p.parse_args()


def _yyyymmdd(day: pd.Timestamp) -> str:
    return pd.Timestamp(day).strftime("%Y%m%d")


def previous_krx_close_date(run_date: pd.Timestamp) -> pd.Timestamp:
    rd = pd.Timestamp(run_date).normalize()
    start = rd - pd.Timedelta(days=14)
    days = fetch_business_days(_yyyymmdd(start), _yyyymmdd(rd))
    days = [pd.Timestamp(d).normalize() for d in days if pd.Timestamp(d).normalize() < rd]
    if days:
        return max(days)
    return (rd - pd.offsets.BDay(1)).normalize()


def normalize_mcap_snapshot(raw: pd.DataFrame, snapshot_date: pd.Timestamp) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    out = raw.copy()
    if "ticker" not in out.columns:
        return pd.DataFrame()
    out["ticker"] = out["ticker"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    out["snapshot_date"] = pd.Timestamp(snapshot_date).normalize()
    for col in ("market_cap", "listed_shares", "volume", "value"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "market" not in out.columns:
        out["market"] = ""
    keep = [
        c for c in (
            "ticker", "snapshot_date", "market", "market_cap",
            "listed_shares", "volume", "value",
        )
        if c in out.columns
    ]
    out = out[keep].drop_duplicates(["snapshot_date", "ticker"], keep="last")
    return out.sort_values(["snapshot_date", "ticker"]).reset_index(drop=True)


def derive_listed_history_from_historical_mcap(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    p = panel.copy()
    p["ticker"] = p["ticker"].astype(str).str.zfill(6)
    p["snapshot_date"] = pd.to_datetime(p["snapshot_date"], errors="coerce").dt.normalize()
    p = p.dropna(subset=["snapshot_date", "ticker"])
    agg = p.groupby("ticker").agg(
        n_appearances=("snapshot_date", "size"),
        first_seen_date=("snapshot_date", "min"),
        last_seen_date=("snapshot_date", "max"),
    ).reset_index()
    if "market" in p.columns:
        market_mode = (
            p.groupby("ticker")["market"]
            .agg(lambda s: s.value_counts().index[0] if len(s.dropna()) else "")
            .reset_index()
        )
        agg = agg.merge(market_mode, on="ticker", how="left")
    else:
        agg["market"] = ""
    latest_snap = p["snapshot_date"].max()
    latest_tickers = set(p.loc[p["snapshot_date"] == latest_snap, "ticker"])
    agg["is_active_latest"] = agg["ticker"].isin(latest_tickers)
    agg["inferred_listing_date"] = agg["first_seen_date"]
    agg["inferred_delisting_date"] = agg.apply(
        lambda r: pd.NaT if r["is_active_latest"] else r["last_seen_date"],
        axis=1,
    )
    earliest = p["snapshot_date"].min()
    agg["listing_date_is_lower_bound"] = agg["first_seen_date"] == earliest
    return agg.sort_values(["market", "ticker"]).reset_index(drop=True)


def append_pit_mcap_snapshot(snapshot: pd.DataFrame, dry_run: bool = False) -> dict[str, Any]:
    if snapshot.empty:
        return {"updated": False, "reason": "empty snapshot"}
    if HISTORICAL_MCAP_PATH.exists():
        existing = pd.read_parquet(HISTORICAL_MCAP_PATH)
    else:
        existing = pd.DataFrame(columns=snapshot.columns)
    existing = existing.copy()
    if not existing.empty:
        existing["ticker"] = existing["ticker"].astype(str).str.zfill(6)
        existing["snapshot_date"] = pd.to_datetime(existing["snapshot_date"], errors="coerce").dt.normalize()
    snap_date = pd.Timestamp(snapshot["snapshot_date"].iloc[0]).normalize()
    base = existing[existing["snapshot_date"] != snap_date].copy() if not existing.empty else existing
    combined = pd.concat([base, snapshot], ignore_index=True)
    combined = combined.drop_duplicates(["snapshot_date", "ticker"], keep="last")
    combined = combined.sort_values(["ticker", "snapshot_date"]).reset_index(drop=True)
    listed = derive_listed_history_from_historical_mcap(combined)
    payload = {
        "updated": not dry_run,
        "snapshot_date": str(snap_date.date()),
        "new_snapshot_rows": int(len(snapshot)),
        "historical_mcap_rows_before": int(len(existing)),
        "historical_mcap_rows_after": int(len(combined)),
        "historical_mcap_min": str(combined["snapshot_date"].min().date()) if not combined.empty else "",
        "historical_mcap_max": str(combined["snapshot_date"].max().date()) if not combined.empty else "",
        "listed_history_rows_after": int(len(listed)),
    }
    if not dry_run:
        HISTORICAL_MCAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(HISTORICAL_MCAP_PATH, index=False)
        listed.to_parquet(LISTED_HISTORY_PATH, index=False)
        invalidate_pit_caches()
    return payload


def main() -> int:
    args = parse_args()
    run_date = pd.Timestamp(args.run_date).normalize() if args.run_date else pd.Timestamp.today().normalize()
    as_of = pd.Timestamp(args.as_of).normalize() if args.as_of else previous_krx_close_date(run_date)
    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    expected_mcap_cache = DATA_ROOT / "cache_pykrx" / f"mktcap_ALL_{_yyyymmdd(as_of)}.parquet"
    expected_avg_cache = DATA_ROOT / "cache_misc" / f"avg_value_{args.lookback_days}d_{_yyyymmdd(as_of)}.parquet"
    payload: dict[str, Any] = {
        "run_date": str(run_date.date()),
        "as_of": str(as_of.date()),
        "dry_run": bool(args.dry_run),
        "expected_mcap_cache": str(expected_mcap_cache),
        "expected_avg_value_cache": str(expected_avg_cache),
    }
    if args.dry_run:
        payload.update({
            "mcap_cache_exists": expected_mcap_cache.exists(),
            "avg_value_cache_exists": expected_avg_cache.exists(),
            "historical_mcap_path": str(HISTORICAL_MCAP_PATH),
            "historical_mcap_exists": HISTORICAL_MCAP_PATH.exists(),
        })
    else:
        log(f"[refresh] fetching mcap snapshot for {as_of.date()}")
        mcap = fetch_market_cap_market(_yyyymmdd(as_of), market="ALL", refresh_days=args.refresh_days)
        snapshot = normalize_mcap_snapshot(mcap, as_of)
        payload["mcap_rows"] = int(len(snapshot))
        payload["mcap_cache_exists"] = expected_mcap_cache.exists()
        tickers = snapshot["ticker"].astype(str).tolist() if not snapshot.empty else []
        if args.max_tickers and args.max_tickers > 0:
            tickers = tickers[:args.max_tickers]
            payload["avg_value_ticker_throttle"] = int(args.max_tickers)
        if not args.skip_avg_value and tickers:
            avg = compute_avg_trading_value_60d(
                as_of,
                lookback_days=args.lookback_days,
                refresh_days=args.refresh_days,
                tickers=tickers,
            )
            payload["avg_value_rows"] = int(len(avg))
            payload["avg_value_cache_exists"] = expected_avg_cache.exists()
        else:
            payload["avg_value_rows"] = 0
            payload["avg_value_skipped"] = True
        if not args.skip_pit_update:
            payload["pit_update"] = append_pit_mcap_snapshot(snapshot, dry_run=False)
        else:
            payload["pit_update"] = {"updated": False, "reason": "skip_pit_update"}

    stamp = as_of.strftime("%Y%m%d")
    out_path = out_dir / f"kr1000_daily_data_refresh_{stamp}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("KR1000 daily data refresh")
    print(f"  as_of:      {as_of.date()}")
    print(f"  dry_run:    {args.dry_run}")
    print(f"  manifest:   {out_path}")
    if payload.get("pit_update"):
        print(f"  pit_update: {payload['pit_update']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
