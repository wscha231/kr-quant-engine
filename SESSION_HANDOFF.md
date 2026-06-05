# Session Handoff - Single Inbox

## Current Status - 2026-06-05 19:12 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

The official user target remains unmet:

- Official target: 8y+ broker-ledger CAGR `>= 35%`, MDD `>= -25%`,
  KOSPI200 excess CAGR `> 0`, Sharpe `> 1.0`, IR `> 0.5`.
- Current best broker-ledger challenger remains the available 2020-2024 P_MB
  OOS window with `pmb_pre_surge`, gross `0.70`, daily hard-exit enabled, and
  portfolio DD ladder:
  CAGR `25.38%`, MDD `-24.00%`, Sharpe `1.23`, IR `1.00`,
  KOSPI200 excess `+23.12%`.
- This is not an official pass because CAGR is below `35%`, the available
  P_MB OOS window is not 8y+, and the latest readiness row is
  liquidity-only.

## What Changed In The Latest Pass

Fixed the mixed scored-panel schema regression:

- Latest readiness rows added `eligible_final`.
- Historical scored rows in the concatenated panel therefore had
  `eligible_final=NaN`.
- `compute_leader_scores()` had treated those historical rows as ineligible,
  wiping out historical P_MB ranks.
- Added `kr1000_leader._eligibility_series()` so `eligible_final=NaN` falls
  back to PIT membership via `in_kr1000`.
- Added regression coverage:
  `tests/test_kr1000_leader.py::test_nan_eligible_final_falls_back_to_in_kr1000`.

Recorded broker A/B evidence on the same cached P_MB OOS price panel:

- Current best recheck: CAGR `25.38%`, MDD `-24.00%`, Sharpe `1.23`,
  excess CAGR `+23.12%`, trades `1433`.
- Daily hard-exit disabled: CAGR `21.64%`, MDD `-31.22%`, Sharpe `1.00`.
- N10 concentration, higher gross exposure, no ladder, loose ladder, and
  tighter variants did not beat the current best.
- Conclusion: keep daily hard-exit and DD ladder. The next real improvement
  path is signal/full-feature backfill quality, not exposure-only tuning.

## GitHub / Automation State

GitHub automation exists and should be used as the shared operating system:

- `Daily KR1000 Broker Check`
  - weekdays after KRX close
  - refreshes latest market/PIT data
  - writes daily broker check and current-holdings trade plan artifacts
- `KR1000 Data Update and Validation`
  - weekday light mode: market/PIT/readiness gate
  - weekly full mode: collector-backed rebuild, component/strategy A/B, and
    official broker-ledger validation
  - syncs refreshed caches and outputs back to GDrive when
    `RCLONE_CONFIG_GDRIVE` is present
- `Quarterly Backtest`
  - slower long-horizon diagnostic and artifact retention

Required GitHub secrets:

- `RCLONE_CONFIG_GDRIVE`
- `DART_API_KEY`
- `BOK_ECOS_API_KEY`

The operations contract is documented in `docs/KR1000_GITHUB_OPERATIONS.md`.

## Latest Validation

Passed in this pass:

- `py -3 tests\test_kr1000_leader.py` -> 11 passed, 0 failed.
- `py -3 tests\test_kr1000_data_store.py` -> 5 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 4 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `git diff --check` -> passed.
- P_MB no-hard-exit broker diagnostic completed and showed worse risk/return,
  so defensive rules remain justified.

Note: `test_kr1000_data_store.py` and `test_kr1000_validation_gate.py` needed
unsandboxed execution in the local Codex desktop environment because sandboxed
Python tempfile cleanup returned `PermissionError`. They passed outside the
sandbox.

## Git / Worktree Notes

- Current branch: `codex/kr1000-github-automation`
- Latest pushed commit before this pass: `2558960`
- Intended files to stage:
  - `kr1000_leader.py`
  - `tools/run_kr1000_validation_gate.py`
  - `tests/test_kr1000_leader.py`
  - `CHANGELOG.md`
  - `SESSION_HANDOFF.md`
  - `docs/KR1000_GITHUB_OPERATIONS.md`
- Existing unrelated dirty file remains:
  `research/10_theme_lifecycle/leader_themes_per_quarter.csv`
  Do not stage it unless the user explicitly asks.
- Backtest output directories under `outputs/` are evidence only and should not
  be staged unless a future task explicitly asks to version artifacts.

## Next Step

1. Run the remaining validation checks listed above.
2. Commit and push the schema-union + operations-doc update.
3. Update PR #1 with the new evidence and confirm the GitHub smoke workflow is
   green on the pushed SHA.
4. Next performance work:
   - build faster full-feature 2025-current backfill using cached ticker
     histories;
   - rerun component A/B on full-feature rows;
   - target remains CAGR `>= 35%`, MDD `>= -25%`, broker-ledger next-close.
