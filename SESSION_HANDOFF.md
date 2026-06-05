# Session Handoff - Single Inbox

## Current Status - 2026-06-05 14:07 KST

KR1000 Leader Alpha now has GitHub/GDrive automation, data-store setup, broker
validation, and component/challenger A/B support on branch
`codex/kr1000-github-automation`.

The official user target remains unmet:

- Official target: 8y+ broker-ledger CAGR `>= 35%`, MDD `>= -25%`,
  KOSPI200 excess CAGR `> 0`, Sharpe `> 1.0`, IR `> 0.5`.
- Current best broker-ledger diagnostic found this session:
  P_MB OOS defensive run, 2020-01-31 to 2025-01-09, CAGR `24.69%`,
  MDD `-24.74%`, Sharpe `1.26`, KOSPI200 excess `+22.42%`.
- This is not an official 8y pass because the scored panel currently covers
  only `2019-01-31` to `2024-12-30`, and CAGR is below `35%`.

## What Changed In The Latest Pass

Two broker-ledger bugs were fixed:

1. Sparse P_MB OOS probabilities were being scored with robust z-score. Since
   most rows are zero, MAD was zero and all `leader_score` values became `0`.
   P_MB positive picks were then ranked by liquidity instead of probability.
2. Backtest trade planning computed current weights against holdings market
   value only, excluding cash. Once cash built up, buys were sized as a percent
   of remaining holdings instead of total NAV, leaving the ledger mostly cash.

Implemented fixes:

- `kr1000_leader._sparse_positive_rank_score()`
- `apply_kr1000_score_profile()` uses it for `pmb_pre_surge` and `hybrid_pmb_rs`
- `generate_trade_plan(..., account_nav=...)`
- `run_event_driven_backtest()` passes daily NAV into trade-plan generation
- `tools/run_kr1000_backtest.py` strategy override CLI:
  `--gross-exposure`, `--hard-stop-loss-pct`, `--buy-rank-threshold`,
  `--hold-rank-threshold`, `--min-notional-krw`, `--slippage-bp`,
  `--disable-daily-hard-exit`

Regression tests added:

- sparse P_MB OOS positive rows rank above zero non-picks
- trade plan weights holdings against cash-inclusive account NAV
- P_MB OOS picks merge remains PIT-safe by `rebalance_date,ticker`

## Latest Performance Evidence

Same P_MB OOS source:

- Before fixes: 2020-2024 broker-ledger `CAGR -5.61%`, `MDD -34.33%`
- Sparse-rank fix only: `CAGR 9.35%`, `MDD -25.18%`
- Sparse-rank + NAV fix: `CAGR 23.52%`, `MDD -33.07%`, Sharpe `0.99`
- Defensive CLI run:

```bash
py -3 tools\run_kr1000_backtest.py --start 2020-01-01 --end 2024-12-31 --score-profile pmb_pre_surge --gross-exposure 0.60 --hard-stop-loss-pct 0.10 --top-holdings 20 --buy-rank-threshold 20 --hold-rank-threshold 40 --out-dir "G:\내 드라이브\kr_quant_engine\outputs\kr1000_pmb_oos_defensive_cli_bt_2020_2024" --save-scored-panel --max-rank-for-prices 20 --price-panel "G:\내 드라이브\kr_quant_engine\outputs\kr1000_pmb_oos_rankfix_bt_2020_2024\leader_price_panel.parquet"
```

Result: `CAGR 24.69%`, `MDD -24.74%`, Sharpe `1.26`, excess `+22.42%`.

Full-score remains a failed signal after proper NAV sizing:

- `G:\내 드라이브\kr_quant_engine\outputs\kr1000_full_navfix_bt_2018_latest`
- Actual available window: `2019-01-31` to `2025-01-09`
- CAGR `-5.61%`, MDD `-50.01%`, excess `-7.41%`

## Latest Validation

Passed in this session:

- `py -3 tests/test_kr1000_leader.py` -> 8 passed, 0 failed
- `py -3 tests/test_kr1000_validation_gate.py` -> 3 passed, 0 failed
- `py -3 tests/smoke_test.py --quick` -> 24 passed, 0 failed
- `py -3 tests/smoke_test.py` -> 46 passed, 0 failed
- `py -3 tests/test_kr1000_data_store.py` -> 2 passed, 0 failed
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --component-ab --dry-run` -> 11 planned backtests
- `git diff --check` -> clean except CRLF warnings

## GitHub State

- Branch: `codex/kr1000-github-automation`
- Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1
- Latest pushed commit before this handoff: `7646908`
- Current latest fixes are local until committed/pushed.
- Existing unrelated dirty file: `research/10_theme_lifecycle/leader_themes_per_quarter.csv`.
  Do not stage it unless the user explicitly asks.

## Next Step

1. Run the remaining smoke/data-store checks.
2. Commit and push the broker-ledger fixes and docs.
3. Update PR #1 with the new performance evidence.
4. Restart or rerun GitHub validation on the new SHA.
5. For performance improvement, do not spend more time on the ledger harness
   first. The harness now exposes the problem clearly: full score fails, P_MB
   OOS works but tops out near `25%` CAGR under MDD control. Next work should
   improve alpha signal quality and rebuild the scored panel beyond
   `2024-12-30`.
