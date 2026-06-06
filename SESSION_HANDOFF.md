# Session Handoff - Single Inbox

## Current Status - 2026-06-07 08:30 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this handoff: `df09d57`.
Latest known GitHub Smoke on `df09d57` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27076382789

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

Intended files in this handoff:

- `.github/workflows/smoke_test.yml`
- `kr1000_leader.py`
- `tools/run_kr1000_backtest.py`
- `tools/run_kr1000_validation_gate.py`
- `tools/analyze_pmb_oos_quality.py`
- `tools/build_pmb_realized_rerank_picks.py`
- `tests/test_kr1000_leader.py`
- `tests/test_kr1000_validation_gate.py`
- `tests/test_pmb_realized_rerank.py`
- `CHANGELOG.md`
- `SESSION_HANDOFF.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`

## What Changed

P_MB OOS merge is stricter and more complete:

- `merge_pmb_oos_predictions()` now carries `p_pre_entry`,
  `p_continuation`, `p_risk`, `p_combined`, and `pmb_oos_fold_id`.
- When a PIT sparse OOS picks file is supplied, non-picked rows are explicit
  zeroes for generated P_MB columns. They no longer inherit any panel-side
  live/classifier values.

Added challenger profiles:

- `pmb_pre_entry`
- `pmb_pre_entry_defensive`
- `pmb_pre_entry_blend`
- `pmb_pre_entry_blend_regime`

Added `tools/build_pmb_realized_rerank_picks.py`, a PIT sidecar that reranks
P_MB rows by training only on prior realized next-rebalance outcomes:

```bash
py -3 tools\build_pmb_realized_rerank_picks.py --pmb-rows outputs\pmb_oos_quality_realized_preentry_2018_20260604\pmb_rows.parquet --target-start 2018-01-01 --target-end 2026-06-04 --out outputs\p_mb_oos_picks_realized_rerank_2018_20260604.csv --k-per-month 30 --embargo-months 3 --min-train-rows 240 --risk-penalty 0.12 --fail-on-coverage-gap
```

The reranker output covered `100/102` official months and passed the configured
coverage gate.

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

## Latest Realized P_MB OOS Quality Result

Latest diagnostic output:

- `outputs/pmb_oos_quality_realized_preentry_2018_20260604/`

Important filters:

- all P_MB OOS: mean `0.758%`, median `-1.606%`, loss `< -10%` rate
  `23.45%`
- `pre_entry_high`: mean `1.060%`, median `-1.210%`, loss `< -10%` rate
  `19.87%`
- `pre_entry_defensive_high_bench_3m_pos`: mean `1.937%`, median `-0.630%`,
  loss `< -10%` rate `16.30%`, average min return `-8.01%`

Interpretation: pre-entry/risk filters improve drawdown distribution, but not
enough to create a high-CAGR portfolio in the broker ledger.

## Latest Broker-Ledger Results

Still failed:

- `pmb_pre_surge` strict OOS top20: CAGR `3.99%`, MDD `-35.87%`,
  excess CAGR `-14.57%`, Sharpe `0.302`
- `pmb_pre_entry` top20: CAGR `3.37%`, MDD `-29.00%`, excess CAGR `-15.20%`
- `pmb_pre_entry_defensive` top15 + DD ladder: CAGR `3.38%`, MDD `-26.29%`,
  excess CAGR `-15.19%`
- `pmb_pre_entry_blend` top20: CAGR `1.18%`, MDD `-39.01%`
- `pmb_pre_entry_blend_regime` top15: CAGR `1.64%`, MDD `-23.80%`,
  excess CAGR `-16.93%`
- `pmb_pre_entry_blend_regime` top15 + DD ladder: CAGR `-0.32%`, MDD `-26.00%`
- realized-history rerank top20: CAGR `2.30%`, MDD `-39.30%`
- realized-history rerank top15: CAGR `2.01%`, MDD `-44.53%`
- realized-history rerank top20 + DD ladder: CAGR `-1.48%`, MDD `-28.26%`

The target is not met. Current bottleneck is not the broker ledger and not a
simple pre-entry/risk rerank. The P_MB label design and false positives need
to be rebuilt from a broader training population.

## Tests Run In This Work

- `py -3 -m py_compile kr1000_leader.py tools\run_kr1000_backtest.py tools\run_kr1000_validation_gate.py tools\analyze_pmb_oos_quality.py tools\build_pmb_realized_rerank_picks.py` -> passed.
- `py -3 tests\test_kr1000_leader.py` -> 15 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 10 passed, 0 failed.
- `py -3 tests\test_pmb_oos_quality.py` -> 3 passed, 0 failed.
- `py -3 tests\test_pmb_realized_rerank.py` -> 2 passed, 0 failed.
- `py -3 tools\build_pmb_realized_rerank_picks.py ... --fail-on-coverage-gap` -> passed.

Run full smoke before commit:

```bash
py -3 tests\smoke_test.py --quick
py -3 tests\smoke_test.py
```

## Next Engineering Steps

1. Inspect P_MB false positives by fold and year. Focus on names selected by
   `p_pre_surge` that realized `analysis_return <= -10%` or
   `analysis_min_return <= -15%`.
2. Train the loss-aware model from the broader scored KR1000 universe, not
   only from already selected P_MB OOS rows. The selected-only reranker failed.
3. Revisit `label_risk()` and the P_MB risk threshold. Current `p_risk` has
   weak realized risk correlation.
4. Add regime features that explain 2018/2022/2024 failures before trying more
   exposure changes.
5. Daily readiness still needs real `DATA_ROOT/state/current_holdings.csv`.
