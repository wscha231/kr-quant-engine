# 07 — Phase Experiments

각 Phase A/B 측정 raw 데이터 + diff. r1000 패턴: `PHASE_<KEY>_ENABLED=1` vs `=0` 결과 비교.

## 형식

각 실험은 `phase{N}_ab_{topic}.md` + `phase{N}_ab_{topic}.json` (raw metrics).

```yaml
---
phase_key: PHASE2_FOREIGN_FLOW
on_metrics: { cagr: 0.235, sharpe: 1.18, max_dd: -0.231 }
off_metrics: { cagr: 0.198, sharpe: 1.05, max_dd: -0.245 }
delta: { cagr: +0.037, sharpe: +0.13, max_dd: +0.014 }
verdict: SHIP
ship_gate_passed: true
---
```

## TODO

phase별 실험 결과는 phase 진행하며 채움.
