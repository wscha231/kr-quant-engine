# 00 — Data Sources Audit

각 데이터 소스 (pykrx, DART, BOK ECOS, KOSIS, FinanceDataReader)의 커버리지 + 신뢰도 + 알려진 함정을 문서화. 새 소스 추가 시 여기에 audit 먼저 작성.

## 데이터 소스 매트릭스

| Source | Free? | API 키 | 용도 | 갱신 주기 | 신뢰도 |
|---|---|---|---|---|---|
| pykrx | ✓ | 불필요 | 가격, 시총, PER/PBR, 외인기관 | 일별 16:00 KST 이후 | A |
| DART OpenAPI | ✓ | 필요 (pending) | 공시, XBRL 재무, 임원매매 | 수시 | A |
| FinanceDataReader | ✓ | 불필요 | 보조 (listing, 지수, 환율) | 일별 | B |
| BOK ECOS | ✓ | 보유 ✅ | 한국 매크로 (금리, 환율, 산업생산) | 월별/일별 | A |
| KOSIS | ✓ | 미신청 | 거시 통계 (CSI, BSI) | 월별 | A |
| KRX 공시채널 | ✓ | 불필요 (scrape) | 단기과열/투자경고/관리종목 | 일별 | A |
| Naver/Daum | ✓ | 불필요 (scrape) | 컨센서스 (보수적 파싱) | 주별 | C |
| FRED + yfinance | ✓ | 불필요 | USD/KRW, 글로벌 매크로 보조 | 일별 | A |

## 알려진 함정 (TBD — 실제 fetch 후 채움)

- pykrx: KRX 사이트 응답 느릴 때 `requests.exceptions.ReadTimeout` 빈번 → backoff 필요
- DART: `corp_code.zip`이 매일 갱신, 캐시 expiry 1일 권장
- BOK ECOS: 영업일 외 fetch 시 빈 array 반환 (에러 X) → 빈 array 체크 필수
- 가격제한 처리: pykrx OHLCV의 종가가 상한가일 때 백테스트 fill 가능 여부 별도 처리 필요

## TODO P0

- [ ] `p0_pykrx_coverage.md` — 일별 fetch 시 KOSPI/KOSDAQ 커버리지 % + null rate
- [ ] `p0_bok_ecos_keymap.md` — 사용할 BOK series code 매핑
- [ ] `p0_listing_changes_audit.md` — 2016-2026 KRX 종목코드 변경/상폐 이력 (FinanceDataReader)
