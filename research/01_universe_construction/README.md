# 01 — Universe Construction

KOSPI + KOSDAQ 통합 유니버스 멤버십 + 필터 결정 산출물.

## 핵심 결정 사항

### Hard exclusions (P0)
- 우선주 (종목코드 끝 5/7/9)
- ETF, ETN, ELW (rebal date에 KRX 분류 기준)
- 관리종목, 거래정지, 정리매매 (rebal date 시점 기준)
- SPAC (스팩)
- 리츠 (선택적 — P1에서 결정)

### Soft filters (P0)
- 60일 일평균 거래대금 ≥ 5억원 (5e8 KRW) [tunable]
- 시가총액 ≥ 500억원 (5e10 KRW) [tunable]
- 상장 후 ≥ 12개월 (lookback 확보)

### Membership snapshot
매월 첫 영업일에 universe 멤버십 freeze. rebalance date 기준 PIT.

## 통합 전략 (KOSPI + KOSDAQ)

r1000_unified_universe.py 패턴 차용:
- 두 거래소 종목을 한 DataFrame으로 union
- `exchange` 컬럼 (KOSPI / KOSDAQ)으로 구분
- 시그널 계산 시 cross-sectional ranking은 통합 universe 기준
- 일부 시그널 (RS_kospi, RS_kosdaq)은 거래소별 별도 계산
- Sleeve 내 거래소 비중 제한 (예: KOSDAQ 30% 이내) — P3+에서 재검토

## TODO P0

- [ ] `p0_kospi_kosdaq_unified_v0.md` — 첫 통합 universe count by month
- [ ] `p0_filter_threshold_sweep.md` — 거래대금/시총 필터 임계값 grid
- [ ] `p0_exclusion_audit.md` — 관리/SPAC/우선주 제외 후 잔여 명세
- [ ] `p0_listing_history.md` — 2016-2026 신규상장/상폐 시계열
