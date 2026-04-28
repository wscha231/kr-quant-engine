"""kr_technicals — Technical indicators for kr_quant_engine (P3).

Pure-numeric module. Takes a daily OHLCV DataFrame (date-sorted, with columns
date, open, high, low, close, volume) and produces a dict of per-rebalance
technical indicator values.

All computations are PIT-safe — only data with date <= rebalance_date is used.

Catalog (alpha hypotheses based on r1000 phase11 + IBD/O'Neil/Minervini):

Trend / Stage:
  - ma_5, ma_20, ma_60, ma_120, ma_200    : moving averages
  - ma_stack_aligned                        : MA20 > MA50 > MA150 > MA200 (Stage 2)
  - dist_from_ma_50/150/200                 : (close - MA) / MA
  - dist_from_52w_high                      : (close - 52w_high) / 52w_high  (≤0)
  - dist_from_52w_low                       : (close - 52w_low) / 52w_low  (≥0)
  - new_52w_high_flag                       : close == 52w_high

Volume:
  - volume_ma_50                            : 50-day average
  - volume_zscore_50                        : (vol - vol_ma_50) / vol_std_50
  - volume_dryup_pct                        : recent volume vs 50d (<0.7 = drying up)

Momentum oscillators:
  - rsi_14                                  : 14-day RSI
  - rsi_overbought                          : flag (rsi > 70)
  - rsi_oversold                            : flag (rsi < 30)

Volatility:
  - atr_14                                  : 14-day Average True Range
  - atr_pct                                 : atr / close
  - bb_upper_20, bb_lower_20                : Bollinger 20/2
  - bb_position                             : (close - lower) / (upper - lower) ∈ [0, 1]
  - vol_contraction                         : atr_pct(20d) / atr_pct(60d) (<1 = contraction)

Composite signals:
  - stage_label                             : "1_basing" | "2_uptrend" | "3_topping" | "4_decline"
  - trend_template_score                    : 0..8, Minervini 8 conditions met
  - breakout_flag                           : close > 52w_high(prev_60d) AND volume_zscore > 1.5
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# ===========================================================================
# 1. Moving averages + stack alignment
# ===========================================================================
def compute_moving_averages(prices: pd.DataFrame,
                             windows: tuple[int, ...] = (5, 20, 50, 60, 150, 200)) -> dict:
    """Compute MA at multiple windows. Returns dict {f'ma_{n}': latest_value}.

    Uses simple MA (SMA). Min_periods = window so partial windows return NaN.
    """
    if prices.empty or "close" not in prices.columns:
        return {f"ma_{w}": np.nan for w in windows}
    s = prices["close"].astype(float)
    out = {}
    for w in windows:
        if len(s) >= w:
            out[f"ma_{w}"] = float(s.rolling(window=w, min_periods=w).mean().iloc[-1])
        else:
            out[f"ma_{w}"] = np.nan
    return out


def is_ma_stack_aligned(ma_dict: dict, close: float) -> bool:
    """True if close > MA20 > MA50 > MA150 > MA200 (Minervini Stage 2 condition)."""
    needed = ("ma_20", "ma_50", "ma_150", "ma_200")
    if not all(k in ma_dict for k in needed):
        return False
    vals = [ma_dict[k] for k in needed]
    if any(pd.isna(v) for v in vals):
        return False
    return bool(close > vals[0] > vals[1] > vals[2] > vals[3])


# ===========================================================================
# 2. 52-week extremes
# ===========================================================================
def compute_52w_extremes(prices: pd.DataFrame, business_days: int = 252) -> dict:
    """52-week (252 business days) high / low + distance from current close.

    Returns:
        high_52w, low_52w, dist_from_52w_high (≤0), dist_from_52w_low (≥0),
        new_52w_high_flag
    """
    if prices.empty or "close" not in prices.columns:
        return {"high_52w": np.nan, "low_52w": np.nan,
                "dist_from_52w_high": np.nan, "dist_from_52w_low": np.nan,
                "new_52w_high_flag": False}
    s = prices["close"].astype(float)
    if len(s) < 5:
        return {"high_52w": np.nan, "low_52w": np.nan,
                "dist_from_52w_high": np.nan, "dist_from_52w_low": np.nan,
                "new_52w_high_flag": False}
    window = s.tail(business_days)
    high = float(window.max())
    low = float(window.min())
    close = float(s.iloc[-1])
    dist_high = (close / high) - 1.0 if high > 0 else np.nan
    dist_low = (close / low) - 1.0 if low > 0 else np.nan
    return {
        "high_52w": high,
        "low_52w": low,
        "dist_from_52w_high": dist_high,
        "dist_from_52w_low": dist_low,
        "new_52w_high_flag": bool(close >= high * 0.999),  # 0.1% tolerance
    }


# ===========================================================================
# 3. Volume statistics
# ===========================================================================
def compute_volume_stats(prices: pd.DataFrame, window: int = 50) -> dict:
    """Volume MA, z-score, dry-up ratio."""
    if prices.empty or "volume" not in prices.columns:
        return {"volume_ma_50": np.nan, "volume_zscore_50": np.nan,
                "volume_dryup_pct": np.nan}
    v = prices["volume"].astype(float)
    if len(v) < window:
        return {"volume_ma_50": np.nan, "volume_zscore_50": np.nan,
                "volume_dryup_pct": np.nan}
    ma = float(v.rolling(window=window, min_periods=window).mean().iloc[-1])
    std = float(v.rolling(window=window, min_periods=window).std().iloc[-1])
    latest = float(v.iloc[-1])
    z = (latest - ma) / std if std > 0 else 0.0
    # Recent 5d avg vs 50d
    recent = float(v.tail(5).mean())
    dryup = recent / ma if ma > 0 else np.nan
    return {
        "volume_ma_50": ma,
        "volume_zscore_50": float(z),
        "volume_dryup_pct": float(dryup),
    }


# ===========================================================================
# 4. RSI (Relative Strength Index)
# ===========================================================================
def compute_rsi(prices: pd.DataFrame, window: int = 14) -> dict:
    """Wilder's RSI. Returns rsi_14 + overbought/oversold flags."""
    if prices.empty or "close" not in prices.columns or len(prices) < window + 1:
        return {"rsi_14": np.nan, "rsi_overbought": False, "rsi_oversold": False}
    delta = prices["close"].astype(float).diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0/window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0/window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    latest = rsi.iloc[-1]
    if pd.isna(latest):
        return {"rsi_14": np.nan, "rsi_overbought": False, "rsi_oversold": False}
    return {
        "rsi_14": float(latest),
        "rsi_overbought": bool(latest > 70),
        "rsi_oversold": bool(latest < 30),
    }


# ===========================================================================
# 5. ATR (Average True Range)
# ===========================================================================
def compute_atr(prices: pd.DataFrame, window: int = 14) -> dict:
    """Wilder's ATR + atr/close ratio."""
    if prices.empty or len(prices) < window + 1:
        return {"atr_14": np.nan, "atr_pct": np.nan}
    cols = {"high", "low", "close"}
    if not cols.issubset(prices.columns):
        return {"atr_14": np.nan, "atr_pct": np.nan}
    high = prices["high"].astype(float)
    low = prices["low"].astype(float)
    close = prices["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0/window, adjust=False, min_periods=window).mean().iloc[-1]
    if pd.isna(atr):
        return {"atr_14": np.nan, "atr_pct": np.nan}
    latest_close = float(close.iloc[-1])
    return {
        "atr_14": float(atr),
        "atr_pct": float(atr / latest_close) if latest_close > 0 else np.nan,
    }


# ===========================================================================
# 6. Bollinger Bands
# ===========================================================================
def compute_bollinger(prices: pd.DataFrame, window: int = 20, k: float = 2.0) -> dict:
    """Bollinger upper/lower + position [0, 1]."""
    if prices.empty or "close" not in prices.columns or len(prices) < window:
        return {"bb_upper_20": np.nan, "bb_lower_20": np.nan,
                "bb_position": np.nan}
    c = prices["close"].astype(float)
    ma = c.rolling(window=window, min_periods=window).mean().iloc[-1]
    std = c.rolling(window=window, min_periods=window).std().iloc[-1]
    upper = ma + k * std
    lower = ma - k * std
    close = float(c.iloc[-1])
    if pd.isna(upper) or pd.isna(lower) or upper - lower < 1e-9:
        return {"bb_upper_20": float(upper) if not pd.isna(upper) else np.nan,
                "bb_lower_20": float(lower) if not pd.isna(lower) else np.nan,
                "bb_position": np.nan}
    position = (close - lower) / (upper - lower)
    return {
        "bb_upper_20": float(upper),
        "bb_lower_20": float(lower),
        "bb_position": float(np.clip(position, 0.0, 1.0)),
    }


# ===========================================================================
# 7. Volatility contraction (Minervini VCP)
# ===========================================================================
def compute_volatility_contraction(prices: pd.DataFrame) -> dict:
    """ATR(20) / ATR(60) ratio. <1 = contraction (volatility tightening).

    Volatility contraction patterns precede breakouts.
    """
    short = compute_atr(prices, window=20).get("atr_pct", np.nan)
    long = compute_atr(prices, window=60).get("atr_pct", np.nan)
    if pd.isna(short) or pd.isna(long) or long < 1e-9:
        return {"vol_contraction": np.nan}
    return {"vol_contraction": float(short / long)}


# ===========================================================================
# 8. Stage classifier (Weinstein 4-stage)
# ===========================================================================
def classify_stage(prices: pd.DataFrame) -> dict:
    """Weinstein 4-stage classification using MA200 slope + price position.

    Stage 1 (Basing):    price near MA200, MA200 flat
    Stage 2 (Uptrend):   price > MA200, MA200 rising
    Stage 3 (Topping):   price near MA200, MA200 flattening from rise
    Stage 4 (Decline):   price < MA200, MA200 falling

    Returns: stage_label (str)
    """
    if prices.empty or "close" not in prices.columns or len(prices) < 220:
        return {"stage_label": "unknown"}
    s = prices["close"].astype(float)
    ma200 = s.rolling(window=200, min_periods=200).mean()
    if pd.isna(ma200.iloc[-1]):
        return {"stage_label": "unknown"}
    close = float(s.iloc[-1])
    ma200_now = float(ma200.iloc[-1])
    ma200_prev = float(ma200.iloc[-21]) if len(ma200) >= 21 else np.nan
    if pd.isna(ma200_prev) or ma200_prev < 1e-9:
        return {"stage_label": "unknown"}
    slope_pct = (ma200_now / ma200_prev) - 1.0
    above_ma = close > ma200_now * 1.02   # 2% buffer
    below_ma = close < ma200_now * 0.98

    if above_ma and slope_pct > 0.005:        # rising MA200 + above
        stage = "2_uptrend"
    elif below_ma and slope_pct < -0.005:     # falling MA200 + below
        stage = "4_decline"
    elif above_ma and slope_pct <= 0.005:     # above but MA flattening
        stage = "3_topping"
    elif below_ma and slope_pct >= -0.005:    # below but MA flat
        stage = "1_basing"
    else:
        stage = "1_basing"  # ambiguous — call it basing
    return {"stage_label": stage}


# ===========================================================================
# 9. Minervini Trend Template (8 conditions, sums to score 0..8)
# ===========================================================================
def compute_trend_template_score(
    prices: pd.DataFrame,
    benchmark_prices: Optional[pd.DataFrame] = None,
) -> dict:
    """Mark Minervini's 8-condition Stage 2 trend template:
       1. Close > MA150 and MA200
       2. MA150 > MA200
       3. MA200 trending up (>= 1 month)
       4. MA50 > MA150 > MA200
       5. Close > MA50
       6. Close >= 25% above 52w low
       7. Close within 25% of 52w high
       8. RS rating high (vs benchmark; using 12-month return rank > 0.7 as proxy)

    Returns score 0..8. RS condition skipped if benchmark not provided (max 7).
    """
    if prices.empty or "close" not in prices.columns or len(prices) < 220:
        return {"trend_template_score": 0,
                "trend_template_pass": False}
    s = prices["close"].astype(float)
    close = float(s.iloc[-1])

    ma_50 = s.rolling(50, min_periods=50).mean().iloc[-1]
    ma_150 = s.rolling(150, min_periods=150).mean().iloc[-1]
    ma_200 = s.rolling(200, min_periods=200).mean().iloc[-1]
    ma_200_prev = s.rolling(200, min_periods=200).mean().iloc[-21] if len(s) >= 220 else np.nan

    score = 0
    if not pd.isna(ma_150) and not pd.isna(ma_200):
        if close > ma_150 and close > ma_200:
            score += 1
        if ma_150 > ma_200:
            score += 1
    if not pd.isna(ma_200_prev) and not pd.isna(ma_200) and ma_200_prev > 0:
        if (ma_200 / ma_200_prev) - 1.0 > 0.005:
            score += 1
    if not pd.isna(ma_50) and not pd.isna(ma_150) and not pd.isna(ma_200):
        if ma_50 > ma_150 > ma_200:
            score += 1
    if not pd.isna(ma_50) and close > ma_50:
        score += 1

    # 52w high/low distance
    window = s.tail(252)
    if len(window) >= 5:
        high_52w = float(window.max())
        low_52w = float(window.min())
        if low_52w > 0 and close >= low_52w * 1.25:
            score += 1
        if high_52w > 0 and close >= high_52w * 0.75:
            score += 1

    # RS rating proxy (vs benchmark 12m return)
    if benchmark_prices is not None and not benchmark_prices.empty and len(s) >= 252:
        own_12m = (s.iloc[-1] / s.iloc[-252] - 1.0) if s.iloc[-252] > 0 else np.nan
        bench_close = benchmark_prices["close"].astype(float)
        if len(bench_close) >= 252:
            bench_12m = (bench_close.iloc[-1] / bench_close.iloc[-252] - 1.0
                         ) if bench_close.iloc[-252] > 0 else np.nan
            if not pd.isna(own_12m) and not pd.isna(bench_12m):
                if own_12m > bench_12m:
                    score += 1

    return {
        "trend_template_score": int(score),
        "trend_template_pass": bool(score >= 7),
    }


# ===========================================================================
# 10. Top-level orchestrator: compute all technicals for one ticker
# ===========================================================================
def compute_all_technicals(
    prices: pd.DataFrame,
    benchmark_prices: Optional[pd.DataFrame] = None,
) -> dict:
    """Compute the full technical indicator set for one ticker's daily OHLCV.

    `prices` should be sorted ascending by date, contain (date, open, high, low,
    close, volume), and end at the rebalance date (caller's responsibility for
    PIT).
    """
    out = {}
    if prices.empty:
        return out

    out.update(compute_moving_averages(prices))
    out["close"] = float(prices["close"].iloc[-1])
    out["ma_stack_aligned"] = is_ma_stack_aligned(out, out["close"])

    out.update(compute_52w_extremes(prices))
    out.update(compute_volume_stats(prices))
    out.update(compute_rsi(prices))
    out.update(compute_atr(prices))
    out.update(compute_bollinger(prices))
    out.update(compute_volatility_contraction(prices))
    out.update(classify_stage(prices))
    out.update(compute_trend_template_score(prices, benchmark_prices))

    # Composite breakout flag: new 52w high + volume spike
    breakout = (out.get("new_52w_high_flag", False)
                and (out.get("volume_zscore_50") or 0) > 1.5)
    out["breakout_flag"] = bool(breakout)

    # Distances from key MAs (signed %)
    close = out["close"]
    for ma_key in ("ma_50", "ma_150", "ma_200"):
        ma_val = out.get(ma_key)
        if ma_val and ma_val > 0 and not pd.isna(ma_val):
            out[f"dist_from_{ma_key}"] = (close / ma_val) - 1.0
        else:
            out[f"dist_from_{ma_key}"] = np.nan

    return out
