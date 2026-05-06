"""tools/build_dart_induty_panel.py — Phase G P5 self-built industry index.

KRX 27 업종지수 fetch는 pykrx 1.2.7 + KRX 2025 redesign 영향으로 broken.
Workaround: build our own industry index by fetching DART /api/company.json
per corp once (= one-shot ~15 min for 1500 listed corps), then:

  1. Save ticker -> induty_code (5-digit KSIC) mapping at
     data_pit/ticker_induty_map.parquet
  2. Optional follow-up: build daily mcap-weighted index per induty
     (consumed by kr_themes.build_dart_induty_strength_panel).

Usage
-----
    py -3 tools/build_dart_induty_panel.py [--limit 1500]

Output
------
    data_pit/ticker_induty_map.parquet
        ticker, corp_code, corp_name, induty_code, induty_name, est_dt

A second call with --build-strength-panel produces:
    data_pit/induty_strength_panel_<period>.parquet

Idempotent — skips tickers already mapped (cached) unless --force.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402
from kr_helpers import log  # noqa: E402

# KSIC 5-digit code human-readable label (popular subset; full mapping
# could be loaded from KOSIS, but we cover ~80% of listed firms with
# the major 30 codes below).
KSIC_LABELS = {
    "26110": "반도체 및 전자부품",
    "26120": "다이오드/트랜지스터",
    "26210": "디스플레이",
    "26410": "의료/통신기기",
    "26429": "전자부품 기타",
    "27199": "전기장비 기타",
    "27310": "전동기/발전기",
    "27320": "변압기/스위치",
    "28202": "반도체 제조용 기계",
    "29280": "수송용 기계",
    "30110": "선박/구조물 (조선)",
    "30310": "철도차량",
    "30391": "항공기 제조",
    "32011": "방산무기 (전차/탄약)",
    "13212": "섬유의류",
    "10712": "식음료 가공",
    "21102": "의약품/바이오",
    "21210": "원료의약품",
    "21300": "의료용 물질",
    "20119": "기초화학",
    "20120": "산업용 가스",
    "23999": "비금속광물",
    "24190": "1차 철강",
    "47221": "온라인 쇼핑",
    "58221": "응용 소프트웨어 (게임 포함)",
    "58221_game": "게임 SW",
    "62010": "컴퓨터프로그래밍",
    "63111": "데이터처리/호스팅",
    "64190": "은행/저축은행",
    "64201": "지주회사",
    "65111": "보험",
    "66120": "증권",
    "70113": "지주/지배",
    "72200": "수산물 양식",
}


DART_COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DART induty mapping builder")
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N corps (debug).")
    p.add_argument("--polite-sleep-s", type=float, default=0.10,
                   help="Pause between DART calls (default 0.1s).")
    p.add_argument("--force", action="store_true",
                   help="Refetch every ticker even if already cached.")
    p.add_argument("--build-strength-panel", action="store_true",
                   help="After mapping, build daily mcap-weighted index per induty.")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--end", default=None)
    return p.parse_args()


def _fetch_company_info(corp_code: str, api_key: str,
                          polite_sleep: float = 0.10) -> dict:
    """Single DART /api/company.json call."""
    params = {"crtfc_key": api_key, "corp_code": corp_code}
    time.sleep(polite_sleep)
    r = requests.get(DART_COMPANY_URL, params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != "000":
        return {}
    return {
        "corp_code": j.get("corp_code"),
        "corp_name": j.get("corp_name"),
        "ticker": (j.get("stock_code") or "").zfill(6) if j.get("stock_code") else None,
        "induty_code": j.get("induty_code"),
        "est_dt": j.get("est_dt"),
        "ceo_nm": j.get("ceo_nm"),
        "phn_no": j.get("phn_no"),
    }


def main() -> int:
    args = parse_args()
    import os
    api_key = os.environ.get("DART_API_KEY")
    if not api_key:
        # Try to load from .env
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("DART_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        log("[induty] DART_API_KEY not set", level="ERROR")
        return 1

    out_path = DATA_ROOT / "data_pit" / "ticker_induty_map.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Existing cache
    existing = pd.DataFrame()
    if out_path.exists() and not args.force:
        try:
            existing = pd.read_parquet(out_path)
            log(f"[induty] existing cache: {len(existing)} rows")
        except Exception:
            existing = pd.DataFrame()
    cached_tickers = set(existing["ticker"].astype(str).str.zfill(6))

    # Source: corp_to_ticker_map (listed corps)
    from kr_dart_client import fetch_corp_to_ticker_map
    cmap = fetch_corp_to_ticker_map()
    cmap["ticker"] = cmap["ticker"].astype(str).str.zfill(6)
    targets = cmap[~cmap["ticker"].isin(cached_tickers)]
    if args.limit:
        targets = targets.head(args.limit)
    log(f"[induty] {len(targets)} corps to fetch ({len(cached_tickers)} cached)")

    rows: list[dict] = []
    t0 = time.time()
    for k, (_, r) in enumerate(targets.iterrows(), 1):
        if k % 50 == 0:
            elapsed = time.time() - t0
            log(f"[induty] {k}/{len(targets)}, {len(rows)} new, {elapsed:.0f}s")
        try:
            info = _fetch_company_info(
                r["corp_code"], api_key,
                polite_sleep=args.polite_sleep_s,
            )
        except Exception as e:
            log(f"[induty] {r['ticker']} fail: {e}", level="WARN")
            continue
        if info:
            rows.append(info)

    if rows:
        new_df = pd.DataFrame(rows)
        if not existing.empty:
            new_df = pd.concat([existing, new_df], ignore_index=True)
            new_df = new_df.drop_duplicates(subset=["ticker"], keep="last")
        new_df["induty_label"] = new_df["induty_code"].map(KSIC_LABELS)
        try:
            new_df.to_parquet(out_path, index=False)
            log(f"[induty] wrote {out_path} ({len(new_df)} rows)")
        except Exception as e:
            log(f"[induty] write fail: {e}", level="ERROR")
            return 2
    else:
        if existing.empty:
            log("[induty] no rows fetched", level="WARN")
            return 3
        new_df = existing

    # Distribution summary
    counts = new_df["induty_code"].value_counts().head(20)
    print()
    print("Top 20 induty_code by listed-corp count:")
    for code, n in counts.items():
        label = KSIC_LABELS.get(str(code), "")
        print(f"  {str(code):<8} {n:>5} corps  {label}")

    # Optional: daily strength panel
    if args.build_strength_panel:
        log("[induty] building strength panel from induty mapping...")
        return _build_strength_panel(new_df, args.start, args.end)
    return 0


def _build_strength_panel(induty_map: pd.DataFrame,
                            start: str, end: Optional[str]) -> int:
    """Mcap-weighted daily index per induty_code."""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    log(f"[induty] strength window {start} -> {end}")

    from kr_pit_universe import load_historical_mcap
    from kr_pykrx_client import fetch_ticker_history

    # Filter to listed tickers only
    induty_map = induty_map.dropna(subset=["ticker", "induty_code"])
    induty_map["ticker"] = induty_map["ticker"].astype(str).str.zfill(6)
    induty_map["induty_code"] = induty_map["induty_code"].astype(str)
    log(f"[induty] mapped tickers: {len(induty_map)}")

    # Aggregate per induty_code (top 5 mcap leaders, equal-weighted close)
    mcap_panel = load_historical_mcap(rebuild_if_missing=False)
    if mcap_panel.empty:
        log("[induty] historical_mcap empty -> using simple average", level="WARN")
        latest_mcap = {}
    else:
        latest_mcap = (
            mcap_panel.sort_values("snapshot_date").groupby("ticker").last()
            ["market_cap"].to_dict()
        )

    induty_map["mcap"] = induty_map["ticker"].map(latest_mcap).fillna(0)
    out_frames = []
    for induty, sub in induty_map.groupby("induty_code"):
        if len(sub) < 3:
            continue   # too small
        leaders = sub.nlargest(5, "mcap")["ticker"].tolist()
        # Pull each leader's close
        closes = {}
        for tk in leaders:
            try:
                h = fetch_ticker_history(
                    tk,
                    pd.Timestamp(start).strftime("%Y%m%d"),
                    pd.Timestamp(end).strftime("%Y%m%d"),
                    refresh_days=30,
                )
                if not h.empty and "close" in h.columns:
                    closes[tk] = (
                        h.sort_values("date").set_index("date")["close"].astype(float)
                    )
            except Exception:
                continue
        if not closes:
            continue
        comp = pd.concat(closes.values(), axis=1, keys=closes.keys()).sort_index()
        ret = comp.pct_change().mean(axis=1)
        df = pd.DataFrame({
            "date": ret.index,
            "induty_code": str(induty),
            "induty_label": KSIC_LABELS.get(str(induty), ""),
            "return": ret.values,
            "n_leaders": len(closes),
        })
        out_frames.append(df)

    if not out_frames:
        log("[induty] no induty panels built", level="WARN")
        return 4
    panel = pd.concat(out_frames, ignore_index=True)
    out_path = DATA_ROOT / "data_pit" / f"induty_strength_panel_{start.replace('-','')}_{end.replace('-','')}.parquet"
    panel.to_parquet(out_path, index=False)
    log(f"[induty] wrote {out_path} ({len(panel)} rows, "
        f"{panel['induty_code'].nunique()} industries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
