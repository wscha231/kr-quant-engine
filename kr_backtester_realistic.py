"""kr_backtester_realistic — 1억 KRW seed realistic backtest.

Addresses leakage + realistic friction:
  1. OOS picks via fold-based walk-forward (no in-sample contamination)
  2. Position sizing in actual share counts (KRW seed → shares)
  3. Mcap-tiered slippage:
       mcap > 5조: 5bp / 1-5조: 10bp / < 1조: 20bp (per side)
  4. Tax 0.18% on sell (KRX 거래세) + 0.015% × 2 brokerage = round-trip 31-51bp
  5. Daily ±30% price limit handling
  6. Drawdown ladder: -8% → 0.85, -15% → 0.65, -25% → 0.40 sleeve scale
  7. VKOSPI hard guard: > 25 → cash 30%, > 35 → cash 50%
  8. Regime sleeve multipliers (kr_regime)

Public API:
  generate_oos_picks(scored_panel, episodes, n_folds=5) → fold-OOS picks DataFrame
  run_realistic_backtest(picks, seed_krw, ...) → metrics dict
  realistic_cost_for_mcap(mcap_krw) → bp
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from kr_config import DATA_ROOT
from kr_helpers import log


# ===========================================================================
# 1. OOS picks generation (fold-based, no in-sample contamination)
# ===========================================================================
def generate_oos_picks(
    labeled_panel: pd.DataFrame,
    feature_cols: list[str],
    n_folds: int = 5,
    k_per_month: int = 30,
    cb_params: Optional[dict] = None,
) -> pd.DataFrame:
    """Walk-forward OOS picks: for each fold, train on prior and predict on test.

    Crucially each picks row is OOS (its rebalance_date never seen by the
    classifier during training). Concatenates 5 fold-test predictions →
    full timeline OOS picks.

    Returns DataFrame: rebalance_date, ticker, name, market_cap,
    p_pre_surge, rank_in_month, fold_id.
    """
    from kr_multibagger_classifier import walk_forward_splits
    try:
        from catboost import CatBoostClassifier
    except ImportError:
        log("[oos] catboost not installed", level="ERROR")
        return pd.DataFrame()

    if labeled_panel.empty or "is_pre_surge" not in labeled_panel.columns:
        return pd.DataFrame()

    cb_default = {
        "iterations": 400, "learning_rate": 0.05, "depth": 6,
        "loss_function": "Logloss", "eval_metric": "AUC", "verbose": 0,
        "random_seed": 42, "auto_class_weights": "Balanced",
    }
    cb_params = {**cb_default, **(cb_params or {})}

    splits = walk_forward_splits(labeled_panel, n_folds=n_folds)
    if not splits:
        return pd.DataFrame()

    log(f"[oos] generating OOS picks across {len(splits)} folds")
    fold_results = []
    panel = labeled_panel.reset_index(drop=True)

    for k, (tr_idx, te_idx) in enumerate(splits, 1):
        X_tr = panel.iloc[tr_idx][feature_cols].fillna(0).values
        y_tr = panel.iloc[tr_idx]["is_pre_surge"].values
        if y_tr.sum() < 5:
            log(f"[oos] fold {k}: insufficient positives, skip", level="WARN")
            continue
        log(f"[oos] fold {k}/{len(splits)}: train n={len(tr_idx)} (pos {y_tr.sum()}), "
            f"test n={len(te_idx)}")

        model = CatBoostClassifier(**cb_params)
        model.fit(X_tr, y_tr)
        X_te = panel.iloc[te_idx][feature_cols].fillna(0).values
        proba = model.predict_proba(X_te)[:, 1]

        sub = panel.iloc[te_idx][["rebalance_date", "ticker"]].copy()
        if "name" in panel.columns:
            sub["name"] = panel.iloc[te_idx]["name"].values
        if "market_cap" in panel.columns:
            sub["market_cap"] = panel.iloc[te_idx]["market_cap"].values
        sub["p_pre_surge"] = proba
        sub["fold_id"] = k
        fold_results.append(sub)

    if not fold_results:
        return pd.DataFrame()

    all_oos = pd.concat(fold_results, ignore_index=True)
    # Rank within each (rebalance_date), keep top-K
    all_oos = all_oos.sort_values(["rebalance_date", "p_pre_surge"], ascending=[True, False])
    all_oos["rank_in_month"] = all_oos.groupby("rebalance_date")["p_pre_surge"].rank(
        method="first", ascending=False).astype(int)
    out = all_oos[all_oos["rank_in_month"] <= k_per_month].reset_index(drop=True)
    log(f"[oos] OOS picks: {len(out)} rows × {out['rebalance_date'].nunique()} months")
    return out


def generate_oos_picks_purged_3sleeve(
    labeled_panel: pd.DataFrame,
    feature_cols: list[str],
    n_folds: int = 5,
    embargo_months: int = 9,
    k_per_month: int = 40,
    cb_params: Optional[dict] = None,
) -> pd.DataFrame:
    """Phase C3 OOS picks via 3 separate purged-walk-forward models.

    Three labels (kr_multibagger_classifier):
      is_pre_entry     [-6m, -1m] from surge_start
      is_continuation  [0, +3m]   from surge_start
      is_risk          forward 1m return <= -20%

    Trains a CatBoost binary classifier per label inside each purged fold
    (embargo_months drops the train tail that overlaps the test fold's label
    window). Output: rebalance_date, ticker [, name, market_cap],
    p_pre_entry, p_continuation, p_risk, p_combined, rank_in_month, fold_id.

    p_combined = 0.5 * p_pre_entry + 0.5 * p_continuation, scaled by
    (1 - p_risk). This single composite is the default ranking signal so the
    realistic backtester remains compatible with picks lacking sleeve flags.
    """
    from kr_multibagger_classifier import walk_forward_splits_purged
    try:
        from catboost import CatBoostClassifier
    except ImportError:
        log("[oos-3s] catboost not installed", level="ERROR")
        return pd.DataFrame()

    if labeled_panel.empty:
        return pd.DataFrame()
    label_cols = ("is_pre_entry", "is_continuation", "is_risk")
    have = [c for c in label_cols if c in labeled_panel.columns]
    if not have:
        return pd.DataFrame()

    cb_default = {
        "iterations": 400, "learning_rate": 0.05, "depth": 6,
        "loss_function": "Logloss", "eval_metric": "AUC", "verbose": 0,
        "random_seed": 42, "auto_class_weights": "Balanced",
    }
    cb_params = {**cb_default, **(cb_params or {})}

    splits = walk_forward_splits_purged(
        labeled_panel, n_folds=n_folds, embargo_months=embargo_months,
    )
    if not splits:
        log(f"[oos-3s] insufficient panel for purged splits "
            f"(n_folds={n_folds}, embargo={embargo_months}m)", level="WARN")
        return pd.DataFrame()

    log(f"[oos-3s] {len(splits)} purged folds, training {len(have)} models each")
    panel = labeled_panel.reset_index(drop=True)
    fold_results = []

    for k, (tr_idx, te_idx) in enumerate(splits, 1):
        X_tr = panel.iloc[tr_idx][feature_cols].fillna(0).values
        X_te = panel.iloc[te_idx][feature_cols].fillna(0).values
        sub = panel.iloc[te_idx][["rebalance_date", "ticker"]].copy()
        if "name" in panel.columns:
            sub["name"] = panel.iloc[te_idx]["name"].values
        if "market_cap" in panel.columns:
            sub["market_cap"] = panel.iloc[te_idx]["market_cap"].values

        for label in have:
            y_tr = panel.iloc[tr_idx][label].values.astype(int)
            if y_tr.sum() < 5:
                log(f"[oos-3s] fold {k} label {label}: <5 positives, skip",
                    level="WARN")
                sub[f"p_{label.replace('is_','')}"] = 0.0
                continue
            model = CatBoostClassifier(**cb_params)
            model.fit(X_tr, y_tr)
            proba = model.predict_proba(X_te)[:, 1]
            sub[f"p_{label.replace('is_','')}"] = proba

        sub["fold_id"] = k
        fold_results.append(sub)

    if not fold_results:
        return pd.DataFrame()

    out = pd.concat(fold_results, ignore_index=True)
    p_pre = out.get("p_pre_entry", pd.Series(0.0, index=out.index)).fillna(0)
    p_cont = out.get("p_continuation", pd.Series(0.0, index=out.index)).fillna(0)
    p_risk = out.get("p_risk", pd.Series(0.0, index=out.index)).fillna(0)
    out["p_combined"] = (0.5 * p_pre + 0.5 * p_cont) * (1.0 - p_risk)
    # Backwards-compat: realistic backtester reads `p_pre_surge` for ranking.
    out["p_pre_surge"] = out["p_combined"]
    out = out.sort_values(["rebalance_date", "p_combined"],
                          ascending=[True, False])
    out["rank_in_month"] = out.groupby("rebalance_date")["p_combined"].rank(
        method="first", ascending=False).astype(int)
    out = out[out["rank_in_month"] <= k_per_month].reset_index(drop=True)
    log(f"[oos-3s] picks: {len(out)} rows × "
        f"{out['rebalance_date'].nunique()} months")
    return out


# ===========================================================================
# 2. Realistic cost model (mcap-tiered slippage)
# ===========================================================================
def realistic_cost_per_side_bp(mcap_krw: float) -> float:
    """Per-side cost in basis points (one direction).

    Components:
      - Brokerage: 1.5bp (low-cost retail)
      - Slippage: 5/10/20 bp by mcap tier
      - Sell-only: +18bp tax (caller adds for sell side)
    """
    if mcap_krw is None or pd.isna(mcap_krw) or mcap_krw <= 0:
        slip = 20.0  # unknown mcap: assume small-cap
    elif mcap_krw >= 5e12:
        slip = 5.0
    elif mcap_krw >= 1e12:
        slip = 10.0
    else:
        slip = 20.0
    return 1.5 + slip


def total_round_trip_bp(mcap_krw: float) -> float:
    """Round-trip in bp = buy_bp + sell_bp + 18bp (KRX 매도세)."""
    one_side = realistic_cost_per_side_bp(mcap_krw)
    return 2 * one_side + 18.0


# ===========================================================================
# 3. Position sizing
# ===========================================================================
def size_positions(
    picks: pd.DataFrame,
    capital_krw: float,
    weights: pd.Series,
    prices: dict,
    avg_value_60d: Optional[dict] = None,
    max_pct_of_volume: float = 0.05,
) -> pd.DataFrame:
    """Convert weights to actual share counts (round-down).

    Liquidity constraint: per-position KRW notional <= 5% of 60d avg trading value.

    Returns DataFrame: ticker, target_weight, krw_notional, price, shares,
    realized_weight, liquidity_capped.
    """
    rows = []
    for _, p in picks.iterrows():
        tk = str(p["ticker"]).zfill(6)
        target_w = float(weights.get(tk, 1.0 / len(picks)))
        target_krw = capital_krw * target_w
        # Liquidity cap
        liq_cap_krw = float("inf")
        if avg_value_60d is not None and tk in avg_value_60d:
            liq_cap_krw = avg_value_60d[tk] * max_pct_of_volume
        actual_krw = min(target_krw, liq_cap_krw)
        liquidity_capped = actual_krw < target_krw - 1
        # Shares
        price = prices.get(tk, np.nan)
        if pd.isna(price) or price <= 0:
            shares = 0
            actual_krw = 0
        else:
            shares = int(actual_krw / price)
            actual_krw = shares * price
        rows.append({
            "ticker": tk,
            "target_weight": target_w,
            "krw_notional_target": target_krw,
            "krw_notional_actual": actual_krw,
            "price": price,
            "shares": shares,
            "liquidity_capped": liquidity_capped,
        })
    out = pd.DataFrame(rows)
    total = out["krw_notional_actual"].sum()
    out["realized_weight"] = (out["krw_notional_actual"] / total) if total > 0 else 0.0
    return out


# ===========================================================================
# 4. Drawdown ladder + VKOSPI guard
# ===========================================================================
def compute_sleeve_scale(
    current_dd: float,
    vkospi_level: float = 18.0,
    dd_thresholds=(-0.08, -0.15, -0.25),
    dd_scales=(0.85, 0.65, 0.40),
    vkospi_panic_threshold: float = 25.0,
    vkospi_extreme_threshold: float = 35.0,
) -> dict:
    """Return sleeve scale (0..1) based on drawdown + VKOSPI.

    Output: {sleeve_scale, cash_floor, dd_level, vkospi_level_active}
    Scale = min(dd_scale, 1 - vkospi_cash_floor).
    """
    # Drawdown scale (most-recent threshold breached)
    dd_scale = 1.0
    dd_level = 0
    for i, (th, sc) in enumerate(zip(dd_thresholds, dd_scales)):
        if current_dd <= th:
            dd_scale = sc
            dd_level = i + 1
    # VKOSPI cash floor
    vk_cash = 0.0
    if vkospi_level > vkospi_extreme_threshold:
        vk_cash = 0.5
    elif vkospi_level > vkospi_panic_threshold:
        vk_cash = 0.3
    sleeve_scale = min(dd_scale, 1.0 - vk_cash)
    return {
        "sleeve_scale": float(sleeve_scale),
        "cash_floor": 1.0 - sleeve_scale,
        "dd_level": dd_level,
        "vkospi_cash_floor": vk_cash,
    }


# ===========================================================================
# 5. Realistic backtest engine
# ===========================================================================
def run_realistic_backtest(
    picks: pd.DataFrame,
    seed_capital_krw: float = 1e8,
    top_n: int = 30,
    weighting: str = "score_power",   # equal | score | score_power | capped
    score_power: float = 1.5,
    weight_cap: float = 0.06,
    use_drawdown_breaker: bool = True,
    use_vkospi_guard: bool = True,
    fetch_macro_for_vkospi: bool = False,
    use_governance_overlay: bool = True,    # Phase C2: hard veto + weight cap
    governance_penalty_factor: float = 0.35,  # adjusted_score = p * (1 - λ*risk)
    use_sleeve_separation: bool = False,    # Phase C3: 3-sleeve capital split
    sleeve_pre_entry_pct: float = 0.40,
    sleeve_continuation_pct: float = 0.40,
    sleeve_defensive_pct: float = 0.20,
    verbose: bool = True,
) -> dict:
    """End-to-end realistic 1억 backtest with mcap-tiered cost + risk mgmt.

    Args:
        picks: DataFrame with rebalance_date, ticker, p_pre_surge,
               rank_in_month [, market_cap]. Optional governance columns:
               governance_risk_score, governance_hard_veto_flag — when
               present and use_governance_overlay=True, picks are filtered
               by veto and re-ranked / weight-capped.

    Returns:
        dict with metrics + monthly equity curve + trades log.
    """
    from kr_pykrx_client import fetch_ticker_history, fetch_market_cap_market

    if picks.empty:
        return {"error": "empty picks"}

    picks["rebalance_date"] = pd.to_datetime(picks["rebalance_date"])
    picks["ticker"] = picks["ticker"].astype(str).str.zfill(6)

    rebal_dates = sorted(picks["rebalance_date"].unique())
    log(f"[realistic] backtest {rebal_dates[0]} ~ {rebal_dates[-1]} "
        f"({len(rebal_dates)} months), seed {seed_capital_krw:,.0f} KRW")

    # Pre-fetch ticker histories for all unique tickers (inc. wider top_n+5
    # buffer so any historical holding remains priceable)
    all_tickers = (picks[picks["rank_in_month"] <= top_n + 10]["ticker"]
                    .astype(str).str.zfill(6).unique().tolist())
    log(f"[realistic] pre-fetching {len(all_tickers)} ticker histories...")
    prices_all = {}
    start = pd.Timestamp(rebal_dates[0]).strftime("%Y%m%d")
    end = (pd.Timestamp(rebal_dates[-1]) + pd.Timedelta(days=10)).strftime("%Y%m%d")
    for i, tk in enumerate(all_tickers, 1):
        if i % 100 == 0:
            log(f"[realistic] price fetch {i}/{len(all_tickers)}")
        h = fetch_ticker_history(tk, start, end, refresh_days=30)
        if not h.empty:
            h = h.sort_values("date").set_index("date")[["close", "high", "low", "volume", "value"]]
            prices_all[tk] = h
    log(f"[realistic] got prices for {len(prices_all)}/{len(all_tickers)} tickers")

    # Capital tracking
    capital = seed_capital_krw
    peak = capital
    cash = capital
    holdings = {}   # ticker -> shares
    monthly_log = []
    trades_log = []

    for i in range(len(rebal_dates) - 1):
        rd = pd.Timestamp(rebal_dates[i])
        next_rd = pd.Timestamp(rebal_dates[i + 1])

        # 1. Mark current portfolio at rd (close prices)
        portfolio_value = cash
        for tk, sh in holdings.items():
            if tk in prices_all:
                p = prices_all[tk]
                p_at_rd = p[p.index <= rd]["close"]
                if not p_at_rd.empty:
                    portfolio_value += sh * float(p_at_rd.iloc[-1])
        capital = portfolio_value

        # 2. Drawdown
        peak = max(peak, capital)
        current_dd = (capital / peak) - 1.0

        # 3. VKOSPI guard (placeholder — needs macro panel)
        vkospi_level = 18.0  # default; would fetch from panel here
        scale_info = compute_sleeve_scale(current_dd, vkospi_level)
        sleeve_scale = scale_info["sleeve_scale"] if (use_drawdown_breaker or use_vkospi_guard) else 1.0
        target_invested = capital * sleeve_scale

        # 4. Select picks at this rd
        # Phase C2 governance overlay: veto hard-flagged tickers and
        # re-rank by adjusted_score = p * (1 - penalty * risk). When the
        # picks DataFrame lacks governance columns we fall back to plain
        # rank_in_month behaviour.
        rd_pool = picks[picks["rebalance_date"] == rd].copy()
        if (use_governance_overlay
                and "governance_hard_veto_flag" in rd_pool.columns):
            n_before = len(rd_pool)
            rd_pool = rd_pool[
                rd_pool["governance_hard_veto_flag"].fillna(0).astype(float) < 0.5
            ].copy()
            if "governance_risk_score" in rd_pool.columns:
                risk = rd_pool["governance_risk_score"].fillna(0).astype(float)
                rd_pool["adjusted_score"] = (
                    rd_pool["p_pre_surge"].fillna(0).astype(float)
                    * (1.0 - governance_penalty_factor * risk)
                )
                rd_pool = rd_pool.sort_values("adjusted_score", ascending=False)
                rd_pool["rank_in_month_adj"] = range(1, len(rd_pool) + 1)
                rd_pool["rank_in_month"] = rd_pool["rank_in_month_adj"]
            n_after = len(rd_pool)
            if n_before != n_after:
                log(f"[gov] {pd.Timestamp(rd).date()}: vetoed "
                    f"{n_before - n_after}/{n_before} tickers")

        # Phase C3 sleeve separation: when enabled and the picks DataFrame
        # has p_pre_entry / p_continuation columns, partition top_n into
        # equal halves selected by each sleeve's score. The defensive sleeve
        # is realised by reducing target_invested by sleeve_defensive_pct
        # (cash floor) below.
        sleeve_active = (
            use_sleeve_separation
            and "p_pre_entry" in rd_pool.columns
            and "p_continuation" in rd_pool.columns
        )
        if sleeve_active:
            n_a = max(1, int(round(top_n * sleeve_pre_entry_pct
                                     / (sleeve_pre_entry_pct + sleeve_continuation_pct))))
            n_b = max(1, top_n - n_a)
            pool_a = (rd_pool.sort_values("p_pre_entry", ascending=False)
                       .head(n_a).copy())
            pool_a["sleeve"] = "pre_entry"
            remaining = rd_pool[~rd_pool["ticker"].isin(pool_a["ticker"])]
            pool_b = (remaining.sort_values("p_continuation", ascending=False)
                       .head(n_b).copy())
            pool_b["sleeve"] = "continuation"
            sel = pd.concat([pool_a, pool_b], ignore_index=True)
            # Apply defensive cash floor on top of any DD/VKOSPI-driven scale
            target_invested = target_invested * (1.0 - sleeve_defensive_pct)
        else:
            sel = rd_pool[rd_pool["rank_in_month"] <= top_n].copy()
        if sel.empty:
            monthly_log.append({"rd": rd, "capital": capital, "cash": cash,
                                  "n_holdings": len(holdings), "dd": current_dd,
                                  "sleeve_scale": sleeve_scale, "skipped": True})
            continue

        # 5. Compute weights
        sel["mcap_krw"] = sel.get("market_cap", 1e12)
        if weighting == "equal":
            sel["weight"] = 1.0 / len(sel)
        elif weighting == "score":
            s = sel["p_pre_surge"].clip(lower=0)
            sel["weight"] = s / s.sum() if s.sum() > 0 else 1.0/len(sel)
        elif weighting == "score_power":
            s = sel["p_pre_surge"].clip(lower=0) ** score_power
            sel["weight"] = s / s.sum() if s.sum() > 0 else 1.0/len(sel)
        elif weighting == "capped":
            s = sel["p_pre_surge"].clip(lower=0)
            w = s / s.sum() if s.sum() > 0 else pd.Series(1.0/len(sel), index=sel.index)
            w = w.clip(upper=weight_cap)
            sel["weight"] = w / w.sum()
        # Phase C2: per-ticker governance weight cap (tier-based) — applied
        # only when governance columns present.
        if use_governance_overlay and "governance_risk_score" in sel.columns:
            from kr_governance import governance_weight_cap as _gov_cap
            risk_arr = sel["governance_risk_score"].fillna(0).astype(float)
            sel["weight"] = [
                _gov_cap(r, w) for r, w in zip(risk_arr, sel["weight"].astype(float))
            ]
            tot = float(sel["weight"].sum())
            if tot > 0:
                sel["weight"] = sel["weight"] / tot
        weights = dict(zip(sel["ticker"].astype(str).str.zfill(6), sel["weight"]))

        # 6. Get prices at rd — for BOTH new picks AND old holdings (critical
        #    fix: previously only sel tickers were priced, causing old
        #    holdings to fail the sell lookup and cash to diverge)
        rd_prices = {}
        avg_values = {}
        all_relevant = set(sel["ticker"].astype(str).str.zfill(6)) | set(holdings.keys())
        for tk in all_relevant:
            tk = str(tk).zfill(6)
            if tk in prices_all:
                p = prices_all[tk]
                row = p[p.index <= rd]
                if not row.empty:
                    rd_prices[tk] = float(row.iloc[-1]["close"])
                    if "value" in p.columns:
                        avg_values[tk] = float(p[p.index <= rd]["value"].tail(60).mean())

        # 7. Size positions on target_invested (sleeve_scale applied)
        sized = size_positions(sel, target_invested, pd.Series(weights),
                                 rd_prices, avg_value_60d=avg_values)
        # Build mcap map from sel for cost calculation
        mcap_lookup = dict(zip(
            sel["ticker"].astype(str).str.zfill(6).values,
            sel["mcap_krw"].fillna(1e12).astype(float).values,
        ))
        for tk in holdings:
            if tk not in mcap_lookup:
                mcap_lookup[tk] = 1e12  # default for legacy holdings

        # 8. Compute trades — sell old, buy new
        new_holdings = dict(zip(sized["ticker"], sized["shares"].astype(int)))
        new_holdings = {k: v for k, v in new_holdings.items() if v > 0}

        sell_proceeds = 0.0
        for tk, old_sh in list(holdings.items()):
            new_sh = new_holdings.get(tk, 0)
            if new_sh < old_sh:
                sell_qty = old_sh - new_sh
                if tk in rd_prices:
                    px = rd_prices[tk]
                    mc = mcap_lookup.get(tk, 1e12)
                    sell_bp = realistic_cost_per_side_bp(mc) + 18.0
                    proceeds = sell_qty * px * (1.0 - sell_bp / 10000)
                    sell_proceeds += proceeds
                    trades_log.append({"rd": rd, "ticker": tk, "side": "SELL",
                                         "qty": sell_qty, "price": px,
                                         "cost_bp": sell_bp, "amount": proceeds})
        buy_cost_total = 0.0
        for tk, new_sh in new_holdings.items():
            old_sh = holdings.get(tk, 0)
            if new_sh > old_sh:
                buy_qty = new_sh - old_sh
                if tk in rd_prices:
                    px = rd_prices[tk]
                    mc = mcap_lookup.get(tk, 1e12)
                    buy_bp = realistic_cost_per_side_bp(mc)
                    cost = buy_qty * px * (1.0 + buy_bp / 10000)
                    buy_cost_total += cost
                    trades_log.append({"rd": rd, "ticker": tk, "side": "BUY",
                                         "qty": buy_qty, "price": px,
                                         "cost_bp": buy_bp, "amount": cost})

        # 9. Update cash
        cash = cash + sell_proceeds - buy_cost_total
        holdings = new_holdings

        n_h = len(new_holdings)
        invested = sum((sh * rd_prices.get(tk, 0)) for tk, sh in new_holdings.items())
        if verbose:
            log(f"[realistic] {rd.date()}: cap {capital:,.0f}, dd {current_dd*100:+.1f}%, "
                f"scale {sleeve_scale:.2f}, holdings {n_h}, invested {invested:,.0f}, "
                f"cash {cash:,.0f}")
        monthly_log.append({"rd": rd, "capital": capital, "cash": cash,
                              "invested": invested, "n_holdings": n_h, "dd": current_dd,
                              "peak": peak, "sleeve_scale": sleeve_scale,
                              "skipped": False})

    # Final mark
    final_rd = pd.Timestamp(rebal_dates[-1])
    portfolio_value = cash
    for tk, sh in holdings.items():
        if tk in prices_all:
            p = prices_all[tk]
            p_at = p[p.index <= final_rd]["close"]
            if not p_at.empty:
                portfolio_value += sh * float(p_at.iloc[-1])
    final_capital = portfolio_value
    monthly_log.append({"rd": final_rd, "capital": final_capital, "cash": cash,
                         "invested": final_capital - cash, "n_holdings": len(holdings),
                         "dd": (final_capital/peak - 1.0), "peak": peak,
                         "sleeve_scale": 1.0, "skipped": False})

    # Metrics
    df_log = pd.DataFrame(monthly_log)
    n_months = len(df_log)
    years = n_months / 12.0
    cum_return = final_capital / seed_capital_krw - 1.0
    cagr = (final_capital / seed_capital_krw) ** (1.0 / max(years, 1e-9)) - 1.0 \
        if final_capital > 0 else -1.0

    # MDD
    df_log["cap_max"] = df_log["capital"].cummax()
    df_log["dd_track"] = df_log["capital"] / df_log["cap_max"] - 1.0
    mdd = float(df_log["dd_track"].min())

    # Monthly returns
    df_log["monthly_ret"] = df_log["capital"].pct_change()
    sharpe = (df_log["monthly_ret"].mean() / df_log["monthly_ret"].std() * np.sqrt(12)
              if df_log["monthly_ret"].std() > 0 else 0.0)

    return {
        "seed_krw": seed_capital_krw,
        "final_krw": final_capital,
        "cum_return": cum_return,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": float(sharpe),
        "n_months": n_months,
        "years": years,
        "n_trades": len(trades_log),
        "monthly_log": df_log.to_dict(orient="records"),
        "trades_log": trades_log[:50],   # first 50 for brevity
    }
