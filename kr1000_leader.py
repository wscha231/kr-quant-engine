"""KR1000 Leader Alpha.

Stock-first leader ranking, current-holdings reconciliation, and a lightweight
order/cash/position ledger backtester for the KR1000 production universe.

The module is intentionally self-contained. It reuses existing PIT universe,
flow, DART, governance, and technical columns when they are present, but it can
also run on small synthetic panels for tests and quick backtest development.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from kr_config import (
    BROKERAGE_FEE_ONE_WAY,
    DATA_ROOT,
    DEFAULT_CFG,
    DEFAULT_ROUND_TRIP_COST,
    PRICE_LIMIT_PCT,
    PROJECT_ROOT,
    TRANSACTION_TAX_SELL,
    kr1000_leader_alpha_cfg,
)
from kr_helpers import cross_sectional_robust_z, log


TRADE_ACTIONS = ("BUY", "ADD", "HOLD", "TRIM", "SELL", "BLOCKED", "NO_TRADE")

LEADER_SCORE_WEIGHTS = {
    "rs_score": 0.35,
    "flow_score": 0.20,
    "technical_score": 0.15,
    "quality_growth_score": 0.10,
    "valuation_score": 0.08,
    "theme_sector_score": 0.07,
    "event_governance_score": 0.05,
}

KR1000_COMPONENT_SCORE_PROFILES = {
    "full": tuple(LEADER_SCORE_WEIGHTS),
    "rs_only": ("rs_score",),
    "rs_flow": ("rs_score", "flow_score"),
    "rs_flow_technical": ("rs_score", "flow_score", "technical_score"),
}

KR1000_DIRECT_SCORE_PROFILES = (
    "legacy_p0_momentum",
    "legacy_p1_blended",
    "pmb_pre_surge",
    "pmb_mid_rank_7_23",
    "hybrid_pmb_rs",
)

KR1000_SCORE_PROFILES = {
    **KR1000_COMPONENT_SCORE_PROFILES,
    **{name: () for name in KR1000_DIRECT_SCORE_PROFILES},
}

KR1000_AB_SCORE_PROFILES = (
    "full",
    "rs_only",
    "rs_flow",
    "rs_flow_technical",
    "legacy_p1_blended",
    "pmb_pre_surge",
    "pmb_mid_rank_7_23",
    "hybrid_pmb_rs",
)
CURRENT_HOLDINGS_COLUMNS = (
    "as_of_date",
    "account_id",
    "ticker",
    "name",
    "shares",
    "avg_cost",
    "last_price",
    "market_value",
    "weight",
    "unrealized_pnl_pct",
    "thesis_tag",
    "manual_lock",
)


def _cfg(cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if cfg and cfg.get("strategy_name") == "KR1000 Leader Alpha":
        return dict(cfg)
    return kr1000_leader_alpha_cfg(cfg or {})


def _to_bool_series(values: Any, index: pd.Index, default: bool = False) -> pd.Series:
    if isinstance(values, pd.Series):
        s = values.reindex(index)
    else:
        s = pd.Series(values, index=index)
    if s.dtype == bool:
        return s.fillna(default)
    return s.astype(str).str.lower().isin(("1", "true", "yes", "y", "on"))


def _eligibility_series(df: pd.DataFrame) -> pd.Series:
    """Resolve candidate eligibility with schema-union fallback.

    Older scored panels do not have eligible_final. Newer appended readiness
    rows do. After concatenation, old rows therefore carry eligible_final=NaN;
    those rows must fall back to in_kr1000 rather than becoming ineligible.
    """
    if "eligible_final" in df.columns:
        raw = df["eligible_final"]
        primary = _to_bool_series(raw, df.index, True)
        fallback = _to_bool_series(
            df["in_kr1000"] if "in_kr1000" in df.columns else True,
            df.index,
            True,
        )
        return primary.where(raw.notna(), fallback)
    elif "in_kr1000" in df.columns:
        raw = df["in_kr1000"]
    else:
        raw = True
    return _to_bool_series(raw, df.index, True)


def _numeric(df: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _score_series(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    if s.notna().sum() < 5:
        valid = s.dropna()
        if len(valid) <= 1:
            return pd.Series(0.0, index=s.index, dtype=float)
        std = valid.std()
        if not std or std <= 1e-12:
            return pd.Series(0.0, index=s.index, dtype=float)
        return ((s - valid.mean()) / std).clip(-4.0, 4.0).fillna(0.0)
    return cross_sectional_robust_z(s).fillna(0.0)


def _sparse_positive_rank_score(series: pd.Series) -> pd.Series:
    """Rank sparse positive signals while leaving missing/non-selected rows at 0.

    P_MB walk-forward OOS picks are sparse: only selected names have
    probabilities and the rest are explicit zeroes. Robust z-score degenerates
    to all zero when the cross-section median and MAD are both zero, so use a
    positive-only percentile rank for these PIT-safe probabilities.
    """
    s = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out = pd.Series(0.0, index=s.index, dtype=float)
    positive = s > 0
    if not positive.any():
        return out
    pos = s[positive]
    if pos.nunique(dropna=True) <= 1:
        out.loc[positive] = 1.0
    else:
        out.loc[positive] = pos.rank(pct=True, method="average")
    return out


def _z(df: pd.DataFrame, col: str) -> pd.Series:
    return _score_series(_numeric(df, col))


def _normalise_ticker(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
def build_kr1000_universe(
    rebalance_date: str | pd.Timestamp,
    cfg: Optional[dict[str, Any]] = None,
    snapshot: Optional[pd.DataFrame] = None,
    include_discovery: bool = False,
) -> pd.DataFrame:
    """Build PIT KR1000 from a universe snapshot.

    KR1000 is the top N eligible KOSPI+KOSDAQ common stocks by
    avg_trading_value_60d, with market_cap as the deterministic tiebreaker.
    When include_discovery=True, return every eligible row and mark KR1000
    membership with in_kr1000.
    """
    cfg = _cfg(cfg)
    rd = pd.Timestamp(rebalance_date).normalize()
    if snapshot is None:
        from kr_universe import build_universe_snapshot

        snapshot = build_universe_snapshot(rd, cfg=cfg)
    if snapshot is None or snapshot.empty:
        return pd.DataFrame()

    df = snapshot.copy()
    if "ticker" not in df.columns:
        raise ValueError("snapshot must include ticker")
    df["ticker"] = _normalise_ticker(df["ticker"])
    if "rebalance_date" not in df.columns:
        df["rebalance_date"] = rd
    if "eligible" not in df.columns:
        df["eligible"] = True
    if "exclude_reason" not in df.columns:
        df["exclude_reason"] = ""

    for col in ("avg_trading_value_60d", "market_cap"):
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values(
        ["eligible", "avg_trading_value_60d", "market_cap", "ticker"],
        ascending=[False, False, False, True],
    ).drop_duplicates("ticker", keep="first").reset_index(drop=True)

    common_code = df["ticker"].str.match(r"^\d{6}$", na=False)
    df.loc[~common_code & (df["exclude_reason"].fillna("") == ""), "exclude_reason"] = "non_common_stock_code"
    df.loc[~common_code, "eligible"] = False

    tradable = (
        df["eligible"].fillna(False).astype(bool)
        & df["avg_trading_value_60d"].notna()
        & (df["avg_trading_value_60d"] > 0)
        & common_code
    )
    ranked = df.loc[tradable].sort_values(
        ["avg_trading_value_60d", "market_cap", "ticker"],
        ascending=[False, False, True],
    )
    df["kr1000_liquidity_rank"] = np.nan
    df.loc[ranked.index, "kr1000_liquidity_rank"] = np.arange(1, len(ranked) + 1)

    cap_ranked = df.loc[tradable].sort_values(
        ["market_cap", "avg_trading_value_60d", "ticker"],
        ascending=[False, False, True],
    )
    df["kr1000_market_cap_rank"] = np.nan
    df.loc[cap_ranked.index, "kr1000_market_cap_rank"] = np.arange(1, len(cap_ranked) + 1)

    n = int(cfg.get("kr1000_size", 1000))
    df["in_kr1000"] = (
        df["kr1000_liquidity_rank"].notna()
        & (df["kr1000_liquidity_rank"] <= n)
    )
    df["universe_bucket"] = np.where(df["in_kr1000"], "KR1000", "KR-All Discovery")
    df["eligible_final"] = df["in_kr1000"].astype(bool)

    out = df if include_discovery else df[df["in_kr1000"]].copy()
    return out.sort_values(
        ["kr1000_liquidity_rank", "market_cap"], ascending=[True, False]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Relative strength and component scores
# ---------------------------------------------------------------------------
def _prepare_close_frame(panel: pd.DataFrame | pd.Series) -> pd.DataFrame:
    if isinstance(panel, pd.Series):
        return pd.DataFrame({"date": pd.to_datetime(panel.index), "close": panel.values})
    df = panel.copy()
    if "date" not in df.columns:
        df = df.reset_index().rename(columns={df.index.name or "index": "date"})
    if "close" not in df.columns:
        raise ValueError("price panel must include close")
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["date", "close"])


def _lookback_return(close: pd.Series, lookback: int) -> float:
    close = close.dropna()
    if len(close) <= lookback:
        return float("nan")
    cur = float(close.iloc[-1])
    prev = float(close.iloc[-lookback - 1])
    if prev <= 0:
        return float("nan")
    return cur / prev - 1.0


def _beta_adjusted_return(stock_close: pd.Series, bench_close: pd.Series, lookback: int) -> float:
    if len(stock_close.dropna()) <= lookback or len(bench_close.dropna()) <= lookback:
        return float("nan")
    sret = stock_close.pct_change().dropna().tail(max(lookback, 60))
    bret = bench_close.pct_change().dropna().tail(max(lookback, 60))
    aligned = pd.concat([sret.rename("s"), bret.rename("b")], axis=1).dropna()
    if len(aligned) < 20 or aligned["b"].var() <= 1e-12:
        beta = 1.0
    else:
        beta = float(aligned["s"].cov(aligned["b"]) / aligned["b"].var())
    stock_r = _lookback_return(stock_close, lookback)
    bench_r = _lookback_return(bench_close, lookback)
    if not np.isfinite(stock_r) or not np.isfinite(bench_r):
        return float("nan")
    return stock_r - beta * bench_r


def compute_kospi200_relative_strength(
    price_panel: pd.DataFrame,
    benchmark_panel: pd.DataFrame | pd.Series,
    as_of: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Compute 1m/3m/6m relative strength vs KOSPI200."""
    if price_panel.empty:
        return pd.DataFrame()
    prices = _prepare_close_frame(price_panel)
    if "ticker" not in prices.columns:
        raise ValueError("price_panel must include ticker")
    prices["ticker"] = _normalise_ticker(prices["ticker"])
    bench = _prepare_close_frame(benchmark_panel).sort_values("date")
    if as_of is None:
        as_ts = min(prices["date"].max(), bench["date"].max())
    else:
        as_ts = pd.Timestamp(as_of).normalize()
    prices = prices[prices["date"] <= as_ts].sort_values(["ticker", "date"])
    bench = bench[bench["date"] <= as_ts].set_index("date")["close"].astype(float)

    rows: list[dict[str, Any]] = []
    horizons = {"1m": 21, "3m": 63, "6m": 126}
    for tk, g in prices.groupby("ticker", sort=False):
        s = g.set_index("date")["close"].astype(float).sort_index()
        row: dict[str, Any] = {"ticker": tk, "as_of_date": as_ts}
        for label, lb in horizons.items():
            sr = _lookback_return(s, lb)
            br = _lookback_return(bench, lb)
            row[f"rs_{label}"] = sr - br if np.isfinite(sr) and np.isfinite(br) else np.nan
        row["beta_adjusted_rs_3m"] = _beta_adjusted_return(s, bench, 63)
        row["beta_adjusted_rs_6m"] = _beta_adjusted_return(s, bench, 126)
        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    z1 = _score_series(out["rs_1m"])
    z3 = _score_series(out["rs_3m"])
    z6 = _score_series(out["rs_6m"])
    out["rs_composite"] = 0.25 * z1 + 0.35 * z3 + 0.40 * z6
    out["rs_score"] = out["rs_composite"]
    return out


def add_leader_component_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Add component scores used by leader_score."""
    out = df.copy()
    if "rs_score" not in out.columns:
        if all(c in out.columns for c in ("rs_1m", "rs_3m", "rs_6m")):
            out["rs_score"] = (
                0.25 * _z(out, "rs_1m")
                + 0.35 * _z(out, "rs_3m")
                + 0.40 * _z(out, "rs_6m")
            )
        else:
            out["rs_score"] = 0.0

    if "flow_score" not in out.columns:
        out["flow_score"] = (
            0.35 * _z(out, "foreign_netbuy_20d_to_mcap")
            + 0.25 * _z(out, "inst_netbuy_20d_to_mcap")
            + 0.15 * _z(out, "foreign_netbuy_60d_to_mcap")
            + 0.10 * _z(out, "flow_acceleration")
            + 0.10 * _z(out, "foreign_holding_change_20d")
            + 0.05 * _z(out, "foreign_buying_streak_days")
            + 0.35 * _z(out, "foreign_net_buy_20d_zscore")
            + 0.25 * _z(out, "inst_net_buy_20d_zscore")
            + 0.20 * _z(out, "foreign_inst_combined_zscore_20d")
            - 0.15 * _z(out, "individual_net_buy_20d_zscore").clip(lower=0)
        )

    if "technical_score" not in out.columns:
        trend_flag = _numeric(out, "trend_template_pass", 0.0)
        out["technical_score"] = (
            0.30 * _z(out, "trend_template_score")
            + 0.20 * _z(out, "breakout_flag")
            + 0.20 * (1.0 - _z(out, "dist_from_52w_high").abs() / 4.0).fillna(0.0)
            + 0.15 * _z(out, "volume_zscore_50")
            - 0.10 * _z(out, "atr_pct").clip(lower=0)
            + 0.15 * trend_flag.fillna(0.0)
        )

    if "quality_growth_score" not in out.columns:
        out["quality_growth_score"] = (
            0.30 * _z(out, "revenue_growth_yoy")
            + 0.25 * _z(out, "operating_income_growth_yoy")
            + 0.20 * _z(out, "roe")
            + 0.15 * _z(out, "quality_score")
            + 0.10 * _z(out, "fcf_yield")
        )

    if "valuation_score" not in out.columns:
        cheap = 0.35 * (-_z(out, "per")) + 0.30 * (-_z(out, "pbr")) + 0.20 * _z(out, "value_score")
        growth_confirm = 0.15 * _z(out, "revenue_growth_yoy")
        extreme_penalty = ((_numeric(out, "per") > 150) & (_numeric(out, "revenue_growth_yoy") <= 0)).astype(float)
        out["valuation_score"] = cheap + growth_confirm - extreme_penalty

    if "theme_sector_score" not in out.columns:
        out["theme_sector_score"] = (
            0.25 * _z(out, "theme_rs_1m")
            + 0.30 * _z(out, "theme_rs_3m")
            + 0.30 * _z(out, "theme_rs_6m")
            + 0.15 * _z(out, "theme_phase_score")
            + 0.25 * _z(out, "sector_rs_3m")
        )

    if "event_governance_score" not in out.columns:
        out["event_governance_score"] = (
            0.45 * _z(out, "disclosure_event_total_score")
            + 0.20 * _z(out, "event_insider_holdings_score")
            + 0.15 * _z(out, "event_treasury_buyback_score")
            + 0.10 * _z(out, "event_major_holders_score")
            + 0.10 * _z(out, "capital_allocation_quality_score")
            - 0.35 * _numeric(out, "governance_risk_score", 0.0).fillna(0.0)
            - 0.25 * _numeric(out, "owner_dilution_risk_score", 0.0).fillna(0.0)
        )
    for col in (
        "rs_score",
        "flow_score",
        "technical_score",
        "quality_growth_score",
        "valuation_score",
        "theme_sector_score",
        "event_governance_score",
    ):
        out[col] = pd.to_numeric(out[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return out


def apply_kr1000_score_profile(
    candidates: pd.DataFrame,
    score_profile: str = "full",
) -> pd.DataFrame:
    """Apply an official KR1000 component A/B score profile.

    Inactive components are zeroed before the standard leader_score formula is
    applied. This keeps the broker backtest identical across A/B variants while
    changing only the stock-selection signal.
    """
    profile = str(score_profile or "full").strip().lower()
    if profile not in KR1000_SCORE_PROFILES:
        valid = ", ".join(sorted(KR1000_SCORE_PROFILES))
        raise ValueError(f"unknown KR1000 score_profile={score_profile!r}; valid: {valid}")

    out = add_leader_component_scores(candidates)
    if profile in KR1000_COMPONENT_SCORE_PROFILES:
        active = set(KR1000_COMPONENT_SCORE_PROFILES[profile])
        for col in LEADER_SCORE_WEIGHTS:
            if col not in active:
                out[col] = 0.0
            else:
                out[col] = pd.to_numeric(out[col], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)

        out["leader_score"] = 0.0
        for col, weight in LEADER_SCORE_WEIGHTS.items():
            out["leader_score"] += weight * out[col]
    elif profile == "legacy_p0_momentum":
        out["leader_score"] = _score_series(_numeric(out, "p0_momentum_score", 0.0))
    elif profile == "legacy_p1_blended":
        source = _numeric(out, "p1_blended_score", np.nan)
        fallback = _numeric(out, "p0_momentum_score", 0.0)
        out["leader_score"] = _score_series(source.fillna(fallback))
    elif profile == "pmb_pre_surge":
        out["leader_score"] = _sparse_positive_rank_score(_numeric(out, "p_pre_surge", 0.0))
    elif profile == "pmb_mid_rank_7_23":
        pmb = _numeric(out, "p_pre_surge", 0.0).fillna(0.0)
        rank = _numeric(out, "pmb_oos_rank", np.nan)
        mid_rank = rank.between(7, 23, inclusive="both")
        out["leader_score"] = _sparse_positive_rank_score(pmb.where(mid_rank, 0.0))
    elif profile == "hybrid_pmb_rs":
        pmb = _sparse_positive_rank_score(_numeric(out, "p_pre_surge", 0.0))
        out["leader_score"] = (
            0.45 * pmb
            + 0.35 * out["rs_score"]
            + 0.20 * out["flow_score"]
        )
    out["score_profile"] = profile
    return out


def compute_leader_scores(candidates: pd.DataFrame, cfg: Optional[dict[str, Any]] = None) -> pd.DataFrame:
    """Compute final stock-first leader ranking and risk veto flags."""
    cfg = _cfg(cfg)
    if candidates.empty:
        return candidates.copy()
    out = apply_kr1000_score_profile(candidates, str(cfg.get("score_profile", "full")))
    if "ticker" in out.columns:
        out["ticker"] = _normalise_ticker(out["ticker"])

    eligible = _eligibility_series(out)
    liquidity_fail = _numeric(out, "avg_trading_value_60d", 1.0).fillna(0) <= 0
    governance_veto = _numeric(out, "governance_hard_veto_flag", 0.0).fillna(0.0) >= 0.5
    admin_veto = _to_bool_series(out.get("admin_issue_flag", False), out.index, False)
    suspended = _to_bool_series(out.get("suspended_flag", False), out.index, False)
    delisting = _to_bool_series(out.get("delisting_risk_flag", False), out.index, False)
    dilution = (
        (_numeric(out, "event_capital_increase_score", 0.0).fillna(0.0) < -0.2)
        | (_numeric(out, "event_convertible_bond_score", 0.0).fillna(0.0) < -0.2)
        | (_numeric(out, "event_warrant_bond_score", 0.0).fillna(0.0) < -0.2)
        | (_numeric(out, "owner_dilution_risk_score", 0.0).fillna(0.0) > 0.70)
    )
    hard_exit = _to_bool_series(out.get("hard_exit_flag", False), out.index, False)
    if "unrealized_pnl_pct" in out.columns:
        hard_exit = hard_exit | ((_numeric(out, "unrealized_pnl_pct", 0.0) < -0.15) & (out["rs_score"] < 0))
    if "rs_score" in out.columns and "flow_score" in out.columns:
        hard_exit = hard_exit | ((out["rs_score"] < -1.5) & (out["flow_score"] < -1.0))

    out["risk_veto_flag"] = (
        liquidity_fail | governance_veto | admin_veto | suspended | delisting
    ).astype(int)
    out["hard_exit_flag"] = hard_exit.astype(int)
    out["dilution_risk_flag"] = dilution.astype(int)
    out["eligible_final"] = (eligible & (out["risk_veto_flag"] == 0)).astype(bool)

    max_w = float(cfg.get("single_stock_max_weight", 0.07))
    out["max_weight"] = max_w
    out.loc[out["dilution_risk_flag"] == 1, "max_weight"] = max_w * 0.5
    out.loc[(out["risk_veto_flag"] == 1) | (out["hard_exit_flag"] == 1), "max_weight"] = 0.0

    out["leader_rank"] = np.nan
    ranked = out[out["eligible_final"]].sort_values(
        ["leader_score", "avg_trading_value_60d", "market_cap"],
        ascending=[False, False, False],
    )
    out.loc[ranked.index, "leader_rank"] = np.arange(1, len(ranked) + 1)
    return out.sort_values(["leader_rank", "leader_score"], ascending=[True, False]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Portfolio target and current-holdings trade plan
# ---------------------------------------------------------------------------
def build_target_portfolio(
    candidates: pd.DataFrame,
    cfg: Optional[dict[str, Any]] = None,
    as_of_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    cfg = _cfg(cfg)
    scored = candidates.copy()
    if "leader_rank" not in scored.columns:
        scored = compute_leader_scores(scored, cfg)
    top_n = int(cfg.get("top_holdings", cfg.get("portfolio_size", 20)))
    gross = float(cfg.get("gross_exposure", cfg.get("gross_exposure_default", 1.0)))
    gross = min(float(cfg.get("gross_exposure_max", 1.0)), max(float(cfg.get("gross_exposure_min", 0.45)), gross))
    sel = scored[
        scored["eligible_final"].fillna(False).astype(bool)
        & (pd.to_numeric(scored["leader_rank"], errors="coerce") <= top_n)
        & (pd.to_numeric(scored["max_weight"], errors="coerce").fillna(0) > 0)
    ].copy()
    if sel.empty:
        return pd.DataFrame(columns=["date", "ticker", "leader_rank", "leader_score", "target_weight"])

    base_w = gross / max(len(sel), 1)
    sel["target_weight"] = np.minimum(base_w, pd.to_numeric(sel["max_weight"], errors="coerce").fillna(base_w))

    group_col = "theme_key" if "theme_key" in sel.columns else ("sector" if "sector" in sel.columns else None)
    cap = float(cfg.get("sector_theme_max_weight", 0.25))
    if group_col:
        for _, idx in sel.groupby(group_col).groups.items():
            group_sum = float(sel.loc[idx, "target_weight"].sum())
            if group_sum > cap and group_sum > 0:
                sel.loc[idx, "target_weight"] *= cap / group_sum

    sel["date"] = pd.Timestamp(as_of_date).normalize() if as_of_date is not None else pd.Timestamp.today().normalize()
    sel["entry_reason"] = "BUY_TOP_SCORE"
    keep = [
        "date", "ticker", "name", "leader_rank", "leader_score", "target_weight",
        "max_weight", "entry_reason", "risk_veto_flag", "hard_exit_flag",
    ]
    keep = [c for c in keep if c in sel.columns]
    return sel[keep].sort_values("leader_rank").reset_index(drop=True)


def portfolio_drawdown_exposure_scale(
    current_drawdown: float,
    thresholds: Any = None,
    scales: Any = None,
) -> float:
    """Return a PIT-safe gross-exposure scale from portfolio drawdown.

    The ladder is evaluated from already-observed account NAV, so it can be
    used at a rebalance without reading future returns.
    """
    try:
        dd = float(current_drawdown)
    except (TypeError, ValueError):
        dd = 0.0
    if not np.isfinite(dd):
        dd = 0.0
    ths = list(thresholds if thresholds is not None else (-0.08, -0.15, -0.25))
    scs = list(scales if scales is not None else (0.85, 0.65, 0.40))
    scale = 1.0
    for th, sc in zip(ths, scs):
        try:
            threshold = float(th)
            candidate = float(sc)
        except (TypeError, ValueError):
            continue
        if dd <= threshold:
            scale = candidate
    return float(min(1.0, max(0.0, scale)))


def load_current_holdings(path: str | Path | None = None) -> pd.DataFrame:
    """Load current holdings. Missing file returns an empty schema frame."""
    p = Path(path) if path else PROJECT_ROOT / "state" / "current_holdings.csv"
    if not p.exists():
        return pd.DataFrame(columns=CURRENT_HOLDINGS_COLUMNS)
    df = pd.read_csv(p, dtype={"ticker": str})
    for c in CURRENT_HOLDINGS_COLUMNS:
        if c not in df.columns:
            df[c] = "" if c in ("as_of_date", "account_id", "ticker", "name", "thesis_tag") else 0
    df["ticker"] = _normalise_ticker(df["ticker"])
    for c in ("shares", "avg_cost", "last_price", "market_value", "weight", "unrealized_pnl_pct"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    if "manual_lock" in df.columns:
        df["manual_lock"] = _to_bool_series(df["manual_lock"], df.index, False)
    return df[list(CURRENT_HOLDINGS_COLUMNS)]


def _prepare_holdings_weights(holdings: pd.DataFrame) -> pd.DataFrame:
    h = holdings.copy()
    if h.empty:
        return h
    h["ticker"] = _normalise_ticker(h["ticker"])
    h["shares"] = pd.to_numeric(h.get("shares", 0), errors="coerce").fillna(0.0)
    h["last_price"] = pd.to_numeric(h.get("last_price", 0), errors="coerce").fillna(0.0)
    h["market_value"] = pd.to_numeric(h.get("market_value", 0), errors="coerce")
    h["market_value"] = h["market_value"].where(h["market_value"].fillna(0) > 0, h["shares"] * h["last_price"])
    total = float(h["market_value"].sum())
    h["weight"] = h["market_value"] / total if total > 0 else 0.0
    return h


def generate_trade_plan(
    current_holdings: pd.DataFrame,
    target_portfolio: pd.DataFrame,
    candidates: pd.DataFrame,
    cfg: Optional[dict[str, Any]] = None,
    as_of_date: str | pd.Timestamp | None = None,
    account_nav: float | None = None,
) -> pd.DataFrame:
    """Reconcile current holdings against target and produce trade actions."""
    cfg = _cfg(cfg)
    h = _prepare_holdings_weights(current_holdings)
    cfg_nav = pd.to_numeric(cfg.get("account_nav_krw", np.nan), errors="coerce")
    nav_value = pd.to_numeric(account_nav, errors="coerce")
    if not np.isfinite(nav_value) or nav_value <= 0:
        nav_value = float(cfg_nav) if np.isfinite(cfg_nav) and float(cfg_nav) > 0 else np.nan
    holdings_mv = float(h["market_value"].sum()) if not h.empty else 0.0
    total_mv = float(nav_value) if np.isfinite(nav_value) and float(nav_value) > 0 else holdings_mv
    if not h.empty and total_mv > 0:
        h["weight"] = h["market_value"] / total_mv
    t = target_portfolio.copy()
    c = candidates.copy()
    for df in (h, t, c):
        if not df.empty and "ticker" in df.columns:
            df["ticker"] = _normalise_ticker(df["ticker"])

    tickers = sorted(set(h.get("ticker", pd.Series(dtype=str))).union(set(t.get("ticker", pd.Series(dtype=str)))))
    if not c.empty and "leader_rank" in c.columns:
        tickers = sorted(set(tickers).union(set(c.loc[pd.to_numeric(c["leader_rank"], errors="coerce") <= int(cfg.get("buy_rank_threshold", 20)), "ticker"])))

    cand = c.set_index("ticker", drop=False) if not c.empty and "ticker" in c.columns else pd.DataFrame()
    tgt = t.set_index("ticker", drop=False) if not t.empty and "ticker" in t.columns else pd.DataFrame()
    cur = h.set_index("ticker", drop=False) if not h.empty and "ticker" in h.columns else pd.DataFrame()

    rows = []
    hold_rank = int(cfg.get("hold_rank_threshold", 40))
    buy_rank = int(cfg.get("buy_rank_threshold", 20))
    band = float(cfg.get("hold_band_weight", 0.01))
    min_notional = float(cfg.get("min_notional_krw", 100000.0))

    for tk in tickers:
        crow = cand.loc[tk] if tk in cand.index else pd.Series(dtype=object)
        trow = tgt.loc[tk] if tk in tgt.index else pd.Series(dtype=object)
        hrow = cur.loc[tk] if tk in cur.index else pd.Series(dtype=object)
        cur_w = float(hrow.get("weight", 0.0) or 0.0)
        tgt_w = float(trow.get("target_weight", 0.0) or 0.0)
        rank = crow.get("leader_rank", np.nan)
        rank_f = float(rank) if pd.notna(rank) else np.inf
        risk_veto = int(crow.get("risk_veto_flag", 0) or 0)
        hard_exit = int(crow.get("hard_exit_flag", 0) or 0)
        manual_lock = bool(hrow.get("manual_lock", False))
        is_current = float(hrow.get("shares", 0.0) or 0.0) > 0

        action = "NO_TRADE"
        reason = "NO_TRADE_MIN_NOTIONAL"
        if manual_lock and is_current:
            action, reason = "HOLD", "HOLD_MANUAL_LOCK"
        elif risk_veto and is_current:
            action, reason = "SELL", "SELL_GOVERNANCE_RISK"
        elif risk_veto:
            action, reason = "BLOCKED", "BLOCKED_ADMIN_ISSUE"
        elif hard_exit and is_current:
            action, reason = "SELL", "SELL_HARD_STOP"
        elif is_current and rank_f > hold_rank and tgt_w <= 0:
            action, reason = "SELL", "SELL_RANK_BREAK"
        elif is_current and tgt_w <= 0 and rank_f <= hold_rank:
            action, reason = "HOLD", "HOLD_WITHIN_BAND"
        elif is_current and tgt_w > cur_w + band:
            action, reason = "ADD", "ADD_TARGET_WEIGHT"
        elif is_current and tgt_w < max(cur_w - band, 0):
            action, reason = "TRIM", "TRIM_OVERWEIGHT"
        elif is_current:
            action, reason = "HOLD", "HOLD_WITHIN_BAND"
        elif (not is_current) and tgt_w > 0 and rank_f <= buy_rank:
            action, reason = "BUY", "BUY_TOP_SCORE"

        delta_w = tgt_w - cur_w
        delta_krw = delta_w * total_mv if total_mv > 0 else np.nan
        if action in ("BUY", "ADD", "TRIM", "SELL") and np.isfinite(delta_krw):
            if abs(delta_krw) < min_notional and action != "SELL":
                action, reason = "NO_TRADE", "NO_TRADE_MIN_NOTIONAL"

        rows.append({
            "date": pd.Timestamp(as_of_date).normalize() if as_of_date is not None else pd.Timestamp.today().normalize(),
            "ticker": tk,
            "name": crow.get("name", trow.get("name", hrow.get("name", ""))),
            "action": action,
            "reason_code": reason,
            "leader_rank": rank if pd.notna(rank) else "",
            "leader_score": crow.get("leader_score", np.nan),
            "current_weight": cur_w,
            "target_weight": tgt_w,
            "delta_weight": delta_w,
            "estimated_trade_krw": delta_krw,
            "shares": hrow.get("shares", 0.0),
            "manual_lock": manual_lock,
            "risk_veto_flag": risk_veto,
            "hard_exit_flag": hard_exit,
        })
    out = pd.DataFrame(rows)
    out["reason_code"] = out["reason_code"].replace("", "NO_TRADE_MIN_NOTIONAL").fillna("NO_TRADE_MIN_NOTIONAL")
    return out.sort_values(["action", "leader_rank", "ticker"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Event-driven ledger backtest
# ---------------------------------------------------------------------------
@dataclass
class BacktestResult:
    metrics: dict[str, Any]
    daily_nav: pd.DataFrame
    holdings_daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame
    monthly_returns: pd.DataFrame
    yearly_returns: pd.DataFrame


def _price_lookup(price_panel: pd.DataFrame) -> pd.DataFrame:
    p = price_panel.copy()
    p["ticker"] = _normalise_ticker(p["ticker"])
    p["date"] = pd.to_datetime(p["date"]).dt.normalize()
    for col in ("open", "close", "high", "low"):
        if col in p.columns:
            p[col] = pd.to_numeric(p[col], errors="coerce")
    if "open" not in p.columns:
        p["open"] = p["close"]
    return p.sort_values(["date", "ticker"]).set_index(["date", "ticker"])


def _portfolio_value(positions: dict[str, dict[str, float]], cash: float, px: pd.DataFrame, day: pd.Timestamp) -> float:
    value = cash
    for tk, pos in positions.items():
        try:
            close = float(px.loc[(day, tk), "close"])
        except Exception:
            close = float(pos.get("last_price", pos.get("avg_cost", 0.0)) or 0.0)
        value += float(pos.get("shares", 0.0)) * close
    return float(value)


def _metrics_from_nav(nav: pd.DataFrame, benchmark_nav: Optional[pd.Series] = None) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    if nav.empty:
        return {}, pd.DataFrame(), pd.DataFrame()
    n = nav.sort_values("date").copy()
    n["ret"] = n["nav"].pct_change().fillna(0.0)
    n["peak"] = n["nav"].cummax()
    n["dd"] = n["nav"] / n["peak"] - 1.0
    years = max((n["date"].iloc[-1] - n["date"].iloc[0]).days / 365.25, 1e-9)
    cagr = (float(n["nav"].iloc[-1]) / float(n["nav"].iloc[0])) ** (1 / years) - 1.0
    mdd = float(n["dd"].min())
    vol = n["ret"].std()
    sharpe = float(n["ret"].mean() / vol * np.sqrt(252)) if vol and vol > 0 else 0.0
    monthly = n.set_index("date")["nav"].resample("ME").last().pct_change().fillna(0.0).reset_index(name="return")
    yearly = n.set_index("date")["nav"].resample("YE").last().pct_change().fillna(0.0).reset_index(name="return")
    metrics = {
        "start_date": str(n["date"].iloc[0].date()),
        "end_date": str(n["date"].iloc[-1].date()),
        "start_nav": float(n["nav"].iloc[0]),
        "final_nav": float(n["nav"].iloc[-1]),
        "cagr": float(cagr),
        "mdd": mdd,
        "sharpe": sharpe,
        "years": float(years),
        "monthly_win_rate": float((monthly["return"] > 0).mean()) if not monthly.empty else 0.0,
    }
    if benchmark_nav is not None and not benchmark_nav.empty:
        b = benchmark_nav.reindex(n["date"]).ffill().dropna()
        if len(b) >= 2:
            byears = max((b.index[-1] - b.index[0]).days / 365.25, 1e-9)
            bcagr = (float(b.iloc[-1]) / float(b.iloc[0])) ** (1 / byears) - 1.0
            metrics["benchmark_cagr"] = float(bcagr)
            metrics["excess_cagr"] = float(cagr - bcagr)
            active = n.set_index("date")["ret"].reindex(b.index).fillna(0.0) - b.pct_change().fillna(0.0)
            metrics["information_ratio"] = float(active.mean() / active.std() * np.sqrt(252)) if active.std() > 0 else 0.0
    return metrics, monthly, yearly


def run_event_driven_backtest(
    scored_panel: pd.DataFrame,
    price_panel: pd.DataFrame,
    cfg: Optional[dict[str, Any]] = None,
    initial_cash: float = 100_000_000.0,
    initial_holdings: Optional[pd.DataFrame] = None,
    benchmark_nav: Optional[pd.Series] = None,
) -> BacktestResult:
    """Run a daily ledger backtest from pre-scored candidate snapshots."""
    cfg = _cfg(cfg)
    if scored_panel.empty or price_panel.empty:
        empty = pd.DataFrame()
        return BacktestResult({"error": "empty scored_panel or price_panel"}, empty, empty, empty, empty, empty, empty)

    sp = scored_panel.copy()
    date_col = "rebalance_date" if "rebalance_date" in sp.columns else "date"
    sp[date_col] = pd.to_datetime(sp[date_col]).dt.normalize()
    sp["ticker"] = _normalise_ticker(sp["ticker"])
    if "leader_rank" not in sp.columns:
        sp = sp.groupby(date_col, group_keys=False).apply(lambda g: compute_leader_scores(g, cfg)).reset_index(drop=True)

    px = _price_lookup(price_panel)
    days = sorted(set(px.index.get_level_values(0)))
    signal_days = sorted(set(sp[date_col]))
    signal_set = set(signal_days)
    cash = float(initial_cash)
    positions: dict[str, dict[str, float]] = {}
    if initial_holdings is not None and not initial_holdings.empty:
        for _, r in _prepare_holdings_weights(initial_holdings).iterrows():
            if float(r.get("shares", 0.0) or 0.0) > 0:
                positions[str(r["ticker"])] = {
                    "shares": float(r["shares"]),
                    "avg_cost": float(r.get("avg_cost", r.get("last_price", 0.0)) or 0.0),
                    "entry_price": float(r.get("avg_cost", r.get("last_price", 0.0)) or 0.0),
                    "last_price": float(r.get("last_price", r.get("avg_cost", 0.0)) or 0.0),
                }

    pending_orders: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []
    holding_rows: list[dict[str, Any]] = []

    buy_cost = BROKERAGE_FEE_ONE_WAY + float(cfg.get("slippage_bp", 5.0)) / 10000.0
    sell_cost = TRANSACTION_TAX_SELL + BROKERAGE_FEE_ONE_WAY + float(cfg.get("slippage_bp", 5.0)) / 10000.0
    min_notional = float(cfg.get("min_notional_krw", 100000.0))
    execution_price = str(cfg.get("execution_price", "next_close"))
    metric_mode = str(cfg.get("metric_mode", "broker_ledger_next_close"))
    price_col = "open" if execution_price == "next_open" else "close"
    hard_stop_loss_pct = float(cfg.get("hard_stop_loss_pct", 0.15))
    base_gross_exposure = float(cfg.get("gross_exposure", cfg.get("gross_exposure_default", 1.0)))
    use_dd_ladder = bool(cfg.get("portfolio_drawdown_ladder_enabled", False))
    dd_ladder_thresholds = cfg.get("portfolio_drawdown_ladder_thresholds", (-0.08, -0.15, -0.25))
    dd_ladder_scales = cfg.get("portfolio_drawdown_ladder_scales", (0.85, 0.65, 0.40))
    peak_nav = float(initial_cash)
    exposure_scale_rows: list[dict[str, Any]] = []

    for i, day in enumerate(days):
        # Execute orders generated after the previous signal day.
        still_pending: list[dict[str, Any]] = []
        for od in pending_orders:
            tk = od["ticker"]
            try:
                row = px.loc[(day, tk)]
                fill_px = float(row.get(price_col, row.get("close")))
                if (not np.isfinite(fill_px)) or fill_px <= 0:
                    fill_px = float(row.get("close", np.nan))
                prev_close = positions.get(tk, {}).get("last_price")
            except Exception:
                od["status"] = "NO_FILL"
                od["reason_code"] = "NO_FILL_SUSPENDED"
                order_rows.append(od | {"execution_date": day})
                continue
            if (not np.isfinite(fill_px)) or fill_px <= 0:
                od["status"] = "NO_FILL"
                od["reason_code"] = "NO_FILL_SUSPENDED"
                order_rows.append(od | {"execution_date": day})
                continue
            if prev_close and cfg.get("apply_no_fill_rules", True):
                if abs(fill_px / float(prev_close) - 1.0) >= float(cfg.get("price_limit_pct", PRICE_LIMIT_PCT)):
                    od["status"] = "NO_FILL"
                    od["reason_code"] = "NO_FILL_LIMIT_PRICE"
                    order_rows.append(od | {"execution_date": day})
                    continue
            trade_value = abs(float(od.get("trade_value", 0.0)))
            if trade_value < min_notional:
                od["status"] = "NO_TRADE"
                od["reason_code"] = "NO_TRADE_MIN_NOTIONAL"
                order_rows.append(od | {"execution_date": day})
                continue
            if od["side"] == "BUY":
                qty = int(min(cash, trade_value) // (fill_px * (1 + buy_cost)))
                if qty <= 0:
                    od["status"] = "NO_TRADE"
                    od["reason_code"] = "NO_TRADE_MIN_NOTIONAL"
                    order_rows.append(od | {"execution_date": day})
                    continue
                cost = qty * fill_px * (1 + buy_cost)
                cash -= cost
                pos = positions.setdefault(tk, {"shares": 0.0, "avg_cost": 0.0, "entry_price": fill_px, "last_price": fill_px})
                old_value = pos["shares"] * pos["avg_cost"]
                pos["shares"] += qty
                pos["avg_cost"] = (old_value + qty * fill_px) / max(pos["shares"], 1)
                pos["last_price"] = fill_px
                gross_value = qty * fill_px
                fee_krw = gross_value * buy_cost
                trade_rows.append(od | {
                    "execution_date": day,
                    "fill_price": fill_px,
                    "qty": qty,
                    "gross_value": gross_value,
                    "fee_krw": fee_krw,
                    "cash_delta": -cost,
                    "cash_after": cash,
                    "fill_mode": execution_price,
                    "metric_mode": metric_mode,
                })
            else:
                pos = positions.get(tk)
                if not pos:
                    continue
                qty = int(min(pos["shares"], trade_value // fill_px if od["side"] == "SELL_PARTIAL" else pos["shares"]))
                if qty <= 0:
                    continue
                proceeds = qty * fill_px * (1 - sell_cost)
                gross_value = qty * fill_px
                fee_krw = gross_value * sell_cost
                cash += proceeds
                pos["shares"] -= qty
                pos["last_price"] = fill_px
                trade_rows.append(od | {
                    "execution_date": day,
                    "fill_price": fill_px,
                    "qty": qty,
                    "gross_value": gross_value,
                    "fee_krw": fee_krw,
                    "cash_delta": proceeds,
                    "cash_after": cash,
                    "fill_mode": execution_price,
                    "metric_mode": metric_mode,
                })
                if pos["shares"] <= 0:
                    del positions[tk]
            od["status"] = "FILLED"
            order_rows.append(od | {
                "execution_date": day,
                "fill_mode": execution_price,
                "metric_mode": metric_mode,
            })
        pending_orders = still_pending

        nav = _portfolio_value(positions, cash, px, day)
        peak_nav = max(peak_nav, nav)
        portfolio_dd = nav / peak_nav - 1.0 if peak_nav > 0 else 0.0
        nav_rows.append({
            "date": day,
            "nav": nav,
            "cash": cash,
            "n_holdings": len(positions),
            "portfolio_drawdown": portfolio_dd,
            "peak_nav": peak_nav,
        })
        for tk, pos in positions.items():
            try:
                last_px = float(px.loc[(day, tk), "close"])
            except Exception:
                last_px = float(pos.get("last_price", 0.0))
            pos["last_price"] = last_px
            holding_rows.append({
                "date": day,
                "ticker": tk,
                "shares": pos["shares"],
                "last_price": last_px,
                "market_value": pos["shares"] * last_px,
                "weight": (pos["shares"] * last_px / nav) if nav > 0 else 0.0,
            })

        if cfg.get("daily_hard_exit_enabled", True) and i < len(days) - 1:
            pending_sell_tickers = {
                od["ticker"] for od in pending_orders
                if od.get("side") in ("SELL", "SELL_PARTIAL")
            }
            for tk, pos in list(positions.items()):
                if tk in pending_sell_tickers:
                    continue
                basis = float(pos.get("entry_price", pos.get("avg_cost", 0.0)) or 0.0)
                last_px = float(pos.get("last_price", 0.0) or 0.0)
                if basis <= 0 or last_px <= 0:
                    continue
                pnl_pct = last_px / basis - 1.0
                if pnl_pct <= -hard_stop_loss_pct:
                    pending_orders.append({
                        "signal_date": day,
                        "ticker": tk,
                        "side": "SELL",
                        "trade_value": float(pos.get("shares", 0.0)) * last_px,
                        "reason_code": "SELL_HARD_STOP_DAILY",
                        "broker_rule": "signal_after_close_fill_next_close",
                    })

        if i >= len(days) - 1 or day not in signal_set:
            continue

        todays = sp[sp[date_col] == day].copy()
        if todays.empty:
            continue
        rebalance_cfg = dict(cfg)
        gross_scale = 1.0
        if use_dd_ladder:
            gross_scale = portfolio_drawdown_exposure_scale(
                portfolio_dd,
                dd_ladder_thresholds,
                dd_ladder_scales,
            )
            rebalance_cfg["gross_exposure"] = base_gross_exposure * gross_scale
            exposure_scale_rows.append({
                "date": day,
                "portfolio_drawdown": portfolio_dd,
                "gross_exposure_scale": gross_scale,
                "gross_exposure_effective": rebalance_cfg["gross_exposure"],
            })
        target = build_target_portfolio(todays, rebalance_cfg, as_of_date=day)
        cur_rows = []
        for tk, pos in positions.items():
            cur_rows.append({
                "ticker": tk,
                "shares": pos["shares"],
                "avg_cost": pos["avg_cost"],
                "last_price": pos["last_price"],
                "market_value": pos["shares"] * pos["last_price"],
            })
        cur_df = _prepare_holdings_weights(pd.DataFrame(cur_rows))
        plan = generate_trade_plan(cur_df, target, todays, rebalance_cfg, as_of_date=day, account_nav=nav)
        for _, r in plan.iterrows():
            pending_sell_tickers = {
                od["ticker"] for od in pending_orders
                if od.get("side") in ("SELL", "SELL_PARTIAL")
            }
            est_value = pd.to_numeric(r.get("estimated_trade_krw"), errors="coerce")
            delta_w = pd.to_numeric(r.get("delta_weight"), errors="coerce")
            target_w = pd.to_numeric(r.get("target_weight"), errors="coerce")
            if not np.isfinite(delta_w):
                delta_w = 0.0
            if not np.isfinite(target_w):
                target_w = 0.0
            if r["action"] in ("BUY", "ADD"):
                fallback_value = nav * max(float(delta_w), float(target_w), 0.0)
                trade_value = float(est_value) if np.isfinite(est_value) and float(est_value) > 0 else fallback_value
                pending_orders.append({
                    "signal_date": day,
                    "ticker": r["ticker"],
                    "side": "BUY",
                    "trade_value": trade_value,
                    "reason_code": r["reason_code"],
                })
            elif r["action"] == "TRIM":
                fallback_value = nav * abs(min(float(delta_w), 0.0))
                trade_value = abs(float(est_value)) if np.isfinite(est_value) and float(est_value) != 0 else fallback_value
                pending_orders.append({
                    "signal_date": day,
                    "ticker": r["ticker"],
                    "side": "SELL_PARTIAL",
                    "trade_value": trade_value,
                    "reason_code": r["reason_code"],
                })
            elif r["action"] == "SELL":
                if r["ticker"] in pending_sell_tickers:
                    continue
                mv = float(cur_df.loc[cur_df["ticker"] == r["ticker"], "market_value"].sum()) if not cur_df.empty else 0.0
                pending_orders.append({
                    "signal_date": day,
                    "ticker": r["ticker"],
                    "side": "SELL",
                    "trade_value": mv,
                    "reason_code": r["reason_code"],
                })

    daily_nav = pd.DataFrame(nav_rows)
    holdings_daily = pd.DataFrame(holding_rows)
    orders = pd.DataFrame(order_rows)
    trades = pd.DataFrame(trade_rows)
    metrics, monthly, yearly = _metrics_from_nav(daily_nav, benchmark_nav=benchmark_nav)
    metrics["status"] = "completed"
    metrics["metric_mode"] = metric_mode if execution_price == "next_close" else "broker_ledger"
    metrics["fill_mode"] = execution_price
    metrics["signal_timing"] = str(cfg.get("signal_timing", "after_close"))
    metrics["execution_timing"] = str(cfg.get("execution_timing", "next_trading_day_close"))
    metrics["integer_shares"] = bool(cfg.get("integer_shares", True))
    metrics["no_negative_cash"] = bool(cfg.get("no_negative_cash", True))
    metrics["no_leverage"] = bool(cfg.get("no_leverage", True))
    metrics["valid_for_production_metric"] = bool(execution_price == "next_close")
    metrics["initial_cash_krw"] = float(initial_cash)
    metrics["ending_cash_krw"] = float(daily_nav["cash"].iloc[-1]) if not daily_nav.empty and "cash" in daily_nav.columns else float(cash)
    metrics["avg_cash_weight"] = float((daily_nav["cash"] / daily_nav["nav"]).replace([np.inf, -np.inf], np.nan).mean()) if not daily_nav.empty else 0.0
    metrics["portfolio_drawdown_ladder_enabled"] = bool(use_dd_ladder)
    if exposure_scale_rows:
        scales_df = pd.DataFrame(exposure_scale_rows)
        metrics["avg_gross_exposure_effective"] = float(pd.to_numeric(scales_df["gross_exposure_effective"], errors="coerce").mean())
        metrics["min_gross_exposure_effective"] = float(pd.to_numeric(scales_df["gross_exposure_effective"], errors="coerce").min())
        metrics["avg_portfolio_drawdown_at_rebalance"] = float(pd.to_numeric(scales_df["portfolio_drawdown"], errors="coerce").mean())
    else:
        metrics["avg_gross_exposure_effective"] = float(base_gross_exposure)
        metrics["min_gross_exposure_effective"] = float(base_gross_exposure)
    metrics["total_fees_krw"] = float(pd.to_numeric(trades.get("fee_krw", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if not trades.empty else 0.0
    metrics["n_orders"] = int(len(orders))
    metrics["n_trades"] = int(len(trades))
    metrics["turnover_proxy"] = float(trades["trade_value"].abs().sum() / daily_nav["nav"].mean()) if not trades.empty and "trade_value" in trades.columns else 0.0
    return BacktestResult(metrics, daily_nav, holdings_daily, orders, trades, monthly, yearly)


def write_leader_outputs(
    candidates: pd.DataFrame,
    target_portfolio: pd.DataFrame,
    trade_plan: pd.DataFrame,
    backtest: Optional[BacktestResult] = None,
    output_dir: str | Path | None = None,
) -> dict[str, Path]:
    """Persist standard KR1000 leader outputs."""
    out_dir = Path(output_dir) if output_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "candidates": out_dir / "leader_candidates_latest.csv",
        "portfolio": out_dir / "leader_portfolio_latest.csv",
        "trade_plan": out_dir / "leader_trade_plan_latest.csv",
    }
    candidates.to_csv(paths["candidates"], index=False, encoding="utf-8-sig")
    target_portfolio.to_csv(paths["portfolio"], index=False, encoding="utf-8-sig")
    trade_plan.to_csv(paths["trade_plan"], index=False, encoding="utf-8-sig")
    if backtest is not None:
        import json

        paths.update({
            "metrics": out_dir / "leader_backtest_metrics.json",
            "daily_nav": out_dir / "leader_backtest_daily_nav.csv",
            "monthly": out_dir / "leader_backtest_monthly_returns.csv",
            "yearly": out_dir / "leader_backtest_yearly_returns.csv",
            "orders": out_dir / "leader_backtest_orders.csv",
            "trades": out_dir / "leader_backtest_trades.csv",
            "holdings_daily": out_dir / "leader_backtest_holdings_daily.csv",
            "positions_latest": out_dir / "leader_positions_latest.csv",
            "account_state": out_dir / "leader_account_state_latest.json",
        })
        paths["metrics"].write_text(json.dumps(backtest.metrics, indent=2, default=str), encoding="utf-8")
        backtest.daily_nav.to_csv(paths["daily_nav"], index=False, encoding="utf-8-sig")
        backtest.monthly_returns.to_csv(paths["monthly"], index=False, encoding="utf-8-sig")
        backtest.yearly_returns.to_csv(paths["yearly"], index=False, encoding="utf-8-sig")
        backtest.orders.to_csv(paths["orders"], index=False, encoding="utf-8-sig")
        backtest.trades.to_csv(paths["trades"], index=False, encoding="utf-8-sig")
        backtest.holdings_daily.to_csv(paths["holdings_daily"], index=False, encoding="utf-8-sig")
        if not backtest.holdings_daily.empty and "date" in backtest.holdings_daily.columns:
            latest_date = pd.to_datetime(backtest.holdings_daily["date"], errors="coerce").max()
            latest_positions = backtest.holdings_daily[
                pd.to_datetime(backtest.holdings_daily["date"], errors="coerce") == latest_date
            ].copy()
        else:
            latest_date = pd.NaT
            latest_positions = pd.DataFrame()
        latest_positions.to_csv(paths["positions_latest"], index=False, encoding="utf-8-sig")
        if not backtest.daily_nav.empty:
            nav_last = backtest.daily_nav.sort_values("date").iloc[-1].to_dict()
        else:
            nav_last = {}
        account_state = {
            "schema_version": "kr1000-account-ledger-v1",
            "metric_mode": backtest.metrics.get("metric_mode", "broker_ledger_next_close"),
            "fill_mode": backtest.metrics.get("fill_mode", "next_close"),
            "as_of_date": str(pd.Timestamp(latest_date).date()) if pd.notna(latest_date) else nav_last.get("date", ""),
            "nav_krw": nav_last.get("nav"),
            "cash_krw": nav_last.get("cash"),
            "cash_weight": (
                float(nav_last.get("cash", 0.0) or 0.0) / float(nav_last.get("nav", 1.0) or 1.0)
                if float(nav_last.get("nav", 0.0) or 0.0) > 0 else None
            ),
            "position_count": int(len(latest_positions)),
            "metrics": backtest.metrics,
        }
        paths["account_state"].write_text(
            json.dumps(account_state, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    log(f"[kr1000] wrote leader outputs to {out_dir}")
    return paths
