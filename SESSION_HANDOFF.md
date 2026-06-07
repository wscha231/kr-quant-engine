# Session Handoff - Single Inbox

## Current Status - 2026-06-07 15:39 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest committed base before this pass: `f910f03`.

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

- Added `tools/build_kr1000_weekly_price_overlay.py`.
- The tool carries the latest prior monthly PIT scored-panel row into weekly
  signal dates, then refreshes trailing price/RS/technical/volatility/liquidity
  features from cached ticker histories.
- It is a research bridge toward a proper daily/weekly full-universe feature
  refresh. It does not replace the official validation gate.
- Optional broker-ledger outputs are marked `research_only=true`,
  `official_broker_ledger_metric=false`, and `valid_for_production_metric=false`.
- Added regression coverage in `tests/test_kr1000_data_repair_tools.py`.

Short-window actual-cache smokes:

- `outputs/kr1000_weekly_overlay_smoke_2025_80x8_v2`: 80 tickers / 8 signal
  dates, loaded `77/80`, CAGR `32.17%`, MDD `-4.10%`, excess CAGR `-149.41%`.
- `outputs/kr1000_weekly_overlay_smoke_2026_20x4`: 20 tickers / 4 signal
  dates, loaded `19/20`, CAGR `34.39%`, MDD `-0.88%`, excess CAGR `-529.75%`.
- `outputs/kr1000_weekly_overlay_smoke_2026_5x1_flags`: 5 tickers / 1 signal
  date, verified research-only metric flags.

These are harness and metadata checks only. Do not treat them as official target
progress because the windows are too short and benchmark-relative results are
poor.

Prior cash benchmark sleeve research still stands:

- `tools/analyze_kr1000_cash_benchmark_sleeve.py` is diagnostic-only.
- Cost-aware selected sleeve: fraction `0.75`, 21d KOSPI200 guard `-3%`,
  one-way cost `5bp`, CAGR `26.81%`, MDD `-33.38%`, IR `0.378`.
- Do not claim KOSPI200 cash sleeve solves the target gap until an actual
  tradable KODEX200/benchmark sleeve is integrated into the broker ledger and
  clears the official gates.

Daily broker usability:

- `--skip-data-audit` exists in `tools/run_kr1000_daily_broker_check.py` for
  local dry-runs only.
- Production daily readiness still needs actual
  `DATA_ROOT/state/current_holdings.csv`.

## Tests Run

- `py -3 -m py_compile tools\build_kr1000_weekly_price_overlay.py tests\test_kr1000_data_repair_tools.py` -> passed.
- `py -3 tests\test_kr1000_data_repair_tools.py` -> 16 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tests\test_walkforward.py` -> 16 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 14 passed, 0 failed.
- `py -3 tests\test_kr1000_leader.py` -> 15 passed, 0 failed.
- `py -3 tools\build_kr1000_weekly_price_overlay.py --scored-panel "G:\내 드라이브\kr_quant_engine\feature_store\scored_panel_v0_2018-01-01_2026-06-04_2026-06-05-p1-pit-data-audit.parquet" --start 2025-01-01 --end 2026-06-04 --max-dates 8 --max-tickers 80 --run-backtest --out-dir outputs\kr1000_weekly_overlay_smoke_2025_80x8_v2` -> completed, research-only.
- `py -3 tools\build_kr1000_weekly_price_overlay.py --scored-panel "G:\내 드라이브\kr_quant_engine\feature_store\scored_panel_v0_2018-01-01_2026-06-04_2026-06-05-p1-pit-data-audit.parquet" --start 2026-01-01 --end 2026-06-04 --max-dates 4 --max-tickers 20 --run-backtest --out-dir outputs\kr1000_weekly_overlay_smoke_2026_20x4` -> completed, research-only.
- `py -3 tools\build_kr1000_weekly_price_overlay.py --scored-panel "G:\내 드라이브\kr_quant_engine\feature_store\scored_panel_v0_2018-01-01_2026-06-04_2026-06-05-p1-pit-data-audit.parquet" --start 2026-05-01 --end 2026-06-04 --max-dates 1 --max-tickers 5 --run-backtest --out-dir outputs\kr1000_weekly_overlay_smoke_2026_5x1_flags` -> completed, metrics carry research-only flags.

## Next Engineering Steps

1. Keep the top10/cap10/no-ladder value preset as the active challenger; it is
   not a pass.
2. Scale the new weekly overlay carefully:
   - first 2018-current with `--max-tickers 250`, then `500`, then full KR1000
   - watch cache I/O and missing cache coverage
   - compare benchmark-relative CAGR and IR before tuning weights
3. If the overlay remains excess-negative, stop score-weight tinkering and build
   the proper daily/weekly feature store with KR1000-wide trailing price,
   sector/theme RS, and breadth/regime columns.
4. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
   Without it, production daily broker readiness must remain blocked.
