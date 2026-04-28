---
phase: P_MB
date: 2026-04-28
author: Claude (planning + implementation)
verdict: design + V0 implementation SHIPPED (mock-data verified)
ship_gate_passed: pending live data
---

# P_MB v0 — Multibagger Episode Discovery + Retrospective Mining

KOSPI/KOSDAQ에서 24개월 윈도우 +300%+ 종목 episode들을 retrospective하게 발견하고, 그 직전 시그널 패턴을 학습해서 **미래 multibagger 후보를 사전 식별**하는 시스템.

r1000 phase11 (`tmp_r1000_quant_engine/research/phase11_*`) 패턴 ported.

## TL;DR

- **V0 코드 완성** — `kr_multibagger.py` (470 lines) + `tests/test_multibagger.py` (310 lines, **17/17 mock 통과**)
- **다음 단계**: 사용자가 `pip install pykrx` 후 `tools/multibagger_explorer.py` 실행 → 실제 한국시장 episode 인벤토리 발견
- **Stage 2** (P_MB.2): pre-surge 시그널 추출 + binary classifier (CatBoost)
- **Stage 3** (P_MB.3): 월별 live picks → multibagger sleeve로 backtest

## Episode 정의

| Parameter | Value | 근거 |
|---|---|---|
| `return_threshold` | **3.0** (300%, 4x) | 사용자 결정 — KOSDAQ small noise 회피하면서 충분한 sample |
| `window_months` | **24** | 12m은 너무 빠른 surge (분류 어려움), 36m은 surge_start 모호 |
| `min_mcap_krw` | **5e11** (5,000억) | mid+ cap 한정. penny stock 제외 + 기관 매매 가능 사이즈 |
| `min_trading_value_krw` | 5e8 (5억/일) | 60d 평균 거래대금 |
| `min_listed_months` | 12 | lookback 보장 |
| `pre_surge_lookback_months` | 6 | r1000 phase11 동일 |
| `post_surge_include_months` | 3 | surge 초기 (still detectable) 포함 |
| `surge_breakout_pct` | 0.20 | surge_start = 첫 +20% 돌파 시점 |

## Algorithm

### 1. Episode 발견 (`find_episodes_for_ticker`)

```
For each candidate entry t0:
    forward_window = prices[t0+1 : t0+24]
    peak = argmax(forward_window)
    if peak / prices[t0] - 1 >= 3.0:
        candidate = (t0, peak_date, peak/start, months_to_peak)

Greedy collapse overlapping candidates: keep earliest entry.
months_to_peak = panel index distance (정수, exact monthly).
```

### 2. Surge start 식별 (`identify_surge_start`)

```
surge_start = first month after entry where price >= entry * 1.20
fallback: first month >= entry * 1.10 (없으면 None)
```

이 분리로 **accumulation period** (entry → surge_start) 와 **explosive period** (surge_start → peak) 가 구분됨. Pre-signal extraction은 accumulation period (또는 entry - 6m ~ surge_start +3m) 에서 수행.

### 3. Quality filter (`quality_filter_episodes`)

Entry date 기준 mcap 조회 → `mcap >= 5,000억` 이면 `quality_pass=True`.

추가 필터 (P_MB.2에서 추가): 거래대금, 상장 12개월+.

## Mock 검증 (17/17 통과)

| 검증 항목 | 결과 |
|---|---|
| 5x 24m 패턴 발견 | ✓ |
| 2x (threshold 미달) 거부 | ✓ |
| 30m surge → 24m 윈도우 안에서만 episode | ✓ |
| NaN 시작 (신규 상장) 처리 | ✓ |
| 빈/짧은 series 무시 | ✓ |
| 가격 0/음수 무시 | ✓ |
| Overlapping episodes 통합 | ✓ |
| Random walk 가짜 양성 < 5/20 | ✓ |
| Panel input 동작 | ✓ |
| `surge_start` boundary 식별 | ✓ |
| `add_surge_start_dates` 칼럼 추가 | ✓ |
| `episode_summary_stats` 분포 | ✓ |
| Empty input → empty output | ✓ |
| `kr_config` 상수 등록 | ✓ |

## Pre-surge signal 카탈로그 (P_MB.2 — 실제 데이터 후)

각 episode의 pre-surge window (`entry_date - 6m ~ surge_start_date + 3m`) 에서 다음 features collect:

### 기술적 (P3 기술지표 의존)
- `52w_high_distance_pct`, `volume_zscore_60d`, `stage2_uptrend`
- `ma_stack_aligned`, `breakout_strength`, `volatility_contraction`
- `rs_industry_12m`

### 재무 (DART PIT)
- `revenue_growth_yoy_4q`, `opi_sign_flip_recent`
- `revenue_acceleration` (Q-on-Q 가속)
- `roe_inflection`, `fcf_positive_first_time`
- `mcap_band` (5천억-1조 / 1조-5조 / 5조+)

### 공시 이벤트 (DART events)
- `treasury_buyback_announced_6m`
- `major_holders_new_5pct_external`
- `insider_registered_buy_3m`
- `bonus_issue_3m`
- `capital_increase_negative_6m` (없음 = 양호)
- `large_supply_contract_3m`

### 매크로 / 테마
- `regime_recovery_or_bull` (P3 regime)
- `theme_phase_emerging` (P2 themes.yaml)

## 사용자 실행 가이드

### V0 실측 (1회, ~30-60분)

```bash
cd H:/codex/kr_quant_engine
py -3 -m pip install pykrx              # 5분
py -3 -c "from kr_multibagger import load_or_build_episode_panel; load_or_build_episode_panel()"
                                          # 25-50분 (universe ~350종목 × 8년 가격 + mcap)
py -3 tools/multibagger_explorer.py      # 즉시 (캐시 hit)
```

**예상 결과**:
- ~50-150 episodes per year (KOSPI+KOSDAQ mid+ cap 5,000억+ 한정)
- 2016-2024 총 ~600-1,200 episodes
- Top 30 by max_return: 에코프로 (~300x), 에코프로비엠 (~20x), 알테오젠, HLB, 클래시스 등 등장 예상
- Median months_to_peak: ~14-18개월 (24m 윈도우 내)
- Median mcap_at_entry: ~7,000-10,000억 (mid-cap)

### V1 (P_MB.2 — pre-signal extraction + classifier)

P1 (DART 펀더멘털) + P2 (DART 이벤트) 시그널 panel 생성 후:

```bash
py -3 -c "from kr_multibagger import build_pre_surge_features_panel; build_pre_surge_features_panel(...)"
py -3 -c "from kr_multibagger_classifier import train_entry_classifier; train_entry_classifier(...)"
```

CatBoost binary classifier, walk-forward 5-fold validation. Output:
- AUC, precision@K
- Feature importance
- Top-K monthly live picks

## 알려진 한계 (P_MB.2 / P_MB.3에서 해결)

1. **TTM 단순화 영향**: revenue_growth_yoy 정밀 계산은 P3 정밀화 후 가능
2. **Insider holdings count 폭발**: P2.2 net buy KRW/mktcap 재설계 후 사용
3. **Theme classification 미구현**: themes.yaml 매핑 (P2)
4. **Sleeve integration**: P_MB.3에서 backtest 통합 → ΔCAGR 측정
5. **Survivorship bias**: 현재 universe는 latest mcap 기준 → 과거 상폐 종목 제외 (역방향: positive bias 가능). P3에서 corrected universe 사용.

## Ship Gate (P_MB.2 완료 후)

V0 episode discovery는 informational. V1 classifier가 먼저 측정될 ship gate:
- Classifier AUC ≥ 0.65 (random 0.5 baseline)
- Top-30 monthly picks의 24m forward CAGR ≥ +15pp vs equal-weighted universe
- ΔCAGR (multibagger sleeve 추가) ≥ +1pp vs P1 baseline

## Reference

- 원본: r1000 phase11 (`H:/codex/tmp_r1000_quant_engine/research/phase11_*`)
- `multibagger_episodes.csv` — r1000의 5x+ episode 78개 (FIX +859%, FANG +664%, NVDA +597% 등)
- `phase11_entry_classifier.py` — 분류기 reference
- `phase11_walkforward.py` — walk-forward 검증 reference
