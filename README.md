# kr_quant_engine

KOSPI + KOSDAQ 통합 유니버스 기반 한국 주식 퀀트 엔진. `r1000-quant-engine`의 phase-gated dual-sleeve 설계를 한국시장에 이식하여 CAGR 최대화를 목표로 한다.

**Status**: P0 bootstrap (2026-04-27 시작)

## Quick Start

```bash
# 1. Install dependencies
py -3 -m pip install -r requirements.txt

# 2. Set up API keys
cp .env.example .env
# Edit .env and fill in BOK_ECOS_API_KEY (and DART_API_KEY when available)

# 3. Smoke test
py -3 tests/smoke_test.py

# 4. Run quick backtest (P0)
py -3 run_local.py --quick

# 5. View latest verdict
py -3 run_local.py --verdict-only
```

## Architecture

5-module split (r1000 refactor 교훈 day 1부터 적용):

```
kr_config.py    — pure data: PHASE_*_COLUMNS, DEFAULT_CFG, MACRO constants
kr_helpers.py   — pure utilities: cross_sectional_robust_z, percentile_rank
kr_features.py  — feature engineering: build_*, add_* signal builders
kr_signals.py   — sleeve composition + portfolio construction
kr_pipeline.py  — orchestration: build_universe → train → backtest → export
```

Data clients live separately:
```
kr_pykrx_client.py    — pykrx wrapper + caching
kr_bok_client.py      — BOK ECOS macro fetch
kr_dart_client.py     — DART OpenAPI (PIT timestamp 보존) [P1]
kr_universe.py        — KOSPI + KOSDAQ 통합 universe
kr_macro.py           — VKOSPI, USD/KRW, 외국인 흐름 등 한국 매크로 layer
```

## Key invariants

1. **Point-in-time fundamentals**: DART 공시일(`rcept_dt`) 기준 — 미래 데이터 절대 금지
2. **Schema stability**: phase disable 시 컬럼은 0.0으로 채움, 삭제 금지 (downstream KeyError 방지)
3. **Cost realism**: 매도 0.18% 거래세 + 0.015% 수수료 양방향 + 5bp slippage 양방향 ≈ **왕복 31bp** (mid-cap)
4. **Price limit**: ±30% 가격제한폭 적중 시 백테스트 fill 제한 처리
5. **Cache invalidation**: 시그널 공식 변경 시 `KR_ENGINE_REUSE_VERSION` bump

## Phase roadmap

- **P0** (1주, 진행중): Universe + 단순 momentum + cost-aware backtest baseline
- **P1** (2주): DART PIT 펀더멘털 + value/turnaround/quality 시그널
- **P2** (3주): 한국시장 특수 알파 — 외인/기관 흐름, 테마 phase, 단기과열 회피
- **P3** (2주): Regime detection (VKOSPI + USD/KRW + 외인 누적 net flow) + 방어 시스템
- **P4** (3주): ML walk-forward (CatBoost ensemble) + dual-sleeve 포트폴리오
- **P5** (2주): Concentrated champion (N=5/10/15) + paper trading

자세한 내용은 `MASTER_PLAN.md` 참고.

## Reference

- 설계 원본: [r1000-quant-engine](https://github.com/wscha231/r1000-quant-engine)
- 로컬 reference clone: `H:/codex/tmp_r1000_quant_engine/` (read-only)
- 메인 데이터 소스: pykrx (KRX), DART OpenAPI, BOK ECOS

## Project files

- `CLAUDE.md` — 프로젝트 기본 가이드 (Claude/Codex 세션용)
- `MASTER_PLAN.md` — 현행 우선순위 + 다음 액션
- `SESSION_HANDOFF.md` — 다음 작업 single inbox (세션 간 인계)
- `CHANGELOG.md` — chronological 결정 로그
- `research/` — 모든 실험/분석 산출물
