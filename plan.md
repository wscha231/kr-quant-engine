# Implementation Plan — Phase C (사용자 승인 후 진행)

## TL;DR

4 milestones × ~1주 each = **3-4주 작업**. 마무리 후 GitHub deployment (~3일).

목표: CAGR 40% / MDD -20% (realistic forward, after PIT/governance/purged 보정).

---

## Phase A 산출물 (완료, read-only)

```
research/00_data_sources_audit/pit_survivorship_audit.md ✅
research/02_factor_zoo/institutional_methods_import_map.md ✅
research/03_korea_specific_signals/governance_risk_overlay_research.md ✅
research/06_walkforward_baselines/purged_oos_protocol.md ✅
research/08_deployment/github_actions_deployment_plan.md ✅
```

---

## Phase C — Implementation (after user approval)

### C1. PIT Survivorship Fix (1주, ★★★ 우선)

**수정 파일**:
- `kr_pykrx_client.py`: `fetch_listing_at_date()` (PIT-aware)
- `kr_universe.py`: `build_universe_snapshot` historical mcap + listed_months
- `kr_dart_client.py`: corp_code history (변경 추적)
- `tools/build_pit_universe_history.py` (NEW)
- `kr_macro.py`: BOK publication lag 매핑

**새 파일**:
- `data/pit_universe/listed_history.parquet` (corp_code register dates)
- `data/pit_universe/delisted_history.parquet`
- `data/pit_universe/historical_mcap.parquet` (월별 snapshot 누적)

**Tests**:
- `test_fdr_current_listing_not_used_for_historical_backtest`
- `test_historical_mcap_required_before_rebalance_date`
- `test_listed_months_not_stub`
- `test_delisted_ticker_can_exist_in_past_universe`
- `test_macro_publication_lag_applied`

**Ship gate**:
- 모든 backtest universe가 `rd` 시점 listed + `rd` 시점 mcap 사용
- 상폐 종목 universe에 포함 가능 (당시 living 였다면)
- Tests all pass

### C2. Governance Risk Overlay (1주, ★★★ 한국 특화)

**새 파일**:
- `kr_governance.py` (~400 lines)
- `governance_entities.yaml` (수동 registry 15-20 그룹)
- `tools/governance_event_study.py` (검증)

**수정 파일**:
- `kr_config.py`: `PHASE2_GOVERNANCE_COLUMNS` (11 cols) + `PHASE2_KOREA_ALPHA_COLUMNS` 확장
- `kr_features.py`: `add_governance_signals()` 통합
- `kr_backtester_realistic.py`:
  - Picks ranking 단계: governance penalty
  - Position sizing 단계: governance weight cap
  - hard veto 처리

**Tests**:
- `test_governance_hard_veto_removes_new_buy`
- `test_governance_weight_cap_limits_position`
- `test_dilution_dynamic_calculation`
- `test_treasury_sale_vs_cancellation_distinction`
- `test_spinoff_meibei_vs_inseok_handling`

**Ship gate**:
- CAGR 하락 ≤ 2pp (vs no governance)
- MDD 개선 ≥ 2pp
- Worst 5% monthly return 개선
- 대형 희석/처분/물적분할 사례 회피 검증 (event study)
- 회전율 +5pp 이내

### C3. Purged Walk-forward + Sleeve 분리 (1주)

**수정 파일**:
- `kr_multibagger_classifier.py`:
  - `walk_forward_splits_purged()` (embargo strict)
  - `label_pre_entry()` (-6m ~ -1m)
  - `label_continuation()` (0m ~ +3m)
  - `label_risk()` (+1m drawdown >= -20%)
  - 3 separate train_*_classifier functions
- `kr_backtester_realistic.py`: sleeve 분리 (40% pre-entry / 40% continuation / 20% defensive)

**새 파일**:
- `kr_models/pre_entry_classifier.py`
- `kr_models/continuation_classifier.py`
- `kr_models/risk_classifier.py`
- `kr_models/ensemble.py` (CatBoost + LightGBM ranker + Logistic)

**Tests**:
- `test_walk_forward_embargo_actually_excludes_overlap`
- `test_pre_entry_label_excludes_post_surge_period`
- `test_continuation_label_separate_from_pre_entry`
- `test_risk_label_uses_only_past_dd`
- `test_calibration_curve_within_tolerance`
- `test_adversarial_validation_auc_low`
- `test_fold_consistency_70pct`

**Ship gate**:
- Reported AUC honest (likely 0.78-0.82 range, lower than current 0.80 inflated)
- Calibration error < 5%
- Adversarial validation AUC < 0.7
- 5-year OOS CAGR 30%+ (after PIT correction)
- Sleeve 분리로 MDD 추가 -2~3pp

### C4. VKOSPI Source Fix + Risk Integration (3일)

**수정 파일**:
- `kr_derivatives.py`: VKOSPI KRX 1003 직접 fetch fix
- `kr_backtester_realistic.py`: `vkospi_level` from real source (not hardcoded)
- `kr_backtester_realistic.compute_sleeve_scale()`: VKOSPI guard active

**Tests**:
- `test_vkospi_guard_not_constant_when_enabled`
- `test_vkospi_panic_threshold_triggers_cash`

**Ship gate**:
- 2022 KOSPI bear (VKOSPI > 30 시기) MDD 개선 5pp+
- Sharpe stability across regimes

---

## Phase D — GitHub Deployment (~3일, after C 완료)

```
.github/workflows/smoke_test.yml          (push hook)
.github/workflows/monthly_picks.yml       (1st 04:00 KST cron)
.github/workflows/quarterly_backtest.yml  (분기 1일)
.github/workflows/monthly_retrain.yml     (1st 02:00)

tools/generate_monthly_picks.py           (CLI entry)
tools/run_quarterly_backtest.py
tools/run_classifier_retrain.py

streamlit_app.py                          (dashboard)
secrets.toml.example
```

GitHub Secrets 설정:
- DART_API_KEY
- BOK_ECOS_API_KEY
- RCLONE_CONFIG_GDRIVE
- SLACK_WEBHOOK_URL

---

## Phase E — Paper trading (1-2개월, after Phase D)

```
kr_paper_executor.py                      (KIS API 모의 매매)
kr_portfolio_tracker.py                   (holdings DB)
kr_trade_log.py                           (체결가 vs 가정 비교)
```

Manual approval workflow.

---

## A/B Experiments

### Data Truth (D1-D3)

```
B0: current best (N=20 equal + DD)
D1: B0 + survivorship-corrected universe
D2: D1 + historical mcap only
D3: D2 + purged walk-forward
```

기대: Reported CAGR 38.97% → D3 honest CAGR 30-34%.

### Alpha (A1-A6)

```
A1: D3 + pre-entry model only
A2: D3 + pre-entry + continuation
A3: A2 + technical gate (VCP + 52w high)
A4: A3 + flow confirmation
A5: A4 + governance overlay
A6: A5 + regime/VKOSPI guard
```

각 단계 ship gate (CAGR +0.5pp AND MDD ≤ +2pp).

### Governance (G0-G4)

```
G0: A6 (no governance)
G1: A6 + soft penalty (35%)
G2: A6 + hard veto only
G3: A6 + weight cap
G4: A6 + event-driven sell alert
```

Best (likely G3 weight cap or G3+G4 combined).

---

## Final Ship Gate

```
✅ 10년 (또는 가능한 최대) PIT OOS:
  CAGR ≥ 35% realistic (after corrections)
  목표 CAGR 40% 근접 또는 초과
  MDD ≤ -20% 목표 (최소 -22% 이내)
  Sharpe ≥ 1.2
  Fold 70%+ baseline 대비 개선
  상폐/현재상장 누수 없음
  Governance overlay tail loss 개선

✅ Governance overlay 별도:
  CAGR 하락 ≤ 2pp
  MDD 개선 ≥ 2pp
  worst 5% 월간 손실 개선
  대형 희석/처분/물적분할 회피 검증
```

---

## Rollback Plan

각 phase 완료 후:
- Tag git: `pre-c1`, `post-c1`, `post-c2` 등
- 만약 ship gate 미통과: revert to previous tag
- Branch 작업 (main에 직접 push X), PR 후 merge

---

## 사용자 결정 필요

### Option A — Phase C1부터 즉시 진행 (auto)
- PIT survivorship fix 1주 자동 진행
- 매주 progress report

### Option B — research note review 먼저
- 4 research notes (이미 작성됨) 사용자 검토
- 우선순위 확인 후 시작

### Option C — Phase 부분 선택
- C1 (PIT) only — 먼저 정직한 baseline
- C2 (Governance) only — 한국시장 specific
- C3 (Purged WF) only — ML rigor
- 등 부분 선택 가능

### Option D — 즉시 GitHub deployment 먼저
- 현재 시스템 (Phase C 미적용) 상태로 Github Actions cron 가동
- 매월 picks 자동 생성 시작 (단 in-sample biased)
- Phase C 후 정확한 picks으로 교체

---

## 추천: Option A (auto Phase C)

```
Week 1: Phase C1 PIT fix
Week 2: Phase C2 Governance overlay
Week 3: Phase C3 Purged WF + sleeve 분리
Week 3-4: Phase C4 VKOSPI fix
Week 4: A/B experiments + ship gate validation
Week 5: Phase D GitHub deployment
Week 5-7: Phase E Paper trading
```

총 7주 후 진정한 자동 운영 가능 시스템.

---

## Phase A 산출물 미리 review 가능

```
research/00_data_sources_audit/pit_survivorship_audit.md       # 5개 leakage 식별
research/02_factor_zoo/institutional_methods_import_map.md     # 6 패턴 import 맵
research/03_korea_specific_signals/governance_risk_overlay_research.md  # 한국 특화
research/06_walkforward_baselines/purged_oos_protocol.md       # CV 방법
research/08_deployment/github_actions_deployment_plan.md       # 자동 운영
```

---

**사용자 결정 대기 중**. Auto 모드라도 큰 변경이라 명시적 승인 받음 후 Phase C 진행.
