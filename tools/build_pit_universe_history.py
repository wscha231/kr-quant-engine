"""tools/build_pit_universe_history.py -- One-shot orchestrator for Phase C1.

Builds the two PIT artifacts that replace the FDR-current-listing leak:
  - data_pit/listed_history.parquet
  - data_pit/historical_mcap.parquet

Both are derived purely from the existing cached monthly mktcap snapshots
(cache_pykrx/mktcap_ALL_*.parquet). No new network fetches required when
the cache is already populated by historical backtests.

Run:
    py -3 tools/build_pit_universe_history.py

Re-run safely (overwrites artifacts). After running, the live engine
(kr_universe.build_universe_snapshot) will use these artifacts via
kr_pit_universe.fetch_listing_at_date and compute_listed_months_pit.

Optional flags (env vars):
    PIT_REBUILD=1   force rebuild even if artifacts already exist
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root importable when launched from tools/ directly
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_pit_universe import (  # noqa: E402
    HISTORICAL_MCAP_PATH,
    LISTED_HISTORY_PATH,
    build_historical_mcap_panel,
    build_listed_history_from_cache,
    invalidate_pit_caches,
)


def main() -> int:
    force = os.environ.get("PIT_REBUILD", "").strip() in {"1", "true", "TRUE"}
    print("=" * 70)
    print("Phase C1 -- Build PIT universe history (listed_history + historical_mcap)")
    print("=" * 70)

    skip_listed = LISTED_HISTORY_PATH.exists() and not force
    skip_mcap = HISTORICAL_MCAP_PATH.exists() and not force

    print(f"\nlisted_history target  : {LISTED_HISTORY_PATH}")
    print(f"  exists: {LISTED_HISTORY_PATH.exists()}, force={force} -> "
          f"{'SKIP' if skip_listed else 'BUILD'}")

    print(f"\nhistorical_mcap target : {HISTORICAL_MCAP_PATH}")
    print(f"  exists: {HISTORICAL_MCAP_PATH.exists()}, force={force} -> "
          f"{'SKIP' if skip_mcap else 'BUILD'}")

    invalidate_pit_caches()

    if not skip_listed:
        print("\n[1/2] Building listed_history.parquet from cached mktcap snapshots...")
        hist = build_listed_history_from_cache(save=True)
        if hist.empty:
            print("  FAIL: listed_history empty (no cached mktcap files found).")
            return 1
        print(f"  OK: {len(hist)} tickers")
        print(f"     active     : {int(hist['is_active_latest'].sum())}")
        print(f"     delisted   : {int((~hist['is_active_latest']).sum())}")
        print(f"     listing-known     : "
              f"{int((~hist['listing_date_is_lower_bound']).sum())}")
        print(f"     listing-pre-cache : "
              f"{int(hist['listing_date_is_lower_bound'].sum())}")
        print(f"     date range : "
              f"{hist['first_seen_date'].min().date()} -> "
              f"{hist['last_seen_date'].max().date()}")

    if not skip_mcap:
        print("\n[2/2] Building historical_mcap.parquet from cached mktcap snapshots...")
        panel = build_historical_mcap_panel(save=True)
        if panel.empty:
            print("  FAIL: historical_mcap empty.")
            return 1
        print(f"  OK: {len(panel)} rows, "
              f"{panel['ticker'].nunique()} unique tickers, "
              f"{panel['snapshot_date'].nunique()} snapshots")
        print(f"     date range : "
              f"{panel['snapshot_date'].min().date()} -> "
              f"{panel['snapshot_date'].max().date()}")

    print("\nDone. Artifacts ready for kr_universe.build_universe_snapshot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
