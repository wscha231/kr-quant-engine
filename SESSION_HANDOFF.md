# Session Handoff - Single Inbox

## Current Status - 2026-06-07 09:17 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit entering this pass: `e54e761`.
Latest known GitHub Smoke on `e54e761` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27077373525

Active official target:

- broker-ledger next-close backtest over 8y+
- CAGR `>= 30%` official gate; `>= 35%` remains stretch objective
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- daily broker readiness based on actual holdings

Do not mark the active goal complete.

## Current Local Change Set

Do not stage or revert this unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

Intended files in this pass:

- `.github/workflows/smoke_test.yml`
- `tools/analyze_pmb_false_positives.py`
- `tests/test_pmb_false_positive_audit.py`
- `CHANGELOG.md`
- `SESSION_HANDOFF.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`

## What Changed

Added a leakage-safe false-positive audit:

- `tools/analyze_pmb_false_positives.py`
- consumes `outputs/pmb_oos_quality_realized_preentry_2018_20260604/pmb_rows.parquet`
- adds realized `good_trade` / `bad_trade` labels for diagnostics only
- selects numeric features while excluding `forward_`, `future_`, `target_`,
  `realized_`, `analysis_`, generated trade labels, `year`, and
  `pmb_oos_fold_id`
- runs pre-embargo walk-forward ExtraTrees models for `p_good_oos` and
  `p_bad_oos`
- writes `summary.json`, `model_metrics.csv`, `feature_gaps.csv`,
  `folds.csv`, and `oos_scores.parquet`

Added `tests/test_pmb_false_positive_audit.py` and wired it into GitHub Smoke.

## Current Data/Leakage State

The PIT data repair remains valid:

- `tools\audit_data_integrity.py --as-of 2026-06-04` previously returned
  Critical `0`, High `0`, Medium `0`.
- Canonical scored panel covers `2016-01-29` through `2026-06-04` with no
  missing monthly signals.
- Official purged 3-sleeve P_MB OOS picks cover `102/102` official target
  months from `2018-01` through `2026-06`.

Daily broker readiness is still not production-ready because actual holdings
evidence is missing. Required canonical path:

- `DATA_ROOT/state/current_holdings.csv`

## Latest False-Positive Audit

Command:

```bash
py -3 tools\analyze_pmb_false_positives.py --pmb-rows outputs\pmb_oos_quality_realized_preentry_2018_20260604\pmb_rows.parquet --target-start 2018-01-01 --target-end 2026-06-04 --out-dir outputs\pmb_false_positive_audit_2018_20260604
```

Output:

- rows: `2,438`
- months: `100`
- feature count: `112`
- OOS scored rows: `1,812`
- OOS scored months: `74`
- good-trade rate: `20.18%`
- bad-trade rate: `30.80%`
- `p_good_oos` vs good trade: AUC `0.510`
- `p_bad_oos` vs bad trade: AUC `0.538`
- existing `p_risk` vs bad trade: AUC `0.492`

Interpretation: current features do not support a reliable P_MB false-positive
gate. The risk model must be redesigned from better labels/features before it
is used in the broker ledger.

## Latest Broker-Ledger Results

Still failed:

- `pmb_pre_surge` strict OOS top20: CAGR `3.99%`, MDD `-35.87%`,
  excess CAGR `-14.57%`, Sharpe `0.302`
- `pmb_pre_entry_blend_regime` top15: CAGR `1.64%`, MDD `-23.80%`,
  excess CAGR `-16.93%`
- broad realized-history rerank top20: CAGR `2.86%`, MDD `-38.83%`,
  excess CAGR `-15.71%`, Sharpe `0.248`
- broad `base_plus_loss` top20: CAGR `3.32%`, MDD `-38.62%`,
  excess CAGR `-15.25%`, Sharpe `0.270`
- sparse `filter_def_hi_bench_pos` top20: CAGR `3.15%`, MDD `-60.29%`,
  excess CAGR `-15.42%`
- sparse `filter_def_hi_no_trend_bench_pos` top20: CAGR `3.54%`,
  MDD `-61.39%`, excess CAGR `-15.03%`

The target is not met. Current bottleneck is P_MB label design and
false-positive control, not the broker ledger or PIT coverage.

## Tests Run In This Work

- `py -3 -m py_compile tools\analyze_pmb_false_positives.py tests\test_pmb_false_positive_audit.py` -> passed.
- `py -3 tests\test_pmb_false_positive_audit.py` -> 2 passed, 0 failed.
- `py -3 tools\analyze_pmb_false_positives.py ...` -> completed.

Run before commit:

```bash
py -3 tests\smoke_test.py --quick
py -3 tests\smoke_test.py
py -3 tests\test_pmb_false_positive_audit.py
py -3 tests\test_pmb_broad_realized_rerank.py
py -3 tests\test_kr1000_validation_gate.py
```

## Next Engineering Steps

1. Rebuild P_MB label definitions. Separate ordinary high-volatility stocks,
   pre-entry winners, and post-surge continuation names more strictly.
2. Add new loss labels that have observed OOS correlation before using them in
   the broker ledger.
3. Focus on 2018/2022/2024 false-positive clusters; current momentum/RS
   strength often corresponds to worse bad-trade risk inside P_MB candidates.
4. Add regime features only after the label audit improves; do not continue
   exposure-only or sparse-filter experiments.
5. Daily readiness still needs real `DATA_ROOT/state/current_holdings.csv`.
