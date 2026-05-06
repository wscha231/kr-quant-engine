---
phase: Phase G research (theme lifecycle / sector rotation)
date: 2026-05-06
author: Claude (auto)
trigger: User vision "주도섹터 발굴 → 상승초입 매수 → 최대한 오래 보유 → KOSPI200 약화 시 회전"
---

# Theme / Sector Lifecycle Strategy — Research Note

## 0. TL;DR

월별 multibagger picks (현재 시스템)는 **종목 단위** 신호. 사용자가 원하는 건 **테마 단위** 라이프싸이클 매니지먼트:

```
Theme strength rotation cycle:
  Stage 1 — Accumulation (축적)   : RS 횡보, 거래량 dry-up
  Stage 2 — Markup     (상승초입) : RS 양수 전환 + 거래량 폭발 + 신고가
  Stage 3 — Distribution (분배)  : RS peak, 거래량 감소
  Stage 4 — Markdown   (약화)    : RS 음수 전환 → 회전 신호

Hold horizon: 6 months ~ 2+ years per theme
Action: stage 1→2 진입, stage 3→4 탈출
```

한국시장 사례 (검증 필요, 수동 분류):
- 2020 Q1-Q2: COVID → 비대면/언택트 (NAVER, 카카오, 게임주)
- 2021 H1: 메타버스/NFT (위메이드, 컴투스 등)
- 2022 H1: 인플레/원자재 (현대제철, POSCO, 한일시멘트 등)
- 2023 H1-H2: AI / 2차전지 (삼성전자, 한미반도체, 에코프로, LG에너지솔루션)
- 2024: 방산 (한화에어로, LIG, 현대로템) + SMR + 반도체 부활
- 2025-2026 (현재): 조선 (HD현대중공업, 삼성중공업), 제약/바이오, AI 인프라

**핵심 통찰**: 테마가 명확히 식별되면 leader 종목 보유 1-2년 +200~+1000% 가능. 회전 missed (Iron sector 2021, COVID stock 2022) 시 20-40% 손실.

---

## 1. 데이터 소스

### 1.1 KRX 27개 업종지수 (가장 안정적)

KRX index codes (`fetch_index_ohlcv`):
- KOSPI: 1003=음식료품, 1004=섬유의복, 1005=종이목재, 1006=화학, 1007=의약품, 1008=비금속, 1009=철강금속, 1010=기계, 1011=전기전자, 1012=의료정밀, 1013=운수장비, 1014=유통업, 1015=전기가스, 1016=건설업, 1017=운수창고, 1018=통신업, 1019=금융업, 1020=은행, 1021=증권, 1022=보험, 1023=서비스업, 1024=제조업
- KOSDAQ: 2002~2026 (음식료, 섬유의류, 제지목재, 화학, 의약품, 비금속, 금속, 기계장비, 일반전기전자, 의료정밀기기, 운송장비부품, 유통, 숙박음식, 금융, IT부품, 통신방송, 컴퓨터서비스, 컴퓨터제조, 출판매체, 정보기기, 소프트웨어, IT종합, 운송, 금융기타)
- KOSPI 200 시리즈: 1028=KOSPI200, 1015=KOSPI200_금융, 등

총 27 KOSPI + 25 KOSDAQ ≈ 52개 업종지수.

장점:
- 안정적 시계열 (10+년)
- 종목별 업종 매핑 명확
- pykrx에서 fetch 가능

단점:
- 너무 광범위 (반도체 안에 한미반도체와 SK하이닉스 모두 포함, 라이프싸이클 다름)
- 신규 테마 (AI, SMR) 별도 추출 어려움

### 1.2 WICS (한국 GICS) 분류

FnGuide WICS sector / industry / sub-industry 3-level:
- 11 sectors → 24 industry groups → 71 industries → 158 sub-industries

장점: 글로벌 GICS 호환, 깊은 분류
단점: 라이센스 비용 (FnGuide), 무료로 못 받음

### 1.3 자체 메이저 테마 수동 분류 (실용)

핵심 20-30개 테마 수동 정의:
```yaml
themes:
  ai_semiconductor:
    name: AI/반도체
    leaders: ["005930", "000660", "042700", "058470", "108860"]   # 삼전, 하이닉스, 한미반도체, 리노공업, 에이비프로바이오
    period: ["2023-01", "2025-12"]   # active period
  battery:
    name: 2차전지
    leaders: ["086520", "247540", "373220", "066970"]   # 에코프로, 에코프로비엠, LGES, 엘앤에프
    period: ["2020-01", "2024-06"]
  defense:
    name: 방산
    leaders: ["012450", "064350", "047810", "079550"]   # 한화에어로, 현대로템, 한국항공우주, LIG넥스원
    period: ["2024-01", "2026-12"]
  shipbuilding:
    name: 조선
    leaders: ["329180", "010140", "010620", "298050"]   # HD현대중공업, 삼성중공업, 현대미포조선, 현대마린엔진
    period: ["2024-06", "2026-12"]
  smr_nuclear:
    name: SMR/원전
    leaders: ["011930", "048410", "267260", "036530", "104100"]   # 두산에너빌리티, 현대바이오, HD현대일렉트릭, 화천기공, 한전기술
    period: ["2024-09", "2025-12"]
  metaverse:
    name: 메타버스/NFT
    leaders: ["112040", "078340", "036570"]   # 위메이드, 컴투스, NCSOFT
    period: ["2021-01", "2022-06"]
  iron_steel:
    name: 철강
    leaders: ["005490", "005380", "004020"]   # POSCO홀딩스, 현대제철, 한일시멘트
    period: ["2022-01", "2022-12"]
  pharma_bio:
    name: 제약/바이오
    leaders: ["207940", "068270", "326030", "048410"]   # 삼성바이오, 셀트리온, SK바이오팜, 현대바이오
    period: ["2020-01", "2026-12"]   # always active
```

이 방식은 (1) 빠른 구현 (2) 실제 판단 가까움. 단점: 새 테마 자동 발견 어려움.

### 1.4 자체 클러스터링 (자동)

각 종목의 90/180/360일 returns 시리즈를 K-means / DBSCAN으로 클러스터링:
- 매년 새 클러스터 형성 (예: 2023 = AI 클러스터 emergence)
- 클러스터 ID + 평균 return = 테마 표시

장점: 자동, 새 테마 발견
단점: 라벨 없음, 해석 어려움

### 추천 hybrid

```
Layer 1: KRX 27 업종지수 (baseline, 항상 추적)
Layer 2: 수동 메이저 테마 ~25개 (current/recent leaders 명확히 식별)
Layer 3: (옵션) 클러스터링 (신규 테마 발견)
```

---

## 2. Theme strength signals

### 2.1 Per-theme metrics (daily)

```python
theme_strength = {
    "abs_return_5d":      sum(daily returns over 5d),
    "abs_return_20d":     ...
    "abs_return_60d":     ...
    "abs_return_120d":    ...
    "rs_kospi_5d":        return_5d - kospi_5d,
    "rs_kospi_20d":       ...
    "rs_kospi_60d":       ...
    "rs_kospi_252d":      (long horizon, 1y)
    "vol_z_60d":          거래량 z-score
    "new_high_count_pct": % of theme stocks at 52w high
    "vcp_count_pct":      % showing volatility contraction
    "ma_stack_aligned":   % above 200ma
    "breadth":            participation rate (% leaders rising)
}
```

### 2.2 Lifecycle stage classifier

```python
def classify_theme_stage(metrics) -> Literal["accumulate", "markup", "distribute", "markdown"]:
    if metrics["rs_kospi_60d"] < 0 and metrics["abs_return_120d"] < 0:
        return "markdown"
    if metrics["rs_kospi_60d"] < 0 and metrics["abs_return_60d"] < 0 and metrics["vol_z_60d"] > 0.5:
        return "distribute"
    if metrics["rs_kospi_20d"] > 0.05 and metrics["abs_return_20d"] > 0 and metrics["new_high_count_pct"] > 0.30:
        return "markup"
    if metrics["rs_kospi_60d"] > -0.02 and metrics["vol_z_60d"] < 0 and metrics["ma_stack_aligned"] > 0.50:
        return "accumulate"
    return "transition"
```

룰 기반. 후속 ML로 정밀화.

### 2.3 Entry / Exit triggers

**Entry** (theme rotation 매수 트리거):
- Stage transitions: accumulate → markup
- Confirmed by: rs_kospi_20d > 5pct AND vol_z_60d > 0.5 AND breadth > 50pct
- Add to portfolio: top 3-5 leader stocks of this theme

**Exit** (theme 회전 트리거):
- Stage: distribute → markdown
- Confirmed by: rs_kospi_60d < -5pct AND breadth < 30pct
- Sell all holdings of this theme

**Soft Exit**:
- 단계 distribute 진입 시 → 50pct reduce
- 단계 markdown 진입 시 → 100pct exit

**Hard Stop**:
- Theme RS rolling 20d Z-score < -2.0 → emergency exit regardless of stage

---

## 3. Stock selection within theme

테마 진입 결정 후, 그 테마 내에서 어떤 종목을 살지:

```python
def select_theme_leaders(theme_stocks, n=3) -> list[str]:
    # Score each stock:
    # 0.4 * RS_kospi_60d         (relative strength)
    # 0.3 * dist_from_52w_high   (breakout proximity)
    # 0.2 * vol_z_60d_growth     (volume expansion)
    # 0.1 * mcap_score           (size — prefer mid-large)
    # Select top N
```

또한 **multibagger classifier**와 결합:
- 테마 진입 시점에 P_pre_surge가 높은 종목 우선
- 두 시그널 조합 → high-conviction leader

---

## 4. Backtest design

### 4.1 Data layout

```
panel:
  date              YYYY-MM-DD daily
  theme             string
  metric_*          12개 metrics
  stage             accumulate / markup / distribute / markdown / transition
  is_active_today   bool
  members_alive     int (테마 종목 수)
```

### 4.2 Strategy simulator

```python
def theme_rotation_backtest(
    theme_panel,        # daily theme strength
    stock_panel,        # daily stock OHLCV
    seed_capital=1e8,
    max_themes=3,       # 동시 보유 테마 수
    stocks_per_theme=3, # 테마당 종목 수 (= 9 stocks total)
    rebalance_freq="W", # 주간 rebal
) -> dict:
    # 1. 매주 월요일:
    #    - 모든 테마의 현재 stage 평가
    #    - 보유 테마 중 stage = distribute/markdown 인 것 제거
    #    - 새 markup 진입 테마 추가 (max_themes - 보유 테마 수만큼)
    #    - 각 활성 테마에서 top stocks_per_theme leader 선택
    # 2. 거래:
    #    - 제거된 테마 종목 매도 (cost 31bp)
    #    - 새 테마 종목 매수 (cost 31bp)
    #    - 기존 보유 종목 비중 조정
    # 3. 매일:
    #    - 종목별 가격 갱신
    #    - 보유 종목이 KOSPI200 RS_60d < -10pct → emergency sell
    # 4. End-of-day:
    #    - portfolio value tracking
    #    - drawdown tracking
```

### 4.3 Metrics

- CAGR
- MDD
- Sharpe / Sortino
- Avg holding period per theme (months)
- Avg holding period per stock (days)
- Theme rotation count (changes)
- Hit rate per theme entry
- Best vs worst theme contribution

### 4.4 Trade log

매매 내역 자세히 기록:

```
trade_log columns:
  date, ticker, name, theme, action (BUY/SELL/REDUCE),
  qty, price, cost_krw, reason (theme_entry/theme_exit/leader_swap/stop_loss),
  prior_holding_days, realized_return_pct
```

**예상 출력**:
```
trade_log/2026-04_log.csv
  2024-08-12 매수  HD현대중공업 조선 ENTRY (theme markup confirmed)
  2024-08-12 매수  현대미포조선 조선 ENTRY
  2025-03-04 매도  현대미포조선 조선 SWAP_LEADER (RS 약화) 보유 204일 +47%
  2025-03-04 매수  HD한국조선해양 조선 SWAP_LEADER (신규 leader)
  2026-01-20 매도  HD현대중공업 조선 EXIT_THEME (theme distribute) 보유 526일 +213%
  2026-01-20 매도  HD한국조선해양 조선 EXIT_THEME 보유 322일 +127%
  2026-01-20 매수  ... 새 테마 leader
```

---

## 5. 한국시장 주도 테마 historical truth (수동 검증)

검증 가능한 leader stock + 시기:

| 시기 | 테마 | Leader stocks | Peak return |
|---|---|---|---|
| 2020-Q2~Q4 | 비대면/언택트 | NAVER, 카카오, 게임 | +200% |
| 2020-Q4~2021-Q3 | 2차전지 (1차파동) | LG화학, 삼성SDI, 에코프로비엠 | +500% |
| 2021-H1 | 메타버스 | 위메이드, 컴투스, NCSOFT | +800% |
| 2021-Q4~2022-H1 | 친환경/원자재 | POSCO, 현대제철, 한일 | +60% |
| 2022-Q3~2023-Q1 | 약세장 (no leader) | 현금 | - |
| 2023-Q1~Q3 | AI/반도체 | 삼성전자, 하이닉스, 한미반도체 | +120% |
| 2023-Q3~2024-Q1 | 2차전지 (2차파동, 광기) | 에코프로, 에코프로비엠, 포스코퓨처엠 | +1000% (그 후 -60%) |
| 2024-H1 | 방산 | 한화에어로스페이스, 현대로템 | +200% |
| 2024-H2~2025 | 조선 | HD현대중공업, 삼성중공업, HD한국조선해양 | +250% |
| 2024-Q4~2025 | SMR | 두산에너빌리티, HD현대일렉트릭 | +400% |
| 2025-2026 | 제약/바이오 | 삼성바이오로직스, 셀트리온, 알테오젠 | +150% |

이 목록을 코드로 변환 (`themes_history.yaml`) → 백테스트 ground-truth로 사용.

**중요 검증 항목**:
- 우리 strategy 가 2023 H2 에코프로 광기에서 entry → markup → 2024 Q1 distribute 정확히 잡았는가?
- 2022 H1 약세장에서 cash 비중 늘렸는가?
- 2024 방산 진입 시점 (KRX 코스피 방산지수 RS 양전환)
- 2025 조선 진입 시점

---

## 6. Implementation roadmap

### G1 — Theme universe + strength panel (1주)

```
kr_themes.py
  load_themes_yaml()                   # 수동 메이저 테마 + KRX 27 업종 fetch
  build_theme_member_panel()           # ticker -> theme(s)
  build_theme_strength_panel()         # daily metrics × theme
  classify_theme_stage()               # rule-based stage classifier

themes.yaml (수동, 25개 테마)
themes_history.yaml (검증용 ground truth)
```

### G2 — Historical theme analysis (3일)

```
tools/mine_theme_history.py
  Input: kr_themes 패널 + KOSPI200 OHLCV
  Output:
    data_pit/theme_strength_panel_<period>.parquet
    research/10_theme_lifecycle/leader_themes_per_quarter.csv
```

### G3 — Theme rotation backtester (1주)

```
kr_theme_rotation_backtester.py
  run_theme_rotation_backtest(...) -> {metrics, monthly_log, trade_log}

tools/run_theme_rotation_backtest.py    (CLI)
```

매매 내역 자세히 기록 (entry/exit reason, holding days, realized return).

### G4 — Live theme dashboard (3일)

```
tools/theme_dashboard.py
  - 매주 월요일 실행
  - 모든 테마의 현재 stage + RS 표시
  - 추천 매수/매도 list
  - Slack alert: stage transition 발생 시
```

GitHub Actions: `.github/workflows/weekly_theme_dashboard.yml`

### G5 — Integration with monthly multibagger picks (3일)

```
- 월별 multibagger picks 생성 후
- 활성 테마 leaders 와 cross-reference
- "테마 leader + multibagger 후보" 더블 시그널 → highest conviction
- 이런 종목에 더 큰 비중 (e.g., score-power 2.0+)
```

총 ~3-4주.

---

## 7. Backtest expected results

가설 (실제 검증 필요):

```
Naive HOLD KOSPI200 (5y CAGR 7%, MDD -25%)
Multibagger monthly (현재 시스템): CAGR 33% / MDD -25%
Theme rotation 만:                CAGR 25-40% / MDD -15% (분산 효과)
Theme rotation + Multibagger 결합: CAGR 40-50% / MDD -15-20%
```

핵심 가치:
- 종목 picks이 전부가 아님. **언제 어느 테마로 회전하느냐**가 결정적.
- Multibagger detect 가 잡는 것 = "강한 종목". Theme rotation 이 잡는 것 = "강한 시장".
- 두 신호가 일치 (강한 시장 안의 강한 종목) → 최강 conviction.

---

## 8. 즉시 가치 (1일 deliverable)

KRX 27 업종지수 daily fetch + 단순 ranking → 매주 월요일 leader theme 출력:

```
tools/theme_strength_today.py
  - 모든 KRX 27 업종 RS_60d 계산
  - KOSPI200 대비 상위 5 / 하위 5 출력
  - Slack alert
```

이걸 먼저 띄우면 사용자가 즉시 시장 흐름 파악 가능. 백테스트는 나중.

---

## 9. 다음 단계 결정

```
Option A — 즉시 가치 먼저 (Section 8)
  tools/theme_strength_today.py 1일 작성 → 매주 leader 표시 → 후속 본격 구현

Option B — 본격 G1+G2+G3 한꺼번에 (3주)
  완전한 theme rotation backtest 시스템

Option C — Hybrid (추천)
  Section 8 즉시 + G1 module 1주 + G3 backtest 1주 → ~2.5주 후 backtest 결과
```

추천: **Option C** — 빠른 가치 + 본격 구현 병행.
