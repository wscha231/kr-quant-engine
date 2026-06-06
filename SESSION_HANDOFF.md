# Session Handoff - Single Inbox

## Current Status - 2026-06-06 14:30 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit: `df28efb`.
Latest GitHub Smoke on `df28efb` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27053488434

The active user target is:

- 8y+ broker-ledger CAGR `>= 35%`
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- IR `> 0.5`
- daily account/broker readiness based on actual holdings

Official metrics must be `broker_ledger_next_close` with next-close fills.

Current best broker-ledger challenger remains the available 2020-2024 P_MB OOS
window with `pmb_pre_surge`, gross `0.70`, daily hard-exit enabled, and
portfolio DD ladder:

- CAGR `25.38%`
- MDD `-24.00%`
- Sharpe `1.23`
- IR `1.00`
- KOSPI200 excess `+23.12%`

This is not an official pass because CAGR is below `35%` and P_MB OOS picks
cover only `2020-01-31` to `2024-12-30`.

## What Changed After df28efb

Forward risk labels are now being added to the full scored-panel path:

- `kr_config.PHASE4_PMB_TARGET_COLUMNS` adds:
  - `forward_return_1m`
  - `forward_min_return_1m`
- `kr_pipeline.add_forward_return_labels()` computes 1-month forward return
  and intra-horizon minimum return from daily closes.
- `kr_pipeline.build_scored_panel_v0()` applies these labels before saving the
  scored panel when `forward_label_enabled=True`.
- The labels are targets, not features. `tools/build_pmb_oos_picks.py` already
  excludes `forward_` prefixed columns from classifier feature selection.
- `kr1000_leader_alpha_cfg()` target CAGR is back to `0.35`.

This should allow the purged 3-sleeve P_MB risk model to stop falling back to
all-zero `is_risk` after the scored panel is rebuilt.

## Data / Performance Facts

- Canonical scored panel in GDrive:
  `feature_store/scored_panel_v0_2019-01-01_2026-06-04_2026-06-05-p1-pit-data-audit.parquet`
- That panel has historical rows through `2024-12-30` plus latest
  `2026-06-04` readiness rows.
- Official scored-panel start gate currently fails for an 8y run because the
  panel starts at `2019-01-31`, not `2018-01-01`.
- Default legacy P_MB OOS picks:
  `research/06_walkforward_baselines/p_mb_v1_oos_picks.csv`
  covers `2020-01-31` to `2024-12-30`, 60 months, 30 picks/month.
- Full score 2019-2025 broker-ledger run remains poor:
  CAGR `-5.61%`, MDD `-50.01%`.

## Latest Validation

Validation on `df28efb` before the current forward-label edits:

- `py -3 -m py_compile tools\build_pmb_oos_picks.py tools\run_kr1000_validation_gate.py tools\run_classifier_retrain.py kr_multibagger_classifier.py`
  -> passed.
- `py -3 tests\test_walkforward.py` -> 10 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 7 passed, 0 failed.
- `py -3 tests\test_kr1000_leader.py` -> 11 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- GitHub Smoke on `df28efb` -> success.

Validation after current forward-label edits:

- `py -3 -m py_compile kr_pipeline.py kr_config.py tools\run_kr1000_validation_gate.py`
  -> passed.
- `py -3 tests\test_walkforward.py` -> 11 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 7 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --build-pmb-oos-picks --component-ab --strategy-ab --dry-run --out-dir H:\kr_quant_engine\outputs\kr1000_validation_dryrun_cagr35_forward_labels`
  -> planned `build_pmb_oos_picks`, 12 broker backtests, target CAGR `0.35`,
  and all backtests include `--pmb-oos-picks`.
- `git diff --check` -> passed.

## Git / Worktree Notes

- Existing unrelated dirty file remains:
  `research/10_theme_lifecycle/leader_themes_per_quarter.csv`
  Do not stage it unless explicitly asked.
- Backtest/test output directories under `outputs/` are evidence only and are
  gitignored.

Files intended for the next commit:

- `kr_config.py`
- `kr_pipeline.py`
- `tools/run_kr1000_validation_gate.py`
- `tests/test_kr1000_validation_gate.py`
- `tests/test_walkforward.py`
- `tests/smoke_test.py`
- `docs/KR1000_GITHUB_OPERATIONS.md`
- `SESSION_HANDOFF.md`
- `CHANGELOG.md`

## Next Step

1. Validate and commit/push the forward-label and 35% target alignment.
2. Rebuild full-feature scored panel from at least `2018-01-01`, preferably
   `2016-01-01`.
3. Generate purged P_MB OOS picks for `2018-current` with active risk sleeve.
4. Run official validation with `--component-ab --strategy-ab`.
5. If CAGR remains below `35%`, improve signal quality in this order:
   hybrid `pmb+RS+flow+technical`, sector/theme RS exits, then macro regime
   sleeve scaling. Avoid exposure-only experiments until signal coverage
   improves.
