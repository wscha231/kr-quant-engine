# Session Handoff - Single Inbox

## Current Status - 2026-06-07 14:46 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest committed base before this pass: `f0c064c`.

Active target:

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

Local diagnostic CSVs under `outputs/` are research artifacts and are not meant
for git staging unless explicitly requested.

## Current Best Broker-Ledger Result

The active production challenger is still:

- strategy preset: `kr1000_technical_value_mcap_mdd_gate`
- score profile: `kr1000_technical_value_mcap_regime`
- top holdings: `10`
- buy threshold: `10`
- hold threshold: `20`
- single stock max weight: `10%`
- gross exposure: `1.00`
- hard stop: `15%`
- portfolio drawdown ladder: disabled

Official runner command:

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

This remains below the official CAGR `>=30%` and IR `>0.5` gates.

## This Pass

Performance diagnostics:

- Wrote `outputs/kr1000_value_top10_month_active_exposure_audit.csv`.
- Worst active months are still strong KOSPI200 rebound/bull months, especially
  2026-05, 2020-11, 2023-11, 2019-01, and 2020-04.
- Fast A/B of recovery mask `bench_ret_3m > 0 or bench_ret_1m > 0` reached
  roughly CAGR `25.68%` but MDD worsened to `-25.38%`.
- Gross/stop/DD-ladder variants restored risk only by giving back CAGR. Do not
  promote the recovery mask yet.

Daily broker safety:

- Added `NON_ACTIONABLE_SNAPSHOT_MODES`.
- Added `non_actionable_snapshot_reasons()`.
- Added `select_actionable_signal_snapshot()`.
- `tools/run_kr1000_daily_broker_check.py` now skips
  `snapshot_build_mode=latest_fast_liquidity_only` for live action generation
  and falls back to the latest prior actionable signal.
- Actual selector check on the current official scored panel selected
  `2026-05-29` while recording visible latest `2026-06-04` as skipped
  `latest_fast_liquidity_only`.

Reason: a liquidity-only latest snapshot has incomplete feature scores and can
otherwise create false `SELL_RANK_BREAK` actions for real current holdings.

## Tests Run

- `py -3 -m py_compile tools\run_kr1000_daily_broker_check.py tests\test_kr1000_data_repair_tools.py` -> passed.
- `py -3 tests\test_kr1000_data_repair_tools.py` -> 14 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.

The full daily broker check with data audit timed out in an interactive
120-second run; the new signal-selector behavior itself was verified directly.

## Next Engineering Steps

1. Keep the current top10/cap10/no-ladder value preset as the production
   challenger; it is not a pass.
2. Next performance work should focus on a signal that captures rebound months
   without breaking the MDD gate. The simple `bench1` recovery mask was not
   enough.
3. Investigate daily/weekly feature refresh rather than only monthly scored
   panels, because several worst active months are missed before month-end
   signals can react.
4. Add actual `DATA_ROOT/state/current_holdings.csv`; without it daily broker
   readiness must remain blocked.
5. If touching daily check again, consider a bounded/optional data-audit mode
   for local dry-runs, but keep production validation strict.
