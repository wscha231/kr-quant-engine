# Session Handoff - Single Inbox

## Current Status - 2026-06-07 12:26 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this pass: `9230f3a`.

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

Committed changes in this pass:

- `tools/run_kr1000_backtest.py`
- `tools/run_kr1000_validation_gate.py`
- `tests/test_kr1000_validation_gate.py`
- `CHANGELOG.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`
- `SESSION_HANDOFF.md`

This pass also generated local diagnostic outputs under `outputs/`; they are
not intended for git staging unless explicitly requested.

## What Changed

Latest hardening pass:

- `tools/run_kr1000_backtest.py` and
  `tools/run_kr1000_validation_gate.py` now default P_MB OOS input to
  `DATA_ROOT/outputs/p_mb_oos_picks_purged_3sleeve_latest.csv`.
- The legacy `research/06_walkforward_baselines/p_mb_v1_oos_picks.csv` is no
  longer a default official input.
- `--build-pmb-oos-picks --require-pmb-oos-coverage` now passes
  `--fail-on-coverage-gap` to `tools/build_pmb_oos_picks.py`.
- Added tests that lock the purged latest default path and fail-fast build
  command behavior.

Latest performance pass:

- Tuned `kr1000_technical_mcap_mdd_gate` to the current best MDD-safe ladder:
  gross `0.90`, thresholds `-0.10,-0.18,-0.24`, scales `0.95,0.75,0.50`.
- Changed `PRODUCTION_GATE_STRATEGY_PRESET` to
  `kr1000_technical_mcap_mdd_gate` so official validation status follows the
  best current production challenger, not the older P_MB defensive preset.
- Added `test_production_gate_tracks_technical_mcap_preset`.

Previous pushed pass:

Added `kr1000_technical_mcap_regime`.

Rules:

- It is a KR1000-wide profile, not a P_MB OOS profile.
- Eligible only when:
  - `bench_ret_3m > 0.0`,
  - `technical_score > 0.0`,
  - `market_cap >= monthly KR1000 60th percentile`, and
  - `avg_trading_value_60d >= monthly KR1000 60th percentile`.
- It ranks eligible names by `technical_score`.
- It sets `score_profile_eligible_flag = False` outside that regime so the
  broker ledger holds cash instead of buying arbitrary zero-score names.

Current `kr1000_technical_mcap_mdd_gate` strategy A/B preset:

- profile: `kr1000_technical_mcap_regime`
- top holdings: `15`
- buy threshold: `15`
- hold threshold: `30`
- gross exposure: `0.90`
- hard stop: `15%`
- portfolio drawdown ladder thresholds `-0.10,-0.18,-0.24`
- portfolio drawdown ladder scales `0.95,0.75,0.50`

## Data/Leakage State

Latest rechecked data audit remains valid:

```bash
py -3 tools\audit_data_integrity.py --as-of 2026-06-04
```

Prior result: Critical `0`, High `0`, Medium `0`.

Official P_MB OOS coverage remains `102/102` months from 2018-01 through
2026-06 with full PIT sparse OOS picks, but from this pass forward the default
official P_MB path is the purged latest OOS artifact. The new
`kr1000_technical_mcap_regime` does not depend on P_MB probabilities, but it
still uses the same PIT scored panel and broker-ledger path.

## Latest Broker-Ledger Results

New best current MDD-safe challenger:

```bash
py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile kr1000_technical_mcap_regime --price-panel outputs\kr1000_bt_sparse_bench_pos_mcap_liq_technical_top20_2018_20260604\leader_price_panel.parquet --top-holdings 15 --max-rank-for-prices 40 --buy-rank-threshold 15 --hold-rank-threshold 30 --gross-exposure 0.90 --portfolio-dd-ladder --portfolio-dd-thresholds -0.10,-0.18,-0.24 --portfolio-dd-scales 0.95,0.75,0.50 --out-dir outputs\kr1000_bt_technical_mcap_regime_top15_g90_ladder_best_2018_20260604 --save-scored-panel
```

Result:

- years `8.34`
- CAGR `17.60%`
- KOSPI200 CAGR `18.57%`
- excess CAGR `-0.97%`
- MDD `-23.45%`
- Sharpe `1.055`
- trades `984`
- metric mode `broker_ledger_next_close`, fill mode `next_close`

Prior top15 ladder result:

- CAGR `16.44%`, MDD `-24.17%`, excess `-2.12%`, Sharpe `1.007`.

Prior top20 technical/mcap result:

- CAGR `13.66%`, MDD `-22.04%`, excess `-4.90%`, Sharpe `0.918`.

Top15 no-ladder sensitivity:

- CAGR `16.79%`, MDD `-24.36%`, excess `-1.78%`, Sharpe `0.98`.
- It improves CAGR but weakens Sharpe below `1.0`; keep the laddered top15
  setup as the strategy A/B preset.

Previous best P_MB-only MDD-safe challenger:

- `pmb_recovery_trend_value_regime` top20: CAGR `9.76%`, MDD `-19.06%`,
  excess CAGR `-8.81%`, Sharpe `0.763`, average cash weight `71.49%`.

Best KR1000 sparse diagnostics from this pass:

- `kr1000_not_bear_mcap_liq_rs_value` top20 without ladder:
  CAGR `14.77%`, MDD `-34.28%`, excess `-3.80%`.
- `kr1000_bench_pos_mcap_liq_technical` top20 without ladder:
  CAGR `12.80%`, MDD `-25.57%`, excess `-5.77%`.
- `kr1000_bench_pos_mcap_liq_technical` top20 with ladder:
  CAGR `13.66%`, MDD `-22.04%`, excess `-4.90%`.
- combined P_MB value-recovery + KR1000 technical balanced sleeve with ladder:
  CAGR `10.74%`, MDD `-20.36%`, excess `-7.83%`.
- exchange sleeves:
  - KOSDAQ-only top15 ladder: CAGR `6.22%`, MDD `-27.23%`.
  - KOSPI-only top15 ladder: CAGR `16.25%`, MDD `-23.78%`.
  - exchange-balanced top15 ladder: CAGR `12.97%`, MDD `-23.40%`.

The target is not met. The new KR1000 technical/mcap top15 g90 ladder profile
improves CAGR, MDD, Sharpe, and the KOSPI200 excess gap, but it still fails
CAGR/excess/IR.

## Tests Run

- `py -3 -m py_compile kr1000_leader.py tools\run_kr1000_validation_gate.py tests\test_kr1000_leader.py tests\test_kr1000_validation_gate.py` -> passed.
- `py -3 -m py_compile tools\run_kr1000_backtest.py tools\run_kr1000_validation_gate.py tests\test_kr1000_validation_gate.py` -> passed.
- `py -3 tests\test_kr1000_leader.py` -> 15 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 12 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\test_walkforward.py` -> 16 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --build-pmb-oos-picks --require-pmb-oos-coverage --component-ab --strategy-ab --dry-run --out-dir outputs\kr1000_validation_dryrun_pmb_oos_default_hardened` -> planned `25` broker backtests.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04` -> Critical `0`, High `0`, Medium `0`.
- `py -3 -m py_compile tools\run_kr1000_validation_gate.py tests\test_kr1000_validation_gate.py` -> passed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 13 passed, 0 failed.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --component-ab --strategy-ab --dry-run --out-dir outputs\kr1000_validation_dryrun_technical_mcap_g90_production` -> planned `25` broker backtests.
- `py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile kr1000_technical_mcap_regime --price-panel outputs\kr1000_bt_sparse_bench_pos_mcap_liq_technical_top20_2018_20260604\leader_price_panel.parquet --top-holdings 15 --max-rank-for-prices 40 --buy-rank-threshold 15 --hold-rank-threshold 30 --gross-exposure 0.90 --portfolio-dd-ladder --portfolio-dd-thresholds -0.10,-0.18,-0.24 --portfolio-dd-scales 0.95,0.75,0.50 --out-dir outputs\kr1000_bt_technical_mcap_regime_top15_g90_ladder_best_2018_20260604 --save-scored-panel` -> CAGR `17.60%`, MDD `-23.45%`.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --component-ab --strategy-ab --dry-run --out-dir outputs\kr1000_validation_dryrun_technical_mcap_profile` -> planned `25` broker backtests.
- `py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile kr1000_technical_mcap_regime --price-panel outputs\kr1000_bt_sparse_bench_pos_mcap_liq_technical_top20_2018_20260604\leader_price_panel.parquet --top-holdings 15 --max-rank-for-prices 40 --buy-rank-threshold 15 --hold-rank-threshold 30 --portfolio-dd-ladder --portfolio-dd-thresholds -0.08,-0.15,-0.25 --portfolio-dd-scales 0.85,0.65,0.40 --out-dir outputs\kr1000_bt_technical_mcap_regime_top15_ladder_2018_20260604 --save-scored-panel` -> CAGR `16.44%`, MDD `-24.17%`.

## Next Engineering Steps

1. Treat `kr1000_technical_mcap_regime` top15 ladder as the current best
   MDD-safe challenger, but do not call it production-pass because CAGR/excess
   and IR fail.
2. Use this profile as the new base for alpha improvement:
   add sector/theme RS exits, improve KOSDAQ/KOSPI sleeve allocation, and test
   macro exposure control without reducing already weak CAGR.
3. Investigate why the best `rs_value` sleeve reaches CAGR `14.77%` but MDD
   breaks at `-34.28%`; the next useful work is a drawdown-aware exit or
   crash-regime veto, not another P_MB probability tweak.
4. Rebuild P_MB labels separately; P_MB-only profiles remain too defensive and
   underperform KOSPI200.
5. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
