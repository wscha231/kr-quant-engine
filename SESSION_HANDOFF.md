# Session Handoff — Single Inbox

> 다음 세션에서 가장 먼저 읽을 파일. "방금 뭐 했고 다음에 뭐 할지" 한 가지만 적는다.
> Phase 끝날 때마다 덮어씀. 누적하지 말 것.

---

## 마지막으로 한 일 (2026-04-28 22:30 KST)

**정밀분석 + Pipeline wiring (commit 145be80) + 후속 fix Issue B/C/D + GitHub Actions CI SHIPPED.**

### 이번 세션에 처리한 것

| 단계 | 내용 |
|---|---|
| 1 | 전체 코드 정밀분석 (7,521 lines, 16 modules) → 결정적 결함 발견: pipeline 6/7 패널 미연결 |
| 2 | **Pipeline 6-panel wiring** — `build_scored_panel_v0`이 phase 토글 기반으로 event/macro/market_flow/ticker_flow/foreign_holding/derivatives 모두 사전 빌드 + `add_universe_features` 전달. `KR_ENGINE_REUSE_VERSION=2026-04-28-p3-pipeline-wired`. |
| 3 | **Issue C** — multibagger classifier overlapping episode label leakage 제거: `deduplicate_overlapping_episodes()` 신설, `label_pre_surge`가 자동 호출. `tests/test_multibagger_classifier.py` 7 tests 신설. |
| 4 | **Issue B** — pykrx ImportError silent → loud-fail (kr_flow 3 위치, kr_derivatives 1 위치). 백테스트 도중 시그널 증발 위험 제거. |
| 5 | **Issue D** — `kr_universe.compute_listed_months` stub→real impl (FDR `StockListing` + `fetch_ticker_history` 폴백 + in-process cache). `min_listed_months=12` 필터가 비로소 작동. |
| 6 | **GitHub Actions CI** — `.github/workflows/smoke.yml` 추가. 모든 push/PR에서 quick smoke + label dedup tests 자동 실행. |
| 7 | Smoke `--quick`: 28/0 ✅ (이전 23 → 28, +5 regression guards) |

### 정밀분석에서 deferred 로 남긴 logic 위험 (다음 세션 처리)

| # | 위치 | 이슈 | 우선 |
|---|---|---|---|
| ~~B~~ | ~~kr_flow / kr_derivatives~~ | ~~pykrx silent fail~~ | **DONE 22:30** |
| ~~C~~ | ~~kr_multibagger_classifier~~ | ~~label leakage~~ | **DONE 22:30** |
| ~~D~~ | ~~kr_universe.compute_listed_months~~ | ~~stub 999~~ | **DONE 22:30** |
| A | `kr_dart_client.build_universe_quarterly_panel:521` | `period_end`를 `bsns_year+reprt_code` 하드코딩 → rebal 매칭 시 3-30일 룩어헤드 가능 (rcept_dt 직접 사용 권장) | ★ HIGH |
| E | `kr_regime.py:81-88` | VKOSPI/USDKRW/PMI threshold 하드코딩 (2023-24 튜닝) | MED |
| F | `kr_macro.py:325-329` | BOK monthly → daily ffill로 월말 시그널 클러스터링 | MED |
| G | `kr_pipeline.backtest._ticker_month_return` | 종목당 pykrx 호출 N×months 회 in loop, 큰 universe에서 매우 느림 | MED |
| H | `kr_dart_client._extract_key_accounts:334-385` | account_id 또는 한글명 매칭 실패 시 silent drop | LOW |

## 다음 액션

### 1. 사용자 — 로컬 환경에서 검증
```powershell
cd H:\codex\kr_quant_engine
git pull origin claude/code-analysis-planning-BH3Yz
py -3 -m pip install -r requirements.txt
py -3 tests/smoke_test.py             # 모든 162+ tests 통과 확인
py -3 tests/test_multibagger_classifier.py    # 7 dedup tests
```

### 2. 사용자 — 전 토글 ON baseline 측정 (이제 시그널이 실제로 들어감)
```powershell
$env:PHASE_PHASE0_MOMENTUM_ENABLED="1"
$env:PHASE_PHASE1_FUNDAMENTAL_ENABLED="1"
$env:PHASE_PHASE2_DART_EVENTS_ENABLED="1"
$env:PHASE_PHASE2_FLOW_ENABLED="1"
$env:PHASE_PHASE2_DERIVATIVES_ENABLED="1"
$env:PHASE_PHASE3_TECHNICAL_ENABLED="1"
$env:PHASE_PHASE3_MACRO_ENABLED="1"
$env:PHASE_PHASE3_REGIME_ENABLED="1"
py -3 run_local.py --quick --start-date 2019-01-01 --end-date 2024-12-31
```

첫 실행은 모든 패널이 fresh build + listing-date 해석으로 1-2시간 예상. 이후 캐시 재사용 5-15분.

### 3. 다음 세션 (개발자, 우선순위 순)

**P_MB.2 multibagger classifier — 이제 dedup 까지 완료, 본격 학습 가능**:
- `kr_multibagger.add_pre_surge_features(...)` — fund/event/flow/macro/deriv panel 호출 (wiring 으로 모두 가능)
- `kr_pipeline.run_p_mb_v1()` 실행 → AUC ≥ 0.55 + P@30 검증
- `research/06_walkforward_baselines/p_mb_v1_classifier_results.md` 결과 정리
- 결과 양호 시 backtest 와 blend 하는 `multibagger_classifier_weight` cfg 추가

**Issue A (HIGH) — DART PIT 강화**:
- `kr_dart_client.build_universe_quarterly_panel:521` 의 `period_end` 파생 제거 → caller가 `rcept_dt` 직접 매칭하도록 정리
- PIT 룩어헤드 3-30일 제거 → 백테스트 alpha 정확성 +2-5pp 영향 가능

**Issue G — backtest 속도**:
- `_ticker_month_return` 종목별 fetch 제거 → 월말 OHLCV market snapshot 한 번에 가져와 in-memory join
- 큰 universe (1,000+ ticker) 에서 백테스트 시간 10-100x 단축 기대

## 차단 사항
- pykrx + KOSIS API 미설치/미발급 (이전과 동일)
- DART 첫 fetch 시 ~5,000 API call → 한 번 받으면 캐시되어 이후 무영향

## GitHub
- Repo: https://github.com/wscha231/kr-quant-engine (private)
- 작업 브랜치: `claude/code-analysis-planning-BH3Yz`
- 직전 commit: `145be80` (pipeline 6-panel wiring)
- 다음 commit: Issue B/C/D + GitHub Actions CI
- **CI 추가됨**: 이후 모든 push/PR에서 자동 smoke 실행 (Actions 탭에서 결과 확인)
