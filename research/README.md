# Research Index

모든 실험/분석/리서치 산출물은 이 디렉토리에 번호별로 정리한다. 각 서브폴더 README가 그 분야의 단일 진실 (single source of truth).

## 폴더 구조

| # | 폴더 | 내용 | 우선 |
|---|---|---|---|
| 00 | `00_data_sources_audit/` | pykrx / DART / BOK / KOSIS 커버리지 + 신뢰도 audit | P0 |
| 01 | `01_universe_construction/` | KOSPI + KOSDAQ 통합 멤버십 결정 + 필터 튜닝 | P0 |
| 02 | `02_factor_zoo/` | 팩터 IC / IR 리서치 (value, quality, momentum, low-vol, size) | P1+ |
| 03 | `03_korea_specific_signals/` | 외인흐름, 기관흐름, 테마 phase, 단기과열, 공시이벤트 | P2 |
| 04 | `04_regime_detection/` | VKOSPI, USD/KRW, 외인 누적 net flow, regime taxonomy | P3 |
| 05 | `05_cost_friction_audit/` | 거래세 0.18% + 가격제한 + 슬리피지 cost 모델링 | P0 |
| 06 | `06_walkforward_baselines/` | phase별 walk-forward 백테스트 결과 + verdict | 모든 phase |
| 07 | `07_phase_experiments/` | A/B 측정 raw 데이터 + diff | 모든 phase |
| 99 | `99_archive/` | 폐기된 가설 / deprecated 실험 | – |

## 작성 규칙

1. 각 실험은 `{phase}_{date}_{topic}.md` 형식
   - 예: `p0_2026-04-29_universe_filter_threshold_sweep.md`
2. 결과 CSV / JSON은 같은 prefix
   - 예: `p0_2026-04-29_universe_filter_threshold_sweep.csv`
3. 각 .md 파일 상단에 다음 헤더 필수:
   ```yaml
   ---
   phase: P0
   date: 2026-04-29
   author: Claude (or 사람 이름)
   verdict: SHIP / REJECT / PARTIAL / DEFER
   ship_gate_passed: true / false
   ---
   ```
4. **결론 위에 적기** — TL;DR을 첫 단락에 둔다 (CLAUDE.md / SESSION_HANDOFF.md가 빨리 읽도록)

## 현재 상태 (2026-04-27)

P0 시작 직전. 아직 어떤 실험도 완료되지 않음. 첫 산출물 예정:
- `00_data_sources_audit/p0_pykrx_coverage.md` — pykrx로 가져올 수 있는 컬럼 + 신뢰도
- `01_universe_construction/p0_kospi_kosdaq_unified_v0.md` — 첫 통합 universe count
- `06_walkforward_baselines/p0_baseline.md` — 첫 momentum 백테스트
