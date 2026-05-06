"""tools/discover_themes_clustering.py — Phase G P6 unsupervised theme discovery.

No hardcoded theme/sector mapping. Clusters tickers by their normalized
180-day return correlation, surfacing the actual co-moving groups in
the market — including names and sectors that aren't in themes.yaml.

Pipeline
--------
1. Pull cached daily closes for every PIT-eligible mid-large-cap ticker.
2. Compute log returns over the look-back window (default 180 trading days).
3. Drop tickers with > 30% missing data.
4. KMeans (or DBSCAN) on a centered/scaled return matrix.
5. For each cluster, compute:
     - top 5 tickers (by recent RS)
     - centroid 60d / 20d return
     - centroid stage (Wyckoff)
     - implied "theme name" via mode of existing themes.yaml memberships,
       or "<sector_count>×stocks_unnamed" placeholder.
6. Print cluster leaderboard + write outputs/discovered_themes_<date>.json.

Usage
-----
    py -3 tools/discover_themes_clustering.py [--lookback 180] [--n-clusters 20]
                                              [--min-mcap-krw 5e11]
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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Theme discovery via clustering")
    p.add_argument("--lookback", type=int, default=180,
                   help="Trading-day lookback for return matrix.")
    p.add_argument("--n-clusters", type=int, default=20,
                   help="KMeans cluster count.")
    p.add_argument("--min-mcap-krw", type=float, default=5e11,
                   help="Mcap floor for ticker eligibility.")
    p.add_argument("--max-tickers", type=int, default=600,
                   help="Cap on universe size (top by mcap).")
    p.add_argument("--method", default="kmeans",
                   choices=("kmeans", "dbscan"),
                   help="Clustering method.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    today = datetime.now()

    # Universe
    from kr_pit_universe import (
        fetch_listing_at_date,
        load_historical_mcap,
    )
    rd = pd.Timestamp(today).normalize()
    universe = fetch_listing_at_date(rd, name_lookup=True)
    if universe.empty:
        log("[cluster] empty PIT universe", level="ERROR")
        return 1
    log(f"[cluster] PIT universe: {len(universe)} tickers")

    # Mcap filter
    if "market_cap" in universe.columns and args.min_mcap_krw > 0:
        universe = universe[
            universe["market_cap"].fillna(0).astype(float) >= args.min_mcap_krw
        ].copy()
    universe = universe.sort_values("market_cap", ascending=False).head(args.max_tickers)
    log(f"[cluster] after mcap filter: {len(universe)} tickers")
    tickers = universe["ticker"].astype(str).str.zfill(6).tolist()
    name_map = dict(zip(
        universe["ticker"].astype(str).str.zfill(6),
        universe["name"].astype(str),
    ))

    # Pull closes
    from kr_pykrx_client import fetch_ticker_history
    end = today.strftime("%Y%m%d")
    start = (today - timedelta(days=args.lookback * 2)).strftime("%Y%m%d")
    closes_dict = {}
    for k, tk in enumerate(tickers, 1):
        if k % 100 == 0:
            log(f"[cluster] fetched {k}/{len(tickers)}")
        try:
            h = fetch_ticker_history(tk, start, end, refresh_days=30)
            if h.empty or "close" not in h.columns or len(h) < args.lookback // 2:
                continue
            closes_dict[tk] = (
                h.sort_values("date")
                 .set_index(pd.to_datetime(h["date"]))["close"].astype(float)
            )
        except Exception:
            continue
    if not closes_dict:
        log("[cluster] no closes", level="ERROR")
        return 2
    log(f"[cluster] {len(closes_dict)} tickers with usable history")

    # Build return matrix
    panel = pd.concat(closes_dict.values(), axis=1, keys=closes_dict.keys()).sort_index()
    panel = panel.tail(args.lookback + 5)
    rets = panel.pct_change().dropna(how="all")
    # Drop tickers with >30% NaN
    nan_rate = rets.isna().mean()
    keep = nan_rate[nan_rate <= 0.30].index
    rets = rets[keep].fillna(0)
    log(f"[cluster] return matrix: {rets.shape}")

    # Standardize per ticker (column)
    X = rets.values.T   # rows = tickers, cols = days
    mu = X.mean(axis=1, keepdims=True)
    sd = X.std(axis=1, ddof=1, keepdims=True) + 1e-9
    Xs = (X - mu) / sd

    # Cluster
    if args.method == "kmeans":
        from sklearn.cluster import KMeans
        km = KMeans(n_clusters=args.n_clusters, random_state=42, n_init=10)
        labels = km.fit_predict(Xs)
    else:
        from sklearn.cluster import DBSCAN
        db = DBSCAN(eps=0.5, min_samples=5)
        labels = db.fit_predict(Xs)

    # Build cluster summary
    df = pd.DataFrame({
        "ticker": list(rets.columns),
        "name": [name_map.get(t, "") for t in rets.columns],
        "cluster": labels,
    })
    # Add recent metrics
    cum60 = (1 + rets.tail(60)).prod() - 1
    cum20 = (1 + rets.tail(20)).prod() - 1
    df["return_60d"] = df["ticker"].map(cum60.to_dict())
    df["return_20d"] = df["ticker"].map(cum20.to_dict())
    df["mcap"] = df["ticker"].map(
        dict(zip(universe["ticker"].astype(str).str.zfill(6),
                  universe["market_cap"].astype(float)))
    )
    # Try to map ticker -> known theme
    from kr_themes import load_themes_yaml
    cfg = load_themes_yaml()
    tk_to_theme = {}
    for theme_key, meta in (cfg.get("themes") or {}).items():
        for tk in (meta.get("tickers") or []):
            tk_to_theme[str(tk).zfill(6)] = meta.get("name_kr", theme_key)
    df["named_theme"] = df["ticker"].map(tk_to_theme)

    # Cluster summary
    summary = []
    for cid, sub in df.groupby("cluster"):
        if cid < 0:
            continue   # DBSCAN noise
        leaders = sub.sort_values("return_60d", ascending=False).head(5)
        themes_in_cluster = sub["named_theme"].dropna().value_counts()
        primary_theme = (themes_in_cluster.index[0]
                          if len(themes_in_cluster) else "unnamed")
        summary.append({
            "cluster_id": int(cid),
            "n_members": int(len(sub)),
            "primary_named_theme": primary_theme,
            "named_themes_distribution": themes_in_cluster.to_dict(),
            "centroid_return_60d": float(sub["return_60d"].mean()),
            "centroid_return_20d": float(sub["return_20d"].mean()),
            "median_mcap_조": float(sub["mcap"].median() / 1e12)
                if "mcap" in sub.columns else None,
            "top_5_leaders": leaders[
                ["ticker", "name", "return_60d", "return_20d", "named_theme"]
            ].to_dict(orient="records"),
        })
    summary.sort(key=lambda x: -x["centroid_return_60d"])

    # Print
    print()
    print("=" * 88)
    print(f"  Discovered theme clusters - {today.strftime('%Y-%m-%d')}")
    print("=" * 88)
    for i, s in enumerate(summary[:15], 1):
        print(f"\n  #{i}: cluster {s['cluster_id']} "
              f"({s['n_members']} members, primary={s['primary_named_theme']})")
        print(f"     centroid 60d={s['centroid_return_60d']:+.2%}  "
              f"20d={s['centroid_return_20d']:+.2%}  "
              f"median mcap={s['median_mcap_조'] or 0:.2f}조")
        for r in s["top_5_leaders"][:5]:
            tag = f"[{r['named_theme']}]" if r.get("named_theme") else "[unnamed]"
            name = (r.get("name") or "")[:14]
            print(f"     {r['ticker']} {name:<14} 60d={r['return_60d']:+.2%}  {tag}")

    # Persist
    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"discovered_themes_{today.strftime('%Y-%m-%d')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "date": today.strftime("%Y-%m-%d"),
            "lookback_days": args.lookback,
            "n_clusters": args.n_clusters,
            "method": args.method,
            "min_mcap_krw": args.min_mcap_krw,
            "n_universe": len(universe),
            "n_used": len(rets.columns),
            "clusters": summary,
        }, f, indent=2, ensure_ascii=False, default=str)
    log(f"[cluster] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
