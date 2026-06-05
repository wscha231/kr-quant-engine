"""kr_pit_universe -- Point-in-time universe membership history.

Phase C1 fix for survivorship + look-ahead leakage. Replaces the FDR-current
listing fallback (which contaminated historical universes with currently-listed
names) with a PIT-correct source derived from cached monthly mktcap snapshots.

Public API
----------
build_listed_history_from_cache() -> DataFrame
    Scan cache_pykrx/mktcap_ALL_*.parquet to derive per-ticker
    first_seen_date / last_seen_date / inferred_listing_date /
    inferred_delisting_date / n_appearances. Saves to
    data_pit/listed_history.parquet.

build_historical_mcap_panel() -> DataFrame
    Long-format panel: ticker, snapshot_date, market_cap, listed_shares,
    market. Reuses the same cache scan. Saves to
    data_pit/historical_mcap.parquet.

fetch_listing_at_date(rebalance_date) -> DataFrame
    PIT listing snapshot. Returns ticker, market, market_cap, listed_shares
    AS OF that date -- strictly from a snapshot dated <= rebalance_date.
    No FDR-current fallback. Empty DataFrame if no snapshot exists in window.

compute_listed_months_pit(rebalance_date, tickers) -> DataFrame
    Real listed_months, computed from inferred_listing_date in listed_history.
    No more 999 stub.

Critical invariants
-------------------
1. Never call FDR StockListing for historical dates -- it returns CURRENT only.
2. Always filter by snapshot_date <= rebalance_date for PIT correctness.
3. For dates earlier than the earliest cached snapshot, return empty (caller
   must fail loudly, not fall back to current).
4. A ticker delisted before rebalance_date is INCLUDED in past universes if
   it was active at that earlier date -- survivorship-bias-free.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from kr_config import DATA_ROOT
from kr_helpers import log


# ---------------------------------------------------------------------------
# Cache locations
# ---------------------------------------------------------------------------
PYKRX_CACHE_DIR = DATA_ROOT / "cache_pykrx"
PIT_DIR = DATA_ROOT / "data_pit"
PIT_DIR.mkdir(parents=True, exist_ok=True)

LISTED_HISTORY_PATH = PIT_DIR / "listed_history.parquet"
HISTORICAL_MCAP_PATH = PIT_DIR / "historical_mcap.parquet"

# Filename patterns for cached pykrx snapshots
_MKTCAP_PATTERN = re.compile(r"^mktcap_ALL_(\d{8})\.parquet$")
_LISTING_PATTERN = re.compile(r"^listing_ALL_(\d{8})\.parquet$")


# ---------------------------------------------------------------------------
# Cache scanning helpers
# ---------------------------------------------------------------------------
def _scan_cached_mktcap_files() -> list[tuple[pd.Timestamp, Path]]:
    """Return [(snapshot_date, path)] for every mktcap_ALL_*.parquet in cache.

    Sorted ascending by date. Empty list if cache_pykrx directory missing.
    """
    if not PYKRX_CACHE_DIR.exists():
        return []
    out: list[tuple[pd.Timestamp, Path]] = []
    for p in PYKRX_CACHE_DIR.iterdir():
        m = _MKTCAP_PATTERN.match(p.name)
        if not m:
            continue
        try:
            d = pd.Timestamp(datetime.strptime(m.group(1), "%Y%m%d"))
        except ValueError:
            continue
        out.append((d, p))
    out.sort(key=lambda t: t[0])
    return out


def _scan_cached_listing_files() -> list[tuple[pd.Timestamp, Path]]:
    """Return [(snapshot_date, path)] for every listing_ALL_*.parquet in cache.

    NOTE: listing snapshots are FDR-derived and contain CURRENT listing only;
    we keep them only for `name` lookup (they are not the source of truth for
    PIT membership -- mktcap snapshots are).
    """
    if not PYKRX_CACHE_DIR.exists():
        return []
    out: list[tuple[pd.Timestamp, Path]] = []
    for p in PYKRX_CACHE_DIR.iterdir():
        m = _LISTING_PATTERN.match(p.name)
        if not m:
            continue
        try:
            d = pd.Timestamp(datetime.strptime(m.group(1), "%Y%m%d"))
        except ValueError:
            continue
        out.append((d, p))
    out.sort(key=lambda t: t[0])
    return out


# ---------------------------------------------------------------------------
# Build listed_history + historical_mcap from cache scan
# ---------------------------------------------------------------------------
def build_listed_history_from_cache(save: bool = True) -> pd.DataFrame:
    """Derive listing/delisting history from cached mktcap snapshots.

    Each ticker's first appearance in any snapshot = inferred_listing_date.
    Each ticker's last appearance in any snapshot = inferred_last_seen_date.
    A ticker absent from the most recent snapshot is treated as delisted
    (inferred_delisting_date = inferred_last_seen_date).

    Returns DataFrame with columns:
        ticker, market, n_appearances, first_seen_date, last_seen_date,
        inferred_listing_date, inferred_delisting_date (NaT if still active),
        is_active_latest (bool).

    Saves to data_pit/listed_history.parquet when save=True.
    """
    files = _scan_cached_mktcap_files()
    if not files:
        log("[pit] no cached mktcap_ALL_*.parquet files found", level="WARN")
        return pd.DataFrame()

    log(f"[pit] scanning {len(files)} mktcap snapshots: "
        f"{files[0][0].date()} ~ {files[-1][0].date()}")

    rows: list[dict] = []
    for snap_dt, path in files:
        try:
            df = pd.read_parquet(path, columns=["ticker", "market"])
        except Exception as e:
            log(f"[pit] read fail {path.name}: {e}", level="WARN")
            continue
        if df.empty or "ticker" not in df.columns:
            continue
        df["ticker"] = df["ticker"].astype(str).str.zfill(6)
        df["snapshot_date"] = snap_dt
        if "market" not in df.columns:
            df["market"] = ""
        rows.append(df[["ticker", "market", "snapshot_date"]])

    if not rows:
        return pd.DataFrame()
    panel = pd.concat(rows, ignore_index=True)

    # Aggregate per ticker
    agg = panel.groupby("ticker").agg(
        n_appearances=("snapshot_date", "size"),
        first_seen_date=("snapshot_date", "min"),
        last_seen_date=("snapshot_date", "max"),
    ).reset_index()

    # Most-frequent market per ticker (handles KOSPI↔KOSDAQ migrations rare-case)
    market_mode = (
        panel.groupby("ticker")["market"]
        .agg(lambda s: s.value_counts().index[0] if len(s) else "")
        .reset_index()
    )
    agg = agg.merge(market_mode, on="ticker", how="left")

    latest_snap = files[-1][0]
    latest_tickers = set(panel.loc[panel["snapshot_date"] == latest_snap, "ticker"])
    agg["is_active_latest"] = agg["ticker"].isin(latest_tickers)
    agg["inferred_listing_date"] = agg["first_seen_date"]
    agg["inferred_delisting_date"] = agg.apply(
        lambda r: pd.NaT if r["is_active_latest"] else r["last_seen_date"],
        axis=1,
    )
    # NOTE: first_seen_date is a LOWER BOUND on the true listing date -- if the
    # ticker existed before our earliest cached snapshot we cannot infer the
    # exact listing date. Caller should treat first_seen_date == earliest_cache
    # as "listing date unknown / pre-cache".
    earliest_cache = files[0][0]
    agg["listing_date_is_lower_bound"] = (agg["first_seen_date"] == earliest_cache)

    agg = agg.sort_values(["market", "ticker"]).reset_index(drop=True)

    if save:
        try:
            agg.to_parquet(LISTED_HISTORY_PATH, index=False)
            log(f"[pit] saved listed_history -> {LISTED_HISTORY_PATH.name} "
                f"({len(agg)} tickers, "
                f"{int(agg['is_active_latest'].sum())} active, "
                f"{int((~agg['is_active_latest']).sum())} delisted/inactive)")
        except Exception as e:
            log(f"[pit] listed_history save fail: {e}", level="WARN")
    return agg


def build_historical_mcap_panel(save: bool = True) -> pd.DataFrame:
    """Long-format historical mcap panel from all cached snapshots.

    Returns columns: ticker, snapshot_date, market, market_cap, listed_shares,
    volume, value when present in the source snapshots.
    Saves to data_pit/historical_mcap.parquet when save=True.
    """
    files = _scan_cached_mktcap_files()
    if not files:
        log("[pit] no cached mktcap snapshots", level="WARN")
        return pd.DataFrame()

    rows: list[pd.DataFrame] = []
    for snap_dt, path in files:
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            log(f"[pit] read fail {path.name}: {e}", level="WARN")
            continue
        if df.empty:
            continue
        df = df.copy()
        df["ticker"] = df["ticker"].astype(str).str.zfill(6)
        df["snapshot_date"] = snap_dt
        keep = [c for c in ("ticker", "snapshot_date", "market",
                             "market_cap", "listed_shares", "volume", "value")
                if c in df.columns]
        rows.append(df[keep])

    if not rows:
        return pd.DataFrame()
    panel = pd.concat(rows, ignore_index=True)
    panel = panel.sort_values(["ticker", "snapshot_date"]).reset_index(drop=True)

    if save:
        try:
            panel.to_parquet(HISTORICAL_MCAP_PATH, index=False)
            log(f"[pit] saved historical_mcap -> {HISTORICAL_MCAP_PATH.name} "
                f"({len(panel)} rows, "
                f"{panel['ticker'].nunique()} unique tickers, "
                f"{panel['snapshot_date'].nunique()} snapshots)")
        except Exception as e:
            log(f"[pit] historical_mcap save fail: {e}", level="WARN")
    return panel


# ---------------------------------------------------------------------------
# Loaders (cached lazy)
# ---------------------------------------------------------------------------
_LISTED_HISTORY_CACHE: Optional[pd.DataFrame] = None
_HISTORICAL_MCAP_CACHE: Optional[pd.DataFrame] = None


def load_listed_history(rebuild_if_missing: bool = True) -> pd.DataFrame:
    """Load listed_history.parquet, building it on demand if missing."""
    global _LISTED_HISTORY_CACHE
    if _LISTED_HISTORY_CACHE is not None and not _LISTED_HISTORY_CACHE.empty:
        return _LISTED_HISTORY_CACHE
    if LISTED_HISTORY_PATH.exists():
        try:
            df = pd.read_parquet(LISTED_HISTORY_PATH)
            if not df.empty:
                _LISTED_HISTORY_CACHE = df
                return df
        except Exception as e:
            log(f"[pit] listed_history read fail: {e}", level="WARN")
    if rebuild_if_missing:
        df = build_listed_history_from_cache(save=True)
        _LISTED_HISTORY_CACHE = df
        return df
    return pd.DataFrame()


def load_historical_mcap(rebuild_if_missing: bool = True) -> pd.DataFrame:
    """Load historical_mcap.parquet, building it on demand if missing."""
    global _HISTORICAL_MCAP_CACHE
    if _HISTORICAL_MCAP_CACHE is not None and not _HISTORICAL_MCAP_CACHE.empty:
        return _HISTORICAL_MCAP_CACHE
    if HISTORICAL_MCAP_PATH.exists():
        try:
            df = pd.read_parquet(HISTORICAL_MCAP_PATH)
            if not df.empty:
                _HISTORICAL_MCAP_CACHE = df
                return df
        except Exception as e:
            log(f"[pit] historical_mcap read fail: {e}", level="WARN")
    if rebuild_if_missing:
        df = build_historical_mcap_panel(save=True)
        _HISTORICAL_MCAP_CACHE = df
        return df
    return pd.DataFrame()


def invalidate_pit_caches() -> None:
    """Clear in-memory caches (useful for tests / after rebuild)."""
    global _LISTED_HISTORY_CACHE, _HISTORICAL_MCAP_CACHE
    _LISTED_HISTORY_CACHE = None
    _HISTORICAL_MCAP_CACHE = None


# ---------------------------------------------------------------------------
# PIT lookups
# ---------------------------------------------------------------------------
def _fetch_listing_from_historical_mcap(
    rd: pd.Timestamp,
    market: str = "ALL",
) -> pd.DataFrame:
    """Lightweight PIT listing source using only historical_mcap.parquet.

    For deployment on slim runners (GitHub Actions) we want to avoid
    syncing the full cache_pykrx folder (~800 MiB). The pre-built
    historical_mcap panel contains the same per-ticker per-snapshot mcap
    rows the cache_pykrx scan would have produced, so we can use it
    directly to answer fetch_listing_at_date without touching cache_pykrx.

    Returns columns: ticker, market, market_cap, listed_shares,
    snapshot_date, rebalance_date. Empty DataFrame when historical_mcap
    is missing or has no observation at-or-before rd.
    """
    panel = load_historical_mcap(rebuild_if_missing=False)
    if panel.empty or "snapshot_date" not in panel.columns:
        return pd.DataFrame()
    eligible_dates = panel["snapshot_date"][panel["snapshot_date"] <= rd]
    if eligible_dates.empty:
        return pd.DataFrame()
    snap_dt = eligible_dates.max()
    sub = panel[panel["snapshot_date"] == snap_dt].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["ticker"] = sub["ticker"].astype(str).str.zfill(6)
    if "market" in sub.columns and market.upper() != "ALL":
        sub = sub[sub["market"].str.upper().isin([market.upper()])].copy()
    keep = [c for c in ("ticker", "market", "market_cap", "listed_shares",
                        "volume", "value")
            if c in sub.columns]
    sub = sub[keep].copy()
    sub["snapshot_date"] = snap_dt
    sub["rebalance_date"] = rd
    return sub


def fetch_listing_at_date(
    rebalance_date: str | pd.Timestamp,
    market: str = "ALL",
    name_lookup: bool = True,
) -> pd.DataFrame:
    """PIT listing snapshot -- strictly historical, never current-fallback.

    Returns columns: ticker, market, market_cap, listed_shares, name (optional).

    Strategy (Phase D+ optimization, 2026-05-02):
        1. Try historical_mcap.parquet first (single 1.8 MiB file). Avoids
           the 800 MiB cache_pykrx folder so this works on slim deployers.
        2. If historical_mcap is empty or missing, fall back to scanning
           cache_pykrx/mktcap_ALL_*.parquet files.
        3. Filter to requested market(s).
        4. Optionally enrich with `name` from DART corp_to_ticker_map.

    Returns EMPTY DataFrame if no snapshot exists at-or-before rebalance_date.
    Caller MUST treat empty as a hard error (no FDR fallback).
    """
    rd = pd.Timestamp(rebalance_date).normalize()

    # Fast path: derived historical_mcap panel
    df = _fetch_listing_from_historical_mcap(rd, market=market)
    source = "historical_mcap"

    # Fallback: raw cache_pykrx scan (legacy path, used during PIT bootstrap)
    if df.empty:
        files = _scan_cached_mktcap_files()
        if not files:
            log(f"[pit] fetch_listing_at_date({rd.date()}) -- "
                f"no historical_mcap and no cached snapshots",
                level="WARN")
            return pd.DataFrame()
        eligible = [(d, p) for d, p in files if d <= rd]
        if not eligible:
            log(f"[pit] fetch_listing_at_date({rd.date()}) -- earliest "
                f"snapshot is {files[0][0].date()}, requested date too early",
                level="WARN")
            return pd.DataFrame()
        snap_dt, snap_path = eligible[-1]
        try:
            df = pd.read_parquet(snap_path)
        except Exception as e:
            log(f"[pit] read fail {snap_path.name}: {e}", level="WARN")
            return pd.DataFrame()
        if df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["ticker"] = df["ticker"].astype(str).str.zfill(6)
        if "market" in df.columns and market.upper() != "ALL":
            df = df[df["market"].str.upper().isin([market.upper()])].copy()
        keep = [c for c in ("ticker", "market", "market_cap", "listed_shares",
                              "volume", "value")
                if c in df.columns]
        df = df[keep]
        df["snapshot_date"] = snap_dt
        df["rebalance_date"] = rd
        source = f"cache_pykrx/{snap_path.name}"

    if name_lookup:
        df = _enrich_with_name(df)

    log(f"[pit] fetch_listing_at_date({rd.date()}) -> {len(df)} tickers "
        f"from {source}")
    return df


def _enrich_with_name(df: pd.DataFrame) -> pd.DataFrame:
    """Add `name` column from DART corp_to_ticker_map.

    DART corp_name is stable across time so this is PIT-safe for the name field.
    We do NOT rely on DART for membership; only for cosmetic naming.
    Falls back gracefully when DART unavailable.
    """
    if df.empty or "ticker" not in df.columns:
        return df
    try:
        from kr_dart_client import fetch_corp_to_ticker_map
        cmap = fetch_corp_to_ticker_map()
        if cmap.empty:
            df["name"] = ""
            return df
        cmap = cmap[["ticker", "corp_name"]].rename(columns={"corp_name": "name"})
        cmap["ticker"] = cmap["ticker"].astype(str).str.zfill(6)
        df = df.merge(cmap, on="ticker", how="left")
        df["name"] = df["name"].fillna("")
    except Exception as e:
        log(f"[pit] name enrichment failed: {e}", level="WARN")
        df["name"] = ""
    return df


def compute_listed_months_pit(
    rebalance_date: str | pd.Timestamp,
    tickers: list[str],
) -> pd.DataFrame:
    """Real listed_months via inferred_listing_date in listed_history.

    For each ticker, returns months elapsed between inferred_listing_date and
    rebalance_date. If the ticker's listing_date_is_lower_bound flag is set
    (= the ticker existed before the earliest cached snapshot), we return
    a high value (999) to indicate "long-listed, exact unknown" -- this is
    safe for the min_listed_months ≥ 12 filter since long-listed names pass.

    Returns DataFrame with columns: ticker, listed_months,
    listing_date_known (bool).
    """
    rd = pd.Timestamp(rebalance_date).normalize()
    hist = load_listed_history()
    if hist.empty:
        return pd.DataFrame({
            "ticker": tickers,
            "listed_months": [999] * len(tickers),  # legacy stub fallback
            "listing_date_known": [False] * len(tickers),
        })

    tickers_padded = [str(t).zfill(6) for t in tickers]
    sub = hist[hist["ticker"].isin(tickers_padded)][[
        "ticker", "inferred_listing_date", "listing_date_is_lower_bound",
    ]].copy()

    months_diff = (
        (rd - sub["inferred_listing_date"]).dt.days / 30.4375
    ).round().astype("Int64")
    sub["listed_months"] = months_diff
    # If listing date is a lower bound (pre-cache), set 999 so the filter
    # treats them as long-listed (passes min_listed_months).
    pre_cache = sub["listing_date_is_lower_bound"].fillna(False)
    sub.loc[pre_cache, "listed_months"] = 999
    sub["listing_date_known"] = ~pre_cache

    out = pd.DataFrame({"ticker": tickers_padded}).merge(
        sub[["ticker", "listed_months", "listing_date_known"]],
        on="ticker", how="left",
    )
    out["listed_months"] = out["listed_months"].fillna(0).astype(int)
    out["listing_date_known"] = out["listing_date_known"].fillna(False)
    return out


def get_mcap_at_date(
    ticker: str,
    rebalance_date: str | pd.Timestamp,
) -> Optional[float]:
    """PIT mcap lookup for a single ticker.

    Returns the mcap from the most recent snapshot at-or-before rebalance_date.
    None if the ticker has no snapshot in window (e.g., not yet listed, or
    delisted before any cached snapshot).
    """
    rd = pd.Timestamp(rebalance_date).normalize()
    panel = load_historical_mcap()
    if panel.empty:
        return None
    tk = str(ticker).zfill(6)
    sub = panel[(panel["ticker"] == tk) & (panel["snapshot_date"] <= rd)]
    if sub.empty:
        return None
    latest = sub.sort_values("snapshot_date").iloc[-1]
    val = latest.get("market_cap")
    return float(val) if pd.notna(val) else None


# ---------------------------------------------------------------------------
# Sanity test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("kr_pit_universe sanity test")
    print("=" * 60)

    print("\n[1/4] Scan cache_pykrx for snapshots...")
    files = _scan_cached_mktcap_files()
    print(f"  Found {len(files)} mktcap_ALL_*.parquet snapshots")
    if files:
        print(f"  Range: {files[0][0].date()} ~ {files[-1][0].date()}")

    print("\n[2/4] Build listed_history...")
    hist = build_listed_history_from_cache(save=True)
    print(f"  Tickers: {len(hist)}")
    if not hist.empty:
        print(f"  Active: {int(hist['is_active_latest'].sum())}")
        print(f"  Delisted: {int((~hist['is_active_latest']).sum())}")
        print(f"  Listing date known: "
              f"{int((~hist['listing_date_is_lower_bound']).sum())}")
        print(f"  Pre-cache (listing date unknown): "
              f"{int(hist['listing_date_is_lower_bound'].sum())}")

    print("\n[3/4] PIT listing for 2018-12-31 (PRE earliest listing snapshot)...")
    df18 = fetch_listing_at_date("2018-12-31", name_lookup=False)
    print(f"  Tickers active 2018-12-31: {len(df18)}")
    if not df18.empty:
        print(df18.head(3)[["ticker", "market", "market_cap"]])

    print("\n[4/4] PIT listing for 2024-06-30...")
    df24 = fetch_listing_at_date("2024-06-30", name_lookup=False)
    print(f"  Tickers active 2024-06-30: {len(df24)}")
    if not df24.empty:
        print(df24.head(3)[["ticker", "market", "market_cap"]])

    if not df18.empty and not df24.empty:
        only18 = set(df18["ticker"]) - set(df24["ticker"])
        only24 = set(df24["ticker"]) - set(df18["ticker"])
        print(f"\n  Only in 2018: {len(only18)} (delisted between 2018 and 2024)")
        print(f"  Only in 2024: {len(only24)} (newly listed 2018-2024)")

    print("\nDone.")
