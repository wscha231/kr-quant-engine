---
phase: Phase A research
date: 2026-05-02
author: Claude (auto)
---

# Institutional Methods Import Map

운용사 / Kaggle 대회 상위권의 일반적 패턴 중 **현재 kr-quant-engine에 도입 가치 높은 것**을 매핑.

## 1. AQR / Factor investing (★★★ 도입 강도 높음)

### 패턴
- Value + Momentum + Quality + Low Risk 결합
- Sector / industry neutralization
- Factor crowding monitoring
- Turnover penalty

### 우리 적용
| 기존 | 추가 |
|---|---|
| P_MB classifier 단일 score | + Quality overlay (turnaround × ROE × capital_allocation_quality) |
| momentum z-score | + sector-neutral percentile (within 11 GICS sectors) |
| simple cost 31bp | + turnover penalty in objective (rank × (1 - 0.5×turnover)) |

### 예상 효과
- CAGR ±0pp (분산 효과)
- Sharpe +0.05~0.15
- MDD -1~3pp

## 2. Two Sigma / Kaggle competition (★★★ 매우 높음)

### 패턴
- **Purged walk-forward CV** (embargo strict 적용)
- Out-of-time validation
- Target leakage audit
- Feature importance stability across folds
- **Ensemble blending** (CatBoost + LightGBM + Linear)
- Calibration curve

### 우리 적용
| 기존 | 추가 |
|---|---|
| `walk_forward_splits` embargo 인자만 (no actual purge) | **Strict purge: drop train rows within `embargo_days` of test fold** |
| CatBoost binary 단일 | **Ensemble: CatBoost binary + LightGBM ranker + Logistic calibration** |
| AUC만 metric | + precision@K + realized sleeve CAGR + left-tail loss |
| 전체 train + predict | **Fold-by-fold prediction** (이미 generate_oos_picks에 일부 적용) |

### 예상 효과
- Reported CAGR -3~5pp (more honest OOS)
- True forward CAGR ±0pp (동일)
- Confidence in metrics 크게 ↑

## 3. Renaissance / stat arb (★ 부분 도입)

### 패턴
- 짧은 horizon micro alpha
- 수많은 weak alpha의 ensemble
- 비용/체결 최적화

### 우리 적용
- Full stat arb 비현실적 (월별 rebal 운영 vs 인트라데이)
- **단, daily DART event monitor + price-limit hit alert** 추가
- 가격제한폭 hit / 거래정지 / 단기과열 종목 실시간 감지

### 예상 효과
- Tail risk 감지 (대형 손실 회피)
- CAGR ±0pp
- MDD -1~2pp

## 4. Citadel / Millennium pod (★★ 중간 도입)

### 패턴
- Position-level risk limit
- Sleeve별 capital allocation
- Daily P&L attribution

### 우리 적용
| 기존 | 추가 |
|---|---|
| 단일 portfolio (Top 30) | **Sleeve 분리: P_MB pre-entry sleeve + Continuation sleeve + Defensive sleeve** |
| equal weight | + sleeve별 risk-parity allocation |
| no position limit | + max position weight 6%, sector weight 25% |
| no daily attribution | + daily P&L attribution (signal contribution) |

### 예상 효과
- MDD -2~5pp (sleeve diversification)
- Sharpe +0.10~0.20
- CAGR ±1pp

## 5. O'Neil / Minervini / CANSLIM (★★★ 매우 높음, 한국시장 적합)

### 패턴
- 신고가 근접 + 강한 RS
- 매출/이익 성장 가속
- Volume breakout
- Volatility contraction (VCP)
- MA stack alignment
- 주도업종 / 주도테마

### 우리 적용
이미 P3.1 technicals에 모든 요소 있음 (`new_52w_high_flag`, `volume_zscore_50`, `vol_contraction`, `trend_template_score`, `breakout_flag`, `ma_stack_aligned`).

**현재 missing**: Technical signal을 **alpha**로 쓰는데 **gate**로도 써야:
- Pre-entry model: VCP + 52w high proximity (필수 통과)
- Continuation model: trend_template + MA stack
- Exit model: MA50 break + volume spike + trend_template fail

### 예상 효과
- CAGR +2~4pp (entry timing 정밀화)
- MDD -2~3pp (exit timing 정밀화)
- Sharpe +0.10~0.15

## 6. Kaggle 대회 상위권 패턴 (★★ 도입 가치)

### 패턴
- Ensemble (단일 모델 X)
- Rank objective / pairwise ranking
- Strict OOF (out-of-fold) prediction
- Target leakage 제거
- Adversarial validation (train vs test 분포 차이)
- Public score 과적합 방지

### 우리 적용
- **CatBoost binary → ensemble (CatBoost + LightGBM ranker)**
- **rank objective**: top-30 picks의 ranking 최적화 (단순 binary X)
- Adversarial validation: 2020 train vs 2024 test 분포 비교
- Calibration curve: p_pre_surge가 실제 frequency와 match하는지

### 예상 효과
- AUC 0.80 → 0.83 (ensemble lift)
- CAGR +2~3pp (rank objective)
- Stability ↑

---

## 우선순위 도입 순서 (Phase C plan)

| # | 패턴 | 작업 | 기대 효과 | 우선 |
|---|---|---|---|---|
| 1 | **Purged walk-forward** (Two Sigma) | `walk_forward_splits` strict embargo | Reported -3pp, True CAGR honest | ★★★ |
| 2 | **Sleeve 분리** (Citadel) | pre-entry / continuation / risk model | MDD -2-5pp, Sharpe +0.15 | ★★★ |
| 3 | **Technical gating** (Minervini) | VCP + 52w high gate on entries | CAGR +2-4pp | ★★★ |
| 4 | **Ensemble** (Kaggle) | CB + LGBM ranker + calibration | AUC +0.03, CAGR +2-3pp | ★★ |
| 5 | **Sector neutralization** (AQR) | Within-sector rank | Sharpe +0.05-0.15 | ★★ |
| 6 | **Daily event monitor** (Renaissance) | DART event + price limit alert | MDD -1-2pp | ★ |

---

## Combined expected (모든 적용 후)

```
Current best:    CAGR 38.97% / MDD -24% / Sharpe 1.35
After Phase C:   CAGR 35-40% / MDD -18~22% / Sharpe 1.40-1.55

Realistic forward (after PIT correction):
                 CAGR 30-35% / MDD -18~22% / Sharpe 1.30-1.45
```

→ **CAGR은 유지/약간 줄지만, MDD + Sharpe + reliability 모두 향상**.
→ 이게 사용자 목표 (CAGR 40% / MDD -20%)에 더 근접한 방향.
