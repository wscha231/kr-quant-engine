"""kr_pipeline — orchestration: universe → features → score → backtest.

P0 entry points:
- run_p0_baseline(cfg) — full universe build + features + Top-N backtest
- backtest_topn_momentum_v0(scored_panel, cfg) — month rebal + 22bp cost
- run_full_validation_suite(cfg) — PIT, NaN cliff, member count checks

Mirrors r1000_pipeline.run_default_pipeline structure but P0 minimal.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from kr_config import (
    DEFAULT_CFG,
    DEFAULT_ROUND_TRIP_COST,
    KR_ENGINE_REUSE_VERSION,
    DATA_ROOT,
    PHASE4_PMB_TARGET_COLUMNS,
)
from kr_features import (
    add_universe_features,
    compute_p0_score,
    prepare_pit_fundamentals_panel,
)
from kr_helpers import log, phase_is_enabled
from kr_pykrx_client import (
    fetch_index_ohlcv,
    fetch_month_end_business_days,
    fetch_ticker_history,
)
from kr_universe import build_universe_snapshot


# ---------------------------------------------------------------------------
# Forward target labels for P_MB risk sleeve
# ---------------------------------------------------------------------------
def _last_close_at_or_before_series(close: pd.Series, day: pd.Timestamp) -> float:
    sub = close.loc[close.index <= pd.Timestamp(day).normalize()]
    if sub.empty:
        return float("nan")
    return float(sub.iloc[-1])


def add_forward_return_labels(
    scored_panel: pd.DataFrame,
    cfg: Optional[dict] = None,
    price_panel: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Add forward return / forward drawdown labels for classifier targets.

    These columns are target labels for walk-forward classifier training, not
    tradable features. Downstream feature selection excludes the `forward_`
    prefix to avoid leakage.
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    out = scored_panel.copy()
    for col in PHASE4_PMB_TARGET_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    if out.empty or not {"rebalance_date", "ticker"}.issubset(out.columns):
        return out

    horizon_months = int(cfg.get("forward_label_horizon_months", 1) or 1)
    refresh_days = int(cfg.get("forward_label_refresh_days", cfg.get("avg_value_refresh_days", 3650)) or 3650)
    fetch_missing_prices = bool(cfg.get("forward_label_fetch_missing_prices", True))
    label_as_of_raw = cfg.get("forward_label_as_of_date")
    label_as_of = (
        pd.Timestamp(label_as_of_raw).normalize()
        if label_as_of_raw
        else pd.Timestamp.today().normalize()
    )
    out["rebalance_date"] = pd.to_datetime(out["rebalance_date"], errors="coerce").dt.normalize()
    out["ticker"] = out["ticker"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    missing = out[list(PHASE4_PMB_TARGET_COLUMNS)].isna().any(axis=1)
    work = out.loc[missing & out["rebalance_date"].notna(), ["rebalance_date", "ticker"]]
    if work.empty:
        return out

    prices_by_ticker: dict[str, pd.DataFrame] = {}
    if price_panel is not None and not price_panel.empty:
        pp = price_panel.copy()
        pp["ticker"] = pp["ticker"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
        pp["date"] = pd.to_datetime(pp["date"], errors="coerce").dt.normalize()
        if "close" in pp.columns:
            pp["close"] = pd.to_numeric(pp["close"], errors="coerce")
            for tk, group in pp.dropna(subset=["date", "close"]).groupby("ticker"):
                prices_by_ticker[str(tk)] = group.sort_values("date")

    min_date = pd.Timestamp(work["rebalance_date"].min()).normalize()
    max_date = pd.Timestamp(work["rebalance_date"].max()).normalize()
    fetch_start = (min_date - pd.Timedelta(days=10)).strftime("%Y%m%d")
    fetch_end = (max_date + pd.DateOffset(months=horizon_months) + pd.Timedelta(days=10)).strftime("%Y%m%d")

    filled = 0
    groups = work.groupby("ticker").groups
    total_tickers = len(groups)
    for i, (tk, idxs) in enumerate(groups.items(), start=1):
        if i == 1 or i % 100 == 0 or i == total_tickers:
            log(f"[pipeline] forward labels ticker {i}/{total_tickers}")
        hist = prices_by_ticker.get(str(tk))
        if hist is None:
            if not fetch_missing_prices:
                continue
            hist = fetch_ticker_history(str(tk), fetch_start, fetch_end, refresh_days=refresh_days)
            if hist is None or hist.empty:
                continue
            hist = hist.copy()
            if "close" not in hist.columns:
                continue
            hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.normalize()
            hist["close"] = pd.to_numeric(hist["close"], errors="coerce")
            hist = hist.dropna(subset=["date", "close"]).sort_values("date")
        if hist.empty:
            continue
        close = hist.drop_duplicates("date", keep="last").set_index("date")["close"].sort_index()
        for idx in idxs:
            rd = pd.Timestamp(out.at[idx, "rebalance_date"]).normalize()
            entry_close = _last_close_at_or_before_series(close, rd)
            if not np.isfinite(entry_close) or entry_close <= 0:
                continue
            horizon_end = rd + pd.DateOffset(months=horizon_months)
            if horizon_end > label_as_of:
                continue
            future = close.loc[(close.index > rd) & (close.index <= horizon_end)]
            if future.empty:
                continue
            out.at[idx, "forward_return_1m"] = float(future.iloc[-1] / entry_close - 1.0)
            out.at[idx, "forward_min_return_1m"] = float(future.min() / entry_close - 1.0)
            filled += 1
    log(f"[pipeline] forward labels filled {filled}/{len(work)} missing rows")
    return out


# ---------------------------------------------------------------------------
# Scored panel builder
# ---------------------------------------------------------------------------
def build_scored_panel_v0(
    start_date: str = "2016-01-01",
    end_date: Optional[str] = None,
    cfg: Optional[dict] = None,
) -> pd.DataFrame:
    """For each month-end, build universe snapshot + add features + score.

    Returns concatenated long-format DataFrame.
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    end_date = end_date or datetime.now().strftime("%Y-%m-%d")

    cache_path = scored_panel_cache_path(start_date, end_date)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg.get("reuse_existing_artifacts", True) and cache_path.exists():
        log(f"[pipeline] reuse cached scored panel {cache_path.name}")
        return pd.read_parquet(cache_path)

    month_ends = fetch_month_end_business_days(
        pd.Timestamp(start_date).strftime("%Y%m%d"),
        pd.Timestamp(end_date).strftime("%Y%m%d"),
    )
    prior_panel = pd.DataFrame()
    prior_max_date: Optional[pd.Timestamp] = None
    prior_dates: set[pd.Timestamp] = set()
    if cfg.get("reuse_existing_artifacts", True) and cfg.get("scored_panel_incremental_rebuild", True):
        prior_path = find_incremental_scored_panel_cache(
            start_date,
            end_date,
            allow_prior_engine_versions=bool(cfg.get("scored_panel_allow_prior_engine_reuse", False)),
        )
        if prior_path is not None:
            try:
                prior_panel = pd.read_parquet(prior_path)
                if "rebalance_date" in prior_panel.columns and not prior_panel.empty:
                    prior_panel["rebalance_date"] = pd.to_datetime(
                        prior_panel["rebalance_date"], errors="coerce"
                    ).dt.normalize()
                    target_start = pd.Timestamp(start_date).normalize()
                    target_end = pd.Timestamp(end_date).normalize()
                    prior_panel = prior_panel[
                        (prior_panel["rebalance_date"] >= target_start)
                        & (prior_panel["rebalance_date"] <= target_end)
                    ].copy()
                    prior_max_date = prior_panel["rebalance_date"].max()
                    prior_dates = {
                        pd.Timestamp(x).normalize()
                        for x in prior_panel["rebalance_date"].dropna().unique()
                    }
                    log(
                        f"[pipeline] incremental scored panel base {prior_path.name} "
                        f"covering {len(prior_dates)} month-ends through {prior_max_date.strftime('%Y-%m-%d')}"
                    )
            except Exception as e:
                prior_panel = pd.DataFrame()
                prior_max_date = None
                prior_dates = set()
                log(f"[pipeline] incremental scored panel read fail {prior_path.name}: {e}", level="WARN")

    if prior_dates:
        build_month_ends = [
            pd.Timestamp(me).normalize()
            for me in month_ends
            if pd.Timestamp(me).normalize() not in prior_dates
        ]
    else:
        build_month_ends = [pd.Timestamp(me).normalize() for me in month_ends]
    log(
        f"[pipeline] build scored panel: {len(build_month_ends)}/{len(month_ends)} "
        f"month-ends to compute"
    )
    max_new_months = int(cfg.get("scored_panel_incremental_max_new_months", 0) or 0)
    if prior_max_date is not None and max_new_months > 0 and len(build_month_ends) > max_new_months:
        original_count = len(build_month_ends)
        build_month_ends = build_month_ends[-max_new_months:]
        log(
            f"[pipeline] incremental max_new_months={max_new_months}: "
            f"compute latest {len(build_month_ends)} of {original_count} missing month-ends"
        )

    if not build_month_ends and not prior_panel.empty:
        try:
            prior_panel.to_parquet(cache_path, index=False)
            log(f"[pipeline] materialized scored panel cache -> {cache_path}")
        except Exception as e:
            log(f"[pipeline] panel save fail: {e}", level="WARN")
        return prior_panel

    # ----- P1: pre-build full fundamentals panel once (DART bulk fetch) -----
    fund_panel = pd.DataFrame()
    if phase_is_enabled("phase1_fundamental", default=True):
        # Sample tickers from a recent month to drive the corp_code fetch
        # (faster than per-month re-discovery)
        log("[pipeline] phase1 enabled -> pre-build DART fundamentals panel")
        sample_snap = build_universe_snapshot(build_month_ends[-1], cfg=cfg)
        if not sample_snap.empty:
            sample_tickers = sample_snap[sample_snap["eligible"]]["ticker"].astype(str).tolist()
            fund_start = int(cfg.get("dart_fund_start_year", 2014))
            fund_end = pd.Timestamp(end_date).year
            fund_cache = DATA_ROOT / "feature_store" / (
                f"fund_panel_{fund_start}_{fund_end}_"
                f"{KR_ENGINE_REUSE_VERSION}.parquet"
            )
            if cfg.get("reuse_existing_artifacts", True) and fund_cache.exists():
                fund_panel = pd.read_parquet(fund_cache)
                log(f"[pipeline] reuse cached fund_panel: {len(fund_panel)} rows")
            else:
                fund_panel = prepare_pit_fundamentals_panel(
                    sample_tickers,
                    start_year=fund_start,
                    end_year=fund_end,
                )
                if not fund_panel.empty:
                    try:
                        fund_panel.to_parquet(fund_cache, index=False)
                        log(f"[pipeline] saved fund_panel ({len(fund_panel)} rows)")
                    except Exception as e:
                        log(f"[pipeline] fund_panel save fail: {e}", level="WARN")

    frames = []
    for i, me in enumerate(build_month_ends, 1):
        log(f"[pipeline] [{i}/{len(build_month_ends)}] {me.strftime('%Y-%m-%d')}")
        snap = build_universe_snapshot(me, cfg=cfg)
        if snap.empty:
            continue
        # Only featurize eligible rows (cost saving)
        eligible = snap[snap["eligible"]].copy()
        if eligible.empty:
            continue
        feat = add_universe_features(eligible, me, fund_panel=fund_panel)
        frames.append(feat)

    if not frames and prior_panel.empty:
        return pd.DataFrame()
    all_frames = []
    if not prior_panel.empty:
        all_frames.append(prior_panel)
    all_frames.extend(frames)
    panel = pd.concat(all_frames, ignore_index=True)
    if {"rebalance_date", "ticker"}.issubset(panel.columns):
        panel["rebalance_date"] = pd.to_datetime(panel["rebalance_date"], errors="coerce").dt.normalize()
        panel["ticker"] = panel["ticker"].astype(str).str.zfill(6)
        panel = panel.drop_duplicates(["rebalance_date", "ticker"], keep="last")
        panel = panel.sort_values(["rebalance_date", "ticker"]).reset_index(drop=True)
    if cfg.get("forward_label_enabled", True):
        panel = add_forward_return_labels(panel, cfg=cfg)
    try:
        panel.to_parquet(cache_path, index=False)
        log(f"[pipeline] saved scored panel ({len(panel)} rows) -> {cache_path}")
    except Exception as e:
        log(f"[pipeline] panel save fail: {e}", level="WARN")
    return panel


def scored_panel_cache_path(start_date: str, end_date: str) -> Path:
    """Canonical scored_panel_v0 cache path for a date window."""
    return DATA_ROOT / "feature_store" / (
        f"scored_panel_v0_{start_date}_{end_date}_{KR_ENGINE_REUSE_VERSION}.parquet"
    )


def find_incremental_scored_panel_cache(
    start_date: str,
    end_date: str,
    feature_store: Optional[Path] = None,
    allow_prior_engine_versions: bool = False,
) -> Optional[Path]:
    """Find the best prior scored_panel cache that overlaps the target window.

    Only panels whose filename end date is on or before the target end date are
    eligible. This prevents a future-dated panel from leaking into a historical
    rebuild while still allowing a weekly job to append only new month-ends.
    The cache may start after the requested start date: the builder computes
    missing leading months and reuses overlapping later rows.
    """
    root = Path(feature_store) if feature_store is not None else DATA_ROOT / "feature_store"
    if not root.exists():
        return None
    target_end = pd.Timestamp(end_date).normalize()
    target_start = pd.Timestamp(start_date).normalize()
    pattern = "scored_panel_v0_*.parquet"
    candidates: list[tuple[int, int, pd.Timestamp, float, Path]] = []
    for path in root.glob(pattern):
        name = path.name
        prefix = "scored_panel_v0_"
        suffix = ".parquet"
        if not name.startswith(prefix) or not name.endswith(suffix):
            continue
        tail = name[len(prefix):-len(suffix)]
        parts = tail.split("_", 2)
        if len(parts) != 3:
            continue
        raw_start, raw_end, engine_version = parts
        engine_match = engine_version == KR_ENGINE_REUSE_VERSION
        if not engine_match and not allow_prior_engine_versions:
            continue
        try:
            panel_start = pd.Timestamp(raw_start).normalize()
            panel_end = pd.Timestamp(raw_end).normalize()
        except Exception:
            continue
        if panel_end > target_end:
            continue
        overlap_start = max(panel_start, target_start)
        overlap_end = min(panel_end, target_end)
        if overlap_end < overlap_start:
            continue
        overlap_months = len(pd.period_range(overlap_start.to_period("M"), overlap_end.to_period("M"), freq="M"))
        starts_at_or_before_target = 1 if panel_start <= target_start else 0
        candidates.append((
            overlap_months,
            starts_at_or_before_target,
            panel_end,
            path.stat().st_mtime,
            path,
        ))
    if not candidates:
        return None
    return max(candidates, key=lambda x: (x[0], x[1], x[2], x[3]))[4]


# ---------------------------------------------------------------------------
# Top-N selection per month
# ---------------------------------------------------------------------------
def select_topn_per_month(scored_panel: pd.DataFrame, n: int = 30,
                          score_col: Optional[str] = None) -> pd.DataFrame:
    """For each rebalance_date, select Top-N by score.

    score_col auto-detect: prefer p1_blended_score if available (P1 enabled),
    else fall back to p0_momentum_score.

    Returns rows with rank assigned (1..n).
    """
    if scored_panel.empty:
        return pd.DataFrame()
    if score_col is None:
        if "p1_blended_score" in scored_panel.columns and \
                scored_panel["p1_blended_score"].abs().sum() > 0:
            score_col = "p1_blended_score"
        else:
            score_col = "p0_momentum_score"
    if score_col not in scored_panel.columns:
        return pd.DataFrame()
    log(f"[pipeline] select_topn_per_month using score='{score_col}'")
    out = []
    for rd, group in scored_panel.groupby("rebalance_date"):
        g = group.sort_values(score_col, ascending=False).head(n).copy()
        g["rank"] = np.arange(1, len(g) + 1)
        g["score_col_used"] = score_col
        out.append(g)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ---------------------------------------------------------------------------
# Backtest engine (월 rebal + cost-aware)
# ---------------------------------------------------------------------------
def _ticker_month_return(
    ticker: str, period_start: pd.Timestamp, period_end: pd.Timestamp
) -> float:
    """Return for ticker over [period_start, period_end] using close prices."""
    start = (period_start - timedelta(days=10)).strftime("%Y%m%d")
    end = (period_end + timedelta(days=10)).strftime("%Y%m%d")
    df = fetch_ticker_history(ticker, start, end, refresh_days=30)
    if df.empty:
        return 0.0
    df = df.sort_values("date")
    p_start = df[df["date"] <= period_start]["close"]
    p_end = df[df["date"] <= period_end]["close"]
    if p_start.empty or p_end.empty:
        return 0.0
    s = float(p_start.iloc[-1])
    e = float(p_end.iloc[-1])
    if s <= 0:
        return 0.0
    return e / s - 1.0


def backtest_topn_momentum_v0(
    selected: pd.DataFrame,
    cfg: Optional[dict] = None,
) -> dict:
    """Month-rebalance Top-N backtest with cost.

    cfg keys used: round_trip_cost, weighting_mode, single_stock_max_weight.

    Returns metrics dict + monthly returns DataFrame.
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    if selected.empty:
        return {"error": "empty selected"}

    cost = float(cfg.get("round_trip_cost", DEFAULT_ROUND_TRIP_COST))
    weighting = cfg.get("weighting_mode", "equal")

    # Sort by month
    rds = sorted(selected["rebalance_date"].unique())
    monthly_rows = []
    prev_holdings: dict[str, float] = {}    # ticker -> weight

    for i in range(len(rds) - 1):
        rd = pd.Timestamp(rds[i])
        next_rd = pd.Timestamp(rds[i + 1])
        sel = selected[selected["rebalance_date"] == rd].copy()

        # Determine weights
        if weighting == "equal":
            weights = pd.Series(1.0 / len(sel), index=sel.index)
        elif weighting == "score":
            score_col = sel["score_col_used"].iloc[0] if "score_col_used" in sel.columns \
                else "p0_momentum_score"
            s = sel[score_col].clip(lower=0) if score_col in sel.columns else pd.Series(0.0, index=sel.index)
            if s.sum() <= 0:
                weights = pd.Series(1.0 / len(sel), index=sel.index)
            else:
                weights = s / s.sum()
        else:  # default fallback equal
            weights = pd.Series(1.0 / len(sel), index=sel.index)

        # Per-name cap
        cap = float(cfg.get("single_stock_max_weight", 0.14))
        weights = weights.clip(upper=cap)
        weights = weights / weights.sum() if weights.sum() > 0 else weights

        # Compute holding returns over [rd, next_rd]
        ret_per_name = {}
        for idx, row in sel.iterrows():
            tk = row["ticker"]
            r = _ticker_month_return(tk, rd, next_rd)
            ret_per_name[tk] = r
        sel["period_return"] = sel["ticker"].map(ret_per_name).fillna(0.0)
        sel["weight"] = weights.values

        gross_ret = float((sel["weight"] * sel["period_return"]).sum())

        # Turnover & cost
        new_holdings = dict(zip(sel["ticker"], sel["weight"]))
        all_t = set(prev_holdings) | set(new_holdings)
        turnover = sum(abs(new_holdings.get(t, 0.0) - prev_holdings.get(t, 0.0)) for t in all_t) / 2.0
        period_cost = turnover * cost
        net_ret = gross_ret - period_cost

        monthly_rows.append({
            "rebalance_date": rd,
            "next_rebalance_date": next_rd,
            "n_holdings": len(sel),
            "gross_return": gross_ret,
            "turnover": turnover,
            "cost": period_cost,
            "net_return": net_ret,
        })
        prev_holdings = new_holdings

    monthly = pd.DataFrame(monthly_rows)
    if monthly.empty:
        return {"error": "no months"}

    # Compute cumulative + summary metrics
    monthly["cum_return"] = (1.0 + monthly["net_return"]).cumprod()
    n_months = len(monthly)
    n_years = n_months / 12.0
    final_cum = float(monthly["cum_return"].iloc[-1])
    cagr = final_cum ** (1.0 / max(n_years, 1e-9)) - 1.0 if final_cum > 0 else -1.0
    monthly_std = float(monthly["net_return"].std())
    sharpe = (float(monthly["net_return"].mean()) / monthly_std * np.sqrt(12)
              if monthly_std > 1e-12 else 0.0)

    cum = monthly["cum_return"].values
    running_max = np.maximum.accumulate(cum)
    drawdown = (cum - running_max) / running_max
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    avg_turnover = float(monthly["turnover"].mean())
    total_cost = float(monthly["cost"].sum())

    # Benchmark (KOSPI200) over same window
    start_date = monthly["rebalance_date"].min()
    end_date = monthly["next_rebalance_date"].max()
    bench = fetch_index_ohlcv("1028", start_date.strftime("%Y%m%d"),
                               end_date.strftime("%Y%m%d"), refresh_days=30)
    bench_cagr = 0.0
    if not bench.empty:
        b = bench.sort_values("date")
        b_start = float(b.iloc[0]["close"])
        b_end = float(b.iloc[-1]["close"])
        bench_cagr = (b_end / b_start) ** (1.0 / max(n_years, 1e-9)) - 1.0 if b_start > 0 else 0.0

    excess_cagr = cagr - bench_cagr

    metrics = {
        "engine_version": KR_ENGINE_REUSE_VERSION,
        "rebalance_count": n_months,
        "years_simulated": round(n_years, 2),
        "n_holdings_avg": float(monthly["n_holdings"].mean()),
        "weighting_mode": weighting,
        "round_trip_cost": cost,
        "strategy_cagr": cagr,
        "benchmark_cagr": bench_cagr,
        "excess_cagr": excess_cagr,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "avg_turnover": avg_turnover,
        "total_cost_drag": total_cost,
        "final_cum_return": final_cum,
        "beat_month_ratio": float((monthly["net_return"] > 0).mean()),
    }
    return {"metrics": metrics, "monthly_returns": monthly}


# ---------------------------------------------------------------------------
# Validation suite
# ---------------------------------------------------------------------------
def run_full_validation_suite(scored_panel: pd.DataFrame, cfg: Optional[dict] = None) -> dict:
    """PIT, NaN cliff, member count, listing change checks."""
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    checks = {}
    if scored_panel.empty:
        return {"error": "empty panel"}

    # Member count per month
    counts = scored_panel.groupby("rebalance_date").size()
    checks["min_member_count"] = int(counts.min())
    checks["max_member_count"] = int(counts.max())
    checks["mean_member_count"] = float(counts.mean())
    checks["min_member_count_pass"] = checks["min_member_count"] >= 200

    # NaN rate per signal column
    sig_cols = [c for c in ("ret_12_1m", "ret_6m", "rs_kospi_12m", "p0_momentum_score")
                if c in scored_panel.columns]
    nan_rates = {c: float(scored_panel[c].isna().mean()) for c in sig_cols}
    checks["nan_rates_per_column"] = nan_rates
    checks["max_nan_rate_pass"] = all(r < 0.30 for r in nan_rates.values())

    # Member change per month
    if len(counts) >= 2:
        member_sets = scored_panel.groupby("rebalance_date")["ticker"].apply(set)
        changes = []
        prev = None
        for rd, s in member_sets.items():
            if prev is not None:
                jaccard = len(s & prev) / max(1, len(s | prev))
                changes.append(1 - jaccard)
            prev = s
        checks["max_member_change_pct"] = float(max(changes)) if changes else 0.0
        checks["mean_member_change_pct"] = float(sum(changes) / len(changes)) if changes else 0.0
        checks["member_stability_pass"] = checks["max_member_change_pct"] < 0.50

    checks["all_pass"] = all(v is True for k, v in checks.items() if k.endswith("_pass"))
    return checks


# ---------------------------------------------------------------------------
# P0 entry point
# ---------------------------------------------------------------------------
def run_p0_baseline(cfg: Optional[dict] = None) -> dict:
    """Run end-to-end P0: universe → features → Top-N → backtest → save metrics."""
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    start_date = cfg.get("start_date", "2016-01-01")
    end_date = cfg.get("end_date") or datetime.now().strftime("%Y-%m-%d")
    n = int(cfg.get("portfolio_size", 30))

    log(f"[pipeline] === P0 baseline run ===")
    log(f"[pipeline] window {start_date} -> {end_date}, Top-{n}, cost={cfg.get('round_trip_cost'):.4f}")

    t0 = time.time()
    panel = build_scored_panel_v0(start_date, end_date, cfg=cfg)
    if panel.empty:
        log("[pipeline] FATAL: scored panel is empty")
        return {"error": "empty scored panel"}
    log(f"[pipeline] scored panel: {len(panel)} rows in {time.time()-t0:.1f}s")

    t1 = time.time()
    selected = select_topn_per_month(panel, n=n)
    log(f"[pipeline] selected: {len(selected)} rows ({selected['rebalance_date'].nunique()} months)")

    t2 = time.time()
    bt = backtest_topn_momentum_v0(selected, cfg=cfg)
    log(f"[pipeline] backtest done in {time.time()-t2:.1f}s")

    val = run_full_validation_suite(panel, cfg=cfg)

    # Save outputs
    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / "p0_baseline_metrics.json"
    monthly_path = out_dir / "p0_monthly_returns.csv"
    selected_path = out_dir / "p0_selected_topn.csv"
    val_path = out_dir / "p0_validation.json"

    if "metrics" in bt:
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(bt["metrics"], f, indent=2, ensure_ascii=False, default=str)
        bt["monthly_returns"].to_csv(monthly_path, index=False)
    selected.to_csv(selected_path, index=False)
    with open(val_path, "w", encoding="utf-8") as f:
        json.dump(val, f, indent=2, ensure_ascii=False, default=str)

    log(f"[pipeline] saved outputs:")
    log(f"  - {metrics_path}")
    log(f"  - {monthly_path}")
    log(f"  - {selected_path}")
    log(f"  - {val_path}")

    return {"metrics": bt.get("metrics"), "validation": val,
            "selected_path": str(selected_path), "metrics_path": str(metrics_path)}


# ---------------------------------------------------------------------------
# Verdict (compare against ship gate)
# ---------------------------------------------------------------------------
def run_verdict_only() -> dict:
    """Read latest outputs and print verdict. ~2s."""
    metrics_path = DATA_ROOT / "outputs" / "p0_baseline_metrics.json"
    val_path = DATA_ROOT / "outputs" / "p0_validation.json"
    if not metrics_path.exists():
        log("[verdict] no metrics file. Run p0_baseline first.", level="WARN")
        return {"error": "no metrics"}

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    validation = json.loads(val_path.read_text(encoding="utf-8")) if val_path.exists() else {}

    cagr = metrics.get("strategy_cagr", 0.0)
    bench = metrics.get("benchmark_cagr", 0.0)
    excess = metrics.get("excess_cagr", 0.0)
    sharpe = metrics.get("sharpe", 0.0)
    max_dd = metrics.get("max_dd", 0.0)
    turnover = metrics.get("avg_turnover", 0.0)

    print("=" * 60)
    print(f"P0 BASELINE VERDICT - engine {metrics.get('engine_version')}")
    print("=" * 60)
    print(f"  Strategy CAGR : {cagr:7.2%}")
    print(f"  Benchmark CAGR: {bench:7.2%}  (KOSPI200)")
    print(f"  Excess CAGR   : {excess:+7.2%}")
    print(f"  Sharpe        : {sharpe:7.3f}")
    print(f"  Max Drawdown  : {max_dd:7.2%}")
    print(f"  Avg Turnover  : {turnover:7.2%}")
    print(f"  Years         : {metrics.get('years_simulated'):>5}")
    print(f"  Beat-month %  : {metrics.get('beat_month_ratio', 0):7.1%}")
    if validation:
        print(f"  Validation    : {'PASS' if validation.get('all_pass') else 'FAIL'}")
    print("=" * 60)

    # Ship gate (P0 vs benchmark only — first baseline)
    p0_target_excess = 0.03   # +3pp vs benchmark
    if excess >= p0_target_excess:
        verdict = "SHIP - proceed to P1 (DART fundamentals)"
    elif excess >= 0:
        verdict = "PARTIAL - universe/cost OK, alpha weak. Tune before P1."
    else:
        verdict = "REGRESS - strategy underperforms benchmark. Debug cost / filters / signals."
    print(f"  VERDICT: {verdict}")
    print("=" * 60)
    return {"metrics": metrics, "validation": validation, "verdict": verdict}
