"""tools/run_production_backtest.py — Phase G v5 production preset.

One-command entrypoint that runs the recommended v5 theme rotation
backtest with all the best parameters discovered in the Step 1+2 sweep.

Equivalent to:
    py -3 tools/run_theme_rotation_backtest.py \
        --theme-panel data_pit/theme_strength_panel_<period>.parquet \
        --start 2020-01-01 --end 2026-04-30 \
        --max-themes 3 --stocks-per-theme 3 \
        --exit-confirmation-weeks 1 --hard-stop-pct -0.30 \
        --g5-classifier-weight 0.4

Production preset
-----------------
- Weekly rebalance (Monday).
- Hold up to 3 themes × 3 leaders = 9 stocks.
- Stage classifier (Wyckoff lifecycle) decides theme entry/exit.
- Single-week stage exit (no anti-shake-out delay) — empirically best.
- Hard stop -30% per holding catches catastrophic outliers.
- G5 classifier blend (weight 0.4) reorders leaders within a theme by
  multibagger P_pre_surge for highest-conviction picks.

Latest sweep result (2020-2026.04, 1억 KRW):
  CAGR  44.92pct
  MDD  -28.21pct
  Sharpe 1.35
  Calmar 1.59
  Avg holding  164 days (~5.4 months)
  346/346 weekly rebalances, 188 round-trip trades
"""
from __future__ import annotations

import argparse
import subprocess
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
    p = argparse.ArgumentParser(description="v5 production backtest")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--end", default=None,
                   help="Default = today - 30d.")
    p.add_argument("--seed-krw", type=float, default=1e8)
    p.add_argument("--theme-panel", default=None,
                   help="Override panel path (default = autodetect newest).")
    p.add_argument("--max-themes", type=int, default=3)
    p.add_argument("--stocks-per-theme", type=int, default=3)
    p.add_argument("--exit-confirmation-weeks", type=int, default=1)
    p.add_argument("--hard-stop-pct", type=float, default=-0.30)
    p.add_argument("--g5-classifier-weight", type=float, default=0.4)
    p.add_argument("--out-prefix", default=None)
    return p.parse_args()


def _newest(pattern: str, dir_path: Path) -> Path | None:
    if not dir_path.exists():
        return None
    matches = sorted(dir_path.glob(pattern),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def main() -> int:
    args = parse_args()
    end = args.end or (pd.Timestamp.today() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")

    panel = args.theme_panel
    if panel is None:
        panel_path = _newest("theme_strength_panel_*.parquet",
                             DATA_ROOT / "data_pit")
        if panel_path is None:
            log("[prod] no theme strength panel found; run "
                "tools/mine_theme_history.py first.", level="ERROR")
            return 1
        panel = str(panel_path)
    log(f"[prod] using theme panel: {panel}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    prefix = args.out_prefix or f"production_v5_{stamp}"

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "run_theme_rotation_backtest.py"),
        "--theme-panel", panel,
        "--start", args.start,
        "--end", end,
        "--seed-krw", str(args.seed_krw),
        "--max-themes", str(args.max_themes),
        "--stocks-per-theme", str(args.stocks_per_theme),
        "--exit-confirmation-weeks", str(args.exit_confirmation_weeks),
        "--hard-stop-pct", str(args.hard_stop_pct),
        "--g5-classifier-weight", str(args.g5_classifier_weight),
        "--out-prefix", prefix,
    ]
    log(f"[prod] $ {' '.join(cmd[1:])}")
    rc = subprocess.call(cmd)
    if rc != 0:
        log(f"[prod] backtest failed rc={rc}", level="ERROR")
        return rc
    log(f"[prod] success — outputs: outputs/{prefix}.json + "
        f"_monthly.csv + _trades.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
