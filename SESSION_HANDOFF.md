# Session Handoff - Single Inbox

## Current Status - 2026-06-07 18:04 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest committed base before this pass: `3828079`.

Active official target:

- broker-ledger next-close backtest over 8y+
- CAGR `>= 30%`; CAGR `>= 35%` remains a stretch objective
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- daily broker readiness based on actual holdings

Do not mark the active goal complete.

## Working Tree Rules

Do not stage or revert this unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

Local diagnostic CSVs/parquets under `outputs/` are research artifacts and are
not meant for git staging unless explicitly requested.

## Current Best Broker-Ledger Result

The active production preset remains:

- strategy preset: `kr1000_technical_value_mcap_mdd_gate`
- score profile: `kr1000_technical_value_mcap_regime`
- top holdings: `10`
- buy threshold: `10`
- hold threshold: `20`
- single stock max weight: `10%`
- gross exposure: `1.00`
- hard stop: `15%`
- portfolio drawdown ladder: disabled
- `sell_before_buy_same_day`: disabled

Latest reproduced default result:

- output `outputs\kr1000_bt_technical_value_mcap_cash_reason_default_2018_20260604`
- years `8.34`
- CAGR `25.46%`
- KOSPI200 CAGR `18.57%`
- excess CAGR `+6.89%`
- MDD `-21.90%`
- Sharpe `1.259`
- Information Ratio `0.251`
- trades `583`
- insufficient-cash orders `127`
- average cash weight `59.88%`
- metric mode `broker_ledger_next_close`, fill mode `next_close`

This remains below the official CAGR `>=30%` and IR `>0.5` goals.

## This Pass

2026-05 invested underperformance diagnosis:

- `2026-05` was not mainly a cash problem:
  - strategy return `+15.69%`
  - KOSPI200 return `+35.34%`
  - active return `-19.66%`
  - average cash weight `7.29%`
- 2026-05 average holdings were concentrated in
  `402340`, `000660`, `005930`, `006400`, `005380`, plus weaker top-rank
  names such as `001440`, `006340`, and `042700`.
- Top 2026-05 movers such as `009150` and `066570` were rank 6/7 on the
  2026-04-30 signal, but cash constraints left them at tiny average weights.

Broker cash/sequence work:

- Added `NO_TRADE_INSUFFICIENT_CASH` for buy orders that are not min-notional
  failures but cannot fill from available cash.
- Added metrics:
  - `insufficient_cash_orders`
  - `sell_before_buy_same_day`
- Added `--sell-before-buy-same-day` to `tools/run_kr1000_backtest.py`.
- Added validation-gate plumbing for presets that choose to pass
  `sell_before_buy_same_day`.
- Added tests for:
  - optional same-day sell/trim before buy execution
  - explicit insufficient-cash reason codes

A/B evidence:

- Default cash ledger stayed unchanged:
  - CAGR `25.46%`
  - MDD `-21.90%`
  - insufficient-cash orders `127`
- `--sell-before-buy-same-day` with rank-order-preserving buys:
  - output `outputs\kr1000_bt_technical_value_mcap_sell_first_rank_order_2018_20260604`
  - CAGR `21.60%`
  - MDD `-27.98%`
  - excess CAGR `+3.04%`
  - Sharpe `0.97`
  - insufficient-cash orders still present, but lower
- Conclusion: same-day sell proceeds / buying-power assumption increases
  exposure to weak top-rank names and breaks the MDD gate. Keep it as A/B
  diagnostic, not production default.

Failed experiments this pass:

- Offline index-leader/mcap/RS score profiles were weaker over the full 8y
  broker ledger and were not promoted.
- Param grid saved to `outputs\kr1000_param_grid_current_code_2018_20260604.csv`.
  MDD-gated best stayed the locked value top10/cap10 preset.
- DD-ladder grid saved to
  `outputs\kr1000_ladder_grid_current_code_2018_20260604.csv`; ladders reduced
  CAGR too much and did not beat the locked challenger.

## Tests Run

- `py -3 -m py_compile kr1000_leader.py tools\run_kr1000_backtest.py tools\run_kr1000_validation_gate.py tests\test_kr1000_leader.py` -> passed.
- `py -3 tests\test_kr1000_leader.py` -> 18 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 14 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile kr1000_technical_value_mcap_regime --price-panel outputs\kr1000_bt_technical_value_mcap_regime_top15_g90_ladder_2018_20260604\leader_price_panel.parquet --top-holdings 10 --max-rank-for-prices 20 --buy-rank-threshold 10 --hold-rank-threshold 20 --single-stock-max-weight 0.10 --gross-exposure 1.00 --hard-stop-loss-pct 0.15 --out-dir outputs\kr1000_bt_technical_value_mcap_cash_reason_default_2018_20260604 --save-scored-panel` -> completed.
- `py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile kr1000_technical_value_mcap_regime --price-panel outputs\kr1000_bt_technical_value_mcap_regime_top15_g90_ladder_2018_20260604\leader_price_panel.parquet --top-holdings 10 --max-rank-for-prices 20 --buy-rank-threshold 10 --hold-rank-threshold 20 --single-stock-max-weight 0.10 --gross-exposure 1.00 --hard-stop-loss-pct 0.15 --sell-before-buy-same-day --out-dir outputs\kr1000_bt_technical_value_mcap_sell_first_rank_order_2018_20260604 --save-scored-panel` -> completed, failed MDD gate.

## Next Engineering Steps

1. Commit and push this broker cash-reason/sequencing A/B work.
2. Keep the top10/cap10/no-ladder value preset as the active production gate
   until a challenger clears the full official target.
3. Next likely useful path is not same-day sell proceeds or simple mcap/RS
   scoring. Focus on improving rank quality inside the current top10 signal:
   overextended losers such as `001440`, `006340`, and `042700` need a
   full-window-safe overextension/reversal risk model, not a 2026-05-only rule.
4. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
   Without it, production daily broker readiness must remain blocked.
