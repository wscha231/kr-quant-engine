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
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, kr1000_leader_alpha_cfg  # noqa: E402
from kr1000_leader import KR1000_AB_SCORE_PROFILES, KR1000_SCORE_PROFILES  # noqa: E402
from tools.audit_data_integrity import build_audit, write_markdown  # noqa: E402


OFFICIAL_PERIODS = {
    "official_8y": ("2018-01-01", None),
    "full_2016": ("2016-01-01", None),
    "stress_2020_2022": ("2020-01-01", "2022-12-31"),
    "stress_2023_current": ("2023-01-01", None),
    "recent_1y": ("recent_1y", None),
}

KR1000_STRATEGY_AB_PRESETS = {
    "pmb_defensive_mdd_gate": {
        "score_profile": "pmb_pre_surge",
        "top_holdings": 20,
        "buy_rank_threshold": 20,
        "hold_rank_threshold": 40,
        "gross_exposure": 0.70,
        "hard_stop_loss_pct": 0.10,
        "portfolio_dd_ladder": True,
        "portfolio_dd_thresholds": [-0.10, -0.18, -0.24],
        "portfolio_dd_scales": [0.80, 0.60, 0.35],
    },
    "pmb_mid_rank_no_leverage_mdd_gate": {
        "score_profile": "pmb_mid_rank_7_23",
        "top_holdings": 20,
        "buy_rank_threshold": 20,
        "hold_rank_threshold": 40,
        "gross_exposure": 1.00,
        "hard_stop_loss_pct": 0.10,
        "portfolio_dd_ladder": True,
        "portfolio_dd_thresholds": [-0.10, -0.18, -0.24],
        "portfolio_dd_scales": [0.80, 0.60, 0.35],
    },
    "pmb_pre_entry_defensive_mdd_gate": {
        "score_profile": "pmb_pre_entry_defensive",
        "top_holdings": 15,
        "buy_rank_threshold": 15,
        "hold_rank_threshold": 30,
        "gross_exposure": 1.00,
        "hard_stop_loss_pct": 0.10,
        "portfolio_dd_ladder": True,
        "portfolio_dd_thresholds": [-0.10, -0.18, -0.24],
        "portfolio_dd_scales": [0.80, 0.60, 0.35],
    },
}

PRODUCTION_GATE_STRATEGY_PRESET = "pmb_defensive_mdd_gate"
PMB_SCORE_PROFILES = {
    "pmb_pre_surge",
    "pmb_pre_entry",
    "pmb_pre_entry_defensive",
    "pmb_pre_entry_blend",
    "pmb_pre_entry_blend_regime",
    "pmb_pullback_recovery_regime",
    "pmb_mid_rank_7_23",
    "pmb_mid_rank_regime",
    "pmb_mid_tech_regime",
    "hybrid_pmb_rs",
}
DATA_GATE_BLOCKING_HIGH_MESSAGES = {
    "mktcap cache has month-level gaps >45 days": "data_integrity_mktcap_cache_gap",
    "avg_trading_value cache has gaps >45 days": "data_integrity_avg_value_cache_gap",
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
                   help="Run official_8y with the standard KR1000 challenger score profiles.")
    p.add_argument("--strategy-ab", action="store_true",
                   help="Run official_8y with standard broker strategy challenger presets.")
    p.add_argument("--refresh-data", action="store_true",
                   help="Run tools/refresh_kr1000_daily_data.py before auditing.")
    p.add_argument("--skip-avg-value-refresh", action="store_true",
                   help="Pass --skip-avg-value to data refresh for a lighter PIT mcap update.")
    p.add_argument("--rebuild-scored-panel", action="store_true",
                   help="Run run_local.py to rebuild scored_panel_v0 through --as-of before auditing.")
    p.add_argument("--build-latest-snapshot", action="store_true",
                   help="Append a fast latest scored snapshot after data refresh and before daily broker readiness.")
    p.add_argument("--build-pmb-oos-picks", action="store_true",
                   help="Build purged 3-sleeve P_MB OOS picks before broker backtests.")
    p.add_argument("--pmb-oos-score-mode", default="balanced",
                   choices=[
                       "balanced",
                       "pre_entry_focus",
                       "strict_pre_entry",
                       "pre_entry_risk_only",
                       "continuation_focus",
                       "no_risk_balanced",
                   ],
                   help="Score mode passed to tools/build_pmb_oos_picks.py.")
    p.add_argument("--pmb-pre-buffer-months", type=int, default=1,
                   help="Months immediately before surge_start excluded from P_MB pre-entry labels.")
    p.add_argument("--pmb-iterations", type=int, default=400,
                   help="CatBoost iterations passed to P_MB OOS builder.")
    p.add_argument("--enrich-forward-labels", action="store_true",
                   help=(
                       "Add forward target labels to the selected scored panel before "
                       "building P_MB OOS picks/backtests. Useful when reusing an older "
                       "full-feature panel without rebuilding all features."
                   ))
    p.add_argument("--pmb-oos-picks", default=None,
                   help="PIT-safe P_MB OOS picks CSV passed to broker backtests.")
    p.add_argument("--require-pmb-oos-coverage", action="store_true",
                   help="Block official P_MB diagnostics unless OOS picks cover the 8y gate.")
    p.add_argument("--rebuild-start-date", default=None,
                   help=(
                       "Start date for scored-panel rebuild. Default: in quick mode, "
                       "reuse the latest scored_panel_v0 start date when present; "
                       "in --full-rebuild mode, use 2016-01-01."
                   ))
    p.add_argument("--full-rebuild", action="store_true",
                   help="Use run_local.py --full instead of --quick when --rebuild-scored-panel is set.")
    p.add_argument("--rebuild-max-new-months", type=int, default=0,
                   help=(
                       "For cache-preserving quick rebuilds, compute only the latest N missing "
                       "rebalance dates. Use 0 to fill all missing months."
                   ))
    p.add_argument("--rebuild-forward-labels", action="store_true",
                   help=(
                       "Generate forward target labels during scored-panel rebuild. "
                       "Default is off; use --enrich-forward-labels when P_MB risk labels are needed."
                   ))
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


def _infer_scored_panel_start_date(
    feature_store: Path | None = None,
    default_start: str = "2016-01-01",
) -> str:
    """Infer rebuild start from the latest scored_panel_v0 cache.

    Cache-preserving GitHub runs should extend the current canonical panel
    instead of silently falling back to a from-scratch 2016 rebuild.
    """
    root = feature_store or DATA_ROOT / "feature_store"
    if not root.exists():
        return default_start
    pattern = re.compile(r"^scored_panel_v0_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})_.+\.parquet$")
    candidates: list[tuple[float, str, str]] = []
    for path in root.glob("scored_panel_v0_*.parquet"):
        match = pattern.match(path.name)
        if not match:
            continue
        candidates.append((path.stat().st_mtime, match.group(2), match.group(1)))
    if not candidates:
        return default_start
    return max(candidates, key=lambda x: (x[0], x[1], x[2]))[2]


def _extend_cmd_with_strategy_preset(cmd: list[str], preset: dict[str, Any]) -> None:
    if preset.get("gross_exposure") is not None:
        cmd.extend(["--gross-exposure", str(float(preset["gross_exposure"]))])
    if preset.get("hard_stop_loss_pct") is not None:
        cmd.extend(["--hard-stop-loss-pct", str(float(preset["hard_stop_loss_pct"]))])
    if preset.get("buy_rank_threshold") is not None:
        cmd.extend(["--buy-rank-threshold", str(int(preset["buy_rank_threshold"]))])
    if preset.get("hold_rank_threshold") is not None:
        cmd.extend(["--hold-rank-threshold", str(int(preset["hold_rank_threshold"]))])
    if preset.get("portfolio_dd_ladder"):
        cmd.append("--portfolio-dd-ladder")
    if preset.get("portfolio_dd_thresholds") is not None:
        cmd.extend([
            "--portfolio-dd-thresholds",
            ",".join(str(float(x)) for x in preset["portfolio_dd_thresholds"]),
        ])
    if preset.get("portfolio_dd_scales") is not None:
        cmd.extend([
            "--portfolio-dd-scales",
            ",".join(str(float(x)) for x in preset["portfolio_dd_scales"]),
        ])


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
            "threshold": float(cfg.get("target_cagr_gate", 0.30)),
            "pass": metric_value(metrics, "cagr", "strategy_cagr") >= float(cfg.get("target_cagr_gate", 0.30)),
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


def _job_requires_pmb_oos(job: dict[str, Any]) -> bool:
    profile = str(job.get("profile", "")).strip().lower()
    strategy = str(job.get("strategy_preset", "")).strip().lower()
    return profile in PMB_SCORE_PROFILES or strategy == PRODUCTION_GATE_STRATEGY_PRESET


def _data_gate_blockers(audit: dict[str, Any]) -> list[str]:
    """Return data audit issues that invalidate official broker metrics."""
    blockers: list[str] = []
    if int((audit.get("summary") or {}).get("critical", 0)) > 0:
        blockers.append("data_integrity_audit_has_critical")
    for issue in audit.get("issues", []) or []:
        if str(issue.get("severity", "")).upper() != "HIGH":
            continue
        key = DATA_GATE_BLOCKING_HIGH_MESSAGES.get(str(issue.get("message", "")))
        if key and key not in blockers:
            blockers.append(key)
    return blockers


def evaluate_job_metrics(
    metrics: dict[str, Any],
    cfg: dict[str, Any],
    job: dict[str, Any],
    pmb_oos_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate = evaluate_backtest_metrics(metrics, cfg)
    if _job_requires_pmb_oos(job):
        passed = bool((pmb_oos_gate or {}).get("pass"))
        gate["checks"]["pmb_oos_coverage"] = {
            "value": (pmb_oos_gate or {}).get("status", "missing"),
            "threshold": "8y_pit_safe_oos_coverage",
            "pass": passed,
        }
        gate["all_pass"] = bool(gate["all_pass"] and passed)
    return gate


def _planned_backtests(args: argparse.Namespace, as_of: pd.Timestamp, out_dir: Path) -> list[dict[str, Any]]:
    periods = _split_csv(args.periods)
    profiles = _split_csv(args.profiles)
    if args.component_ab:
        profiles = list(KR1000_AB_SCORE_PROFILES)
    for profile in profiles:
        if profile not in KR1000_SCORE_PROFILES:
            valid = ", ".join(sorted(KR1000_SCORE_PROFILES))
            raise ValueError(f"unknown score profile {profile!r}; valid: {valid}")

    jobs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    pmb_oos_picks = getattr(args, "pmb_oos_picks", None)
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
            if pmb_oos_picks:
                cmd.extend(["--pmb-oos-picks", str(pmb_oos_picks)])
            jobs.append({
                "period": period,
                "profile": profile,
                "strategy_preset": "default",
                "start": str(start.date()),
                "end": str(end.date()),
                "out_dir": str(job_out),
                "cmd": cmd,
            })
    if args.strategy_ab:
        start, end = _period_window("official_8y", as_of)
        for name, preset in KR1000_STRATEGY_AB_PRESETS.items():
            profile = str(preset["score_profile"])
            job_out = out_dir / f"official_8y_{name}"
            cmd = [
                sys.executable,
                _script("tools/run_kr1000_backtest.py"),
                "--start", str(start.date()),
                "--end", str(end.date()),
                "--initial-cash", str(float(args.initial_cash)),
                "--top-holdings", str(int(preset.get("top_holdings", args.top_holdings))),
                "--max-rank-for-prices", str(int(args.max_rank_for_prices)),
                "--out-dir", str(job_out),
                "--score-profile", profile,
                "--save-scored-panel",
            ]
            _extend_cmd_with_strategy_preset(cmd, preset)
            if args.scored_panel:
                cmd.extend(["--scored-panel", args.scored_panel])
            if args.price_panel:
                cmd.extend(["--price-panel", args.price_panel])
            if pmb_oos_picks:
                cmd.extend(["--pmb-oos-picks", str(pmb_oos_picks)])
            jobs.append({
                "period": "official_8y",
                "profile": profile,
                "strategy_preset": name,
                "start": str(start.date()),
                "end": str(end.date()),
                "out_dir": str(job_out),
                "cmd": cmd,
            })
    return jobs


def _default_pmb_oos_picks_path() -> Path:
    return PROJECT_ROOT / "research" / "06_walkforward_baselines" / "p_mb_v1_oos_picks.csv"


def _latest_scored_panel_path() -> Path | None:
    root = DATA_ROOT / "feature_store"
    if not root.exists():
        return None
    files = sorted(
        root.glob("scored_panel_v0_*.parquet"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def _read_rebalance_dates(path: Path) -> pd.Series:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, usecols=lambda c: c in {"rebalance_date", "date"})
    else:
        try:
            df = pd.read_parquet(path, columns=["rebalance_date"])
        except Exception:
            df = pd.read_parquet(path)
            keep = [c for c in ("rebalance_date", "date") if c in df.columns]
            df = df[keep]
    date_col = "rebalance_date" if "rebalance_date" in df.columns else "date"
    if date_col not in df.columns:
        return pd.Series(dtype="datetime64[ns]")
    return pd.to_datetime(df[date_col], errors="coerce").dropna().dt.normalize()


def _audit_scored_panel_window(path: Path | None, target_start: pd.Timestamp, as_of: pd.Timestamp) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": str(path) if path else None,
        "target_start": str(target_start.date()),
        "target_end": str(as_of.date()),
    }
    if path is None or not path.exists():
        payload.update({
            "status": "missing",
            "pass": False,
            "reason": "scored_panel_not_found",
        })
        return payload
    try:
        dates = _read_rebalance_dates(path)
    except Exception as exc:
        payload.update({
            "status": "error",
            "pass": False,
            "reason": f"{type(exc).__name__}: {exc}",
        })
        return payload
    if dates.empty:
        payload.update({
            "status": "missing",
            "pass": False,
            "reason": "no_valid_rebalance_dates",
        })
        return payload
    first = pd.Timestamp(dates.min()).normalize()
    last = pd.Timestamp(dates.max()).normalize()
    observed_months = set(dates.dt.to_period("M").unique())
    expected_months = list(pd.period_range(target_start.to_period("M"), as_of.to_period("M"), freq="M"))
    missing_months = [str(m) for m in expected_months if m not in observed_months]
    first_month = first.to_period("M")
    target_start_month = target_start.to_period("M")
    ok = first_month <= target_start_month and last >= as_of and not missing_months
    payload.update({
        "status": "passed" if ok else "failed",
        "pass": bool(ok),
        "first_signal_date": str(first.date()),
        "last_signal_date": str(last.date()),
        "months": int(dates.dt.to_period("M").nunique()),
        "expected_months": int(len(expected_months)),
        "missing_month_count": int(len(missing_months)),
        "missing_months": missing_months[:24],
    })
    if first_month > target_start_month:
        payload["reason"] = "scored_panel_starts_after_official_start"
    elif last < as_of:
        payload["reason"] = "scored_panel_ends_before_as_of"
    elif missing_months:
        payload["reason"] = "scored_panel_missing_months"
    return payload


def _audit_pmb_oos_picks(path: Path, as_of: pd.Timestamp, cfg: dict[str, Any]) -> dict[str, Any]:
    from tools.build_pmb_oos_picks import audit_pmb_oos_coverage

    payload: dict[str, Any] = {
        "path": str(path),
        "target_start": "2018-01-01",
        "target_end": str(as_of.date()),
    }
    if not path.exists():
        payload.update({
            "status": "missing",
            "pass": False,
            "reason": "pmb_oos_picks_not_found",
        })
        return payload
    try:
        picks = pd.read_csv(path, dtype={"ticker": str})
        audit = audit_pmb_oos_coverage(
            picks,
            target_start="2018-01-01",
            target_end=as_of,
            min_covered_years=float(cfg.get("target_min_backtest_years", 8.0)),
            min_coverage_ratio=0.95,
            min_picks_per_month=min(20, int(cfg.get("top_holdings", 20))),
        )
        audit["path"] = str(path)
        return audit
    except Exception as exc:
        payload.update({
            "status": "error",
            "pass": False,
            "reason": f"{type(exc).__name__}: {exc}",
        })
        return payload


def _render_report(payload: dict[str, Any]) -> str:
    thresholds = payload.get("thresholds") or {}
    cagr_target = float(thresholds.get("cagr", 0.30))
    lines = [
        "# KR1000 Validation Gate",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- As of: `{payload.get('as_of')}`",
        f"- Official target: `CAGR >= {cagr_target:.0%}, MDD >= -25%, excess CAGR > 0`",
        f"- Data gate: `{payload.get('data_gate', {}).get('status')}`",
        f"- Daily broker check: `{payload.get('daily_broker_check', {}).get('status')}`",
        "",
        "## Official Gate",
        "",
    ]
    official = payload.get("official_gate") or {}
    if official:
        lines.append(
            f"- Period/profile/preset: `{official.get('period')}` / "
            f"`{official.get('profile')}` / `{official.get('strategy_preset')}`"
        )
        lines.append(f"- Pass: `{official.get('all_pass')}`")
        for name, check in (official.get("checks") or {}).items():
            lines.append(
                f"- {name}: value=`{check.get('value')}`, threshold=`{check.get('threshold')}`, pass=`{check.get('pass')}`"
            )
    else:
        lines.append("- Not run.")
    baseline = payload.get("baseline_gate") or {}
    if baseline:
        lines.extend(["", "## Full Baseline Gate", ""])
        lines.append(
            f"- Period/profile/preset: `{baseline.get('period')}` / "
            f"`{baseline.get('profile')}` / `{baseline.get('strategy_preset')}`"
        )
        lines.append(f"- Pass: `{baseline.get('all_pass')}`")
        lines.append(f"- Metrics: `{baseline.get('metrics_path')}`")
    scored_gate = payload.get("scored_panel_window_gate") or {}
    if scored_gate:
        lines.extend(["", "## Scored Panel Window", ""])
        lines.append(f"- Path: `{scored_gate.get('path')}`")
        lines.append(f"- Status: `{scored_gate.get('status')}`")
        lines.append(f"- Pass: `{scored_gate.get('pass')}`")
        if scored_gate.get("first_signal_date"):
            lines.append(
                f"- Window: `{scored_gate.get('first_signal_date')}` .. `{scored_gate.get('last_signal_date')}`"
            )
        if scored_gate.get("reason"):
            lines.append(f"- Reason: `{scored_gate.get('reason')}`")
    pmb_gate = payload.get("pmb_oos_gate") or {}
    if pmb_gate:
        lines.extend(["", "## P_MB OOS Coverage", ""])
        lines.append(f"- Path: `{pmb_gate.get('path')}`")
        lines.append(f"- Status: `{pmb_gate.get('status')}`")
        lines.append(f"- Pass: `{pmb_gate.get('pass')}`")
        settings = payload.get("pmb_oos_build_settings") or {}
        if settings:
            lines.append(
                f"- Build settings: score_mode=`{settings.get('score_mode')}`, "
                f"pre_buffer_months=`{settings.get('pre_buffer_months')}`, "
                f"iterations=`{settings.get('iterations')}`"
            )
        if pmb_gate.get("expected_months") is not None:
            lines.append(
                f"- Covered months: `{pmb_gate.get('covered_months')}` / `{pmb_gate.get('expected_months')}`"
            )
        if pmb_gate.get("covered_years") is not None:
            lines.append(f"- Covered years: `{pmb_gate.get('covered_years')}`")
        if pmb_gate.get("reason"):
            lines.append(f"- Reason: `{pmb_gate.get('reason')}`")
    lines.extend(["", "## Backtests", ""])
    for item in payload.get("backtests", []):
        metrics = item.get("metrics") or {}
        strategy = item.get("strategy_preset", "default")
        lines.append(
            f"- `{item.get('period')}` / `{item.get('profile')}` / `{strategy}`: rc=`{item.get('returncode')}`, "
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
    if args.build_pmb_oos_picks and not args.pmb_oos_picks:
        args.pmb_oos_picks = str(out_dir / "p_mb_oos_picks_purged_3sleeve.csv")

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
        rebuild_start = args.rebuild_start_date
        if not rebuild_start:
            if args.full_rebuild:
                rebuild_start = "2016-01-01"
            else:
                rebuild_start = _infer_scored_panel_start_date()
                if pd.Timestamp(rebuild_start) > pd.Timestamp("2018-01-01"):
                    rebuild_start = "2018-01-01"
        cmd = [
            sys.executable,
            _script("run_local.py"),
            "--full" if args.full_rebuild else "--quick",
            "--start-date", rebuild_start,
            "--end-date", str(as_of.date()),
            "--portfolio-size", str(int(args.top_holdings)),
            "--panel-only",
        ]
        if not args.full_rebuild and int(args.rebuild_max_new_months) > 0:
            cmd.extend(["--incremental-max-new-months", str(int(args.rebuild_max_new_months))])
        if args.skip_collector:
            cmd.append("--no-collector")
        if not args.rebuild_forward_labels:
            cmd.append("--no-forward-labels")
        commands.append({"step": "rebuild_scored_panel", "cmd": cmd})

    if args.build_latest_snapshot:
        commands.append({
            "step": "build_latest_snapshot",
            "cmd": [
                sys.executable,
                _script("tools/build_latest_kr1000_scored_snapshot.py"),
                "--as-of", str(as_of.date()),
                "--no-rs",
            ],
        })

    if args.enrich_forward_labels:
        enriched_panel = out_dir / "scored_panel_v0_forward_labels.parquet"
        cmd = [
            sys.executable,
            _script("tools/enrich_scored_panel_forward_labels.py"),
            "--out", str(enriched_panel),
        ]
        if args.scored_panel:
            cmd.extend(["--panel", str(args.scored_panel)])
        commands.append({"step": "enrich_forward_labels", "cmd": cmd})
        args.scored_panel = str(enriched_panel)

    if args.build_pmb_oos_picks:
        build_cmd = [
            sys.executable,
            _script("tools/build_pmb_oos_picks.py"),
            "--out", str(args.pmb_oos_picks),
            "--coverage-json", str(out_dir / "p_mb_oos_picks_purged_3sleeve.coverage.json"),
            "--target-start", "2018-01-01",
            "--target-end", str(as_of.date()),
            "--score-mode", str(args.pmb_oos_score_mode),
            "--pre-buffer-months", str(int(args.pmb_pre_buffer_months)),
            "--iterations", str(int(args.pmb_iterations)),
        ]
        if args.scored_panel:
            build_cmd.extend(["--panel", str(args.scored_panel)])
        commands.append({
            "step": "build_pmb_oos_picks",
            "cmd": build_cmd,
        })

    if args.dry_run:
        backtest_jobs = _planned_backtests(args, as_of, out_dir)
        payload = {
            "status": "dry_run",
            "as_of": str(as_of.date()),
            "out_dir": str(out_dir),
            "pmb_oos_picks": str(args.pmb_oos_picks or _default_pmb_oos_picks_path()),
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
            "pmb_oos_build_settings": {
                "score_mode": args.pmb_oos_score_mode,
                "pre_buffer_months": int(args.pmb_pre_buffer_months),
                "iterations": int(args.pmb_iterations),
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
    data_blockers = _data_gate_blockers(audit)
    data_gate_status = "passed" if not data_blockers else "blocked"
    blockers.extend([b for b in data_blockers if b not in blockers])

    scored_panel_window_gate = _audit_scored_panel_window(
        Path(args.scored_panel) if args.scored_panel else _latest_scored_panel_path(),
        pd.Timestamp("2018-01-01"),
        as_of,
    )
    if not args.skip_backtests and not scored_panel_window_gate.get("pass"):
        blockers.append("scored_panel_window_gate_failed")

    pmb_oos_gate = _audit_pmb_oos_picks(
        Path(args.pmb_oos_picks) if args.pmb_oos_picks else _default_pmb_oos_picks_path(),
        as_of,
        cfg,
    )
    if args.require_pmb_oos_coverage and not pmb_oos_gate.get("pass"):
        blockers.append("pmb_oos_coverage_gate_failed")

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
    baseline_gate: dict[str, Any] | None = None
    production_gate: dict[str, Any] | None = None
    if run_backtests:
        for job in _planned_backtests(args, as_of, out_dir):
            result = _run_command(job["cmd"], PROJECT_ROOT)
            metrics_path = Path(job["out_dir"]) / "leader_backtest_metrics.json"
            metrics = _read_json(metrics_path)
            gate = evaluate_job_metrics(metrics, cfg, job, pmb_oos_gate) if metrics else {"all_pass": False, "checks": {}}
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
            if (
                job["period"] == "official_8y"
                and job["profile"] == "full"
                and job.get("strategy_preset") == "default"
            ):
                baseline_gate = {
                    "period": job["period"],
                    "profile": job["profile"],
                    "strategy_preset": job.get("strategy_preset"),
                    "all_pass": bool(gate.get("all_pass")),
                    "checks": gate.get("checks", {}),
                    "metrics_path": str(metrics_path),
                }
            if (
                job["period"] == "official_8y"
                and job.get("strategy_preset") == PRODUCTION_GATE_STRATEGY_PRESET
            ):
                production_gate = {
                    "period": job["period"],
                    "profile": job["profile"],
                    "strategy_preset": job.get("strategy_preset"),
                    "all_pass": bool(gate.get("all_pass")),
                    "checks": gate.get("checks", {}),
                    "metrics_path": str(metrics_path),
                }
        official_gate = production_gate or baseline_gate
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
        "pmb_oos_build_settings": {
            "score_mode": args.pmb_oos_score_mode,
            "pre_buffer_months": int(args.pmb_pre_buffer_months),
            "iterations": int(args.pmb_iterations),
        },
        "data_gate": {
            "status": data_gate_status,
            "audit_json": str(audit_json),
            "audit_md": str(audit_md),
            "summary": audit.get("summary", {}),
        },
        "scored_panel_window_gate": scored_panel_window_gate,
        "pmb_oos_gate": pmb_oos_gate,
        "daily_broker_check": daily_payload,
        "command_results": command_results,
        "baseline_gate": baseline_gate,
        "production_gate": production_gate,
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
    if status in {"passed", "completed_no_official_backtest"}:
        return 0
    return 2 if status == "blocked" else 1


if __name__ == "__main__":
    sys.exit(main())
