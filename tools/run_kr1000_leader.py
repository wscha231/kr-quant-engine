"""Run KR1000 Leader Alpha candidate scoring and trade-plan generation.

Examples:
    py -3 tools/run_kr1000_leader.py --candidates-csv research/candidates.csv
    py -3 tools/run_kr1000_leader.py --as-of 2026-04-30
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, kr1000_leader_alpha_cfg  # noqa: E402
from kr_helpers import log  # noqa: E402
from kr1000_leader import (  # noqa: E402
    build_kr1000_universe,
    build_target_portfolio,
    compute_leader_scores,
    generate_trade_plan,
    load_current_holdings,
    run_event_driven_backtest,
    write_leader_outputs,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KR1000 Leader Alpha runner")
    p.add_argument("--as-of", default=None, help="Signal date. Default=today.")
    p.add_argument("--candidates-csv", default=None,
                   help="Optional prebuilt candidate panel with component features.")
    p.add_argument("--current-holdings", default=None,
                   help="Optional current holdings CSV override. Default=DATA_ROOT/state/current_holdings.csv, with project state fallback.")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--top-holdings", type=int, default=None)
    p.add_argument("--scored-panel-csv", default=None,
                   help="Optional scored panel for ledger backtest.")
    p.add_argument("--price-panel-csv", default=None,
                   help="Optional price panel for ledger backtest.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg_overrides = {}
    if args.top_holdings:
        cfg_overrides["top_holdings"] = args.top_holdings
        cfg_overrides["portfolio_size"] = args.top_holdings
    cfg = kr1000_leader_alpha_cfg(cfg_overrides)
    as_of = pd.Timestamp(args.as_of).normalize() if args.as_of else pd.Timestamp.today().normalize()

    if args.candidates_csv:
        candidates = pd.read_csv(args.candidates_csv, dtype={"ticker": str})
        log(f"[kr1000-run] loaded candidates {args.candidates_csv}: {len(candidates)} rows")
    else:
        log(f"[kr1000-run] building KR1000 universe for {as_of.date()}")
        candidates = build_kr1000_universe(as_of, cfg=cfg)
        if candidates.empty:
            log("[kr1000-run] empty KR1000 universe", level="ERROR")
            return 1
        log("[kr1000-run] no candidate feature file supplied; scoring will use "
            "available universe columns only.", level="WARN")

    scored = compute_leader_scores(candidates, cfg)
    target = build_target_portfolio(scored, cfg, as_of_date=as_of)
    holdings = load_current_holdings(args.current_holdings)
    plan = generate_trade_plan(holdings, target, scored, cfg, as_of_date=as_of)

    backtest = None
    if args.scored_panel_csv and args.price_panel_csv:
        sp = pd.read_csv(args.scored_panel_csv, dtype={"ticker": str})
        pp = pd.read_csv(args.price_panel_csv, dtype={"ticker": str})
        backtest = run_event_driven_backtest(sp, pp, cfg=cfg)
        log(f"[kr1000-run] backtest metrics: {backtest.metrics}")

    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    write_leader_outputs(scored, target, plan, backtest=backtest, output_dir=out_dir)
    print(f"KR1000 candidates: {len(scored)}")
    print(f"Target holdings:   {len(target)}")
    print(f"Trade plan rows:   {len(plan)}")
    print(f"Output dir:        {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
