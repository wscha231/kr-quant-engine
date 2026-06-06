# Session Handoff - Single Inbox

## Current Status - 2026-06-07 05:55 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this handoff: `d02487c`.
Latest known GitHub Smoke on `d02487c` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27062372746

Active official target:

- broker-ledger next-close backtest over 8y+
- CAGR `>= 30%`
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- daily broker readiness based on actual holdings

CAGR `>= 35%` is stretch only. Do not mark the active goal complete.

## Current Local Change Set

Do not stage or revert this unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

Intended uncommitted files:

- `tools/quarantine_suspect_mcap_caches.py`
- `tools/materialize_mcap_from_marcap_yearly.py`
- `tools/run_kr1000_backtest.py`
- `tools/run_kr1000_validation_gate.py`
- `kr_pykrx_client.py`
- `kr_pipeline.py`
- `run_local.py`
- `tests/test_kr1000_data_repair_tools.py`
- `tests/test_kr1000_data_store.py`
- `tests/test_kr1000_validation_gate.py`
- `CHANGELOG.md`
- `SESSION_HANDOFF.md`
- `docs/KR1000_GITHUB_OPERATIONS.md` if updated before commit

## Data Repair Completed

The previous mcap leakage blocker is cleared.

Actions completed:

- Quarantined `54` suspect duplicate mcap snapshots and `51` derived avg-value
  proxy caches.
- Quarantine manifest:
  `G:\내 드라이브\kr_quant_engine\outputs\mcap_quarantine_20260606.json`
- Added and used `tools/materialize_mcap_from_marcap_yearly.py`.
- Downloaded local yearly marcap sources for `2015`, `2016`, and `2026`.
- Materialized true PIT mcap caches from yearly marcap:
  - `2015-2016`: `24` monthly snapshots
  - `2017-2025`: `36` monthly snapshots
  - `2026`: `6` monthly snapshots through `2026-06-04`
- `KOSDAQ GLOBAL` is normalized to `KOSDAQ`; KONEX remains excluded.
- Rebuilt PIT historical mcap:
  `363,680` rows, `148` snapshots, `2015-01-30` through `2026-06-04`.
- Materialized 2015-2017 avg-value proxy caches and refreshed 2026 proxies.

Current data audit:

```bash
py -3 tools\audit_data_integrity.py --as-of 2026-06-04
```

Result: Critical `0`, High `0`, Medium `0`.

## Scored Panel / OOS Coverage

Current canonical warm-up scored panel:

`G:\내 드라이브\kr_quant_engine\feature_store\scored_panel_v0_2016-01-01_2026-06-04_2026-06-05-p1-pit-data-audit.parquet`

Coverage:

- `159,005` rows
- `126` monthly signals
- first signal `2016-01-29`
- last signal `2026-06-04`
- no missing months

Forward-label enriched panel for P_MB training:

`G:\내 드라이브\kr_quant_engine\outputs\scored_panel_v0_2016_20260604_forward_labels.parquet`

Official P_MB OOS picks:

`G:\내 드라이브\kr_quant_engine\outputs\p_mb_oos_picks_purged_3sleeve_2018_20260604_latest.csv`

Coverage audit:

- `3,150` rows
- pick window `2017-10-31` through `2026-06-04`
- official target coverage `102/102` months
- first official target month `2018-01`
- last official target month `2026-06`
- coverage pass `true`

Validation coverage command:

```bash
py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --skip-daily-check --skip-backtests --require-pmb-oos-coverage --pmb-oos-picks "G:\내 드라이브\kr_quant_engine\outputs\p_mb_oos_picks_purged_3sleeve_2018_20260604_latest.csv" --out-dir outputs\kr1000_validation_after_pmb_oos_coverage
```

Result: no blockers, data/scored-panel/P_MB OOS coverage passed.

## Official 8y Performance Result

Official production preset still fails badly.

Validation command:

```bash
py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --skip-daily-check --periods official_8y --profiles pmb_pre_surge --strategy-ab --require-pmb-oos-coverage --pmb-oos-picks "G:\내 드라이브\kr_quant_engine\outputs\p_mb_oos_picks_purged_3sleeve_2018_20260604_latest.csv" --price-panel "G:\내 드라이브\kr_quant_engine\outputs\kr1000_bt_2018_20260604_component_ab_top20_price_panel.parquet" --out-dir outputs\kr1000_validation_official_8y_strategy_ab_oos_union_prices
```

Result: `failed_performance`.

Official `pmb_defensive_mdd_gate`:

- CAGR `-0.31%`
- MDD `-28.60%`
- excess CAGR `-18.88%`
- Sharpe `0.04`
- IR `-0.99`
- broker rule pass `true`
- P_MB OOS coverage pass `true`

Other strategy A/B:

- `pmb_pre_surge default`: CAGR `3.99%`, MDD `-35.87%`
- `pmb_mid_rank_7_23 no-leverage MDD gate`: CAGR `4.66%`, MDD `-35.37%`

Component A/B command:

```bash
py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --skip-daily-check --periods official_8y --profiles full --component-ab --require-pmb-oos-coverage --pmb-oos-picks "G:\내 드라이브\kr_quant_engine\outputs\p_mb_oos_picks_purged_3sleeve_2018_20260604_latest.csv" --price-panel "G:\내 드라이브\kr_quant_engine\outputs\kr1000_bt_2018_20260604_component_ab_top20_price_panel.parquet" --out-dir outputs\kr1000_validation_official_8y_component_ab_oos
```

Best component CAGR:

- `pmb_mid_rank_7_23 default`: CAGR `6.65%`, MDD `-44.12%`

Conclusion: the ledger/data harness is working; the active bottleneck is
signal quality, not coverage or execution simulation.

## Price Panel Notes

`tools/run_kr1000_backtest.py` was fixed to stop requesting prices after the
official `as_of` date. The prior `+10d` request forced network fetching beyond
`marcap_2026` coverage and caused 8y backtests to time out.

Reusable price panels:

- P_MB defensive top-20:
  `G:\내 드라이브\kr_quant_engine\outputs\kr1000_bt_2018_20260604_pmb_defensive_oos_price_panel.parquet`
- Component A/B top-20 union:
  `G:\내 드라이브\kr_quant_engine\outputs\kr1000_bt_2018_20260604_component_ab_top20_price_panel.parquet`

## Daily Broker Readiness

Daily broker readiness is still not production-ready because actual holdings
evidence is missing:

- expected canonical path: `DATA_ROOT/state/current_holdings.csv`
- accepted raw exports: `current_holdings_raw.csv`, `current_holdings_raw.tsv`,
  `broker_holdings.csv`, `broker_holdings.tsv`, `holdings_export.csv`,
  `holdings_export.tsv`

Do not treat daily trade plans as actionable until current holdings are present
and non-empty.

## Tests Run In This Work

- `py -3 tests\test_kr1000_data_repair_tools.py` -> 13 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 10 passed, 0 failed.
- `py -3 tests\test_kr1000_data_store.py` -> 11 passed, 0 failed.
- `py -3 -m py_compile tools\run_kr1000_backtest.py` -> passed.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04` -> Critical `0`,
  High `0`, Medium `0`.

Before committing, still run:

```bash
py -3 tests\smoke_test.py --quick
py -3 tests\smoke_test.py
```

## Next Engineering Steps

Do not optimize exposure first; that already worsens MDD. Improve signal
quality in this order:

1. Inspect why `full`, `rs_only`, `rs_flow`, and `rs_flow_technical` all have
   identical 8y metrics. That suggests disabled/zero-filled flow and technical
   columns or score-profile collapse.
2. Diagnose P_MB OOS ranking quality by year and regime, especially the
   2018-2019 and 2025-2026 extension.
3. Build a hybrid score that uses P_MB only as a candidate prior, then requires
   positive KOSPI200-relative strength and trend confirmation before buy.
4. Add sector/theme RS and theme-break exits after stock-level RS works.
5. Add macro gross-exposure and KOSDAQ/KOSPI sleeve scaling only after the
   base signal has positive excess CAGR.

