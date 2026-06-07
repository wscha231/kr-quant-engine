# Session Handoff - Single Inbox

## Current Status - 2026-06-07 10:23 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit entering this pass: `bf77a64`.

Active official target:

- broker-ledger next-close backtest over 8y+
- CAGR `>= 30%` official gate; CAGR `>= 35%` remains stretch objective
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
- `CHANGELOG.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`
- `SESSION_HANDOFF.md`

## What Changed

Added `pmb_recovery_trend_value_regime`.

Rules:

- It keeps the `pmb_pullback_recovery_regime` sleeve:
  `bench_ret_1m <= -0.01 and bench_ret_3m >= 0.0`.
- It adds a value-filtered trend sleeve:
  `bench_ret_3m >= 0.0387` and `valuation_score` above the P_MB-selected
  monthly median.
- It ranks with direct probabilities:
  `0.60 * p_pre_surge + 0.40 * p_pre_entry - 0.20 * p_risk`.
- It preserves full P_MB OOS file coverage; outside the regime,
  `score_profile_eligible_flag = False`, so the broker ledger goes to cash.

## Data/Leakage State

Latest rechecked data audit remains valid:

```bash
py -3 tools\audit_data_integrity.py --as-of 2026-06-04
```

Prior result: Critical `0`, High `0`, Medium `0`.

Official P_MB OOS coverage remains `102/102` months from 2018-01 through
2026-06 with full PIT sparse OOS picks.

## Latest Broker-Ledger Results

Best current return/MDD balance:

```bash
py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile pmb_recovery_trend_value_regime --pmb-oos-picks "G:\내 드라이브\kr_quant_engine\outputs\p_mb_oos_picks_purged_3sleeve_2018_20260604_latest.csv" --price-panel "G:\내 드라이브\kr_quant_engine\outputs\kr1000_bt_2018_20260604_component_ab_top20_price_panel_after_component_fix.parquet" --top-holdings 20 --max-rank-for-prices 20 --buy-rank-threshold 20 --hold-rank-threshold 40 --out-dir outputs\kr1000_bt_pmb_recovery_trend_value_profile_top20_2018_20260604 --save-scored-panel
```

Top20 result:

- years `8.42`
- CAGR `9.76%`
- KOSPI200 CAGR `18.57%`
- excess CAGR `-8.81%`
- MDD `-19.06%`
- Sharpe `0.763`
- Information Ratio `-0.452`
- trades `965`
- average cash weight `71.49%`
- metric mode `broker_ledger_next_close`, fill mode `next_close`

Top15 sensitivity:

- CAGR `9.81%`
- MDD `-18.08%`
- excess CAGR `-8.75%`
- Sharpe `0.742`
- trades `784`
- average cash weight `71.09%`

Defensive-only baseline:

- `pmb_pullback_recovery_regime` top20: CAGR `6.57%`, MDD `-17.82%`,
  excess CAGR `-12.00%`, Sharpe `0.846`, average cash weight `88.32%`.

The target is not met. `pmb_recovery_trend_value_regime` is the best current
full-window P_MB broker-ledger challenger under the MDD gate, but it still
fails CAGR/excess/Sharpe/IR.

## Tests Run

- `py -3 -m py_compile kr1000_leader.py tools\run_kr1000_validation_gate.py tests\test_kr1000_leader.py tests\test_kr1000_validation_gate.py` -> passed.
- `py -3 tests\test_kr1000_leader.py` -> 15 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 10 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --component-ab --strategy-ab --dry-run --out-dir outputs\kr1000_validation_dryrun_recovery_trend_value_profile` -> planned `23` broker backtests.

## Next Engineering Steps

1. Use `pmb_recovery_trend_value_regime` as the current best MDD-safe P_MB
   challenger, but do not call it production-pass because CAGR/excess fail.
2. Add another positive-excess return sleeve or improve labels; simply
   loosening the regime guard will likely reintroduce MDD.
3. Rebuild P_MB labels so ordinary high-volatility stocks, true pre-entry
   winners, and post-surge continuation names are separated more cleanly.
4. Add loss/false-positive labels only if OOS diagnostic AUC and realized
   return spread improve materially; current `p_bad_oos` AUC was only `0.538`.
5. Inspect 2018, 2022, and 2024 false-positive clusters at the fold level.
6. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
