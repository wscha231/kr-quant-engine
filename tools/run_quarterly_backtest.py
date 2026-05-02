"""tools/run_quarterly_backtest.py — quarterly OOS backtest CLI (Phase D).

Triggered by GitHub Actions on the 1st of Jan / Apr / Jul / Oct. Reuses
existing OOS picks (research/06_walkforward_baselines/p_mb_v1_oos_picks.csv)
when available; otherwise computes a fresh OOS run via
generate_oos_picks_purged_3sleeve.

Outputs:
  outputs/quarterly_backtest_<YYYY-Qx>.json
  outputs/quarterly_backtest_<YYYY-Qx>_monthly.csv
  outputs/quarterly_backtest_latest.json     (overwrite each run)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402
from kr_helpers import log  # noqa: E402


def _quarter_label(today: datetime | None = None) -> str:
    today = today or datetime.now()
    q = (today.month - 1) // 3 + 1
    return f"{today.year}-Q{q}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Quarterly backtest")
    p.add_argument("--picks", default=None,
                   help="CSV path of OOS picks; default = "
                        "research/06_walkforward_baselines/p_mb_v1_oos_picks.csv")
    p.add_argument("--seed-krw", type=float, default=1e8)
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--weighting", default="equal",
                   choices=("equal", "score", "score_power", "capped"))
    p.add_argument("--no-governance", action="store_true")
    p.add_argument("--no-sleeve", action="store_true")
    p.add_argument("--label", default=None,
                   help="Override quarter label, e.g. '2026-Q2'.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    label = args.label or _quarter_label()
    log(f"[quarterly] running {label} backtest")

    picks_path = (
        Path(args.picks) if args.picks
        else PROJECT_ROOT / "research" / "06_walkforward_baselines"
             / "p_mb_v1_oos_picks.csv"
    )
    if not picks_path.exists():
        log(f"[quarterly] picks CSV not found: {picks_path}", level="ERROR")
        return 1

    picks = pd.read_csv(picks_path)
    if "rebalance_date" in picks.columns:
        picks["rebalance_date"] = pd.to_datetime(picks["rebalance_date"])

    log(f"[quarterly] picks loaded: {len(picks)} rows × "
        f"{picks['rebalance_date'].nunique()} months")

    from kr_backtester_realistic import run_realistic_backtest

    result = run_realistic_backtest(
        picks=picks,
        seed_capital_krw=args.seed_krw,
        top_n=args.top_n,
        weighting=args.weighting,
        use_governance_overlay=not args.no_governance,
        use_sleeve_separation=(not args.no_sleeve)
            and ("p_pre_entry" in picks.columns)
            and ("p_continuation" in picks.columns),
        verbose=True,
    )

    if "error" in result:
        log(f"[quarterly] backtest failed: {result['error']}", level="ERROR")
        return 1

    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"quarterly_backtest_{label}.json"
    monthly_csv = out_dir / f"quarterly_backtest_{label}_monthly.csv"
    latest_json = out_dir / "quarterly_backtest_latest.json"

    # Strip non-serializable monthly_log into separate CSV
    monthly = result.pop("monthly_log", None)
    trades = result.pop("trades_log", None)

    payload = {
        "label": label,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metrics": result,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    with open(latest_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    if monthly:
        pd.DataFrame(monthly).to_csv(monthly_csv, index=False)

    log(f"[quarterly] wrote {json_path.name}")
    if "metrics" in payload and isinstance(payload["metrics"], dict):
        m = payload["metrics"]
        for k in ("cagr", "mdd", "sharpe", "final_capital"):
            if k in m:
                log(f"[quarterly] {k} = {m[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
