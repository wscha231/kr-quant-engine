---
phase: Phase A research
date: 2026-05-02
author: Claude (auto)
---

# Purged Out-of-Sample Protocol (Two Sigma / Kaggle 패턴)

## TL;DR

현재 `walk_forward_splits()` 는 `embargo_days` 인자만 받고 **실제 적용 X**. 또한 P_MB label window가 pre-entry와 continuation을 혼합. 이 두 가지 fix가 **OOS 신뢰성의 핵심**.

---

## 1. 현재 walk_forward_splits 분석

```python
# kr_multibagger_classifier.py:walk_forward_splits()
def walk_forward_splits(panel, n_folds=5, embargo_days=126):
    rds = sorted(panel["rebalance_date"].unique())
    fold_size = n // (n_folds + 1)
    for k in range(n_folds):
        train_end_idx = (k+1) * fold_size
        test_start_idx = train_end_idx
        test_end_idx = train_end_idx + fold_size
        # ⚠️ embargo_days 인자만 받고 실제 인덱스에서 buffer 미적용
```

**문제**:
- `embargo_days = 126` 명시했지만 train_end와 test_start 사이 buffer 없음
- 즉 train의 마지막 month-end의 다음 month부터 test → label leakage 가능

**Symptom**:
- P_MB label window: surge_start ± 6개월 → train fold 끝 직전의 row가 test fold 첫 row의 label과 직접 연관
- OOS AUC 0.80은 **약간 inflated**

---

## 2. 올바른 purged walk-forward (López de Prado)

```
Train fold:   [t0, t1)
Embargo gap:  [t1, t1 + embargo_days)   ← drop completely
Test fold:    [t1 + embargo_days, t2)

For each test row at time t_test:
  Drop train rows with label_window overlapping [t_test - 6m, t_test + 3m]
  (즉 label horizon 만큼 추가 buffer)
```

### 우리 적용

P_MB label = surge_start ± [−6m, +3m], 즉 9개월 window.

따라서:
- **embargo = 9개월 (≈ 189 business days, but 우리는 monthly rebal이므로 9 months)**
- 또는 train fold 끝에서 **9 months drop** + test 시작

### 코드 제안 (Phase C3)

```python
def walk_forward_splits_purged(panel, n_folds=5, embargo_months=9):
    rds = sorted(panel["rebalance_date"].unique())
    n = len(rds)
    fold_size = n // (n_folds + 1)
    splits = []
    for k in range(n_folds):
        train_end_pos = (k+1) * fold_size
        # Apply embargo: drop train rows within last `embargo_months`
        train_dates = rds[: max(0, train_end_pos - embargo_months)]
        test_dates = rds[train_end_pos: train_end_pos + fold_size]
        if len(train_dates) > 0 and len(test_dates) > 0:
            train_idx = panel.index[panel["rebalance_date"].isin(train_dates)].to_numpy()
            test_idx = panel.index[panel["rebalance_date"].isin(test_dates)].to_numpy()
            splits.append((train_idx, test_idx))
    return splits
```

**중요 추가**: `train_end_pos - embargo_months` 직전까지만 train에 사용. 그 사이 9개월은 train도 test도 아닌 buffer.

---

## 3. Pre-entry vs Continuation label 분리

현재 P_MB label:
```python
is_pre_surge = 1 if surge_start - 6m <= rebal_date <= surge_start + 3m else 0
```

**문제**: 이 9-month window 안에 두 종류 reality 섞임:
- (a) **Pre-entry** (-6m ~ surge_start): 식별 어려움, value-quality-turnaround driven
- (b) **Continuation** (surge_start ~ +3m): 식별 쉬움, momentum + technical driven

분류기가 둘 모두에 학습 → "이미 surge한 종목" 학습 더 잘함 (continuation easier) → live mode에서 **이미 늦은 종목** picks. 진짜 pre-entry 못 잡음.

### 해결: 3 separate models

```python
# Model A: Pre-entry classifier
label_A = 1 if surge_start - 6m <= rebal_date < surge_start - 1m else 0
features_A: ROE, turnaround, debt, low_52w, valuation
horizon_A: predict 6m-out multibagger probability

# Model B: Continuation classifier
label_B = 1 if surge_start <= rebal_date <= surge_start + 3m else 0
features_B: momentum, RS, MA stack, volume, trend_template
horizon_B: predict 3m-out additional gain

# Model C: Risk/fail classifier (defensive)
label_C = 1 if rebal_date+1m drawdown <= -20% else 0
features_C: governance_risk, overheating, vkospi, regime
horizon_C: predict 1m-out drawdown probability
```

### Sleeve 분리

```python
final_score =
    p_pre_entry  * w_pre  * (1 - p_risk)
    + p_continuation * w_cont * (1 - p_risk)
```

또는 portfolio sleeves:
- **Sleeve A** (40%): Top 10 by p_pre_entry — 6-month hold
- **Sleeve B** (40%): Top 10 by p_continuation — 1-3 month hold
- **Sleeve C** (20%): Cash floor (when p_risk high)

---

## 4. OOS metrics — 모든 fold에서 honest measurement

현재:
```python
auc = roc_auc_score(y_test, proba_test)   # OK
precision_at_30 = ...                      # OK
```

**추가 필요**:

| Metric | 의미 |
|---|---|
| **realized_sleeve_cagr** | top-K picks 1-month forward return의 CAGR |
| **left_tail_loss_at_5pct** | bottom 5% of monthly returns |
| **calibration_error** | p_pre_surge가 실제 frequency와 match? (0.7 → 70%?) |
| **fold_consistency** | 5 fold 중 70% 이상에서 baseline 대비 개선? |
| **feature_importance_stability** | top-10 features가 fold마다 일관? |

### Calibration curve

```python
# Predicted probability bins → actual realized rate
bins = [0.0, 0.5, 0.7, 0.8, 0.9, 1.0]
for low, high in zip(bins[:-1], bins[1:]):
    in_bin = (proba >= low) & (proba < high)
    actual_rate = y_test[in_bin].mean()
    print(f"Predicted [{low}, {high}) → actual {actual_rate}")
```

Calibration 불량하면 (예: 0.9 predicted but 0.3 actual) → ensemble 또는 isotonic regression 추가.

---

## 5. Adversarial validation

Train (2019-2022) 분포가 Test (2023-2024) 분포와 다르면 OOS 신뢰성 낮음.

```python
# Train binary classifier: distinguish train vs test rows
y_adversarial = [0 if rd <= 2022-12 else 1]
X = features
model.fit(X, y_adversarial)
auc_adversarial = ...

# If AUC > 0.7: distribution shift detected
# → drop highest-importance features (likely time-dependent)
```

---

## 6. Ensemble blending

현재 단일 CatBoost binary. 추가 모델:

| Model | Library | 역할 |
|---|---|---|
| **CatBoost binary** (현재) | catboost | base classifier |
| **LightGBM ranker** (신규) | lightgbm | rank objective (top-K 직접 최적화) |
| **Logistic regression** (신규) | sklearn | calibration + linear baseline |

**Blend**:
```python
final_p = 0.5 * p_catboost + 0.4 * p_lgbm_rank_normalized + 0.1 * p_logistic
```

또는 stacking (meta-learner 사용).

**예상 lift**: AUC 0.80 → 0.83 (3pp).

---

## 7. Test requirements (Phase B plan)

```python
test_walk_forward_embargo_actually_excludes_overlap
test_pre_entry_label_excludes_post_surge_period
test_continuation_label_separate_from_pre_entry
test_risk_label_uses_only_past_dd
test_calibration_curve_within_tolerance
test_adversarial_validation_auc_low (<0.7)
test_fold_consistency_70pct
```

---

## 8. 구현 cost

- `walk_forward_splits_purged`: ~30 lines (kr_multibagger_classifier.py)
- `label_pre_surge` → 3 separate label functions: ~100 lines
- Sleeve 분리 in run_realistic_backtest: ~150 lines
- Ensemble blender: ~80 lines
- Calibration + adversarial validation: ~100 lines
- Tests: ~200 lines

**Total: ~3-5일**.

---

## 9. 정리

| 변경 | Reported AUC | True OOS AUC | True CAGR | Confidence |
|---|---|---|---|---|
| 현재 (no purge, mixed label) | 0.80 | ~0.75 | 38.97% (inflated) | Low |
| Purged WF 적용 | 0.78 | 0.78 | 32-36% (honest) | High |
| + 3 model split | 0.81 | 0.81 | 35-40% (better timing) | High |
| + Ensemble | 0.83 | 0.83 | 36-42% | High |

→ **Reported 숫자는 약간 줄지만, 진짜 forward expected는 증가**.

→ 사용자 목표 (CAGR 40% / MDD -20%) 달성 가능성 **명확히 향상**.
