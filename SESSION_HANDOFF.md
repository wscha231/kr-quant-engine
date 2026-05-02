# Session Handoff — Single Inbox

> 다음 세션에서 가장 먼저 읽을 파일.

---

## ⚠️ NEXT SESSION 시작 순서 (필수)

```
1. 이 파일 (SESSION_HANDOFF.md) 읽기
2. plan.md 읽기 (Phase C/D/E 로드맵)
3. (선택) research/ 5개 노트
4. 사용자 확인:
   - Phase E (Paper trading) 진행 여부
   - 또는 A/B 실험 (D1-D3 / A1-A6 / G0-G4) 즉시 실행 여부
   - 또는 GDrive sync + GitHub Secrets 설정 후 첫 cron 실행
```

**현재 Status (2026-05-02)**: **Phase C 전체 완료 + Phase D 완료 (코드 사이드만)**.
GitHub Actions secrets 설정 + 첫 cron 실행 = 사용자 액션 대기.

---

## 마지막 큰 성과 (2026-05-02)

### 🏆 Phase C 완전 종료 (4 sub-phases)

```
post-c1 : PIT survivorship + macro publication-lag
post-c2 : Korean governance risk overlay (15 chaebol groups)
post-c3 : Purged walk-forward + 3-sleeve label / picks
post-c4 : VKOSPI fetch + realized-vol fallback + PIT integration
```

### Test 통과 현황 (regression-clean)

```
44/44  smoke_test
 7/7   test_pit_universe        (Phase C1)
 8/8   test_governance          (Phase C2)
 8/8   test_walkforward         (Phase C3)
 5/5   test_vkospi              (Phase C4)
-----
72/72  TOTAL
```

### Phase D — GitHub Actions deployment 완료 (코드 사이드)

```
.github/workflows/
  smoke_test.yml                   (push hook)
  monthly_picks.yml                (1st 04:00 KST cron)
  quarterly_backtest.yml           (분기 1일 06:00 KST)
  monthly_classifier_retrain.yml   (1st 02:00 KST cron)

tools/
  generate_monthly_picks.py        (CLI: PIT universe -> features -> classifier -> picks CSV)
  run_quarterly_backtest.py        (CLI: realistic backtest -> JSON+CSV)
  run_classifier_retrain.py        (CLI: walk-forward retrain -> .cbm + metrics)
  build_pit_universe_history.py    (Phase C1, 이미 작성)

streamlit_app.py                   (Streamlit Cloud entry; 자동 picks/backtest 표시)
secrets.toml.example               (Streamlit Secrets 템플릿)
requirements.txt                   (streamlit + lightgbm 추가)
.gitignore                         (secrets.toml + data_pit 제외)
```

---

## Phase A 산출물 (2026-04-30 완료, read-only)

```
research/00_data_sources_audit/pit_survivorship_audit.md             ✅
research/02_factor_zoo/institutional_methods_import_map.md           ✅
research/03_korea_specific_signals/governance_risk_overlay_research.md ✅
research/06_walkforward_baselines/purged_oos_protocol.md             ✅
research/08_deployment/github_actions_deployment_plan.md             ✅
plan.md                                                               ✅
```

---

## Phase C 작업 요약

### C1 — PIT survivorship fix (2026-05-02 commit 852085b)

```
kr_pit_universe.py                   (NEW, 360 lines)
  build_listed_history_from_cache    -- 108 mktcap snapshot 스캔
  build_historical_mcap_panel
  fetch_listing_at_date               -- PIT-strict, no FDR-current 폴백
  compute_listed_months_pit
  get_mcap_at_date

kr_universe.py                       (modified)
  build_universe_snapshot             -- PIT path 통합
  compute_listed_months               -- 999 stub 제거

kr_macro.py                          (modified)
  PUBLICATION_LAG_DAYS                -- BOK/KOSIS pub-lag map
  get_macro_snapshot_pit              -- per-series cutoff

tools/build_pit_universe_history.py  (NEW)
tests/test_pit_universe.py           (NEW, 7 tests)

artifacts:
  data_pit/listed_history.parquet    (3,299 tickers)
  data_pit/historical_mcap.parquet   (287,910 rows)
```

### C2 — Korean governance risk overlay (commit 44fcdf7)

```
kr_governance.py                     (NEW, 450 lines)
  compute_owner_dilution_risk         -- 유상증자 + CB + BW + 제3자배정
  compute_treasury_overhang_risk      -- 자사주 처분 (=! 매입)
  compute_spinoff_risk                -- 물적분할
  compute_capital_reduction_risk      -- 감자
  compute_succession_proxy            -- chaebol membership × insider activity
  compute_capital_allocation_quality  -- 자사주 매입 + 무상증자
  compute_governance_score_for_ticker -- 11 PHASE2_GOVERNANCE_COLUMNS aggregator
  is_hard_veto                       -- 5 hard-veto rules
  governance_weight_cap              -- tier-based (0.40 / 0.60 / 0.80)
  add_governance_signals             -- universe-level wiring

governance_entities.yaml             (NEW, 15 chaebol groups)
kr_config.py                         (PHASE2_GOVERNANCE_COLUMNS x11 추가)
kr_features.py                       (add_governance_signals 통합)
kr_backtester_realistic.py           (use_governance_overlay flag)

tests/test_governance.py             (NEW, 8 tests)
```

### C3 — Purged walk-forward + 3-sleeve (commit afe97ec)

```
kr_multibagger_classifier.py         (modified)
  walk_forward_splits_purged          -- 9-month embargo strict
  label_pre_entry                     -- [-6m, -1m)
  label_continuation                  -- [0, +3m]
  label_risk                          -- forward DD <= -20%
  calibration_curve                   -- predicted vs actual rate
  adversarial_validation              -- distribution shift detector

kr_backtester_realistic.py           (modified)
  generate_oos_picks_purged_3sleeve   -- 3 separate models, p_combined ranking
  run_realistic_backtest              -- use_sleeve_separation flag (40/40/20)

tests/test_walkforward.py            (NEW, 8 tests)
```

### C4 — VKOSPI source fix + risk integration (commit 8fbaa6f)

```
kr_derivatives.py                    (modified)
  _compute_realized_vol_proxy         -- KOSPI std × sqrt(252)
  fetch_vkospi                        -- pykrx -> yfinance -> proxy
  get_vkospi_at_date                  -- PIT lookup helper

kr_backtester_realistic.py           (modified)
  derivatives_panel                   -- new arg, replaces hardcoded 18.0
  vkospi_default_level

tests/test_vkospi.py                 (NEW, 5 tests)
```

---

## Phase D — GitHub Actions deployment

### 사용자가 해야 할 액션 (코드는 완료됨)

```
1. GitHub Secrets 설정 (Settings -> Secrets and variables -> Actions):
   DART_API_KEY            = (현재 .env 값)
   BOK_ECOS_API_KEY        = (현재 .env 값)
   RCLONE_CONFIG_GDRIVE    = `rclone config show` 출력 전체
   SLACK_WEBHOOK_URL       = (선택)

2. GDrive 폴더 구조 확인 (rclone remote name = `gdrive`):
   gdrive:kr_quant_engine/
     ├ cache_pykrx/
     ├ cache_dart/
     ├ cache_macro/
     ├ feature_store/
     ├ models/
     ├ data_pit/
     └ outputs/

3. 첫 수동 trigger:
   - GitHub UI -> Actions -> Monthly Picks -> Run workflow
   - 또는 cron 다음 1일까지 대기

4. Streamlit Cloud (선택):
   - https://share.streamlit.io 에서 repo 등록
   - Entry point: streamlit_app.py
   - Secrets에 DART_API_KEY 등 설정
```

### 체크리스트

```
✅ 4 workflows 작성 (.github/workflows/)
✅ 3 CLI tools (tools/)
✅ streamlit_app.py
✅ secrets.toml.example
✅ requirements.txt 업데이트 (streamlit, lightgbm)
✅ .gitignore 업데이트
☐ GitHub Secrets 설정 (사용자)
☐ rclone GDrive auth (사용자)
☐ 첫 monthly_picks 수동 trigger
☐ Slack workspace + webhook (사용자)
☐ Streamlit Cloud 등록 (선택)
```

---

## 알려진 Issues (Phase C 후 잔존)

| # | Issue | Phase 적용 | 상태 |
|---|---|---|---|
| 1 | pykrx 1.2.7 broken (KRX 2025 redesign) | C4 (realized-vol proxy) | ✅ |
| 2 | KRX direct scrape 400 Bad Request | (deferred) | ⚠️ |
| 3 | PIT survivorship bias | C1 | ✅ |
| 4 | listed_months stub (모두 999) | C1 | ✅ |
| 5 | TTM annual factor 단순화 | (deferred) | ⚠️ |
| 6 | KOSDAQ 150 / VKOSPI yfinance 404 | C4 (proxy fallback) | ✅ |
| 7 | FDR ticker 5-digit pad (delisted handling) | C1 | ✅ |
| 8 | walk_forward embargo 미적용 | C3 | ✅ |
| 9 | Single-model label window mixing | C3 | ✅ |
| 10 | Hardcoded vkospi_level=18.0 | C4 | ✅ |

---

## 다음 단계 옵션

### Option A — 즉시 첫 monthly_picks 실행
- Secrets 설정 → Actions UI에서 manual trigger
- 결과 CSV 확인 후 다음 cron까지 대기

### Option B — A/B 실험 즉시 시작 (plan.md)
- D1-D3 (PIT/historical mcap/purged) 단계별 baseline
- A1-A6 (alpha layers)
- G0-G4 (governance)
- 5년 OOS CAGR 측정 → Final ship gate

### Option C — Phase E 직접 진입
- KIS API 모의매매 setup
- 1-2개월 paper trading
- 신뢰도 확인 후 live 소액

### Option D — TTM annual factor + KRX direct scrape 추가 fix
- 잔존 Issue #2 #5 해결

**추천**: B (A/B 실험) → C (Paper trading). A는 secrets 설정 후 자동 진행.

---

## 시스템 상태

- All Phase C-D code committed + tagged.
- 72/72 tests pass (regression-clean).
- GitHub Actions ready (코드만, secrets/run = 사용자 액션).
- Streamlit dashboard ready (cloud 등록만).
- Paper trading 준비 가능 (KIS API key 필요).

## 데이터 위치

```
Code:          H:/codex/kr_quant_engine/                       (PROJECT_ROOT)
Data:          G:/내 드라이브/kr_quant_engine/                   (DATA_ROOT)
GitHub:        wscha231/kr-quant-engine (private)

Latest tags:   post-c1, post-c2, post-c3, post-c4
Latest commit: Phase C4 VKOSPI fix (8fbaa6f) + Phase D pending commit

PIT artifacts: G:/내 드라이브/kr_quant_engine/data_pit/
  listed_history.parquet     (3,299 tickers)
  historical_mcap.parquet    (287,910 rows, 108 snapshots)

Outputs:       G:/내 드라이브/kr_quant_engine/outputs/
  realistic_backtest_best_5y.json
  realistic_backtest_best_monthly.csv
  current_portfolio_*.csv
```

## 참고 — Best config (live 운영 기준값)

```python
TOP_N            = 20
WEIGHTING        = 'equal'
USE_DD_BREAKER   = True
DD_THRESHOLDS    = (-0.08, -0.15, -0.25)
DD_SCALES        = (0.85, 0.65, 0.40)
USE_VKOSPI_GUARD = True
USE_GOVERNANCE   = True   # Phase C2 신규
USE_SLEEVE       = True   # Phase C3 신규 (picks에 p_pre_entry/p_continuation 있을 때)
REBAL_FREQ       = 'monthly_first_business_day'
COST_MODEL       = 'mcap_tiered'  # 5/10/20bp slip + 18bp tax
```
