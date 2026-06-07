# Session Handoff - Single Inbox

## Current Status - 2026-06-07 15:07 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest committed base before this pass: `4525ae3`.

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

Local diagnostic CSVs/parquets under `outputs/` are research artifacts and are
not meant for git staging unless explicitly requested.

## Current Best Broker-Ledger Result

The active production challenger remains:

- strategy preset: `kr1000_technical_value_mcap_mdd_gate`
- score profile: `kr1000_technical_value_mcap_regime`
- top holdings: `10`
- buy threshold: `10`
- hold threshold: `20`
- single stock max weight: `10%`
- gross exposure: `1.00`
- hard stop: `15%`
- portfolio drawdown ladder: disabled

Official runner:

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

This remains below the official CAGR `>=30%`, IR `>0.5`, and stretch CAGR
`>=35%` goals.

## This Pass

Weekly price overlay research:

- Built local research artifacts under
  `outputs/kr1000_weekly_price_overlay_research/`.
- Simple weekly price-only overlay was weak. Best saved row was approximately
  CAGR `16.04%`, MDD `-37.38%`, excess `-2.53%`.
- Do not pursue this simple price-only weekly overlay without a proper
  universe-wide daily feature build.

Cash benchmark sleeve research:

- Added `tools/analyze_kr1000_cash_benchmark_sleeve.py`.
- It is diagnostic-only and marks `official_broker_ledger_metric=false`.
- Cost-aware selected sleeve:
  - fraction `0.75`
  - 21d KOSPI200 guard `-3%`
  - one-way cost `5bp`
  - CAGR `26.81%`
  - MDD `-33.38%`
  - IR `0.378`
- MDD-safe sleeve grid rows stayed near CAGR `26.3%`, still below the official
  `30%` gate and well below the `35%` stretch target.
- Conclusion: do not claim KOSPI200 cash sleeve solves the target gap until an
  actual tradable KODEX200/benchmark sleeve is integrated into the broker
  ledger with order/fill/cost accounting and clears the gates.

Daily broker usability:

- Added `--skip-data-audit` to `tools/run_kr1000_daily_broker_check.py`.
- This is local dry-run only; production validation must keep the data audit.
- Verified skip-audit daily check completed for evaluation date `2026-06-04`,
  selected actionable signal `2026-05-29`, and visible latest `2026-06-04`.

## Tests Run

- `py -3 -m py_compile tools\analyze_kr1000_cash_benchmark_sleeve.py tools\run_kr1000_daily_broker_check.py tests\test_kr1000_data_repair_tools.py` -> passed.
- `py -3 tests\test_kr1000_data_repair_tools.py` -> 15 passed, 0 failed.
- `py -3 tools\analyze_kr1000_cash_benchmark_sleeve.py --daily-nav outputs\kr1000_bt_technical_value_mcap_regime_top10_cap10_g100_no_ladder_official_runner_2018_20260604\leader_backtest_daily_nav.csv --out-dir outputs\kr1000_cash_benchmark_sleeve_diag_2018_20260604 --selected-fraction 0.75 --selected-guard -0.03 --selected-cost-bp 5` -> selected CAGR `26.81%`, MDD `-33.38%`.
- `py -3 tools\run_kr1000_daily_broker_check.py --evaluation-date 2026-06-04 --scored-panel outputs\kr1000_bt_technical_value_mcap_regime_top10_cap10_g100_no_ladder_official_runner_2018_20260604\leader_scored_panel.parquet --allow-empty-holdings --skip-data-audit --out-dir outputs\kr1000_daily_check_skip_audit_test` -> completed.

## Next Engineering Steps

1. Keep the top10/cap10/no-ladder value preset as the active challenger; it is
   not a pass.
2. Stop testing simple weekly price-only overlays and simple cash benchmark
   sleeves as if they are likely to solve the gap; both failed after realistic
   checks.
3. The next credible performance path is a proper daily/weekly full-universe
   feature refresh: KR1000-wide trailing price/RS/technical features, not just
   ever-top20 price panel overlays.
4. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
   Without it, production daily broker readiness must remain blocked.
