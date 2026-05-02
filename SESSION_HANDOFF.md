# Session Handoff — Single Inbox

> 다음 세션에서 가장 먼저 읽을 파일.

---

## ⚠️ NEXT SESSION 시작 순서 (필수)

```
1. 이 파일 (SESSION_HANDOFF.md) 읽기
2. plan.md 읽기 (Phase C 계획 + 사용자 결정 옵션)
3. research/ 5개 노트 read 가능 (선택, 필요시만):
   - research/00_data_sources_audit/pit_survivorship_audit.md
   - research/02_factor_zoo/institutional_methods_import_map.md
   - research/03_korea_specific_signals/governance_risk_overlay_research.md
   - research/06_walkforward_baselines/purged_oos_protocol.md
   - research/08_deployment/github_actions_deployment_plan.md
4. 사용자 질문: "Phase C 어느 옵션으로 진행?" (A/B/C/D)
```

**현재 Status (2026-04-30 마감)**: Phase A 완료, Phase B plan.md 작성 완료, **Phase C 사용자 승인 대기**.

---

## 마지막 큰 성과 (2026-04-30 21:30 KST)

### 🏆 1억 5년 OOS Backtest CAGR 38.97% ★★★★

```
Best config: N=20 equal weight + DD breaker
Seed:    100,000,000 KRW
Final:   518,257,415 KRW (5.18배)
CAGR:    +38.97%
MDD:     -24.66%
Sharpe:   1.350
Years:    5.00 (60 months OOS)
Trades:   1,777
```

**r1000 (US) reference보다 모든 지표 우수**:
- CAGR: 33.40% → 38.97% (+5.6pp)
- MDD: -25.29% → -24.66% (better)
- Sharpe: 1.28 → 1.35 (+0.07)

### 검증 chain (entire session)

```
1. Universe 1,278 eligible (KOSPI+KOSDAQ filtered)
2. Multibagger episodes 398 detected (205 quality-pass)
   ├─ 사용자 예상 모두 검증: 에코프로 +1084%, HLB +613%, 알테오젠 +627%
3. P_MB classifier AUC 0.80 (5-fold walk-forward)
4. Pre-identified 효성중공업 +1219% (50개월 사전 detect)
5. Sleeve sandbox: 71m +62pp / 12m OOS +38pp
6. Realistic 1억 backtest:
   - V2 N30 capped:    CAGR 33.55%, MDD -24%, Sharpe 1.25
   - BEST N20 equal:   CAGR 38.97%, MDD -25%, Sharpe 1.35 ★
```

### 마지막 commits (GitHub: wscha231/kr-quant-engine)

```
4194810 Realistic 1eok backtester + OOS picks: CAGR 33.55%
47b4a0b P_MB.3 sleeve backtest +38.34pp
033bc7b KOSPI 500+KOSDAQ 100 P0 baseline +7.42pp
50e03e1 SESSION_HANDOFF v3
67570f1 P_MB.2 V1: AUC 0.80
73a6200 P2.5 Naver flow scraper + multibagger episodes
52c3716 FDR fallback for pykrx
c64f29b 4-layer integration (macro/flow/derivatives/regime)
aa8dc43 P2 events v2 + P3 technicals
7e0d304 Initial commit
```

## Phase A 산출물 (2026-04-30 완료, read-only)

5개 research notes 작성 + plan.md 작성. 사용자 두 개선안(Governance Risk Overlay + Codex Agent Instruction) 분석 통합:

```
✅ research/00_data_sources_audit/pit_survivorship_audit.md
   → 3 critical leakage paths (FDR current listing / current mcap / listed_months stub)
   → Reported CAGR 38.97% 중 +5-9pp inflated 추정

✅ research/02_factor_zoo/institutional_methods_import_map.md
   → AQR / Two Sigma / Renaissance / Citadel / O'Neil-Minervini / Kaggle 6 패턴 매핑
   → Top 6 우선순위 도입 plan

✅ research/03_korea_specific_signals/governance_risk_overlay_research.md
   → 7 governance risk types
   → 11 PHASE2_GOVERNANCE_COLUMNS 설계
   → Weight cap 추천 (vs hard veto / soft penalty)

✅ research/06_walkforward_baselines/purged_oos_protocol.md
   → walk_forward_splits embargo 미적용 진단
   → 3 separate models 설계 (pre-entry / continuation / risk)

✅ research/08_deployment/github_actions_deployment_plan.md
   → 4 cron workflows (smoke / monthly_picks / quarterly_backtest / classifier_retrain)
   → Streamlit Cloud + GDrive(rclone) + Slack
   → 비용 $0/월

✅ plan.md
   → Phase C1-C4 (3-4주) + Phase D (3일) + Phase E (1-2개월)
   → A/B 실험 설계 (D1-D3 / A1-A6 / G0-G4)
   → Final ship gate
```

## Phase C 사용자 결정 대기 (plan.md 참조)

```
Option A — 전체 Phase C 자동 (4주, 추천)
Option B — research notes 검토 먼저
Option C — Phase 부분 선택 (C1만 / C2만 / C3만)
Option D — 즉시 GitHub deployment (현재 in-sample biased state로)
```

**다음 세션 첫 액션**: 사용자에게 Option 확인 후 진행.

## 💼 Best config (live operation)

```python
TOP_N            = 20
WEIGHTING        = 'equal'
USE_DD_BREAKER   = True
DD_THRESHOLDS    = (-0.08, -0.15, -0.25)
DD_SCALES        = (0.85, 0.65, 0.40)
USE_VKOSPI_GUARD = True  # when source ready
REBAL_FREQ       = 'monthly_first_business_day'
COST_MODEL       = 'mcap_tiered'  # 5/10/20bp slip + 18bp tax
```

## 알려진 Issues (Tier 1 해결 필요)

1. pykrx 1.2.7 broken (KRX 2025 redesign) → FDR + Naver fallback ✅
2. KRX direct scrape 400 Bad Request → endpoint 변경 필요
3. PIT survivorship bias → -3~5pp CAGR realism
4. listed_months stub (모두 999)
5. TTM annual factor 단순화
6. KOSDAQ 150 / VKOSPI yfinance 404
7. FDR ticker 5-digit pad (delisted handling)

## 차단 사항 — 사용자 결정 대기

- 사용자가 plan.md Option A/B/C/D 결정 필요
- 결정 후 즉시 Phase C1 (PIT survivorship fix) 시작 가능

## 시스템 상태

- All data sources operational (FDR + Naver + DART + BOK)
- Realistic backtest framework verified
- Phase A research 완료, Phase B plan 완료
- Phase C 구현 미시작 (사용자 승인 후)

## 데이터 위치

```
Code:          H:/codex/kr_quant_engine/   (PROJECT_ROOT)
Data:          G:/내 드라이브/kr_quant_engine/   (DATA_ROOT)
GitHub:        wscha231/kr-quant-engine (private)
Latest commit: ~38.97% CAGR
Outputs:       outputs/realistic_backtest_best_5y.json
               outputs/realistic_backtest_best_monthly.csv
               outputs/current_portfolio_*.csv
               research/06_walkforward_baselines/p_mb_v1_oos_picks.csv
               research/06_walkforward_baselines/p_mb_v1_classifier_metrics.json
```
