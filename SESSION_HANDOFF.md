# Session Handoff - Single Inbox

## Current Status - 2026-06-05 15:33 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

The official user target remains unmet:

- Official target: 8y+ broker-ledger CAGR `>= 35%`, MDD `>= -25%`,
  KOSPI200 excess CAGR `> 0`, Sharpe `> 1.0`, IR `> 0.5`.
- Current best broker-ledger challenger:
  `pmb_defensive_mdd_gate`, available 2020-2024 P_MB OOS window,
  CAGR `25.38%`, MDD `-24.00%`, Sharpe `1.23`, IR `1.00`,
  KOSPI200 excess `+23.12%`.
- This is not an official pass because CAGR is below `35%` and the current
  scored-panel history still needs to be rebuilt through latest close for the
  official 8y gate.

## What Changed In The Latest Pass

Made the GitHub data/full-validation path cache-preserving by default:

- `kr_universe.find_prior_avg_value_cache()` selects only prior
  `avg_value_60d` caches within `avg_value_fallback_max_days`.
- `kr_universe.compute_avg_trading_value_60d()` can reuse that PIT-safe prior
  cache when the exact dated cache is missing.
- `kr_pipeline.find_incremental_scored_panel_cache()` finds the widest
  same-start scored-panel cache whose filename end date is not after the
  target end date.
- `kr_pipeline.build_scored_panel_v0()` now appends only missing month-ends
  when `reuse_existing_artifacts=True` and
  `scored_panel_incremental_rebuild=True`.
- Default config now includes:
  - `avg_value_refresh_days=3650`
  - `avg_value_fallback_max_days=10`
  - `scored_panel_incremental_rebuild=True`
- GitHub `KR1000 Data Update and Validation` full mode now runs the scored
  panel rebuild with caches preserved by default. Manual dispatch can still
  force a from-scratch rebuild with `force_full_rebuild=true`.

## Latest Validation

Passed in this session:

- `py -3 tests/test_kr1000_data_store.py` -> 4 passed, 0 failed
- `py -3 tests/test_kr1000_validation_gate.py` -> 3 passed, 0 failed
- `py -3 tests/smoke_test.py --quick` -> 24 passed, 0 failed
- `py -3 tests/smoke_test.py` -> 46 passed, 0 failed
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --refresh-data --rebuild-scored-panel --component-ab --strategy-ab --dry-run`
  -> 2 planned commands, 12 planned backtests
- Verified the dry-run `rebuild_scored_panel` command is:

```text
run_local.py --quick --start-date 2016-01-01 --end-date 2026-06-04 --portfolio-size 20
```

- `git diff --check` -> clean except CRLF warnings

## Git / Worktree Notes

- Current branch: `codex/kr1000-github-automation`
- Previous pushed commit before this pass: `2745aa1`
- New cache-preserving refresh changes are local until committed/pushed.
- Existing unrelated dirty file remains:
  `research/10_theme_lifecycle/leader_themes_per_quarter.csv`
  Do not stage it unless the user explicitly asks.

## Next Step

1. Commit and push this cache-preserving GitHub refresh pass.
2. Update PR #1 with the new automation behavior.
3. Let GitHub Smoke Test run on the pushed SHA.
4. Trigger or wait for `KR1000 Data Update and Validation` full mode so the
   scored panel extends through latest observable KRX close.
5. If data gate passes but performance still fails, use the 12-run
   component/strategy A/B manifest to continue signal improvement toward
   CAGR `>= 35%` without breaking MDD `>= -25%`.
