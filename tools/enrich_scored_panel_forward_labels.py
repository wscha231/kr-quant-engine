"""Enrich an existing scored_panel_v0 with forward target labels.

This is a fast bridge for P_MB risk-sleeve training. It avoids rebuilding all
features when the scored panel already exists but lacks the target-only
`forward_return_1m` / `forward_min_return_1m` columns.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, PHASE4_PMB_TARGET_COLUMNS, kr1000_leader_alpha_cfg  # noqa: E402
from kr_helpers import log  # noqa: E402
from kr_pipeline import add_forward_return_labels  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Add P_MB forward target labels to a scored panel")
    p.add_argument(
        "--panel",
        default=None,
        help="Input scored_panel parquet/csv. Default=latest feature_store/scored_panel_v0_*.parquet.",
    )
    p.add_argument(
        "--price-panel",
        default=None,
        help="Optional price panel parquet/csv with date,ticker,close. Default=use ticker history caches/provider.",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Output parquet/csv. Default=<panel stem>_forward_labels<suffix>.",
    )
    p.add_argument(
        "--audit-json",
        default=None,
        help="Audit JSON output. Default=<out>.forward_labels.json.",
    )
    p.add_argument("--start", default=None, help="Only fill labels for rows on/after this rebalance_date.")
    p.add_argument("--end", default=None, help="Only fill labels for rows on/before this rebalance_date.")
    p.add_argument(
        "--label-as-of",
        default=None,
        help="Latest date with fully observable forward-label data. Default=today.",
    )
    p.add_argument("--horizon-months", type=int, default=None, help="Forward horizon in months. Default=config.")
    p.add_argument("--refresh-days", type=int, default=None, help="Ticker history cache refresh days. Default=config.")
    p.add_argument(
        "--no-cache-preload",
        action="store_true",
        help="Do not preload covering ticker histories from cache_pykrx before labeling.",
    )
    p.add_argument(
        "--fetch-missing-prices",
        action="store_true",
        help="Allow provider fetch for tickers missing from cache. Default is cache-only for this bridge.",
    )
    p.add_argument(
        "--fail-if-no-fill",
        action="store_true",
        help="Exit non-zero if no newly label-ready rows are produced.",
    )
    return p.parse_args()


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype={"ticker": str})
    return pd.read_parquet(path)


def _write_table(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        df.to_csv(path, index=False)
    else:
        df.to_parquet(path, index=False)


def _latest_scored_panel_path() -> Path:
    fs = DATA_ROOT / "feature_store"
    files = sorted(
        [p for p in fs.glob("scored_panel_v0_*.parquet") if "mini" not in p.name],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError(f"No scored_panel_v0_*.parquet found in {fs}")
    return files[0]


def _default_out_path(panel_path: Path) -> Path:
    if panel_path.stem.endswith("_forward_labels"):
        return panel_path
    return panel_path.with_name(f"{panel_path.stem}_forward_labels{panel_path.suffix}")


def _mask_by_date(panel: pd.DataFrame, start: str | None, end: str | None) -> pd.Series:
    if "rebalance_date" not in panel.columns:
        return pd.Series(False, index=panel.index)
    dates = pd.to_datetime(panel["rebalance_date"], errors="coerce").dt.normalize()
    mask = dates.notna()
    if start:
        mask &= dates >= pd.Timestamp(start).normalize()
    if end:
        mask &= dates <= pd.Timestamp(end).normalize()
    return mask


def _label_ready(df: pd.DataFrame) -> pd.Series:
    for col in PHASE4_PMB_TARGET_COLUMNS:
        if col not in df.columns:
            return pd.Series(False, index=df.index)
    return df[list(PHASE4_PMB_TARGET_COLUMNS)].notna().all(axis=1)


def _label_as_of(cfg: dict[str, Any] | None = None) -> pd.Timestamp:
    cfg = cfg or {}
    raw = cfg.get("forward_label_as_of_date")
    return pd.Timestamp(raw).normalize() if raw else pd.Timestamp.today().normalize()


def _horizon_months(cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg or {}
    return int(cfg.get("forward_label_horizon_months", 1) or 1)


def _clear_incomplete_forward_labels(panel: pd.DataFrame, cfg: dict[str, Any] | None = None) -> int:
    """Clear target labels whose full forward horizon is not observable."""
    if "rebalance_date" not in panel.columns:
        return 0
    for col in PHASE4_PMB_TARGET_COLUMNS:
        if col not in panel.columns:
            panel[col] = np.nan
    dates = pd.to_datetime(panel["rebalance_date"], errors="coerce").dt.normalize()
    horizon_end = dates + pd.DateOffset(months=_horizon_months(cfg))
    incomplete = dates.notna() & (horizon_end > _label_as_of(cfg))
    if not incomplete.any():
        return 0
    had_labels = panel.loc[incomplete, list(PHASE4_PMB_TARGET_COLUMNS)].notna().any(axis=1)
    cleared = int(had_labels.sum())
    panel.loc[incomplete, list(PHASE4_PMB_TARGET_COLUMNS)] = np.nan
    return cleared


def _normalise_ticker(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def _label_work(panel: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    out = panel.copy()
    for col in PHASE4_PMB_TARGET_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    if not {"rebalance_date", "ticker"}.issubset(out.columns):
        return pd.DataFrame(columns=["rebalance_date", "ticker"])
    out["rebalance_date"] = pd.to_datetime(out["rebalance_date"], errors="coerce").dt.normalize()
    out["ticker"] = _normalise_ticker(out["ticker"])
    mask = _mask_by_date(out, start, end)
    missing = out[list(PHASE4_PMB_TARGET_COLUMNS)].isna().any(axis=1)
    return out.loc[mask & missing & out["rebalance_date"].notna(), ["rebalance_date", "ticker"]].copy()


def load_cached_price_panel_for_forward_labels(
    panel: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load covering ticker caches for rows that still need forward labels."""
    from tools.run_kr1000_backtest import (
        _build_ticker_cache_index,
        _cached_ticker_history_covering,
    )

    cfg = kr1000_leader_alpha_cfg(cfg or {})
    work = _label_work(panel, start, end)
    if work.empty:
        return pd.DataFrame(columns=["date", "ticker", "close"]), {
            "needed_tickers": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "rows": 0,
        }
    horizon_months = int(cfg.get("forward_label_horizon_months", 1) or 1)
    label_as_of_raw = cfg.get("forward_label_as_of_date")
    label_as_of = (
        pd.Timestamp(label_as_of_raw).normalize()
        if label_as_of_raw
        else pd.Timestamp.today().normalize()
    )
    min_date = pd.Timestamp(work["rebalance_date"].min()).normalize()
    max_date = pd.Timestamp(work["rebalance_date"].max()).normalize()
    fetch_start_day = min_date - pd.Timedelta(days=10)
    fetch_end_day = min(
        max_date + pd.DateOffset(months=horizon_months) + pd.Timedelta(days=10),
        label_as_of,
    )
    tickers = sorted(work["ticker"].dropna().astype(str).unique())
    log(f"[forward-labels] indexing cache for {len(tickers)} label tickers")
    cache_index = _build_ticker_cache_index()

    frames: list[pd.DataFrame] = []
    hits = 0
    misses = 0
    for i, ticker in enumerate(tickers, start=1):
        if i == 1 or i % 100 == 0 or i == len(tickers):
            log(f"[forward-labels] cached price preload {i}/{len(tickers)}")
        hist = _cached_ticker_history_covering(ticker, fetch_start_day, fetch_end_day, cache_index)
        if hist.empty:
            misses += 1
            continue
        hits += 1
        h = hist.copy()
        h["ticker"] = ticker
        h["date"] = pd.to_datetime(h["date"], errors="coerce").dt.normalize()
        h["close"] = pd.to_numeric(h.get("close"), errors="coerce")
        h = h.dropna(subset=["date", "ticker", "close"])
        frames.append(h[["date", "ticker", "close"]])
    if frames:
        prices = pd.concat(frames, ignore_index=True).drop_duplicates(["date", "ticker"], keep="last")
        prices = prices.sort_values(["ticker", "date"]).reset_index(drop=True)
    else:
        prices = pd.DataFrame(columns=["date", "ticker", "close"])
    return prices, {
        "needed_tickers": int(len(tickers)),
        "cache_hits": int(hits),
        "cache_misses": int(misses),
        "rows": int(len(prices)),
        "fetch_start": str(fetch_start_day.date()),
        "fetch_end": str(fetch_end_day.date()),
    }


def _audit_labels(before: pd.DataFrame, after: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    before_ready = _label_ready(before)
    after_ready = _label_ready(after)
    if "rebalance_date" in after.columns:
        dates = pd.to_datetime(after["rebalance_date"], errors="coerce").dt.normalize()
    else:
        dates = pd.Series(pd.NaT, index=after.index)
    active = mask.reindex(after.index, fill_value=False)
    changed_cells = 0
    for col in PHASE4_PMB_TARGET_COLUMNS:
        if col not in before.columns or col not in after.columns:
            continue
        b = pd.to_numeric(before[col], errors="coerce")
        a = pd.to_numeric(after[col], errors="coerce")
        changed_cells += int((active & b.isna() & a.notna()).sum())
    ready_by_month = (
        after.loc[active & after_ready, ["rebalance_date"]]
        .assign(rebalance_date=lambda x: pd.to_datetime(x["rebalance_date"], errors="coerce").dt.to_period("M").astype(str))
        .groupby("rebalance_date")
        .size()
        .to_dict()
    )
    return {
        "rows": int(len(after)),
        "active_rows": int(active.sum()),
        "label_ready_before": int((active & before_ready).sum()),
        "label_ready_after": int((active & after_ready).sum()),
        "newly_label_ready_rows": int((active & ~before_ready & after_ready).sum()),
        "filled_label_cells": int(changed_cells),
        "first_rebalance_date": str(dates.min().date()) if dates.notna().any() else None,
        "last_rebalance_date": str(dates.max().date()) if dates.notna().any() else None,
        "active_first_rebalance_date": str(dates[active].min().date()) if dates[active].notna().any() else None,
        "active_last_rebalance_date": str(dates[active].max().date()) if dates[active].notna().any() else None,
        "ready_month_count": int(len(ready_by_month)),
        "ready_rows_by_month_sample": dict(list(ready_by_month.items())[:12]),
    }


def enrich_panel_with_forward_labels(
    panel: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
    price_panel: pd.DataFrame | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return a panel with forward labels filled for the requested date range."""
    out = panel.copy()
    for col in PHASE4_PMB_TARGET_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    before = out.copy()
    cleared = _clear_incomplete_forward_labels(out, cfg)
    mask = _mask_by_date(out, start, end)
    if mask.any():
        subset = out.loc[mask].copy()
        enriched = add_forward_return_labels(subset, cfg=cfg, price_panel=price_panel)
        for col in PHASE4_PMB_TARGET_COLUMNS:
            if col in enriched.columns:
                out.loc[enriched.index, col] = enriched[col]
    audit = _audit_labels(before, out, mask)
    audit["cleared_incomplete_horizon_rows"] = int(cleared)
    return out, audit


def main() -> int:
    args = parse_args()
    panel_path = Path(args.panel) if args.panel else _latest_scored_panel_path()
    out_path = Path(args.out) if args.out else _default_out_path(panel_path)
    audit_path = Path(args.audit_json) if args.audit_json else out_path.with_suffix(".forward_labels.json")

    cfg = kr1000_leader_alpha_cfg()
    if args.horizon_months is not None:
        cfg["forward_label_horizon_months"] = int(args.horizon_months)
    cfg["forward_label_as_of_date"] = args.label_as_of or pd.Timestamp.today().strftime("%Y-%m-%d")
    if args.refresh_days is not None:
        cfg["forward_label_refresh_days"] = int(args.refresh_days)
    cfg["forward_label_fetch_missing_prices"] = bool(args.fetch_missing_prices)

    log(f"[forward-labels] panel = {panel_path}")
    panel = _read_table(panel_path)
    cache_audit: dict[str, Any] | None = None
    if args.price_panel:
        price_panel = _read_table(Path(args.price_panel))
    elif args.no_cache_preload:
        price_panel = None
    else:
        price_panel, cache_audit = load_cached_price_panel_for_forward_labels(
            panel,
            cfg=cfg,
            start=args.start,
            end=args.end,
        )
    enriched, audit = enrich_panel_with_forward_labels(
        panel,
        cfg=cfg,
        price_panel=price_panel,
        start=args.start,
        end=args.end,
    )
    _write_table(enriched, out_path)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_panel": str(panel_path),
        "output_panel": str(out_path),
        "price_panel": str(args.price_panel) if args.price_panel else None,
        "start": args.start,
        "end": args.end,
        "label_as_of": cfg.get("forward_label_as_of_date"),
        "target_columns": list(PHASE4_PMB_TARGET_COLUMNS),
        "cache_price_panel": cache_audit,
        "fetch_missing_prices": bool(args.fetch_missing_prices),
        "audit": audit,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print("Scored panel forward-label enrichment")
    print(f"  rows:         {audit['rows']}")
    print(f"  active rows:  {audit['active_rows']}")
    print(f"  ready before: {audit['label_ready_before']}")
    print(f"  ready after:  {audit['label_ready_after']}")
    print(f"  newly ready:  {audit['newly_label_ready_rows']}")
    print(f"  out:          {out_path}")
    print(f"  audit:        {audit_path}")
    if args.fail_if_no_fill and int(audit.get("newly_label_ready_rows", 0)) <= 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
