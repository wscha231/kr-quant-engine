---
phase: Phase A research (post-deployment)
date: 2026-05-04
author: Claude (auto)
trigger: User request "물량뺏기인지 진짜 무너지는건지 잘 파악"
---

# Shake-out vs Distribution Classifier — Research Note

## 0. TL;DR

급락 후 다시 신고가로 돌아오는 종목 (= shake-out, 물량뺏기) vs 신고가 회복 못하는 종목 (= distribution, 진짜 분배)을 구분하는 것은 **수십 년 검증된 영역**이다. 1920s Wyckoff method부터 현대 ML까지 일관된 답:

```
Shake-out signature:
  - 짧고 깊은 급락 (1-5일)
  - 평균 대비 큰 거래량 (2-5x)
  - 외국인/기관 순매수 (개인이 panic sell)
  - 뉴스/공시 negative 없음 (가격만 빠짐)
  - 동종 업종/섹터는 동행 하락 안 함
  - 회복 시 거래량 함께 증가 → 빠른 V자

Distribution signature:
  - 점진적/계단식 하락 (수주~수개월)
  - 거래량은 평균 또는 작음 (low volume sell-off)
  - 외국인/기관 순매도 (smart money exit)
  - 신고가 후 negative news/공시 동반
  - 섹터 동반 약세
  - 반등 시도 → 50% 정도 회복 후 다시 하락
```

해결책: **rule-based gating + ML classifier hybrid**. 룰 기반 trigger (dip event 정의)로 후보 사건 추출 → ML로 shake-out 확률 예측 → 임계값 기반 hold/sell 결정.

---

## 1. 학문적/업계 기반

### 1.1 Wyckoff Method (1920s, Richard Wyckoff)

Stage 4단계: **Accumulation → Markup → Distribution → Markdown**

| Stage | 가격 패턴 | 거래량 패턴 | 우리 매핑 |
|---|---|---|---|
| Accumulation | 횡보 + 가짜 하향 돌파 (Spring) | 큰 거래량 + 회복 | shake-out 직전 |
| Markup | 상승 + 일시 dip + 회복 | 상승 시 큰 거래량, dip 시 작은 | shake-out 본질 |
| Distribution | 횡보 + 가짜 상향 돌파 (Upthrust) | 큰 거래량 후 흡수 안 됨 | breakdown 직전 |
| Markdown | 하락 지속 | 작은 거래량 (low-volume sell-off) | distribution 종료 |

**핵심 개념**:
- **Spring** = 지지선 잠깐 깬 후 즉시 회복 (물량뺏기) → bullish
- **Upthrust** = 저항선 잠깐 깬 후 즉시 거부 (진짜 분배) → bearish
- 두 패턴은 동전의 양면. 각각 reverse direction의 shake-out/distribution.

### 1.2 Volume Spread Analysis (Tom Williams, 1980s)

VSA 4 핵심 개념:
1. **Effort vs Result**: 큰 effort (volume)에 작은 result (price range) = absorption
2. **No supply / No demand**: 작은 거래량으로 움직임 = 약한 신호
3. **Stopping volume**: 하락 중 큰 거래량 + 회복 = 매집 (= shake-out)
4. **Up-thrust** vs **Selling Climax**

VSA에서 shake-out 시그널:
- 하락 캔들 + 큰 거래량 + 종가가 캔들 high 근처 → 매집
- 하락 캔들 + 큰 거래량 + 종가가 캔들 low 근처 → 분배

### 1.3 Mark Minervini / O'Neil CANSLIM

CANSLIM의 **shake-out** 정의:
- 강한 base 형성 후 일시적 break-down (50일선 이탈 또는 -8% drop)
- 거래량 평균 이상
- 7-10일 이내 회복하면 = bullish shake-out (계속 보유)
- 회복 못하면 = stage failure → exit

Minervini의 8% rule + tightening pivots:
- 매수가 -7~8% 손절 (default)
- 단, "shake-out probability high"이면 보유 OK
- VCP (Volatility Contraction Pattern) 직후 shake-out은 매우 흔함

### 1.4 Quantitative literature

**Chan, Quantitative Trading (2009)**:
- "Mean reversion vs momentum" 구분
- 짧은 시간 (1-5일) mean reversion = shake-out
- 긴 시간 (수주+) momentum down = distribution

**Lopez de Prado, Advances in Financial ML (2018)**:
- "Triple barrier method" — labeling for trend following
- shake-out classifier에 정확히 매핑됨
- 상단/하단 barrier + time barrier로 label

**Andreas Clenow, Stocks on the Move**:
- Dip 후 회복 패턴이 momentum strategy의 핵심
- "Continuation gap" vs "Exhaustion gap"

---

## 2. 한국시장 특화 시그널

### 2.1 외국인/기관/개인 일별 매매대금 (KRX 공시)

이게 한국시장의 **가장 강력한 differentiator**. 미국 시장은 dark pool 때문에 institutional flow가 lag 있는 반면, KRX는 일별 명확한 공시.

| 시그널 | shake-out | distribution |
|---|---|---|
| 외국인 net (dip day) | + (매수) | - (매도) |
| 기관 net (dip day) | + 또는 0 | - |
| 개인 net (dip day) | - (panic sell) | + (catching falling knife) |
| 외국인 5일 누적 | trend up | trend down |

**증거**: 2020 COVID 폭락 (3월), 2022 KOSPI 약세 — 외국인 순매수 stocks이 V자 회복, 외국인 순매도 stocks은 계단식 하락.

### 2.2 가격제한폭 (±30%) hit

KRX 가격제한폭은 강한 시그널:
- 하한가 hit → 매수 incoming wave 평가 필요
  - 하한가 후 다음날 +20%+ 회복 = shake-out
  - 하한가 다음날 다시 약세 = distribution
- 상한가 hit는 momentum 시그널 (별개)

### 2.3 단기과열 / 투자경고 / 관리종목

KRX 공시:
- 단기과열 종목 = 거래량 + 가격 비정상 → 종종 shake-out 직전 신호 (반대로)
- 투자경고 종목 = real risk → distribution 가능성↑
- 관리종목 = 사실상 distribution 진행 중

### 2.4 DART 공시 이벤트

Dip이 공시 이후 발생하면:
- positive 공시 후 dip = shake-out 가능성↑ (호재 출회)
- negative 공시 후 dip = distribution 가능성↑

key DART events (이미 catalog 있음):
- 유상증자, CB/BW, 자사주처분 → distribution 직전 흔함
- 자사주매입, 무상증자 → shake-out 직후 흔함

### 2.5 공매도 잔고

KRX 공매도 잔고 공시:
- 공매도 잔고 증가 + 가격 하락 = distribution
- 공매도 잔고 감소 (커버) + 가격 회복 = short squeeze (shake-out 변형)

---

## 3. 사용자 예시 분석

### 오클로 (Oklo, OKLO) / SMR 패턴

사용자 관찰: "빠졌을때 신고가 부분을 다시 못가는 경향"

이는 **distribution 패턴의 교과서적 사례**:
- 2024년 후반 SMR 테마 광풍 (NVIDIA AI → 전력 부족 → SMR 수요)
- Oklo IPO 후 +700% (2024-09 ~ 11)
- 그 후 점진적 거래량 감소 + 가격 횡보
- Institutional flow: peak 매수 → 분배
- 메인 holders: VCs / insiders unlock 시점
- 결과: 신고가 회복 못함 → distribution

**식별 가능한 시그널 (사후 검증)**:
1. Volume profile: peak 후 거래량 점진 감소
2. Insider sales: SEC Form 4 filing
3. Sector-relative: SMR 섹터 동반 약세
4. Implied volatility: peak 시 IV 매우 높음 → IV crush

### 한국시장 동등 사례

비슷한 패턴:
- 에코프로 2023년 7월 peak → 9월 -50% → 회복 못함 (distribution)
  - 외국인/기관 매도 vs 개인 매수 패턴 명확
- 한미반도체 2024년 peak 후 점진 약세 → 신고가 못 감
- vs LG에너지솔루션 2022년 IPO 직후 폭락 후 반등 (shake-out)

---

## 4. 우리 시스템 적용 — 데이터 매핑

### 4.1 이미 있는 데이터 (Phase C 적용 후)

| 데이터 | 위치 | shake-out 활용 |
|---|---|---|
| Daily OHLCV | `cache_pykrx/ticker_*` | dip 정의, 거래량 |
| 외국인 net buy | `kr_naver_flow.py` | smart money flow |
| 기관 net buy | `kr_naver_flow.py` | smart money flow |
| 개인 net buy | derived | dumb money sell |
| DART events | `cache_dart/events/*` | 공시 trigger |
| KRX 단기과열 | `kr_krx_scraper.py` | overheat flag |
| 시가총액 PIT | `data_pit/historical_mcap.parquet` | mcap-tier filter |
| Listed history | `data_pit/listed_history.parquet` | survivorship-free |
| Governance risk | `kr_governance.py` | distribution prior |

### 4.2 추가 필요 데이터

| 데이터 | 소스 | 우선순위 |
|---|---|---|
| 공매도 잔고 (per ticker, daily) | KRX 공시 또는 데이터 벤더 | ★★★ |
| 5분봉 / 1분봉 OHLCV | KIS API 또는 별도 | ★★ (intraday detection) |
| 외국인 보유 비율 | KRX 또는 FnGuide | ★★★ |
| 섹터 분류 (GICS or KRX 업종지수) | DART corp_code induty_code | ★★ (sector neutralization) |
| 호가창 imbalance | 별도 스트리밍 데이터 | ★ (advanced) |

---

## 5. 분류기 설계

### 5.1 Label 정의 (Triple-barrier method)

```python
# Trigger event: 큰 일일 dip 발생
TRIGGER_THRESHOLD = -0.05  # -5% in one day, OR
TRIGGER_DRAWDOWN_5D = -0.10  # 5-day drawdown -10%+ from local peak

# Outcome window: T+N days
OUTCOME_WINDOW_DAYS = 20  # 한 달 기준

# Label:
#   1 (shake-out): T+N 일 close >= T-day's pre-dip peak * 0.95
#   0 (distribution): T+N 일 close < dip low * 1.05
#   skip: ambiguous (in between)
```

### 5.2 Feature engineering

5 categories:

**A. Price action (10 features)**
- `dip_magnitude_pct` — pre-dip peak 대비 dip low %
- `dip_duration_days` — peak에서 trough까지 일수
- `pre_dip_uptrend_strength` — 30일 전 RS_kospi
- `pre_dip_consolidation_days` — base 형성 일수
- `dist_from_52w_high` — dip 시점
- `dist_from_50_ma` — dip 시점
- `dist_from_200_ma` — dip 시점
- `bollinger_band_position` — dip 시점
- `rsi_14_at_dip` — RSI value
- `atr_14_pct_at_dip` — volatility

**B. Volume (8 features)**
- `vol_dip_day_zscore_60d` — dip 일 거래량 z-score
- `vol_5d_avg_vs_60d_avg` — recent volume profile
- `vol_dryup_pct_pre_dip` — dip 전 거래량 dryup
- `obv_trend_30d` — OBV slope
- `accumulation_distribution_line_slope` — A/D line slope
- `vol_recovery_day_1_to_5` — dip 다음 5일 거래량 합 vs dip 일
- `large_block_trade_count` — 큰 매매 (대량 거래) 빈도
- `intraday_volume_imbalance` — open vs close volume

**C. Flow signals (한국 특화, 10 features)**
- `foreign_net_buy_dip_day_zscore` — 외국인 dip 일 z-score
- `foreign_net_buy_5d_post_dip` — 외국인 5일 누적 (post-dip)
- `inst_net_buy_dip_day_zscore` — 기관 dip 일
- `inst_net_buy_5d_post_dip` — 기관 5일 누적
- `individual_net_sell_dip_day_zscore` — 개인 panic sell 강도
- `foreign_holding_pct_change_30d` — 외국인 보유율 변화
- `foreign_buying_streak_post_dip` — 외국인 연속 순매수 일수
- `inst_buying_streak_post_dip` — 기관 연속 순매수
- `short_interest_change_30d` — 공매도 잔고 변화
- `short_squeeze_score` — 공매도 잔고 vs 회복 강도

**D. Context / regime (8 features)**
- `kospi_drawdown_at_dip` — 시장 drawdown
- `vkospi_at_dip` — 변동성 지수
- `sector_drawdown_at_dip` — 동종 섹터 약세 (sector-relative)
- `kospi_above_ma200` — long-term regime
- `usd_krw_change_5d` — 환율 충격
- `regime_label_kr` — 우리 regime classifier
- `time_of_year` — 분기 말 / 결산 등 seasonal
- `pre_earnings_window` — 실적 발표 임박 여부

**E. Event triggers (6 features)**
- `dart_event_within_5d_pre_dip` — 직전 공시 이벤트
- `dart_event_severity_score` — 공시 negative 정도
- `dilution_event_within_30d` — 유상증자/CB 발행
- `governance_risk_score` — 우리 governance overlay
- `overheating_flag_within_5d` — KRX 단기과열 직전
- `news_sentiment_change` — 뉴스 분위기 (NLP, 추가 작업 필요)

### 5.3 Modeling

**Step 1**: Episode mining
- 모든 종목 × 모든 day 에서 trigger event 탐지
- 5년 historical: 추정 ~50,000 events
- Filter: shake-out 가능성 있는 universe (mcap >= 5천억, 거래량 >= threshold)

**Step 2**: Label
- Triple barrier method (위 5.1)
- Class balance: shake-out ~30%, distribution ~30%, ambiguous ~40%

**Step 3**: Train
- CatBoost binary (shake-out vs distribution)
- Walk-forward purged (현재 우리 system에 이미 있음)
- 9-month embargo (dip event window 매핑)

**Step 4**: Calibration
- Predicted P(shake-out) → 실제 frequency
- Bin: [0.0-0.3-0.5-0.7-1.0]
- Decision threshold:
  - P > 0.7 → HOLD (very likely shake-out)
  - 0.4 < P < 0.7 → REDUCE 50% (uncertain)
  - P < 0.4 → SELL (likely distribution)

**Step 5**: Validation
- Confusion matrix:
  - True positive (shake-out 정확): 보유 → 회복 수익
  - False positive (shake-out 오인): 보유 → 추가 손실
  - True negative (distribution 정확): 매도 → 손실 한정
  - False negative (distribution 놓침): 매도 후 회복 → 기회 손실
- 비대칭 비용: false positive (보유 → -50%) >> false negative (매도 → -10%)
- → P(shake-out) 임계값 보수적으로 (0.7+)

### 5.4 Live application

운영 시:
1. **Daily monitor**: 모든 보유 종목에서 dip event trigger 검사
2. **Trigger 발생 시**: classifier 호출
3. **Output**: P(shake-out) + decision (HOLD/REDUCE/SELL)
4. **Slack alert**: P < 0.5 시 alert + 사용자 manual review

워크플로우: 기존 `daily_event_monitor.yml` 확장 가능. 또는 별도 `daily_dip_classifier.yml`.

---

## 6. 구현 단계 (Phase F 가칭)

### F1 — Episode mining (1주)
```
tools/mine_dip_episodes.py
  Input: kr_universe (5년 history) + cache_pykrx ohlcv
  Output: data/dip_episodes/dip_events_<period>.parquet
    Columns: ticker, dip_date, dip_magnitude, peak_date, trough_date,
             outcome_label (shake-out=1, distribution=0, ambiguous=NaN)
```

### F2 — Feature panel (1주)
```
kr_dip_features.py
  prepare_dip_feature_panel(episodes, panels)
  Output: panel with 42 features per dip event
```

### F3 — Classifier training (3일)
```
tools/train_dip_classifier.py
  Walk-forward purged
  CatBoost + LightGBM ensemble (이미 있는 인프라 재사용)
  Output: models/dip_classifier_<stamp>.cbm + feature_cols
```

### F4 — Live monitor integration (3일)
```
tools/dip_monitor.py
  - 매일 보유 종목 + watchlist scan
  - Trigger detected → classifier predict
  - Slack alert with P(shake-out) + recommendation

.github/workflows/daily_dip_monitor.yml
  - 평일 17:00 KST (장 마감 후)
  - dip_events_today.json 출력
```

### F5 — Backtest validation (1주)
```
tools/backtest_dip_strategy.py
  - Hold-vs-sell 시뮬레이션
  - vs naive 5% stop-loss
  - vs naive hold-everything
  - Report: realized return, MDD, win rate by quintile of P(shake-out)
```

**총 예상**: ~3-4주.

---

## 7. 전제 / 위험

### 7.1 한계
- ML classifier는 과거 패턴 학습 → 새 패턴 (예: 메이저 sector rotation) 놓칠 수 있음
- "이번엔 다르다" — 2020 COVID 같은 unprecedented events
- 대형 macro shock (금융위기) 시 모든 종목 distribution 패턴 → classifier 무력
- News sentiment / fundamental shift는 OHLCV/flow 데이터로 부분만 잡힘

### 7.2 보호 조치
- Macro overlay: VKOSPI > 35 시 classifier override → 모두 SELL
- Hard stop loss: -25% 도달 시 classifier 무시하고 SELL
- Position size cap: classifier 신뢰 못해도 단일 종목 max 10% (concentrated mode 사용 시 더 strict 필요)

### 7.3 실거래 차이
- Backtest: dip event 사후에만 트리거 → 실거래는 실시간 detect 필요
- Slippage: 큰 거래량 dip day는 slippage 커짐 → 비용 모델 보수적
- 가격제한폭: 하한가 락 → 매도 불가능 → 다음날 갭다운

---

## 8. 참고 문헌

### 학술
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. (Triple barrier method, purged CV)
- Bouchaud, J.-P., Bonart, J., et al. (2018). *Trades, Quotes and Prices: Financial Markets Under the Microscope*. (Microstructure)
- Kakushadze & Yu (2017). "151 Trading Strategies"

### 실무
- Wyckoff (1931). *The Day Trader's Bible* (출판은 1986).
- Williams, T. (1993). *The Master of the Markets*. (VSA 시조)
- Coulling, A. (2013). *A Complete Guide to Volume Price Analysis*.
- Minervini, M. (2013). *Trade Like a Stock Market Wizard*. (Shake-out 분석)
- O'Neil, W. (2009). *How to Make Money in Stocks*. (CANSLIM, base patterns)
- Connors, L. (2009). *Short Term Trading Strategies that Work*. (Mean reversion ML)

### 한국시장
- 한국거래소 시장 구조 자료 (KRX).
- 자본시장연구원 보고서: 외국인 매매 행태와 주가 영향.
- 자본시장연구원: 단기과열 종목 지정 효과 분석.

### 관련 기존 우리 노트
- `research/03_korea_specific_signals/governance_risk_overlay_research.md`
- `research/06_walkforward_baselines/purged_oos_protocol.md`
- `research/02_factor_zoo/institutional_methods_import_map.md` — Citadel sleeve / Wyckoff 토픽

---

## 9. 즉시 시작 가능한 첫 단계

가장 적은 작업으로 가장 큰 가치 — 사용자 보유 종목 daily monitor 추가:

```python
# tools/dip_monitor_simple.py (rule-based, no ML)
# 매일 실행, 보유 종목 dip event 감지

For each ticker in current portfolio:
  ohlcv = fetch_recent_60d(ticker)
  flow = fetch_recent_5d_flow(ticker)

  if today_close < yesterday_close * 0.95:
    # Dip event detected
    foreign_net = flow.foreign_net_5d
    inst_net = flow.inst_net_5d

    if foreign_net > 0 and inst_net > 0:
      verdict = "LIKELY_SHAKEOUT — hold"
    elif foreign_net < -threshold or inst_net < -threshold:
      verdict = "LIKELY_DISTRIBUTION — review for sell"
    else:
      verdict = "AMBIGUOUS — manual decision"

    slack_alert(ticker, dip_pct, foreign_net, inst_net, verdict)
```

이건 ML 없이도 **즉시 가치**. ML 분류기 학습 데이터 누적되는 동안 사용 가능.

---

## 10. 정리

| 단계 | 노력 | 가치 |
|---|---|---|
| Section 9 rule-based monitor | 1일 | ★★★ 즉시 작동 |
| F1 Episode mining | 1주 | ★★ 학습 데이터 |
| F2 Feature panel | 1주 | ★★ |
| F3 Classifier training | 3일 | ★★★ ML 분류기 |
| F4 Live monitor integration | 3일 | ★★★ 실 운영 |
| F5 Backtest validation | 1주 | ★★ 신뢰성 |

**총 ~3-4주 후 실 운영 가능 시스템**.

핵심 원칙:
- **외국인/기관 flow는 한국시장 최강 시그널**
- 단순 OHLCV만으로 미국식 patterns만 보면 한국시장 한계
- Rule-based + ML hybrid 가 가장 robust
- 비대칭 비용 (false positive >> false negative) → 임계값 보수적
- Macro override (VKOSPI > 35) 항상 동반

---

## 11. 사용자 결정 필요

```
Option A — Section 9 즉시 구현 (1일, 가장 빠름)
Option B — F1-F5 전체 구현 (3-4주, 본격 ML)
Option C — Phase 분할: A 먼저 → B 단계적
Option D — 대기 (다른 작업 우선)
```

추천: **Option C** — 즉시 사용 가능한 rule-based 먼저, 운영하면서 ML 학습 데이터 누적.
