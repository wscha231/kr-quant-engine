"""tools/run_phase_f_pipeline.py — Phase F end-to-end chain.

Mining (F1) -> Feature panel build (F2) -> Train classifier (F3) ->
Backtest validation (F5).  Skips F1 and just builds F2/F3/F5 from an
existing dip_episodes parquet by default (since mining is the slow step
and is usually run separately as a background job).

Usage
-----
After tools/mine_dip_episodes.py finishes:

    py -3 tools/run_phase_f_pipeline.py \
        --episodes data_pit/dip_episodes_20190101_20241231.parquet

Or with a fresh full mining (slow, ~2.5h):

    py -3 tools/run_phase_f_pipeline.py --mine \
        --start 2019-01-01 --end 2024-12-31

Outputs (all under DATA_ROOT)
-----------------------------
    data_pit/dip_features_panel_<stamp>.parquet
    models/dip_classifier_<stamp>.cbm + dip_classifier_latest.cbm
    models/dip_classifier_latest_metrics.json
    outputs/dip_strategy_backtest.json

Each step is idempotent and skippable via flags.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402
from kr_helpers import log  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase F pipeline runner")
    p.add_argument("--episodes", default=None,
                   help="Existing dip_episodes parquet to use (skips mining).")
    p.add_argument("--mine", action="store_true",
                   help="Run F1 mining as part of the pipeline.")
    p.add_argument("--start", default="2019-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--limit", type=int, default=None,
                   help="Mining ticker limit (debug or quick re-runs).")
    p.add_argument("--skip-features", action="store_true",
                   help="Reuse existing dip_features_panel parquet.")
    p.add_argument("--skip-train", action="store_true",
                   help="Reuse existing classifier.")
    p.add_argument("--skip-backtest", action="store_true")
    return p.parse_args()


def _run(cmd: list[str]) -> int:
    log(f"[chain] $ {' '.join(cmd)}")
    p = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return p.returncode


def _newest(pattern: str, base: Path) -> Path | None:
    if not base.exists():
        return None
    matches = sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime,
                     reverse=True)
    return matches[0] if matches else None


def main() -> int:
    args = parse_args()

    # F1 — mining (optional)
    episodes_path = Path(args.episodes) if args.episodes else None
    if args.mine:
        log("[chain] F1 mining...")
        cmd = [sys.executable, str(PROJECT_ROOT / "tools" / "mine_dip_episodes.py"),
                "--start", args.start, "--end", args.end]
        if args.limit:
            cmd += ["--limit", str(args.limit)]
        rc = _run(cmd)
        if rc != 0:
            log(f"[chain] mining failed rc={rc}", level="ERROR")
            return rc
        episodes_path = _newest("dip_episodes_*.parquet",
                                  DATA_ROOT / "data_pit")
    if episodes_path is None or not episodes_path.exists():
        episodes_path = _newest("dip_episodes_*.parquet",
                                  DATA_ROOT / "data_pit")
    if episodes_path is None or not episodes_path.exists():
        log("[chain] no episodes parquet — run with --mine or specify "
            "--episodes", level="ERROR")
        return 1
    log(f"[chain] using episodes: {episodes_path}")

    # F2 — feature panel
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    feat_path = DATA_ROOT / "data_pit" / f"dip_features_panel_{stamp}.parquet"
    if args.skip_features:
        prev = _newest("dip_features_panel_*.parquet",
                       DATA_ROOT / "data_pit")
        if prev is None:
            log("[chain] no existing feature panel; running F2", level="WARN")
            args.skip_features = False
        else:
            feat_path = prev
            log(f"[chain] reusing feature panel: {feat_path}")
    if not args.skip_features:
        log("[chain] F2 building feature panel...")
        episodes = pd.read_parquet(episodes_path)
        log(f"[chain]   episodes: {len(episodes)} rows")

        # Build the feature panel directly (no separate CLI tool yet — we
        # reuse the public API).
        from kr_dip_features import prepare_dip_feature_panel
        from kr_pykrx_client import fetch_ticker_history
        from kr_naver_flow import fetch_ticker_flow_naver
        from kr_pit_universe import load_historical_mcap

        # Pre-fetch ohlcv per unique ticker (cache should be populated already)
        unique_tk = episodes["ticker"].astype(str).str.zfill(6).unique().tolist()
        log(f"[chain]   {len(unique_tk)} unique tickers in episodes")
        ohlcv_cache: dict[str, pd.DataFrame] = {}
        flow_cache: dict[str, pd.DataFrame] = {}
        for k, tk in enumerate(unique_tk, 1):
            if k % 100 == 0:
                log(f"[chain]   fetching ohlcv {k}/{len(unique_tk)}")
            try:
                ohlcv_cache[tk] = fetch_ticker_history(
                    tk,
                    pd.Timestamp(args.start).strftime("%Y%m%d"),
                    pd.Timestamp(args.end).strftime("%Y%m%d"),
                    refresh_days=30,
                )
            except Exception as e:
                log(f"[chain]   ohlcv {tk} fail: {e}", level="WARN")
                ohlcv_cache[tk] = pd.DataFrame()

        # Naver flow (slow per-ticker; skip if no internet)
        for k, tk in enumerate(unique_tk, 1):
            if k % 50 == 0:
                log(f"[chain]   naver flow {k}/{len(unique_tk)}")
            try:
                flow_cache[tk] = fetch_ticker_flow_naver(
                    tk, pages=10, refresh_days=30,
                )
            except Exception:
                flow_cache[tk] = pd.DataFrame()

        # mcap lookup
        mcap_panel = load_historical_mcap(rebuild_if_missing=False)
        if mcap_panel.empty:
            mcap_lookup = lambda tk, dt: None
        else:
            groups = {tk: g.reset_index(drop=True)
                      for tk, g in mcap_panel.groupby("ticker")}
            def mcap_lookup(tk, dt):
                g = groups.get(str(tk).zfill(6))
                if g is None or g.empty:
                    return None
                idx = g["snapshot_date"].searchsorted(
                    pd.Timestamp(dt), side="right") - 1
                if idx < 0:
                    return None
                return float(g.iloc[idx]["market_cap"])

        feat_panel = prepare_dip_feature_panel(
            episodes,
            ohlcv_lookup=lambda t: ohlcv_cache.get(t, pd.DataFrame()),
            flow_lookup=lambda t: flow_cache.get(t, pd.DataFrame()),
            mcap_lookup=mcap_lookup,
        )
        feat_panel.to_parquet(feat_path, index=False)
        log(f"[chain]   wrote {feat_path}")

    # F3 — train
    if not args.skip_train:
        log("[chain] F3 training classifier...")
        rc = _run([
            sys.executable, str(PROJECT_ROOT / "tools" / "train_dip_classifier.py"),
            "--feature-panel", str(feat_path),
            "--n-folds", "5",
            "--embargo-days", "60",
        ])
        if rc != 0:
            log(f"[chain] training failed rc={rc}", level="ERROR")
            return rc

    # F5 — backtest
    if not args.skip_backtest:
        log("[chain] F5 backtest...")
        rc = _run([
            sys.executable, str(PROJECT_ROOT / "tools" / "backtest_dip_strategy.py"),
            "--feature-panel", str(feat_path),
        ])
        if rc != 0:
            log(f"[chain] backtest failed rc={rc}", level="WARN")

    log("[chain] done.")
    log(f"[chain] feature_panel: {feat_path}")
    metrics = DATA_ROOT / "models" / "dip_classifier_latest_metrics.json"
    if metrics.exists():
        with open(metrics, "r", encoding="utf-8") as f:
            m = json.load(f)
        log(f"[chain] mean blend AUC: {m.get('auc_blend_mean')}")
    bt = DATA_ROOT / "outputs" / "dip_strategy_backtest.json"
    if bt.exists():
        log(f"[chain] backtest report: {bt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
