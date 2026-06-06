# Session Handoff - Single Inbox

## Current Status - 2026-06-06 21:25 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this handoff: `3baac7e`.
Latest GitHub Smoke on `3baac7e` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27058964711

Draft PR body was last refreshed for `3baac7e` before the current uncommitted
mcap leakage-audit changes.

The active user target has been realigned to:

- 8y+ broker-ledger CAGR `>= 30%`
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- IR `> 0.5`
- daily account/broker readiness based on actual holdings

Official metrics must be `broker_ledger_next_close` with next-close fills.
CAGR `>= 35%` is now a stretch target only, not the official pass gate.

Important current blocker:

- The strengthened data audit now finds historical mcap cache leakage:
  `mktcap_ALL` snapshots from `2016-01-29` through `2026-05-29` include a
  54-snapshot identical fingerprint group without valid PIT provenance.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04`
  now returns Critical `1`, High `0`, Medium `0`.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --skip-daily-check --skip-backtests`
  is blocked on `data_integrity_audit_has_critical`.
- Do not record official 8y CAGR/MDD until those mcap caches are quarantined
  or replaced with true PIT data.

## Current Local Change Set

Latest uncommitted change set after `3baac7e`:

- `tools/audit_data_integrity.py` now detects distant identical
  `mktcap_ALL_YYYYMMDD.parquet` snapshots without explicit carry-forward
  provenance and raises a Critical issue.
- `kr_pykrx_client.fetch_ticker_history()` can reuse a broader covering ticker
  history cache instead of refetching exact overlapping windows.
- `kr_pipeline.find_incremental_scored_panel_cache()` now selects the best
  overlapping PIT-safe scored panel, including later-start panels, while still
  rejecting future-ended panels.
- `kr_pipeline.build_scored_panel_v0()` now computes missing rebalance dates
  rather than only dates after the prior panel max date.
- `run_local.py` now supports `--panel-only` and `--no-forward-labels`.
- `tools/run_kr1000_validation_gate.py --rebuild-scored-panel` now uses
  `run_local.py --panel-only --no-forward-labels` by default; use
  `--rebuild-forward-labels` or `--enrich-forward-labels` when target labels
  are explicitly needed.
- `tools/materialize_avg_value_proxy_caches.py --start 2016-01-01 --end 2018-12-31`
  wrote `36` avg-value proxy caches, failed `0`.
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
- `kr1000_leader.resolve_current_holdings_path()` was added and default
  holdings discovery now prefers
  `DATA_ROOT/state/current_holdings.csv` before project-local state.
- `tools/import_current_holdings.py` was added so raw broker CSV/TSV exports
  can be normalized into `DATA_ROOT/state/current_holdings.csv`.
- Daily and validation GitHub workflows now auto-import raw holdings exports
  from `data/state/current_holdings_raw.{csv,tsv}`,
  `data/state/broker_holdings.{csv,tsv}`, or
  `data/state/holdings_export.{csv,tsv}` before broker readiness, then sync
  `data/state` back to GDrive.
- Daily readiness now also blocks holdings files whose `shares` are all zero.
- `docs/KR1000_GITHUB_OPERATIONS.md`, `CHANGELOG.md`, and this handoff were
  updated for the new data repair/readiness behavior.

Do not stage the unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

## Data Gate Result

Data-integrity blockers are NOT cleared anymore. The newer audit found a root
mcap/PIT data issue:

- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04`
  -> Critical `1`, High `0`, Medium `0`.
- Critical issue:
  `mktcap cache has identical distant snapshots without carry-forward provenance`.
- Duplicate group details from
  `G:\내 드라이브\kr_quant_engine\outputs\data_integrity_audit_20260604.json`:
  first date `2016-01-29`, last date `2026-05-29`, span `3773` days,
  snapshot count `54`, rows `2772`.
- The first examples include `2016-01-29`, `2016-02-29`, `2016-03-31`,
  `2016-04-29`, `2016-05-31`, `2016-06-30` with no provenance, plus
  carry-forward rows derived from those suspect sources.
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
- Current canonical missing path:
  `DATA_ROOT/state/current_holdings.csv`.
- Accepted raw export drop locations in GDrive `state/`:
  `current_holdings_raw.csv`, `current_holdings_raw.tsv`,
  `broker_holdings.csv`, `broker_holdings.tsv`, `holdings_export.csv`,
  `holdings_export.tsv`.
- After the mcap duplicate audit change, daily readiness should also be treated
  as blocked by data integrity until the suspicious mcap snapshots are fixed.
- The tool still writes an inspection trade plan, but it must not be treated as
  production-ready until `DATA_ROOT/state/current_holdings.csv` is present and
  non-empty and the data audit has Critical `0`.

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
- `py -3 tests\test_kr1000_data_repair_tools.py` was rerun after the
  DATA_ROOT holdings resolver change -> 5 passed, 0 failed.
- `py -3 tests\test_kr1000_data_repair_tools.py` was rerun after the broker
  holdings import automation change -> 7 passed, 0 failed.
- `py -3 tests\test_kr1000_data_store.py` -> 7 passed, 0 failed.

Completed on 2026-06-06 21:25 KST:

- `py -3 -m py_compile tools\audit_data_integrity.py kr_pykrx_client.py run_local.py kr_pipeline.py tools\run_kr1000_validation_gate.py tests\test_kr1000_data_repair_tools.py tests\test_kr1000_data_store.py tests\test_kr1000_validation_gate.py`
  -> passed.
- `py -3 tests\test_kr1000_data_repair_tools.py` -> 9 passed, 0 failed.
- `py -3 tests\test_kr1000_data_store.py` -> 8 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 8 passed, 0 failed.
- `py -3 tools\materialize_avg_value_proxy_caches.py --start 2016-01-01 --end 2018-12-31`
  -> planned `36`, written `36`, skipped `0`, failed `0`.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04`
  -> Critical `1`, High `0`, Medium `0`; detected distant identical mcap
  snapshots without carry-forward provenance.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --skip-daily-check --skip-backtests --out-dir outputs\kr1000_validation_data_leak_audit_check`
  -> blocked on `data_integrity_audit_has_critical`.
- Attempted `run_local.py --quick --start-date 2018-01-01 --end-date 2026-06-04 --portfolio-size 20 --panel-only --no-collector --no-forward-labels`.
  It timed out before producing a 2018-start scored panel; do not leave this
  path running. The original bottleneck was per-ticker history fetching, so
  `fetch_ticker_history()` was updated to reuse covering caches, but a
  reliable full backfill still requires fixing/quarantining the suspect mcap
  cache first.

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

1. Stage only the intended files from this session. Do not stage
   `research/10_theme_lifecycle/leader_themes_per_quarter.csv`.
2. Commit/push the mcap leakage audit / scoped backfill changes and refresh
   the draft PR body.
3. Quarantine or replace the suspicious `mktcap_ALL` cache group before
   recording any official 8y CAGR/MDD. The flagged group spans `2016-01-29`
   through `2026-05-29` and appears to contain current-list/FDR fallback data
   written into historical dates.
4. After mcap cache repair, rebuild `data_pit/historical_mcap.parquet` and
   rerun `py -3 tools\audit_data_integrity.py --as-of 2026-06-04`; require
   Critical `0`.
5. Provide or sync actual `DATA_ROOT/state/current_holdings.csv`; rerun the
   daily broker check and require status `completed`. A raw broker export can
   be dropped into `DATA_ROOT/state/broker_holdings.csv` or one of the accepted
   raw filenames above and imported with `tools/import_current_holdings.py`.
6. Rebuild full-feature scored panel from at least `2018-01-01`, preferably
   `2016-01-01`.
7. Generate purged P_MB OOS picks for `2018-current` with active risk sleeve.
8. Run official validation with `--component-ab --strategy-ab`.
9. If CAGR remains below `30%`, improve signal quality in this order:
   `pmb + RS + flow + technical`, sector/theme RS exits, then macro regime
   sleeve scaling. Avoid exposure-only experiments until the 8y signal
   coverage problem is solved.
