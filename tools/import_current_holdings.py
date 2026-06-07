"""Import a broker/export holdings file into canonical KR1000 holdings schema.

The daily broker check requires an actual account snapshot at
`DATA_ROOT/state/current_holdings.csv`. Broker CSVs usually have different
column names, especially Korean headers; this tool normalizes common export
formats into the canonical schema and writes a small audit JSON.

Run:
    py -3 tools/import_current_holdings.py --input path/to/broker_holdings.csv --as-of 2026-06-04 --account-id main --dry-run
    py -3 tools/import_current_holdings.py --input path/to/broker_holdings.csv --as-of 2026-06-04 --account-id main
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402
from kr1000_leader import CURRENT_HOLDINGS_COLUMNS, resolve_current_holdings_path  # noqa: E402


TEXT_EXTS = {".csv", ".txt", ".tsv"}
EXCEL_EXTS = {".xlsx", ".xls"}

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "as_of_date": ("as_of_date", "date", "기준일", "평가일", "조회일", "일자"),
    "account_id": ("account_id", "account", "account_no", "account_number", "계좌", "계좌번호", "계좌명"),
    "ticker": ("ticker", "symbol", "code", "stock_code", "종목코드", "단축코드", "표준코드", "코드"),
    "name": ("name", "stock_name", "security_name", "종목명", "종목", "상품명", "한글명"),
    "shares": ("shares", "quantity", "qty", "holding_qty", "보유수량", "수량", "잔고수량", "보유잔고", "주식수"),
    "avg_cost": ("avg_cost", "average_price", "avg_price", "cost_basis", "매입단가", "평균단가", "매입평균가", "평균매입가"),
    "last_price": ("last_price", "price", "current_price", "close", "현재가", "평가단가", "종가", "현재가격"),
    "market_value": ("market_value", "value", "amount", "valuation", "평가금액", "평가액", "평가잔고", "잔고평가금액"),
    "weight": ("weight", "portfolio_weight", "비중", "구성비", "평가비중"),
    "unrealized_pnl_pct": ("unrealized_pnl_pct", "pnl_pct", "return_pct", "수익률", "손익률", "평가손익률"),
    "thesis_tag": ("thesis_tag", "tag", "memo", "메모", "전략태그"),
    "manual_lock": ("manual_lock", "lock", "locked", "보유고정", "수동잠금"),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import broker holdings to KR1000 current_holdings.csv")
    p.add_argument("--input", required=True, help="Broker/export CSV, TSV, or XLSX file.")
    p.add_argument("--out", default=None, help="Output CSV. Default=DATA_ROOT/state/current_holdings.csv.")
    p.add_argument("--as-of", default=None, help="Snapshot date used when the source lacks a date column.")
    p.add_argument("--account-id", default=None, help="Account id used when the source lacks account column.")
    p.add_argument("--sheet", default=0, help="Excel sheet name/index. Default=0.")
    p.add_argument("--encoding", default=None, help="CSV encoding override. Default tries utf-8-sig then cp949.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--audit-json", default=None)
    return p.parse_args()


def _canonical_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s\-_()/\\.%]+", "", text)
    return text


def _alias_lookup() -> dict[str, str]:
    out: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            out[_canonical_header(alias)] = canonical
    return out


def read_source_table(path: Path, *, encoding: str | None = None, sheet: str | int = 0) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTS:
        encodings = [encoding] if encoding else ["utf-8-sig", "cp949", "utf-8"]
        last_exc: Exception | None = None
        sep = "\t" if suffix == ".tsv" else ","
        for enc in encodings:
            if not enc:
                continue
            try:
                return pd.read_csv(path, dtype=str, encoding=enc, sep=sep)
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(f"failed to read text holdings file {path}: {last_exc}")
    if suffix in EXCEL_EXTS:
        return pd.read_excel(path, sheet_name=sheet, dtype=str)
    raise ValueError(f"unsupported holdings file extension: {suffix}")


def _rename_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    lookup = _alias_lookup()
    rename: dict[str, str] = {}
    used: set[str] = set()
    for col in df.columns:
        canonical = lookup.get(_canonical_header(col))
        if canonical and canonical not in used:
            rename[col] = canonical
            used.add(canonical)
    return df.rename(columns=rename), {str(k): str(v) for k, v in rename.items()}


def _parse_number(value: Any, *, pct: bool = False) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "-"}:
        return 0.0
    neg = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    had_pct = "%" in text
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return 0.0
    try:
        val = float(text)
    except ValueError:
        return 0.0
    if neg:
        val = -val
    if pct or had_pct:
        if abs(val) > 1.0:
            val /= 100.0
    return float(val)


def _parse_bool(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "t", "yes", "y", "lock", "locked", "고정", "잠금", "예", "네"}


def _normalize_ticker(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\.0$", "", text)
    digits = re.sub(r"[^0-9]", "", text)
    return digits.zfill(6) if digits else ""


def normalize_holdings_frame(
    raw: pd.DataFrame,
    *,
    as_of_date: str | None = None,
    account_id: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    renamed, column_map = _rename_columns(raw)
    out = pd.DataFrame(index=renamed.index)
    for col in CURRENT_HOLDINGS_COLUMNS:
        if col in renamed.columns:
            out[col] = renamed[col]
        else:
            out[col] = ""

    default_date = str(pd.Timestamp(as_of_date).date()) if as_of_date else str(pd.Timestamp.today().date())
    out["as_of_date"] = out["as_of_date"].where(out["as_of_date"].astype(str).str.strip() != "", default_date)
    if account_id is not None:
        out["account_id"] = out["account_id"].where(out["account_id"].astype(str).str.strip() != "", str(account_id))
    out["ticker"] = out["ticker"].map(_normalize_ticker)
    out["name"] = out["name"].astype(str).replace({"nan": ""})
    out["thesis_tag"] = out["thesis_tag"].astype(str).replace({"nan": ""})
    out["manual_lock"] = out["manual_lock"].map(_parse_bool)

    for col in ("shares", "avg_cost", "last_price", "market_value", "weight"):
        out[col] = out[col].map(_parse_number)
    out["unrealized_pnl_pct"] = out["unrealized_pnl_pct"].map(lambda x: _parse_number(x, pct=True))

    before_rows = int(len(out))
    out = out[out["ticker"].astype(str).str.len().gt(0)].copy()
    out = out[pd.to_numeric(out["shares"], errors="coerce").fillna(0.0) > 0].copy()

    if not out.empty:
        missing_mv = pd.to_numeric(out["market_value"], errors="coerce").fillna(0.0) <= 0
        out.loc[missing_mv, "market_value"] = (
            pd.to_numeric(out.loc[missing_mv, "shares"], errors="coerce").fillna(0.0)
            * pd.to_numeric(out.loc[missing_mv, "last_price"], errors="coerce").fillna(0.0)
        )
        missing_price = pd.to_numeric(out["last_price"], errors="coerce").fillna(0.0) <= 0
        shares = pd.to_numeric(out["shares"], errors="coerce").fillna(0.0)
        mv = pd.to_numeric(out["market_value"], errors="coerce").fillna(0.0)
        out.loc[missing_price & (shares > 0), "last_price"] = mv[missing_price & (shares > 0)] / shares[missing_price & (shares > 0)]
        total_mv = float(pd.to_numeric(out["market_value"], errors="coerce").fillna(0.0).sum())
        if total_mv > 0:
            out["weight"] = pd.to_numeric(out["market_value"], errors="coerce").fillna(0.0) / total_mv

    out = out.drop_duplicates("ticker", keep="last").reset_index(drop=True)
    audit = validate_current_holdings_frame(out)
    audit.update({
        "source_rows": before_rows,
        "output_rows": int(len(out)),
        "dropped_rows": int(before_rows - len(out)),
        "column_map": column_map,
    })
    return out[list(CURRENT_HOLDINGS_COLUMNS)], audit


def validate_current_holdings_frame(df: pd.DataFrame) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    missing = [c for c in CURRENT_HOLDINGS_COLUMNS if c not in df.columns]
    if missing:
        issues.append({"severity": "CRITICAL", "message": "missing_required_columns", "columns": missing})
    if df.empty:
        issues.append({"severity": "CRITICAL", "message": "current_holdings_empty"})
    if "ticker" in df.columns:
        blank = int(df["ticker"].astype(str).str.strip().eq("").sum())
        if blank:
            issues.append({"severity": "CRITICAL", "message": "blank_ticker_rows", "rows": blank})
        dupes = int(df["ticker"].astype(str).duplicated().sum())
        if dupes:
            issues.append({"severity": "HIGH", "message": "duplicate_ticker_rows", "rows": dupes})
    if "shares" in df.columns:
        shares = pd.to_numeric(df["shares"], errors="coerce").fillna(0.0)
        if not (shares > 0).any():
            issues.append({"severity": "CRITICAL", "message": "no_positive_share_rows"})
        neg = int((shares < 0).sum())
        if neg:
            issues.append({"severity": "CRITICAL", "message": "negative_share_rows", "rows": neg})
    if "market_value" in df.columns:
        mv = pd.to_numeric(df["market_value"], errors="coerce").fillna(0.0)
        if len(df) and float(mv.sum()) <= 0:
            issues.append({"severity": "HIGH", "message": "non_positive_total_market_value"})
    return {
        "rows": int(len(df)),
        "issues": issues,
        "summary": {
            "critical": sum(1 for x in issues if x["severity"] == "CRITICAL"),
            "high": sum(1 for x in issues if x["severity"] == "HIGH"),
            "medium": sum(1 for x in issues if x["severity"] == "MEDIUM"),
        },
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    out_path = Path(args.out) if args.out else resolve_current_holdings_path()
    audit_path = Path(args.audit_json) if args.audit_json else DATA_ROOT / "outputs" / "current_holdings_import_audit.json"

    raw = read_source_table(input_path, encoding=args.encoding, sheet=args.sheet)
    normalized, audit = normalize_holdings_frame(raw, as_of_date=args.as_of, account_id=args.account_id)
    payload = {
        "input": str(input_path),
        "out": str(out_path),
        "dry_run": bool(args.dry_run),
        **audit,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    if not args.dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        normalized.to_csv(out_path, index=False, encoding="utf-8-sig")

    print("KR1000 current holdings import")
    print(f"  input:    {input_path}")
    print(f"  out:      {out_path}")
    print(f"  rows:     {payload['rows']}")
    print(f"  critical: {payload['summary']['critical']}")
    print(f"  high:     {payload['summary']['high']}")
    print(f"  audit:    {audit_path}")
    return 0 if payload["summary"]["critical"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
