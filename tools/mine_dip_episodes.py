"""tools/mine_dip_episodes.py — Phase F.F1 dip episode miner.

Walks through a multi-year window of cached daily OHLCV for every PIT-
eligible ticker, detects dip events, and applies the López de Prado
triple-barrier method to label each as shake-out (1) / distribution (0)
/ ambiguous (NaN).

Output: data/dip_episodes/dip_events_<start>_<end>.parquet
        with columns
          ticker, dip_date, peak_date, dip_low_close, peak_close,
          drawdown_pct, recovery_close, label, label_reason,
          days_to_label, recovered, exchange, market_cap_at_dip

Trigger
-------
A "dip" starts on day t when (close_t / close_{t-1} - 1) < dip_1d_threshold
OR rolling-5d drawdown from peak < dip_5d_threshold.  Once triggered we
freeze the local peak price (max close of preceding 30 days) and look
forward.

Triple-barrier
--------------
Within `outcome_window_days` (default 30):
  Upper barrier (recovery)   : close >= peak_close * (1 - upper_pct)
                                -> label = 1 (shake-out)
  Lower barrier (further dd) : close <= dip_low_close * (1 - lower_pct)
                                -> label = 0 (distribution)
  Time barrier               : neither hit by t + window_days
                                -> label = NaN (ambiguous, used for
                                   rule-validation only)

Caching
-------
fetch_ticker_history is already disk-cached per (ticker, start, end)
under cache_pykrx/, so re-runs are fast.

Run
---
    py -3 tools/mine_dip_episodes.py \
        --start 2019-01-01 --end 2024-12-31 \
        --tickers-from data_pit/listed_history.parquet
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402
from kr_helpers import log  # noqa: E402


# Defaults
DEFAULT_DIP_1D = -0.05
DEFAULT_DIP_5D = -0.10
DEFAULT_PEAK_LOOKBACK = 30        # business days for local-peak window
DEFAULT_OUTCOME_WINDOW = 30       # business days forward for triple barrier
DEFAULT_UPPER_PCT = 0.05          # within 5pct of peak = shake-out
DEFAULT_LOWER_PCT = 0.20          # additional -20pct from dip low = distribution
DEFAULT_MIN_MCAP_KRW = 5e11       # 5,000억 (consistent with picks generator)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dip episode miner")
    p.add_argument("--start", default="2019-01-01",
                   help="Start date YYYY-MM-DD (default 2019-01-01).")
    p.add_argument("--end", default=None,
                   help="End date YYYY-MM-DD (default = today - 60d so we have "
                        "enough forward window to label).")
    p.add_argument("--tickers", default=None,
                   help="Comma-separated explicit ticker list (debug).")
    p.add_argument("--tickers-from-listed-history", action="store_true",
                   default=True,
                   help="Source tickers from data_pit/listed_history.parquet "
                        "(default: True).")
    p.add_argument("--min-mcap-krw", type=float, default=DEFAULT_MIN_MCAP_KRW,
                   help="Minimum mcap at dip date (default 5,000억).")
    p.add_argument("--dip-1d", type=float, default=DEFAULT_DIP_1D)
    p.add_argument("--dip-5d", type=float, default=DEFAULT_DIP_5D)
    p.add_argument("--peak-lookback", type=int, default=DEFAULT_PEAK_LOOKBACK)
    p.add_argument("--outcome-window", type=int, default=DEFAULT_OUTCOME_WINDOW)
    p.add_argument("--upper-pct", type=float, default=DEFAULT_UPPER_PCT)
    p.add_argument("--lower-pct", type=float, default=DEFAULT_LOWER_PCT)
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N tickers (debug).")
    p.add_argument("--out", default=None,
                   help="Output path (default data_pit/dip_episodes_...).")
    return p.parse_args()


def _load_tickers(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        return [t.strip().zfill(6) for t in args.tickers.split(",") if t.strip()]
    listed_history = DATA_ROOT / "data_pit" / "listed_history.parquet"
    if not listed_history.exists():
        log("[mine] listed_history not found, falling back to PYKRX_FETCH",
            level="WARN")
        from kr_pit_universe import build_listed_history_from_cache
        build_listed_history_from_cache(save=True)
    df = pd.read_parquet(listed_history)
    tickers = df["ticker"].astype(str).str.zfill(6).unique().tolist()
    log(f"[mine] {len(tickers)} tickers from listed_history")
    return tickers


def _detect_episodes_for_ticker(
    ticker: str, hist: pd.DataFrame, args: argparse.Namespace,
    mcap_at_dip_lookup,
) -> list[dict]:
    """Apply trigger + triple-barrier labelling to a single ticker history."""
    if hist.empty or "close" not in hist.columns or len(hist) < 60:
        return []
    h = hist.sort_values("date").reset_index(drop=True)
    closes = h["close"].astype(float).values
    dates = pd.to_datetime(h["date"]).values
    n = len(h)
    if n < args.peak_lookback + args.outcome_window + 5:
        return []

    out: list[dict] = []
    cooldown_until = -1
    for i in range(args.peak_lookback, n - 1):
        if i <= cooldown_until:
            continue
        prev = closes[i - 1]
        if prev <= 0:
            continue
        ret_1d = closes[i] / prev - 1.0
        # 5-day drawdown from local 5-day peak
        recent_5_peak = max(closes[max(0, i - 4): i + 1])
        ret_5d = closes[i] / recent_5_peak - 1.0 if recent_5_peak > 0 else 0
        if ret_1d > args.dip_1d and ret_5d > args.dip_5d:
            continue
        # Trigger fired. Freeze the local peak (preceding peak_lookback window).
        peak_window = closes[max(0, i - args.peak_lookback): i]
        if len(peak_window) == 0:
            continue
        peak_close = float(peak_window.max())
        peak_idx_offset = int(np.argmax(peak_window))
        peak_idx = max(0, i - args.peak_lookback) + peak_idx_offset
        peak_date = pd.Timestamp(dates[peak_idx])
        dip_low_close = float(closes[i])
        # mcap filter (from dip-date snapshot)
        mcap_ok = True
        mcap = float("nan")
        if mcap_at_dip_lookup is not None:
            mcap = mcap_at_dip_lookup(ticker, dates[i])
            if mcap is None or pd.isna(mcap) or mcap < args.min_mcap_krw:
                mcap_ok = False
        if not mcap_ok:
            cooldown_until = i + 5
            continue
        # Triple barrier forward
        upper = peak_close * (1.0 - args.upper_pct)   # close >= upper -> shake-out
        lower = dip_low_close * (1.0 - args.lower_pct)
        label = np.nan
        label_reason = "ambiguous"
        days_to_label = None
        recovered = False
        end_j = min(n - 1, i + args.outcome_window)
        for j in range(i + 1, end_j + 1):
            cj = closes[j]
            if cj >= upper:
                label = 1
                label_reason = "recovery_5pct_to_peak"
                days_to_label = j - i
                recovered = True
                break
            if cj <= lower:
                label = 0
                label_reason = f"further_drawdown_{int(args.lower_pct*100)}pct"
                days_to_label = j - i
                recovered = False
                break
        recovery_close = float(closes[min(end_j, i + args.outcome_window)])
        out.append({
            "ticker": ticker,
            "dip_date": pd.Timestamp(dates[i]).date(),
            "peak_date": peak_date.date(),
            "dip_low_close": dip_low_close,
            "peak_close": peak_close,
            "drawdown_pct": float(dip_low_close / peak_close - 1.0),
            "recovery_close": recovery_close,
            "label": label,
            "label_reason": label_reason,
            "days_to_label": days_to_label,
            "recovered": recovered,
            "market_cap_at_dip": mcap,
        })
        # Cooldown: avoid re-triggering on every day of the same drawdown
        cooldown_until = i + max(args.peak_lookback // 2,
                                   days_to_label or args.outcome_window)
    return out


def main() -> int:
    args = parse_args()
    end = (args.end or
            (pd.Timestamp.today() - pd.Timedelta(days=60)).strftime("%Y-%m-%d"))
    args.end = end
    log(f"[mine] window {args.start} -> {end}")
    log(f"[mine] dip 1d={args.dip_1d}, 5d={args.dip_5d}, "
        f"peak_lookback={args.peak_lookback}d, outcome={args.outcome_window}d, "
        f"upper={args.upper_pct}, lower={args.lower_pct}")

    # Load tickers
    tickers = _load_tickers(args)
    if args.limit:
        tickers = tickers[: args.limit]
        log(f"[mine] limit applied -> {len(tickers)} tickers")

    # PIT mcap lookup (uses historical_mcap.parquet)
    try:
        from kr_pit_universe import load_historical_mcap
        mcap_panel = load_historical_mcap(rebuild_if_missing=True)
        if mcap_panel.empty:
            mcap_panel = None
        else:
            mcap_panel = mcap_panel.sort_values(["ticker", "snapshot_date"])
    except Exception as e:
        log(f"[mine] mcap panel load fail: {e}", level="WARN")
        mcap_panel = None

    if mcap_panel is None:
        def mcap_lookup(tk, dt):
            return None
    else:
        # group + searchsorted for speed
        gb = mcap_panel.groupby("ticker")
        groups = {tk: g.reset_index(drop=True) for tk, g in gb}
        def mcap_lookup(tk, dt):
            g = groups.get(str(tk).zfill(6))
            if g is None or g.empty:
                return None
            idx = g["snapshot_date"].searchsorted(pd.Timestamp(dt), side="right") - 1
            if idx < 0:
                return None
            return float(g.iloc[idx]["market_cap"])

    from kr_pykrx_client import fetch_ticker_history

    rows: list[dict] = []
    t0 = time.time()
    for k, tk in enumerate(tickers, 1):
        if k % 50 == 0:
            elapsed = time.time() - t0
            log(f"[mine] {k}/{len(tickers)} tickers, "
                f"{len(rows)} events, {elapsed:.0f}s elapsed")
        try:
            hist = fetch_ticker_history(
                tk,
                pd.Timestamp(args.start).strftime("%Y%m%d"),
                pd.Timestamp(end).strftime("%Y%m%d"),
                refresh_days=30,
            )
        except Exception as e:
            log(f"[mine] history fail {tk}: {e}", level="WARN")
            continue
        events = _detect_episodes_for_ticker(tk, hist, args, mcap_lookup)
        rows.extend(events)

    if not rows:
        log("[mine] no events detected", level="WARN")
        return 1
    df = pd.DataFrame(rows)
    log(f"[mine] {len(df)} events; "
        f"shake-out={int((df['label'] == 1).sum())}, "
        f"distribution={int((df['label'] == 0).sum())}, "
        f"ambiguous={int(df['label'].isna().sum())}")

    # Persist
    out_dir = DATA_ROOT / "data_pit"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.out:
        path = Path(args.out)
    else:
        s = pd.Timestamp(args.start).strftime("%Y%m%d")
        e = pd.Timestamp(end).strftime("%Y%m%d")
        path = out_dir / f"dip_episodes_{s}_{e}.parquet"
    try:
        df.to_parquet(path, index=False)
        log(f"[mine] wrote {path}")
    except Exception as e:
        log(f"[mine] write fail: {e}", level="ERROR")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
