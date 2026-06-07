# Master Plan v0.1 — kr_quant_engine (2026-04-27)

**Current official gate**: KR1000 broker-ledger next-close 8y+ validation must clear CAGR `>= 30%`, MDD `>= -25%`, and KOSPI200 excess CAGR `> 0`. CAGR `>= 35%` is stretch only until the official gate is cleared.

**Core goal**: KOSPI + KOSDAQ 통합 유니버스에서 KOSPI200 초과수익 + CAGR 최대화. 1차 목표 CAGR 25%+ (Main), 35%+ (Concentrated N=5).

**Reference baseline (r1000-quant-engine)**:
- Main diversified: CAGR 23.58% / Sharpe 1.18 / MaxDD -23.17% / IR 0.9955
- Concentrated N=5: CAGR 33.40% / Sharpe 1.28 / MaxDD -25.29%

## Backtest Window
- **시작**: 2016-01-01 (KOSPI/KOSDAQ 종목코드 안정 + DART XBRL 일관성)
- **End**: 현재 (rolling)
- **Embargo**: 126일 (walk-forward look-ahead 방지)

## Universe Targets
- KOSPI 약 800 + KOSDAQ 약 1,500 = ~2,300 raw → 필터 후 **800–1,200 tradeable**
- 필터: 60일 일평균 거래대금 ≥ 5억원, 시총 ≥ 500억원, 관리/거래정지/SPAC 제외, 상장 ≥ 12개월

## Phase 우선순위

### P0. Bootstrap (1주, 진행중) — pykrx 가격 + 단순 momentum
- [x] 디렉토리 + scaffolding + docs
- [x] requirements.txt + .env (BOK 키)
- [ ] `kr_pykrx_client` — 일별 가격/시총/외인기관 캐시
- [ ] `kr_bok_client` — BOK 매크로 캐시
- [ ] `kr_universe.build_universe_monthly_v0` — 멤버십 + 기본 필터
- [ ] `kr_features.add_basic_momentum` — 1m/3m/6m/12m + RS_kospi/kosdaq
- [ ] `kr_pipeline.backtest_portfolio_v0` — Top-30 월 rebal, **31bp 왕복 cost 반영**
- [ ] P0 baseline: KOSPI200 동일가중 vs Top-30 momentum 5년 백테스트
- [ ] `tests/smoke_test.py` 통과

**Verdict 기준**: Top-30 momentum이 KOSPI200 + 3pp CAGR 이상 → P1 진입

### P1. Fundamental + DART PIT (2주) — DART 키 도착 후
- [ ] `kr_dart_client` — XBRL 파싱 + `rcept_dt` PIT 보존
- [ ] `kr_features.add_fundamental_features` — PER / PBR / ROE / 매출성장 / 영업이익률
- [ ] `value_inflection_score`, `turnaround_score`, `quality_score` (r1000 Phase 1 등가)
- [ ] 분기 lag 검증 (실제 공시일이 month-end 이전인지)
- [ ] **A/B**: P0 momentum vs P0+P1 blended

**Ship gate**: ΔCAGR ≥ +1pp AND ΔSharpe ≥ +0.05

### P2. Korean-specific Alpha (3주) — ★ 가장 큰 lift 기대
| 시그널 | 데이터 소스 | 가설 | 예상 IC |
|---|---|---|---|
| `foreign_net_buy_5d_zscore` | pykrx | 외인 매수 spike → 1m 초과수익 | 0.05–0.10 |
| `inst_net_buy_20d_zscore` | pykrx | 기관 누적 매수 → 3m 초과수익 | 0.04–0.08 |
| `theme_phase_score` | themes.yaml + 가격 | 테마 초기 진입 (rotation) | 0.06–0.12 |
| `short_interest_change` | KRX 대차잔고 | 공매도 감소 → 반등 | 0.03–0.06 |
| `chaebol_premium` | universe.yaml | 4대 그룹 spillover | 0.02–0.04 |
| `won_export_sensitivity` | USD/KRW 회귀 | 약달러 시기 수출주 | 0.04–0.08 |
| `disclosure_event_score` | DART | 자사주매입/배당확대 양성 | 0.05–0.09 |
| `overheating_avoidance` | KRX 단기과열 | 경고 종목 회피 | 0.03–0.05 |

각 시그널은 phase-gated → A/B 측정 가능.

### P3. Regime + Risk (2주)
- [ ] `kr_macro` — VKOSPI z-score, USD/KRW 변화, 외인 KOSPI 누적 net flow, BOK 금리
- [ ] Regime 라벨: `bull_trending`, `bull_peaking`, `bear_falling`, `recovery`, `stagflation_kr`, `won_crisis`
- [ ] Phase 4 등가물 (regime-conditional sleeve weights)
- [ ] Phase 6 등가물 (drawdown 사다리 + VKOSPI hard guard + vol targeting)

**Ship gate**: 2022년 KOSPI bear에서 -3pp 이내 보호

### P4. ML Walk-forward + Dual-sleeve (3주)
- [ ] CatBoost ensemble (reg + cls + rank) — r1000 그대로 포팅
- [ ] 126일 embargo 그대로
- [ ] Sleeve 분류: core / future / early — Korean 컨텍스트 재정의
- [ ] r1000 Phase 11 (multibagger) / Phase 14 (hybrid alpha) 패턴 도입

### P5. Concentrated + Paper Trading (2주)
- [ ] N=5/10/15 concentrated grid
- [ ] score_power weighting
- [ ] Paper trade: 키움 OpenAPI+ 또는 한국투자증권 API 통합 (실시간 검증)

## CAGR 최대화 레버 (한국 특화)
1. **회전율 제어**: 거래세 0.18% + 양방향 슬리피지 → 왕복 31bp. 월 25-35% 목표 (r1000은 45.5%). turnover 1pp 감소 ≈ CAGR 0.4pp 보존
2. **외인/기관 흐름**: r1000에 없는 시그널, 한국시장 검증된 alpha
3. **테마 회전**: 분기 단위 rotation (반도체, 2차전지, AI, 바이오 등) — themes.yaml + phase classifier
4. **소형주 sleeve 분리**: KOSDAQ 소형주 momentum 강함 + 변동성 큼 → 별도 sleeve로 CAGR + Sharpe 동시 개선
5. **공시 이벤트**: 자사주매입 / 무상증자 / 배당확대 → DART 단발성 alpha (1-3개월 hold)

## 현실적 목표
| 단계 | Main CAGR | Concentrated CAGR | MaxDD |
|---|---|---|---|
| P0 단순 momentum | KOSPI200 + 5pp ≈ 12-15% | – | -30% |
| + P1 fundamental | 18-20% | – | -28% |
| + P2 외인+테마+공시 | 22-25% | – | -25% |
| + P3 regime | 25-28% | – | **-20%** (방어 추가) |
| + P4 ML ensemble | 28-30% | – | -22% |
| + P5 concentrated N=5 | – | **35-40%** | -30% |

## 현재 액션 (DART 대기 중, 2026-04-27)

**즉시**:
1. P0 scaffolding 완료 (이번 세션)
2. pykrx + BOK 클라이언트 working code
3. universe builder v0 (가격 only)
4. 단순 momentum backtest → P0 baseline

**DART 도착 후 (Day 3+)**:
1. DART client + PIT 검증 테스트
2. P1 fundamental 시그널
3. P0 → P1 A/B

## Open Questions
- Git repo 전략: codex monorepo 안에 둘지 별도 repo로 분리할지 (P0 완료 시점 결정)
- Paper trading API 선택 (P5 시점)
- Drive 백업 경로 (Colab 동기화 필요해지면 셋업)
