"""Audit kr_quant_engine data integrity, freshness, and PIT leakage risks.

This tool inspects the actual synchronized data under DATA_ROOT and the local
collection/backtest workflow. It is intentionally evidence-first: it reports
what exists, what date range it covers, and which current-state checks fail.

Run:
    py -3 tools/audit_data_integrity.py
    py -3 tools/audit_data_integrity.py --as-of 2026-06-05
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import (  # noqa: E402
    BROKERAGE_FEE_ONE_WAY,
    DATA_ROOT,
    DEFAULT_ROUND_TRIP_COST,
    DEFAULT_SLIPPAGE_BP,
    PROJECT_ROOT as CONFIG_PROJECT_ROOT,
    TRANSACTION_TAX_SELL,
)
from kr_helpers import log  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit KR quant data integrity")
    p.add_argument("--as-of", default=None,
                   help="Audit date. Default=today from the local clock.")
    p.add_argument("--out-dir", default=None,
                   help="Output directory. Default DATA_ROOT/outputs.")
    p.add_argument("--stale-days", type=int, default=45,
                   help="Warn when core data is older than this many days.")
    return p.parse_args()


def _jsonable(v: Any) -> Any:
    if isinstance(v, (pd.Timestamp,)):
        return str(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, Path):
        return str(v)
    if pd.isna(v) if not isinstance(v, (list, tuple, dict, str, bytes)) else False:
        return None
    return v


def _date_stats(df: pd.DataFrame, candidates: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in candidates:
        if col not in df.columns:
            continue
        s = pd.to_datetime(df[col], errors="coerce")
        if s.notna().any():
            out[col] = {
                "min": str(s.min().date()),
                "max": str(s.max().date()),
                "nunique": int(s.nunique()),
            }
    return out


def _safe_read_parquet(path: Path) -> pd.DataFrame:
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        log(f"[audit] read fail {path}: {exc}", level="WARN")
        return pd.DataFrame()


def _describe_parquet(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
    }
    if not path.exists():
        return info
    info["size_bytes"] = int(path.stat().st_size)
    info["modified_time"] = str(pd.Timestamp.fromtimestamp(path.stat().st_mtime))
    df = _safe_read_parquet(path)
    info["rows"] = int(len(df))
    info["columns"] = int(len(df.columns))
    if "ticker" in df.columns:
        info["tickers"] = int(df["ticker"].astype(str).nunique())
    info["date_stats"] = _date_stats(
        df,
        [
            "date", "rebalance_date", "snapshot_date", "first_seen_date",
            "last_seen_date", "inferred_listing_date", "inferred_delisting_date",
            "period_end", "rcept_dt", "fundamentals_rcept_dt",
            "fundamentals_period_end",
        ],
    )
    return info


def _scan_dated_cache(folder: Path, pattern: str) -> dict[str, Any]:
    dates = []
    regex = re.compile(pattern)
    if folder.exists():
        for path in folder.glob("*.parquet"):
            m = regex.match(path.name)
            if not m:
                continue
            try:
                dates.append(pd.Timestamp(m.group(1)))
            except Exception:
                pass
    dates = sorted(set(dates))
    gaps = [
        {"from": str(a.date()), "to": str(b.date()), "days": int((b - a).days)}
        for a, b in zip(dates, dates[1:])
        if (b - a).days > 45
    ]
    return {
        "count": int(len(dates)),
        "min": str(dates[0].date()) if dates else None,
        "max": str(dates[-1].date()) if dates else None,
        "gaps_gt_45d": gaps,
    }


def _latest_scored_panel(feature_store: Path) -> Path | None:
    files = sorted(feature_store.glob("scored_panel_v0_*.parquet"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _audit_scored_panel(panel_path: Path | None, historical_mcap_path: Path) -> dict[str, Any]:
    if panel_path is None:
        return {"exists": False, "issues": [{"severity": "CRITICAL", "message": "No scored_panel_v0 parquet found"}]}

    df = _safe_read_parquet(panel_path)
    out = _describe_parquet(panel_path)
    out["issues"] = []
    if df.empty:
        out["issues"].append({"severity": "CRITICAL", "message": "scored panel is empty"})
        return out

    date_col = "rebalance_date" if "rebalance_date" in df.columns else "date"
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)

    if "fundamentals_rcept_dt" in df.columns:
        rcept = pd.to_datetime(df["fundamentals_rcept_dt"], errors="coerce")
        fund_leak = df[rcept.notna() & (rcept > df[date_col])]
        out["fundamentals_rcept_after_signal_rows"] = int(len(fund_leak))
        if len(fund_leak):
            out["issues"].append({
                "severity": "CRITICAL",
                "message": f"{len(fund_leak)} scored rows have fundamentals_rcept_dt after rebalance_date",
                "examples": fund_leak[[date_col, "ticker", "fundamentals_rcept_dt"]].head(5).astype(str).to_dict("records"),
            })

    if "fundamentals_period_end" in df.columns:
        period_end = pd.to_datetime(df["fundamentals_period_end"], errors="coerce")
        period_after_signal = df[period_end.notna() & (period_end > df[date_col])]
        out["fundamentals_period_after_signal_rows"] = int(len(period_after_signal))
        if len(period_after_signal):
            out["issues"].append({
                "severity": "HIGH",
                "message": f"{len(period_after_signal)} scored rows have fundamentals_period_end after rebalance_date",
            })

        if "fundamentals_rcept_dt" in df.columns:
            rcept = pd.to_datetime(df["fundamentals_rcept_dt"], errors="coerce")
            period_after_rcept = df[period_end.notna() & rcept.notna() & (period_end > rcept)]
            out["fundamentals_period_after_rcept_rows"] = int(len(period_after_rcept))
            if len(period_after_rcept):
                out["issues"].append({
                    "severity": "HIGH",
                    "message": (
                        f"{len(period_after_rcept)} scored rows have fundamentals_period_end "
                        "after fundamentals_rcept_dt"
                    ),
                    "note": (
                        "This usually indicates non-December fiscal-year metadata was mapped "
                        "as calendar-year metadata; rcept_dt PIT filtering is checked separately."
                    ),
                    "examples": period_after_rcept[
                        [date_col, "ticker", "fundamentals_period_end", "fundamentals_rcept_dt"]
                    ].head(5).astype(str).to_dict("records"),
                })

    if {"avg_trading_value_60d", "days_observed"}.issubset(df.columns):
        avg = pd.to_numeric(df["avg_trading_value_60d"], errors="coerce")
        days = pd.to_numeric(df["days_observed"], errors="coerce")
        out["avg_value_missing_or_zero_pct"] = float((avg.fillna(0) <= 0).mean())
        out["days_observed_lt_40_pct"] = float((days.fillna(0) < 40).mean())

    mcap = _safe_read_parquet(historical_mcap_path)
    if not mcap.empty and "snapshot_date" in mcap.columns:
        mcap = mcap.copy()
        mcap["snapshot_date"] = pd.to_datetime(mcap["snapshot_date"], errors="coerce")
        mcap["ticker"] = mcap["ticker"].astype(str).str.zfill(6)
        leak_rows = []
        for rd, g in df.groupby(date_col):
            eligible_dates = mcap.loc[mcap["snapshot_date"] <= rd, "snapshot_date"]
            if eligible_dates.empty:
                leak_rows.append({"rebalance_date": str(pd.Timestamp(rd).date()), "reason": "no prior mcap snapshot", "rows": int(len(g))})
                continue
            snap = eligible_dates.max()
            active = set(mcap.loc[mcap["snapshot_date"] == snap, "ticker"])
            missing = sorted(set(g["ticker"]) - active)
            if missing:
                leak_rows.append({
                    "rebalance_date": str(pd.Timestamp(rd).date()),
                    "snapshot_date": str(pd.Timestamp(snap).date()),
                    "missing_ticker_count": int(len(missing)),
                    "examples": missing[:10],
                })
        out["membership_leak_checks"] = {
            "dates_checked": int(df[date_col].nunique()),
            "dates_with_missing_membership": int(len(leak_rows)),
            "examples": leak_rows[:10],
        }
        if leak_rows:
            out["issues"].append({
                "severity": "CRITICAL",
                "message": "Scored panel contains tickers absent from the PIT mcap snapshot at-or-before signal date",
                "examples": leak_rows[:3],
            })
    return out


def _audit_source_panels(feature_store: Path) -> dict[str, Any]:
    panels: dict[str, Any] = {}
    for path in sorted(feature_store.glob("*.parquet")):
        if path.name.startswith(("scored_panel_v0",)):
            continue
        panels[path.name] = _describe_parquet(path)
        if path.name.startswith("event_panel_"):
            panels[path.name]["note"] = (
                "Event panels may intentionally include rcept_dt after the filename end date. "
                "They are safe only when downstream joins filter rcept_dt <= signal date."
            )
    return panels


def _workflow_summary() -> dict[str, Any]:
    wf_dir = CONFIG_PROJECT_ROOT / ".github" / "workflows"
    out: dict[str, Any] = {}
    if not wf_dir.exists():
        return {"exists": False}
    for path in sorted(wf_dir.glob("*.yml")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        direct_backtest = "run_kr1000_backtest.py" in text
        validation_gate = "run_kr1000_validation_gate.py" in text
        validation_runs_backtests = validation_gate and (
            "--component-ab" in text
            or "--strategy-ab" in text
            or ("mode.outputs.mode" in text and "full" in text)
        )
        out[path.name] = {
            "mentions_rclone": "rclone" in text,
            "syncs_cache_pykrx": "cache_pykrx" in text and "rclone copy gdrive:kr_quant_engine/cache_pykrx" in text,
            "syncs_data_pit": "data_pit" in text,
            "runs_pit_builder": "build_pit_universe_history.py" in text,
            "runs_kr1000_validation_gate": validation_gate,
            "runs_kr1000_backtest": direct_backtest or validation_runs_backtests,
            "runs_kr1000_backtest_direct": direct_backtest,
            "runs_kr1000_backtest_via_validation_gate": validation_runs_backtests,
            "runs_kr1000_daily_refresh": "refresh_kr1000_daily_data.py" in text,
            "runs_kr1000_daily_broker_check": "run_kr1000_daily_broker_check.py" in text,
            "cron_lines": [line.strip() for line in text.splitlines() if "cron:" in line],
        }
    return out


def _add_issue(issues: list[dict[str, Any]], severity: str, message: str, **extra: Any) -> None:
    item = {"severity": severity, "message": message}
    item.update(extra)
    issues.append(item)


def build_audit(as_of: pd.Timestamp, stale_days: int) -> dict[str, Any]:
    data_root = DATA_ROOT
    feature_store = data_root / "feature_store"
    data_pit = data_root / "data_pit"
    cache_pykrx = data_root / "cache_pykrx"
    cache_misc = data_root / "cache_misc"
    issues: list[dict[str, Any]] = []

    historical_mcap = data_pit / "historical_mcap.parquet"
    listed_history = data_pit / "listed_history.parquet"
    scored_path = _latest_scored_panel(feature_store)

    mcap_cache = _scan_dated_cache(cache_pykrx, r"^mktcap_ALL_(\d{8})\.parquet$")
    avg_cache = _scan_dated_cache(cache_misc, r"^avg_value_\d+d_(\d{8})\.parquet$")

    hist_info = _describe_parquet(historical_mcap)
    listed_info = _describe_parquet(listed_history)
    scored_audit = _audit_scored_panel(scored_path, historical_mcap)
    workflows = _workflow_summary()

    hist_max = hist_info.get("date_stats", {}).get("snapshot_date", {}).get("max")
    if hist_max:
        stale = int((as_of - pd.Timestamp(hist_max)).days)
        hist_info["staleness_days"] = stale
        if stale > stale_days:
            _add_issue(issues, "HIGH", f"historical_mcap latest snapshot is stale by {stale} days", latest_snapshot=hist_max)

    scored_max = scored_audit.get("date_stats", {}).get("rebalance_date", {}).get("max")
    if scored_max:
        stale = int((as_of - pd.Timestamp(scored_max)).days)
        scored_audit["staleness_days"] = stale
        if stale > stale_days:
            _add_issue(issues, "CRITICAL", f"latest scored_panel_v0 signal date is stale by {stale} days", latest_signal=scored_max)

    if mcap_cache["gaps_gt_45d"]:
        _add_issue(issues, "HIGH", "mktcap cache has month-level gaps >45 days", gaps=mcap_cache["gaps_gt_45d"][:10])
    if avg_cache["gaps_gt_45d"]:
        _add_issue(issues, "HIGH", "avg_trading_value cache has gaps >45 days", gaps=avg_cache["gaps_gt_45d"][:10])

    if workflows:
        runs_kr1000 = any(v.get("runs_kr1000_backtest") for v in workflows.values())
        if not runs_kr1000:
            _add_issue(issues, "MEDIUM", "No GitHub workflow runs tools/run_kr1000_backtest.py")
        monthly = workflows.get("monthly_picks.yml", {})
        has_daily_refresh = any(v.get("runs_kr1000_daily_refresh") for v in workflows.values())
        if monthly and not monthly.get("syncs_cache_pykrx") and not has_daily_refresh:
            _add_issue(
                issues,
                "HIGH",
                "monthly_picks workflow skips cache_pykrx; future PIT mcap history cannot advance unless data_pit is refreshed elsewhere",
            )
        has_daily_check = any(v.get("runs_kr1000_daily_broker_check") for v in workflows.values())
        if not has_daily_check:
            _add_issue(issues, "MEDIUM", "No GitHub workflow runs tools/run_kr1000_daily_broker_check.py")

    # Surface scored-panel internal issues at top level too.
    for item in scored_audit.get("issues", []):
        issues.append(item)

    return {
        "audit_as_of": str(as_of.date()),
        "project_root": str(CONFIG_PROJECT_ROOT),
        "data_root": str(data_root),
        "cost_model": {
            "initial_cash_default_krw": 100_000_000,
            "transaction_tax_sell": TRANSACTION_TAX_SELL,
            "brokerage_fee_one_way": BROKERAGE_FEE_ONE_WAY,
            "slippage_bp": DEFAULT_SLIPPAGE_BP,
            "round_trip_cost": DEFAULT_ROUND_TRIP_COST,
        },
        "assets": {
            "historical_mcap": hist_info,
            "listed_history": listed_info,
            "scored_panel_v0_latest": scored_audit,
            "source_panels": _audit_source_panels(feature_store),
        },
        "cache_coverage": {
            "mktcap_ALL": mcap_cache,
            "avg_value": avg_cache,
        },
        "workflows": workflows,
        "issues": issues,
        "summary": {
            "critical": sum(1 for x in issues if x["severity"] == "CRITICAL"),
            "high": sum(1 for x in issues if x["severity"] == "HIGH"),
            "medium": sum(1 for x in issues if x["severity"] == "MEDIUM"),
            "low": sum(1 for x in issues if x["severity"] == "LOW"),
        },
    }


def write_markdown(audit: dict[str, Any], path: Path) -> None:
    lines = [
        "# KR Quant Data Integrity Audit",
        "",
        f"- Audit as of: {audit['audit_as_of']}",
        f"- Project root: `{audit['project_root']}`",
        f"- Data root: `{audit['data_root']}`",
        "",
        "## Summary",
        "",
    ]
    summary = audit["summary"]
    lines.append(f"- Critical: {summary['critical']}")
    lines.append(f"- High: {summary['high']}")
    lines.append(f"- Medium: {summary['medium']}")
    lines.append(f"- Low: {summary['low']}")
    lines.extend(["", "## Issues", ""])
    if audit["issues"]:
        for issue in audit["issues"]:
            lines.append(f"- **{issue['severity']}**: {issue['message']}")
            for k, v in issue.items():
                if k in {"severity", "message"}:
                    continue
                lines.append(f"  - {k}: `{v}`")
    else:
        lines.append("- No issues detected by this audit.")

    lines.extend(["", "## Core Coverage", ""])
    hist = audit["assets"]["historical_mcap"]
    scored = audit["assets"]["scored_panel_v0_latest"]
    lines.append(f"- historical_mcap rows: `{hist.get('rows')}`, dates: `{hist.get('date_stats', {}).get('snapshot_date')}`")
    lines.append(f"- latest scored_panel rows: `{scored.get('rows')}`, dates: `{scored.get('date_stats', {}).get('rebalance_date')}`")
    lines.append(f"- mktcap cache: `{audit['cache_coverage']['mktcap_ALL']}`")
    lines.append(f"- avg value cache: `{audit['cache_coverage']['avg_value']}`")

    lines.extend(["", "## Cost Model", ""])
    for k, v in audit["cost_model"].items():
        lines.append(f"- {k}: `{v}`")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    as_of = pd.Timestamp(args.as_of).normalize() if args.as_of else pd.Timestamp.today().normalize()
    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    audit = build_audit(as_of, args.stale_days)
    stamp = as_of.strftime("%Y%m%d")
    json_path = out_dir / f"data_integrity_audit_{stamp}.json"
    md_path = out_dir / f"data_integrity_audit_{stamp}.md"
    json_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False, default=_jsonable), encoding="utf-8")
    write_markdown(audit, md_path)

    print("KR data integrity audit")
    print(f"  as_of:    {audit['audit_as_of']}")
    print(f"  critical: {audit['summary']['critical']}")
    print(f"  high:     {audit['summary']['high']}")
    print(f"  medium:   {audit['summary']['medium']}")
    print(f"  json:     {json_path}")
    print(f"  report:   {md_path}")
    for issue in audit["issues"][:10]:
        print(f"  - {issue['severity']}: {issue['message']}")
    return 1 if audit["summary"]["critical"] else 0


if __name__ == "__main__":
    sys.exit(main())
