"""kr_themes — Theme universe + Wyckoff lifecycle classifier (Phase G).

Public API
----------
load_themes_yaml(path=None) -> dict
    Reads themes.yaml + returns the parsed dict (themes + thresholds).

build_theme_member_panel(themes_cfg) -> pd.DataFrame
    Long-format ticker -> theme rows (one ticker may belong to multiple).

build_theme_strength_panel(theme_keys, themes_cfg, start, end,
                            kospi_index=None) -> pd.DataFrame
    Daily metrics × theme. Computes both equal-weight composite of leader
    stocks AND optional KRX-index strength for passive trackers.

classify_lifecycle_stage(metrics, thresholds) -> str
    "accumulate" | "markup" | "distribute" | "markdown" | "transition"

select_theme_leaders(theme_key, ohlcv_lookup, mcap_lookup, n=3, as_of=...)
    Within a theme, score and rank leader stocks for live entry.

CLI sanity test:
    py -3 kr_themes.py
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

from kr_config import DATA_ROOT, PROJECT_ROOT
from kr_helpers import log


_THEMES_YAML = PROJECT_ROOT / "themes.yaml"


def load_themes_yaml(path: Optional[Path] = None) -> dict:
    """Read themes.yaml. Falls back to project default."""
    p = path or _THEMES_YAML
    if not p.exists():
        log(f"[themes] themes.yaml not found at {p}", level="WARN")
        return {}
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg


def build_theme_member_panel(themes_cfg: dict) -> pd.DataFrame:
    """Convert themes block into long DataFrame: theme_key, ticker, period_start, period_end."""
    rows = []
    for key, meta in (themes_cfg.get("themes") or {}).items():
        for tk in (meta.get("tickers") or []):
            rows.append({
                "theme_key": key,
                "theme_name_kr": meta.get("name_kr", key),
                "ticker": str(tk).zfill(6),
                "period_start": meta.get("period_start"),
                "period_end": meta.get("period_end"),
            })
    return pd.DataFrame(rows)


def _theme_composite_returns(
    theme_tickers: list[str],
    start: str,
    end: str,
    weights: Optional[dict] = None,
) -> pd.DataFrame:
    """Equal-weighted composite daily return series for a theme's leaders.

    Returns DataFrame: date, return, n_active (active leader count today).
    """
    from kr_pykrx_client import fetch_ticker_history

    closes = {}
    for tk in theme_tickers:
        try:
            h = fetch_ticker_history(
                tk,
                pd.Timestamp(start).strftime("%Y%m%d"),
                pd.Timestamp(end).strftime("%Y%m%d"),
                refresh_days=30,
            )
            if not h.empty and "close" in h.columns:
                h = h.sort_values("date").set_index("date")["close"].astype(float)
                closes[tk] = h
        except Exception as e:
            log(f"[themes] history fail {tk}: {e}", level="WARN")
    if not closes:
        return pd.DataFrame(columns=["date", "return", "n_active"])
    panel = pd.concat(closes.values(), axis=1, keys=closes.keys()).sort_index()
    # Daily returns per ticker
    rets = panel.pct_change()
    # Active = ticker has data today
    n_active = panel.notna().sum(axis=1)
    composite = rets.mean(axis=1)   # equal-weighted, NaN-skipping
    out = pd.DataFrame({
        "date": composite.index,
        "return": composite.values,
        "n_active": n_active.values,
    }).reset_index(drop=True)
    return out


def _rolling_metrics(returns_series: pd.Series,
                       benchmark_series: Optional[pd.Series] = None) -> pd.DataFrame:
    """Compute rolling abs return + RS metrics."""
    out = pd.DataFrame({"date": returns_series.index})
    closes_implied = (1 + returns_series.fillna(0)).cumprod()
    out["abs_return_5d"] = closes_implied.pct_change(5).values
    out["abs_return_20d"] = closes_implied.pct_change(20).values
    out["abs_return_60d"] = closes_implied.pct_change(60).values
    out["abs_return_120d"] = closes_implied.pct_change(120).values
    out["abs_return_252d"] = closes_implied.pct_change(252).values

    if benchmark_series is not None:
        bench_implied = (1 + benchmark_series.reindex(returns_series.index)
                          .fillna(0)).cumprod()
        for d in (5, 20, 60, 252):
            br = bench_implied.pct_change(d)
            tr = closes_implied.pct_change(d)
            out[f"rs_kospi_{d}d"] = (tr - br).values
        # 20d RS z-score (over 60d window)
        rs20 = pd.Series(out["rs_kospi_20d"].values, index=returns_series.index)
        rs20_mu = rs20.rolling(60, min_periods=20).mean()
        rs20_sd = rs20.rolling(60, min_periods=20).std()
        out["rs_kospi_20d_zscore_60d"] = (
            (rs20 - rs20_mu) / rs20_sd.replace(0, np.nan)
        ).values
    return out


def build_theme_strength_panel(
    themes_cfg: dict,
    start: str,
    end: str,
    benchmark_ticker: str = "1028",   # KOSPI 200
    theme_keys: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Long-format daily theme strength panel.

    Columns:
      date, theme_key, theme_name_kr, return, n_active,
      abs_return_5d/20d/60d/120d/252d,
      rs_kospi_5d/20d/60d/252d, rs_kospi_20d_zscore_60d,
      stage  (lifecycle)
    """
    from kr_pykrx_client import fetch_index_ohlcv

    bench = fetch_index_ohlcv(
        benchmark_ticker,
        pd.Timestamp(start).strftime("%Y%m%d"),
        pd.Timestamp(end).strftime("%Y%m%d"),
        refresh_days=30,
    )
    if not bench.empty and "close" in bench.columns:
        bench = bench.sort_values("date").set_index("date")["close"].astype(float)
        bench_ret = bench.pct_change()
    else:
        bench_ret = None

    out_frames = []
    target_keys = theme_keys or list((themes_cfg.get("themes") or {}).keys())
    for key in target_keys:
        meta = themes_cfg["themes"].get(key)
        if not meta:
            continue
        tickers = [str(t).zfill(6) for t in meta.get("tickers") or []]
        if not tickers:
            continue
        comp = _theme_composite_returns(tickers, start, end)
        if comp.empty:
            continue
        comp = comp.set_index("date")
        metrics = _rolling_metrics(comp["return"], bench_ret)
        metrics = metrics.set_index("date")
        joined = comp.join(metrics, how="left").reset_index()
        joined["theme_key"] = key
        joined["theme_name_kr"] = meta.get("name_kr", key)
        out_frames.append(joined)
    if not out_frames:
        return pd.DataFrame()
    panel = pd.concat(out_frames, ignore_index=True)
    # Apply stage classifier per row
    thresholds = themes_cfg.get("lifecycle_thresholds") or {}
    panel["stage"] = panel.apply(
        lambda r: classify_lifecycle_stage(r.to_dict(), thresholds), axis=1
    )
    return panel


def classify_lifecycle_stage(metrics: dict, thresholds: dict) -> str:
    """Wyckoff-style 4 stages + transition.

    Rule-based for v1; replace with ML when sufficient labeled data.
    """
    me = thresholds.get("markup_entry", {})
    de = thresholds.get("distribute_entry", {})
    md = thresholds.get("markdown_entry", {})
    em = thresholds.get("emergency_exit", {})

    rs20 = metrics.get("rs_kospi_20d") or 0
    rs60 = metrics.get("rs_kospi_60d") or 0
    abs60 = metrics.get("abs_return_60d") or 0
    abs120 = metrics.get("abs_return_120d") or 0
    abs20 = metrics.get("abs_return_20d") or 0
    rs20_z = metrics.get("rs_kospi_20d_zscore_60d") or 0

    # Emergency exit
    if rs20_z <= em.get("rs_kospi_20d_zscore_max", -2.0):
        return "markdown"  # treat as immediate markdown
    # Markdown
    if (rs60 < md.get("rs_kospi_60d_max", -0.05)
            and abs120 < md.get("abs_return_120d_max", 0.0)):
        return "markdown"
    # Distribute
    if (rs60 < de.get("rs_kospi_60d_max", -0.02)
            and abs60 < de.get("abs_return_60d_max", 0.0)):
        return "distribute"
    # Markup
    if (rs20 > me.get("rs_kospi_20d_min", 0.05)
            and abs20 > me.get("abs_return_20d_min", 0.0)):
        return "markup"
    # Accumulate (low energy)
    if abs60 > -0.05 and abs60 < 0.05 and rs60 > -0.02:
        return "accumulate"
    return "transition"


# ---------------------------------------------------------------------------
# Stock selection within a theme
# ---------------------------------------------------------------------------
def select_theme_leaders(
    theme_key: str,
    themes_cfg: dict,
    as_of: pd.Timestamp,
    n: int = 3,
    benchmark_ticker: str = "1028",
) -> list[dict]:
    """Score and rank leader stocks of a theme as of `as_of`.

    Score = 0.4 * RS_kospi_60d + 0.3 * 52w-high proximity + 0.2 * vol_z_60d
            + 0.1 * mcap_score
    """
    from kr_pykrx_client import fetch_ticker_history

    meta = themes_cfg.get("themes", {}).get(theme_key)
    if not meta:
        return []
    tickers = [str(t).zfill(6) for t in meta.get("tickers") or []]
    if not tickers:
        return []
    end = pd.Timestamp(as_of).strftime("%Y%m%d")
    start = (pd.Timestamp(as_of) - pd.Timedelta(days=400)).strftime("%Y%m%d")

    rows = []
    for tk in tickers:
        try:
            h = fetch_ticker_history(tk, start, end, refresh_days=30)
        except Exception:
            continue
        if h.empty or "close" not in h.columns or len(h) < 60:
            continue
        h = h.sort_values("date")
        closes = h["close"].astype(float).values
        cur = closes[-1]
        if len(closes) >= 252:
            high_252 = float(closes[-252:].max())
            dist_high = (cur / high_252 - 1.0) if high_252 > 0 else np.nan
        else:
            dist_high = np.nan
        if len(closes) >= 60 and "volume" in h.columns:
            vols = h["volume"].astype(float).fillna(0).values
            vw = vols[-60:]
            mu = float(vw.mean())
            sd = float(vw.std(ddof=1)) if len(vw) > 1 else 0
            vol_z = (vols[-1] - mu) / sd if sd > 0 else 0
        else:
            vol_z = 0
        # RS 60d will be computed against benchmark separately
        rows.append({
            "ticker": tk,
            "name": "",
            "close_today": cur,
            "dist_from_52w_high": dist_high,
            "vol_z_60d": vol_z,
        })

    if not rows:
        return []
    # Compose score
    df = pd.DataFrame(rows)
    df["proximity_score"] = (1 + df["dist_from_52w_high"].fillna(-0.5)).clip(0, 1)
    # Normalize vol_z to 0..1 with sigmoid
    df["vol_z_score"] = (df["vol_z_60d"].fillna(0).rank(pct=True))
    df["composite_score"] = (
        0.5 * df["proximity_score"]
        + 0.3 * df["vol_z_score"]
        + 0.2 * 0.5  # placeholder for RS / mcap if you wire those in
    )
    df = df.sort_values("composite_score", ascending=False)
    return df.head(n).to_dict(orient="records")


# ---------------------------------------------------------------------------
# Sanity test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("kr_themes sanity test")
    print("=" * 60)
    cfg = load_themes_yaml()
    print(f"loaded {len(cfg.get('themes', {}))} themes")
    print(f"thresholds: {list(cfg.get('lifecycle_thresholds', {}).keys())}")
    print()
    print("Top 5 themes:")
    for k, v in list((cfg.get("themes") or {}).items())[:5]:
        print(f"  {k:30s} {v.get('name_kr')} ({len(v.get('tickers') or [])} tickers)")

    panel_path = DATA_ROOT / "data_pit" / "theme_strength_panel_2024-2026.parquet"
    if panel_path.exists():
        panel = pd.read_parquet(panel_path)
        print(f"\nFound theme_strength_panel: {len(panel)} rows")
    else:
        print("\nTheme strength panel not yet built; run "
              "tools/mine_theme_history.py --start 2024-01-01")
