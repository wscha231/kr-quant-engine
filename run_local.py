"""run_local.py — local entry point for kr_quant_engine.

Usage:
    py -3 run_local.py --quick          # ~5-15min QUICK_RESCORE (caches reused)
    py -3 run_local.py --full           # ~1-2h FULL rebuild
    py -3 run_local.py --verdict-only   # ~2s last metrics + verdict
    py -3 run_local.py --no-collector   # skip collector, run pipeline only

After any --quick or --full run, a verdict is printed at the end.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure module path
sys.path.insert(0, str(Path(__file__).parent))

from kr_config import DEFAULT_CFG, KR_ENGINE_REUSE_VERSION
from kr_helpers import load_dotenv_if_present, log


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="kr_quant_engine local runner")
    p.add_argument("--quick", action="store_true",
                   help="Quick rescore (caches reused). Default mode.")
    p.add_argument("--full", action="store_true",
                   help="Full rebuild (no cache reuse).")
    p.add_argument("--verdict-only", action="store_true",
                   help="Skip run, print last verdict from outputs/.")
    p.add_argument("--panel-only", action="store_true",
                   help="Build/materialize scored_panel_v0 only; skip P0 selection/backtest/verdict.")
    p.add_argument("--no-collector", action="store_true",
                   help="Skip BOK macro collector step.")
    p.add_argument("--start-date", default=None,
                   help="Backtest start (YYYY-MM-DD). Default 2016-01-01.")
    p.add_argument("--end-date", default=None,
                   help="Backtest end (YYYY-MM-DD). Default today.")
    p.add_argument("--portfolio-size", type=int, default=None,
                   help="Top-N. Default 30.")
    p.add_argument("--incremental-max-new-months", type=int, default=None,
                   help="When quick incremental rebuild is used, compute only the latest N missing rebalance dates.")
    p.add_argument("--incremental-fill-order", choices=["latest", "earliest"], default=None,
                   help="For incremental rebuild limits, choose latest or earliest missing rebalance dates.")
    p.add_argument("--no-forward-labels", action="store_true",
                   help="Skip forward target label generation during scored-panel rebuild.")
    p.add_argument("--phase0-momentum", default="auto",
                   help="'auto' | '0' | '1' - Phase 0 momentum toggle.")
    return p.parse_args()


def apply_phase_env(args: argparse.Namespace) -> None:
    """Set PHASE_<KEY>_ENABLED env vars from CLI args."""
    import os
    if args.phase0_momentum != "auto":
        os.environ["PHASE_PHASE0_MOMENTUM_ENABLED"] = str(args.phase0_momentum)


def build_cfg(args: argparse.Namespace) -> dict:
    cfg = dict(DEFAULT_CFG)
    if args.start_date:
        cfg["start_date"] = args.start_date
    if args.end_date:
        cfg["end_date"] = args.end_date
    if args.portfolio_size:
        cfg["portfolio_size"] = args.portfolio_size
    if args.incremental_max_new_months is not None:
        cfg["scored_panel_incremental_max_new_months"] = int(args.incremental_max_new_months)
    if args.incremental_fill_order:
        cfg["scored_panel_incremental_fill_order"] = args.incremental_fill_order
    if args.no_forward_labels:
        cfg["forward_label_enabled"] = False
    if args.full:
        cfg["reuse_existing_artifacts"] = False
    elif args.quick:
        cfg["reuse_existing_artifacts"] = True
    return cfg


def banner() -> None:
    print("=" * 60)
    print(f"kr_quant_engine - engine {KR_ENGINE_REUSE_VERSION}")
    print("=" * 60)


def main() -> int:
    args = parse_args()
    banner()
    load_dotenv_if_present()
    apply_phase_env(args)

    if args.verdict_only:
        from kr_pipeline import run_verdict_only
        run_verdict_only()
        return 0

    cfg = build_cfg(args)
    mode = "FULL" if args.full else "QUICK"
    log(f"[run_local] mode={mode} start={cfg.get('start_date')} end={cfg.get('end_date')} "
        f"top_n={cfg.get('portfolio_size')} reuse={cfg.get('reuse_existing_artifacts')}")

    if not args.no_collector:
        log("[run_local] collector step (BOK macro)")
        try:
            from kr_bok_client import fetch_macro_panel
            macro = fetch_macro_panel(cfg["start_date"], cfg.get("end_date") or "today")
            log(f"[run_local] macro panel: {len(macro)} rows, "
                f"cols={list(macro.columns)[:6]}{'...' if len(macro.columns)>6 else ''}")
        except Exception as e:
            log(f"[run_local] BOK fetch fail: {e}", level="WARN")

    log("[run_local] pipeline step")
    t0 = time.time()
    from kr_pipeline import build_scored_panel_v0, run_p0_baseline, run_verdict_only
    if args.panel_only:
        panel = build_scored_panel_v0(
            cfg.get("start_date", "2016-01-01"),
            cfg.get("end_date"),
            cfg=cfg,
        )
        log(f"[run_local] panel-only done in {(time.time()-t0)/60:.1f}min rows={len(panel)}")
        return 0 if not panel.empty else 1

    result = run_p0_baseline(cfg)
    log(f"[run_local] pipeline done in {(time.time()-t0)/60:.1f}min")

    if "error" in result:
        log(f"[run_local] FAIL: {result['error']}", level="ERROR")
        return 1

    print()
    run_verdict_only()
    return 0


if __name__ == "__main__":
    sys.exit(main())
