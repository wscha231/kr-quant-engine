"""Build and append a fast latest KR1000 scored snapshot.

This is the daily-readiness bridge when a full monthly scored-panel backfill is
too expensive. It builds the latest PIT KR1000 universe, computes cached
KOSPI200 relative strength where broad ticker caches are available, leaves
unavailable components neutral, and materializes a fresh scored_panel_v0 cache
so the broker check can evaluate the latest observable close.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, KR_ENGINE_REUSE_VERSION, kr1000_leader_alpha_cfg  # noqa: E402
from kr_helpers import log  # noqa: E402
from kr_universe import build_universe_snapshot  # noqa: E402
from kr1000_leader import build_kr1000_universe, compute_leader_scores  # noqa: E402
from tools.run_kr1000_backtest import (  # noqa: E402
    _benchmark_returns,
    _month_return,
)
from tools.run_kr1000_validation_gate import _infer_scored_panel_start_date  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build latest KR1000 scored snapshot")
    p.add_argument("--as-of", default=None,
                   help="Latest observable close date, YYYY-MM-DD. Default=previous KRX close.")
    p.add_argument("--run-date", default=None,
                   help="Calendar run date used to infer previous close when --as-of is omitted.")
    p.add_argument("--start-date", default=None,
                   help="Output scored-panel start date. Default=infer from latest cache.")
    p.add_argument("--base-panel", default=None,
                   help="Optional base scored_panel_v0 parquet/csv. Default=latest by mtime.")
    p.add_argument("--out", default=None,
                   help="Optional output parquet path. Default=feature_store/scored_panel_v0_<start>_<as_of>_<engine>.parquet.")
    p.add_argument("--refresh-days", type=int, default=3650)
    p.add_argument("--fetch-missing-prices", action="store_true",
                   help="Allow network/provider fetch when a covering ticker cache is missing.")
    p.add_argument("--no-rs", action="store_true",
                   help="Skip price/RS calculation and rank the latest snapshot by neutral score + liquidity.")
    p.add_argument("--classifier-mode", default="auto", choices=("auto", "none", "require"),
                   help=(
                       "Score the latest snapshot with classifier_latest.cbm for live readiness. "
                       "auto skips gracefully when unavailable; require fails if unavailable; none disables."
                   ))
    p.add_argument("--out-dir", default=None)
    return p.parse_args()


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype={"ticker": str})
    return pd.read_parquet(path)


def _latest_scored_panel_path() -> Path:
    files = sorted(
        (DATA_ROOT / "feature_store").glob("scored_panel_v0_*.parquet"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError(f"No scored_panel_v0_*.parquet found in {DATA_ROOT / 'feature_store'}")
    return files[0]


def _normalise_ticker(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def _yyyymmdd(day: pd.Timestamp) -> str:
    return pd.Timestamp(day).strftime("%Y%m%d")


def previous_krx_close_date(run_date: pd.Timestamp) -> pd.Timestamp:
    rd = pd.Timestamp(run_date).normalize()
    try:
        from kr_pykrx_client import fetch_business_days

        start = rd - pd.Timedelta(days=14)
        days = fetch_business_days(_yyyymmdd(start), _yyyymmdd(rd))
        days = [pd.Timestamp(d).normalize() for d in days if pd.Timestamp(d).normalize() < rd]
        if days:
            return max(days)
    except Exception:
        pass
    return (rd - pd.offsets.BDay(1)).normalize()


def _find_classifier() -> Path | None:
    models_dir = DATA_ROOT / "models"
    if not models_dir.exists():
        return None
    preferred = models_dir / "classifier_latest.cbm"
    if preferred.exists():
        return preferred
    candidates = sorted(
        list(models_dir.glob("p_mb_v*.cbm")) + list(models_dir.glob("classifier_*.cbm")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _load_classifier_feature_cols(classifier_path: Path) -> list[str] | None:
    models_dir = classifier_path.parent
    candidates = [models_dir / "classifier_latest_metrics.json"] + sorted(
        models_dir.glob("classifier_metrics_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        if not path.exists():
            continue
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        cols = meta.get("feature_cols")
        if cols and isinstance(cols, list):
            return [str(c) for c in cols]
    return None


def _carry_forward_classifier_features(
    latest: pd.DataFrame,
    base_panel: pd.DataFrame | None,
    as_of: pd.Timestamp,
    feature_cols: list[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fill missing latest classifier features from prior full-feature rows.

    This is a live-readiness bridge only. It never looks at rows dated on or
    after as_of, so repeated latest snapshot appends cannot feed themselves
    back into classifier inference.
    """
    out = latest.copy()
    if base_panel is None or base_panel.empty or not feature_cols:
        return out, {
            "carried_feature_count": 0,
            "carry_source_min_date": None,
            "carry_source_max_date": None,
        }
    if "rebalance_date" not in base_panel.columns or "ticker" not in base_panel.columns:
        return out, {
            "carried_feature_count": 0,
            "carry_source_min_date": None,
            "carry_source_max_date": None,
        }

    base = base_panel.copy()
    base["rebalance_date"] = pd.to_datetime(base["rebalance_date"], errors="coerce").dt.normalize()
    base["ticker"] = _normalise_ticker(base["ticker"])
    base = base[base["rebalance_date"].notna() & (base["rebalance_date"] < as_of.normalize())].copy()
    if base.empty:
        return out, {
            "carried_feature_count": 0,
            "carry_source_min_date": None,
            "carry_source_max_date": None,
        }

    available = [c for c in feature_cols if c in base.columns]
    if not available:
        return out, {
            "carried_feature_count": 0,
            "carry_source_min_date": None,
            "carry_source_max_date": None,
        }

    latest_cols = set(out.columns)
    last = (
        base.sort_values(["ticker", "rebalance_date"])
        .drop_duplicates("ticker", keep="last")[["ticker", "rebalance_date"] + available]
        .rename(columns={"rebalance_date": "_feature_carry_source_date"})
    )
    out["ticker"] = _normalise_ticker(out["ticker"])
    out = out.merge(last, on="ticker", how="left", suffixes=("", "_carry"))

    carried_count = 0
    for col in available:
        carry_col = f"{col}_carry"
        if col not in latest_cols:
            if col in out.columns and out[col].notna().any():
                carried_count += 1
            continue
        if carry_col not in out.columns:
            continue
        if col in out.columns:
            current = pd.to_numeric(out[col], errors="coerce")
            carried = pd.to_numeric(out[carry_col], errors="coerce")
            before = int(current.notna().sum())
            out[col] = current.fillna(carried)
            if int(out[col].notna().sum()) > before:
                carried_count += 1
        else:
            out[col] = pd.to_numeric(out[carry_col], errors="coerce")
            if out[col].notna().any():
                carried_count += 1
        out = out.drop(columns=[carry_col])

    src = pd.to_datetime(out.get("_feature_carry_source_date"), errors="coerce")
    meta = {
        "carried_feature_count": int(carried_count),
        "carry_source_min_date": str(src.min().date()) if src.notna().any() else None,
        "carry_source_max_date": str(src.max().date()) if src.notna().any() else None,
    }
    return out, meta


def _score_live_classifier(
    latest: pd.DataFrame,
    base_panel: pd.DataFrame | None,
    as_of: pd.Timestamp,
    mode: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = latest.copy()
    meta: dict[str, Any] = {
        "classifier_mode": mode,
        "classifier_applied": False,
        "classifier_model": None,
        "feature_cols": 0,
        "shared_features": 0,
        "missing_features": 0,
        "carried_feature_count": 0,
    }
    if mode == "none":
        return out, meta

    classifier_path = _find_classifier()
    if classifier_path is None:
        if mode == "require":
            raise FileNotFoundError(f"No classifier model found under {DATA_ROOT / 'models'}")
        log("[latest-snapshot] classifier not found -> keep p_pre_surge neutral", level="WARN")
        return out, meta

    feature_cols = _load_classifier_feature_cols(classifier_path)
    if not feature_cols:
        if mode == "require":
            raise RuntimeError(f"No feature_cols metadata found for {classifier_path}")
        log("[latest-snapshot] classifier feature metadata missing -> keep p_pre_surge neutral", level="WARN")
        return out, meta

    out, carry_meta = _carry_forward_classifier_features(out, base_panel, as_of, feature_cols)
    meta.update(carry_meta)
    try:
        from catboost import CatBoostClassifier

        model = CatBoostClassifier()
        model.load_model(str(classifier_path))
        aligned = pd.DataFrame(0.0, index=out.index, columns=feature_cols)
        shared = [c for c in feature_cols if c in out.columns]
        if shared:
            aligned[shared] = out[shared].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        proba = model.predict_proba(aligned.values)[:, 1]
    except Exception as exc:
        if mode == "require":
            raise
        log(f"[latest-snapshot] classifier scoring failed: {exc} -> keep p_pre_surge neutral", level="WARN")
        return out, meta

    out["p_pre_surge"] = proba
    out["pmb_live_model"] = classifier_path.name
    out["pmb_live_shared_features"] = len(shared)
    out["pmb_live_missing_features"] = len(feature_cols) - len(shared)
    out["pmb_live_carried_features"] = int(meta.get("carried_feature_count", 0))
    meta.update({
        "classifier_applied": True,
        "classifier_model": str(classifier_path),
        "feature_cols": len(feature_cols),
        "shared_features": len(shared),
        "missing_features": len(feature_cols) - len(shared),
        "p_pre_surge_min": float(np.nanmin(proba)) if len(proba) else None,
        "p_pre_surge_mean": float(np.nanmean(proba)) if len(proba) else None,
        "p_pre_surge_max": float(np.nanmax(proba)) if len(proba) else None,
    })
    log(
        f"[latest-snapshot] live classifier scored {len(out)} rows "
        f"({len(shared)}/{len(feature_cols)} shared, "
        f"{meta.get('carried_feature_count', 0)} carried)"
    )
    return out, meta


def _last_close(close: pd.Series, day: pd.Timestamp) -> float:
    sub = close.loc[close.index <= day]
    if sub.empty:
        return float("nan")
    return float(sub.iloc[-1])


def _read_exact_ticker_cache(ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    path = DATA_ROOT / "cache_pykrx" / f"ticker_{ticker}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.parquet"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        log(f"[latest-snapshot] exact ticker cache read fail {path.name}: {exc}", level="WARN")
        return pd.DataFrame()
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["ticker"] = ticker
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    return out[(out["date"] >= start) & (out["date"] <= end)].copy()


def _build_rs_features(candidates: pd.DataFrame, as_of: pd.Timestamp, refresh_days: int,
                       fetch_missing: bool) -> pd.DataFrame:
    from kr_pykrx_client import fetch_ticker_history

    out = candidates.copy()
    out["ticker"] = _normalise_ticker(out["ticker"])
    bench_rets, _ = _benchmark_returns(as_of, as_of, refresh_days)
    bench_row = bench_rets.iloc[-1].to_dict() if not bench_rets.empty else {}
    start = as_of - pd.DateOffset(months=8)
    exact_start = pd.Timestamp("2024-12-11")
    rows: list[dict[str, Any]] = []
    fetch_start = exact_start.strftime("%Y%m%d")
    fetch_end = as_of.strftime("%Y%m%d")
    for i, ticker in enumerate(out["ticker"].tolist(), start=1):
        if i == 1 or i % 100 == 0 or i == len(out):
            log(f"[latest-snapshot] RS {i}/{len(out)}")
        hist = _read_exact_ticker_cache(ticker, exact_start, as_of)
        if hist.empty and fetch_missing:
            hist = fetch_ticker_history(ticker, fetch_start, fetch_end, refresh_days=refresh_days)
        row: dict[str, Any] = {"ticker": ticker}
        if hist is None or hist.empty or "date" not in hist.columns or "close" not in hist.columns:
            for horizon in ("1m", "3m", "6m"):
                row[f"ret_{horizon}"] = np.nan
                row[f"rs_{horizon}"] = np.nan
            rows.append(row)
            continue
        h = hist.copy()
        h["date"] = pd.to_datetime(h["date"], errors="coerce").dt.normalize()
        h["close"] = pd.to_numeric(h["close"], errors="coerce")
        close = h.dropna(subset=["date", "close"]).sort_values("date").set_index("date")["close"]
        for horizon, months in (("1m", 1), ("3m", 3), ("6m", 6)):
            ret = _month_return(close, as_of, months)
            row[f"ret_{horizon}"] = ret
            bench = float(bench_row.get(f"bench_ret_{horizon}", np.nan))
            row[f"rs_{horizon}"] = ret - bench if np.isfinite(ret) and np.isfinite(bench) else np.nan
        row["last_price"] = _last_close(close, as_of)
        rows.append(row)
    rs = pd.DataFrame(rows)
    out = out.merge(rs, on="ticker", how="left")
    out["p0_momentum_score"] = (
        0.35 * pd.to_numeric(out.get("ret_6m"), errors="coerce").fillna(0.0)
        + 0.30 * pd.to_numeric(out.get("ret_3m"), errors="coerce").fillna(0.0)
        + 0.20 * pd.to_numeric(out.get("rs_3m"), errors="coerce").fillna(0.0)
        + 0.15 * pd.to_numeric(out.get("rs_6m"), errors="coerce").fillna(0.0)
    )
    out["p1_blended_score"] = out["p0_momentum_score"]
    return out


def build_latest_snapshot(
    as_of: pd.Timestamp,
    refresh_days: int,
    fetch_missing_prices: bool,
    no_rs: bool = False,
    base_panel: pd.DataFrame | None = None,
    classifier_mode: str = "auto",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cfg = kr1000_leader_alpha_cfg({"universe_name_lookup": False})
    snapshot = build_universe_snapshot(as_of, cfg=cfg)
    universe = build_kr1000_universe(as_of, cfg=cfg, snapshot=snapshot, include_discovery=False)
    if universe.empty:
        raise RuntimeError(f"KR1000 universe is empty at {as_of.date()}")
    if no_rs:
        latest = universe.copy()
        for horizon in ("1m", "3m", "6m"):
            latest[f"ret_{horizon}"] = 0.0
            latest[f"rs_{horizon}"] = 0.0
        latest["p0_momentum_score"] = 0.0
        latest["p1_blended_score"] = 0.0
    else:
        latest = _build_rs_features(universe, as_of, refresh_days, fetch_missing_prices)
    latest["rebalance_date"] = as_of.normalize()
    latest["eligible_final"] = latest.get("in_kr1000", True)
    latest["p_pre_surge"] = 0.0
    latest, classifier_meta = _score_live_classifier(latest, base_panel, as_of, classifier_mode)
    for col in (
        "flow_score", "technical_score", "quality_growth_score", "valuation_score",
        "theme_sector_score", "event_governance_score",
    ):
        if col not in latest.columns:
            latest[col] = 0.0
    if classifier_meta.get("classifier_applied"):
        cfg["score_profile"] = "pmb_pre_surge" if no_rs else "hybrid_pmb_rs"
    classifier_meta["score_profile"] = str(cfg.get("score_profile", "full"))
    latest = compute_leader_scores(latest, cfg)
    build_mode = "latest_fast_liquidity_only" if no_rs else "latest_fast_cached_rs"
    if classifier_meta.get("classifier_applied"):
        build_mode = f"{build_mode}_live_pmb"
    latest["snapshot_build_mode"] = build_mode
    latest["engine_version"] = KR_ENGINE_REUSE_VERSION
    return latest, classifier_meta


def main() -> int:
    args = parse_args()
    run_date = pd.Timestamp(args.run_date).normalize() if args.run_date else pd.Timestamp.today().normalize()
    as_of = pd.Timestamp(args.as_of).normalize() if args.as_of else previous_krx_close_date(run_date)
    base_path = Path(args.base_panel) if args.base_panel else _latest_scored_panel_path()
    base = _read_table(base_path)
    if "rebalance_date" not in base.columns:
        raise ValueError(f"base panel must include rebalance_date: {base_path}")
    base["rebalance_date"] = pd.to_datetime(base["rebalance_date"], errors="coerce").dt.normalize()
    base["ticker"] = _normalise_ticker(base["ticker"])

    start_date = args.start_date or _infer_scored_panel_start_date()
    out_path = Path(args.out) if args.out else DATA_ROOT / "feature_store" / (
        f"scored_panel_v0_{start_date}_{as_of.date()}_{KR_ENGINE_REUSE_VERSION}.parquet"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    latest, classifier_meta = build_latest_snapshot(
        as_of,
        args.refresh_days,
        args.fetch_missing_prices,
        no_rs=args.no_rs,
        base_panel=base,
        classifier_mode=args.classifier_mode,
    )
    combined = pd.concat([base, latest], ignore_index=True, sort=False)
    combined["rebalance_date"] = pd.to_datetime(combined["rebalance_date"], errors="coerce").dt.normalize()
    combined["ticker"] = _normalise_ticker(combined["ticker"])
    combined = combined.drop_duplicates(["rebalance_date", "ticker"], keep="last")
    combined = combined.sort_values(["rebalance_date", "ticker"]).reset_index(drop=True)
    combined.to_parquet(out_path, index=False)

    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "as_of": str(as_of.date()),
        "base_panel": str(base_path),
        "output_panel": str(out_path),
        "base_rows": int(len(base)),
        "latest_rows": int(len(latest)),
        "combined_rows": int(len(combined)),
        "combined_min_signal": str(combined["rebalance_date"].min().date()),
        "combined_max_signal": str(combined["rebalance_date"].max().date()),
        "engine_version": KR_ENGINE_REUSE_VERSION,
        "build_mode": str(latest["snapshot_build_mode"].iloc[0]) if "snapshot_build_mode" in latest.columns else "",
        "no_rs": bool(args.no_rs),
        "classifier": classifier_meta,
    }
    manifest_path = out_dir / f"kr1000_latest_scored_snapshot_{as_of.strftime('%Y%m%d')}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print("KR1000 latest scored snapshot")
    print(f"  as_of:       {as_of.date()}")
    print(f"  latest rows: {len(latest)}")
    print(f"  output:      {out_path}")
    print(f"  manifest:    {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
