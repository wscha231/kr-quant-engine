"""tools/mine_theme_history.py — Phase G2 historical theme strength miner.

Builds a daily theme strength panel for the configured themes (themes.yaml)
+ KRX 27 업종지수 (passive baseline) over the requested window.

Output:
  data_pit/theme_strength_panel_<start>_<end>.parquet
    columns: date, theme_key, theme_name_kr, return, n_active,
             abs_return_5d/20d/60d/120d/252d,
             rs_kospi_5d/20d/60d/252d, rs_kospi_20d_zscore_60d, stage

Plus a leader-by-quarter ranking CSV for ground-truth validation:
  research/10_theme_lifecycle/leader_themes_per_quarter.csv

Usage:
    py -3 tools/mine_theme_history.py --start 2020-01-01 --end 2026-04-30
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402
from kr_helpers import log  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Theme strength miner")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--end", default=None,
                   help="Default = today - 30d.")
    p.add_argument("--benchmark", default="1028",
                   help="KRX index code for RS benchmark (default 1028=KOSPI200).")
    p.add_argument("--themes", default=None,
                   help="Comma-separated theme_keys. Default = all in themes.yaml.")
    p.add_argument("--out", default=None,
                   help="Output parquet path.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    end = args.end or (pd.Timestamp.today() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    args.end = end
    log(f"[mine-theme] window {args.start} -> {end}")

    from kr_themes import (
        load_themes_yaml,
        build_theme_strength_panel,
    )
    cfg = load_themes_yaml()
    if not cfg.get("themes"):
        log("[mine-theme] themes.yaml empty/missing", level="ERROR")
        return 1

    selected = (args.themes.split(",") if args.themes else None)
    panel = build_theme_strength_panel(
        cfg, args.start, end,
        benchmark_ticker=args.benchmark,
        theme_keys=selected,
    )
    if panel.empty:
        log("[mine-theme] panel empty", level="ERROR")
        return 2

    out_dir = DATA_ROOT / "data_pit"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.out:
        path = Path(args.out)
    else:
        s = pd.Timestamp(args.start).strftime("%Y%m%d")
        e = pd.Timestamp(end).strftime("%Y%m%d")
        path = out_dir / f"theme_strength_panel_{s}_{e}.parquet"
    try:
        panel.to_parquet(path, index=False)
        log(f"[mine-theme] wrote {path}")
    except Exception as e:
        log(f"[mine-theme] write fail: {e}", level="ERROR")
        return 3

    # Quarterly leader table
    log("[mine-theme] computing per-quarter leader ranking...")
    panel["date"] = pd.to_datetime(panel["date"])
    panel["quarter"] = panel["date"].dt.to_period("Q").astype(str)
    quarter_avg = panel.groupby(
        ["quarter", "theme_key", "theme_name_kr"]
    )["abs_return_60d"].mean().reset_index()
    quarter_avg = quarter_avg.sort_values(
        ["quarter", "abs_return_60d"], ascending=[True, False]
    )
    quarter_avg["rank_in_quarter"] = quarter_avg.groupby("quarter").cumcount() + 1
    leader_top5 = quarter_avg[quarter_avg["rank_in_quarter"] <= 5].copy()
    csv_path = (PROJECT_ROOT / "research" / "10_theme_lifecycle"
                / "leader_themes_per_quarter.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    leader_top5.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log(f"[mine-theme] leader ranking -> {csv_path}")

    # Print quick summary
    print()
    print("Top theme per quarter (by avg abs_return_60d):")
    top1 = leader_top5[leader_top5["rank_in_quarter"] == 1][
        ["quarter", "theme_key", "theme_name_kr", "abs_return_60d"]
    ]
    for _, r in top1.iterrows():
        print(f"  {r['quarter']}  {r['theme_name_kr']:30s} "
              f"abs60d_avg={r['abs_return_60d']:+.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
