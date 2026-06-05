"""kr_dart_client — DART OpenAPI wrapper + parquet caching.

DART OpenAPI base: https://opendart.fss.or.kr/api/

Endpoints used:
- /corpCode.xml          — 전체 회사 corp_code XML (ZIP)
- /list.json             — 공시 검색
- /fnlttSinglAcntAll.json — 단일회사 전체 재무제표 (사업/반기/분기)
- /document.xml          — 원본 공시 (필요시 P2+)

Critical invariant — Point-in-time (PIT):
Every fetched record carries `rcept_dt` (공시일) and `rcept_no`. When joining
with universe panel at rebalance_date `t`, ALWAYS filter `rcept_dt <= t`.
Never use period_end (`bsns_year` + `reprt_code`) as the join timestamp —
that would leak future data.

Caching:
- corp_code XML: cache_dart/corp_code.zip + corp_code.parquet (~1MB), TTL 1 day
- Financials: cache_dart/financials/{corp_code}/{bsns_year}_{reprt_code}.parquet
  (one row per account_id, multi-period within parquet)
- Disclosure list: cache_dart/disclosures/{corp_code}_{year}.parquet

Report codes (reprt_code):
- 11011 — 사업보고서 (annual)
- 11012 — 반기보고서 (semi-annual / H1)
- 11013 — 1분기보고서 (Q1)
- 11014 — 3분기보고서 (Q3)
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import pandas as pd
import requests

from kr_config import DATA_ROOT
from kr_helpers import load_dotenv_if_present, log


CACHE_DIR = DATA_ROOT / "cache_dart"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DART_BASE_URL = "https://opendart.fss.or.kr/api"

# Report code lookup
REPRT_CODES = {
    "annual":   "11011",   # 사업보고서
    "semi":     "11012",   # 반기보고서
    "q1":       "11013",   # 1분기보고서
    "q3":       "11014",   # 3분기보고서
}
REPRT_NAMES = {v: k for k, v in REPRT_CODES.items()}

CALENDAR_PERIOD_END_BY_REPRT_CODE = {
    "11013": (3, 31),
    "11012": (6, 30),
    "11014": (9, 30),
    "11011": (12, 31),
}


def _calendar_period_end(bsns_year: int, reprt_code: str) -> pd.Timestamp:
    """Calendar-year period end implied by DART report code."""
    month, day = CALENDAR_PERIOD_END_BY_REPRT_CODE[str(reprt_code)]
    return pd.Timestamp(year=int(bsns_year), month=month, day=day)


def _previous_calendar_quarter_end(ts: pd.Timestamp) -> pd.Timestamp:
    """Latest Mar/Jun/Sep/Dec quarter-end strictly before ts."""
    anchor = pd.Timestamp(ts).normalize() - pd.Timedelta(days=1)
    candidates = []
    for year in (anchor.year - 1, anchor.year):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            cand = pd.Timestamp(year=year, month=month, day=day)
            if cand <= anchor:
                candidates.append(cand)
    return max(candidates)


def infer_report_period_end(
    bsns_year: int,
    reprt_code: str,
    rcept_dt: Optional[pd.Timestamp] = None,
) -> tuple[pd.Timestamp, bool]:
    """Infer non-future period_end metadata for DART financial reports.

    DART report codes are fiscal-period codes, not always calendar-period
    codes. For non-December fiscal-year companies, the naive calendar mapping
    can produce impossible metadata such as Q3 period_end after rcept_dt. The
    filing date remains the PIT timestamp; this helper only prevents future
    period metadata from contaminating TTM/order logic.

    Returns:
        (period_end, adjusted_from_calendar)
    """
    period_end = _calendar_period_end(int(bsns_year), str(reprt_code))
    if rcept_dt is None or pd.isna(rcept_dt):
        return period_end, False

    rd = pd.Timestamp(rcept_dt).normalize()
    if period_end <= rd:
        return period_end, False

    return _previous_calendar_quarter_end(rd), True

# Account ID mapping — XBRL standard (IFRS) + DART extension
# Used for fast account_id lookup in financial statements
KEY_ACCOUNTS = {
    "revenue":              ["ifrs-full_Revenue", "ifrs_Revenue", "revenue"],
    "operating_income":     ["dart_OperatingIncomeLoss", "ifrs-full_OperatingIncomeLoss",
                             "ifrs-full_ProfitLossFromOperatingActivities"],
    "net_income":           ["ifrs-full_ProfitLoss", "dart_ProfitLossOfTheCurrentTerm",
                             "ifrs-full_ProfitLossAttributableToOwnersOfParent"],
    "total_assets":         ["ifrs-full_Assets"],
    "total_equity":         ["ifrs-full_Equity",
                             "ifrs-full_EquityAttributableToOwnersOfParent"],
    "total_liabilities":    ["ifrs-full_Liabilities"],
    "operating_cash_flow":  ["ifrs-full_CashFlowsFromUsedInOperatingActivities",
                             "ifrs_CashFlowsFromUsedInOperatingActivities"],
    "shares_outstanding":   [],   # DART finstat 안 줌; pykrx에서 읽음
}

# Korean account_nm fallback (when account_id mismatch — common in older filings)
KOREAN_NAME_FALLBACK = {
    "revenue":           ["매출액", "매출", "수익(매출액)", "영업수익"],
    "operating_income":  ["영업이익", "영업이익(손실)"],
    "net_income":        ["당기순이익", "당기순이익(손실)", "지배기업 소유주지분 순이익"],
    "total_assets":      ["자산총계", "자산총계(자산총계)"],
    "total_equity":      ["자본총계", "지배기업 소유주지분", "자본총계(자본총계)"],
    "total_liabilities": ["부채총계"],
    "operating_cash_flow": ["영업활동현금흐름", "영업활동으로 인한 현금흐름"],
}


def _get_api_key() -> str:
    """Load DART_API_KEY from .env or env. Raise if missing."""
    load_dotenv_if_present()
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "DART_API_KEY not set. "
            "Get free key at https://opendart.fss.or.kr and add to .env"
        )
    return key


# ---------------------------------------------------------------------------
# 1. Corp code download + parse
# ---------------------------------------------------------------------------
def download_corp_code(refresh_days: int = 1) -> Path:
    """Download corpCode.xml ZIP and extract to cache_dart/.

    The ZIP contains a single file CORPCODE.xml (~3MB).
    Refresh daily — new corp registrations happen sometimes.
    """
    zip_path = CACHE_DIR / "corp_code.zip"
    xml_path = CACHE_DIR / "CORPCODE.xml"

    # TTL check
    if xml_path.exists():
        age_s = time.time() - xml_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            return xml_path

    api_key = _get_api_key()
    url = f"{DART_BASE_URL}/corpCode.xml"
    log(f"[dart] download corpCode.xml")
    resp = requests.get(url, params={"crtfc_key": api_key}, timeout=60)
    resp.raise_for_status()

    # Response is a ZIP file
    with open(zip_path, "wb") as f:
        f.write(resp.content)

    # Extract
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(CACHE_DIR)

    if not xml_path.exists():
        raise RuntimeError(f"CORPCODE.xml not found after extraction in {CACHE_DIR}")
    log(f"[dart] corpCode.xml extracted ({xml_path.stat().st_size // 1024}KB)")
    return xml_path


def parse_corp_code_to_df(xml_path: Optional[Path] = None) -> pd.DataFrame:
    """Parse CORPCODE.xml -> DataFrame.

    Columns: corp_code (8-digit), corp_name, stock_code (6-digit, blank if unlisted),
             modify_date (YYYYMMDD).

    Cached as cache_dart/corp_code.parquet.
    """
    parquet_path = CACHE_DIR / "corp_code.parquet"
    if parquet_path.exists() and xml_path is None:
        age_s = time.time() - parquet_path.stat().st_mtime
        if age_s < 86400:
            return pd.read_parquet(parquet_path)

    if xml_path is None:
        xml_path = download_corp_code()
    tree = ET.parse(xml_path)
    root = tree.getroot()

    rows = []
    for elem in root.findall("list"):
        rows.append({
            "corp_code":   (elem.findtext("corp_code") or "").strip(),
            "corp_name":   (elem.findtext("corp_name") or "").strip(),
            "stock_code":  (elem.findtext("stock_code") or "").strip(),
            "modify_date": (elem.findtext("modify_date") or "").strip(),
        })
    df = pd.DataFrame(rows)
    try:
        df.to_parquet(parquet_path, index=False)
    except Exception as e:
        log(f"[dart] corp_code parquet write fail: {e}", level="WARN")
    log(f"[dart] parsed corp_code: {len(df)} rows "
        f"({(df['stock_code'] != '').sum()} listed)")
    return df


def fetch_corp_to_ticker_map(refresh_days: int = 1) -> pd.DataFrame:
    """Return DataFrame of listed companies only: corp_code, corp_name, ticker.

    `ticker` = 6-digit stock_code (KOSPI/KOSDAQ/KONEX 모두 포함).
    Caller should join with pykrx listing to filter market.
    """
    df = parse_corp_code_to_df()
    listed = df[df["stock_code"].str.len() == 6].copy()
    listed = listed.rename(columns={"stock_code": "ticker"})
    return listed[["corp_code", "corp_name", "ticker", "modify_date"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 2. Disclosure list (P2 이벤트 시그널 prep)
# ---------------------------------------------------------------------------
def fetch_disclosure_list(
    corp_code: str,
    bgn_de: str,
    end_de: str,
    pblntf_ty: Optional[str] = None,
    refresh_days: int = 7,
) -> pd.DataFrame:
    """Fetch disclosure list for a corp.

    Args:
        corp_code: 8-digit
        bgn_de, end_de: YYYYMMDD
        pblntf_ty: 공시유형 (A=정기공시, B=주요사항보고, C=발행공시, D=지분공시,
                              E=기타공시, F=외부감사 등). None=all.
    """
    cache = CACHE_DIR / "disclosures" / f"{corp_code}_{bgn_de}_{end_de}.parquet"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        age_s = time.time() - cache.stat().st_mtime
        if age_s < refresh_days * 86400:
            return pd.read_parquet(cache)

    api_key = _get_api_key()
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": bgn_de.replace("-", ""),
        "end_de": end_de.replace("-", ""),
        "page_count": 100,
    }
    if pblntf_ty:
        params["pblntf_ty"] = pblntf_ty

    url = f"{DART_BASE_URL}/list.json"
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") not in ("000", "013"):  # 000=ok, 013=no data
        log(f"[dart] disclosure list status={data.get('status')} msg={data.get('message')}",
            level="WARN")

    rows = data.get("list", [])
    df = pd.DataFrame(rows)
    if not df.empty and "rcept_dt" in df.columns:
        df["rcept_dt"] = pd.to_datetime(df["rcept_dt"], format="%Y%m%d", errors="coerce")
    try:
        df.to_parquet(cache, index=False)
    except Exception as e:
        log(f"[dart] disclosure cache write fail: {e}", level="WARN")
    return df


# ---------------------------------------------------------------------------
# 3. Financial statements — 단일회사 전체 재무제표
# ---------------------------------------------------------------------------
def fetch_single_company_financials(
    corp_code: str,
    bsns_year: int,
    reprt_code: str,
    fs_div: str = "CFS",      # CFS=연결, OFS=별도
    refresh_days: int = 30,
) -> pd.DataFrame:
    """Fetch fnlttSinglAcntAll for one (corp, year, report).

    Returns: account_id, account_nm, sj_div (BS/IS/CIS/CF), thstrm_amount,
             frmtrm_amount, bfefrmtrm_amount, rcept_no, fs_div, ord, ...

    Cache: cache_dart/financials/{corp_code}/{year}_{reprt_code}_{fs_div}.parquet
    """
    cache_dir = CACHE_DIR / "financials" / corp_code
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{bsns_year}_{reprt_code}_{fs_div}.parquet"

    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                return pd.read_parquet(cache_path)
            except Exception as e:
                log(f"[dart] cache read fail {cache_path.name}: {e}", level="WARN")

    api_key = _get_api_key()
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(bsns_year),
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }
    url = f"{DART_BASE_URL}/fnlttSinglAcntAll.json"
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status", "")
    if status == "013":
        # 조회된 데이터가 없습니다 (보고서 미제출 등)
        empty = pd.DataFrame()
        try:
            empty.to_parquet(cache_path, index=False)
        except Exception:
            pass
        return empty
    if status != "000":
        log(f"[dart] fnlttSinglAcntAll status={status} msg={data.get('message')} "
            f"corp={corp_code} year={bsns_year} reprt={reprt_code}", level="WARN")
        return pd.DataFrame()

    rows = data.get("list", [])
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Coerce numeric (DART returns "1,234,567" strings)
    for col in ("thstrm_amount", "frmtrm_amount", "bfefrmtrm_amount",
                "thstrm_add_amount", "frmtrm_add_amount"):
        if col in df.columns:
            df[col] = (df[col].astype(str)
                       .str.replace(",", "", regex=False)
                       .str.replace("-", "0", regex=False)
                       .str.replace(" ", "", regex=False))
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "rcept_no" in df.columns:
        # rcept_no = 14-digit YYYYMMDD + 6-digit serial → derive rcept_dt
        df["rcept_dt"] = pd.to_datetime(
            df["rcept_no"].astype(str).str[:8],
            format="%Y%m%d", errors="coerce",
        )

    df["bsns_year"] = bsns_year
    df["reprt_code"] = reprt_code
    df["fs_div"] = fs_div

    try:
        df.to_parquet(cache_path, index=False)
    except Exception as e:
        log(f"[dart] financials cache write fail: {e}", level="WARN")
    return df


def extract_key_accounts(financials: pd.DataFrame) -> dict[str, float]:
    """Extract standard accounts from a financials DataFrame.

    Returns dict mapping standard_name -> thstrm_amount value.
    First tries account_id, falls back to account_nm.
    """
    out: dict[str, float] = {}
    if financials.empty:
        return out

    aid = financials.get("account_id", pd.Series(dtype=str)).fillna("").astype(str)
    anm = financials.get("account_nm", pd.Series(dtype=str)).fillna("").astype(str)
    amt = financials.get("thstrm_amount", pd.Series(dtype=float))

    for std_name, id_candidates in KEY_ACCOUNTS.items():
        # Try account_id match first
        found = None
        for cand in id_candidates:
            mask = aid == cand
            if mask.any():
                # Prefer 연결재무제표 (already filtered by fs_div upstream)
                # Take first match (most filings have unique entry per account)
                vals = amt[mask].dropna()
                if not vals.empty:
                    found = float(vals.iloc[0])
                    break

        # Fallback to Korean name match
        if found is None:
            for kn in KOREAN_NAME_FALLBACK.get(std_name, []):
                mask = anm.str.contains(kn, regex=False, na=False)
                if mask.any():
                    vals = amt[mask].dropna()
                    if not vals.empty:
                        found = float(vals.iloc[0])
                        break

        if found is not None:
            out[std_name] = found

    # PIT timestamp
    if "rcept_dt" in financials.columns:
        rd = financials["rcept_dt"].dropna()
        if not rd.empty:
            out["rcept_dt"] = pd.Timestamp(rd.iloc[0])

    # Period metadata
    for col in ("bsns_year", "reprt_code", "fs_div"):
        if col in financials.columns and not financials[col].empty:
            out[col] = financials[col].iloc[0]

    return out


# ---------------------------------------------------------------------------
# 4. Multi-company bulk fetch (faster path for P1 build)
# ---------------------------------------------------------------------------
def fetch_multi_company_main_accounts(
    corp_codes: list[str],
    bsns_year: int,
    reprt_code: str,
    refresh_days: int = 30,
) -> pd.DataFrame:
    """Bulk fetch for up to 100 corps (key accounts only).

    fnlttMultiAcnt.json returns 매출액/영업이익/법인세차감전순이익/당기순이익/
    자산총계/부채총계/자본총계 etc. (~6-10 accounts depending on filing).

    For P1 fundamentals this is sufficient and ~100x faster than per-corp loop.

    Cache: cache_dart/multi/{first_corp}_{N}_{year}_{reprt_code}.parquet
    keyed by hash of corp_code list to avoid mass cache misses.
    """
    if not corp_codes:
        return pd.DataFrame()
    if len(corp_codes) > 100:
        raise ValueError("DART fnlttMultiAcnt max 100 corps per call")

    # Deterministic cache key from sorted corp list
    import hashlib
    sig = hashlib.md5(",".join(sorted(corp_codes)).encode()).hexdigest()[:10]
    cache_dir = CACHE_DIR / "multi"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{bsns_year}_{reprt_code}_{sig}.parquet"

    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                return pd.read_parquet(cache_path)
            except Exception as e:
                log(f"[dart] multi cache read fail {cache_path.name}: {e}", level="WARN")

    api_key = _get_api_key()
    params = {
        "crtfc_key": api_key,
        "corp_code": ",".join(corp_codes),
        "bsns_year": str(bsns_year),
        "reprt_code": reprt_code,
    }
    url = f"{DART_BASE_URL}/fnlttMultiAcnt.json"
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status", "")
    if status == "013":
        empty = pd.DataFrame()
        try:
            empty.to_parquet(cache_path, index=False)
        except Exception:
            pass
        return empty
    if status != "000":
        log(f"[dart] fnlttMultiAcnt status={status} msg={data.get('message')}",
            level="WARN")
        return pd.DataFrame()

    rows = data.get("list", [])
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    for col in ("thstrm_amount", "frmtrm_amount", "bfefrmtrm_amount"):
        if col in df.columns:
            df[col] = (df[col].astype(str)
                       .str.replace(",", "", regex=False)
                       .str.replace("-", "0", regex=False)
                       .str.replace(" ", "", regex=False))
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "rcept_no" in df.columns:
        df["rcept_dt"] = pd.to_datetime(
            df["rcept_no"].astype(str).str[:8],
            format="%Y%m%d", errors="coerce",
        )

    df["bsns_year"] = bsns_year
    df["reprt_code"] = reprt_code

    try:
        df.to_parquet(cache_path, index=False)
    except Exception as e:
        log(f"[dart] multi cache write fail: {e}", level="WARN")
    return df


def build_universe_quarterly_panel(
    corp_codes: list[str],
    start_year: int = 2015,
    end_year: Optional[int] = None,
    polite_sleep_s: float = 0.3,
) -> pd.DataFrame:
    """Build long-format quarterly panel for many corps using bulk endpoint.

    Returns: corp_code, bsns_year, reprt_code, rcept_dt, period_end,
             revenue, operating_income, net_income, total_assets,
             total_equity, total_liabilities (subset of KEY_ACCOUNTS that
             fnlttMultiAcnt returns).

    Note: fnlttMultiAcnt only returns key accounts (no operating_cash_flow).
    For full account list, use build_corp_quarterly_panel per-corp.
    """
    end_year = end_year or datetime.now().year

    log(f"[dart] build_universe_quarterly_panel: "
        f"{len(corp_codes)} corps, {start_year}~{end_year}")
    rows = []
    # Process in batches of 100
    batches = [corp_codes[i:i+100] for i in range(0, len(corp_codes), 100)]

    for year in range(start_year, end_year + 1):
        for reprt_code in REPRT_CODES.values():
            for bi, batch in enumerate(batches):
                df = fetch_multi_company_main_accounts(batch, year, reprt_code)
                time.sleep(polite_sleep_s)
                if df.empty:
                    continue

                # Pivot into one row per corp_code
                # (account names: 매출액, 영업이익, 당기순이익, 자산총계, 부채총계, 자본총계)
                for cc, g in df.groupby("corp_code"):
                    rcept_dt = pd.NaT
                    if "rcept_dt" in g.columns:
                        rd = g["rcept_dt"].dropna()
                        rcept_dt = rd.iloc[0] if not rd.empty else pd.NaT
                    period_end, adjusted = infer_report_period_end(year, reprt_code, rcept_dt)
                    row = {
                        "corp_code": cc,
                        "bsns_year": year,
                        "reprt_code": reprt_code,
                        "period_end": period_end,
                        "period_end_adjusted_from_calendar": adjusted,
                    }
                    # Most recent rcept_dt within group
                    row["rcept_dt"] = rcept_dt

                    # Map account_nm -> standard
                    nm_amt = dict(zip(g.get("account_nm", []), g.get("thstrm_amount", [])))
                    for std_name, korean_names in KOREAN_NAME_FALLBACK.items():
                        for kn in korean_names:
                            for nm, amt in nm_amt.items():
                                if pd.notna(nm) and kn in str(nm) and pd.notna(amt):
                                    row[std_name] = float(amt)
                                    break
                            if std_name in row:
                                break
                    rows.append(row)
            log(f"[dart] {year} {REPRT_NAMES.get(reprt_code, reprt_code)}: "
                f"{len([r for r in rows if r['bsns_year']==year and r['reprt_code']==reprt_code])} rows")

    if not rows:
        return pd.DataFrame()
    panel = pd.DataFrame(rows).sort_values(
        ["corp_code", "bsns_year", "reprt_code"]
    ).reset_index(drop=True)
    return panel


# ---------------------------------------------------------------------------
# 5. Quarterly time-series builder (single corp — full account detail)
# ---------------------------------------------------------------------------
def build_corp_quarterly_panel(
    corp_code: str,
    start_year: int = 2015,
    end_year: Optional[int] = None,
    fs_div: str = "CFS",
    polite_sleep_s: float = 0.15,
) -> pd.DataFrame:
    """Build long-format quarterly panel for one corp:
       columns: corp_code, bsns_year, reprt_code, rcept_dt, period_end, fs_div,
                revenue, operating_income, net_income, total_assets,
                total_equity, total_liabilities, operating_cash_flow

    Iterates over all 4 reports per year.
    """
    end_year = end_year or datetime.now().year

    rows = []
    for year in range(start_year, end_year + 1):
        for reprt_name, reprt_code in REPRT_CODES.items():
            df = fetch_single_company_financials(corp_code, year, reprt_code, fs_div=fs_div)
            time.sleep(polite_sleep_s)   # Be nice to DART
            if df.empty:
                continue
            row = extract_key_accounts(df)
            row["corp_code"] = corp_code
            row["bsns_year"] = year
            row["reprt_code"] = reprt_code
            row["fs_div"] = fs_div

            # Compute non-future period metadata; rcept_dt remains the PIT timestamp.
            period_end, adjusted = infer_report_period_end(year, reprt_code, row.get("rcept_dt"))
            row["period_end"] = period_end
            row["period_end_adjusted_from_calendar"] = adjusted
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    panel = pd.DataFrame(rows).sort_values(["bsns_year", "reprt_code"]).reset_index(drop=True)
    return panel


# ---------------------------------------------------------------------------
# 6. PIT-safe join helper
# ---------------------------------------------------------------------------
def pit_filter_panel(panel: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Filter panel to rows visible as of `as_of` (rcept_dt <= as_of).

    Critical for backtest no-look-ahead. If rcept_dt missing, drop the row
    (defensive — never use stale period_end-based fallback).
    """
    if panel.empty or "rcept_dt" not in panel.columns:
        return panel.iloc[0:0]   # empty same-schema
    out = panel[panel["rcept_dt"].notna() & (panel["rcept_dt"] <= as_of)].copy()
    return out


# ===========================================================================
# 7. Disclosure event endpoints (P2 prep)
#
# DART OpenAPI provides dedicated JSON endpoints for major corporate events.
# These are the foundation for `disclosure_event_score` Korean alpha signal.
#
# Endpoint groups (signal direction in parens):
#   - elestock     (+/-)  임원·주요주주 특정증권등 소유상황보고 (Form 5 등가)
#   - majorstock   (+/-)  주식등의 대량보유상황보고 (5%+ holders)
#   - piicDecsn    (-)    유상증자결정 (capital increase / dilution)
#   - fricDecsn    (+)    무상증자결정 (bonus issue)
#   - tsstkAqDecsn (+)    자기주식 취득결정 (buyback)
#   - tsstkDpDecsn (-)    자기주식 처분결정 (treasury sell)
#   - cvbdIsDecsn  (-)    전환사채(CB)발행결정
#   - bdIsDecsn    (-)    신주인수권부사채(BW)발행결정
#   - mgDecsn      (?)    합병결정 (merger - case-by-case)
#   - divDecsn     (?)    분할결정 (split - case-by-case)
#   - crDecsn      (-)    감자결정 (capital reduction)
#
# All endpoints accept: crtfc_key, corp_code, bgn_de, end_de (YYYYMMDD).
# All return: status, message, list[{rcept_no, rcept_dt, corp_name, ...}]
# rcept_dt → PIT timestamp (공시일).
# ===========================================================================

# ===========================================================================
# DART_EVENT_CATALOG v2 (2026-04-28) — KRW-amount-based scoring
#
# v1 (count-based) had a critical issue: Samsung 2024 had 2,614 insider
# disclosures × 0.30 weight = +784 score (literally 100x too large). The
# fix is to score by economic magnitude (KRW amount / mcap), not count.
#
# Each event has:
#   endpoint           — DART JSON endpoint name
#   direction          — "positive" / "negative" / "bidirectional" / "case"
#   alpha_weight       — max contribution to final score (signed)
#   scoring_mode       — how to compute magnitude:
#       "amount_pct_mcap"      — sum(amount_field) / mcap, capped at full_pct
#       "binary"               — fires once if any event in window
#       "stkrt_change"         — sum(stkrt_irds) (already %), signed
#       "insider_net_buy"      — special: sum (delta_qty × trade_uv) / mcap
#       "computed_dilution"    — nstk_ostk_qy × bdis_pric / mcap (capital raise)
#   amount_field       — column to read amount from (mode-dependent)
#   full_weight_pct    — mcap pct at which alpha_weight is fully applied
#                        (e.g. 0.02 = 2% mcap event → max weight)
#   kr_name            — Korean disclosure name
# ===========================================================================
DART_EVENT_CATALOG = {
    "treasury_buyback": {
        "endpoint": "tsstkAqDecsn",
        "direction": "positive",
        "alpha_weight": 0.50,
        "scoring_mode": "amount_pct_mcap",
        "amount_field": "aqpln_prc_ostk",   # 취득예정금액 보통주 (KRW)
        "full_weight_pct": 0.02,             # 2% mcap buyback = full +0.50
        "kr_name": "자기주식취득결정",
    },
    "capital_increase": {
        "endpoint": "piicDecsn",
        "direction": "negative",
        "alpha_weight": -0.40,
        "scoring_mode": "computed_dilution",
        "amount_field": "nstk_ostk_qy",      # 보통주 신주수 (×bdis_pric → 발행규모)
        "full_weight_pct": 0.05,             # 5% dilution = full -0.40
        "kr_name": "유상증자결정",
    },
    "bonus_issue": {
        "endpoint": "fricDecsn",
        "direction": "positive",
        "alpha_weight": 0.30,
        "scoring_mode": "binary",            # 심리 효과 — size 무관
        "amount_field": None,
        "full_weight_pct": None,
        "kr_name": "무상증자결정",
    },
    "treasury_sell": {
        "endpoint": "tsstkDpDecsn",
        "direction": "negative",
        "alpha_weight": -0.30,
        "scoring_mode": "amount_pct_mcap",
        "amount_field": "dppln_prc_ostk",    # 처분예정금액 보통주
        "full_weight_pct": 0.02,
        "kr_name": "자기주식처분결정",
    },
    "convertible_bond": {
        "endpoint": "cvbdIsDecsn",
        "direction": "negative",
        "alpha_weight": -0.20,
        "scoring_mode": "amount_pct_mcap",
        "amount_field": "bd_fta",            # 사채총액
        "full_weight_pct": 0.10,             # 10% mcap CB = full -0.20
        "kr_name": "전환사채발행결정",
    },
    "warrant_bond": {
        "endpoint": "bdIsDecsn",
        "direction": "negative",
        "alpha_weight": -0.20,
        "scoring_mode": "amount_pct_mcap",
        "amount_field": "bd_fta",
        "full_weight_pct": 0.10,
        "kr_name": "신주인수권부사채발행결정",
    },
    "insider_holdings": {
        "endpoint": "elestock",
        "direction": "bidirectional",
        "alpha_weight": 0.30,                # signed magnitude
        "scoring_mode": "insider_net_buy",
        "amount_field": None,                # special handling
        "full_weight_pct": 0.005,            # 0.5% mcap net buy = full ±0.30
        "kr_name": "임원·주요주주 소유상황보고",
    },
    "major_holders": {
        "endpoint": "majorstock",
        "direction": "bidirectional",
        "alpha_weight": 0.25,
        "scoring_mode": "stkrt_change",
        "amount_field": "stkrt_irds",        # 보유비율 변동 (%)
        "full_weight_pct": None,             # uses raw 1.0% threshold
        "kr_name": "주식등의 대량보유상황보고",
    },
    "merger": {
        "endpoint": "mgDecsn",
        "direction": "case",
        "alpha_weight": 0.0,                 # P3 분리 모델
        "scoring_mode": "binary",
        "amount_field": None,
        "full_weight_pct": None,
        "kr_name": "합병결정",
    },
    "spinoff": {
        "endpoint": "divDecsn",
        "direction": "negative",             # 한국 특수: 90% 물적분할 → governance discount
        "alpha_weight": -0.15,
        "scoring_mode": "binary",
        "amount_field": None,
        "full_weight_pct": None,
        "kr_name": "분할결정",
    },
    "capital_reduction": {
        "endpoint": "crDecsn",
        "direction": "negative",
        "alpha_weight": -0.40,
        "scoring_mode": "binary",            # 보통 부실 시그널
        "amount_field": None,
        "full_weight_pct": None,
        "kr_name": "감자결정",
    },
}


def _fetch_dart_event(
    endpoint: str,
    corp_code: str,
    bgn_de: str,
    end_de: str,
    refresh_days: int = 7,
) -> pd.DataFrame:
    """Generic DART event fetch.

    Returns DataFrame with rcept_no, rcept_dt + endpoint-specific fields.
    Cache: cache_dart/events/{endpoint}/{corp_code}_{bgn}_{end}.parquet
    """
    cache_dir = CACHE_DIR / "events" / endpoint
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{corp_code}_{bgn_de}_{end_de}.parquet"

    if cache_path.exists():
        age_s = time.time() - cache_path.stat().st_mtime
        if age_s < refresh_days * 86400:
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass

    api_key = _get_api_key()
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": bgn_de.replace("-", ""),
        "end_de": end_de.replace("-", ""),
    }
    url = f"{DART_BASE_URL}/{endpoint}.json"
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log(f"[dart] event fetch fail {endpoint} {corp_code}: {e}", level="WARN")
        return pd.DataFrame()

    data = resp.json()
    status = data.get("status", "")
    if status == "013":  # no data
        empty = pd.DataFrame()
        try:
            empty.to_parquet(cache_path, index=False)
        except Exception:
            pass
        return empty
    if status != "000":
        log(f"[dart] event {endpoint} status={status} msg={data.get('message')} "
            f"corp={corp_code}", level="WARN")
        return pd.DataFrame()

    rows = data.get("list", [])
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    if "rcept_no" in df.columns:
        df["rcept_dt"] = pd.to_datetime(
            df["rcept_no"].astype(str).str[:8], format="%Y%m%d", errors="coerce",
        )

    # Coerce monetary/quantity/ratio fields. DART convention suffixes:
    #   _amt/_amount  — 금액 (KRW)
    #   _qy           — 수량 (주, 건)
    #   _stkqy        — 주식수
    #   _prc/_pric    — 가격/금액 (e.g. aqpln_prc_ostk, bdis_pric)
    #   _fta          — 사채 총액
    #   _rt           — 비율
    #   _irds         — 변동률
    #   _stkrt        — 주식 비율
    #   _ostk/_estk   — 보통주/우선주 (수량)
    for col in df.columns:
        if any(suffix in col.lower() for suffix in (
            "_amt", "_amount", "_qy", "_stkqy", "_prc", "_pric",
            "_fta", "_rt", "_irds", "_stkrt", "_ostk", "_estk",
        )):
            df[col] = (df[col].astype(str)
                       .str.replace(",", "", regex=False)
                       .str.replace("-", "0", regex=False)
                       .str.replace(" ", "", regex=False))
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["event_type"] = endpoint
    try:
        df.to_parquet(cache_path, index=False)
    except Exception as e:
        log(f"[dart] event cache write fail: {e}", level="WARN")
    return df


def fetch_insider_holdings(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """임원·주요주주 특정증권등 소유상황보고 (`elestock`).

    Returns rows for each insider transaction reporting.
    Key fields: repror (보고자), isu_exctv_rgist_at (등록임원 여부),
                isu_exctv_ofcps (직위), trade_dt (거래일), trade_stock_kind,
                trade_qy (거래수량), trade_uv (단가), trade_amt (총액),
                bsis_stkqy (변동전 보유수량), aft_stkqy (변동후 보유수량).

    Signal: net buy (aft - bsis) → positive alpha if executive, negative if sell.
    """
    return _fetch_dart_event("elestock", corp_code, bgn_de, end_de)


def fetch_major_holders(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """주식등의 대량보유상황보고 (`majorstock`).

    Reports of 5%+ holdings changes (institutions, foreign holders, activists).
    Key fields: repror (보고자), stkqy (보유주식수), stkrt (보유비율),
                stkqy_irds (변동수량), stkrt_irds (변동비율),
                report_resn (보고사유: 신규/변동/해소).

    Signal: stkrt_irds > 0 (new accumulation) → positive; < 0 → negative.
    """
    return _fetch_dart_event("majorstock", corp_code, bgn_de, end_de)


def fetch_capital_increase_decisions(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """유상증자결정 (`piicDecsn`). NEGATIVE signal — dilution.

    Key fields: nstk_ostk_qy (보통주 신주수), nstk_estk_qy (우선주 신주수),
                fv_ps (1주당 액면가), bdis_pric (발행가),
                fdpp_fclt (시설자금), fdpp_op (영업양수자금),
                pymd (납입일), ic_mthn (배정방법: 주주배정/제3자배정/일반공모).
    """
    return _fetch_dart_event("piicDecsn", corp_code, bgn_de, end_de)


def fetch_bonus_issue_decisions(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """무상증자결정 (`fricDecsn`). POSITIVE signal — psychological lift.

    Key fields: nstk_ostk_qy (보통주 신주수), nstk_estk_qy (우선주),
                fv_ps (액면가), nstk_asstd (배정주식수),
                bddd (이사회 결의일), nstk_dlprd (지급일).
    """
    return _fetch_dart_event("fricDecsn", corp_code, bgn_de, end_de)


def fetch_treasury_buyback_decisions(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """자기주식 취득결정 (`tsstkAqDecsn`). POSITIVE signal — strongest +.

    Key fields: aqpln_stk_ostk (취득예정 보통주), aqpln_stk_estk (우선주),
                aqpln_prc_ostk (취득예정금액 보통주), aqpln_prc_estk,
                aq_prtl_dt (취득예정 시작일), aq_endd (종료일),
                aq_mth (취득방법: 직접취득/신탁계약 등).
    """
    return _fetch_dart_event("tsstkAqDecsn", corp_code, bgn_de, end_de)


def fetch_treasury_sell_decisions(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """자기주식 처분결정 (`tsstkDpDecsn`). NEGATIVE signal — supply risk.

    Key fields: dppln_stk_ostk (처분예정 보통주), dppln_prc_ostk,
                dp_prtl_dt (처분예정 시작), dp_endd, dp_mth (처분방법).
    """
    return _fetch_dart_event("tsstkDpDecsn", corp_code, bgn_de, end_de)


def fetch_convertible_bond_decisions(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """전환사채(CB)발행결정 (`cvbdIsDecsn`). NEGATIVE — latent dilution.

    Key fields: bd_fta (사채총액), bd_intr_ex (이자율 내용),
                cv_prc (전환가격), cv_rt (전환비율),
                cv_rqstpd_bgd (전환청구기간 시작), cv_rqstpd_edd (종료).
    """
    return _fetch_dart_event("cvbdIsDecsn", corp_code, bgn_de, end_de)


def fetch_warrant_bond_decisions(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """신주인수권부사채(BW)발행결정 (`bdIsDecsn`). NEGATIVE — latent dilution.

    Key fields: bd_fta, ex_prc (행사가격), ex_rt (행사비율),
                ex_rqstpd_bgd (행사청구기간 시작), ex_rqstpd_edd.
    """
    return _fetch_dart_event("bdIsDecsn", corp_code, bgn_de, end_de)


def fetch_merger_decisions(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """합병결정 (`mgDecsn`). CASE-BY-CASE — synergy vs dilution.

    Key fields: mg_mth (합병방법), mg_rt (합병비율),
                mg_gmtsck_prd (합병승인 주총일).
    """
    return _fetch_dart_event("mgDecsn", corp_code, bgn_de, end_de)


def fetch_spinoff_decisions(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """분할결정 (`divDecsn`). CASE-BY-CASE — value unlocking vs holdco discount.

    Key fields: div_mth (분할방법: 인적분할/물적분할).
    Note: 물적분할 (90%+ Korean splits) → typically NEGATIVE (-5~10%, governance reform topic).
    """
    return _fetch_dart_event("divDecsn", corp_code, bgn_de, end_de)


def fetch_capital_reduction_decisions(corp_code: str, bgn_de: str, end_de: str) -> pd.DataFrame:
    """감자결정 (`crDecsn`). NEGATIVE — usually distress signal.

    Key fields: cr_rt (감자비율), cr_mth (감자방법).
    """
    return _fetch_dart_event("crDecsn", corp_code, bgn_de, end_de)


def fetch_all_events_for_corp(
    corp_code: str,
    bgn_de: str,
    end_de: str,
    event_types: Optional[list[str]] = None,
    polite_sleep_s: float = 0.15,
) -> pd.DataFrame:
    """Fetch all configured event types for one corp + date range.

    Returns long-format DataFrame with `event_category` column distinguishing rows.

    Args:
        event_types: subset of DART_EVENT_CATALOG keys. None = all.
    """
    types = event_types or list(DART_EVENT_CATALOG.keys())
    frames = []
    for t in types:
        if t not in DART_EVENT_CATALOG:
            log(f"[dart] unknown event_type: {t}", level="WARN")
            continue
        meta = DART_EVENT_CATALOG[t]
        endpoint = meta["endpoint"]
        df = _fetch_dart_event(endpoint, corp_code, bgn_de, end_de)
        time.sleep(polite_sleep_s)
        if df.empty:
            continue
        df = df.copy()
        df["event_category"] = t
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    # Different events have different schemas — use outer join via concat
    return pd.concat(frames, ignore_index=True, sort=False)


# ---------------------------------------------------------------------------
# Event scoring helpers — KRW-amount-based, not count-based
# ---------------------------------------------------------------------------
def _signed_cap(value: float, cap: float = 1.0) -> float:
    """Cap |value| to ±cap, preserving sign."""
    return float(max(-cap, min(cap, value)))


def _score_amount_pct_mcap(
    events_subset: pd.DataFrame,
    mcap: float,
    amount_field: str,
    weight: float,
    full_pct: float,
) -> float:
    """Sum amount_field across events, divide by mcap, scale by weight.

    Returns weight × min(1.0, sum_amt / mcap / full_pct).

    Used for: treasury_buyback (+), treasury_sell (-), convertible_bond (-),
    warrant_bond (-). The weight sign determines direction.
    """
    if events_subset.empty or mcap <= 0 or amount_field not in events_subset.columns:
        return 0.0
    total_amt = pd.to_numeric(events_subset[amount_field], errors="coerce").sum()
    if pd.isna(total_amt) or total_amt <= 0 or full_pct <= 0:
        return 0.0
    pct = float(total_amt) / float(mcap)
    magnitude = min(1.0, pct / full_pct)
    return weight * magnitude


def _score_computed_dilution(
    events_subset: pd.DataFrame,
    mcap: float,
    weight: float,
    full_pct: float,
) -> float:
    """Capital increase dilution magnitude.

    Sum across events of (nstk_ostk_qy × bdis_pric) / mcap → percentage.
    Negative weight applied (dilution is bearish).
    """
    if events_subset.empty or mcap <= 0:
        return 0.0
    qty_col = "nstk_ostk_qy"
    price_col = "bdis_pric"
    if qty_col not in events_subset.columns or price_col not in events_subset.columns:
        # Fallback: count-based binary if amount fields unavailable
        return weight * (0.5 if len(events_subset) > 0 else 0.0)
    qty = pd.to_numeric(events_subset[qty_col], errors="coerce").fillna(0)
    pric = pd.to_numeric(events_subset[price_col], errors="coerce").fillna(0)
    total_dilution_amt = float((qty * pric).sum())
    if total_dilution_amt <= 0 or full_pct <= 0:
        return 0.0
    pct = total_dilution_amt / float(mcap)
    magnitude = min(1.0, pct / full_pct)
    return weight * magnitude


def _score_binary(events_subset: pd.DataFrame, weight: float) -> float:
    """Binary score: weight if any event present, else 0.

    Used for: bonus_issue (+), spinoff (-), capital_reduction (-), merger (case).
    Multiple events in window do NOT stack (one-shot psychological effect).
    """
    return weight if len(events_subset) > 0 else 0.0


def _score_insider_net_buy(
    events_subset: pd.DataFrame,
    mcap: float,
    weight: float,
    full_pct: float,
) -> float:
    """Insider holdings net buy (KRW amount / mcap), signed.

    elestock 보고에서 (aft_stkqy − bsis_stkqy) × trade_uv 합산.
    + → 매수 우세 (positive alpha)
    − → 매도 우세 (negative alpha)

    Cap at ±full_pct of mcap → ±weight magnitude.
    """
    if events_subset.empty or mcap <= 0:
        return 0.0
    if "aft_stkqy" not in events_subset.columns or "bsis_stkqy" not in events_subset.columns:
        return 0.0
    aft = pd.to_numeric(events_subset["aft_stkqy"], errors="coerce").fillna(0)
    bef = pd.to_numeric(events_subset["bsis_stkqy"], errors="coerce").fillna(0)
    delta_qty = aft - bef
    if "trade_uv" in events_subset.columns:
        uv = pd.to_numeric(events_subset["trade_uv"], errors="coerce").fillna(0)
    elif "trade_amt" in events_subset.columns:
        # If unit price missing, derive average from total amount
        amt = pd.to_numeric(events_subset["trade_amt"], errors="coerce").fillna(0)
        # Sign already in delta_qty; magnitude via amt
        signed_amt = (delta_qty.apply(np.sign).fillna(0)) * amt.abs()
        net_amt = float(signed_amt.sum())
        pct = net_amt / float(mcap)
        return weight * _signed_cap(pct / full_pct if full_pct > 0 else 0.0)
    else:
        return 0.0
    signed_amt = delta_qty * uv
    net_amt = float(signed_amt.sum())
    if full_pct <= 0:
        return 0.0
    pct = net_amt / float(mcap)
    return weight * _signed_cap(pct / full_pct)


def _score_stkrt_change(
    events_subset: pd.DataFrame,
    weight: float,
) -> float:
    """Major holders stkrt_irds (% holding change) net sum.

    1.0% net change → full ±weight. Capped at ±1.0.
    """
    if events_subset.empty or "stkrt_irds" not in events_subset.columns:
        return 0.0
    net_pct = pd.to_numeric(events_subset["stkrt_irds"], errors="coerce").fillna(0).sum()
    return weight * _signed_cap(float(net_pct) / 1.0)


def compute_event_score_for_corp(
    events: pd.DataFrame,
    as_of: pd.Timestamp,
    lookback_days: int = 90,
    mcap: float = 0.0,
) -> dict:
    """KRW-amount-based event score (v2, 2026-04-28 redesign).

    Replaces v1 count-based score that exploded on large-cap insider noise
    (Samsung 2024: 2,614 insider rows × 0.30 = +784).

    For each event_category in DART_EVENT_CATALOG, apply mode-specific scoring:
      - amount_pct_mcap: sum amount / mcap, capped at full_weight_pct
      - computed_dilution: (nstk_ostk_qy × bdis_pric) / mcap
      - binary: weight if any event present
      - insider_net_buy: signed (delta × price) / mcap
      - stkrt_change: signed sum of stkrt_irds (already %)

    PIT-safe: uses rcept_dt <= as_of.

    Args:
        events: long-format DataFrame from fetch_all_events_for_corp
        as_of: rebalance date (PIT cutoff)
        lookback_days: rolling window length
        mcap: market cap at as_of (KRW). 0.0 → amount-based scores return 0.

    Returns:
        dict with per-category scores + total:
            {
                "total_score": float,
                "treasury_buyback": float,
                "capital_increase": float,
                ...
            }
    """
    out = {"total_score": 0.0}
    for cat in DART_EVENT_CATALOG:
        out[cat] = 0.0

    if events.empty or "event_category" not in events.columns:
        return out
    if "rcept_dt" not in events.columns:
        return out

    cutoff = as_of - pd.Timedelta(days=lookback_days)
    window = events[
        events["rcept_dt"].notna()
        & (events["rcept_dt"] >= cutoff)
        & (events["rcept_dt"] <= as_of)
    ]
    if window.empty:
        return out

    total = 0.0
    for cat, meta in DART_EVENT_CATALOG.items():
        cat_events = window[window["event_category"] == cat]
        if cat_events.empty:
            continue
        mode = meta["scoring_mode"]
        weight = meta["alpha_weight"]
        full_pct = meta.get("full_weight_pct") or 0.0
        amount_field = meta.get("amount_field")

        if mode == "amount_pct_mcap":
            score = _score_amount_pct_mcap(cat_events, mcap, amount_field, weight, full_pct)
        elif mode == "computed_dilution":
            score = _score_computed_dilution(cat_events, mcap, weight, full_pct)
        elif mode == "binary":
            score = _score_binary(cat_events, weight)
        elif mode == "insider_net_buy":
            score = _score_insider_net_buy(cat_events, mcap, weight, full_pct)
        elif mode == "stkrt_change":
            score = _score_stkrt_change(cat_events, weight)
        else:
            score = 0.0

        out[cat] = float(score)
        total += score

    out["total_score"] = float(total)
    return out


# ---------------------------------------------------------------------------
# Sanity test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("DART OpenAPI sanity test")
    print("=" * 60)

    print("\n[1/4] Download corp_code.xml...")
    try:
        xml_path = download_corp_code(refresh_days=1)
        print(f"      OK -> {xml_path} ({xml_path.stat().st_size//1024}KB)")
    except Exception as e:
        print(f"      FAIL: {e}")
        raise SystemExit(1)

    print("\n[2/4] Parse corp_code -> DataFrame...")
    df = parse_corp_code_to_df(xml_path)
    listed = df[df["stock_code"].str.len() == 6]
    print(f"      Total: {len(df)} corps, listed: {len(listed)}")
    print(f"      Sample listed:\n{listed.head(3).to_string(index=False)}")

    print("\n[3/4] Fetch corp_to_ticker_map...")
    mp = fetch_corp_to_ticker_map()
    samsung = mp[mp["corp_name"].str.contains("삼성전자", na=False)]
    print(f"      Samsung Electronics row:\n{samsung.head(2).to_string(index=False)}")

    if not samsung.empty:
        cc = samsung.iloc[0]["corp_code"]
        print(f"\n[4/5] Fetch Samsung 2023 annual financials (CFS)...")
        fin = fetch_single_company_financials(cc, 2023, "11011", fs_div="CFS")
        if fin.empty:
            print("      FAIL: empty financials")
        else:
            print(f"      Rows: {len(fin)}")
            keys = extract_key_accounts(fin)
            for k, v in keys.items():
                if isinstance(v, (int, float)):
                    print(f"        {k:25s} = {v:>20,.0f}")
                else:
                    print(f"        {k:25s} = {v}")

        print(f"\n[5/5] Fetch Samsung 2024 events (treasury buyback / capital raise / insider)...")
        events = fetch_all_events_for_corp(
            cc, "20240101", "20241231",
            event_types=["treasury_buyback", "capital_increase",
                         "insider_holdings", "major_holders"],
        )
        if events.empty:
            print("      No events in 2024.")
        else:
            print(f"      Rows: {len(events)}")
            counts = events["event_category"].value_counts()
            for cat, n in counts.items():
                meta = DART_EVENT_CATALOG[cat]
                print(f"        {cat:25s} ({meta['kr_name']:30s}) "
                      f"n={n}, weight={meta['alpha_weight']:+.2f}, "
                      f"mode={meta['scoring_mode']}")
            # Samsung mcap as of 2024-12-31 (approx): 600조원 = 6e14 KRW
            samsung_mcap = 6e14
            scores = compute_event_score_for_corp(
                events, pd.Timestamp("2024-12-31"),
                lookback_days=365, mcap=samsung_mcap,
            )
            print(f"\n      Event scores (samsung, 2024-12-31, 365d, mcap=600조):")
            for k, v in scores.items():
                if abs(v) > 1e-9 or k == "total_score":
                    print(f"        {k:25s} = {v:+.4f}")
    print("\nDone.")
