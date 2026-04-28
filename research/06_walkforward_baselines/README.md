# 06 — Walk-forward Baselines

Phase별 walk-forward 백테스트 결과 + verdict 누적.

## Verdict 형식

각 baseline은 다음 metric 기록 (r1000 패턴):
- `strategy_cagr` — 연복리
- `excess_cagr` vs KOSPI200
- `sharpe`
- `ir` (information ratio)
- `max_dd`
- `beat_month_ratio`
- `turnover_monthly`
- `transaction_cost_total` (왕복 22bp 가정)
- `acceptance_checks` (PIT, NaN cliff, member count)

## Baselines (예정)

- [ ] `p0_baseline_kospi200_eq.md` — KOSPI200 동일가중 (vanilla benchmark)
- [ ] `p0_baseline_top30_momentum.md` — Top-30 12-1m momentum, 월 rebal, 22bp cost
- [ ] `p1_baseline_top30_blended.md` — momentum + value + quality blend
- [ ] `p2_baseline_top30_korea_alpha.md` — + 외인/기관/테마/공시
- [ ] `p3_baseline_top30_regime_aware.md` — + regime detection + DD breaker
- [ ] `p4_baseline_top30_ml.md` — + CatBoost ensemble
- [ ] `p5_baseline_concentrated_n5.md` — N=5 concentrated

## Ship gate (모든 phase 공통)
- ΔCAGR ≥ +0.5pp AND
- ΔSharpe ≥ -0.05 AND
- ΔMaxDD ≥ -3pp (positive delta = less drawdown = better)

## 현재 상태 (2026-04-27)

아직 baseline 미측정. P0 코드 완성 후 첫 baseline 기록.
