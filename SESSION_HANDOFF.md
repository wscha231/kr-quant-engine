# Session Handoff - Single Inbox

## Current Status - 2026-06-07 16:57 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest committed base before this pass: `5425050`.

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

The active production preset is still:

- strategy preset: `kr1000_technical_value_mcap_mdd_gate`
- score profile: `kr1000_technical_value_mcap_regime`
- top holdings: `10`
- buy threshold: `10`
- hold threshold: `20`
- single stock max weight: `10%`
- gross exposure: `1.00`
- hard stop: `15%`
- portfolio drawdown ladder: disabled

Locked baseline result:

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

This remains below the official CAGR `>=30%` and IR `>0.5` goals.

## This Pass

Broker-ledger benchmark sleeve:

- Added a synthetic KOSPI200 proxy sleeve ticker `900200` inside
  `kr1000_leader.run_event_driven_backtest()`.
- Sleeve orders are real broker-ledger orders: signal after close, next-close
  fill, integer shares, fees/slippage/tax, cash ledger, order/trade reason codes.
- Added daily idle-cash monitoring so hard-stop exits can be followed by
  benchmark sleeve orders when cash is high and KOSPI200 trend is strong.
- Added runner flags:
  - `--benchmark-sleeve`
  - `--benchmark-sleeve-fraction`
  - `--benchmark-sleeve-cash-trigger`
  - `--benchmark-sleeve-bench-ret-1m-min`
  - `--benchmark-sleeve-bench-ret-3m-min`
  - `--benchmark-sleeve-max-weight`
- Added validation strategy A/B preset:
  `kr1000_technical_value_mcap_benchmark_sleeve`.
- Kept `PRODUCTION_GATE_STRATEGY_PRESET` unchanged on
  `kr1000_technical_value_mcap_mdd_gate`.

Backtest evidence:

- Naive sleeve (`fraction=0.75`, `trigger=0.80`, `1m>=3%`, `3m>=0%`,
  `max=75%`) failed:
  - output `outputs\kr1000_bt_technical_value_mcap_benchmark_sleeve_2018_20260604`
  - CAGR `19.49%`
  - MDD `-26.97%`
  - do not use these settings
- Conservative sleeve default (`fraction=0.35`, `trigger=0.95`,
  `1m>=5%`, `3m>=10%`, `max=35%`) is the best sleeve challenger tested:
  - output `outputs\kr1000_bt_technical_value_mcap_benchmark_sleeve_default_2018_20260604`
  - CAGR `25.86%`
  - KOSPI200 CAGR `18.57%`
  - excess CAGR `+7.29%`
  - MDD `-19.65%`
  - Sharpe `1.28`
  - Information Ratio `0.267`
  - sleeve trades `6`

Conclusion:

- The conservative sleeve improved CAGR by about `+0.40pp` and improved MDD by
  about `+2.25pp` versus the locked baseline.
- This is useful, but not enough to clear CAGR `>=30%` or IR `>0.5`.
- Next work should focus on invested underperformance and missing index leaders,
  especially `2026-05`, not simply adding more cash exposure.

## Tests Run

- `py -3 -m py_compile kr1000_leader.py tools\run_kr1000_backtest.py tools\run_kr1000_validation_gate.py tests\test_kr1000_leader.py tests\test_kr1000_validation_gate.py` -> passed.
- `py -3 tests\test_kr1000_leader.py` -> 16 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 14 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile kr1000_technical_value_mcap_regime --price-panel outputs\kr1000_bt_technical_value_mcap_regime_top15_g90_ladder_2018_20260604\leader_price_panel.parquet --top-holdings 10 --max-rank-for-prices 20 --buy-rank-threshold 10 --hold-rank-threshold 20 --single-stock-max-weight 0.10 --gross-exposure 1.00 --hard-stop-loss-pct 0.15 --benchmark-sleeve --out-dir outputs\kr1000_bt_technical_value_mcap_benchmark_sleeve_default_2018_20260604 --save-scored-panel` -> completed.

## Next Engineering Steps

1. Commit and push the benchmark sleeve challenger work.
2. Keep the top10/cap10/no-ladder value preset as the active production gate
   until a challenger clears the full official target.
3. Use `outputs\kr1000_challenger_loss_months_2018_20260604` to diagnose
   invested underperformance months, especially `2026-05`.
4. Prototype sector/theme breadth or index-leader participation scoring to
   capture KOSPI200-led rallies without increasing broad market sleeve risk.
5. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
   Without it, production daily broker readiness must remain blocked.
