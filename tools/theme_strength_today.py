"""tools/theme_strength_today.py -- Phase G4 live theme dashboard.

Daily-runnable dashboard:
  1) Fetches the latest 250 days of KOSPI200 + every KRX 27 industry index
     and every named theme in themes.yaml.
  2) Ranks them by RS_kospi_60d (long horizon) and RS_kospi_20d
     (short horizon).
  3) Classifies each theme's CURRENT stage (Wyckoff).
  4) Prints two leaderboards (named themes + KRX industries) +
     a "stage transitions today" section.
  5) Optionally writes outputs/theme_today_<date>.json for
     dashboard/Slack consumption.

Usage:
    py -3 tools/theme_strength_today.py [--show-bottom] [--no-krx]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402
from kr_helpers import log  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live theme strength dashboard")
    p.add_argument("--days", type=int, default=400,
                   help="Lookback days for strength panel (default 400).")
    p.add_argument("--no-krx", action="store_true",
                   help="Skip KRX 27 industry baseline (named themes only).")
    p.add_argument("--show-bottom", action="store_true",
                   help="Also print weakest 5 themes (markdown candidates).")
    p.add_argument("--top-n", type=int, default=10,
                   help="Top-N themes to show (default 10).")
    p.add_argument("--out", default=None,
                   help="JSON output path (default outputs/theme_today_<date>.json).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    today = datetime.now()
    start = (today - timedelta(days=args.days)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    from kr_themes import (
        load_themes_yaml,
        build_theme_strength_panel,
        build_krx_industry_strength_panel,
    )
    cfg = load_themes_yaml()
    if not cfg:
        log("[today] themes.yaml load fail", level="ERROR")
        return 1

    # Named themes
    log(f"[today] building named theme panel {start} -> {end}...")
    named = build_theme_strength_panel(cfg, start, end)
    if named.empty:
        log("[today] named theme panel empty", level="WARN")

    # KRX industries (auto-discovery baseline)
    krx = pd.DataFrame()
    if not args.no_krx:
        log(f"[today] building KRX 27 industry panel...")
        krx = build_krx_industry_strength_panel(cfg, start, end)
        if krx.empty:
            log("[today] KRX panel empty -- skipping", level="WARN")

    panels = []
    if not named.empty:
        named["source"] = "named"
        panels.append(named)
    if not krx.empty:
        krx["source"] = "krx_industry"
        panels.append(krx)
    if not panels:
        log("[today] all panels empty", level="ERROR")
        return 2
    panel = pd.concat(panels, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    latest_date = panel["date"].max()
    snap = panel[panel["date"] == latest_date].copy()
    log(f"[today] snapshot date {latest_date.date()}, "
        f"{len(snap)} themes/sectors")

    # Sort by composite leadership score: rs_kospi_60d + 0.5 * rs_kospi_20d
    snap["lead_score"] = (
        snap["rs_kospi_60d"].fillna(0)
        + 0.5 * snap["rs_kospi_20d"].fillna(0)
    )
    snap = snap.sort_values("lead_score", ascending=False)

    # Build report
    report = {
        "snapshot_date": latest_date.strftime("%Y-%m-%d"),
        "n_themes_total": int(len(snap)),
        "stages": snap["stage"].value_counts().to_dict(),
        "top": [],
        "bottom": [],
    }
    cols_show = ["theme_name_kr", "theme_key", "source", "stage",
                 "abs_return_20d", "abs_return_60d",
                 "rs_kospi_20d", "rs_kospi_60d", "rs_kospi_252d"]
    for c in cols_show:
        if c not in snap.columns:
            snap[c] = None

    print()
    print("=" * 88)
    print(f"  Theme leaderboard -- {latest_date.strftime('%Y-%m-%d')}")
    print("=" * 88)
    header = f"  {'#':>3}  {'name':<24} {'stage':<12} {'rs60d':>7} {'rs20d':>7} {'src':<14}"
    print(header)
    print("  " + "-" * 80)
    top = snap.head(args.top_n)
    for i, (_, r) in enumerate(top.iterrows(), 1):
        name = (str(r.get("theme_name_kr") or r.get("theme_key") or "")[:24]).ljust(24)
        stage = (str(r.get("stage") or "")[:12]).ljust(12)
        rs60 = r.get("rs_kospi_60d") or 0
        rs20 = r.get("rs_kospi_20d") or 0
        src = (str(r.get("source") or "")[:14]).ljust(14)
        print(f"  {i:>3}  {name} {stage} {rs60:+7.2%} {rs20:+7.2%} {src}")
        report["top"].append({
            "rank": i,
            "theme_name_kr": str(r.get("theme_name_kr") or ""),
            "theme_key": str(r.get("theme_key") or ""),
            "source": str(r.get("source") or ""),
            "stage": str(r.get("stage") or ""),
            "rs_kospi_20d": float(r.get("rs_kospi_20d") or 0),
            "rs_kospi_60d": float(r.get("rs_kospi_60d") or 0),
            "rs_kospi_252d": float(r.get("rs_kospi_252d") or 0),
            "abs_return_20d": float(r.get("abs_return_20d") or 0),
            "abs_return_60d": float(r.get("abs_return_60d") or 0),
        })

    if args.show_bottom:
        print()
        print("  bottom 5 (markdown candidates)")
        print("  " + "-" * 80)
        bot = snap.tail(5)
        for i, (_, r) in enumerate(bot.iterrows(), 1):
            name = (str(r.get("theme_name_kr") or r.get("theme_key") or "")[:24]).ljust(24)
            stage = (str(r.get("stage") or "")[:12]).ljust(12)
            rs60 = r.get("rs_kospi_60d") or 0
            rs20 = r.get("rs_kospi_20d") or 0
            print(f"  {i:>3}  {name} {stage} {rs60:+7.2%} {rs20:+7.2%}")
            report["bottom"].append({
                "rank": i,
                "theme_name_kr": str(r.get("theme_name_kr") or ""),
                "stage": str(r.get("stage") or ""),
                "rs_kospi_60d": float(r.get("rs_kospi_60d") or 0),
                "rs_kospi_20d": float(r.get("rs_kospi_20d") or 0),
            })

    # Stage transitions (today vs yesterday)
    yesterday = latest_date - pd.Timedelta(days=1)
    while panel[panel["date"] == yesterday].empty and yesterday > latest_date - pd.Timedelta(days=10):
        yesterday -= pd.Timedelta(days=1)
    if not panel[panel["date"] == yesterday].empty:
        prev = panel[panel["date"] == yesterday][["theme_key", "stage"]] \
            .rename(columns={"stage": "prev_stage"})
        merged = snap.merge(prev, on="theme_key", how="left")
        transitions = merged[merged["stage"] != merged["prev_stage"]].dropna(
            subset=["prev_stage"]
        )
        if not transitions.empty:
            print()
            print(f"  stage transitions vs {yesterday.strftime('%Y-%m-%d')}")
            print("  " + "-" * 80)
            for _, r in transitions.iterrows():
                print(f"  {r['theme_name_kr']:<24} "
                      f"{r['prev_stage']:>12} -> {r['stage']:<12} "
                      f"(rs60d {(r.get('rs_kospi_60d') or 0):+.1%})")
            report["transitions"] = [
                {"theme_name_kr": r["theme_name_kr"],
                 "from": r["prev_stage"], "to": r["stage"],
                 "rs_kospi_60d": float(r.get("rs_kospi_60d") or 0)}
                for _, r in transitions.iterrows()
            ]

    print()
    print("=" * 88)

    # Persist
    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = (Path(args.out) if args.out
                else out_dir / f"theme_today_{latest_date.strftime('%Y-%m-%d')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    log(f"[today] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
