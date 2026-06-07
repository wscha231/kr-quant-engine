# KR1000 GitHub Operations

## Objective

KR1000 Leader Alpha is accepted only when the official broker-ledger path
passes the current gate:

- 8y+ backtest from `2018-01-01`
- CAGR `>= 30%`
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- `metric_mode = broker_ledger_next_close`
- `fill_mode = next_close`

Do not treat vectorized or next-open runs as production metrics. The active
objective remains CAGR `>= 30%` with MDD no worse than `-25%`. CAGR `>= 35%`
is a stretch target, not the official pass gate.

## Current Signal Diagnosis

The data and broker-ledger harness are usable, but the strategy has not passed
the official performance gate. A June 2026 component A/B repair found that
schema-union score columns could carry all-zero placeholders; these are now
recomputed from live source columns. After the repair, component profiles no
longer collapse to identical rankings, but the 2018-current official 8y
broker-ledger results still fail. Treat further work as signal-quality research:
improve P_MB OOS ranking, then add RS/flow/technical confirmation and regime
scaling only after positive excess CAGR appears.

Latest P_MB OOS audit command:

```bash
py -3 tools\analyze_pmb_oos_quality.py --start 2018-01-01 --end 2026-06-04 --out-dir outputs\pmb_oos_quality_realized_2018_20260604
```

When the default daily price panel is available, this report uses realized
next-rebalance holding returns with broker-like next-close timing instead of
sparse forward labels. The 2026-06-07 realized audit found `2,438` realized
rows out of `2,708` P_MB OOS rows over `102` months. The all-P_MB realized
mean was only `0.758%` per holding period with median `-1.606%`, and the best
tested rank-window broker grid still failed (`r7_23_antirs` top15: CAGR
`6.235%`, MDD `-34.18%`). Recent RS/momentum remains weak confirmation:
`rs_3m_nonpos` had lower loss risk than `rs_3m_pos`, while `rs_3m` realized
return correlation was `-0.0076`. The new `pmb_mid_rank_regime` and
`pmb_mid_tech_regime` challengers are diagnostic profiles only; both still
fail the official 8y broker-ledger gate.

The follow-up pre-entry/risk pass preserved P_MB OOS sleeve columns in the
broker path and blocked sparse OOS merge fallback leakage. With a supplied PIT
OOS picks file, non-picked rows now receive zero P_MB generated probabilities
instead of inheriting any panel-side live/classifier value. This makes official
P_MB broker diagnostics stricter and closer to the documented OOS protocol.

The same pass added `pmb_pre_entry`, `pmb_pre_entry_defensive`,
`pmb_pre_entry_blend`, and `pmb_pre_entry_blend_regime`. These are still
challengers, not production. The best MDD defensive result was
`pmb_pre_entry_blend_regime` top15 with CAGR `1.64%`, MDD `-23.80%`; it passes
the MDD threshold but fails CAGR/excess return badly. A PIT realized-history
reranker sidecar (`tools/build_pmb_realized_rerank_picks.py`) also failed:
reranked top20 produced CAGR `2.30%`, MDD `-39.30%`. The current evidence says
simple P_MB pre-entry filtering and linear realized-history reranking are not
enough to reach the target.

A broader realized-history reranker was then tested through
`tools/build_pmb_broad_realized_rerank_picks.py`. It trains pre-embargo
return/loss models from the full scored KR1000 panel, then scores only the
PIT-safe P_MB OOS candidates for each month. Coverage passed for all `102/102`
official months from 2018-01 through 2026-06, but broker-ledger next-close
results still failed: top20 CAGR `2.86%`, MDD `-38.83%`; top15 CAGR `-0.84%`,
MDD `-41.34%`; top20 with DD ladder CAGR `-2.08%`, MDD `-29.03%`; best tested
broad variant `base_plus_loss` CAGR `3.32%`, MDD `-38.62%`. Do not repeat
linear broad realized reranking as the next alpha path; rebuild P_MB label
definitions and false-positive/risk modeling first.

The next leakage-safe false-positive audit (`tools/analyze_pmb_false_positives.py`)
confirmed that the current feature set is not yet strong enough for a P_MB risk
gate. On `2,438` realized P_MB OOS rows, the OOS `p_good_oos` model reached
only AUC `0.510` against good trades and `p_bad_oos` reached only AUC `0.538`
against bad trades; existing `p_risk` was worse at AUC `0.492`. Sparse filter
variants also failed in the broker ledger: `filter_def_hi_bench_pos` top20
produced CAGR `3.15%`, MDD `-60.29%`; `filter_def_hi_no_trend_bench_pos` top20
produced CAGR `3.54%`, MDD `-61.39%`. Treat this as evidence that the P_MB
label definitions need to be rebuilt, not that another exposure filter is
needed.

The strict pre-entry score-mode pass added configurable P_MB OOS composites to
the official builder and validation gate. `balanced` preserves the legacy
`0.5 * p_pre_entry + 0.5 * p_continuation` blend, while `strict_pre_entry`
uses `p_pre_entry * (1 - p_continuation) * (1 - p_risk)` so post-surge
continuation/overheat candidates are penalized before the broker ledger sees
them. A strict buffer-2 OOS file covered all `102/102` official months, but the
broker-ledger top20 test still failed: CAGR `4.87%`, MDD `-38.76%`, excess
CAGR `-13.70%`, Sharpe `0.358`. This improves the prior strict run only
slightly and confirms that the next work should redesign labels/features rather
than just changing exposure or score weights.

The `pmb_pullback_recovery_regime` profile is the first current 8y challenger
that clears the drawdown gate with full P_MB OOS coverage. It uses
`bench_ret_1m <= -0.01` and `bench_ret_3m >= 0.0` as an as-of benchmark regime
mask, then applies the P_MB pre-entry blend only inside those months. The
2018-current broker-ledger top20 result was CAGR `6.57%`, MDD `-17.82%`,
excess CAGR `-12.00%`, Sharpe `0.846`, with average cash weight `88.32%`.
This proves the MDD problem can be controlled, but the profile is too defensive
to meet the CAGR/excess return gate.

The follow-up `pmb_recovery_trend_value_regime` keeps that pullback-recovery
guard and adds a value-filtered trend sleeve: `bench_ret_3m >= 0.0387` plus
`valuation_score` above the P_MB-selected monthly median. It ranks with direct
P_MB probabilities, `0.60 * p_pre_surge + 0.40 * p_pre_entry - 0.20 * p_risk`.
This is the best current full-window P_MB broker-ledger challenger under the
MDD gate: top20 CAGR `9.76%`, MDD `-19.06%`, excess CAGR `-8.81%`, Sharpe
`0.763`; top15 CAGR `9.81%`, MDD `-18.08%`. It is still not production-pass
because CAGR and KOSPI200 excess are too low.

The next non-P_MB pass promoted `kr1000_technical_mcap_regime` as the current
best MDD-safe KR1000-wide challenger. It buys technical leaders only when
KOSPI200 3-month return is positive and the stock is in the top 40% of KR1000
by both market cap and 60-day trading value. With the tested drawdown ladder
(`-0.06,-0.12,-0.20` -> `0.85,0.65,0.35`), the 2018-current broker-ledger
top20 result improved to CAGR `13.66%`, MDD `-22.04%`, excess CAGR `-4.90%`,
Sharpe `0.918`, IR `-0.320`, and average cash weight `63.81%`. This is better
than the P_MB-only profiles but still fails the official CAGR, excess, Sharpe,
and IR gates. Treat it as the new MDD-safe challenger baseline, not as a
production-pass strategy.

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
  - build purged 3-sleeve P_MB OOS picks for the same validation run
  - run official 8y broker-ledger validation
  - run component/challenger A/B: `full`, `rs_only`, `rs_flow`,
    `rs_flow_technical`, `legacy_p1_blended`, `pmb_pre_surge`,
    `pmb_pre_entry`, `pmb_pre_entry_defensive`, `pmb_pre_entry_blend`,
    `pmb_pre_entry_blend_regime`, `pmb_pullback_recovery_regime`,
    `pmb_recovery_trend_value_regime`, `pmb_mid_rank_7_23`,
    `pmb_mid_rank_regime`, `pmb_mid_tech_regime`,
    `kr1000_technical_mcap_regime`, `hybrid_pmb_rs`

The default full GitHub run preserves caches and appends missing rebalance
dates when a compatible prior `scored_panel_v0` exists, but it will widen the
rebuild start back to `2018-01-01` if the latest compatible panel starts later.
Use the manual `force_full_rebuild=true` workflow input only when an
engine-version change or suspected cache corruption requires a from-scratch
2016 rebuild.

`Daily KR1000 Broker Check`

- Lightweight daily operating bridge.
- Syncs model metadata from GDrive, refreshes latest market/PIT data, appends a
  latest scored snapshot, then evaluates current holdings.
- Produces the latest current-holdings trade plan.
- It may be `blocked` when the scored panel is stale or the data audit finds a
  critical issue. Fix data freshness/PIT leakage first; do not record official
  performance from a blocked run.
- It is also `blocked` when the actual current-holdings evidence is missing or
  empty. By default the tool looks for
  `DATA_ROOT/state/current_holdings.csv` first, then project-local
  `state/current_holdings.csv`. Use `--allow-empty-holdings` only for research
  dry-runs; production readiness must be based on actual account holdings.

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

Private account files such as `DATA_ROOT/state/current_holdings.csv` stay
gitignored. Only `state/current_holdings.example.csv` is copied into the data
store.

To feed actual account holdings into daily readiness, place either the
canonical file or a raw broker export in the GDrive `state` folder:

- canonical: `state/current_holdings.csv`
- raw exports auto-imported by GitHub workflows:
  `state/current_holdings_raw.csv`, `state/current_holdings_raw.tsv`,
  `state/broker_holdings.csv`, `state/broker_holdings.tsv`,
  `state/holdings_export.csv`, `state/holdings_export.tsv`

The import step writes or validates `state/current_holdings.csv` before the
daily broker check:

```bash
python tools/import_current_holdings.py --input state/broker_holdings.csv --out state/current_holdings.csv --as-of <latest-trading-date> --account-id broker
```

Common English and Korean broker headers are normalized into the canonical
schema. The import fails when no positive-share positions remain.

## Data Update Contract

Daily light automation is allowed to update market/PIT/readiness artifacts
without rebuilding every full feature:

- `cache_pykrx/mktcap_ALL_YYYYMMDD.parquet`
- `data_pit/historical_mcap.parquet`

### PIT mcap repair from marcap yearly files

When the data audit flags duplicate or stale `mktcap_ALL_YYYYMMDD.parquet`
snapshots, quarantine suspect caches before recording official backtest
metrics:

```bash
python tools/quarantine_suspect_mcap_caches.py --dry-run
python tools/quarantine_suspect_mcap_caches.py
```

Then rebuild monthly PIT mcap snapshots from local yearly marcap parquet files:

```bash
python tools/materialize_mcap_from_marcap_yearly.py --start-year 2015 --end-year 2025
python tools/materialize_mcap_from_marcap_yearly.py --start-year 2026 --end-year 2026 --max-date <latest-observable-date> --overwrite
python tools/materialize_avg_value_proxy_caches.py --start 2015-01-01 --end <latest-observable-date>
python tools/audit_data_integrity.py --as-of <latest-observable-date>
```

Use `--max-date` for current-year marcap files. Official backtests must not
materialize or request prices after the validation `as_of` date.

For 2018-current official P_MB OOS coverage, keep a warm-up scored panel from
2016-current and target the coverage gate from 2018:

```bash
python run_local.py --quick --start-date 2016-01-01 --end-date <latest-observable-date> --portfolio-size 20 --panel-only --no-collector --no-forward-labels --incremental-fill-order earliest
python tools/enrich_scored_panel_forward_labels.py --panel <scored_panel_2016_current.parquet> --label-as-of <latest-observable-date> --out outputs/scored_panel_v0_2016_forward_labels.parquet
python tools/build_pmb_oos_picks.py --panel outputs/scored_panel_v0_2016_forward_labels.parquet --target-start 2018-01-01 --target-end <latest-observable-date> --out outputs/p_mb_oos_picks_purged_3sleeve_latest.csv --fail-on-coverage-gap
```
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
fundamental-derived feature, model, purged P_MB OOS picks, and scored-panel
caches before broker backtests. If the weekly run is too slow, first improve
cache reuse and scoped backfill; do not substitute a liquidity-only latest
snapshot for official CAGR/MDD evidence.

`tools/audit_data_integrity.py` also checks whether distant
`cache_pykrx/mktcap_ALL_YYYYMMDD.parquet` snapshots are byte-for-byte
identical without explicit carry-forward provenance. Treat this as a critical
PIT/data-leakage failure. Historical current-list or FDR fallback snapshots
must be quarantined or replaced with a true PIT source before any 8y broker
metric is recorded.

Validation-gate scored-panel rebuilds run `run_local.py --panel-only
--no-forward-labels` by default. This materializes the feature panel without
the legacy P0 backtest or expensive forward target labels. Forward labels are
used only for P_MB risk-sleeve labeling and are excluded from classifier
features by the `forward_` prefix.

When a usable full-feature scored panel already exists but lacks those target
labels, use `tools/enrich_scored_panel_forward_labels.py` instead of rebuilding
all features. `tools/run_kr1000_validation_gate.py --enrich-forward-labels`
runs that bridge before P_MB OOS generation and passes the enriched scored
panel into both the OOS builder and broker backtests.

The enrichment bridge defaults to cache-only price loading. Use
`--fetch-missing-prices` only when provider/network fetches are intended. Use
`--label-as-of <date>` to prevent incomplete future horizons from being labeled;
rows whose full forward horizon is not observable are cleared back to NaN.

Official production pass/fail uses the locked `pmb_defensive_mdd_gate` strategy
preset when `--strategy-ab` is run. The `full` score profile remains a baseline
gate for diagnosis. P_MB and hybrid jobs cannot pass the official gate unless
the P_MB OOS coverage audit passes the 8y PIT-safe coverage check.

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

Import or validate current holdings manually:

```bash
python tools/import_current_holdings.py --input <broker-export.csv> --as-of <latest-trading-date> --account-id <account-name>
python tools/import_current_holdings.py --input <broker-export.csv> --dry-run
```

Light validation with the same ordering GitHub uses:

```bash
python tools/run_kr1000_validation_gate.py --refresh-data --skip-avg-value-refresh --build-latest-snapshot --skip-backtests
```

Full rebuild and official validation:

```bash
python tools/setup_kr1000_data_store.py
python tools/run_kr1000_validation_gate.py --refresh-data --rebuild-scored-panel --build-pmb-oos-picks --component-ab --strategy-ab
```

Forced full rebuild, for cache invalidation only:

```bash
python tools/run_kr1000_validation_gate.py --refresh-data --rebuild-scored-panel --build-pmb-oos-picks --full-rebuild --component-ab --strategy-ab
```

Dry-run command manifest:

```bash
python tools/run_kr1000_validation_gate.py --build-pmb-oos-picks --component-ab --strategy-ab --dry-run
```

Dry-run with existing scored-panel forward-label enrichment:

```bash
python tools/run_kr1000_validation_gate.py --build-pmb-oos-picks --enrich-forward-labels --component-ab --strategy-ab --dry-run
```

Enrich an existing scored panel without a full feature rebuild:

```bash
python tools/enrich_scored_panel_forward_labels.py --label-as-of <latest-observable-date> --out feature_store/scored_panel_v0_forward_labels.parquet --audit-json outputs/scored_panel_forward_labels.json
```

Backfill historical market-cap cache gaps safely:

```bash
python tools/backfill_mcap_cache_gaps.py --start 2016-01-01 --end <latest-trading-date> --dry-run
python tools/backfill_mcap_cache_gaps.py --start 2016-01-01 --end <latest-trading-date>
```

This tool disables FDR current-list fallback. If pykrx returns empty for an
old date, the date is left failed rather than saving current listings into a
historical PIT cache.

Materialize PIT-safe carried-forward mcap proxy caches when pykrx is source
blocked:

```bash
python tools/materialize_mcap_carry_forward_caches.py --start 2016-01-01 --end <latest-trading-date> --dry-run
python tools/materialize_mcap_carry_forward_caches.py --start 2016-01-01 --end <latest-trading-date>
```

This writes missing `mktcap_ALL_YYYYMMDD.parquet` files by carrying forward
only the latest prior PIT snapshot, never a future/current listing. The output
keeps `mcap_snapshot_source`, `mcap_snapshot_source_date`,
`mcap_snapshot_true_source_date`, and `mcap_snapshot_carry_days` provenance so
future diagnostics can separate true pykrx snapshots from PIT-safe proxy
snapshots.

Repair stale scored-panel DART period metadata:

```bash
python tools/repair_scored_panel_fundamental_metadata.py --dry-run
python tools/repair_scored_panel_fundamental_metadata.py
```

This bridge is for cached scored panels created before
`kr_features.sanitize_fundamental_period_metadata()` existed. It repairs only
the audit-trail `fundamentals_period_end` metadata using `rcept_dt` as the PIT
authority. A full scored-panel rebuild is still preferred before official
performance evidence.

Materialize PIT-safe avg-value proxy caches:

```bash
python tools/materialize_avg_value_proxy_caches.py --start 2025-01-01 --end <latest-trading-date> --dry-run
python tools/materialize_avg_value_proxy_caches.py --start 2025-01-01 --end <latest-trading-date>
```

This writes `avg_value_60d_YYYYMMDD.parquet` using the PIT mcap snapshot
`value` column, not true 60-day OHLCV-derived average value. The output keeps
`avg_value_source=mktcap_value_proxy:<YYYYMMDD>` so downstream diagnostics can
separate true liquidity caches from proxy caches.

Build purged P_MB OOS picks without overwriting the legacy research CSV:

```bash
python tools/build_pmb_oos_picks.py --target-start 2018-01-01 --target-end <latest-trading-date> --out outputs/p_mb_oos_picks_purged_3sleeve_latest.csv
```

P_MB defensive broker-ledger diagnostic:

```bash
python tools/run_kr1000_backtest.py --start 2020-01-01 --end 2024-12-31 --score-profile pmb_pre_surge --gross-exposure 0.70 --hard-stop-loss-pct 0.10 --portfolio-dd-ladder --portfolio-dd-thresholds "-0.10,-0.18,-0.24" --portfolio-dd-scales "0.80,0.60,0.35" --top-holdings 20 --buy-rank-threshold 20 --hold-rank-threshold 40 --save-scored-panel
```

As of the 2026-06-05 drawdown-ladder pass, this diagnostic produced CAGR
`25.38%`, MDD `-24.00%`, Sharpe `1.23`, IR `1.00`, and KOSPI200 excess
`+23.12%` on the available 2020-2024 P_MB OOS window. It remains a defensive
MDD-safe reference, but it is below the official CAGR `>= 30%` gate and is not
an 8y official pass.

P_MB mid-rank broker-ledger challenger:

```bash
python tools/run_kr1000_backtest.py --start 2020-01-01 --end 2024-12-31 --score-profile pmb_mid_rank_7_23 --gross-exposure 1.0 --hard-stop-loss-pct 0.10 --portfolio-dd-ladder --portfolio-dd-thresholds "-0.10,-0.18,-0.24" --portfolio-dd-scales "0.80,0.60,0.35" --top-holdings 20 --buy-rank-threshold 20 --hold-rank-threshold 40 --save-scored-panel
```

This challenger uses PIT-safe P_MB OOS ranks `7..23` only. The 2020-2024
diagnostic reproduced CAGR `28.15%`, MDD `-22.18%`, Sharpe `1.29`, and
KOSPI200 excess `+25.88%` in the same broker harness. It is the best known
2020-2024 broker-ledger challenger, but it is still below the official CAGR
`>= 30%` gate and needs 2018-current OOS coverage before it can be treated as
an official gate candidate.

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
   - `pmb_mid_rank_7_23`
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
python tests/test_pmb_oos_quality.py
python tests/test_pmb_realized_rerank.py
python tests/test_pmb_broad_realized_rerank.py
python tests/test_pmb_false_positive_audit.py
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

As of the 2026-06-07 08:05 KST handoff:

- The data-integrity blocker is cleared: `tools/audit_data_integrity.py --as-of
  2026-06-04` reports Critical `0`, High `0`, Medium `0`.
- The daily broker check now correctly blocks when actual holdings evidence is
  missing. Current local status is `blocked` because
  `G:\내 드라이브\kr_quant_engine\state\current_holdings.csv` is absent, even
  though the trade-plan artifact is still generated for inspection.
- The appended `2026-06-04` rows are liquidity-only readiness rows, not
  full-feature backtest rows.
- Full scored-panel rebuilds now add `forward_min_return_1m` /
  `forward_return_1m`. Existing full-feature panels can be bridged with
  `tools/enrich_scored_panel_forward_labels.py` so the purged 3-sleeve P_MB
  risk sleeve stops falling back to zero before a complete rebuild is ready.
- The 2026-06-06 cache-only bridge enriched the current GDrive panel to
  `34,744` fully observed label-ready rows across `2019-01` to `2024-12`.
  Incomplete `2026-06` readiness rows were cleared by `--label-as-of
  2026-06-06`.
- The full-panel forward-label P_MB diagnostic is not a production challenger:
  observed-mask defensive broker replay for `2020-2024` produced CAGR `4.26%`,
  MDD `-28.38%`, Sharpe `0.38`, below the legacy P_MB defensive result
  `25.38%` CAGR / `-24.00%` MDD.
- The workflow-audit false positive is fixed: full validation-gate workflows
  count as broker-backtest automation.
- PIT-safe avg-value proxy caches were materialized for `2025-01-31` through
  `2026-03-31`.
- Historical mcap pykrx backfill is source-blocked in this environment, so
  missing month-end mcap caches were materialized with carried-forward PIT
  proxies. `historical_mcap.parquet` now has `366,782` rows and `137`
  snapshots.
- The stale scored-panel DART metadata audit trail was repaired in a separate
  `_fundmeta_repaired.parquet` panel: `293` period-after-rcept rows and `113`
  period-after-signal rows were reduced to `0`.
- The schema-union regression from `eligible_final=NaN` on historical rows is
  fixed and covered by `tests/test_kr1000_leader.py`.
- Full score fails after proper NAV sizing (`CAGR -5.61%`, MDD `-50.01%` on
  the available 2019-2024 window).
- P_MB OOS now has official 2018-current coverage, but realized
  next-rebalance holding-return diagnostics show weak ranking quality:
  `2,438/2,708` rows have realized observations, all-P_MB mean return is only
  `0.758%`, median is `-1.606%`, and the best rank-window broker grid tested
  so far (`r7_23_antirs`, top15) produced CAGR `6.235%` with MDD `-34.18%`.
- Pre-entry/risk filters improve drawdown but not return. The best MDD
  challenger from the 2026-06-07 pass, `pmb_pre_entry_blend_regime` top15,
  produced CAGR `1.64%`, MDD `-23.80%`, excess CAGR `-16.93%`.
- PIT realized-history reranking did not fix the signal. The selected-only
  reranked top20 broker run produced CAGR `2.30%`, MDD `-39.30%`.
- Broad KR1000 realized-history reranking also failed despite full official
  coverage. The broad top20 run produced CAGR `2.86%`, MDD `-38.83%`; the
  best tested broad variant, `base_plus_loss`, produced CAGR `3.32%`, MDD
  `-38.62%`.
- Leakage-safe false-positive modeling is currently too weak for production:
  `p_good_oos` AUC `0.510`, `p_bad_oos` AUC `0.538`, existing `p_risk` AUC
  `0.492`. Sparse filter replays worsened drawdown, with MDD around `-60%`.
- Strict P_MB score-mode replay improved only marginally and still failed:
  `strict_pre_entry` buffer-2 top20 produced CAGR `4.87%`, MDD `-38.76%`,
  excess CAGR `-13.70%`.
- `pmb_pullback_recovery_regime` fixes MDD but not CAGR: top20 produced CAGR
  `6.57%`, MDD `-17.82%`, excess CAGR `-12.00%`, average cash weight `88.32%`.
- `pmb_recovery_trend_value_regime` is the current best MDD-safe P_MB
  challenger: top20 produced CAGR `9.76%`, MDD `-19.06%`, excess CAGR
  `-8.81%`; top15 produced CAGR `9.81%`, MDD `-18.08%`.

The next production step is to provide/sync actual
`DATA_ROOT/state/current_holdings.csv` for the daily broker readiness path, then
rebuild the P_MB label design itself. In particular, inspect fold-level false
positives, add label definitions that separate pre-surge winners from ordinary
high-volatility stocks, and prove loss probability has realized correlation
before adding exposure. CAGR `>= 35%` remains the stretch target after the
official `>= 30%` gate is cleared. Avoid more exposure-only experiments until
the realized signal-quality audit improves.
