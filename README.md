# kr_quant_engine

KOSPI + KOSDAQ 통합 유니버스 기반 한국 주식 퀀트 엔진. `r1000-quant-engine`의 phase-gated dual-sleeve 설계를 한국시장에 이식하여 CAGR 최대화를 목표로 한다.

**Status**: P3.3 + multibagger classifier shipped (engine version `2026-04-28-p3-pipeline-wired`). Pipeline 6-panel wiring + Issues A/B/C/D/F/G 완료. P_MB.2 학습 가능.

## Quick Start (Local — Windows)

```powershell
# 1. Install dependencies
py -3 -m pip install -r requirements.txt

# 2. Set up API keys
cp .env.example .env
# Edit .env: BOK_ECOS_API_KEY (필수), DART_API_KEY (P1+ 필요)

# 3. Smoke test (~10s)
py -3 tests/smoke_test.py

# 4. Quick backtest with all phases ON
$env:PHASE_PHASE0_MOMENTUM_ENABLED="1"
$env:PHASE_PHASE1_FUNDAMENTAL_ENABLED="1"
$env:PHASE_PHASE2_DART_EVENTS_ENABLED="1"
$env:PHASE_PHASE2_FLOW_ENABLED="1"
$env:PHASE_PHASE2_DERIVATIVES_ENABLED="1"
$env:PHASE_PHASE3_TECHNICAL_ENABLED="1"
$env:PHASE_PHASE3_MACRO_ENABLED="1"
$env:PHASE_PHASE3_REGIME_ENABLED="1"
py -3 run_local.py --quick --start-date 2019-01-01 --end-date 2024-12-31

# 5. View latest verdict
py -3 run_local.py --verdict-only
```

## Run on GitHub

### (a) GitHub Actions CI — 자동, 모든 push/PR 마다 실행
- Workflow: [`.github/workflows/smoke.yml`](.github/workflows/smoke.yml)
- 실행 내용: syntax + structural smoke (31 tests) + multibagger label-deconfliction (7 tests)
- 시간: ~1-2분
- API 키 불필요
- 결과 확인: 저장소 **Actions** 탭

수동 트리거: Actions → smoke → **Run workflow** (workflow_dispatch).

### (b) GitHub Codespaces — 클라우드 dev 환경에서 baseline 실행
- `.devcontainer/devcontainer.json`이 Python 3.11 + `requirements.txt` 자동 설치
- 시작:
  1. 저장소 main 페이지 → **Code** → **Codespaces** → **Create codespace on `<branch>`**
  2. Container 빌드 완료 (~2-3분) 후 터미널 열림
  3. API 키 등록:
     ```bash
     cp .env.example .env
     # nano .env → 키 붙여넣기
     ```
     또는 **Settings → Codespaces → Secrets**에 `BOK_ECOS_API_KEY`, `DART_API_KEY` 등록 (자동 주입)
  4. 실행:
     ```bash
     export PHASE_PHASE0_MOMENTUM_ENABLED=1 PHASE_PHASE1_FUNDAMENTAL_ENABLED=1
     export PHASE_PHASE2_DART_EVENTS_ENABLED=1 PHASE_PHASE2_FLOW_ENABLED=1
     export PHASE_PHASE2_DERIVATIVES_ENABLED=1 PHASE_PHASE3_TECHNICAL_ENABLED=1
     export PHASE_PHASE3_MACRO_ENABLED=1 PHASE_PHASE3_REGIME_ENABLED=1
     python run_local.py --quick --start-date 2019-01-01 --end-date 2024-12-31
     ```
- 첫 실행 시 DART corp_code + macro fetch ~5,000 calls (50분-1시간). 이후 캐시 재사용 5-10분.
- Codespaces 무료 티어: 월 60시간. 무거운 백테스트는 로컬 권장.

### (c) Pull Request — 자동 검증 + 머지 전 게이트
- PR 생성 시 smoke CI 자동 실행
- 녹색 ✓ 확인 후 머지

## Architecture

```
kr_config.py           — pure data: PHASE_*_COLUMNS, DEFAULT_CFG, MACRO constants
kr_helpers.py          — pure utilities: cross_sectional_robust_z, percentile_rank, phase_is_enabled
kr_features.py         — feature engineering: P0~P3 add_*_signal builders + composites
kr_pipeline.py         — orchestration: build_universe → features → score → backtest → verdict
run_local.py           — CLI entry: --quick / --full / --verdict-only / --no-collector
```

데이터 클라이언트:
```
kr_pykrx_client.py     — pykrx + FDR fallback, per-day parquet caching
kr_dart_client.py      — DART OpenAPI: 11 events + financials, PIT-safe (rcept_dt)
kr_bok_client.py       — BOK ECOS macro fetch
```

시그널 layers:
```
kr_universe.py         — KOSPI+KOSDAQ universe + listing date 해석 + 필터
kr_flow.py             — P2.5 외인/기관/개인 flow signals (17 cols)
kr_derivatives.py      — P2.6 VKOSPI + foreign futures OI (7 cols)
kr_technicals.py       — P3.1 31 indicators (MA stack, 52w, RSI, ATR, BB, stage, trend template)
kr_macro.py            — P3.2 BOK + FRED + yfinance macro panel (23 cols, ffill capped at 45d)
kr_regime.py           — P3.3 8-state regime classifier + sleeve multipliers
kr_multibagger.py      — episode discovery (≥300% return, ≤24m window)
kr_multibagger_classifier.py  — P_MB.2 CatBoost binary entry classifier (label dedup'd)
```

## Key invariants

1. **Point-in-time fundamentals**: DART 공시일(`rcept_dt`) 기준. `pit_filter_panel(strict=True)` 가 `rcept_dt < period_end` 인 row drop + WARN.
2. **Schema stability**: phase disable 시 컬럼은 0.0 또는 NaN 으로 채움, 삭제 금지 (downstream KeyError 방지).
3. **Cost realism**: 매도 0.18% 거래세 + 0.015% 수수료 양방향 + 5bp slippage 양방향 ≈ **왕복 31bp** (mid-cap baseline).
4. **Price limit**: ±30% 가격제한폭 적중 시 백테스트 fill 제한 처리.
5. **Cache invalidation**: 시그널 공식 변경 시 `KR_ENGINE_REUSE_VERSION` bump → cache_*, feature_store, models 재생성.
6. **Loud failures**: pykrx 미설치 시 ImportError → silent NaN 이 아닌 RuntimeError raise.
7. **Backtest speed**: month-end OHLCV market snapshot 한 번에 prefetch → 1k-ticker × 60 month 백테스트가 ~60 calls (이전 60,000+).

## Phase roadmap

- ~~**P0**~~ DONE: Universe + 단순 momentum + cost-aware backtest
- ~~**P1**~~ DONE: DART PIT 펀더멘털 + value/turnaround/quality
- ~~**P2**~~ DONE: 외인/기관 flow + DART events + 파생
- ~~**P3.1**~~ DONE: 31 technical indicators
- ~~**P3.2**~~ DONE: macro layer (BOK + FRED + yfinance)
- ~~**P3.3**~~ DONE: 8-state regime classifier + sleeve multipliers
- ~~**P_MB.1**~~ DONE: episode discovery
- ~~**P_MB.2**~~ READY: multibagger classifier (label dedup 완료, 학습 가능)
- **P4**: ML walk-forward (CatBoost ensemble) + dual-sleeve 포트폴리오
- **P5**: Concentrated champion (N=5/10/15) + paper trading

자세한 내용은 `MASTER_PLAN.md` 참고.

## Reference

- 설계 원본: [r1000-quant-engine](https://github.com/wscha231/r1000-quant-engine)
- 메인 데이터 소스: pykrx (KRX), DART OpenAPI, BOK ECOS, FRED, yfinance
- 회계기준: K-IFRS, 연결재무제표 우선
- 거래세: 매도 0.18% (KOSPI/KOSDAQ 동일, 2026 기준)

## Project files

- `CLAUDE.md` — 프로젝트 기본 가이드 (Claude/Codex 세션용)
- `MASTER_PLAN.md` — 현행 우선순위 + 다음 액션
- `SESSION_HANDOFF.md` — 다음 작업 single inbox (세션 간 인계)
- `CHANGELOG.md` — chronological 결정 로그
- `research/` — 모든 실험/분석 산출물
