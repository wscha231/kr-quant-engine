"""tools/phase_ab_quick.py — Phase contribution A/B measurement.

Runs backtest with different phase combinations and measures excess
CAGR vs P0-only baseline.

Usage:
    py -3 tools/phase_ab_quick.py --start 2024-03-29 --end 2024-12-30 --n 30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from kr_helpers import log


PHASE_TOGGLES = {
    "P0_only": {
        "PHASE_PHASE0_MOMENTUM_ENABLED": "1",
        "PHASE_PHASE1_FUNDAMENTAL_ENABLED": "0",
        "PHASE_PHASE2_DART_EVENTS_ENABLED": "0",
        "PHASE_PHASE2_FLOW_ENABLED": "0",
        "PHASE_PHASE2_DERIVATIVES_ENABLED": "0",
        "PHASE_PHASE3_TECHNICAL_ENABLED": "0",
        "PHASE_PHASE3_MACRO_ENABLED": "0",
        "PHASE_PHASE3_REGIME_ENABLED": "0",
    },
    "P0+P1": {
        "PHASE_PHASE0_MOMENTUM_ENABLED": "1",
        "PHASE_PHASE1_FUNDAMENTAL_ENABLED": "1",
        "PHASE_PHASE2_DART_EVENTS_ENABLED": "0",
        "PHASE_PHASE2_FLOW_ENABLED": "0",
        "PHASE_PHASE2_DERIVATIVES_ENABLED": "0",
        "PHASE_PHASE3_TECHNICAL_ENABLED": "0",
        "PHASE_PHASE3_MACRO_ENABLED": "0",
        "PHASE_PHASE3_REGIME_ENABLED": "0",
    },
    "P0+P2_events": {
        "PHASE_PHASE0_MOMENTUM_ENABLED": "1",
        "PHASE_PHASE1_FUNDAMENTAL_ENABLED": "0",
        "PHASE_PHASE2_DART_EVENTS_ENABLED": "1",
        "PHASE_PHASE2_FLOW_ENABLED": "0",
        "PHASE_PHASE2_DERIVATIVES_ENABLED": "0",
        "PHASE_PHASE3_TECHNICAL_ENABLED": "0",
        "PHASE_PHASE3_MACRO_ENABLED": "0",
        "PHASE_PHASE3_REGIME_ENABLED": "0",
    },
    "P0+P2.5_flow": {
        "PHASE_PHASE0_MOMENTUM_ENABLED": "1",
        "PHASE_PHASE1_FUNDAMENTAL_ENABLED": "0",
        "PHASE_PHASE2_DART_EVENTS_ENABLED": "0",
        "PHASE_PHASE2_FLOW_ENABLED": "1",
        "PHASE_PHASE2_DERIVATIVES_ENABLED": "0",
        "PHASE_PHASE3_TECHNICAL_ENABLED": "0",
        "PHASE_PHASE3_MACRO_ENABLED": "0",
        "PHASE_PHASE3_REGIME_ENABLED": "0",
    },
    "P0+P3.1_technical": {
        "PHASE_PHASE0_MOMENTUM_ENABLED": "1",
        "PHASE_PHASE1_FUNDAMENTAL_ENABLED": "0",
        "PHASE_PHASE2_DART_EVENTS_ENABLED": "0",
        "PHASE_PHASE2_FLOW_ENABLED": "0",
        "PHASE_PHASE2_DERIVATIVES_ENABLED": "0",
        "PHASE_PHASE3_TECHNICAL_ENABLED": "1",
        "PHASE_PHASE3_MACRO_ENABLED": "0",
        "PHASE_PHASE3_REGIME_ENABLED": "0",
    },
    "ALL": {
        "PHASE_PHASE0_MOMENTUM_ENABLED": "1",
        "PHASE_PHASE1_FUNDAMENTAL_ENABLED": "1",
        "PHASE_PHASE2_DART_EVENTS_ENABLED": "1",
        "PHASE_PHASE2_FLOW_ENABLED": "1",
        "PHASE_PHASE2_DERIVATIVES_ENABLED": "1",
        "PHASE_PHASE3_TECHNICAL_ENABLED": "1",
        "PHASE_PHASE3_MACRO_ENABLED": "1",
        "PHASE_PHASE3_REGIME_ENABLED": "1",
    },
}


def apply_env(toggles: dict) -> dict:
    """Set env vars from toggles dict, return previous values for rollback."""
    prev = {}
    for k, v in toggles.items():
        prev[k] = os.environ.get(k)
        os.environ[k] = v
    return prev


def restore_env(prev: dict):
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def run_one_combo(
    combo_name: str,
    universe_tickers: list[str],
    rebal_dates: list[pd.Timestamp],
    panels: dict,
    top_n: int = 30,
    round_trip_cost: float = 0.0031,
) -> dict:
    """Run backtest for one phase combination on pre-built universe + panels."""
    from kr_pykrx_client import fetch_listing, fetch_ticker_history, fetch_index_ohlcv
    from kr_features import add_universe_features

    log(f"\n{'=' * 60}\nCombo: {combo_name}\n{'=' * 60}")

    # Apply toggles
    prev = apply_env(PHASE_TOGGLES[combo_name])
    try:
        # Build universe DataFrame from tickers + listing snapshot
        listing = fetch_listing(rebal_dates[-1].strftime("%Y%m%d"),
                                  market="ALL", refresh_days=30)
        listing = listing[listing["ticker"].astype(str).isin(universe_tickers)].copy()
        listing["eligible"] = True
        if "exchange" not in listing.columns:
            listing["exchange"] = listing.get("market_src", "KOSPI")

        prev_holdings = set()
        monthly = []

        for i, rd in enumerate(rebal_dates):
            df = listing.copy()
            # Run all features (phases gated by env vars)
            df = add_universe_features(
                df, rd,
                fund_panel=panels.get("fund_panel"),
                event_panel=panels.get("event_panel"),
                macro_panel=panels.get("macro_panel"),
                market_flow_panel=panels.get("market_flow_panel"),
                ticker_flow_panel=panels.get("ticker_flow_panel"),
                foreign_holding_panel=panels.get("foreign_holding_panel"),
                derivatives_panel=panels.get("derivatives_panel"),
            )
            # Determine score column based on enabled phases
            score_col = "p2_blended_score"
            if score_col not in df.columns or df[score_col].isna().all():
                score_col = "p1_blended_score"
            if score_col not in df.columns or df[score_col].isna().all():
                score_col = "p0_momentum_score"

            top = df.sort_values(score_col, ascending=False).head(top_n)
            new_holdings = set(top["ticker"].astype(str))
            turnover = (
                len(new_holdings.symmetric_difference(prev_holdings))
                / (2 * top_n) if prev_holdings else 1.0
            )
            cost = turnover * round_trip_cost

            if i + 1 < len(rebal_dates):
                next_rd = rebal_dates[i + 1]
                rets = []
                for tk in new_holdings:
                    hist = fetch_ticker_history(
                        tk, rd.strftime("%Y%m%d"),
                        (next_rd + pd.Timedelta(days=5)).strftime("%Y%m%d"),
                        refresh_days=30,
                    )
                    if hist.empty:
                        continue
                    hist = hist.sort_values("date")
                    p0_sub = hist[hist["date"] <= rd]["close"]
                    p1_sub = hist[hist["date"] <= next_rd]["close"]
                    if not p0_sub.empty and not p1_sub.empty:
                        p0v = float(p0_sub.iloc[-1])
                        p1v = float(p1_sub.iloc[-1])
                        if p0v > 0:
                            rets.append(p1v / p0v - 1)
                gross = float(np.mean(rets)) if rets else 0.0
                net = gross - cost
                monthly.append({
                    "rd": rd, "next_rd": next_rd, "n_priced": len(rets),
                    "turnover": turnover, "gross": gross, "cost": cost, "net": net,
                    "score_col_used": score_col,
                })
                log(f"  {rd.strftime('%Y-%m-%d')} → {next_rd.strftime('%Y-%m-%d')}: "
                    f"gross {gross*100:+.2f}%, turnover {turnover*100:.0f}%, "
                    f"net {net*100:+.2f}%  [score={score_col}]")
            prev_holdings = new_holdings
    finally:
        restore_env(prev)

    if not monthly:
        return {"combo": combo_name, "error": "no periods"}

    df_m = pd.DataFrame(monthly)
    cum = float((1 + df_m["net"]).prod()) - 1.0
    annualized = (1 + cum) ** (12 / max(len(df_m), 1)) - 1.0 if cum > -1.0 else -1.0

    return {
        "combo": combo_name,
        "n_periods": len(df_m),
        "cumulative": cum,
        "annualized": annualized,
        "avg_turnover": float(df_m["turnover"].mean()),
        "total_cost": float(df_m["cost"].sum()),
        "monthly": df_m.to_dict(orient="records"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-03-29")
    parser.add_argument("--end", default="2024-12-30")
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--universe-size", type=int, default=200,
                          help="Top N by mcap (KOSPI). If --include-kosdaq, "
                               "also adds top --kosdaq-size from KOSDAQ.")
    parser.add_argument("--include-kosdaq", action="store_true",
                          help="Add KOSDAQ top-mcap to universe")
    parser.add_argument("--kosdaq-size", type=int, default=200)
    parser.add_argument("--combos", nargs="+",
                          default=["P0_only", "P0+P3.1_technical", "ALL"])
    args = parser.parse_args()

    from kr_pykrx_client import fetch_listing, fetch_index_ohlcv

    universe_label = (
        f"KOSPI top {args.universe_size}"
        + (f" + KOSDAQ top {args.kosdaq_size}" if args.include_kosdaq else "")
    )
    log(f"=== Phase A/B QUICK: {args.start} → {args.end}, top {args.n} of "
        f"({universe_label}) ===")

    # Build universe (KOSPI top N by mcap, optionally + KOSDAQ top M)
    kospi = fetch_listing(args.end.replace("-", ""), market="KOSPI", refresh_days=30)
    if "market_cap" not in kospi.columns:
        log("ERROR: KOSPI listing has no market_cap column", level="ERROR")
        return 1
    kospi = kospi.sort_values("market_cap", ascending=False).head(args.universe_size)
    listing_parts = [kospi]
    if args.include_kosdaq:
        kosdaq = fetch_listing(args.end.replace("-", ""), market="KOSDAQ", refresh_days=30)
        if "market_cap" in kosdaq.columns:
            kosdaq = kosdaq.sort_values("market_cap", ascending=False).head(args.kosdaq_size)
            listing_parts.append(kosdaq)
    listing = pd.concat(listing_parts, ignore_index=True)
    tickers = listing["ticker"].astype(str).tolist()
    log(f"Universe: {len(tickers)} tickers "
        f"({(listing['market_src']=='KOSPI').sum() if 'market_src' in listing.columns else 'KOSPI ?'} KOSPI"
        f"{', ' + str(len(tickers) - args.universe_size) + ' KOSDAQ' if args.include_kosdaq else ''})")

    # Quarter rebalance dates
    rebal_dates = pd.date_range(args.start, args.end, freq="QE").tolist()
    if len(rebal_dates) < 2:
        rebal_dates = [pd.Timestamp(args.start), pd.Timestamp(args.end)]

    log(f"Rebal dates: {[d.strftime('%Y-%m-%d') for d in rebal_dates]}")

    # Panels: empty placeholders (only macro is cheap to build)
    panels = {}
    try:
        from kr_macro import build_macro_panel
        panels["macro_panel"] = build_macro_panel(args.start, args.end, refresh_days=7)
    except Exception as e:
        log(f"macro panel build fail: {e}", level="WARN")

    # Run combos
    results = {}
    for combo in args.combos:
        if combo not in PHASE_TOGGLES:
            log(f"unknown combo: {combo}", level="WARN")
            continue
        results[combo] = run_one_combo(combo, tickers, rebal_dates, panels, top_n=args.n)

    # Benchmark KOSPI200
    bench = fetch_index_ohlcv(
        "1028", rebal_dates[0].strftime("%Y%m%d"),
        rebal_dates[-1].strftime("%Y%m%d"), refresh_days=30,
    )
    bench_ret = 0.0
    if not bench.empty:
        bench = bench.sort_values("date")
        bench_ret = float(bench["close"].iloc[-1] / bench["close"].iloc[0] - 1.0)

    # Summary
    print()
    print("=" * 70)
    print("PHASE A/B SUMMARY")
    print("=" * 70)
    print(f"  KOSPI200 cum return: {bench_ret*100:+.2f}%")
    print()
    print(f"  {'Combo':<25} {'Cum':>8} {'Annualized':>12} {'Turnover':>10} {'Excess':>10}")
    print(f"  {'-'*25} {'-'*8} {'-'*12} {'-'*10} {'-'*10}")
    for combo, r in results.items():
        if "error" in r:
            continue
        print(f"  {combo:<25} {r['cumulative']*100:>+7.2f}% "
              f"{r['annualized']*100:>+11.2f}% "
              f"{r['avg_turnover']*100:>9.0f}% "
              f"{(r['cumulative'] - bench_ret)*100:>+9.2f}pp")

    # Save
    out_dir = PROJECT_ROOT / "research" / "07_phase_experiments"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"phase_ab_quick_{args.start}_{args.end}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "args": vars(args),
            "benchmark_cum_return": bench_ret,
            "rebal_dates": [d.strftime("%Y-%m-%d") for d in rebal_dates],
            "results": results,
        }, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n  Saved: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
