# kr_quant_engine — Changelog

> Agent Update Contract: 모든 commit은 이 파일에 entry 추가. `HH:MM KST` 타임스탬프 + `symbols_added` / `symbols_changed` / `config_fields_added` / `breaking_changes` 필드 명시. 적용 안되면 `none`.

---

## 2026-04-27

### 14:00 KST — p0-bootstrap-scaffolding

**Scope**: kr_quant_engine 프로젝트 초기 셋업. Reference repo (r1000-quant-engine) 설계 패턴 차용.

**What landed**:
- 디렉토리 구조: `cache_pykrx`, `cache_dart`, `cache_macro`, `cache_misc`, `feature_store`, `data_raw`, `outputs`, `outputs_advisor`, `models`, `backtest_results`, `research/{00..99}`, `tests`, `tools`
- 보안: `.gitignore` (secrets + caches 제외), `.env.example` (template), `.env` (BOK_ECOS_API_KEY 저장 — gitignored)
- 의존성: `requirements.txt` (pykrx, opendartreader, finance-datareader, catboost, exchange-calendars, scikit-learn 등)
- 문서: `README.md`, `CLAUDE.md`, `MASTER_PLAN.md`, `SESSION_HANDOFF.md`, `CHANGELOG.md` (this)
- 설정 YAML: `universe.yaml` (KOSPI+KOSDAQ 멤버십 + hard/soft 필터 규칙), `themes.yaml` (10개 핵심 테마 + phase classifier 규칙)
- research/ READMEs: 8개 서브폴더 각각 단일 진실 README (00 data_sources_audit, 01 universe_construction, 02 factor_zoo, 03 korea_specific_signals, 04 regime_detection, 05 cost_friction_audit, 06 walkforward_baselines, 07 phase_experiments, 99 archive)

**symbols_added**: none (scaffolding only)
**symbols_changed**: none
**config_fields_added**: none
**breaking_changes**: none

**Verdict**: scaffolding complete.

---

### 15:30 KST — p0-modules-implementation

**Scope**: P0 코드 모듈 구현 — pykrx + BOK macro fetch까지 fully functional, DART 도착 후 P1 펀더멘털 시그널 추가 예정.

**What landed**:
- `kr_config.py` (220 lines) — `KR_ENGINE_REUSE_VERSION="2026-04-27-p0-bootstrap"`, `DEFAULT_CFG` (40 keys), `PHASE0_MOMENTUM_COLUMNS`, `PHASE1_FUNDAMENTAL_COLUMNS`, `PHASE2_KOREA_ALPHA_COLUMNS`, `PHASE3_REGIME_COLUMNS`, `MACRO_BOK_SERIES` (10 series), `DEFAULT_ROUND_TRIP_COST`
- `kr_helpers.py` (180 lines) — `phase_is_enabled`, `cross_sectional_robust_z`, `percentile_rank`, `sign_flip_pos/neg`, `hard_sanitize`, `safe_float`, `load_dotenv_if_present`, `log`
- `kr_pykrx_client.py` (270 lines) — `fetch_listing`, `fetch_daily_ohlcv_market`, `fetch_market_cap_market`, `fetch_per_pbr_eps_bps`, `fetch_foreign_inst_flow`, `fetch_index_ohlcv`, `fetch_ticker_history`, `fetch_business_days`, `fetch_month_end_business_days`. Per-day parquet caching at `cache_pykrx/`.
- `kr_bok_client.py` (210 lines) — `fetch_bok_series` (single ECOS series), `fetch_macro_panel` (multi-series wide panel, daily ffill), `list_macro_keys`. Per-series parquet cache at `cache_macro/bok_*.parquet`.
- `kr_universe.py` (250 lines) — `build_universe_snapshot` (single rebal date), `build_universe_monthly_v0` (full historical panel). Hard exclusions: 우선주 (정규식 끝자리 5/7/9), SPAC (이름 패턴), REIT (이름 패턴). Soft filters: 60d avg trading value ≥ 5억원, mktcap ≥ 500억원, listed ≥ 12 months. `compute_avg_trading_value_60d` aggregates 60 daily snapshots.
- `kr_features.py` (200 lines) — `add_basic_momentum` (1m/3m/6m/12m + 12-1m skip + RS_kospi/kosdaq), `add_basic_value` (PER/PBR/EPS from pykrx TTM), `add_cross_sectional_ranks`, `compute_p0_score` (50% ret_12_1m + 30% ret_6m + 20% rs_kospi_12m). Phase toggle `PHASE_PHASE0_MOMENTUM_ENABLED` with zero-fill fallback.
- `kr_pipeline.py` (260 lines) — `build_scored_panel_v0`, `select_topn_per_month`, `backtest_topn_momentum_v0` (월 rebal + cost-aware turnover penalty + per-name 14% cap), `run_full_validation_suite` (member count, NaN cliff, member stability checks), `run_p0_baseline` (orchestrator), `run_verdict_only`.
- `run_local.py` (90 lines) — argparse entry: `--quick` / `--full` / `--verdict-only` / `--no-collector` / `--phase0-momentum=auto|0|1`.
- `tests/smoke_test.py` (310 lines) — 23 tests: syntax (1), structural (8), import (7), logic (5), regression (2). 통과 기록: 23/23 in 0.78s.

**Cost model recalibration** (2026-04-27 15:25 KST):
- 초기 추정 22bp는 슬리피지 단방향만 반영한 오류 → 양방향 5bp = 10bp 합산
- 실제: 매도세 18bp + 양방향 수수료 3bp + 양방향 슬리피지 10bp = **왕복 31bp** (mid-cap baseline)
- Doc/test 모두 31bp로 업데이트

**symbols_added**:
- kr_config: KR_ENGINE_REUSE_VERSION, DEFAULT_CFG, PHASE0_MOMENTUM_COLUMNS, PHASE1_FUNDAMENTAL_COLUMNS, PHASE2_KOREA_ALPHA_COLUMNS, PHASE3_REGIME_COLUMNS, ALL_PHASE_COLUMNS, MACRO_BOK_SERIES, MACRO_FRED_SERIES, MACRO_YF_TICKERS, ACCEPTANCE_CHECKS, BENCHMARK_KOSPI200, BENCHMARK_KOSDAQ150, DEFAULT_ROUND_TRIP_COST, PRICE_LIMIT_PCT, EXCHANGES, BACKTEST_START_DATE
- kr_helpers: phase_is_enabled, cross_sectional_robust_z, percentile_rank, sign_flip_pos, sign_flip_neg, hard_sanitize, safe_float, safe_int, log, load_dotenv_if_present, to_month_end, previous_business_day, get_paths
- kr_pykrx_client: fetch_listing, fetch_daily_ohlcv_market, fetch_market_cap_market, fetch_per_pbr_eps_bps, fetch_foreign_inst_flow, fetch_index_ohlcv, fetch_ticker_history, fetch_business_days, fetch_month_end_business_days, PYKRX_AVAILABLE
- kr_bok_client: fetch_bok_series, fetch_macro_panel, list_macro_keys
- kr_universe: build_universe_snapshot, build_universe_monthly_v0, compute_avg_trading_value_60d, compute_listed_months, is_preferred, is_spac, is_reit
- kr_features: add_basic_momentum, add_basic_momentum_for_ticker, add_basic_value, add_cross_sectional_ranks, compute_p0_score, add_universe_features
- kr_pipeline: build_scored_panel_v0, select_topn_per_month, backtest_topn_momentum_v0, run_full_validation_suite, run_p0_baseline, run_verdict_only

**symbols_changed**: none
**config_fields_added**: 모든 DEFAULT_CFG 키 (40개) — first time
**breaking_changes**: none (greenfield)

**Validation**: `py -3 tests/smoke_test.py` → 23 passed, 0 failed in 0.78s

**Next**:
1. 사용자 액션 — `py -3 -m pip install -r requirements.txt` (pykrx, requests, pyarrow 등)
2. Sanity test — `py -3 kr_pykrx_client.py` (pykrx 첫 fetch 동작 확인)
3. Sanity test — `py -3 kr_bok_client.py` (BOK API 연결 확인)
4. P0 baseline 측정 — `py -3 run_local.py --quick --start-date 2019-01-01 --end-date 2024-12-31` (5년 sample)
5. DART 키 도착 시 → P1 진입 (`kr_dart_client.py` + `add_fundamental_features`)

**Verdict**: P0 scaffolding + module 구현 SHIP. Baseline metric은 사용자가 의존성 설치 후 실행해서 측정.

---

## 2026-04-28

### 09:55 KST — p1-dart-fundamentals-implementation

**Scope**: DART OpenAPI 키 도착 (예상 2일 → 1일). P1 펀더멘털 client + PIT-safe 시그널 구현 + 실측 검증.

**Why now**: DART 키 즉시 활용해서 P0 baseline 측정과 P1 펀더멘털을 동시 진행. 사용자가 pip install + 실행하면 P0 또는 P1 둘 다 가능.

**What landed**:
- `.env` — `DART_API_KEY` 추가 (gitignored)
- `kr_dart_client.py` (470 lines) — DART OpenAPI 통합:
  - `download_corp_code()` — corpCode.xml ZIP 다운로드 + 추출 (1일 TTL)
  - `parse_corp_code_to_df()` — XML → DataFrame (117k corps, 4k listed)
  - `fetch_corp_to_ticker_map()` — listed 종목만 ticker ↔ corp_code
  - `fetch_disclosure_list()` — 공시 검색 (P2 이벤트 시그널 prep)
  - `fetch_single_company_financials()` — fnlttSinglAcntAll (단일회사 전체 재무제표)
  - `fetch_multi_company_main_accounts()` — fnlttMultiAcnt (최대 100개 corps bulk, ~100x faster)
  - `extract_key_accounts()` — XBRL account_id 또는 한국어 account_nm fallback 매칭
  - `build_corp_quarterly_panel()` — 단일 corp 분기별 long-format
  - `build_universe_quarterly_panel()` — bulk fetch로 전체 universe 분기 panel
  - `pit_filter_panel()` — `rcept_dt <= as_of` PIT 필터
  - `KEY_ACCOUNTS` — XBRL standard ID 매핑 (revenue, operating_income, net_income, assets, equity, liabilities, OCF)
  - `KOREAN_NAME_FALLBACK` — 한글 계정명 fallback (구버전 filings 호환)
  - `REPRT_CODES` — 11011 (annual), 11012 (semi), 11013 (Q1), 11014 (Q3)
- `kr_features.py` — P1 features 추가 (~270 lines added):
  - `prepare_pit_fundamentals_panel()` — universe ticker → corp_code → bulk DART panel
  - `add_pit_fundamentals()` — PIT-safe join + TTM/YoY/ratios + composite signals
  - `_compute_ttm_from_panel()` — 분기 보고서 → TTM 환산 (annual은 그대로, 분기는 annualization factor)
  - `_compute_yoy_from_panel()` — 동분기 전년 대비 성장률
  - `compute_value_score()` — z(−PER) + z(−PBR) + z(div_yield) + z(opi_growth)
  - `compute_quality_score()` — z(ROE) + z(opi_margin) + z(−D/E)
  - `compute_turnaround_score()` — 영업이익/순이익 양전환 + 50%+ YoY 가속
  - `compute_p1_score()` — 0.40 P0 + 0.25 value + 0.25 quality + 0.10 turnaround
- `kr_config.py`:
  - `KR_ENGINE_REUSE_VERSION` bump: `2026-04-27-p0-bootstrap` → `2026-04-28-p1-dart-fundamentals` (cache invalidation)
  - `phase1_fundamental_enabled` default OFF → ON
  - DART config keys 추가: `dart_fund_start_year=2014`, `dart_polite_sleep_s=0.3`, `dart_use_consolidated_first=True`, `dart_refresh_days_*`
- `kr_pipeline.py`:
  - `build_scored_panel_v0` — 월별 루프 전 fund_panel 한 번 pre-build (캐시), 모든 month-end snapshot에 PIT join
  - `select_topn_per_month` — score_col auto-detect (p1_blended_score 우선, fallback p0_momentum_score)
  - `backtest_topn_momentum_v0` — score weighting이 사용된 score_col 자동 인식
- `tests/test_dart_pit.py` (155 lines) — 13 PIT invariant tests (import, REPRT_CODES, KEY_ACCOUNTS, 한글 fallback, pit_filter empty/correctness/NaN-drop, phase1 toggle, API 키, version, value/quality/p1_score logic)

**Real API verification (samsung 005930 / corp_code 00126380)**:
- corp_code XML 다운로드: 28MB / 117,161 corps / 3,961 listed
- 삼성전자 2023 사업보고서 fetch:
  - revenue = 258,935,494,000,000 (258.9조) ← actual ✓
  - operating_income = 6,566,976,000,000 (6.57조) ← actual ✓
  - net_income = 15,487,100,000,000 (15.5조) ← actual ✓
  - total_assets = 455.9조, total_equity = 363.7조, total_liabilities = 92.2조
  - operating_cash_flow = 44.1조
  - **rcept_dt = 2024-03-12** ← PIT timestamp 정상 작동 ✓

**symbols_added**:
- kr_dart_client: download_corp_code, parse_corp_code_to_df, fetch_corp_to_ticker_map, fetch_disclosure_list, fetch_single_company_financials, fetch_multi_company_main_accounts, extract_key_accounts, build_corp_quarterly_panel, build_universe_quarterly_panel, pit_filter_panel, REPRT_CODES, REPRT_NAMES, KEY_ACCOUNTS, KOREAN_NAME_FALLBACK
- kr_features: prepare_pit_fundamentals_panel, add_pit_fundamentals, _compute_ttm_from_panel, _compute_yoy_from_panel, compute_value_score, compute_quality_score, compute_turnaround_score, compute_p1_score
- kr_config: dart_* config keys (5)
- tests/test_dart_pit.py (new file)

**symbols_changed**:
- KR_ENGINE_REUSE_VERSION: 2026-04-27-p0-bootstrap → 2026-04-28-p1-dart-fundamentals
- DEFAULT_CFG.phase1_fundamental_enabled: False → True
- kr_pipeline.build_scored_panel_v0: + fund_panel pre-build
- kr_pipeline.select_topn_per_month: score_col auto-detect
- kr_features.add_universe_features: + fund_panel kwarg

**config_fields_added**: dart_fund_start_year, dart_polite_sleep_s, dart_use_consolidated_first, dart_refresh_days_corp_code, dart_refresh_days_financials

**breaking_changes**: KR_ENGINE_REUSE_VERSION bumped → all `feature_store/scored_panel_v0_*` and `feature_store/fund_panel_*` caches will be regenerated on next run.

**Validation**:
- `py -3 tests/smoke_test.py` → 23/23 passed in 3.53s
- `py -3 tests/test_dart_pit.py` → 13/13 passed
- `py -3 kr_dart_client.py` → 실측 fetch 성공 (Samsung 2023 financials + rcept_dt)

**Next**:
1. 사용자 — `py -3 -m pip install -r requirements.txt` (pykrx, pyarrow, requests 등)
2. P0 vs P1 A/B 측정:
   ```bash
   # P0 only (momentum baseline)
   PHASE_PHASE1_FUNDAMENTAL_ENABLED=0 py -3 run_local.py --quick \
       --start-date 2019-01-01 --end-date 2024-12-31 --portfolio-size 30
   # P1 (momentum + fundamentals blend)
   PHASE_PHASE1_FUNDAMENTAL_ENABLED=1 py -3 run_local.py --quick \
       --start-date 2019-01-01 --end-date 2024-12-31 --portfolio-size 30
   py -3 run_local.py --verdict-only   # latest
   ```
3. Ship gate: P1 SHIP if ΔCAGR ≥ +1pp AND ΔSharpe ≥ +0.05 vs P0.
4. P1 SHIP 시 → P2 (한국시장 특수 알파: 외인/기관 흐름, 테마 phase, 공시 이벤트)

**Verdict**: P1 implementation SHIPPED. Baseline metrics 사용자 실행 후 측정.

---

### 10:20 KST — gdrive-migration + p2-dart-events-prep

**Scope**: 데이터 저장소를 H: 로컬 → G: GDrive로 분리 (2TB plan 확정). DART 이벤트 endpoints 11개 추가 (P2 한국 특수 알파 prep).

**Why**:
1. GDrive sync 활용 → 백업 자동 + Colab 호환 + r1000 패턴 일관성
2. 사용자 GDrive 2TB plan 확보 (display 15GB는 desktop sync 보임)
3. DART 키 도착 → P1 펀더멘털 빠르게 + P2 이벤트 시그널 prep도 같이

**What landed**:

**Storage migration**:
- `.env` — `KR_DATA_DIR=G:/내 드라이브/kr_quant_engine` 추가
- `.env.example` — 동일 template 추가
- `kr_config.py`:
  - `_resolve_data_root()` — env 또는 .env 파일에서 `KR_DATA_DIR` 읽어 `DATA_ROOT` 상수 정의
  - `DEFAULT_CACHE_DIRS` — 모든 path가 `DATA_ROOT` 기준 (이전 PROJECT_ROOT)
  - `PROJECT_ROOT` 그대로 (코드 path 용도)
- `kr_pykrx_client.py`, `kr_bok_client.py`, `kr_dart_client.py`, `kr_universe.py`, `kr_pipeline.py` — 모두 `PROJECT_ROOT` import → `DATA_ROOT` import로 교체
- `G:/내 드라이브/kr_quant_engine/` — 10개 서브디렉토리 생성 (cache_pykrx, cache_dart, cache_macro, cache_misc, feature_store, data_raw, outputs, outputs_advisor, models, backtest_results)
- 기존 `H:/codex/kr_quant_engine/cache_dart/` 데이터 이동 → `G:/내 드라이브/kr_quant_engine/cache_dart/` (CORPCODE.xml 28MB + corp_code.parquet + corp_code.zip + financials/00126380/)
- H:의 cache 디렉토리 10개 모두 삭제 (.gitignored, GDrive에서 자동 재생성)

**DART event endpoints (P2 prep) — `kr_dart_client.py` 확장 (~250 lines added)**:
- `DART_EVENT_CATALOG` — 11개 event type 등록: insider_holdings, major_holders, capital_increase, bonus_issue, treasury_buyback, treasury_sell, convertible_bond, warrant_bond, merger, spinoff, capital_reduction (각각 alpha_weight + Korean name + signal direction)
- `_fetch_dart_event(endpoint, corp_code, bgn_de, end_de)` — generic event fetch + 자동 캐싱 (cache_dart/events/{endpoint}/)
- 11개 specific fetch 함수: `fetch_insider_holdings`, `fetch_major_holders`, `fetch_capital_increase_decisions`, `fetch_bonus_issue_decisions`, `fetch_treasury_buyback_decisions`, `fetch_treasury_sell_decisions`, `fetch_convertible_bond_decisions`, `fetch_warrant_bond_decisions`, `fetch_merger_decisions`, `fetch_spinoff_decisions`, `fetch_capital_reduction_decisions`
- `fetch_all_events_for_corp(corp_code, bgn_de, end_de, event_types)` — bulk fetch + concat (long-format with event_category)
- `compute_event_score_for_corp(events, as_of, lookback_days)` — PIT-safe rolling alpha aggregation

**P2 design note**: `research/03_korea_specific_signals/p2_dart_events_taxonomy.md` (160 lines) — event 카탈로그, 가중치 근거, P2.0~P2.4 implementation plan, API 호출 추정 (~5,000 calls for ★★★ 5개 event types), 알려진 함정 5개

**Smoke test 갱신** (23 → 28 tests):
- `structural: DATA_ROOT separated from PROJECT_ROOT` (env 설정 시)
- `structural: kr_pykrx_client uses DATA_ROOT for cache`
- `structural: kr_dart_client uses DATA_ROOT for cache`
- `structural: DART_EVENT_CATALOG has 11 events` (treasury_buyback, capital_increase, bonus_issue, insider_holdings, major_holders, convertible_bond 등 검증)
- `import: kr_dart_client + event endpoints available` (P2 endpoints callable check)

**실측 검증** (실제 DART API 호출 후):
- corp_code XML: G: GDrive에서 로드 정상
- Samsung 2023 annual financials: 매출 258.9조 + rcept_dt 2024-03-12 (P1 그대로)
- **Samsung 2024 events**:
  - insider_holdings: 2,614건 (임원·주주 소유 변동 보고 — 삼성 임원이 많음)
  - major_holders: 40건 (5%+ 대량보유 변동)
  - treasury_buyback: 1건 (2024년 한국 사상 최대 10조 자사주 매입 발표 ✓)
  - compute_event_score (365d lookback) = +43.10

**알려진 design issue (P2 fine-tuning 시 해결)**:
- `insider_holdings` 단순 count × weight는 부적절 (대형주는 임원 많아 cumulative score 폭발). P2.2에서 net_buy_amt / mktcap 비율로 재설계.
- `major_holders` 동일 — net stkrt_irds (보유비율 변동) 기반 재설계.
- 카운트 cap (per-corp per-event-type per-quarter) 도입 검토.

**symbols_added**:
- kr_config: `_resolve_data_root`, `DATA_ROOT`
- kr_dart_client: `DART_EVENT_CATALOG`, `_fetch_dart_event`, `fetch_insider_holdings`, `fetch_major_holders`, `fetch_capital_increase_decisions`, `fetch_bonus_issue_decisions`, `fetch_treasury_buyback_decisions`, `fetch_treasury_sell_decisions`, `fetch_convertible_bond_decisions`, `fetch_warrant_bond_decisions`, `fetch_merger_decisions`, `fetch_spinoff_decisions`, `fetch_capital_reduction_decisions`, `fetch_all_events_for_corp`, `compute_event_score_for_corp`
- tests/smoke_test.py: 5개 새 test
- research/03_korea_specific_signals/p2_dart_events_taxonomy.md (new)

**symbols_changed**:
- kr_pykrx_client / kr_bok_client / kr_dart_client / kr_universe / kr_pipeline: PROJECT_ROOT → DATA_ROOT (cache path)
- DEFAULT_CACHE_DIRS: PROJECT_ROOT 기준 → DATA_ROOT 기준

**config_fields_added**: KR_DATA_DIR (env-driven, .env에서 설정)

**breaking_changes**: 기존 H:/cache_* 사용 코드는 깨짐. 데이터는 모두 G:로 이동. 사용자가 다른 머신에서 작업 시 .env에 KR_DATA_DIR 설정 필요 (없으면 PROJECT_ROOT fallback).

**Validation**:
- `py -3 tests/smoke_test.py` → **28/28 passed** in 1.86s
- `py -3 kr_dart_client.py` → corp_code 캐시 G:에서 로드 + Samsung 이벤트 2,655건 fetch ✓
- DATA_ROOT 분리 확인: PROJECT_ROOT=`H:/codex/kr_quant_engine`, DATA_ROOT=`G:/내 드라이브/kr_quant_engine`

**Next**:
1. 사용자 — `py -3 -m pip install -r requirements.txt` (아직 안 했으면)
2. P0 vs P1 A/B 측정 (이전 SESSION_HANDOFF의 명령 그대로, 데이터는 자동으로 G:에 쌓임)
3. P1 SHIP 시 → P2 events 시그널 정밀화 (insider count 문제 해결 후 alpha 측정)

**Verdict**: Migration + P2 events prep SHIPPED. 데이터 백업 자동, Colab 호환 준비 완료, P2 prep 완료.

---

### 12:55 KST — p_mb-v0-multibagger-episode-discovery

**Scope**: r1000 phase11 패턴 ported — KOSPI/KOSDAQ에서 24개월 +300%+ multibagger episode를 retrospective 발견하는 시스템 V0.

**Why**: 한국시장은 KOSDAQ small/mid에서 multibagger 빈도 높음 (에코프로비엠 +2,000%, 에코프로 +29,000%, 알테오젠 +2,800% 등). 이 episode들의 pre-surge 시그널을 학습하면 강한 entry 알파 가능. P_MB는 P0~P5 옆 별도 layer.

**Sizing decisions** (사용자 결정 2026-04-28):
- `multibagger_return_threshold` = **3.0** (300%, 4x peak/entry)
- `multibagger_window_months` = **24**
- `multibagger_min_mcap_krw` = **5e11 (5,000억)** — mid+ cap만 (KOSDAQ small noise 회피)

**What landed**:
- `kr_config.py` — 8 multibagger config keys 추가 (DEFAULT_CFG)
- `kr_multibagger.py` (470 lines):
  - `find_episodes_for_ticker()` — single ticker rolling forward max-gain → episodes (greedy collapse overlapping)
  - `define_multibagger_episodes()` — panel-level episode discovery
  - `identify_surge_start()` — first +20% breakout from entry
  - `add_surge_start_dates()` — accumulation_months 컬럼 추가
  - `fetch_mcap_at_dates()` — entry 시점 mcap 조회 (pykrx daily snapshot 활용)
  - `quality_filter_episodes()` — mcap >= 5,000억 필터
  - `build_episode_panel()` — full pipeline orchestrator
  - `load_or_build_episode_panel()` — feature_store 캐시 + 빌드
  - `build_price_panel()` — universe ticker × month_end close 패널
  - `episode_summary_stats()` — 분포 분석 (return P25/P50/P75/P95, mcap, by year)
- `tests/test_multibagger.py` (310 lines, 17 tests):
  - Mock data factory: `make_random_walk_series`, `plant_multibagger`
  - Logic tests: 5x 24m 발견, 2x 거부, 30m surge cropping, NaN 처리, 0/음수 가격, overlapping collapse, false positive < 5/20 random walks
  - Surge_start identification + missing entry handling
  - Panel input + summary stats + empty input edge cases
- `tools/multibagger_explorer.py` — post-fetch analysis script (return distribution, top 30, cohort 분석, mcap band)
- `research/06_walkforward_baselines/multibagger_v0_design.md` — design doc + parameter justification + pre-signal 카탈로그 + 사용자 가이드
- `tests/smoke_test.py` — 3 multibagger structural/import tests 추가 (28 → 31 tests)

**Bugs found + fixed during implementation**:
- `find_episodes_for_ticker`: months_to_peak을 days/30.4375 (float, boundary issue) → idx 차이 (정수 month) 정확 계산
- `episode_summary_stats`: `episodes.get("quality_pass", True)` returned literal True (not Series) — 컬럼 존재 체크 후 분기로 수정
- `tests/test_multibagger.py::plant_multibagger`: `s[peak_idx]` 미설정 (off-by-one in surge_len) — `surge_len = peak_idx - surge_start_idx + 1` 로 수정

**Validation**:
- `py -3 tests/test_multibagger.py` → **17/17 passed**
- `py -3 tests/smoke_test.py` → **31/31 passed** in 1.25s
- `py -3 tests/test_dart_pit.py` → **13/13 passed**
- Mock 검증으로 logic 완전 검증; 실측은 사용자 pykrx install 후 수행

**symbols_added**:
- kr_config: 8 multibagger DEFAULT_CFG keys (multibagger_return_threshold, multibagger_window_months, multibagger_min_mcap_krw, multibagger_min_trading_value_krw, multibagger_min_listed_months, multibagger_pre_surge_lookback_months, multibagger_post_surge_include_months, multibagger_surge_breakout_pct)
- kr_multibagger: DEFAULT_THRESHOLD, DEFAULT_WINDOW_MONTHS, DEFAULT_MIN_MCAP_KRW, DEFAULT_MIN_TRADING_VALUE_KRW, DEFAULT_MIN_LISTED_MONTHS, DEFAULT_PRE_SURGE_LOOKBACK, DEFAULT_POST_SURGE_INCLUDE, DEFAULT_SURGE_BREAKOUT_PCT, build_price_panel, find_episodes_for_ticker, define_multibagger_episodes, identify_surge_start, add_surge_start_dates, fetch_mcap_at_dates, quality_filter_episodes, build_episode_panel, load_or_build_episode_panel, episode_summary_stats
- tests/test_multibagger.py (new file)
- tools/multibagger_explorer.py (new file)
- research/06_walkforward_baselines/multibagger_v0_design.md (new file)

**symbols_changed**: none
**config_fields_added**: see above (8 keys)
**breaking_changes**: none

**Next**:
1. 사용자 — `py -3 -m pip install pykrx`
2. 사용자 — `py -3 -c "from kr_multibagger import load_or_build_episode_panel; load_or_build_episode_panel()"` (~30-60min, 첫 build)
3. 사용자 — `py -3 tools/multibagger_explorer.py` (즉시, 분포 + Top 30 출력)
4. P_MB.2 — pre-surge feature panel + CatBoost binary classifier (P1/P2 데이터 필요)
5. P_MB.3 — multibagger sleeve integration + A/B (ΔCAGR ≥ +1pp ship gate)

**Verdict**: V0 episode discovery + retrospective infra SHIPPED (mock-verified). 사용자 실측 후 V1 (classifier) 진입.

---

### 14:30 KST — c-p2-events-v2-redesign + d3-technical-indicators

**Scope**: 사용자 요청에 따라 순차 진행.
- C: P2 DART 이벤트 시그널 정밀화 (count → KRW-amount-based scoring 재설계)
- D-3: 기술지표 P3 모듈 (52w high, MA stack, ATR, RSI, Bollinger, Stage, Minervini Trend Template)

**C — DART_EVENT_CATALOG v2 재설계**:

v1 (count-based)는 Samsung 2024년 인사이더 보고 2,614 row × 0.30 weight = +784로 폭발 (100x scale 오류). v2는 KRW 경제적 규모 / mcap 기준 정상화.

- `DART_EVENT_CATALOG` v1 tuple `(endpoint, direction, weight, name)` → v2 dict 형식 (endpoint, direction, alpha_weight, scoring_mode, amount_field, full_weight_pct, kr_name)
- 5개 scoring modes:
  - `amount_pct_mcap`: sum(amount) / mcap, capped at full_pct (treasury_buyback, treasury_sell, CB, BW)
  - `computed_dilution`: nstk_ostk_qy × bdis_pric / mcap (capital_increase)
  - `binary`: weight if any event present (bonus_issue, merger, spinoff, capital_reduction)
  - `insider_net_buy`: signed (delta_qty × trade_uv) / mcap
  - `stkrt_change`: signed sum of stkrt_irds (already %)
- Score helpers (`_score_amount_pct_mcap`, `_score_computed_dilution`, `_score_binary`, `_score_insider_net_buy`, `_score_stkrt_change`, `_signed_cap`)
- `compute_event_score_for_corp()` 시그니처 변경: `mcap` parameter 필수, return type `dict` (per-category + total_score)
- Numeric coercion suffix list 확장: `_prc`, `_pric`, `_fta`, `_irds`, `_stkrt`, `_ostk`, `_estk` 추가 (Samsung treasury_buyback amount 콤마 string 파싱 실패 버그 fix)
- `kr_features` P2 통합: `prepare_event_panel`, `add_disclosure_event_signal` (PIT join + mcap 정규화), `compute_p2_score` (P0+P1+P2 blended)
- `kr_config`:
  - `PHASE2_DART_EVENT_COLUMNS` (12): disclosure_event_total_score + 11 event-specific scores
  - `PHASE2_FLOW_COLUMNS` (4) — P2.5
  - `PHASE2_THEME_SAFETY_COLUMNS` (6) — P2.6/P2.7
  - `PHASE2_KOREA_ALPHA_COLUMNS` aggregated (22 total)
- Phase toggle: `PHASE_PHASE2_DART_EVENTS_ENABLED`

**Live verification (Samsung 2024)**:
- 이전 v1: total_score = +43.10 (count 폭발)
- 새 v2: total_score = **+0.117** (정상)
  - treasury_buyback = +0.112 (2.68조 매입 / 600조 mcap = 0.45% × 0.50 weight)
  - major_holders = +0.005 (소소한 holdings 변동)
  - insider_holdings = 0 (net buy/sell ≈ 0)

**D-3 — 기술지표 (P3.1)**:

`kr_technicals.py` (340 lines) — pure-numeric 모듈:
- 10개 카테고리 함수: moving averages, 52w extremes, volume stats, RSI, ATR, Bollinger, vol contraction, Weinstein 4-stage classifier, Minervini 8-condition trend template, breakout flag
- 31 indicator outputs (registered as `PHASE3_TECHNICAL_COLUMNS`)
- `compute_all_technicals(prices, benchmark_prices)` orchestrator
- `kr_features.add_technical_indicators` integration with PHASE_PHASE3_TECHNICAL_ENABLED toggle + null-fill on disable

**Tests (총 102/102 통과)**:
- `tests/test_p2_events.py` (18 tests): KRW scoring, mcap normalization, PIT filter, lookback window, **insider count noise immunity** (Samsung 2,614 row 회귀 방지), full integration
- `tests/test_technicals.py` (17 tests): MA SMA, MA stack, 52w extremes, RSI bounds, ATR, Bollinger position, volume stats, stage classifier (uptrend / downtrend), trend template (strong → ≥5, decline → ≤3), full integration, edge cases (empty / short history)
- `tests/smoke_test.py` (33 → 37): DART_EVENT_CATALOG v2 dict format 검증, PHASE2 column split 검증, kr_technicals + features integration 검증
- 기존: dart_pit 13/13, multibagger 17/17 — 모두 유지

**Deferred (next session, incremental)**:
- D-1: DART fnlttSinglAcntAll 전체 IS/BS/CF parsing (현재 6개 핵심 계정만 — 매출원가, 판관비, 매출채권, 재고, 차입금 등 추가)
- D-2: FCF (영업CF − CAPEX), ROIC, Sloan accruals 계산
- D-4: TTM rolling 4Q sum 정밀화 (현재 annual factor envelope 단순화)
- 이유: alpha 직결도가 P_MB classifier / P3 regime detector보다 낮음. 데이터 확장은 후속.

**symbols_added**:
- kr_dart_client (v2): `_signed_cap`, `_score_amount_pct_mcap`, `_score_computed_dilution`, `_score_binary`, `_score_insider_net_buy`, `_score_stkrt_change`. DART_EVENT_CATALOG dict v2 format.
- kr_features (P2): `prepare_event_panel`, `add_disclosure_event_signal`, `compute_p2_score`
- kr_features (P3): `add_technical_indicators`
- kr_technicals (new module): `compute_moving_averages`, `is_ma_stack_aligned`, `compute_52w_extremes`, `compute_volume_stats`, `compute_rsi`, `compute_atr`, `compute_bollinger`, `compute_volatility_contraction`, `classify_stage`, `compute_trend_template_score`, `compute_all_technicals`
- kr_config: `PHASE2_DART_EVENT_COLUMNS` (12), `PHASE2_FLOW_COLUMNS` (4), `PHASE2_THEME_SAFETY_COLUMNS` (6), `PHASE3_TECHNICAL_COLUMNS` (31)
- tests/test_p2_events.py (new), tests/test_technicals.py (new)

**symbols_changed**:
- kr_dart_client.DART_EVENT_CATALOG: tuple → dict format (BREAKING for consumers iterating tuple unpacking)
- kr_dart_client.compute_event_score_for_corp: signature changed (`mcap` required, returns dict not float)
- kr_dart_client._fetch_dart_event: numeric coercion suffix list expanded
- kr_features.add_universe_features: new params `event_panel`, `event_lookback_days`
- kr_config.PHASE2_KOREA_ALPHA_COLUMNS: composition reorganized (3 sub-groups)

**config_fields_added**: PHASE_PHASE2_DART_EVENTS_ENABLED, PHASE_PHASE3_TECHNICAL_ENABLED (env vars, runtime toggles)

**breaking_changes**:
- `compute_event_score_for_corp` return type float → dict; callers must use `result["total_score"]`
- `DART_EVENT_CATALOG[key]` access pattern: `(endpoint, dir, weight, name)` tuple unpacking → `meta["endpoint"]` / `meta["alpha_weight"]` etc.

**Validation**:
- All test suites: smoke 37/37, dart_pit 13/13, multibagger 17/17, p2_events 18/18, technicals 17/17 = **102/102 통과**
- Live DART API: Samsung 2024 score recomputed +0.117 (was +43.10) ✓

**Verdict**: C + D-3 SHIPPED. Test coverage 81 → 102 (+21 tests). 다음 세션에서 P_MB.2 (multibagger classifier with technicals + events as features) 진입 가능.
