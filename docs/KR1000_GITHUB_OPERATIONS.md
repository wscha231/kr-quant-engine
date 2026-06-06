# KR1000 GitHub Operations

## Objective

KR1000 Leader Alpha is accepted only when the official broker-ledger path
passes the current gate:

- 8y+ backtest from `2018-01-01`
- CAGR `>= 35%`
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- `metric_mode = broker_ledger_next_close`
- `fill_mode = next_close`

Do not treat vectorized or next-open runs as production metrics.

## GitHub Workflows

Production automation is split into three lanes:

1. `Daily KR1000 Broker Check` keeps the trading bridge alive after each KRX
   close.
2. `KR1000 Data Update and Validation` updates the data store and runs the
   official data/readiness/backtest gates.
3. `Quarterly Backtest` keeps the long-horizon diagnostic report available for
   slower review cycles.

`KR1000 Data Update and Validation`

- Weekday light run after KRX close:
  - refresh latest market-cap/PIT snapshot and price-derived liquidity caches
  - append a latest scored snapshot after refresh and before broker readiness
  - skip full `avg_value_60d`
  - run data-integrity and daily broker readiness gates
  - sync refreshed PIT/cache/output artifacts back to GDrive
- Weekly full run:
  - refresh market data
  - incrementally rebuild `scored_panel_v0` through the latest observable close
  - run `run_local.py` with collector enabled by default, so DART and
    fundamental-derived feature inputs can be refreshed when API secrets and
    caches are available
  - run official 8y broker-ledger validation
  - run component/challenger A/B: `full`, `rs_only`, `rs_flow`,
    `rs_flow_technical`, `legacy_p1_blended`, `pmb_pre_surge`,
    `hybrid_pmb_rs`

The default full GitHub run preserves caches and appends only missing
rebalance dates when a compatible prior `scored_panel_v0` exists. Use the
manual `force_full_rebuild=true` workflow input only when an engine-version
change or suspected cache corruption requires a from-scratch rebuild.

`Daily KR1000 Broker Check`

- Lightweight daily operating bridge.
- Syncs model metadata from GDrive, refreshes latest market/PIT data, appends a
  latest scored snapshot, then evaluates current holdings.
- Produces the latest current-holdings trade plan.
- It may be `blocked` when the scored panel is stale or the data audit finds a
  critical issue. Fix data freshness/PIT leakage first; do not record official
  performance from a blocked run.

`Quarterly Backtest`

- Keeps the legacy quarterly report.
- Also runs a KR1000 validation diagnostic without hard-coded end dates.

## Required Secrets

- `RCLONE_CONFIG_GDRIVE`: rclone config text with `gdrive:` remote.
- `DART_API_KEY`: DART Open API key.
- `BOK_ECOS_API_KEY`: BOK ECOS API key.
- Optional `SLACK_WEBHOOK_URL` for legacy notification workflows.

## Data Store Layout

The canonical data root is Google Drive path `gdrive:kr_quant_engine`, mounted
locally through `KR_DATA_DIR` when available. The setup tool creates and audits
the required layout:

```bash
python tools/setup_kr1000_data_store.py
```

Required folders:

- `cache_pykrx`
- `cache_dart`
- `cache_macro`
- `cache_misc`
- `data_raw`
- `data_pit`
- `feature_store`
- `models`
- `outputs`
- `outputs_advisor`
- `backtest_results`
- `state`

Private account files such as `state/current_holdings.csv` stay gitignored.
Only `state/current_holdings.example.csv` is copied into the data store.

## Data Update Contract

Daily light automation is allowed to update market/PIT/readiness artifacts
without rebuilding every full feature:

- `cache_pykrx/mktcap_ALL_YYYYMMDD.parquet`
- `data_pit/historical_mcap.parquet`
- `data_pit/listed_history.parquet`
- `cache_misc/avg_value_60d_YYYYMMDD.parquet` when explicitly requested
- `feature_store/scored_panel_v0_*_<latest>_*.parquet`
- daily broker check JSON/Markdown/trade-plan outputs

When `models/classifier_latest.cbm` and `classifier_latest_metrics.json` are
available, the latest daily snapshot uses `--classifier-mode auto` to add
live-only P_MB probabilities. Missing classifier features are filled from each
ticker's prior full-feature scored-panel row with `rebalance_date < as_of`.
This is PIT-safe for live readiness because it does not use same-day or future
rows, but it is still not official backtest evidence.

Weekly full automation is the path for official performance evidence. It runs
the collector-backed rebuild path and can update price, DART, macro,
fundamental-derived feature, model, and scored-panel caches before broker
backtests. If the weekly run is too slow, first improve cache reuse and scoped
backfill; do not substitute a liquidity-only latest snapshot for official
CAGR/MDD evidence.

Mixed historical/latest scored panels are expected. New latest-readiness rows
may add columns such as `eligible_final`; older historical rows with
`eligible_final=NaN` must fall back to PIT membership (`in_kr1000`) and must not
drop out of historical ranking.

## Manual Runs

Light daily gate:

```bash
python tools/setup_kr1000_data_store.py
python tools/run_kr1000_validation_gate.py --refresh-data --skip-avg-value-refresh --skip-backtests
```

Fast latest-readiness snapshot, for clearing stale signal blockers without a
full feature backfill:

```bash
python tools/build_latest_kr1000_scored_snapshot.py --as-of <latest-trading-date> --no-rs
python tools/run_kr1000_validation_gate.py --as-of <latest-trading-date> --skip-backtests
```

This path is for daily broker readiness only. With a classifier available,
`--no-rs` creates `latest_fast_liquidity_only_live_pmb` rows and ranks the
latest candidates by `score_profile=pmb_pre_surge`. Without a classifier it
falls back to `latest_fast_liquidity_only` rows. Do not use either latest-only
path as official CAGR/MDD evidence.

Light validation with the same ordering GitHub uses:

```bash
python tools/run_kr1000_validation_gate.py --refresh-data --skip-avg-value-refresh --build-latest-snapshot --skip-backtests
```

Full rebuild and official validation:

```bash
python tools/setup_kr1000_data_store.py
python tools/run_kr1000_validation_gate.py --refresh-data --rebuild-scored-panel --component-ab --strategy-ab
```

Forced full rebuild, for cache invalidation only:

```bash
python tools/run_kr1000_validation_gate.py --refresh-data --rebuild-scored-panel --full-rebuild --component-ab --strategy-ab
```

Dry-run command manifest:

```bash
python tools/run_kr1000_validation_gate.py --component-ab --dry-run
```

P_MB defensive broker-ledger diagnostic:

```bash
python tools/run_kr1000_backtest.py --start 2020-01-01 --end 2024-12-31 --score-profile pmb_pre_surge --gross-exposure 0.70 --hard-stop-loss-pct 0.10 --portfolio-dd-ladder --portfolio-dd-thresholds "-0.10,-0.18,-0.24" --portfolio-dd-scales "0.80,0.60,0.35" --top-holdings 20 --buy-rank-threshold 20 --hold-rank-threshold 40 --save-scored-panel
```

As of the 2026-06-05 drawdown-ladder pass, this diagnostic produced CAGR
`25.38%`, MDD `-24.00%`, Sharpe `1.23`, IR `1.00`, and KOSPI200 excess
`+23.12%` on the available 2020-2024 P_MB OOS window. It is the current best
broker-ledger challenger, but it does not satisfy the official CAGR `>= 35%`
target and is not an 8y official pass.

Daily hard-exit disabled A/B on the same P_MB OOS window worsened to CAGR
`21.64%`, MDD `-31.22%`, Sharpe `1.00`. Keep daily hard-exit enabled until a
new signal component proves otherwise in the same broker harness.

As of the 2026-06-06 live-PMB automation pass, the latest `2026-06-04`
readiness snapshot can score 1000 KR1000 rows with `classifier_latest.cbm`,
using `110/110` aligned features and `102` carry-forward features from prior
full-feature rows. The daily broker check completed with
`score_profile=pmb_pre_surge`, but this remains live readiness evidence, not an
official 8y CAGR/MDD pass.

## How Other Agents Should Improve Performance

1. Check `SESSION_HANDOFF.md` first.
2. Inspect the latest `kr1000_validation_gate_*/kr1000_validation_gate.json`.
3. If data gate is blocked, fix data freshness/PIT leakage before tuning.
4. If data gate passes but performance fails, compare component A/B:
   - `rs_only`
   - `rs_flow`
   - `rs_flow_technical`
   - `legacy_p1_blended`
   - `pmb_pre_surge`
   - `hybrid_pmb_rs`
   - `full`
5. Only change factor weights or features after identifying which component
   improves CAGR without breaking MDD.
6. Run at least:

```bash
python tests/smoke_test.py --quick
python tests/test_kr1000_data_store.py
python tests/test_kr1000_leader.py
python tests/test_kr1000_validation_gate.py
python tools/run_kr1000_validation_gate.py --component-ab --strategy-ab --dry-run
```

7. When comparing broker strategy settings, use the runner CLI overrides rather
   than editing code:
   - `--gross-exposure`
   - `--hard-stop-loss-pct`
   - `--buy-rank-threshold`
   - `--hold-rank-threshold`
   - `--min-notional-krw`
   - `--slippage-bp`
   - `--disable-daily-hard-exit`
   - `--portfolio-dd-ladder`
   - `--portfolio-dd-thresholds`
   - `--portfolio-dd-scales`

## Current Known Blocker

As of the 2026-06-05 19:12 KST handoff:

- The daily-readiness data blocker is cleared: latest `scored_panel_v0` signal
  is `2026-06-04`, data gate Critical `0`, and daily broker check completed.
- The appended `2026-06-04` rows are liquidity-only readiness rows, not
  full-feature backtest rows.
- The schema-union regression from `eligible_final=NaN` on historical rows is
  fixed and covered by `tests/test_kr1000_leader.py`.
- Full score fails after proper NAV sizing (`CAGR -5.61%`, MDD `-50.01%` on
  the available 2019-2024 window).
- P_MB OOS plus `pmb_defensive_mdd_gate` is the current best broker-ledger
  challenger, but it is still below the official CAGR target.

The next production step is a faster full-feature 2025-current backfill, then
component/strategy A/B toward CAGR `>= 35%` under the broker-ledger/MDD gate.
Avoid more exposure-only experiments until the signal panel is richer.
