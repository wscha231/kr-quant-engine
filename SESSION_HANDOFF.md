# Session Handoff - Single Inbox

## Current Status - 2026-06-06 12:16 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this pass: `3b000ed`.
Latest GitHub Smoke on `3b000ed` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27025568416

The official user target remains unmet:

- Official target: 8y+ broker-ledger CAGR `>= 35%`, MDD `>= -25%`,
  KOSPI200 excess CAGR `> 0`, Sharpe `> 1.0`, IR `> 0.5`.
- Current best broker-ledger challenger remains the available 2020-2024 P_MB
  OOS window with `pmb_pre_surge`, gross `0.70`, daily hard-exit enabled, and
  portfolio DD ladder:
  CAGR `25.38%`, MDD `-24.00%`, Sharpe `1.23`, IR `1.00`,
  KOSPI200 excess `+23.12%`.
- This is not an official pass because CAGR is below `35%`, the available
  P_MB OOS window is not 8y+, and latest live-PMB rows are readiness rows, not
  purged backtest evidence.

## What Changed In The Latest Pass

Improved the daily data-to-broker automation:

- `tools/build_latest_kr1000_scored_snapshot.py` now infers previous KRX close
  when `--as-of` is omitted.
- Added `--classifier-mode {auto,none,require}`.
- In `auto`, the latest snapshot loads `models/classifier_latest.cbm` when
  available and keeps neutral behavior if not.
- Classifier feature alignment now fills missing latest features from each
  ticker's prior full-feature scored-panel row with `rebalance_date < as_of`.
  It intentionally excludes same-day/latest rows to avoid self-feed leakage.
- When live classifier scoring is applied:
  - `--no-rs` latest snapshots use `score_profile=pmb_pre_surge`;
  - cached-RS snapshots use `score_profile=hybrid_pmb_rs`.
- `tools/run_kr1000_daily_broker_check.py` now respects a single embedded
  `score_profile` from the scored panel before recomputing ranks.
- GitHub daily broker workflow now:
  - syncs `models` from GDrive;
  - refreshes latest market/PIT data;
  - builds the latest scored snapshot before broker readiness;
  - syncs `feature_store` back to GDrive;
  - uploads the latest snapshot manifest.
- GitHub light validation now passes `--build-latest-snapshot`, so the gate
  plans `refresh_data` before `build_latest_snapshot`.

## Live Readiness Evidence

Local test snapshot:

```text
py -3 tools\build_latest_kr1000_scored_snapshot.py --as-of 2026-06-04 --no-rs --classifier-mode require --out H:\kr_quant_engine\outputs\kr1000_latest_live_pmb_test.parquet --out-dir H:\kr_quant_engine\outputs\kr1000_latest_live_pmb_test
```

Result:

- latest rows: `1000`
- build mode: `latest_fast_liquidity_only_live_pmb`
- classifier: `classifier_latest.cbm`
- aligned features: `110/110`
- carried features: `102`
- p_pre_surge: min `0.000012`, mean `0.2193`, max `0.9526`
- top-20 are now ranked by P_MB instead of neutral liquidity rank.

Local broker check:

```text
py -3 tools\run_kr1000_daily_broker_check.py --evaluation-date 2026-06-04 --scored-panel H:\kr_quant_engine\outputs\kr1000_latest_live_pmb_test.parquet --current-holdings state\current_holdings.example.csv --out-dir H:\kr_quant_engine\outputs\kr1000_live_pmb_broker_check_test
```

Result:

- status: `completed`
- signal age: `0`
- blockers: `[]`
- score_profile: `pmb_pre_surge`
- trade plan rows: `22`
- actions: `BUY:20`, `SELL:2`

This improves operational daily stock selection, but it is not official
CAGR/MDD evidence.

## Latest Validation

Passed in this pass:

- `py -3 tests\test_kr1000_data_store.py` -> 6 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 4 passed, 0 failed.
- `py -3 tests\test_kr1000_leader.py` -> 11 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 -m py_compile tools\run_kr1000_daily_broker_check.py tools\build_latest_kr1000_scored_snapshot.py tools\run_kr1000_validation_gate.py` -> passed.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --refresh-data --skip-avg-value-refresh --build-latest-snapshot --skip-backtests --dry-run --out-dir H:\kr_quant_engine\outputs\kr1000_validation_dryrun_live_snapshot`
  -> planned commands: `refresh_data`, then `build_latest_snapshot`.

Note: `test_kr1000_data_store.py` and `test_kr1000_validation_gate.py` need
unsandboxed execution in the local Codex desktop environment because sandboxed
Python tempfile cleanup returns `PermissionError`. They pass outside sandbox.

## Data / Performance Facts

- Canonical scored panel in GDrive:
  `feature_store/scored_panel_v0_2019-01-01_2026-06-04_2026-06-05-p1-pit-data-audit.parquet`
- That panel has historical rows through `2024-12-30` plus a latest
  `2026-06-04` readiness row set.
- Default OOS P_MB picks file:
  `research/06_walkforward_baselines/p_mb_v1_oos_picks.csv`
  covers `2020-01-31` to `2024-12-30`, 60 months, 30 picks per month.
- Full score 2019-2025 broker-ledger run remains poor:
  CAGR `-5.61%`, MDD `-50.01%`.
- Current best remains P_MB OOS defensive ladder:
  CAGR `25.38%`, MDD `-24.00%`.

## Git / Worktree Notes

- Current branch: `codex/kr1000-github-automation`
- Existing unrelated dirty file remains:
  `research/10_theme_lifecycle/leader_themes_per_quarter.csv`
  Do not stage it unless the user explicitly asks.
- Backtest/test output directories under `outputs/` are evidence only and are
  gitignored. Do not stage them unless explicitly requested.

Files intended for the next commit:

- `.github/workflows/daily_kr1000_broker_check.yml`
- `.github/workflows/kr1000_data_update_and_validation.yml`
- `tools/build_latest_kr1000_scored_snapshot.py`
- `tools/run_kr1000_daily_broker_check.py`
- `tools/run_kr1000_validation_gate.py`
- `tests/test_kr1000_data_store.py`
- `CHANGELOG.md`
- `SESSION_HANDOFF.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`

## Next Step

1. Run final `git diff --check`, quick smoke, and focused tests.
2. Commit/push the live-PMB daily automation update.
3. Update PR #1 and confirm GitHub Smoke on the new SHA.
4. Next performance work:
   - build purged P_MB OOS coverage beyond 2020-2024, especially 2018-2019 and
     2025-current;
   - build faster full-feature 2025-current backfill using cached ticker
     histories;
   - rerun component A/B on full-feature rows;
   - target remains CAGR `>= 35%`, MDD `>= -25%`, broker-ledger next-close.
