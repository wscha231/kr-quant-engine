# 03 — Korean-Specific Alpha Signals

r1000에 없는, 한국시장 고유 알파 시그널 리서치. **CAGR lift의 가장 큰 원천**.

## 시그널 카탈로그 (P2 타겟)

| 시그널 | 데이터 | 가설 | 예상 IC | 우선 |
|---|---|---|---|---|
| `foreign_net_buy_5d_zscore` | pykrx 외인 매매 | 외인 매수 spike → 1m 초과수익 | 0.05–0.10 | ★★★ |
| `inst_net_buy_20d_zscore` | pykrx 기관 매매 | 기관 누적 매수 → 3m 초과수익 | 0.04–0.08 | ★★★ |
| `theme_phase_score` | themes.yaml + 가격 | 테마 초기 진입 | 0.06–0.12 | ★★★ |
| `disclosure_event_score` | DART | 자사주매입/배당확대 양성 | 0.05–0.09 | ★★ |
| `won_export_sensitivity` | USD/KRW 회귀 | 약달러 수출주 alpha | 0.04–0.08 | ★★ |
| `short_interest_change` | KRX 대차잔고 | 공매도 감소 → 반등 | 0.03–0.06 | ★★ |
| `chaebol_premium` | universe.yaml | 4대 그룹 spillover | 0.02–0.04 | ★ |
| `overheating_avoidance` | KRX 단기과열 | 경고 종목 회피 | 0.03–0.05 | ★ |

## 외국인/기관 매매 (가장 검증된 alpha)

KRX는 매일 거래주체별 매매대금 공개 (외국인/기관/개인/연기금/투신/사모펀드/기타).
- pykrx `stock.get_market_trading_volume_by_date()` 또는 `get_market_net_purchases_of_equities_by_ticker()`
- 5일 / 20일 / 60일 z-score
- 외국인은 KOSPI 대형주 alpha 강함, 기관은 KOSDAQ mid-cap에서 강함 (보고된 패턴)

## 테마 회전 (한국 특수)

KRX 테마 분류는 약 200개 (네이버/한경 기준). 테마 phase classifier:
- Phase 0: dormant (3m return < 0)
- Phase 1: emerging (3m return 0~+15%, 거래대금 spike)
- Phase 2: trending (3m return +15~+50%, 외인기관 net buy)
- Phase 3: parabolic (3m return > +50%) — 회피 대상
- Phase 4: rolling over (3m return < 0 from peak) — 회피

themes.yaml에 200개 테마 mapping. r1000 themes.yaml 패턴 차용.

## 공시 이벤트 (DART P1+)

긍정 이벤트:
- 자사주 매입/소각 공시
- 배당 확대 / 특별배당
- 무상증자 (보통 +5~10% 1주)
- 분할/합병 (case-by-case)
- 임원/주요주주 매수 보고서 (Form 5 등가)

부정 이벤트:
- 유상증자 (보통 -5~10% 1주)
- 전환사채/신주인수권부사채 발행
- 임원 매도

## TODO P2

- [ ] `p2_foreign_flow_ic.md` — 외국인 net buy z-score IC 측정
- [ ] `p2_inst_flow_ic.md` — 기관 동일
- [ ] `p2_theme_phase_classifier.md` — 테마 분류 + phase 정의
- [ ] `p2_dart_event_signals.md` — 공시 이벤트 +/- impact
- [ ] `p2_won_export_corr.md` — USD/KRW 회귀 + 수출주 dummy
