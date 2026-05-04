"""tools/dip_monitor_simple.py -- rule-based shake-out vs distribution monitor.

Phase F Section 9 (research/09_shakeout_vs_distribution/research.md).
First operational deliverable: no ML required, runs against current
live picks file + KRX foreign / institutional / individual flow data
to flag dip events on held tickers and recommend HOLD / REVIEW_SELL.

Logic
-----
For each held ticker:
  - Compute 1-day return (today close / yesterday close - 1).
  - When 1-day < -5pct OR 5-day cumulative drawdown < -10pct, mark as DIP.
  - Pull 5-day foreign / institutional / individual net buy values.
  - Apply rules table:
      foreign 5d > threshold AND inst 5d >= 0       -> LIKELY_SHAKEOUT (hold)
      foreign 5d < -threshold OR inst 5d < -threshold -> LIKELY_DISTRIBUTION (review)
      else                                          -> AMBIGUOUS (manual)
  - threshold = max(mcap * 0.001, 5e8) -- proportional to mcap, min 5억.
  - Emit Slack alert (workflow handles) + JSON / CSV.

Output
------
  outputs/dip_monitor_<YYYY-MM-DD>.json
  outputs/dip_monitor_<YYYY-MM-DD>.csv     (alerts only)

Exit codes
----------
  0  no dip events
  2  one or more LIKELY_DISTRIBUTION alerts (workflow Slack-pings on this)
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


# Detection thresholds (sensible defaults; override via CLI)
DEFAULT_DIP_1D_THRESHOLD = -0.05      # -5pct in one day
DEFAULT_DIP_5D_THRESHOLD = -0.10      # -10pct rolling 5-day drawdown
DEFAULT_FLOW_PCT_OF_MCAP = 0.001       # 0.1pct of mcap as flow threshold
DEFAULT_FLOW_MIN_KRW = 5e8             # 5억 minimum threshold
DEFAULT_LOOKBACK_DAYS = 7              # OHLCV + flow lookback


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily dip monitor (rule-based)")
    p.add_argument("--portfolio", default=None,
                   help="Path to portfolio CSV (default outputs/live_portfolio_latest.csv).")
    p.add_argument("--tickers", default=None,
                   help="Comma-separated extra tickers to watch.")
    p.add_argument("--dip-1d", type=float, default=DEFAULT_DIP_1D_THRESHOLD,
                   help="1-day return threshold for dip (default -0.05).")
    p.add_argument("--dip-5d", type=float, default=DEFAULT_DIP_5D_THRESHOLD,
                   help="5-day cumulative drawdown threshold (default -0.10).")
    p.add_argument("--flow-pct-mcap", type=float, default=DEFAULT_FLOW_PCT_OF_MCAP,
                   help="Flow threshold as fraction of mcap (default 0.001).")
    p.add_argument("--flow-min-krw", type=float, default=DEFAULT_FLOW_MIN_KRW,
                   help="Minimum flow threshold in KRW (default 5e8).")
    p.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                   help="OHLCV + flow lookback window (calendar days).")
    return p.parse_args()


def _classify_dip(
    ticker: str,
    name: str,
    mcap: float,
    today_close: float,
    yesterday_close: float,
    five_day_low: float,
    five_day_peak: float,
    foreign_net_5d: float,
    inst_net_5d: float,
    individual_net_5d: float,
    flow_threshold_krw: float,
    dip_1d_threshold: float,
    dip_5d_threshold: float,
) -> dict:
    """Apply the rules table to one ticker, return verdict dict."""
    if yesterday_close <= 0 or five_day_peak <= 0:
        return {}
    dip_1d = (today_close / yesterday_close) - 1.0
    dip_5d = (five_day_low / five_day_peak) - 1.0
    is_dip = dip_1d < dip_1d_threshold or dip_5d < dip_5d_threshold
    if not is_dip:
        return {}

    # Smart-money flow direction
    foreign_pos = foreign_net_5d > flow_threshold_krw
    foreign_neg = foreign_net_5d < -flow_threshold_krw
    inst_pos = inst_net_5d > flow_threshold_krw
    inst_neg = inst_net_5d < -flow_threshold_krw

    if foreign_pos and (inst_pos or inst_net_5d >= 0):
        verdict = "LIKELY_SHAKEOUT"
        action = "HOLD"
        confidence = "HIGH" if (foreign_pos and inst_pos) else "MEDIUM"
    elif foreign_neg or inst_neg:
        verdict = "LIKELY_DISTRIBUTION"
        action = "REVIEW_SELL"
        confidence = "HIGH" if (foreign_neg and inst_neg) else "MEDIUM"
    else:
        verdict = "AMBIGUOUS"
        action = "MANUAL"
        confidence = "LOW"

    severity = "HIGH" if dip_1d < -0.10 or dip_5d < -0.20 else "INFO"

    return {
        "ticker": ticker,
        "name": name,
        "market_cap": mcap,
        "today_close": today_close,
        "dip_1d_pct": float(dip_1d),
        "dip_5d_pct": float(dip_5d),
        "foreign_net_5d_krw": float(foreign_net_5d),
        "inst_net_5d_krw": float(inst_net_5d),
        "individual_net_5d_krw": float(individual_net_5d),
        "flow_threshold_krw": float(flow_threshold_krw),
        "verdict": verdict,
        "action": action,
        "confidence": confidence,
        "severity": severity,
    }


def main() -> int:
    args = parse_args()
    today = datetime.now()
    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load held tickers
    portfolio_path = Path(args.portfolio) if args.portfolio \
        else out_dir / "live_portfolio_latest.csv"
    held_tickers: list[tuple[str, str, float]] = []
    if portfolio_path.exists():
        try:
            df = pd.read_csv(portfolio_path, encoding="utf-8-sig")
            for _, r in df.iterrows():
                tk = str(r.get("ticker", "")).zfill(6)
                if not tk or tk == "000000":
                    continue
                name = str(r.get("name", ""))
                mcap = float(r.get("market_cap") or 0.0)
                held_tickers.append((tk, name, mcap))
            log(f"[dip] portfolio loaded: {len(held_tickers)} tickers")
        except Exception as e:
            log(f"[dip] portfolio load fail: {e}", level="WARN")
    else:
        log(f"[dip] no portfolio at {portfolio_path}", level="WARN")

    # Extra tickers from CLI
    if args.tickers:
        for tk in args.tickers.split(","):
            tk = tk.strip().zfill(6)
            if tk and tk not in {t for t, *_ in held_tickers}:
                held_tickers.append((tk, "", 0.0))

    if not held_tickers:
        log("[dip] no tickers to monitor -- soft exit", level="WARN")
        return 0

    # 2. Pull recent OHLCV + flow per ticker
    from kr_pykrx_client import (
        fetch_ticker_history,
        fetch_foreign_inst_flow,
    )

    end = today.strftime("%Y%m%d")
    start = (today - timedelta(days=args.lookback_days * 2 + 5)).strftime("%Y%m%d")

    # Bulk flow per business day (cheaper than per-ticker)
    flow_panel = pd.DataFrame()
    for d in pd.date_range(today - timedelta(days=args.lookback_days),
                            today, freq="B"):
        ds = d.strftime("%Y%m%d")
        try:
            df = fetch_foreign_inst_flow(ds, market="ALL", refresh_days=1)
            if not df.empty:
                df["ticker"] = df["ticker"].astype(str).str.zfill(6)
                df["date"] = pd.to_datetime(d).normalize()
                flow_panel = pd.concat([flow_panel, df], ignore_index=True)
        except Exception as e:
            log(f"[dip] flow fetch fail {ds}: {e}", level="WARN")

    if flow_panel.empty:
        log("[dip] no flow data fetched -- flow checks will be neutral",
            level="WARN")

    log(f"[dip] flow panel: {len(flow_panel)} rows, "
        f"{flow_panel['date'].nunique() if not flow_panel.empty else 0} days")

    # 3. Iterate tickers
    alerts: list[dict] = []
    for i, (tk, name, mcap) in enumerate(held_tickers, 1):
        if i % 5 == 0:
            log(f"[dip] processing {i}/{len(held_tickers)}")
        try:
            hist = fetch_ticker_history(tk, start, end, refresh_days=1)
        except Exception as e:
            log(f"[dip] ohlcv fail {tk}: {e}", level="WARN")
            continue
        if hist.empty or "close" not in hist.columns or len(hist) < 6:
            continue
        hist = hist.sort_values("date").reset_index(drop=True)
        today_close = float(hist["close"].iloc[-1])
        yest_close = float(hist["close"].iloc[-2])
        last_5 = hist.tail(5)
        five_day_low = float(last_5["low"].min() if "low" in last_5.columns
                              else last_5["close"].min())
        five_day_peak = float(last_5["high"].max() if "high" in last_5.columns
                               else last_5["close"].max())

        # Flow aggregation for this ticker
        if not flow_panel.empty:
            sub = flow_panel[flow_panel["ticker"] == tk]
            foreign_net_5d = float(sub.get("foreign_net_buy_value",
                                             pd.Series(dtype=float))
                                    .fillna(0).sum())
            inst_net_5d = float(sub.get("inst_net_buy_value",
                                          pd.Series(dtype=float))
                                  .fillna(0).sum())
            individual_net_5d = float(sub.get("individual_net_buy_value",
                                                pd.Series(dtype=float))
                                       .fillna(0).sum())
        else:
            foreign_net_5d = 0.0
            inst_net_5d = 0.0
            individual_net_5d = 0.0

        # Threshold proportional to mcap
        if mcap > 0:
            flow_threshold = max(mcap * args.flow_pct_mcap, args.flow_min_krw)
        else:
            flow_threshold = args.flow_min_krw

        verdict = _classify_dip(
            ticker=tk, name=name, mcap=mcap,
            today_close=today_close, yesterday_close=yest_close,
            five_day_low=five_day_low, five_day_peak=five_day_peak,
            foreign_net_5d=foreign_net_5d,
            inst_net_5d=inst_net_5d,
            individual_net_5d=individual_net_5d,
            flow_threshold_krw=flow_threshold,
            dip_1d_threshold=args.dip_1d,
            dip_5d_threshold=args.dip_5d,
        )
        if verdict:
            alerts.append(verdict)

    # 4. Persist outputs
    stamp = today.strftime("%Y-%m-%d")
    json_path = out_dir / f"dip_monitor_{stamp}.json"
    csv_path = out_dir / f"dip_monitor_{stamp}.csv"
    payload = {
        "scan_date": stamp,
        "n_tickers_monitored": len(held_tickers),
        "n_alerts": len(alerts),
        "n_distribution_alerts": int(sum(
            1 for a in alerts if a["verdict"] == "LIKELY_DISTRIBUTION"
        )),
        "n_shakeout_alerts": int(sum(
            1 for a in alerts if a["verdict"] == "LIKELY_SHAKEOUT"
        )),
        "n_ambiguous_alerts": int(sum(
            1 for a in alerts if a["verdict"] == "AMBIGUOUS"
        )),
        "thresholds": {
            "dip_1d": args.dip_1d,
            "dip_5d": args.dip_5d,
            "flow_pct_mcap": args.flow_pct_mcap,
            "flow_min_krw": args.flow_min_krw,
        },
        "alerts": alerts,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    if alerts:
        pd.DataFrame(alerts).to_csv(csv_path, index=False, encoding="utf-8-sig")

    log(f"[dip] {len(alerts)} alerts ("
        f"{payload['n_shakeout_alerts']} shake-out / "
        f"{payload['n_distribution_alerts']} distribution / "
        f"{payload['n_ambiguous_alerts']} ambiguous)")
    log(f"[dip] wrote {json_path.name}")
    return 2 if payload["n_distribution_alerts"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
