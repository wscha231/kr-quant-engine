# Session Handoff - Single Inbox

## Current Status - 2026-06-07 14:13 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Base commit before this pass: `bc21689`.

Active official target:

- broker-ledger next-close backtest over 8y+
- CAGR `>= 30%` official gate; CAGR `>= 35%` remains stretch objective
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- daily broker readiness based on actual holdings

Do not mark the active goal complete.

## Working Tree Rules

Do not stage or revert this unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

This pass generated local diagnostic outputs under `outputs/`; they are not
intended for git staging unless explicitly requested.

## What Changed In This Pass

New/changed mechanics:

- Added `--single-stock-max-weight` to `tools/run_kr1000_backtest.py`.
- Added diagnostic score profile `kr1000_technical_value06_mcap_regime`.
- Kept `PRODUCTION_GATE_STRATEGY_PRESET` at
  `kr1000_technical_value_mcap_mdd_gate`.
- Re-locked that preset to the officially verified top10/cap10/no-ladder setup:
  - score profile: `kr1000_technical_value_mcap_regime`
  - top holdings: `10`
  - buy threshold: `10`
  - hold threshold: `20`
  - single stock max weight: `10%`
  - gross exposure: `1.00`
  - hard stop: `15%`
  - portfolio drawdown ladder: disabled

Important correction:

- The experimental `kr1000_technical_value06_mcap_regime` looked strong when
  run against a pre-scored panel, but official runner recomputation reduced it.
  Do not promote value06 without a fresh official runner result that beats the
  active top10 value preset.

## Data/Leakage State

Latest rechecked data audit from the prior pass:

```bash
py -3 tools\audit_data_integrity.py --as-of 2026-06-04
```

Result: Critical `0`, High `0`, Medium `0`.

The current production challenger is non-PMB and uses the PIT scored panel plus
broker-ledger next-close path. P_MB OOS coverage remains required for P_MB
diagnostics only.

## Latest Broker-Ledger Result

Current best official-runner challenger:

```bash
py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile kr1000_technical_value_mcap_regime --price-panel outputs\kr1000_bt_technical_value_mcap_regime_top15_g90_ladder_2018_20260604\leader_price_panel.parquet --top-holdings 10 --max-rank-for-prices 20 --buy-rank-threshold 10 --hold-rank-threshold 20 --single-stock-max-weight 0.10 --gross-exposure 1.00 --hard-stop-loss-pct 0.15 --out-dir outputs\kr1000_bt_technical_value_mcap_regime_top10_cap10_g100_no_ladder_official_runner_2018_20260604 --save-scored-panel
```

Result:

- years `8.34`
- CAGR `25.46%`
- KOSPI200 CAGR `18.57%`
- excess CAGR `+6.89%`
- MDD `-21.90%`
- Sharpe `1.259`
- Information Ratio `0.251`
- trades `583`
- average cash weight `59.88%`
- metric mode `broker_ledger_next_close`, fill mode `next_close`
- valid for production metric: `True`

This is the current best 8y official-runner result. It improves the prior
locked top12 value preset but still fails CAGR `>=30%` and IR `>0.5`.

Prior locked challenger:

- `kr1000_technical_value_mcap_mdd_gate` top12/gross0.90/DD-ladder:
  CAGR `21.86%`, MDD `-22.94%`, excess `+3.30%`, Sharpe `1.190`, IR `0.093`.

Other useful diagnostics from this pass:

- Monthly active-return audit:
  `outputs/kr1000_value_top12_month_active_returns.csv`
  and `outputs/kr1000_value_top12_month_factor_diagnostics.csv`.
- Top10/cap grid:
  `outputs/kr1000_value_cap_gross_grid_reweighted.csv`.
- Concentration grid:
  `outputs/kr1000_value_concentration_grid.csv`.
- Value06 diagnostic official runner:
  `outputs/kr1000_bt_technical_value06_mcap_regime_top10_cap10_g100_no_ladder_official_runner_2018_20260604`
  produced CAGR `22.62%`, MDD `-22.93%`, excess `+4.05%`, Sharpe `1.12`;
  it is not better than the active preset.

## Tests Run

- `py -3 -m py_compile kr1000_leader.py tools\run_kr1000_backtest.py tools\run_kr1000_validation_gate.py tests\test_kr1000_leader.py tests\test_kr1000_validation_gate.py` -> passed.
- `py -3 tests\test_kr1000_leader.py` -> 15 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 14 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tests\test_walkforward.py` -> 16 passed, 0 failed.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --component-ab --strategy-ab --dry-run --out-dir outputs\kr1000_validation_dryrun_value_top10_cap10_production` -> planned `29` broker backtests and emitted the top10/cap10/no-ladder production command.
- `py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile kr1000_technical_value_mcap_regime --price-panel outputs\kr1000_bt_technical_value_mcap_regime_top15_g90_ladder_2018_20260604\leader_price_panel.parquet --top-holdings 10 --max-rank-for-prices 20 --buy-rank-threshold 10 --hold-rank-threshold 20 --single-stock-max-weight 0.10 --gross-exposure 1.00 --hard-stop-loss-pct 0.15 --out-dir outputs\kr1000_bt_technical_value_mcap_regime_top10_cap10_g100_no_ladder_official_runner_2018_20260604 --save-scored-panel` -> CAGR `25.46%`, MDD `-21.90%`, excess `+6.89%`.

## Next Engineering Steps

1. Keep the top10/cap10/no-ladder value preset as the current production
   challenger; it is not a pass.
2. The remaining gaps are CAGR/IR. Focus on stabilizing active return, not
   merely adding exposure.
3. Analyze the latest top10 monthly active-return losers, especially strong
   KOSPI200 bull months where the strategy still lags.
4. Test sector/theme RS or breadth confirmation against this top10 preset.
5. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
