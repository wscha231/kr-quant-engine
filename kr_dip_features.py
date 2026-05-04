"""kr_dip_features — feature engineering for the shake-out classifier.

Produces a 42-feature panel keyed by (ticker, dip_date) ready to train the
Phase F.F3 classifier. Uses the dip episode panel from
tools/mine_dip_episodes.py + per-ticker OHLCV / Naver flow caches.

Public API
----------
prepare_dip_feature_panel(episodes_df, ohlcv_lookup, flow_lookup,
                          mcap_lookup, kospi_index_lookup) -> DataFrame
    For each row of episodes_df, compute features and return a wider
    DataFrame with all original episode columns plus 5 feature groups
    (A-E) totaling ~42 columns.

The function is split per feature group so that operators can disable
expensive groups during quick experiments.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd

from kr_helpers import log


# ---------------------------------------------------------------------------
# Group A — Price action (10 features)
# ---------------------------------------------------------------------------
def _price_action_features(
    hist: pd.DataFrame,
    dip_idx: int,
    peak_close: float,
    dip_close: float,
) -> dict:
    """`hist` is a per-ticker daily OHLCV frame sorted ascending.

    Returns 10 features. NaN-safe — returns NaN for any feature whose
    inputs are missing.
    """
    out = {
        "f_dip_magnitude_pct": float(dip_close / peak_close - 1.0)
            if peak_close > 0 else np.nan,
        "f_dip_duration_days": np.nan,
        "f_pre_dip_uptrend_30d": np.nan,
        "f_pre_dip_consolidation_days": np.nan,
        "f_dist_from_52w_high": np.nan,
        "f_dist_from_50_ma": np.nan,
        "f_dist_from_200_ma": np.nan,
        "f_bollinger_band_position": np.nan,
        "f_rsi_14_at_dip": np.nan,
        "f_atr_14_pct": np.nan,
    }
    if hist.empty or "close" not in hist.columns:
        return out
    closes = hist["close"].astype(float).values
    n = len(closes)
    if dip_idx <= 0 or dip_idx >= n:
        return out
    # Distance from peak / 52w-high / MAs
    if dip_idx >= 252:
        high_52w = closes[dip_idx - 252: dip_idx + 1].max()
        out["f_dist_from_52w_high"] = float(dip_close / high_52w - 1.0) \
            if high_52w > 0 else np.nan
    if dip_idx >= 50:
        ma_50 = float(np.mean(closes[dip_idx - 50: dip_idx]))
        out["f_dist_from_50_ma"] = (dip_close / ma_50 - 1.0) if ma_50 > 0 else np.nan
    if dip_idx >= 200:
        ma_200 = float(np.mean(closes[dip_idx - 200: dip_idx]))
        out["f_dist_from_200_ma"] = (dip_close / ma_200 - 1.0) if ma_200 > 0 else np.nan
    # Pre-dip uptrend: 30d return prior to peak
    if dip_idx >= 31:
        peak_idx = dip_idx - int(np.argmax(closes[max(0, dip_idx - 30): dip_idx][::-1]))
        if peak_idx > 30:
            base = closes[peak_idx - 30]
            if base > 0:
                out["f_pre_dip_uptrend_30d"] = float(closes[peak_idx] / base - 1.0)
        # Consolidation: count of days where price stays within ±3pct of MA20
        if dip_idx >= 60:
            window_start = max(0, dip_idx - 60)
            window = closes[window_start: dip_idx]
            if len(window) >= 20:
                ma20_series = pd.Series(window).rolling(20, min_periods=10).mean()
                rel = (pd.Series(window) / ma20_series.replace(0, np.nan)) - 1.0
                out["f_pre_dip_consolidation_days"] = int((rel.abs() < 0.03).sum())
    # Dip duration: days from peak to dip_idx
    if dip_idx >= 30:
        peak_idx_30 = dip_idx - 30 + int(np.argmax(closes[dip_idx - 30: dip_idx + 1]))
        out["f_dip_duration_days"] = max(0, dip_idx - peak_idx_30)
    # Bollinger position
    if dip_idx >= 20:
        win = closes[dip_idx - 20: dip_idx]
        sd = float(np.std(win, ddof=1)) if len(win) > 1 else 0
        mu = float(np.mean(win))
        if sd > 0:
            out["f_bollinger_band_position"] = (dip_close - mu) / (2.0 * sd)
    # RSI 14
    if dip_idx >= 15:
        delta = np.diff(closes[dip_idx - 14: dip_idx + 1])
        gains = np.maximum(delta, 0).mean()
        losses = -np.minimum(delta, 0).mean()
        rs = gains / losses if losses > 0 else np.inf
        rsi = 100.0 - (100.0 / (1.0 + rs)) if rs != np.inf else 100.0
        out["f_rsi_14_at_dip"] = float(rsi)
    # ATR pct (14)
    if dip_idx >= 15 and {"high", "low"}.issubset(hist.columns):
        h = hist["high"].astype(float).values
        l = hist["low"].astype(float).values
        prev_close = closes[dip_idx - 14: dip_idx]
        cur_high = h[dip_idx - 14 + 1: dip_idx + 1]
        cur_low = l[dip_idx - 14 + 1: dip_idx + 1]
        tr1 = cur_high - cur_low
        tr2 = np.abs(cur_high - prev_close)
        tr3 = np.abs(cur_low - prev_close)
        tr = np.maximum.reduce([tr1, tr2, tr3])
        atr = float(tr.mean())
        out["f_atr_14_pct"] = atr / dip_close if dip_close > 0 else np.nan
    return out


# ---------------------------------------------------------------------------
# Group B — Volume (8 features)
# ---------------------------------------------------------------------------
def _volume_features(hist: pd.DataFrame, dip_idx: int) -> dict:
    out = {
        "f_vol_dip_day_z60": np.nan,
        "f_vol_5d_avg_vs_60d": np.nan,
        "f_vol_dryup_pct_pre_dip": np.nan,
        "f_obv_slope_30d": np.nan,
        "f_ad_slope_30d": np.nan,
        "f_vol_post_dip_5d_vs_dip_day": np.nan,
        "f_intraday_close_position": np.nan,
        "f_dip_day_value_zscore_60d": np.nan,
    }
    if "volume" not in hist.columns:
        return out
    vols = hist["volume"].astype(float).fillna(0).values
    closes = hist["close"].astype(float).values
    n = len(vols)
    if dip_idx <= 0 or dip_idx >= n:
        return out
    if dip_idx >= 60:
        win = vols[dip_idx - 60: dip_idx]
        mu = float(np.mean(win))
        sd = float(np.std(win, ddof=1)) if len(win) > 1 else 0
        if sd > 0:
            out["f_vol_dip_day_z60"] = (vols[dip_idx] - mu) / sd
        avg_60 = float(np.mean(vols[dip_idx - 60: dip_idx]))
        avg_5 = float(np.mean(vols[max(0, dip_idx - 5): dip_idx]))
        if avg_60 > 0:
            out["f_vol_5d_avg_vs_60d"] = avg_5 / avg_60
    if dip_idx >= 30:
        win_30 = vols[dip_idx - 30: dip_idx]
        if win_30.mean() > 0:
            out["f_vol_dryup_pct_pre_dip"] = float(
                (win_30 < win_30.mean() * 0.7).sum() / 30.0
            )
    # OBV (positive direction = up days, negative = down days)
    if dip_idx >= 30:
        sign = np.sign(np.diff(closes[dip_idx - 30: dip_idx + 1]))
        obv = (sign * vols[dip_idx - 29: dip_idx + 1]).cumsum()
        if len(obv) >= 2:
            out["f_obv_slope_30d"] = float((obv[-1] - obv[0]) / max(1.0, abs(obv[0]) + 1e-9))
    # A/D line approximation
    if dip_idx >= 30 and {"high", "low"}.issubset(hist.columns):
        h = hist["high"].astype(float).values
        l = hist["low"].astype(float).values
        rng = (h[dip_idx - 29: dip_idx + 1] - l[dip_idx - 29: dip_idx + 1])
        rng = np.where(rng > 0, rng, np.nan)
        clv = ((closes[dip_idx - 29: dip_idx + 1] - l[dip_idx - 29: dip_idx + 1]) -
                 (h[dip_idx - 29: dip_idx + 1] - closes[dip_idx - 29: dip_idx + 1])) / rng
        ad = np.nan_to_num(clv, nan=0.0) * vols[dip_idx - 29: dip_idx + 1]
        ad_cum = ad.cumsum()
        if len(ad_cum) >= 2 and ad_cum[0] != 0:
            out["f_ad_slope_30d"] = float((ad_cum[-1] - ad_cum[0]) / max(1.0, abs(ad_cum[0]) + 1e-9))
    # Volume in 5 days post dip
    end_post = min(n, dip_idx + 6)
    if end_post > dip_idx + 1 and vols[dip_idx] > 0:
        out["f_vol_post_dip_5d_vs_dip_day"] = float(
            vols[dip_idx + 1: end_post].mean() / vols[dip_idx]
        )
    # Intraday close position (within high-low range)
    if {"high", "low"}.issubset(hist.columns):
        h = float(hist["high"].iloc[dip_idx])
        l = float(hist["low"].iloc[dip_idx])
        if h > l:
            out["f_intraday_close_position"] = (closes[dip_idx] - l) / (h - l)
    # 거래대금 zscore
    if "value" in hist.columns and dip_idx >= 60:
        v = hist["value"].astype(float).fillna(0).values
        win = v[dip_idx - 60: dip_idx]
        mu = float(np.mean(win))
        sd = float(np.std(win, ddof=1)) if len(win) > 1 else 0
        if sd > 0:
            out["f_dip_day_value_zscore_60d"] = (v[dip_idx] - mu) / sd
    return out


# ---------------------------------------------------------------------------
# Group C — Flow (Korean institutional / foreign / individual; 10 features)
# ---------------------------------------------------------------------------
def _flow_features(
    naver_flow: pd.DataFrame,
    dip_date: pd.Timestamp,
    mcap: float,
) -> dict:
    """`naver_flow` is per-ticker daily flow (qty × close = approx KRW value)."""
    out = {
        "f_foreign_net_dip_day_z": np.nan,
        "f_foreign_net_5d_post_dip_pct_mcap": np.nan,
        "f_inst_net_dip_day_z": np.nan,
        "f_inst_net_5d_post_dip_pct_mcap": np.nan,
        "f_individual_net_dip_day_z": np.nan,
        "f_foreign_holding_change_30d_pct": np.nan,
        "f_foreign_buying_streak_post_dip": np.nan,
        "f_inst_buying_streak_post_dip": np.nan,
        "f_foreign_net_30d_pct_mcap": np.nan,
        "f_smart_money_directional_score": np.nan,
    }
    if naver_flow is None or naver_flow.empty or "date" not in naver_flow.columns:
        return out
    nf = naver_flow.copy()
    nf["date"] = pd.to_datetime(nf["date"])
    nf = nf.sort_values("date").reset_index(drop=True)
    # Compute foreign / inst net KRW value per row
    if "close" in nf.columns:
        if "foreign_net_qty" in nf.columns:
            nf["foreign_net_value"] = nf["foreign_net_qty"].fillna(0) * nf["close"].fillna(0)
        if "inst_net_qty" in nf.columns:
            nf["inst_net_value"] = nf["inst_net_qty"].fillna(0) * nf["close"].fillna(0)
        # Individual proxy: -(foreign + inst)
        if "foreign_net_value" in nf.columns and "inst_net_value" in nf.columns:
            nf["individual_net_value"] = -(nf["foreign_net_value"] + nf["inst_net_value"])
    dip_ts = pd.Timestamp(dip_date)
    # Slice 60 days pre-dip + 5 days post
    pre_60 = nf[(nf["date"] <= dip_ts) & (nf["date"] >= dip_ts - pd.Timedelta(days=120))]
    post_5 = nf[(nf["date"] > dip_ts) & (nf["date"] <= dip_ts + pd.Timedelta(days=10))]
    # Z-scores on dip day (use full ticker history if available)
    if "foreign_net_value" in nf.columns and len(pre_60) >= 20:
        win = pre_60["foreign_net_value"].fillna(0).values
        mu, sd = float(win.mean()), float(win.std(ddof=1) or 0)
        dip_row = nf[nf["date"] == dip_ts]
        if not dip_row.empty and sd > 0:
            out["f_foreign_net_dip_day_z"] = (
                float(dip_row["foreign_net_value"].iloc[0]) - mu
            ) / sd
    if "inst_net_value" in nf.columns and len(pre_60) >= 20:
        win = pre_60["inst_net_value"].fillna(0).values
        mu, sd = float(win.mean()), float(win.std(ddof=1) or 0)
        dip_row = nf[nf["date"] == dip_ts]
        if not dip_row.empty and sd > 0:
            out["f_inst_net_dip_day_z"] = (
                float(dip_row["inst_net_value"].iloc[0]) - mu
            ) / sd
    if "individual_net_value" in nf.columns and len(pre_60) >= 20:
        win = pre_60["individual_net_value"].fillna(0).values
        mu, sd = float(win.mean()), float(win.std(ddof=1) or 0)
        dip_row = nf[nf["date"] == dip_ts]
        if not dip_row.empty and sd > 0:
            out["f_individual_net_dip_day_z"] = (
                float(dip_row["individual_net_value"].iloc[0]) - mu
            ) / sd
    # 5-day post-dip cumulative as pct of mcap
    if mcap and mcap > 0 and not post_5.empty:
        if "foreign_net_value" in post_5.columns:
            out["f_foreign_net_5d_post_dip_pct_mcap"] = float(
                post_5["foreign_net_value"].fillna(0).sum() / mcap
            )
        if "inst_net_value" in post_5.columns:
            out["f_inst_net_5d_post_dip_pct_mcap"] = float(
                post_5["inst_net_value"].fillna(0).sum() / mcap
            )
    # 30d pre-dip foreign net pct mcap
    pre_30 = pre_60[pre_60["date"] >= dip_ts - pd.Timedelta(days=45)]
    if mcap and mcap > 0 and not pre_30.empty and "foreign_net_value" in pre_30.columns:
        out["f_foreign_net_30d_pct_mcap"] = float(
            pre_30["foreign_net_value"].fillna(0).sum() / mcap
        )
    # Foreign holding pct change
    if "foreign_holding_pct" in nf.columns and len(pre_60) >= 20:
        first = float(pre_60["foreign_holding_pct"].iloc[0])
        last = float(pre_60["foreign_holding_pct"].iloc[-1])
        out["f_foreign_holding_change_30d_pct"] = last - first
    # Buying streaks post dip
    def _streak(values: np.ndarray) -> int:
        n = 0
        for v in values:
            if v > 0:
                n += 1
            else:
                break
        return n
    if "foreign_net_value" in post_5.columns:
        out["f_foreign_buying_streak_post_dip"] = _streak(
            post_5["foreign_net_value"].fillna(0).values
        )
    if "inst_net_value" in post_5.columns:
        out["f_inst_buying_streak_post_dip"] = _streak(
            post_5["inst_net_value"].fillna(0).values
        )
    # Smart money directional score: sign of (foreign_5d + inst_5d)
    f5 = out.get("f_foreign_net_5d_post_dip_pct_mcap") or 0
    i5 = out.get("f_inst_net_5d_post_dip_pct_mcap") or 0
    if not pd.isna(f5) and not pd.isna(i5):
        score = float(f5 + i5) * 100.0  # in pct of mcap
        out["f_smart_money_directional_score"] = float(np.clip(score, -10.0, 10.0))
    return out


# ---------------------------------------------------------------------------
# Group D — Context / regime (8 features)
# ---------------------------------------------------------------------------
def _context_features(
    dip_date: pd.Timestamp,
    kospi_index: Optional[pd.DataFrame],
    vkospi_panel: Optional[pd.DataFrame],
    governance_score: Optional[float],
) -> dict:
    out = {
        "f_kospi_drawdown_at_dip": np.nan,
        "f_kospi_above_ma200": np.nan,
        "f_vkospi_at_dip": np.nan,
        "f_vkospi_change_5d": np.nan,
        "f_governance_risk_score": float(governance_score) if governance_score else 0.0,
        "f_quarter_end_proximity": 0.0,
        "f_year_end_proximity": 0.0,
        "f_pre_earnings_window": 0.0,
    }
    dip_ts = pd.Timestamp(dip_date)
    # Quarter / year-end proxy
    days_to_quarter_end = min(
        abs((dip_ts - pd.Timestamp(dip_ts.year, q * 3, 1)).days)
        for q in (1, 2, 3, 4)
    )
    out["f_quarter_end_proximity"] = float(np.exp(-days_to_quarter_end / 30.0))
    days_to_year_end = abs((dip_ts - pd.Timestamp(dip_ts.year, 12, 31)).days)
    out["f_year_end_proximity"] = float(np.exp(-days_to_year_end / 60.0))
    # KOSPI features
    if kospi_index is not None and not kospi_index.empty:
        ki = kospi_index.copy()
        ki["date"] = pd.to_datetime(ki["date"])
        ki = ki.sort_values("date").reset_index(drop=True)
        prior = ki[ki["date"] <= dip_ts]
        if not prior.empty and "close" in prior.columns:
            kc = prior["close"].astype(float).values
            if len(kc) >= 252:
                high_252 = float(kc[-252:].max())
                out["f_kospi_drawdown_at_dip"] = (kc[-1] / high_252 - 1.0) \
                    if high_252 > 0 else np.nan
            if len(kc) >= 200:
                ma_200 = float(np.mean(kc[-200:]))
                out["f_kospi_above_ma200"] = float(kc[-1] > ma_200)
    # VKOSPI features
    if vkospi_panel is not None and not vkospi_panel.empty:
        vp = vkospi_panel.copy()
        vp["date"] = pd.to_datetime(vp["date"])
        vp = vp.sort_values("date")
        prior = vp[vp["date"] <= dip_ts]
        if not prior.empty and "vkospi_level" in prior.columns:
            v = prior["vkospi_level"].astype(float).values
            if len(v) >= 5:
                out["f_vkospi_at_dip"] = float(v[-1])
                out["f_vkospi_change_5d"] = float(v[-1] / v[-5] - 1.0) \
                    if v[-5] > 0 else np.nan
    return out


# ---------------------------------------------------------------------------
# Group E — Event triggers (6 features)
# ---------------------------------------------------------------------------
def _event_features(
    dart_event_panel: Optional[pd.DataFrame],
    ticker: str,
    dip_date: pd.Timestamp,
) -> dict:
    out = {
        "f_dart_event_within_5d_pre_dip": 0.0,
        "f_dart_event_severity_score": 0.0,
        "f_dilution_event_within_30d": 0.0,
        "f_overheating_flag_within_5d": 0.0,
        "f_negative_event_within_10d": 0.0,
        "f_positive_event_within_10d": 0.0,
    }
    if dart_event_panel is None or dart_event_panel.empty:
        return out
    df = dart_event_panel
    if "ticker" not in df.columns or "rcept_dt" not in df.columns:
        return out
    sub = df[df["ticker"].astype(str).str.zfill(6) == str(ticker).zfill(6)]
    if sub.empty:
        return out
    sub = sub.copy()
    sub["rcept_dt"] = pd.to_datetime(sub["rcept_dt"], errors="coerce")
    dip_ts = pd.Timestamp(dip_date)
    pre_5 = sub[(sub["rcept_dt"] >= dip_ts - pd.Timedelta(days=5)) &
                  (sub["rcept_dt"] < dip_ts)]
    pre_30 = sub[(sub["rcept_dt"] >= dip_ts - pd.Timedelta(days=30)) &
                   (sub["rcept_dt"] < dip_ts)]
    win_10 = sub[(sub["rcept_dt"] >= dip_ts - pd.Timedelta(days=10)) &
                   (sub["rcept_dt"] < dip_ts)]
    out["f_dart_event_within_5d_pre_dip"] = float(len(pre_5))
    negative_cats = {"capital_increase", "treasury_sell", "convertible_bond",
                      "warrant_bond", "spinoff", "capital_reduction"}
    positive_cats = {"treasury_buyback", "bonus_issue"}
    if "event_category" in win_10.columns:
        out["f_negative_event_within_10d"] = float(
            (win_10["event_category"].isin(negative_cats)).sum()
        )
        out["f_positive_event_within_10d"] = float(
            (win_10["event_category"].isin(positive_cats)).sum()
        )
    if "event_category" in pre_30.columns:
        out["f_dilution_event_within_30d"] = float(
            (pre_30["event_category"].isin(
                {"capital_increase", "convertible_bond", "warrant_bond"}
            )).sum()
        )
    out["f_dart_event_severity_score"] = (
        out["f_negative_event_within_10d"] * 1.0
        - out["f_positive_event_within_10d"] * 0.5
    )
    # Overheating flag: the live universe column exists separately
    return out


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------
def prepare_dip_feature_panel(
    episodes: pd.DataFrame,
    ohlcv_lookup: Callable[[str], pd.DataFrame],
    flow_lookup: Optional[Callable[[str], pd.DataFrame]] = None,
    mcap_lookup: Optional[Callable[[str, pd.Timestamp], Optional[float]]] = None,
    kospi_index: Optional[pd.DataFrame] = None,
    vkospi_panel: Optional[pd.DataFrame] = None,
    dart_event_panel: Optional[pd.DataFrame] = None,
    governance_lookup: Optional[Callable[[str, pd.Timestamp], float]] = None,
    progress_every: int = 200,
) -> pd.DataFrame:
    """Compute 42-feature panel for each episode row.

    Args:
        episodes: dip events frame (output of mine_dip_episodes).
        ohlcv_lookup: f(ticker) -> per-ticker OHLCV DataFrame
                      (sorted ascending; close + volume columns required).
        flow_lookup: f(ticker) -> Naver flow DataFrame.
        mcap_lookup: f(ticker, dip_date) -> mcap KRW.
        kospi_index: market index OHLCV (single frame, KOSPI 1001).
        vkospi_panel: VKOSPI index frame.
        dart_event_panel: DART events long format for ALL tickers.
        governance_lookup: f(ticker, dip_date) -> governance_risk_score.

    Returns:
        DataFrame with columns of episodes plus ~42 f_* feature columns.
    """
    if episodes is None or episodes.empty:
        return pd.DataFrame()
    rows: list[dict] = []
    for k, ep in episodes.reset_index(drop=True).iterrows():
        ticker = str(ep["ticker"]).zfill(6)
        dip_date = pd.Timestamp(ep["dip_date"])
        if k % progress_every == 0:
            log(f"[dip-feat] {k}/{len(episodes)}")
        try:
            hist = ohlcv_lookup(ticker)
        except Exception:
            hist = pd.DataFrame()
        if hist.empty:
            continue
        # Find dip_idx in hist by date
        hist = hist.sort_values("date").reset_index(drop=True)
        match = hist.index[pd.to_datetime(hist["date"]) == dip_date]
        if len(match) == 0:
            continue
        dip_idx = int(match[0])

        feat = {}
        feat.update(_price_action_features(
            hist, dip_idx,
            float(ep["peak_close"]), float(ep["dip_low_close"])
        ))
        feat.update(_volume_features(hist, dip_idx))

        flow = flow_lookup(ticker) if flow_lookup else None
        mcap = mcap_lookup(ticker, dip_date) if mcap_lookup else float(ep.get("market_cap_at_dip") or 0)
        feat.update(_flow_features(flow, dip_date, mcap or 0))

        gov = governance_lookup(ticker, dip_date) if governance_lookup else None
        feat.update(_context_features(
            dip_date, kospi_index, vkospi_panel, gov
        ))
        feat.update(_event_features(dart_event_panel, ticker, dip_date))

        # Combine with original episode row
        row = {**ep.to_dict(), **feat}
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    log(f"[dip-feat] panel: {len(out)} rows × "
        f"{sum(c.startswith('f_') for c in out.columns)} features")
    return out


# ---------------------------------------------------------------------------
# Sanity test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("kr_dip_features sanity test")
    # Synthetic episode: dip at index 100 in a synthetic random walk
    np.random.seed(42)
    n = 300
    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    closes = 100 * np.exp(np.cumsum(np.random.normal(0, 0.02, n)))
    hist = pd.DataFrame({
        "date": dates,
        "close": closes,
        "high": closes * 1.01,
        "low": closes * 0.99,
        "volume": np.random.uniform(1e6, 5e6, n),
        "value": closes * np.random.uniform(1e6, 5e6, n),
    })
    episodes = pd.DataFrame([{
        "ticker": "TEST",
        "dip_date": dates[150].date(),
        "peak_date": dates[140].date(),
        "dip_low_close": float(closes[150]),
        "peak_close": float(closes[140]),
        "drawdown_pct": float(closes[150] / closes[140] - 1.0),
        "recovery_close": float(closes[170]),
        "label": 1,
        "market_cap_at_dip": 1e12,
    }])
    panel = prepare_dip_feature_panel(
        episodes,
        ohlcv_lookup=lambda t: hist,
        flow_lookup=lambda t: pd.DataFrame(),
        progress_every=1,
    )
    fcols = [c for c in panel.columns if c.startswith("f_")]
    print(f"\n{len(fcols)} features computed:")
    for c in fcols:
        print(f"  {c:40s} = {panel.iloc[0][c]}")
