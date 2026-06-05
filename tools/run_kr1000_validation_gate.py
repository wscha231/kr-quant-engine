"""Run the official KR1000 data-to-backtest validation gate.

This is the orchestration layer for the KR1000 Leader Alpha acceptance plan:
data freshness/PIT audit -> daily broker readiness -> broker-ledger backtests
-> CAGR/MDD/KOSPI200 excess gates.

Examples:
    py -3 tools/run_kr1000_validation_gate.py --as-of 2026-06-04 --dry-run
    py -3 tools/run_kr1000_validation_gate.py --as-of 2026-06-04 --component-ab
    py -3 tools/run_kr1000_validation_gate.py --as-of 2026-06-04 --refresh-data --rebuild-scored-panel
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, kr1000_leader_alpha_cfg  # noqa: E402
from kr1000_leader import KR1000_SCORE_PROFILES  # noqa: E402
from tools.audit_data_integrity import build_audit, write_markdown  # noqa: E402


OFFICIAL_PERIODS = {
    "official_8y": ("2018-01-01", None),
    "full_2016": ("2016-01-01", None),
    "stress_2020_2022": ("2020-01-01", "2022-12-31"),
    "stress_2023_current": ("2023-01-01", None),
    "recent_1y": ("recent_1y", None),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KR1000 official validation gate")
    p.add_argument("--as-of", default=None,
                   help="Latest fully observable trading date. Default=previous KRX close.")
    p.add_argument("--run-date", default=None,
                   help="Calendar run date used to infer --as-of when omitted.")
    p.add_argument("--out-dir", default=None,
                   help="Default DATA_ROOT/outputs/kr1000_validation_gate_<YYYYMMDD>.")
    p.add_argument("--scored-panel", default=None,
                   help="Optional scored_panel parquet/csv passed to backtest and daily check.")
    p.add_argument("--price-panel", default=None,
                   help="Optional price panel parquet/csv passed to backtests.")
    p.add_argument("--initial-cash", type=float, default=100_000_000.0)
    p.add_argument("--top-holdings", type=int, default=20)
    p.add_argument("--max-rank-for-prices", type=int, default=20)
    p.add_argument("--max-signal-age-days", type=int, default=7)
    p.add_argument("--audit-stale-days", type=int, default=45)
    p.add_argument("--periods", default=",".join(OFFICIAL_PERIODS),
                   help="Comma-separated period keys: official_8y,full_2016,stress_2020_2022,stress_2023_current,recent_1y.")
    p.add_argument("--profiles", default="full",
                   help="Comma-separated score profiles. Use --component-ab for the standard A/B set.")
    p.add_argument("--component-ab", action="store_true",
                   help="Run official_8y with full, rs_only, rs_flow, rs_flow_technical.")
    p.add_argument("--refresh-data", action="store_true",
                   help="Run tools/refresh_kr1000_daily_data.py before auditing.")
    p.add_argument("--skip-avg-value-refresh", action="store_true",
                   help="Pass --skip-avg-value to data refresh for a lighter PIT mcap update.")
    p.add_argument("--rebuild-scored-panel", action="store_true",
                   help="Run run_local.py to rebuild scored_panel_v0 through --as-of before auditing.")
    p.add_argument("--full-rebuild", action="store_true",
                   help="Use run_local.py --full instead of --quick when --rebuild-scored-panel is set.")
    p.add_argument("--skip-collector", action="store_true",
                   help="Pass --no-collector to run_local.py rebuild.")
    p.add_argument("--skip-daily-check", action="store_true")
    p.add_argument("--skip-backtests", action="store_true")
    p.add_argument("--allow-blocked-backtest", action="store_true",
                   help="Run diagnostic backtests even when data/daily gates are blocked.")
    p.add_argument("--dry-run", action="store_true",
                   help="Write the planned command manifest without running mutating/long commands.")
    return p.parse_args()


def _run_date(args: argparse.Namespace) -> pd.Timestamp:
    return pd.Timestamp(args.run_date).normalize() if args.run_date else pd.Timestamp.today().normalize()


def _as_of(args: argparse.Namespace) -> pd.Timestamp:
    if args.as_of:
        return pd.Timestamp(args.as_of).normalize()
    return _previous_krx_close_date(_run_date(args))


def _yyyymmdd(day: pd.Timestamp) -> str:
    return pd.Timestamp(day).strftime("%Y%m%d")


def _previous_krx_close_date(run_date: pd.Timestamp) -> pd.Timestamp:
    rd = pd.Timestamp(run_date).normalize()
    try:
        from kr_pykrx_client import fetch_business_days

        start = rd - pd.Timedelta(days=14)
        days = fetch_business_days(_yyyymmdd(start), _yyyymmdd(rd))
        days = [pd.Timestamp(d).normalize() for d in days if pd.Timestamp(d).normalize() < rd]
        if days:
            return max(days)
    except Exception:
        pass
    return (rd - pd.offsets.BDay(1)).normalize()


def _script(name: str) -> str:
    return str(PROJECT_ROOT / name)


def _run_command(cmd: list[str], cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)
    return {
        "cmd": cmd,
        "returncode": int(proc.returncode),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def _period_window(key: str, as_of: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    if key not in OFFICIAL_PERIODS:
        valid = ", ".join(OFFICIAL_PERIODS)
        raise ValueError(f"unknown period {key!r}; valid: {valid}")
    raw_start, raw_end = OFFICIAL_PERIODS[key]
    if raw_start == "recent_1y":
        start = as_of - pd.DateOffset(years=1)
    else:
        start = pd.Timestamp(raw_start)
    end = pd.Timestamp(raw_end) if raw_end else as_of
    end = min(end.normalize(), as_of)
    return start.normalize(), end.normalize()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def metric_value(metrics: dict[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        if name in metrics and metrics[name] is not None:
            try:
                return float(metrics[name])
            except (TypeError, ValueError):
                return default
    return default


def evaluate_backtest_metrics(metrics: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "min_years": {
            "value": metric_value(metrics, "years"),
            "threshold": float(cfg.get("target_min_backtest_years", 8.0)),
            "pass": metric_value(metrics, "years") >= float(cfg.get("target_min_backtest_years", 8.0)),
        },
        "cagr": {
            "value": metric_value(metrics, "cagr", "strategy_cagr"),
            "threshold": float(cfg.get("target_cagr_gate", 0.35)),
            "pass": metric_value(metrics, "cagr", "strategy_cagr") >= float(cfg.get("target_cagr_gate", 0.35)),
        },
        "mdd": {
            "value": metric_value(metrics, "mdd", "max_dd"),
            "threshold": float(cfg.get("target_mdd_gate", -0.25)),
            "pass": metric_value(metrics, "mdd", "max_dd") >= float(cfg.get("target_mdd_gate", -0.25)),
        },
        "excess_cagr": {
            "value": metric_value(metrics, "excess_cagr"),
            "threshold": float(cfg.get("target_excess_cagr_gate", 0.0)),
            "pass": metric_value(metrics, "excess_cagr") > float(cfg.get("target_excess_cagr_gate", 0.0)),
        },
        "sharpe": {
            "value": metric_value(metrics, "sharpe"),
            "threshold": float(cfg.get("target_sharpe_gate", 1.0)),
            "pass": metric_value(metrics, "sharpe") > float(cfg.get("target_sharpe_gate", 1.0)),
        },
        "information_ratio": {
            "value": metric_value(metrics, "information_ratio"),
            "threshold": float(cfg.get("target_information_ratio_gate", 0.5)),
            "pass": metric_value(metrics, "information_ratio") > float(cfg.get("target_information_ratio_gate", 0.5)),
        },
        "broker_rule": {
            "value": metrics.get("metric_mode"),
            "threshold": "broker_ledger_next_close",
            "pass": metrics.get("metric_mode") == "broker_ledger_next_close"
            and metrics.get("fill_mode") == "next_close"
            and bool(metrics.get("valid_for_production_metric")),
        },
    }
    return {
        "checks": checks,
        "all_pass": all(bool(v["pass"]) for v in checks.values()),
    }


def _planned_backtests(args: argparse.Namespace, as_of: pd.Timestamp, out_dir: Path) -> list[dict[str, Any]]:
    periods = _split_csv(args.periods)
    profiles = _split_csv(args.profiles)
    if args.component_ab:
        profiles = ["full", "rs_only", "rs_flow", "rs_flow_technical"]
    for profile in profiles:
        if profile not in KR1000_SCORE_PROFILES:
            valid = ", ".join(sorted(KR1000_SCORE_PROFILES))
            raise ValueError(f"unknown score profile {profile!r}; valid: {valid}")

    jobs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for period in periods:
        start, end = _period_window(period, as_of)
        period_profiles = profiles if (period == "official_8y" or not args.component_ab) else ["full"]
        for profile in period_profiles:
            key = (period, profile)
            if key in seen:
                continue
            seen.add(key)
            job_out = out_dir / f"{period}_{profile}"
            cmd = [
                sys.executable,
                _script("tools/run_kr1000_backtest.py"),
                "--start", str(start.date()),
                "--end", str(end.date()),
                "--initial-cash", str(float(args.initial_cash)),
                "--top-holdings", str(int(args.top_holdings)),
                "--max-rank-for-prices", str(int(args.max_rank_for_prices)),
                "--out-dir", str(job_out),
                "--score-profile", profile,
                "--save-scored-panel",
            ]
            if args.scored_panel:
                cmd.extend(["--scored-panel", args.scored_panel])
            if args.price_panel:
                cmd.extend(["--price-panel", args.price_panel])
            jobs.append({
                "period": period,
                "profile": profile,
                "start": str(start.date()),
                "end": str(end.date()),
                "out_dir": str(job_out),
                "cmd": cmd,
            })
    return jobs


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# KR1000 Validation Gate",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- As of: `{payload.get('as_of')}`",
        f"- Official target: `CAGR >= 35%, MDD >= -25%, excess CAGR > 0`",
        f"- Data gate: `{payload.get('data_gate', {}).get('status')}`",
        f"- Daily broker check: `{payload.get('daily_broker_check', {}).get('status')}`",
        "",
        "## Official Gate",
        "",
    ]
    official = payload.get("official_gate") or {}
    if official:
        lines.append(f"- Period/profile: `{official.get('period')}` / `{official.get('profile')}`")
        lines.append(f"- Pass: `{official.get('all_pass')}`")
        for name, check in (official.get("checks") or {}).items():
            lines.append(
                f"- {name}: value=`{check.get('value')}`, threshold=`{check.get('threshold')}`, pass=`{check.get('pass')}`"
            )
    else:
        lines.append("- Not run.")
    lines.extend(["", "## Backtests", ""])
    for item in payload.get("backtests", []):
        metrics = item.get("metrics") or {}
        lines.append(
            f"- `{item.get('period')}` / `{item.get('profile')}`: rc=`{item.get('returncode')}`, "
            f"CAGR=`{metric_value(metrics, 'cagr', 'strategy_cagr'):.2%}`, "
            f"MDD=`{metric_value(metrics, 'mdd', 'max_dd'):.2%}`, "
            f"Excess=`{metric_value(metrics, 'excess_cagr'):.2%}`"
        )
    blockers = payload.get("blockers") or []
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend([f"- {b}" for b in blockers])
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    as_of = _as_of(args)
    stamp = as_of.strftime("%Y%m%d")
    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs" / f"kr1000_validation_gate_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = kr1000_leader_alpha_cfg()
    commands: list[dict[str, Any]] = []
    blockers: list[str] = []

    if args.refresh_data:
        cmd = [
            sys.executable,
            _script("tools/refresh_kr1000_daily_data.py"),
            "--as-of", str(as_of.date()),
        ]
        if args.skip_avg_value_refresh:
            cmd.append("--skip-avg-value")
        commands.append({"step": "refresh_data", "cmd": cmd})

    if args.rebuild_scored_panel:
        cmd = [
            sys.executable,
            _script("run_local.py"),
            "--full" if args.full_rebuild else "--quick",
            "--start-date", "2016-01-01",
            "--end-date", str(as_of.date()),
            "--portfolio-size", str(int(args.top_holdings)),
        ]
        if args.skip_collector:
            cmd.append("--no-collector")
        commands.append({"step": "rebuild_scored_panel", "cmd": cmd})

    if args.dry_run:
        backtest_jobs = _planned_backtests(args, as_of, out_dir)
        payload = {
            "status": "dry_run",
            "as_of": str(as_of.date()),
            "out_dir": str(out_dir),
            "planned_commands": commands,
            "planned_backtests": backtest_jobs,
            "thresholds": {
                "cagr": cfg["target_cagr_gate"],
                "mdd": cfg["target_mdd_gate"],
                "excess_cagr": cfg["target_excess_cagr_gate"],
                "sharpe": cfg["target_sharpe_gate"],
                "information_ratio": cfg["target_information_ratio_gate"],
                "min_years": cfg["target_min_backtest_years"],
            },
        }
        json_path = out_dir / "kr1000_validation_gate.json"
        md_path = out_dir / "kr1000_validation_gate.md"
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        md_path.write_text(_render_report(payload), encoding="utf-8")
        print("KR1000 validation gate dry-run")
        print(f"  as_of: {as_of.date()}")
        print(f"  planned commands: {len(commands)}")
        print(f"  planned backtests: {len(backtest_jobs)}")
        print(f"  report: {md_path}")
        return 0

    command_results: list[dict[str, Any]] = []
    for item in commands:
        result = _run_command(item["cmd"], PROJECT_ROOT)
        result["step"] = item["step"]
        command_results.append(result)
        if result["returncode"] != 0:
            blockers.append(f"{item['step']}_failed_rc_{result['returncode']}")

    audit = build_audit(as_of, args.audit_stale_days)
    audit_json = out_dir / f"data_integrity_audit_{stamp}.json"
    audit_md = out_dir / f"data_integrity_audit_{stamp}.md"
    audit_json.write_text(json.dumps(audit, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_markdown(audit, audit_md)
    data_gate_status = "passed" if int(audit.get("summary", {}).get("critical", 0)) == 0 else "blocked"
    if data_gate_status != "passed":
        blockers.append("data_integrity_audit_has_critical")

    daily_payload: dict[str, Any] = {"status": "skipped"}
    if not args.skip_daily_check:
        daily_cmd = [
            sys.executable,
            _script("tools/run_kr1000_daily_broker_check.py"),
            "--evaluation-date", str(as_of.date()),
            "--out-dir", str(out_dir),
            "--max-signal-age-days", str(int(args.max_signal_age_days)),
            "--audit-stale-days", str(int(args.audit_stale_days)),
        ]
        if args.scored_panel:
            daily_cmd.extend(["--scored-panel", args.scored_panel])
        daily_result = _run_command(daily_cmd, PROJECT_ROOT)
        daily_payload = {
            "status": "completed" if daily_result["returncode"] == 0 else "blocked",
            "returncode": daily_result["returncode"],
            "cmd": daily_cmd,
            "stdout_tail": daily_result["stdout_tail"],
            "stderr_tail": daily_result["stderr_tail"],
        }
        if daily_result["returncode"] != 0:
            blockers.append(f"daily_broker_check_failed_rc_{daily_result['returncode']}")

    run_backtests = not args.skip_backtests and (not blockers or args.allow_blocked_backtest)
    backtests: list[dict[str, Any]] = []
    official_gate: dict[str, Any] | None = None
    if run_backtests:
        for job in _planned_backtests(args, as_of, out_dir):
            result = _run_command(job["cmd"], PROJECT_ROOT)
            metrics_path = Path(job["out_dir"]) / "leader_backtest_metrics.json"
            metrics = _read_json(metrics_path)
            gate = evaluate_backtest_metrics(metrics, cfg) if metrics else {"all_pass": False, "checks": {}}
            item = {
                **{k: v for k, v in job.items() if k != "cmd"},
                "cmd": job["cmd"],
                "returncode": result["returncode"],
                "metrics_path": str(metrics_path),
                "metrics": metrics,
                "gate": gate,
                "stdout_tail": result["stdout_tail"],
                "stderr_tail": result["stderr_tail"],
            }
            backtests.append(item)
            if job["period"] == "official_8y" and job["profile"] == "full":
                official_gate = {
                    "period": job["period"],
                    "profile": job["profile"],
                    "all_pass": bool(gate.get("all_pass")),
                    "checks": gate.get("checks", {}),
                    "metrics_path": str(metrics_path),
                }
    elif not args.skip_backtests:
        blockers.append("backtests_skipped_until_data_and_daily_gates_pass")

    if blockers and not backtests:
        status = "blocked"
    elif official_gate and official_gate.get("all_pass"):
        status = "passed"
    elif official_gate:
        status = "failed_performance"
    else:
        status = "completed_no_official_backtest"

    payload = {
        "status": status,
        "as_of": str(as_of.date()),
        "out_dir": str(out_dir),
        "thresholds": {
            "cagr": cfg["target_cagr_gate"],
            "mdd": cfg["target_mdd_gate"],
            "excess_cagr": cfg["target_excess_cagr_gate"],
            "sharpe": cfg["target_sharpe_gate"],
            "information_ratio": cfg["target_information_ratio_gate"],
            "min_years": cfg["target_min_backtest_years"],
        },
        "data_gate": {
            "status": data_gate_status,
            "audit_json": str(audit_json),
            "audit_md": str(audit_md),
            "summary": audit.get("summary", {}),
        },
        "daily_broker_check": daily_payload,
        "command_results": command_results,
        "official_gate": official_gate,
        "backtests": backtests,
        "blockers": blockers,
    }
    json_path = out_dir / "kr1000_validation_gate.json"
    md_path = out_dir / "kr1000_validation_gate.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    md_path.write_text(_render_report(payload), encoding="utf-8")

    print("KR1000 validation gate")
    print(f"  status: {status}")
    print(f"  as_of:  {as_of.date()}")
    print(f"  report: {md_path}")
    for blocker in blockers[:8]:
        print(f"  blocker: {blocker}")
    return 0 if status == "passed" else 2 if status == "blocked" else 1


if __name__ == "__main__":
    sys.exit(main())
