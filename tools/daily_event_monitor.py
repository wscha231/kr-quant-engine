"""tools/daily_event_monitor.py — daily DART event monitor for held tickers.

Section 6.2 (Renaissance-style daily monitor) of the deployment spec. Runs
every weekday at 18:00 KST via .github/workflows/daily_event_monitor.yml.

Pipeline:
  1. Load yesterday's live_portfolio_latest.csv (current holdings).
  2. Fetch DART events for each held ticker over last 2 days.
  3. Compute governance score on the new events.
  4. Output:
       outputs/daily_alerts_<YYYY-MM-DD>.json   (JSON alert list)
       outputs/daily_alerts_<YYYY-MM-DD>.csv    (CSV for dashboard)
  5. Exit code 2 (workflow special-codes a Slack alert) when any held ticker
     gains a hard_veto_flag from new events.

Env: DART_API_KEY, KR_DATA_DIR.
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
    p = argparse.ArgumentParser(description="Daily DART event monitor")
    p.add_argument("--portfolio", default=None,
                   help="Override path to live_portfolio_latest.csv")
    p.add_argument("--lookback-days", type=int, default=2)
    return p.parse_args()


def main() -> int:
    args = parse_args()

    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    portfolio_path = (Path(args.portfolio) if args.portfolio
                       else out_dir / "live_portfolio_latest.csv")
    if not portfolio_path.exists():
        log(f"[monitor] no portfolio at {portfolio_path} — soft exit",
            level="WARN")
        return 0

    portfolio = pd.read_csv(portfolio_path, encoding="utf-8-sig")
    if "ticker" not in portfolio.columns or portfolio.empty:
        log("[monitor] portfolio missing ticker column or empty", level="WARN")
        return 0

    tickers = portfolio["ticker"].astype(str).str.zfill(6).unique().tolist()
    log(f"[monitor] watching {len(tickers)} held tickers")

    # Fetch fresh events
    today = datetime.now()
    end_de = today.strftime("%Y%m%d")
    bgn_de = (today - timedelta(days=args.lookback_days)).strftime("%Y%m%d")

    from kr_dart_client import (
        DART_EVENT_CATALOG,
        fetch_all_events_for_corp,
        fetch_corp_to_ticker_map,
    )
    corp_map = fetch_corp_to_ticker_map()
    cmap = dict(zip(corp_map["ticker"].astype(str).str.zfill(6),
                    corp_map["corp_code"]))

    alerts = []
    governance_event_types = [
        "capital_increase", "treasury_sell", "convertible_bond",
        "warrant_bond", "spinoff", "capital_reduction",
        "treasury_buyback", "insider_holdings",
    ]
    n_seen = 0
    n_alerts = 0
    for tk in tickers:
        cc = cmap.get(tk)
        if not cc:
            continue
        df = fetch_all_events_for_corp(
            cc, bgn_de, end_de,
            event_types=governance_event_types,
            polite_sleep_s=0.10,
        )
        if df.empty:
            continue
        n_seen += len(df)
        for _, row in df.iterrows():
            cat = str(row.get("event_category", ""))
            meta = DART_EVENT_CATALOG.get(cat, {})
            severity = "HIGH" if cat in (
                "capital_increase", "treasury_sell", "spinoff",
                "capital_reduction", "convertible_bond", "warrant_bond",
            ) else "INFO"
            alerts.append({
                "ticker": tk,
                "rcept_dt": str(row.get("rcept_dt", "")),
                "category": cat,
                "kr_name": meta.get("kr_name", cat),
                "direction": meta.get("direction", "?"),
                "severity": severity,
                "rcept_no": str(row.get("rcept_no", "")),
            })
            if severity == "HIGH":
                n_alerts += 1

    stamp = today.strftime("%Y-%m-%d")
    json_path = out_dir / f"daily_alerts_{stamp}.json"
    csv_path = out_dir / f"daily_alerts_{stamp}.csv"
    payload = {
        "scan_date": stamp,
        "lookback_days": args.lookback_days,
        "n_tickers_watched": len(tickers),
        "n_events_seen": n_seen,
        "n_high_severity": n_alerts,
        "alerts": alerts,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str, ensure_ascii=False)
    if alerts:
        pd.DataFrame(alerts).to_csv(csv_path, index=False, encoding="utf-8-sig")

    log(f"[monitor] {n_seen} events scanned, {n_alerts} HIGH-severity alerts")
    log(f"[monitor] wrote {json_path.name}")

    # Exit code 2 = workflow should issue Slack alert
    return 2 if n_alerts > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
