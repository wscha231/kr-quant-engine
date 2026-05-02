# Session Handoff — Single Inbox

> 다음 세션에서 가장 먼저 읽을 파일.

---

## ⚠️ NEXT SESSION 시작 순서 (필수)

```
1. 이 파일 (SESSION_HANDOFF.md) 읽기
2. (선택) plan.md / research/*.md 검토
3. 사용자 확인:
   - Phase E (KIS API paper trading) 진행 여부
   - 또는 A/B 실험 매트릭스 본격 실행 (D1-D3 / A1-A6 / G0-G4)
   - 또는 picks 결과 review + 매수 결정
```

**현재 Status (2026-05-02)**: **Phase A+B+C+D 모두 완료. GitHub Actions 자동화 운영 중.**
- 마지막 commit: `33bd08f` (Fix: feature alignment between classifier training and inference)
- 가장 최근 성공 run: `25248175771` (success, 51.8min, p_pre_surge 정상 작동)

---

## 🚀 자동화 운영 상태 (Phase D 완료)

### GitHub Actions Workflows

| Workflow | Cron | 마지막 결과 | Outputs |
|---|---|---|---|
| `smoke_test.yml` | push hook | passed | tests |
| `monthly_picks.yml` | 매월 1일 04:00 KST | ✅ success (51.8min) | `outputs/live_portfolio_*.csv` |
| `quarterly_backtest.yml` | 분기 1일 06:00 KST | not yet run | `outputs/quarterly_backtest_*.json` |
| `monthly_classifier_retrain.yml` | 매월 1일 02:00 KST | not yet run | `models/p_mb_v_*.cbm` |
| `daily_event_monitor.yml` | 평일 18:00 KST | not yet run | `outputs/daily_alerts_*.json` |

### Secrets 설정 완료
- `DART_API_KEY` ✓
- `BOK_ECOS_API_KEY` ✓
- `RCLONE_CONFIG_GDRIVE` ✓
- `SLACK_WEBHOOK_URL` ⚠️ (선택, 미설정)

### GDrive 동기화 검증
- gdrive scope: `drive` (read+write) ✓
- 폴더 구조 11개 모두 존재 ✓
- live_portfolio CSV 자동 sync 확인 ✓

---

## 🏆 현재 Live Picks (2026-04-30)

```
classifier: p_mb_v_2026-05.cbm (mean AUC 0.7996, 5-fold purged WF)
universe:   1670/2772 eligible (KOSPI 615, KOSDAQ 1055)
top picks:  20개, equal weight 5%

Top 5:
  1. DB (012030)        p=0.0162, gov=0.014
  2. 넥센타이어 (002350)  p=0.0160, gov=0.000
  3. JW홀딩스 (096760)    p=0.0159, gov=0.000
  4. LG (003550)         p=0.0158, gov=0.045  (chaebol prior)
  5. SK네트웍스 (001740)  p=0.0158, gov=0.052  (chaebol prior)
```

> Top picks는 "p_pre_surge probability" 기준. 현재 prevalence ~1.84%, top 픽이 0.016 정도 (~1.0× prevalence) — 예상대로 보수적 수준.

---

## 📊 Phase 완료 매트릭스

### Phase A (research) — 5 노트 + plan.md
- `research/00_data_sources_audit/pit_survivorship_audit.md`
- `research/02_factor_zoo/institutional_methods_import_map.md`
- `research/03_korea_specific_signals/governance_risk_overlay_research.md`
- `research/06_walkforward_baselines/purged_oos_protocol.md`
- `research/08_deployment/github_actions_deployment_plan.md`

### Phase B (plan) — `plan.md` ✅

### Phase C (implementation) — 4 sub-phases, 모두 tagged

| Phase | Tag | 변경 | Tests |
|---|---|---|---|
| C1 PIT survivorship | `post-c1` | `kr_pit_universe.py`, `data_pit/listed_history.parquet` (3,299), `historical_mcap.parquet` (287,910 rows), BOK pub-lag map | 7/7 |
| C2 Governance overlay | `post-c2` | `kr_governance.py`, `governance_entities.yaml` (15 chaebol), 11 PHASE2_GOVERNANCE_COLUMNS, hard veto + tier weight cap | 8/8 |
| C3 Purged WF + 3-sleeve | `post-c3` | `walk_forward_splits_purged`, label_pre_entry/continuation/risk, `generate_oos_picks_purged_3sleeve`, sleeve mode in backtester | 8/8 |
| C4 VKOSPI fix | `post-c4` | realized-vol proxy fallback, `get_vkospi_at_date`, PIT lookup in backtester | 5/5 |

### Phase D (deployment) — 4 workflows + 3 CLIs + dashboard, tagged `post-d`

추가 트랙 A+B+C+D (Codex spec 6.1, 6.2, 6.5, 6.6, 10):
- Track D: daily_event_monitor (Renaissance pattern)
- Track C: sector_neutral_ranks + trend_gate_multiplier (Minervini gate)
- Track B: ensemble blender (CB+LGBM ranker+LR, 0.5/0.4/0.1 blend)
- Track A: A/B matrix orchestrator (15 variants D1-D3 / A1-A6 / G0-G4)

**Smoke 회귀**: 72/72 tests pass.

---

## 🐞 Production Issues 해결 기록

### Issue 1: rclone env conflict (run 25244699916)
- 증상: `RCLONE_CONFIG` env 변수가 rclone 자체 reserved name이라 config 텍스트를 path로 해석 → critical error
- 수정: 모든 workflow에서 `RCLONE_CFG_TEXT` 으로 rename + `printf '%s'` 사용
- Commit: `3572d25`

### Issue 2: cache_pykrx 798 MiB sync timeout (run 25245778751)
- 증상: GitHub Actions runner에서 1시간+ rclone sync 후 timeout
- 수정: `kr_pit_universe.fetch_listing_at_date`가 `historical_mcap.parquet` (1.8 MiB)을 우선 사용 → cache_pykrx sync 생략
- Commit: `93aef9b`

### Issue 3: governance scalar fillna bug
- 증상: `compute_owner_dilution_risk`에서 `pd.to_numeric(df.get(col, 0)).fillna(0)` — column 없으면 scalar 0 반환 → fillna 실패
- 수정: 5곳 모두 `if col in df.columns:` 가드 추가 + zero-Series fallback
- Commit: `1982254`

### Issue 4: classifier feature mismatch (run 25246999591)
- 증상: 모델은 110 features 학습, 추론 시 일부 feature 미존재 → momentum fallback (p_pre_surge=4.0 모두 동일)
- 수정: classifier_metrics_*.json에 feature_cols 저장 + 추론 시 align (missing은 0-fill)
- Commit: `33bd08f`

### Final state (run 25248175771): SUCCESS in 51.8min
- p_pre_surge 정상 [0.012, 0.016]
- 다양한 governance risk score
- GDrive에 picks CSV + meta JSON 자동 sync

---

## 💼 Best config (live 운영)

```python
TOP_N            = 20
WEIGHTING        = 'equal'
USE_DD_BREAKER   = True
DD_THRESHOLDS    = (-0.08, -0.15, -0.25)
DD_SCALES        = (0.85, 0.65, 0.40)
USE_VKOSPI_GUARD = True   # realized-vol proxy via kr_derivatives
USE_GOVERNANCE   = True   # 11 columns, hard veto + tier weight cap
USE_SLEEVE       = True   # when picks have p_pre_entry/p_continuation
GOV_PENALTY      = 0.35
SLEEVE_DEFENSIVE = 0.20   # 20% cash floor
REBAL_FREQ       = 'monthly_first_business_day'
COST_MODEL       = 'mcap_tiered'
```

## 데이터 위치

```
Code:          H:/codex/kr_quant_engine/                 (PROJECT_ROOT)
Data:          G:/내 드라이브/kr_quant_engine/             (DATA_ROOT, =gdrive:)
GitHub:        wscha231/kr-quant-engine (private)

Latest commits:
  33bd08f Fix: feature alignment classifier training/inference
  1982254 Fix: governance scalar-default fillna bug + workflow partial sync
  93aef9b Workflow speedup: skip cache_pykrx, use data_pit
  3572d25 Fix: rename RCLONE_CONFIG env to RCLONE_CFG_TEXT
  2840c14 Track A+B+C+D: A/B + ensemble + sector/trend + daily monitor
  f0736db Phase D: GitHub Actions + Streamlit dashboard
  8fbaa6f Phase C4: VKOSPI fix
  afe97ec Phase C3: Purged WF + 3-sleeve
  44fcdf7 Phase C2: Governance overlay
  852085b Phase C1: PIT survivorship + macro pub-lag

Tags: post-c1, post-c2, post-c3, post-c4, post-d

Outputs (live):
  G:/내 드라이브/kr_quant_engine/outputs/live_portfolio_2026-04-30.csv
  G:/내 드라이브/kr_quant_engine/outputs/live_meta_2026-04-30.json
  G:/내 드라이브/kr_quant_engine/outputs/live_portfolio_latest.csv

Models:
  G:/내 드라이브/kr_quant_engine/models/classifier_latest.cbm
  G:/내 드라이브/kr_quant_engine/models/p_mb_v_2026-05.cbm
  G:/내 드라이브/kr_quant_engine/models/classifier_latest_metrics.json (with feature_cols)
```

---

## 다음 단계 옵션

### Option A — Streamlit Cloud 등록 (5분, web UI)
- https://share.streamlit.io 접속 → wscha231/kr-quant-engine repo
- Entry: `streamlit_app.py`
- Secrets: DART_API_KEY, BOK_ECOS_API_KEY
- Result: https://kr-quant-engine.streamlit.app 자동 데이터 표시

### Option B — Phase E paper trading (KIS API, 1-2개월)
- KIS API key 발급 → 모의투자 계좌
- `kr_paper_executor.py`, `kr_portfolio_tracker.py`, `kr_trade_log.py` 추가 필요
- 매월 picks → KIS API 모의 매매 → 체결가 vs 가정 비교

### Option C — A/B 실험 본격 실행
- 새 picks 생성 (`generate_oos_picks_purged_3sleeve` + governance merge)
- `tools/run_ab_matrix.py` 15 variants 전체 실행
- ~3-4시간 소요, ship gate 검증

### Option D — Live picks 직접 매수
- 위 Top 20 picks을 manual 매매
- 다음 monthly_picks 결과까지 약 1개월 holding

---

## 알려진 잔존 issues

| # | 이슈 | 영향 |
|---|---|---|
| 1 | cache_dart 96MB sync 시 6890 작은 파일이 rclone API 호출 많아 25-35min 소요 | workflow 시간 |
| 2 | `phase1_fundamental DISABLED` (env 미설정) → fundamental zero-fill | feature quality (개선 가능) |
| 3 | `phase2_dart_events DISABLED` → DART event signals zero | governance 일부 작동 안 함 |
| 4 | 현재 picks의 p_pre_surge ~0.016 (낮음) — top 픽 차별화 약함 | model 학습 데이터 보강 필요 |
| 5 | A/B matrix 실 실행 결과 없음 | ship gate 미검증 |

이슈 1은 cache_dart 사용 줄이거나 단계별 sync. 이슈 2-3은 workflow env에 phase 토글 추가. 이슈 4-5는 추후 매일 운영하면서 자연스럽게 개선.

---

## 시스템 상태 요약

- ✅ Phase A 연구 완료
- ✅ Phase B 계획 완료
- ✅ Phase C1-C4 구현 + 테스트 완료 (72/72)
- ✅ Phase D 배포 코드 + 인프라 완료
- ✅ GitHub Actions 자동 운영 검증 완료 (run 25248175771 success)
- ✅ GDrive sync 검증 완료
- ⏳ 다음 cron: 매월 1일 04:00 KST monthly_picks 자동 실행
- ⏳ Streamlit Cloud 등록 (사용자 선택)
- ⏳ Phase E paper trading (사용자 선택)
