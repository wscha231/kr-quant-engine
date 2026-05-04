"""tools/dip_monitor_ml.py — Phase F.F4 ML-driven daily dip monitor.

Successor to tools/dip_monitor_simple.py. Same input contract (held
portfolio + ad-hoc tickers + dip thresholds), but routes detected dip
events through:
  1. kr_dip_features.prepare_dip_feature_panel() — 42 features
  2. dip_classifier_latest.cbm (CatBoost) — P(shake-out)
  3. Decision policy:
       P >= 0.65  -> HOLD          (likely shake-out)
       0.40-0.65  -> REDUCE_50      (uncertain)
       P < 0.40   -> SELL          (likely distribution)
       P missing  -> fall back to rule-based verdict from dip_monitor_simple

Persists outputs/dip_monitor_ml_<date>.{json,csv}.
Exit code 2 when at least one SELL recommendation -> Slack-pings.

When the dip classifier model is missing, the script falls back to
dip_monitor_simple's flow-based rules — keeping the workflow safe even
during the cold-start period before F3 has produced its first model.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402
from kr_helpers import log  # noqa: E402

DEFAULT_HOLD_THRESHOLD = 0.65
DEFAULT_SELL_THRESHOLD = 0.40


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ML-driven dip monitor")
    p.add_argument("--portfolio", default=None,
                   help="Portfolio CSV path.")
    p.add_argument("--tickers", default=None,
                   help="Extra comma-separated tickers.")
    p.add_argument("--hold-threshold", type=float, default=DEFAULT_HOLD_THRESHOLD,
                   help="P(shake-out) >= this -> HOLD.")
    p.add_argument("--sell-threshold", type=float, default=DEFAULT_SELL_THRESHOLD,
                   help="P(shake-out) <  this -> SELL.")
    p.add_argument("--dip-1d", type=float, default=-0.05)
    p.add_argument("--dip-5d", type=float, default=-0.10)
    p.add_argument("--lookback-days", type=int, default=120,
                   help="History to compute features (default 120 days).")
    return p.parse_args()


def _load_portfolio(path: Path) -> list[tuple[str, str, float]]:
    out: list[tuple[str, str, float]] = []
    if not path.exists():
        return out
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
        for _, r in df.iterrows():
            tk = str(r.get("ticker", "")).zfill(6)
            if not tk or tk == "000000":
                continue
            out.append((tk, str(r.get("name", "")),
                        float(r.get("market_cap") or 0.0)))
    except Exception as e:
        log(f"[ml-monitor] portfolio load fail: {e}", level="WARN")
    return out


def _maybe_load_classifier(models_dir: Path):
    cb_path = models_dir / "dip_classifier_latest.cbm"
    metrics_path = models_dir / "dip_classifier_latest_metrics.json"
    if not cb_path.exists():
        log(f"[ml-monitor] classifier missing at {cb_path}", level="WARN")
        return None, None
    try:
        from catboost import CatBoostClassifier
        model = CatBoostClassifier()
        model.load_model(str(cb_path))
    except Exception as e:
        log(f"[ml-monitor] classifier load fail: {e}", level="ERROR")
        return None, None
    feat_cols = None
    if metrics_path.exists():
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            feat_cols = meta.get("feature_cols")
        except Exception:
            pass
    return model, feat_cols


def _detect_dip_event(hist: pd.DataFrame,
                       dip_1d: float, dip_5d: float) -> int | None:
    """Return the index of the latest dip event, or None."""
    if hist.empty or "close" not in hist.columns or len(hist) < 6:
        return None
    closes = hist["close"].astype(float).values
    n = len(closes)
    last = n - 1
    if closes[last - 1] <= 0:
        return None
    ret_1d = closes[last] / closes[last - 1] - 1.0
    recent_5_peak = float(np.max(closes[max(0, last - 4): last + 1]))
    ret_5d = closes[last] / recent_5_peak - 1.0 if recent_5_peak > 0 else 0
    if ret_1d < dip_1d or ret_5d < dip_5d:
        return last
    return None


def main() -> int:
    args = parse_args()
    today = datetime.now()
    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load portfolio
    portfolio_path = Path(args.portfolio) if args.portfolio \
        else out_dir / "live_portfolio_latest.csv"
    held = _load_portfolio(portfolio_path)
    if args.tickers:
        for tk in args.tickers.split(","):
            tk = tk.strip().zfill(6)
            if tk and tk not in {t for t, *_ in held}:
                held.append((tk, "", 0.0))
    if not held:
        log("[ml-monitor] no tickers to scan", level="WARN")
        return 0

    # Classifier
    model, feat_cols = _maybe_load_classifier(DATA_ROOT / "models")
    use_ml = model is not None and feat_cols
    if not use_ml:
        log("[ml-monitor] falling back to rule-based dip_monitor_simple",
            level="WARN")
        # Delegate fully to the simple monitor in fallback mode
        import subprocess
        rc = subprocess.call([sys.executable,
                                str(PROJECT_ROOT / "tools" / "dip_monitor_simple.py")])
        return rc

    # Imports for feature path
    from kr_pykrx_client import fetch_ticker_history
    from kr_naver_flow import fetch_ticker_flow_naver
    from kr_dip_features import prepare_dip_feature_panel
    from kr_pit_universe import load_historical_mcap

    end = today.strftime("%Y%m%d")
    start = (today - timedelta(days=args.lookback_days * 2)).strftime("%Y%m%d")

    # Pre-load shared panels
    mcap_panel = load_historical_mcap(rebuild_if_missing=False)
    if not mcap_panel.empty:
        mcap_groups = {tk: g.reset_index(drop=True)
                        for tk, g in mcap_panel.groupby("ticker")}
        def mcap_lookup(tk, dt):
            g = mcap_groups.get(str(tk).zfill(6))
            if g is None or g.empty:
                return None
            idx = g["snapshot_date"].searchsorted(pd.Timestamp(dt), side="right") - 1
            if idx < 0:
                return None
            return float(g.iloc[idx]["market_cap"])
    else:
        mcap_lookup = lambda *a, **kw: None

    # Iterate tickers, collect dip events
    episode_rows = []
    ohlcv_cache: dict[str, pd.DataFrame] = {}
    flow_cache: dict[str, pd.DataFrame] = {}
    for i, (tk, name, mcap) in enumerate(held, 1):
        if i % 5 == 0:
            log(f"[ml-monitor] {i}/{len(held)}")
        try:
            hist = fetch_ticker_history(tk, start, end, refresh_days=1)
        except Exception as e:
            log(f"[ml-monitor] ohlcv fail {tk}: {e}", level="WARN")
            continue
        if hist.empty:
            continue
        hist = hist.sort_values("date").reset_index(drop=True)
        ohlcv_cache[tk] = hist
        dip_idx = _detect_dip_event(hist, args.dip_1d, args.dip_5d)
        if dip_idx is None:
            continue
        # Compose episode row matching mine_dip_episodes schema
        peak_close = float(hist["close"].iloc[max(0, dip_idx - 30): dip_idx].max() or hist["close"].iloc[dip_idx])
        episode_rows.append({
            "ticker": tk,
            "name": name,
            "dip_date": pd.Timestamp(hist["date"].iloc[dip_idx]).date(),
            "peak_date": pd.Timestamp(hist["date"].iloc[dip_idx]).date(),
            "dip_low_close": float(hist["close"].iloc[dip_idx]),
            "peak_close": peak_close,
            "drawdown_pct": float(hist["close"].iloc[dip_idx] / peak_close - 1.0)
                if peak_close > 0 else 0.0,
            "recovery_close": float(hist["close"].iloc[dip_idx]),
            "label": np.nan,
            "label_reason": "live",
            "days_to_label": None,
            "recovered": None,
            "market_cap_at_dip": mcap,
        })
        # Also pre-fetch flow
        try:
            flow_cache[tk] = fetch_ticker_flow_naver(tk, pages=2, refresh_days=1)
        except Exception:
            flow_cache[tk] = pd.DataFrame()

    if not episode_rows:
        log("[ml-monitor] no dip events today")
        payload = {"scan_date": today.strftime("%Y-%m-%d"), "n_alerts": 0,
                   "alerts": []}
        with open(out_dir / f"dip_monitor_ml_{today.strftime('%Y-%m-%d')}.json",
                  "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return 0

    episodes_df = pd.DataFrame(episode_rows)

    # Build features
    panel = prepare_dip_feature_panel(
        episodes=episodes_df,
        ohlcv_lookup=lambda tk: ohlcv_cache.get(tk, pd.DataFrame()),
        flow_lookup=lambda tk: flow_cache.get(tk, pd.DataFrame()),
        mcap_lookup=mcap_lookup,
        kospi_index=None,        # workflow can pass real KOSPI panel
        vkospi_panel=None,
        dart_event_panel=None,
        progress_every=5,
    )

    # Align to training feature_cols
    aligned = pd.DataFrame(0.0, index=panel.index, columns=feat_cols)
    shared = [c for c in feat_cols if c in panel.columns]
    aligned[shared] = panel[shared].astype(float).fillna(0.0)
    missing = [c for c in feat_cols if c not in panel.columns]
    if missing:
        log(f"[ml-monitor] {len(missing)}/{len(feat_cols)} features missing "
            f"(zero-filled): {missing[:5]}...", level="WARN")
    proba = model.predict_proba(aligned.values)[:, 1]

    # Decisions
    alerts = []
    n_sell = 0
    for k, row in panel.reset_index(drop=True).iterrows():
        p = float(proba[k])
        if p >= args.hold_threshold:
            verdict = "LIKELY_SHAKEOUT"
            action = "HOLD"
        elif p < args.sell_threshold:
            verdict = "LIKELY_DISTRIBUTION"
            action = "SELL"
            n_sell += 1
        else:
            verdict = "UNCERTAIN"
            action = "REDUCE_50"
        alerts.append({
            "ticker": row["ticker"],
            "name": row.get("name", ""),
            "dip_date": str(row["dip_date"]),
            "drawdown_pct": float(row["drawdown_pct"]),
            "p_shakeout": p,
            "verdict": verdict,
            "action": action,
            "market_cap_at_dip": float(row.get("market_cap_at_dip") or 0),
        })

    stamp = today.strftime("%Y-%m-%d")
    json_path = out_dir / f"dip_monitor_ml_{stamp}.json"
    csv_path = out_dir / f"dip_monitor_ml_{stamp}.csv"
    payload = {
        "scan_date": stamp,
        "n_tickers_scanned": len(held),
        "n_dip_events": len(alerts),
        "n_sell_alerts": n_sell,
        "n_hold_alerts": int(sum(1 for a in alerts if a["action"] == "HOLD")),
        "thresholds": {
            "hold": args.hold_threshold,
            "sell": args.sell_threshold,
        },
        "alerts": alerts,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    if alerts:
        pd.DataFrame(alerts).to_csv(csv_path, index=False, encoding="utf-8-sig")

    log(f"[ml-monitor] {len(alerts)} alerts ({n_sell} SELL / "
        f"{payload['n_hold_alerts']} HOLD)")
    return 2 if n_sell > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
