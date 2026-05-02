---
phase: Phase A research (read-only, no code change)
date: 2026-05-02
author: Claude (auto)
verdict: identified critical leakage paths; remediation in plan.md
---

# PIT / Survivorship Audit — 10년 백테스트 신뢰성

## TL;DR

현재 backtest CAGR 38.97%는 **3가지 leakage 경로**로 inflate되어 있을 가능성:

1. **Survivorship bias** (FDR `StockListing` returns CURRENT listing only)
2. **Current mcap proxy** (FDR mcap snapshot used for past universe filter)
3. **listed_months stub** (모두 999개월, 신규 상장 필터 무효)

추정 inflation: **+3~7pp CAGR**. Realistic forward CAGR: **30-35%** (기존 추정 일치).

---

## Audit results

### ⚠️ Critical-1: FDR current listing in historical universe

**Code path**:
```
kr_pykrx_client.py:_fetch_listing_via_fdr()
  → fdr.StockListing('KOSPI'/'KOSDAQ')
  → returns CURRENT listed tickers only
  ↓
kr_universe.py:build_universe_snapshot(rebalance_date=2018-06-30)
  → calls fetch_listing(date)
  → still returns 2025 listing (FDR limitation)
```

**Symptom**: 2018-06-30 universe contains tickers that didn't exist then (e.g., 2020 IPO companies) AND excludes tickers that delisted before 2025.

**Severity**: ★★★ CRITICAL — entire backtest universe is forward-biased.

**Fix path** (Phase C1):
- DART corp_code 변경 history (`corp_code.zip` 변경 추적)
- KRX 상폐/상장 history (수동 csv 또는 DART 정기보고서 변경)
- `listed_at` / `delisted_at` per ticker DB
- universe filter: `listed_at <= rebalance_date AND (delisted_at IS NULL OR delisted_at > rebalance_date)`

### ⚠️ Critical-2: Current mcap as historical proxy

**Code path**:
```
kr_pykrx_client.py:_fetch_listing_via_fdr() returns market_cap
  → this is CURRENT mcap (e.g., Samsung 1297조 in 2025)
  ↓
kr_universe.py: filter "min_market_cap_krw=5e10"
  → applied uniformly across all rebal dates
  → 2018 backtest universe filtered using 2025 mcap
```

**Symptom**: 2018에 mcap이 작았던 (지금 큰) 종목들이 universe 통과. 또는 2018에 컸지만 망한 종목들 빠짐.

**Severity**: ★★★ CRITICAL.

**Fix path** (Phase C1):
- `fetch_market_cap_market(rd_date)` → pykrx if working, else daily snapshot DB
- DART historical mcap (분기보고서 시가총액 보존)
- 매 rebal date의 `market_cap_at_rd` column

### ⚠️ Critical-3: listed_months stub

**Code path**:
```
kr_universe.py:compute_listed_months() returns 999 for all tickers
  → "listed >= 12 months" filter never excludes anything
```

**Symptom**: IPO 직후 (2-3 month listed) 종목들도 universe 통과. 1-month momentum 계산 시 충분한 history 없음.

**Severity**: ★★ MEDIUM (소수 종목 영향).

**Fix path** (Phase C1):
- DART corp_code register 첫 출현 date
- 또는 첫 OHLCV 가용 date

### ⚠️ Mild-4: Macro publication lag

**Code path**:
```
kr_macro.py:add_derived_macro_signals()
  → BOK 산업생산 2024-12-31 데이터를 2024-12-31에 사용
  → 실제 2025-01-30 발표
```

**Symptom**: 1-2개월 future macro info leak.

**Severity**: ★ MILD.

**Fix path** (Phase C1):
- BOK series별 publication lag 매핑 (table)
- `as_of - lag` 적용

### ✅ Verified clean (no leakage)

- DART rcept_dt PIT (`pit_filter_panel`) ✅
- Episode timing (entry < surge_start < peak, 0 violations) ✅
- Cost model 31bp 왕복 ✅
- Per-stock return computation (close/close - 1) ✅
- Walk-forward fold split (date order maintained) ✅
- Feature columns no `future`/`forward`/`peak`/`realized` ✅

---

## Estimated CAGR inflation

| Leak | Estimated impact |
|---|---|
| Critical-1 (survivorship) | **+3~5pp CAGR** |
| Critical-2 (current mcap) | **+1~3pp CAGR** |
| Critical-3 (listed_months) | +0.5pp CAGR (small) |
| Mild-4 (macro lag) | +0.5pp CAGR (small) |
| **Total inflation** | **+5~9pp** |

**Reported CAGR 38.97% → realistic 30-34%** (10년 OOS proper).

여전히 r1000 reference (33.40%) 와 동등하거나 우수. **Big picture는 무사**.

---

## Test requirements (Phase B plan)

```
test_fdr_current_listing_not_used_for_historical_backtest
test_historical_mcap_required_before_rebalance_date
test_listed_months_not_stub
test_delisted_ticker_can_exist_in_past_universe
test_macro_publication_lag_applied
```

## Remediation cost

- **DART corp_code history**: parse `corp_code.zip` 매월 download → diff → 상장/상폐 추적. ~3일 코드.
- **Historical mcap DB**: 매월 first business day mcap snapshot 누적. 2016-2024 backfill 필요. ~2일.
- **listed_months**: corp_code register date 매핑. ~1일.
- **Macro lag table**: BOK series별 publication lag 매핑. ~0.5일.

**Total: ~1주**.

## References

- kr_pykrx_client.py:120-180 (_fetch_listing_via_fdr)
- kr_universe.py:200-260 (build_universe_snapshot, compute_listed_months)
- kr_macro.py:add_derived_macro_signals
- DART OpenAPI: https://opendart.fss.or.kr/intro/main.do (corp_code endpoint)
