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

`KR1000 Data Update and Validation`

- Weekday light run after KRX close:
  - refresh latest market-cap/PIT snapshot
  - skip full `avg_value_60d`
  - run data-integrity and daily broker readiness gates
  - sync refreshed PIT/cache/output artifacts back to GDrive
- Weekly full run:
  - refresh market data
  - incrementally rebuild `scored_panel_v0` through the latest observable close
  - update DART/fundamental-derived feature store when the full rebuild needs it
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
- Produces the latest current-holdings trade plan.
- It may be `blocked` when the scored panel is stale; that is expected and
  should be fixed by the weekly/full validation workflow.

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

## Manual Runs

Light daily gate:

```bash
python tools/setup_kr1000_data_store.py
python tools/run_kr1000_validation_gate.py --refresh-data --skip-avg-value-refresh --skip-backtests
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

As of the 2026-06-05 14:07 KST handoff:

- The latest `scored_panel_v0` signal is stale (`2024-12-30`).
- Full score fails after proper NAV sizing (`CAGR -5.61%`, MDD `-50.01%` on
  the available 2019-2024 window).
- P_MB OOS plus `pmb_defensive_mdd_gate` is the current best broker-ledger
  challenger, but it is still below the official CAGR target.

The next production step is a full scored-panel rebuild through the latest
observable KRX close, then alpha-signal improvement toward CAGR `>= 35%` under
the broker-ledger/MDD gate.
