# Session Handoff - Single Inbox

## Current Status - 2026-06-07 10:08 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit entering this pass: `4028bc7`.

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

This pass changed:

- `kr1000_leader.py`
- `tools/run_kr1000_validation_gate.py`
- `tests/test_kr1000_leader.py`
- `tests/test_kr1000_validation_gate.py`
- `kr_backtester_realistic.py`
- `tools/build_pmb_oos_picks.py`
- `tools/run_kr1000_backtest.py`
- `tests/test_walkforward.py`
- `CHANGELOG.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`
- `SESSION_HANDOFF.md`

## What Changed

Added configurable P_MB 3-sleeve OOS score modes:

- `balanced`: legacy `(0.50 * p_pre_entry + 0.50 * p_continuation) * (1 - p_risk)`.
- `pre_entry_focus`: stronger pre-entry weighting.
- `strict_pre_entry`: `p_pre_entry * (1 - p_continuation) * (1 - p_risk)`.
- `pre_entry_risk_only`, `continuation_focus`, and `no_risk_balanced` diagnostics.

`tools/build_pmb_oos_picks.py` now accepts:

- `--score-mode`
- `--pre-buffer-months`
- `--pre-surge-months`
- `--post-surge-months`
- `--risk-drawdown-threshold`

`tools/run_kr1000_validation_gate.py` now passes through:

- `--pmb-oos-score-mode`
- `--pmb-pre-buffer-months`
- `--pmb-iterations`

The sparse OOS merge now preserves `p_balanced` and `p_clean_pre_entry` for
diagnostics while keeping `p_pre_surge = p_combined` for existing score-profile
compatibility.

This continuation added `pmb_pullback_recovery_regime`:

- full P_MB OOS coverage is preserved; the profile does not omit months from
  the OOS file.
- As-of regime mask: `bench_ret_1m <= -0.01 and bench_ret_3m >= 0.0`.
- Outside that mask, `score_profile_eligible_flag = False`, so the broker
  ledger goes to cash instead of buying arbitrary zero-score names.

## Data/Leakage State

Data integrity was rechecked:

```bash
py -3 tools\audit_data_integrity.py --as-of 2026-06-04
```

Result: Critical `0`, High `0`, Medium `0`.

Strict P_MB OOS build:

```bash
py -3 tools\build_pmb_oos_picks.py --panel "G:\내 드라이브\kr_quant_engine\outputs\scored_panel_v0_2016_20260604_forward_labels.parquet" --target-start 2018-01-01 --target-end 2026-06-04 --out "G:\내 드라이브\kr_quant_engine\outputs\p_mb_oos_picks_strict_preentry_buf2_2018_20260604.csv" --coverage-json "G:\내 드라이브\kr_quant_engine\outputs\p_mb_oos_picks_strict_preentry_buf2_2018_20260604.coverage.json" --score-mode strict_pre_entry --pre-buffer-months 2 --iterations 200 --fail-on-coverage-gap
```

Result:

- coverage `102/102` official months
- split gap `10` months
- features `99`
- label counts: `is_pre_entry=1133`, `is_continuation=1212`, `is_risk=2429`

## Latest Broker-Ledger Result

Best current MDD-control result:

```bash
py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile pmb_pullback_recovery_regime --pmb-oos-picks "G:\내 드라이브\kr_quant_engine\outputs\p_mb_oos_picks_purged_3sleeve_2018_20260604_latest.csv" --price-panel "G:\내 드라이브\kr_quant_engine\outputs\kr1000_bt_2018_20260604_component_ab_top20_price_panel_after_component_fix.parquet" --top-holdings 20 --max-rank-for-prices 20 --buy-rank-threshold 20 --hold-rank-threshold 40 --out-dir outputs\kr1000_bt_pmb_pullback_recovery_profile_top20_2018_20260604 --save-scored-panel
```

Result:

- years `8.42`
- CAGR `6.57%`
- KOSPI200 CAGR `18.57%`
- excess CAGR `-12.00%`
- MDD `-17.82%`
- Sharpe `0.846`
- Information Ratio `-0.575`
- trades `486`
- average cash weight `88.32%`
- metric mode `broker_ledger_next_close`, fill mode `next_close`

This clears the MDD gate but fails CAGR/excess/Sharpe/IR. It is a useful
defensive sleeve, not a complete production strategy.

Strict pre-entry top20 broker-ledger run:

```bash
py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile pmb_pre_surge --pmb-oos-picks "G:\내 드라이브\kr_quant_engine\outputs\p_mb_oos_picks_strict_preentry_buf2_2018_20260604.csv" --top-holdings 20 --max-rank-for-prices 20 --buy-rank-threshold 20 --hold-rank-threshold 40 --out-dir outputs\kr1000_bt_pmb_strict_preentry_buf2_top20_2018_20260604 --save-scored-panel
```

Result:

- years `8.34`
- CAGR `4.87%`
- KOSPI200 CAGR `18.57%`
- excess CAGR `-13.70%`
- MDD `-38.76%`
- Sharpe `0.358`
- Information Ratio `-0.886`
- trades `1,785`
- metric mode `broker_ledger_next_close`, fill mode `next_close`

The target is not met. Strict continuation/risk penalization improves the prior
strict OOS top20 result only slightly. Current bottleneck remains P_MB label and
feature quality, not the broker ledger or PIT coverage.

## Tests Run

- `py -3 -m py_compile kr_backtester_realistic.py tools\build_pmb_oos_picks.py tools\run_kr1000_validation_gate.py tools\run_kr1000_backtest.py tests\test_walkforward.py tests\test_kr1000_validation_gate.py` -> passed.
- `py -3 tests\test_walkforward.py` -> 16 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 10 passed, 0 failed.
- `py -3 tests\test_kr1000_leader.py` -> 15 passed, 0 failed.
- `py -3 tests\test_pmb_false_positive_audit.py` -> 2 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04` -> Critical `0`, High `0`, Medium `0`.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --component-ab --strategy-ab --dry-run --out-dir outputs\kr1000_validation_dryrun_pullback_profile` -> planned `22` broker backtests.

## Next Engineering Steps

1. Use `pmb_pullback_recovery_regime` as the current defensive baseline for
   MDD control, but do not call it production-pass because CAGR/excess fail.
2. Add a return engine for non-pullback months or improve labels; simply
   loosening the regime guard will likely reintroduce MDD.
3. Rebuild P_MB labels so ordinary high-volatility stocks, true pre-entry
   winners, and post-surge continuation names are separated more cleanly.
4. Add loss/false-positive labels only if OOS diagnostic AUC and realized
   return spread improve materially; current `p_bad_oos` AUC was only `0.538`.
5. Inspect 2018, 2022, and 2024 false-positive clusters at the fold level.
6. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
