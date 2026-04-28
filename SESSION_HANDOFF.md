# Session Handoff — Single Inbox

> 다음 세션에서 가장 먼저 읽을 파일. "방금 뭐 했고 다음에 뭐 할지" 한 가지만 적는다.
> Phase 끝날 때마다 덮어씀. 누적하지 말 것.

---

## 마지막으로 한 일 (2026-04-28 21:30 KST)

**전체 코드 정밀분석 + Pipeline 6-panel wiring fix SHIPPED.**

### 발견한 결정적 결함

`kr_pipeline.build_scored_panel_v0`이 `fund_panel`만 사전 빌드하고 나머지 6개 패널(event/macro/market_flow/ticker_flow/foreign_holding/derivatives)은 **None**으로 `add_universe_features`에 넘겨주고 있었음. 결과: 모든 PHASE 토글을 ON해도 P2/P2.5/P2.6/P3.2 시그널이 전부 NaN/0으로 채워짐. SESSION_HANDOFF가 다음 액션으로 제시한 "전 토글 ON baseline 측정"이 현재 코드 상태에서 동작 불가능했음.

### Fix 내용

1. `kr_features.load_or_build_event_panel()` 신설 (DART event panel cache wrapper)
2. `kr_flow.load_or_build_ticker_flow_panel()` 신설
3. `kr_flow.load_or_build_foreign_holding_panel()` 신설 (`sample_dates` 옵션 → 월말 fetch만으로 250x 가속)
4. `kr_pipeline.build_scored_panel_v0`이 phase 토글 기반으로 6개 패널 모두 사전 빌드 + 전달
5. `KR_ENGINE_REUSE_VERSION = "2026-04-28-p3-pipeline-wired"` (캐시 무효화)
6. 회귀 가드 smoke test 2건 추가

**Smoke test**: `tests/smoke_test.py --quick` 25/0 ✅

### 정밀분석에서 추가로 발견한 logic 위험 (deferred)

| # | 위치 | 이슈 | 우선 |
|---|---|---|---|
| A | `kr_dart_client.build_universe_quarterly_panel:521` | `period_end`를 `bsns_year+reprt_code` 하드코딩 → rebal 매칭 시 3-30일 룩어헤드 가능 (rcept_dt 직접 사용 권장) | ★ HIGH |
| B | `kr_flow.py:68-71`, `kr_derivatives.py:122-140` | pykrx ImportError silent return, 시그널 증발 감지 불가 | ★ HIGH |
| C | `kr_multibagger_classifier.py:66-67` | overlapping episode label leakage (9개월 윈도우 겹칠 시 양쪽 1로 라벨) | ★ HIGH (P_MB.2 차단) |
| D | `kr_universe.compute_listed_months` | stub → 999 반환, 12개월 필터 무력화 | MED |
| E | `kr_regime.py:81-88` | VKOSPI/USDKRW/PMI threshold 하드코딩 | MED |
| F | `kr_macro.py:325-329` | BOK monthly → daily ffill로 월말 시그널 클러스터링 | MED |
| G | `kr_pipeline.backtest._ticker_month_return` | 종목당 pykrx 호출 N×months 회 in loop, 큰 universe에서 매우 느림 | MED |
| H | `kr_dart_client._extract_key_accounts:334-385` | account_id 또는 한글명 매칭 실패 시 silent drop | LOW |

## 다음 액션

### 1. 사용자 — pip install 후 첫 fetch
```powershell
cd H:\codex\kr_quant_engine
py -3 -m pip install -r requirements.txt
git pull origin claude/code-analysis-planning-BH3Yz
py -3 tests/smoke_test.py             # 모든 162 tests 통과 확인
```

### 2. 사용자 — 전 토글 ON baseline 측정 (이제 실제 시그널이 들어감)
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

첫 실행은 모든 패널이 fresh build이므로 1-2시간 예상 (DART corp_code + event fetch 5,000+ calls). 이후 QUICK은 캐시 재사용 5-15분.

### 3. 다음 세션 (개발자, 우선순위 순)

**P_MB.2 multibagger classifier — 158 features 갖춰진 상태에서 핵심 ML lift**:
- 위 issue C (label deconfliction) 먼저 fix → 같은 ticker overlapping episode 발견 시 첫 surge_start만 keep
- `kr_multibagger.add_pre_surge_features(episodes, fund_panel, event_panel, flow_panel, macro_panel, deriv_panel, prices)` — 이번 wiring으로 모든 panel 호출 가능
- `tests/test_multibagger_classifier.py` end-to-end integration test (현재 dedicated test 없음, smoke만 존재)
- `research/06_walkforward_baselines/p_mb_v1_classifier_results.md` — AUC, top picks 정리

**기타 follow-up (incremental)**:
- Issue A: DART PIT 강화 → `rcept_dt` 직접 매칭으로 룩어헤드 제거
- Issue B: pykrx silent fail → 명시적 raise + loud log
- Issue D: `compute_listed_months` 실제 구현 (FDR ListingDate 또는 ticker_history 첫 record 기반)
- Issue G: backtest 종목별 fetch loop 제거 → 월말 OHLCV market snapshot 한 번에 가져와 in-memory join
- D-1: DART 전체 IS/BS/CF parsing (OCF / FCF 활성화)
- D-2: ROIC / Sloan accruals
- P2.7: 단기과열/투자경고/관리종목 (KRX scrape)
- P2.8: 테마 분류 + phase classifier (themes.yaml + Naver)

## 알려진 logic issues

위 정밀분석 결과 표 (A-H) 참조. 새 세션에서 우선순위 재확인 후 진행.

## 차단 사항
- pykrx + KOSIS API 미설치/미발급 (이전과 동일)
- DART 첫 fetch 시 ~5,000 API call → 한 번 받으면 캐시되어 이후 무영향

## GitHub
- Repo: https://github.com/wscha231/kr-quant-engine (private)
- 작업 브랜치: `claude/code-analysis-planning-BH3Yz`
- 다음 commit: pipeline 6-panel wiring + structural smoke tests
