"""Daily KR1000 broker-rule account check.

This is the live operating bridge for KR1000 Leader Alpha. It evaluates the
account using the previous observable KRX close and emits a broker-rule trade
plan. Production status is blocked when the scored panel or data audit is
stale, even if a trade plan can still be generated for inspection.

Run:
    py -3 tools/run_kr1000_daily_broker_check.py
    py -3 tools/run_kr1000_daily_broker_check.py --run-date 2026-06-05
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, kr1000_leader_alpha_cfg  # noqa: E402
from kr_helpers import log  # noqa: E402
from kr_pykrx_client import fetch_business_days, fetch_ticker_history  # noqa: E402
from kr1000_leader import (  # noqa: E402
    build_target_portfolio,
    compute_leader_scores,
    generate_trade_plan,
    load_current_holdings,
)
from tools.audit_data_integrity import build_audit  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KR1000 daily broker-rule check")
    p.add_argument("--run-date", default=None,
                   help="Calendar run date. Default=today. Evaluation uses previous KRX close.")
    p.add_argument("--evaluation-date", default=None,
                   help="Override previous-close evaluation date.")
    p.add_argument("--scored-panel", default=None,
                   help="Optional scored_panel parquet/csv. Default=latest scored_panel_v0.")
    p.add_argument("--current-holdings", default=None,
                   help="Optional current holdings CSV. Default=state/current_holdings.csv.")
    p.add_argument("--out-dir", default=None,
                   help="Default DATA_ROOT/outputs.")
    p.add_argument("--max-signal-age-days", type=int, default=7,
                   help="Block production check when latest signal is older than this.")
    p.add_argument("--audit-stale-days", type=int, default=45)
    p.add_argument("--refresh-days", type=int, default=2,
                   help="Price cache TTL for held names.")
    return p.parse_args()


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype={"ticker": str})
    return pd.read_parquet(path)


def _latest_scored_panel_path() -> Path:
    fs = DATA_ROOT / "feature_store"
    files = sorted(fs.glob("scored_panel_v0_*.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No scored_panel_v0_*.parquet found in {fs}")
    return files[0]


def _yyyymmdd(day: pd.Timestamp) -> str:
    return pd.Timestamp(day).strftime("%Y%m%d")


def previous_krx_close_date(run_date: pd.Timestamp) -> pd.Timestamp:
    """Previous KRX business day strictly before run_date."""
    rd = pd.Timestamp(run_date).normalize()
    start = rd - pd.Timedelta(days=14)
    days = fetch_business_days(_yyyymmdd(start), _yyyymmdd(rd))
    days = [pd.Timestamp(d).normalize() for d in days if pd.Timestamp(d).normalize() < rd]
    if days:
        return max(days)
    # Fallback keeps the tool usable in offline test/dev contexts.
    return (rd - pd.offsets.BDay(1)).normalize()


def _latest_close_for_ticker(ticker: str, evaluation_date: pd.Timestamp, refresh_days: int) -> tuple[pd.Timestamp | None, float | None]:
    start = evaluation_date - pd.Timedelta(days=20)
    hist = fetch_ticker_history(ticker, _yyyymmdd(start), _yyyymmdd(evaluation_date), refresh_days=refresh_days)
    if hist is None or hist.empty or "date" not in hist.columns or "close" not in hist.columns:
        return None, None
    h = hist.copy()
    h["date"] = pd.to_datetime(h["date"], errors="coerce").dt.normalize()
    h["close"] = pd.to_numeric(h["close"], errors="coerce")
    h = h[h["date"] <= evaluation_date].dropna(subset=["date", "close"]).sort_values("date")
    if h.empty:
        return None, None
    row = h.iloc[-1]
    return pd.Timestamp(row["date"]).normalize(), float(row["close"])


def mark_holdings_to_previous_close(
    holdings: pd.DataFrame,
    evaluation_date: pd.Timestamp,
    refresh_days: int,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    if holdings.empty:
        return holdings.copy(), []
    out = holdings.copy()
    price_rows: list[dict[str, Any]] = []
    for idx, row in out.iterrows():
        ticker = str(row.get("ticker", "")).zfill(6)
        close_date, close = _latest_close_for_ticker(ticker, evaluation_date, refresh_days)
        if close is None:
            price_rows.append({"ticker": ticker, "status": "missing_price"})
            continue
        shares = float(row.get("shares", 0.0) or 0.0)
        avg_cost = float(row.get("avg_cost", 0.0) or 0.0)
        out.loc[idx, "last_price"] = close
        out.loc[idx, "market_value"] = shares * close
        out.loc[idx, "unrealized_pnl_pct"] = close / avg_cost - 1.0 if avg_cost > 0 else 0.0
        price_rows.append({
            "ticker": ticker,
            "status": "priced",
            "price_date": str(close_date.date()) if close_date is not None else "",
            "close": close,
        })
    total = float(pd.to_numeric(out.get("market_value"), errors="coerce").fillna(0.0).sum())
    out["weight"] = pd.to_numeric(out.get("market_value"), errors="coerce").fillna(0.0) / total if total > 0 else 0.0
    return out, price_rows


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# KR1000 Daily Broker Check",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Run date: `{payload.get('run_date')}`",
        f"- Evaluation close: `{payload.get('evaluation_date')}`",
        f"- Metric mode: `{payload.get('broker_rule', {}).get('metric_mode')}`",
        f"- Latest signal date: `{payload.get('latest_signal_date')}`",
        f"- Signal age days: `{payload.get('signal_age_days')}`",
        f"- Current holdings: `{payload.get('current_holding_count')}`",
        f"- Trade plan rows: `{payload.get('trade_plan_rows')}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = payload.get("blockers") or []
    if blockers:
        lines.extend([f"- {b}" for b in blockers])
    else:
        lines.append("- none")
    lines.extend(["", "## Actions", ""])
    for action, count in (payload.get("action_counts") or {}).items():
        lines.append(f"- {action}: `{count}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    run_date = pd.Timestamp(args.run_date).normalize() if args.run_date else pd.Timestamp.today().normalize()
    evaluation_date = (
        pd.Timestamp(args.evaluation_date).normalize()
        if args.evaluation_date else previous_krx_close_date(run_date)
    )
    cfg = kr1000_leader_alpha_cfg({
        "metric_mode": "broker_ledger_next_close",
        "execution_price": "next_close",
        "execution_timing": "next_trading_day_close",
    })
    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    scored_path = Path(args.scored_panel) if args.scored_panel else _latest_scored_panel_path()
    raw = _read_table(scored_path)
    date_col = "rebalance_date" if "rebalance_date" in raw.columns else "date"
    raw[date_col] = pd.to_datetime(raw[date_col], errors="coerce").dt.normalize()
    raw = raw[raw[date_col].notna()].copy()
    visible_dates = raw.loc[raw[date_col] <= evaluation_date, date_col]
    if visible_dates.empty:
        raise RuntimeError(f"No scored rows visible as of {evaluation_date.date()} in {scored_path}")
    signal_date = pd.Timestamp(visible_dates.max()).normalize()
    signal_age = int((evaluation_date - signal_date).days)

    latest = raw[raw[date_col] == signal_date].copy()
    embedded_profiles = []
    if "score_profile" in latest.columns:
        embedded_profiles = [
            str(x).strip()
            for x in latest["score_profile"].dropna().unique().tolist()
            if str(x).strip()
        ]
    if len(embedded_profiles) == 1:
        cfg["score_profile"] = embedded_profiles[0]
    latest = compute_leader_scores(latest, cfg)
    target = build_target_portfolio(latest, cfg, as_of_date=signal_date)
    holdings_raw = load_current_holdings(args.current_holdings)
    holdings, price_status = mark_holdings_to_previous_close(holdings_raw, evaluation_date, args.refresh_days)
    plan = generate_trade_plan(holdings, target, latest, cfg, as_of_date=evaluation_date)
    plan["metric_mode"] = "broker_ledger_next_close"
    plan["signal_timing"] = "after_close"
    plan["execution_timing"] = "next_trading_day_close"
    plan["evaluation_price_date"] = str(evaluation_date.date())
    plan["source_signal_date"] = str(signal_date.date())

    audit = build_audit(evaluation_date, args.audit_stale_days)
    blockers: list[str] = []
    if signal_age > args.max_signal_age_days:
        blockers.append(f"latest_signal_stale_{signal_age}d_gt_{args.max_signal_age_days}d")
    if int(audit.get("summary", {}).get("critical", 0)) > 0:
        blockers.append("data_integrity_audit_has_critical")
    missing_prices = [x["ticker"] for x in price_status if x.get("status") != "priced"]
    if missing_prices:
        blockers.append(f"missing_holding_prices_{len(missing_prices)}")

    status = "blocked" if blockers else "completed"
    action_counts = plan["action"].value_counts().to_dict() if not plan.empty and "action" in plan.columns else {}
    payload = {
        "status": status,
        "run_date": str(run_date.date()),
        "evaluation_date": str(evaluation_date.date()),
        "latest_signal_date": str(signal_date.date()),
        "signal_age_days": signal_age,
        "scored_panel": str(scored_path),
        "current_holding_count": int(len(holdings)),
        "trade_plan_rows": int(len(plan)),
        "action_counts": {str(k): int(v) for k, v in action_counts.items()},
        "score_profile": str(cfg.get("score_profile", "full")),
        "blockers": blockers,
        "price_status": price_status,
        "data_audit_summary": audit.get("summary", {}),
        "broker_rule": {
            "metric_mode": "broker_ledger_next_close",
            "signal_timing": "after_close",
            "fill_mode": "next_close",
            "execution_timing": "next_trading_day_close",
            "integer_shares": True,
            "no_negative_cash": True,
            "no_leverage": True,
        },
    }
    stamp = evaluation_date.strftime("%Y%m%d")
    json_path = out_dir / f"kr1000_daily_broker_check_{stamp}.json"
    md_path = out_dir / f"kr1000_daily_broker_check_{stamp}.md"
    plan_path = out_dir / f"kr1000_daily_broker_trade_plan_{stamp}.csv"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    md_path.write_text(render_report(payload), encoding="utf-8")
    plan.to_csv(plan_path, index=False, encoding="utf-8-sig")

    print("KR1000 daily broker check")
    print(f"  status:       {status}")
    print(f"  evaluation:   {evaluation_date.date()}")
    print(f"  signal:       {signal_date.date()} ({signal_age}d old)")
    print(f"  trade plan:   {plan_path}")
    print(f"  report:       {md_path}")
    for blocker in blockers:
        print(f"  blocker:      {blocker}")
    return 0 if status == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())
