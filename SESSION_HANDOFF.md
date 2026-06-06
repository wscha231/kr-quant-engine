# Session Handoff - Single Inbox

## Current Status - 2026-06-06 18:11 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this handoff: `c01b843`.
Latest GitHub Smoke on `c01b843` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27057599166

Draft PR body was last refreshed for `c01b843`.

The active user target has been realigned to:

- 8y+ broker-ledger CAGR `>= 30%`
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- IR `> 0.5`
- daily account/broker readiness based on actual holdings

Official metrics must be `broker_ledger_next_close` with next-close fills.
CAGR `>= 35%` is now a stretch target only, not the official pass gate.

## Current Local Change Set

Latest data-gate-clear change set after `c01b843`:

- `tools/materialize_mcap_carry_forward_caches.py` was added.
- `tools/repair_scored_panel_fundamental_metadata.py` was added.
- `tests/test_kr1000_data_repair_tools.py` was added.
- `kr_pykrx_client.fetch_daily_ohlcv_market()` no longer references the
  undefined `allow_fdr_fallback` name.
- `kr_pit_universe.build_historical_mcap_panel()` preserves mcap
  carry-forward provenance columns.
- `tools/run_kr1000_daily_broker_check.py` now blocks production readiness
  when actual holdings evidence is missing or empty. `--allow-empty-holdings`
  exists for research dry-runs only.
- `docs/KR1000_GITHUB_OPERATIONS.md`, `CHANGELOG.md`, and this handoff were
  updated for the new data repair/readiness behavior.

Do not stage the unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

## Data Gate Result

Data-integrity blockers are cleared for the latest available local signal date:

- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04`
  -> Critical `0`, High `0`, Medium `0`.
- `tools/materialize_mcap_carry_forward_caches.py --start 2016-01-01 --end 2026-06-04`
  wrote `28` PIT-safe mcap carry-forward proxy caches, skipped `97`, failed
  `0`, and rebuilt `historical_mcap.parquet` to `366,782` rows / `137`
  snapshots.
- `tools/repair_scored_panel_fundamental_metadata.py` wrote:
  `feature_store/scored_panel_v0_2019-01-01_2026-06-04_2026-06-05-p1-pit-data-audit_forward_labels_fundmeta_repaired.parquet`
  and reduced stale DART metadata audit counts from period-after-rcept `293`
  and period-after-signal `113` to `0`.
- pykrx historical mcap backfill remains source-blocked in this environment:
  all `28` missing month-end fetches returned empty, so the mcap repair used
  prior PIT snapshots only and kept provenance in each proxy file.

## Daily Broker Readiness

Daily broker readiness is now correctly tied to actual holdings evidence:

- `py -3 tools\run_kr1000_daily_broker_check.py --evaluation-date 2026-06-04`
  -> `blocked`.
- Blockers: `current_holdings_file_missing`, `current_holdings_empty`.
- Data audit inside the daily check is clean: Critical `0`, High `0`,
  Medium `0`.
- The tool still writes an inspection trade plan, but it must not be treated as
  production-ready until `state/current_holdings.csv` is present and non-empty.

Latest committed functional change set in `1c33aca`:

- `tools/materialize_avg_value_proxy_caches.py` was added.
- PIT-safe avg-value proxy caches were materialized for `2025-01-31` through
  `2026-03-31`.
- `tools/audit_data_integrity.py --as-of 2026-06-04` is now Critical `0`,
  High `3`, Medium `0`; the avg-value cache gap issue is cleared.

Prior committed change set in `ca002b3`:

- `kr_features.sanitize_fundamental_period_metadata()` repairs stale cached
  DART rows where `period_end > rcept_dt` before PIT joins.
- `add_pit_fundamentals()` and `prepare_pit_fundamentals_panel()` now apply
  that sanitizer, and TTM/YoY helpers sort by `period_end` / `rcept_dt` first.
- `kr_pykrx_client.fetch_market_cap_market()` has `allow_fdr_fallback`; the
  daily refresh disables FDR fallback automatically for historical `--as-of`
  requests older than 14 days.
- `tools/backfill_mcap_cache_gaps.py` was added for pykrx-only historical mcap
  gap backfills.
- `tools/audit_data_integrity.py` now recognizes full validation-gate
  workflows as broker-backtest automation.
- New tests cover cached DART period metadata repair and validation-gate
  workflow audit detection.

## Data / Performance Facts

- Canonical scored panel in GDrive:
  `feature_store/scored_panel_v0_2019-01-01_2026-06-04_2026-06-05-p1-pit-data-audit_forward_labels.parquet`
- That panel starts at `2019-01-31`, so it still fails the official 8y start
  gate for `2018-01-01`.
- Default legacy P_MB OOS picks:
  `research/06_walkforward_baselines/p_mb_v1_oos_picks.csv`
  covers `2020-01-31` to `2024-12-30`, 60 months, 30 picks/month.
- Legacy P_MB defensive broker-ledger diagnostic, 2020-2024:
  CAGR `25.38%`, MDD `-24.00%`, Sharpe `1.23`, IR `1.00`,
  KOSPI200 excess `+23.12%`.
- Newly identified P_MB mid-rank challenger, 2020-2024:
  CAGR about `28.15%`, MDD about `-22.18%`, Sharpe about `1.29`,
  KOSPI200 excess about `+25.88%`.
- Neither P_MB diagnostic is an official pass yet because both lack 8y OOS
  coverage and remain below the official `30%` CAGR target.
- Full score 2019-2025 broker-ledger run remains poor:
  CAGR `-5.61%`, MDD `-50.01%`.
- Full-panel forward-label P_MB diagnostics are weak and must not replace the
  legacy P_MB challenger:
  - raw 3-sleeve: CAGR `2.44%`, MDD `-31.99%`
  - pre-entry only: CAGR `3.26%`, MDD `-32.39%`
  - no-risk combo: CAGR `2.60%`, MDD `-30.82%`
  - observed-mask 3-sleeve: CAGR `4.26%`, MDD `-28.38%`

## Validation Completed

Completed on 2026-06-06 16:41 KST:

- `py -3 -m py_compile kr1000_leader.py tools\run_kr1000_validation_gate.py kr_config.py`
- `py -3 tests\test_kr1000_leader.py` -> 12 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 7 passed, 0 failed.
- `py -3 tests\test_walkforward.py` -> 15 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --build-pmb-oos-picks --component-ab --strategy-ab --dry-run --out-dir H:\kr_quant_engine\outputs\kr1000_validation_dryrun_cagr30_midrank`
  -> planned 14 broker backtests, target CAGR `0.30`, including
  `pmb_mid_rank_7_23` and `pmb_mid_rank_no_leverage_mdd_gate`.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04`
  -> Critical `0`, High `3`, Medium `0` after proxy avg-value materialization.
- `py -3 tests\test_dart_pit.py` -> 17 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 8 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --build-pmb-oos-picks --component-ab --strategy-ab --dry-run --out-dir H:\kr_quant_engine\outputs\kr1000_validation_dryrun_pit_safety`
  -> planned 14 broker backtests, target CAGR `0.30`.

Completed on 2026-06-06 18:11 KST:

- `py -3 -m py_compile kr_pykrx_client.py kr_pit_universe.py tools\materialize_mcap_carry_forward_caches.py tools\repair_scored_panel_fundamental_metadata.py tools\run_kr1000_daily_broker_check.py tests\test_kr1000_data_repair_tools.py`
  -> passed.
- `py -3 tests\test_kr1000_data_repair_tools.py` -> 4 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tests\test_dart_pit.py` -> 17 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 8 passed, 0 failed.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04`
  -> Critical `0`, High `0`, Medium `0`.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --build-pmb-oos-picks --component-ab --strategy-ab --dry-run --out-dir H:\kr_quant_engine\outputs\kr1000_validation_dryrun_data_gate_clear`
  -> planned 14 broker backtests, target CAGR `0.30`.
- `py -3 tools\run_kr1000_daily_broker_check.py --evaluation-date 2026-06-04`
  -> blocked on `current_holdings_file_missing` and `current_holdings_empty`.

Backfill smoke:

- `py -3 tools\materialize_avg_value_proxy_caches.py --start 2025-01-01 --end 2026-06-04 --dry-run`
  -> planned 15 proxy cache writes, skipped 2 existing true caches, failed 0.
- `py -3 tools\materialize_avg_value_proxy_caches.py --start 2025-01-01 --end 2026-06-04`
  -> wrote 15 proxy cache files, skipped 2 existing true caches, failed 0.
- `py -3 tools\backfill_mcap_cache_gaps.py --start 2016-01-01 --end 2026-06-04 --dry-run`
  -> 28 missing business-month-end mcap cache dates.
- `py -3 tools\backfill_mcap_cache_gaps.py --start 2016-01-01 --end 2026-06-04 --max-dates 1`
  -> failed safely: pykrx returned empty for `2016-07-29`, and no FDR
  fallback current-list data or empty cache parquet was saved.

## Reproduction Command

The mid-rank diagnostic was reproduced with:

```bash
py -3 tools\run_kr1000_backtest.py --start 2020-01-01 --end 2024-12-31 --score-profile pmb_mid_rank_7_23 --price-panel "G:\내 드라이브\kr_quant_engine\outputs\kr1000_pmb_legacy_current_baseline_bt_2020_2024\leader_price_panel.parquet" --gross-exposure 1.0 --hard-stop-loss-pct 0.10 --portfolio-dd-ladder --portfolio-dd-thresholds "-0.10,-0.18,-0.24" --portfolio-dd-scales "0.80,0.60,0.35" --top-holdings 20 --buy-rank-threshold 20 --hold-rank-threshold 40 --out-dir "G:\내 드라이브\kr_quant_engine\outputs\kr1000_pmb_mid_rank_7_23_g100_bt_2020_2024"
```

Result: CAGR `28.15%`, MDD `-22.18%`, Sharpe `1.29`, KOSPI200 excess
`+25.88%`, 1,276 trades.

## Next Production Steps

1. If this change set has not yet been pushed, commit/push it and refresh the
   draft PR body.
2. Provide or sync actual `state/current_holdings.csv`; rerun the daily broker
   check and require status `completed`.
3. Rebuild full-feature scored panel from at least `2018-01-01`, preferably
   `2016-01-01`.
4. Generate purged P_MB OOS picks for `2018-current` with active risk sleeve.
5. Run official validation with `--component-ab --strategy-ab`.
6. If CAGR remains below `30%`, improve signal quality in this order:
   `pmb + RS + flow + technical`, sector/theme RS exits, then macro regime
   sleeve scaling. Avoid exposure-only experiments until the 8y signal
   coverage problem is solved.
