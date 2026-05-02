"""streamlit_app.py — kr-quant-engine live dashboard (Phase D).

Streamlit Cloud entry point. Reads outputs/ artifacts produced by the
GitHub Actions workflows (monthly_picks.yml, quarterly_backtest.yml).

Run locally:
    streamlit run streamlit_app.py

Streamlit Cloud:
    1. Connect GitHub repo wscha231/kr-quant-engine
    2. Entry point: streamlit_app.py
    3. Add secrets in `Secrets`:
         GDRIVE_FOLDER_ID = "..."          # if pulling from GDrive directly
         RCLONE_CONFIG    = "..."           # base64 rclone config
       OR commit the latest CSVs to outputs/ in the repo and rely on the
       repo checkout (simpler).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).parent.resolve()
OUTPUTS_DIR_CANDIDATES = [
    PROJECT_ROOT / "outputs",                 # repo-committed
    PROJECT_ROOT / "data" / "outputs",        # GitHub Actions runner layout
]


def _outputs_dir() -> Path:
    for p in OUTPUTS_DIR_CANDIDATES:
        if p.exists():
            return p
    return OUTPUTS_DIR_CANDIDATES[0]


@st.cache_data(ttl=3600)
def load_picks(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


@st.cache_data(ttl=3600)
def load_meta(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main() -> None:
    st.set_page_config(
        page_title="kr-quant-engine | Live Dashboard",
        page_icon="📊",
        layout="wide",
    )
    st.title("📊 kr-quant-engine -- Live Dashboard")
    st.caption("Korean equity multibagger entry classifier with PIT-correct "
               "universe, governance overlay, purged walk-forward, and "
               "VKOSPI panic guard.")

    out_dir = _outputs_dir()
    picks_path = out_dir / "live_portfolio_latest.csv"
    picks = load_picks(picks_path)

    # Sidebar — meta info + controls
    with st.sidebar:
        st.header("Latest Run")
        if picks.empty:
            st.warning("No picks CSV found yet. Trigger the GitHub Action "
                       "`monthly_picks.yml` to generate one.")
        else:
            st.success(f"{len(picks)} picks loaded")
            if "rebalance_date" in picks.columns:
                rd = picks["rebalance_date"].iloc[0]
                st.metric("Rebalance Date", str(rd))
        # Show meta JSON if available
        meta_glob = sorted(out_dir.glob("live_meta_*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        if meta_glob:
            meta = load_meta(meta_glob[0])
            if meta:
                st.subheader("Run Metadata")
                st.json(meta)

    if picks.empty:
        st.info("Awaiting first picks file. Returning placeholder view.")
        return

    # Main — Picks table
    st.header("🎯 Current Picks")
    display_cols = [c for c in (
        "ticker", "name", "exchange", "market_cap",
        "p_pre_surge", "adjusted_score",
        "governance_risk_score", "governance_hard_veto_flag",
        "weight",
    ) if c in picks.columns]
    if "market_cap" in picks.columns:
        picks = picks.copy()
        picks["market_cap"] = (picks["market_cap"] / 1e8).round(0)
        picks = picks.rename(columns={"market_cap": "market_cap_억"})
        display_cols = [
            "market_cap_억" if c == "market_cap" else c for c in display_cols
        ]
    st.dataframe(
        picks[display_cols].head(50),
        use_container_width=True,
        height=600,
    )

    # Risk distribution chart
    if "governance_risk_score" in picks.columns:
        st.header("🚨 Governance Risk Distribution")
        st.bar_chart(
            picks.assign(
                risk_bin=pd.cut(
                    picks["governance_risk_score"].fillna(0),
                    bins=[-0.01, 0.1, 0.3, 0.5, 0.7, 1.0],
                    labels=["clean", "low", "mid", "high", "max"],
                )
            ).groupby("risk_bin", observed=True).size(),
        )

    # Quarterly backtest results
    qb_path = out_dir / "quarterly_backtest_latest.json"
    if qb_path.exists():
        try:
            with open(qb_path, "r", encoding="utf-8") as f:
                qb = json.load(f)
            st.header("📈 Latest Quarterly Backtest")
            metrics = qb.get("metrics", {})
            cols = st.columns(4)
            cols[0].metric("CAGR", f"{metrics.get('cagr', 0)*100:.2f}%"
                            if isinstance(metrics.get('cagr'), (int, float)) else "n/a")
            cols[1].metric("MDD", f"{metrics.get('mdd', 0)*100:.2f}%"
                            if isinstance(metrics.get('mdd'), (int, float)) else "n/a")
            cols[2].metric("Sharpe", f"{metrics.get('sharpe', 0):.2f}"
                            if isinstance(metrics.get('sharpe'), (int, float)) else "n/a")
            cols[3].metric("Final Capital",
                            f"{metrics.get('final_capital', 0)/1e8:.2f}억"
                            if isinstance(metrics.get('final_capital'), (int, float))
                            else "n/a")
            st.caption(f"Run: {qb.get('label')} -- generated "
                       f"{qb.get('generated_at')}")
        except Exception as ex:
            st.warning(f"Failed to load quarterly backtest: {ex}")

    # Equity curve
    monthly_csvs = sorted(
        out_dir.glob("quarterly_backtest_*_monthly.csv"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if monthly_csvs:
        try:
            monthly = pd.read_csv(monthly_csvs[0])
            if "rd" in monthly.columns and "capital" in monthly.columns:
                st.header("💰 Equity Curve (Quarterly Backtest)")
                eq = monthly[["rd", "capital"]].copy()
                eq["rd"] = pd.to_datetime(eq["rd"])
                eq = eq.set_index("rd")
                st.line_chart(eq)
        except Exception:
            pass

    st.markdown("---")
    st.caption(
        f"Dashboard rendered {datetime.now().isoformat(timespec='seconds')} | "
        f"outputs dir: `{out_dir}`"
    )


if __name__ == "__main__":
    main()
