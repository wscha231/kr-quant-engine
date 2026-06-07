"""Diagnose KR1000 top-rank reversal and continuation risk.

This is a diagnostic-only tool. It uses already generated forward returns to
audit whether candidate risk ideas are supported over the full historical
window; it must not feed official broker-ledger scoring directly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_RUN_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "kr1000_bt_technical_value_mcap_cash_reason_default_2018_20260604"
)

DEFAULT_FEATURE_COLUMNS = (
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "rs_1m",
    "rs_3m",
    "rs_6m",
    "market_cap",
    "avg_trading_value_60d",
    "kr1000_liquidity_rank",
    "rsi_14",
    "atr_pct",
    "volume_zscore_50",
    "dist_from_52w_high",
    "foreign_inst_combined_zscore_20d",
    "individual_net_buy_20d_zscore",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Diagnose KR1000 top-rank risk patterns")
    p.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    p.add_argument("--scored-panel", default=None)
    p.add_argument("--out-dir", default="outputs/kr1000_rank_risk_diagnostic")
    p.add_argument("--top-n", type=int, default=10)
    p.add_argument("--quantile", type=float, default=0.80)
    p.add_argument("--feature-cols", default=",".join(DEFAULT_FEATURE_COLUMNS))
    return p.parse_args()


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p, dtype={"ticker": str})
    return pd.read_parquet(p)


def _numeric(df: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def build_rank_risk_panel(scored: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """Return top-ranked rows with numeric diagnostic columns."""
    required = {"leader_rank", "forward_return_1m"}
    missing = required.difference(scored.columns)
    if missing:
        raise ValueError(f"scored panel missing required columns: {sorted(missing)}")
    out = scored.copy()
    date_col = "rebalance_date" if "rebalance_date" in out.columns else "date"
    if date_col in out.columns:
        out[date_col] = pd.to_datetime(out[date_col], errors="coerce").dt.normalize()
        out = out.rename(columns={date_col: "rebalance_date"})
    out["ticker"] = out.get("ticker", "").astype(str).str.extract(r"(\d+)", expand=False).fillna("").str.zfill(6)
    out["leader_rank"] = _numeric(out, "leader_rank")
    out["forward_return_1m"] = _numeric(out, "forward_return_1m")
    out = out[
        out["leader_rank"].le(int(top_n))
        & out["forward_return_1m"].notna()
    ].copy()
    for col in DEFAULT_FEATURE_COLUMNS:
        if col in out.columns:
            out[col] = _numeric(out, col)
    return out.reset_index(drop=True)


def summarize_feature_bins(
    panel: pd.DataFrame,
    feature_cols: list[str] | tuple[str, ...] = DEFAULT_FEATURE_COLUMNS,
    quantile: float = 0.80,
) -> pd.DataFrame:
    """Compare next-month returns for high and low feature bins."""
    rows: list[dict[str, Any]] = []
    if panel.empty:
        return pd.DataFrame(columns=[
            "feature", "low_cut", "high_cut", "n_low", "n_high",
            "low_mean_forward_return", "high_mean_forward_return",
            "high_minus_low", "low_loss_rate", "high_loss_rate",
        ])
    q = min(0.95, max(0.55, float(quantile)))
    for feature in feature_cols:
        if feature not in panel.columns:
            continue
        s = pd.to_numeric(panel[feature], errors="coerce")
        valid = panel[s.notna() & panel["forward_return_1m"].notna()].copy()
        if len(valid) < 20:
            continue
        low_cut = float(valid[feature].quantile(1.0 - q))
        high_cut = float(valid[feature].quantile(q))
        low = valid[valid[feature] <= low_cut]["forward_return_1m"]
        high = valid[valid[feature] >= high_cut]["forward_return_1m"]
        if low.empty or high.empty:
            continue
        rows.append({
            "feature": feature,
            "low_cut": low_cut,
            "high_cut": high_cut,
            "n_low": int(len(low)),
            "n_high": int(len(high)),
            "low_mean_forward_return": float(low.mean()),
            "high_mean_forward_return": float(high.mean()),
            "high_minus_low": float(high.mean() - low.mean()),
            "low_loss_rate": float((low < 0).mean()),
            "high_loss_rate": float((high < 0).mean()),
        })
    return pd.DataFrame(rows).sort_values("high_minus_low").reset_index(drop=True)


def summarize_conditions(panel: pd.DataFrame) -> pd.DataFrame:
    """Summarize recurring chase/continuation risk hypotheses."""
    if panel.empty:
        return pd.DataFrame(columns=[
            "condition", "n", "coverage", "mean_forward_return",
            "median_forward_return", "loss_rate", "complement_mean_forward_return",
        ])
    ret = panel["forward_return_1m"]
    conditions: dict[str, pd.Series] = {
        "ret_1m_gt_50pct": _numeric(panel, "ret_1m", -np.inf) > 0.50,
        "ret_1m_gt_100pct": _numeric(panel, "ret_1m", -np.inf) > 1.00,
        "ret_3m_gt_100pct": _numeric(panel, "ret_3m", -np.inf) > 1.00,
        "ret_3m_gt_200pct": _numeric(panel, "ret_3m", -np.inf) > 2.00,
        "ret_1m_gt_50pct_and_rsi_gt_75": (
            (_numeric(panel, "ret_1m", -np.inf) > 0.50)
            & (_numeric(panel, "rsi_14", -np.inf) > 75.0)
        ),
        "ret_1m_gt_50pct_near_52w_high": (
            (_numeric(panel, "ret_1m", -np.inf) > 0.50)
            & (_numeric(panel, "dist_from_52w_high", -np.inf) > -0.05)
        ),
        "volume_zscore_top_quintile": (
            _numeric(panel, "volume_zscore_50", np.nan)
            >= _numeric(panel, "volume_zscore_50", np.nan).quantile(0.80)
        ),
    }
    rows: list[dict[str, Any]] = []
    for name, mask in conditions.items():
        mask = mask.fillna(False)
        sub = panel[mask]
        comp = panel[~mask]
        if sub.empty:
            continue
        rows.append({
            "condition": name,
            "n": int(len(sub)),
            "coverage": float(len(sub) / len(panel)) if len(panel) else 0.0,
            "mean_forward_return": float(sub["forward_return_1m"].mean()),
            "median_forward_return": float(sub["forward_return_1m"].median()),
            "loss_rate": float((sub["forward_return_1m"] < 0).mean()),
            "complement_mean_forward_return": float(comp["forward_return_1m"].mean()) if not comp.empty else 0.0,
            "diagnostic_edge_vs_complement": float(sub["forward_return_1m"].mean() - comp["forward_return_1m"].mean()) if not comp.empty else 0.0,
        })
    return pd.DataFrame(rows).sort_values("diagnostic_edge_vs_complement").reset_index(drop=True)


def summarize_rank_risk(panel: pd.DataFrame, feature_bins: pd.DataFrame, conditions: pd.DataFrame) -> dict[str, Any]:
    ret = panel["forward_return_1m"] if not panel.empty else pd.Series(dtype=float)
    return {
        "diagnostic_only": True,
        "official_broker_ledger_metric": False,
        "rows": int(len(panel)),
        "months": int(panel["rebalance_date"].nunique()) if "rebalance_date" in panel.columns else 0,
        "mean_forward_return_1m": float(ret.mean()) if len(ret) else 0.0,
        "median_forward_return_1m": float(ret.median()) if len(ret) else 0.0,
        "loss_rate": float((ret < 0).mean()) if len(ret) else 0.0,
        "worst_high_feature_edges": feature_bins.head(5).to_dict(orient="records") if not feature_bins.empty else [],
        "best_high_feature_edges": feature_bins.tail(5).to_dict(orient="records") if not feature_bins.empty else [],
        "condition_edges": conditions.to_dict(orient="records") if not conditions.empty else [],
    }


def write_report(summary: dict[str, Any], path: Path) -> None:
    lines = [
        "# KR1000 Rank-Risk Diagnostic",
        "",
        "Diagnostic only. Forward returns are used for research audit and must not feed official scoring.",
        "",
        "## Summary",
        "",
        f"- Rows: `{summary['rows']}`",
        f"- Months: `{summary['months']}`",
        f"- Mean 1m forward return: `{summary['mean_forward_return_1m']:.2%}`",
        f"- Median 1m forward return: `{summary['median_forward_return_1m']:.2%}`",
        f"- Loss rate: `{summary['loss_rate']:.1%}`",
        "",
        "## Worst High-Feature Edges",
        "",
    ]
    for row in summary["worst_high_feature_edges"]:
        lines.append(
            f"- `{row['feature']}` high-minus-low `{row['high_minus_low']:.2%}` "
            f"(high mean `{row['high_mean_forward_return']:.2%}`, low mean `{row['low_mean_forward_return']:.2%}`)"
        )
    lines.extend(["", "## Condition Edges", ""])
    for row in summary["condition_edges"]:
        lines.append(
            f"- `{row['condition']}` N `{row['n']}`, edge `{row['diagnostic_edge_vs_complement']:.2%}`, "
            f"mean `{row['mean_forward_return']:.2%}`, loss `{row['loss_rate']:.1%}`"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir)
    scored_path = Path(args.scored_panel) if args.scored_panel else run_dir / "leader_scored_panel.parquet"
    scored = _read_table(scored_path)
    features = [x.strip() for x in str(args.feature_cols).split(",") if x.strip()]
    panel = build_rank_risk_panel(scored, top_n=args.top_n)
    feature_bins = summarize_feature_bins(panel, features, quantile=args.quantile)
    conditions = summarize_conditions(panel)
    summary = summarize_rank_risk(panel, feature_bins, conditions)
    summary.update({
        "top_n": int(args.top_n),
        "quantile": float(args.quantile),
        "scored_panel": str(scored_path),
    })

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out_dir / "rank_risk_panel.csv", index=False, encoding="utf-8-sig")
    feature_bins.to_csv(out_dir / "rank_risk_feature_bins.csv", index=False, encoding="utf-8-sig")
    conditions.to_csv(out_dir / "rank_risk_condition_bins.csv", index=False, encoding="utf-8-sig")
    (out_dir / "rank_risk_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    write_report(summary, out_dir / "rank_risk_report.md")

    print("KR1000 rank-risk diagnostic")
    print(f"  rows:   {summary['rows']}")
    print(f"  months: {summary['months']}")
    print(f"  mean:   {summary['mean_forward_return_1m']:.2%}")
    print(f"  loss:   {summary['loss_rate']:.1%}")
    print(f"  output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
