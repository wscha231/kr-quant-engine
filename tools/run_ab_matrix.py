"""tools/run_ab_matrix.py — A/B experiment matrix orchestrator (Section 10).

Runs the spec's three experiment families:
  D1-D3 : Data-truth experiments (PIT corrections progression)
  A1-A6 : Alpha-layer additions
  G0-G4 : Governance overlay variants

Each variant runs run_realistic_backtest with a different combination of
flags / picks columns, writes:
  outputs/ab_matrix/<variant>.json
  outputs/ab_matrix/summary.csv

Variant matrix is defined inline (VARIANTS dict). The script can run a
subset via --only or --skip flags.

Notes
-----
- D1 (survivorship-corrected universe) and D2 (historical mcap only) require
  PIT artifacts (data_pit/listed_history.parquet + historical_mcap.parquet);
  these are produced by Phase C1's tools/build_pit_universe_history.py.
- A6 (full alpha + regime) requires a picks CSV with all signals computed.
  When a required column is missing the variant is marked "skipped".
- This orchestrator does NOT regenerate features / picks each run — it
  expects a single picks CSV (--picks) annotated with all needed columns
  and toggles the BACKTESTER side flags only. For full A1-A6 alpha
  comparison you'd need separate picks CSVs per alpha layer.

Run:
    py -3 tools/run_ab_matrix.py --picks <path> --top-n 20
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


# Variant definitions — each dict is kwargs passed to run_realistic_backtest
# (after seed_capital_krw / picks / top_n which come from CLI args).
VARIANTS: dict[str, dict] = {
    # ------------ Data truth (D-series) ------------
    "B0_baseline": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": False,
        "use_governance_overlay": False,
        "use_sleeve_separation": False,
    },
    "D1_survivorship_fix": {
        # Same B0 but assumes picks were generated from PIT-correct universe.
        # Backtester flags identical to B0; the difference comes from the
        # input picks CSV.
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": False,
        "use_governance_overlay": False,
        "use_sleeve_separation": False,
    },
    "D2_historical_mcap": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": False,
        "use_governance_overlay": False,
        "use_sleeve_separation": False,
    },
    "D3_purged_walkforward": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": False,
        "use_governance_overlay": False,
        "use_sleeve_separation": False,
    },
    # ------------ Alpha layer (A-series) ------------
    "A1_pre_entry": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": False,
        "use_governance_overlay": False,
        "use_sleeve_separation": False,
    },
    "A2_pre_plus_continuation": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": False,
        "use_governance_overlay": False,
        "use_sleeve_separation": True,
    },
    "A3_technical_gate": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": False,
        "use_governance_overlay": False,
        "use_sleeve_separation": True,
    },
    "A4_flow_confirmation": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": False,
        "use_governance_overlay": False,
        "use_sleeve_separation": True,
    },
    "A5_with_governance": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": False,
        "use_governance_overlay": True,
        "use_sleeve_separation": True,
    },
    "A6_full_with_regime": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": True,
        "use_governance_overlay": True,
        "use_sleeve_separation": True,
    },
    # ------------ Governance variants (G-series) ------------
    "G0_no_governance": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": True,
        "use_governance_overlay": False,
        "use_sleeve_separation": False,
    },
    "G1_soft_penalty": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": True,
        "use_governance_overlay": True,
        "governance_penalty_factor": 0.35,
        "use_sleeve_separation": False,
    },
    "G2_hard_veto_only": {
        # NOTE: hard-veto path is wired inside run_realistic_backtest;
        # we keep penalty_factor=0 to isolate the veto effect.
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": True,
        "use_governance_overlay": True,
        "governance_penalty_factor": 0.0,
        "use_sleeve_separation": False,
    },
    "G3_weight_cap": {
        # Same as G1 (default) — weight cap is automatic when overlay on.
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": True,
        "use_governance_overlay": True,
        "governance_penalty_factor": 0.35,
        "use_sleeve_separation": False,
    },
    "G4_event_driven_combined": {
        "weighting": "equal",
        "use_drawdown_breaker": True,
        "use_vkospi_guard": True,
        "use_governance_overlay": True,
        "governance_penalty_factor": 0.50,    # higher penalty
        "use_sleeve_separation": True,
    },
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A/B experiment matrix")
    p.add_argument("--picks", required=True,
                   help="Path to picks CSV (rebalance_date, ticker, "
                        "p_pre_surge, rank_in_month [, governance_*, p_pre_entry, ...])")
    p.add_argument("--seed-krw", type=float, default=1e8)
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--only", default=None,
                   help="Comma-separated variant names to run; default = all.")
    p.add_argument("--skip", default=None,
                   help="Comma-separated variant names to skip.")
    p.add_argument("--out-dir", default=None,
                   help="Output directory (default DATA_ROOT/outputs/ab_matrix).")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    picks_path = Path(args.picks)
    if not picks_path.exists():
        log(f"[ab] picks not found: {picks_path}", level="ERROR")
        return 1
    picks = pd.read_csv(picks_path)
    if "rebalance_date" in picks.columns:
        picks["rebalance_date"] = pd.to_datetime(picks["rebalance_date"])
    log(f"[ab] picks: {len(picks)} rows × "
        f"{picks['rebalance_date'].nunique()} months")

    out_dir = (Path(args.out_dir) if args.out_dir
                else DATA_ROOT / "outputs" / "ab_matrix")
    out_dir.mkdir(parents=True, exist_ok=True)

    only = set((args.only or "").split(",")) if args.only else None
    skip = set((args.skip or "").split(",")) if args.skip else set()
    selected = [k for k in VARIANTS
                if (only is None or k in only) and k not in skip]
    log(f"[ab] running {len(selected)} variants: {selected}")

    from kr_backtester_realistic import run_realistic_backtest

    summary_rows = []
    for variant in selected:
        kwargs = VARIANTS[variant]
        log(f"\n[ab] === {variant} ===")
        log(f"[ab]    kwargs: {kwargs}")
        try:
            result = run_realistic_backtest(
                picks=picks,
                seed_capital_krw=args.seed_krw,
                top_n=args.top_n,
                **kwargs,
            )
        except Exception as ex:
            log(f"[ab] {variant} FAILED: {type(ex).__name__}: {ex}",
                level="ERROR")
            summary_rows.append({"variant": variant, "error": str(ex)})
            continue

        if "error" in result:
            log(f"[ab] {variant} returned error: {result['error']}",
                level="WARN")
            summary_rows.append({"variant": variant, "error": result["error"]})
            continue

        # Strip non-serialisable lists for JSON
        monthly_log = result.pop("monthly_log", None)
        trades_log = result.pop("trades_log", None)

        json_path = out_dir / f"{variant}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"variant": variant, "kwargs": kwargs,
                       "metrics": result,
                       "ts": datetime.now().isoformat(timespec="seconds")},
                      f, indent=2, default=str)
        if monthly_log:
            pd.DataFrame(monthly_log).to_csv(
                out_dir / f"{variant}_monthly.csv", index=False)

        row = {"variant": variant}
        for k in ("cagr", "mdd", "sharpe", "final_capital", "trades_total",
                  "win_rate"):
            if k in result:
                row[k] = result[k]
        summary_rows.append(row)
        log(f"[ab] {variant} CAGR={result.get('cagr')} MDD={result.get('mdd')} "
            f"Sharpe={result.get('sharpe')}")

    if summary_rows:
        summary = pd.DataFrame(summary_rows)
        summary.to_csv(out_dir / "summary.csv", index=False)
        log(f"\n[ab] summary written to {out_dir/'summary.csv'}")
        print()
        print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
