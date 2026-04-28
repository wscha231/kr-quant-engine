"""tools/multibagger_explorer.py — Post-fetch retrospective analysis.

Run AFTER pip install pykrx + first kr_multibagger fetch:
    py -3 tools/multibagger_explorer.py

Loads the cached episode panel and prints:
- Distribution stats (total, by year, by exchange, mcap band)
- Top 30 episodes by max_return
- Theme grouping (if themes.yaml has tickers populated)
- Cohort analysis (entry year × peak year)
- Time-to-peak distribution
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, DEFAULT_CFG
from kr_helpers import log
from kr_multibagger import episode_summary_stats, load_or_build_episode_panel


def explore(eps: pd.DataFrame) -> None:
    """Print full retrospective analysis."""
    if eps.empty:
        print("No episodes loaded — run kr_multibagger build first.")
        return

    print("=" * 60)
    print(f"Multibagger Episode Inventory")
    print("=" * 60)
    print(f"Total episodes (raw):         {len(eps)}")
    if "quality_pass" in eps.columns:
        pass_eps = eps[eps["quality_pass"].astype(bool)]
        print(f"Quality-pass episodes:        {len(pass_eps)}")
    else:
        pass_eps = eps
    print(f"Unique tickers:               {pass_eps['ticker'].nunique()}")

    # Return distribution
    print(f"\nReturn distribution:")
    for q, label in [(0.25, "P25"), (0.50, "P50"), (0.75, "P75"),
                     (0.95, "P95"), (1.00, "MAX")]:
        v = pass_eps["max_return"].quantile(q)
        print(f"  {label}:  {v:>8.2f}x  ({v*100:>6.0f}%)")

    # Months-to-peak distribution
    print(f"\nMonths to peak:")
    for q, label in [(0.25, "P25"), (0.50, "P50"), (0.75, "P75")]:
        v = pass_eps["months_to_peak"].quantile(q)
        print(f"  {label}:  {v:>4.1f} months")

    # Episodes by entry year
    print(f"\nEpisodes by entry year:")
    if "entry_date" in pass_eps.columns:
        ey = pass_eps["entry_date"].dt.year.value_counts().sort_index()
        for year, count in ey.items():
            bar = "█" * min(count, 50)
            print(f"  {int(year)}:  {count:>4}  {bar}")

    # Mcap distribution at entry
    if "mcap_at_entry" in pass_eps.columns:
        print(f"\nMcap at entry distribution:")
        for q, label in [(0.50, "P50"), (0.75, "P75"), (0.95, "P95")]:
            v = pass_eps["mcap_at_entry"].quantile(q)
            print(f"  {label}:  {v/1e12:>6.2f} 조원   ({v/1e8:>10.0f} 억원)")

    # Top 30 by max_return
    print(f"\nTop 30 episodes by max_return:")
    cols = [c for c in ["ticker", "entry_date", "surge_start_date",
                         "peak_date", "max_return", "months_to_peak",
                         "mcap_at_entry"] if c in pass_eps.columns]
    top30 = pass_eps.sort_values("max_return", ascending=False).head(30)[cols]
    print(top30.to_string(index=False))

    # Cohort: entry year × peak year heatmap
    if "entry_date" in pass_eps.columns and "peak_date" in pass_eps.columns:
        print(f"\nCohort: entry year × peak year:")
        coh = pd.crosstab(
            pass_eps["entry_date"].dt.year,
            pass_eps["peak_date"].dt.year,
        )
        print(coh.to_string())


def main() -> int:
    log("[explorer] loading episode panel...")
    cfg = dict(DEFAULT_CFG)
    eps = load_or_build_episode_panel(cfg=cfg, refresh=False)
    if eps.empty:
        print("No episodes available. Run first:")
        print("  py -3 -m pip install pykrx")
        print("  py -3 -c 'from kr_multibagger import load_or_build_episode_panel; load_or_build_episode_panel()'")
        return 1
    explore(eps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
