# Session Handoff - Single Inbox

## Current Status - 2026-06-05 18:31 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

The official user target remains unmet:

- Official target: 8y+ broker-ledger CAGR `>= 35%`, MDD `>= -25%`,
  KOSPI200 excess CAGR `> 0`, Sharpe `> 1.0`, IR `> 0.5`.
- Current best broker-ledger challenger remains:
  `pmb_defensive_mdd_gate`, available 2020-2024 P_MB OOS window,
  CAGR `25.38%`, MDD `-24.00%`, Sharpe `1.23`, IR `1.00`,
  KOSPI200 excess `+23.12%`.
- This is not an official pass because CAGR is below `35%`, the available
  P_MB OOS window is not 8y+, and the latest readiness row is liquidity-only.

## What Changed In The Latest Pass

Cleared the stale scored-panel blocker for daily readiness:

- Added `tools/build_latest_kr1000_scored_snapshot.py`.
  - `--no-rs` builds a latest PIT KR1000 liquidity-only readiness snapshot.
  - It appends a new `scored_panel_v0` parquet under the current engine version.
- Generated:

```text
G:\내 드라이브\kr_quant_engine\feature_store\scored_panel_v0_2019-01-01_2026-06-04_2026-06-05-p1-pit-data-audit.parquet
```

- Latest signal date is now `2026-06-04`, not `2024-12-30`.
- Data integrity gate now has Critical `0`.
- Daily broker check now completes:
  - status `completed`
  - signal `2026-06-04`
  - signal age `0`
  - trade plan rows `20`
  - actions: `BUY: 20`

Also fixed operational blockers found while trying to rebuild from GDrive:

- `run_local.py`, `kr_pipeline.py`, and `kr_universe.py` no longer emit
  runtime em dash / arrow characters that crash Windows cp949 output.
- `tools/run_kr1000_validation_gate.py --skip-backtests` returns exit code
  `0` when data and daily broker gates pass.
- Quick scored-panel rebuild now infers the latest scored-panel start date
  instead of blindly starting from `2016-01-01`.
- Quick scored-panel rebuild can cap missing rebalance dates with
  `--rebuild-max-new-months` / `run_local.py --incremental-max-new-months`.
- `avg_value_60d` can fall back to PIT-safe mktcap daily `value` proxy when
  true 60-day cache is missing.

## Latest Validation

Passed in this session:

- `py -3 tools\build_latest_kr1000_scored_snapshot.py --as-of 2026-06-04 --no-rs`
  -> 1000 latest rows, output scored panel through `2026-06-04`
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --skip-backtests`
  -> `completed_no_official_backtest`, data gate passed, exit code `0`
- Data audit summary: Critical `0`, High `4`, Medium `1`, Low `0`
- Daily broker check: completed, signal age `0`, 20 trade-plan rows
- `py -3 tests/test_kr1000_data_store.py` -> 5 passed, 0 failed
- `py -3 tests/test_kr1000_validation_gate.py` -> 4 passed, 0 failed
- `py -3 tests/smoke_test.py --quick` -> 24 passed, 0 failed

Important caveat:

- The appended `2026-06-04` scored rows are
  `latest_fast_liquidity_only`. They are suitable for daily readiness and
  current-holdings trade-plan plumbing, but not for official CAGR/MDD claims.
- Full-score/performance improvement still requires a full-feature backfill or
  a faster cached RS/flow feature builder for 2025-current.

## Git / Worktree Notes

- Current branch: `codex/kr1000-github-automation`
- Previous pushed commit before this pass: `3ab4820`
- New latest-readiness snapshot changes are local until committed/pushed.
- Existing unrelated dirty file remains:
  `research/10_theme_lifecycle/leader_themes_per_quarter.csv`
  Do not stage it unless the user explicitly asks.

## Next Step

1. Run remaining smoke/data-store/diff checks.
2. Commit and push the latest-readiness snapshot pass.
3. Update PR #1 with the new evidence:
   data gate Critical `0`, daily broker check completed, latest signal
   `2026-06-04`.
4. Next performance work:
   - build a faster full-feature latest/backfill path using cached exact
     ticker histories instead of per-ticker network waits;
   - then run component/strategy A/B on full-feature rows;
   - target remains CAGR `>= 35%`, MDD `>= -25%`, broker-ledger next-close.
