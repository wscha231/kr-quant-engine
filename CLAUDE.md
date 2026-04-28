# kr_quant_engine — Project Guide

## Project Overview
KOSPI + KOSDAQ 통합 유니버스 기반 한국 주식 퀀트 엔진. `r1000-quant-engine` 설계 패턴 ported. **CAGR 최대화 + KOSPI200 초과수익**이 목표. 전체 backtest window는 2016-01-01 ~ 현재.

## Reference Clone (Read-only)
- 원본 r1000-quant-engine 로컬 클론: `H:/codex/tmp_r1000_quant_engine/`
- **이 디렉토리의 파일은 절대 수정하지 말 것** — 설계 참조용
- 핵심 참조 파일:
  - `MASTER_PLAN.md` (현행 우선순위)
  - `PHASE_ROADMAP.md` (phase toggle / keep_cols / env-var 규약)
  - `r1000_pipeline.py` (orchestration 패턴)
  - `r1000_data_collector.py` (cfg presets)
  - `r1000_unified_universe.py` (universe merge 패턴 — KOSPI+KOSDAQ 통합 참고)

## Key Files
- `SESSION_HANDOFF.md` — **다음 세션에서 가장 먼저 읽을 파일.** "방금 뭐 했고 다음에 뭐 할지" single inbox.
- `MASTER_PLAN.md` — phase별 우선순위
- `CHANGELOG.md` — Agent Update Contract 따른 chronological 로그
- `kr_pipeline.py` — orchestration 모듈
- `kr_data_collector.py` — pykrx + DART + BOK 통합 수집
- `colab_run.ipynb` — Colab 런북 (P0 후 추가 예정)

## Current Engine Version
- `KR_ENGINE_REUSE_VERSION = "2026-04-27-p0-bootstrap"` (in `kr_config.py`)
- 시그널 공식 변경 시 bump → cache_*, feature_store, models 자동 무효화

## Environments
- **Code (PROJECT_ROOT)**: `H:/codex/kr_quant_engine/` — .py, .md, .yaml, tests/ (GitHub repo 후보)
- **Data (DATA_ROOT)**: `G:/내 드라이브/kr_quant_engine/` — cache_*, feature_store, outputs, models (GDrive 2TB, Colab share)
- **Override**: `.env`의 `KR_DATA_DIR` env var. 미설정 시 PROJECT_ROOT fallback (greenfield용).
- **Colab**: `/content/drive/MyDrive/kr_quant_engine/` (mount 후 GDrive와 동일 경로)
- **Python**: `py -3` (Windows)

## API Keys (.env에 저장, gitignored)
- **BOK_ECOS_API_KEY**: 발급 완료 ✅
- **DART_API_KEY**: 신청 중 (~2일, 2026-04-27 신청)
- **KOSIS_API_KEY**: 미신청 (P3 매크로 보강 시)

## Pipeline Execution Order
1. `kr_data_collector.run_data_collection(cfg)` — pykrx + DART + BOK 일별 갱신
2. `kr_pipeline.build_universe_monthly(cfg)` — KOSPI+KOSDAQ 멤버십 + 필터 + 시그널
3. `kr_pipeline.train_walkforward(cfg)` — 126일 embargo CatBoost ensemble [P4]
4. `kr_pipeline.backtest_portfolio(cfg)` — Top-N + cost 22bp + price limit 처리
5. `kr_pipeline.run_full_validation_suite(cfg)` — PIT 검증, NaN cliff 체크

## Config Presets (P0 시점, 추후 확장)
- `collector_full_run_cfg()` — 전체 신규 실행 (모든 캐시 무시)
- `collector_lean_full_run_cfg()` — comparison 생략 lean 실행
- `pipeline_quick_rescore_cfg()` — feature_store + 모델 재사용 빠른 실행

## Fast-Iteration Workflow

### Pre-commit smoke test (<10s)
```bash
py -3 tests/smoke_test.py            # 전체 syntax + structural + import 체크
py -3 tests/smoke_test.py --quick    # ~1s syntax + structural only
```

### Local pipeline run
```bash
py -3 run_local.py --quick           # ~5-15min — feature_store 재사용
py -3 run_local.py --full            # ~1-2h — feature_store 전체 재빌드
py -3 run_local.py --verdict-only    # ~2s — 마지막 결과 verdict
py -3 run_local.py --no-collector    # collector 단계 건너뜀
```

### Phase toggle 매커니즘 (r1000 차용)
- `kr_helpers.phase_is_enabled()` 헬퍼가 `PHASE_<KEY>_ENABLED` 환경변수 읽음
- phase disable 시 관련 컬럼은 **삭제가 아니라 0.0으로 채움** (downstream KeyError 방지)
- 새 phase 추가 시 `PHASE<N>_<NAME>_COLUMNS` 상수 필수 + `keep_cols` whitelist에 등록

### 새 phase 추가 시 feature_store 생존 규칙
`build_feature_store.keep_cols` 는 명시적 whitelist다. universe builder에서 붙인 컬럼이라도 whitelist에 없으면 drop 된다 (r1000 phase2-keepcols 회귀 사례). 새 phase:
- `PHASE<N>_<NAME>_COLUMNS` 상수 만들기 (`kr_config.py`)
- `build_feature_store.keep_cols`에 `+ PHASE<N>_<NAME>_COLUMNS` 추가
- 숫자 컬럼이면 `hard_sanitize` 호출 리스트에도 추가
- phase toggle disabled 분기의 zero-placeholder 동기화

### A/B 측정 레시피
1. `PHASE_<KEY>_ENABLED=1`로 QUICK run → metrics 기록
2. `PHASE_<KEY>_ENABLED=0`로 QUICK run → metrics 기록
3. `outputs/concentrated_backtest_metrics.json`의 `strategy_cagr` / `sharpe` / `max_dd` diff
4. **Ship gate**: ΔCAGR ≥ +0.5pp AND ΔSharpe ≥ -0.05 AND ΔMaxDD ≥ -3pp

## Architecture
- Walk-forward training: 126일 embargo (look-ahead 방지)
- Ensemble: Ridge + LogisticRegression + CatBoost (reg/cls/rank) [P4]
- Point-in-time fundamentals: DART `rcept_dt` (공시일) 기준
- Dual-sleeve portfolio: Core (70-95%) + Speculative (5-15%) + Cash (0-55%)
- Cost model: 매도 0.18% 거래세 + 0.015% 수수료 양방향 + 5bp slippage 양방향 ≈ **왕복 31bp** (mid-cap baseline)
- Price limit: ±30% 적중 시 부분 체결 + 다음 영업일 시초가 매수 처리

## Korean Market Specifics
- **거래 시간**: 09:00–15:30 KST (단일가 매매: 08:30–09:00, 15:20–15:30)
- **결제**: T+2
- **가격제한폭**: ±30% (KOSPI/KOSDAQ 동일, 정리매매 종목은 다름)
- **거래세**: 매도 시 0.18% (KOSPI/KOSDAQ 동일, 2026 기준 — 정부 정책 변동 가능)
- **공시 데드라인**: 분기보고서 45일 / 반기보고서 45일 / 사업보고서 90일
- **회계기준**: K-IFRS (한국채택국제회계기준), 연결재무제표 우선

## Result Analysis
백테스트 결과에서 확인할 핵심 지표:
- `strategy_cagr` — 연복리 수익률
- `excess_cagr` > 0 → KOSPI200 초과수익
- `sharpe` > 1.0 → 위험조정 통과
- `ir` (Information Ratio) > 0.5 → 통계적 유의미
- `max_dd` — 최대 낙폭
- `beat_month_ratio` — 월간 승률
- `turnover` — 회전율 (한국시장 22bp cost 감안 25-35% 권장)
- `acceptance_checks` — 전체 통과 여부

## Changelog Writing Rules
- CHANGELOG entries는 영문 + 한글 혼용 가능 (r1000은 영문 only지만 한국 프로젝트는 한글 OK)
- 모든 entry는 `HH:MM KST` 타임스탬프 포함
- `symbols_added`, `symbols_changed`, `config_fields_added`, `breaking_changes` 명시
- 함수/클래스 이름 explicit하게 적기 (서술형 X)

## Current Production Baseline
- **P0 시작 전** — baseline 미측정. P0 완료 시 첫 baseline 기록.

## Ship Gate (any next change)
- ΔCAGR ≥ +0.5pp AND ΔSharpe ≥ -0.05 AND ΔMaxDD ≥ -3pp
- + sleeve sanity (P4 이후): early sleeve count ≥ 4

## Session-continuation checklist
새 세션에서 이어 작업할 때 순서:
1. `SESSION_HANDOFF.md` 먼저 읽기 (단일 inbox)
2. 이 파일 (`CLAUDE.md`) 읽기
3. `CHANGELOG.md` 마지막 ~10 entries 확인
4. `MASTER_PLAN.md`로 phase 우선순위 확인
5. `git log --oneline -5` 또는 마지막 commit 확인
6. `outputs/concentrated_backtest_metrics.json` 최신 baseline 확인
7. `tests/smoke_test.py --quick` 통과 확인
8. **Phase 끝나면 `SESSION_HANDOFF.md` 덮어쓰기 — 누적하지 말 것**
