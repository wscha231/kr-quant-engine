# Session Handoff - Single Inbox

## Current Status - 2026-06-06 14:05 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this pass: `3743b8f`.
Latest GitHub Smoke on `3743b8f` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27051203839

The official user target is now:

- 8y+ broker-ledger CAGR `>= 30%`, MDD `>= -25%`, KOSPI200 excess CAGR `> 0`,
  Sharpe `> 1.0`, IR `> 0.5`.
- `35%` CAGR is a stretch target, not the current official gate.
- Official metrics must be `broker_ledger_next_close` with next-close fills.

Current best broker-ledger challenger remains the available 2020-2024 P_MB OOS
window with `pmb_pre_surge`, gross `0.70`, daily hard-exit enabled, and
portfolio DD ladder:

- CAGR `25.38%`
- MDD `-24.00%`
- Sharpe `1.23`
- IR `1.00`
- KOSPI200 excess `+23.12%`

This is still not an official pass because the P_MB OOS picks cover only
`2020-01-31` to `2024-12-30` and do not satisfy the 8y coverage gate.

## What Changed In This Pass

Re-centered the validation system around leakage-safe 8y P_MB evidence:

- `kr1000_leader_alpha_cfg()` now uses `target_cagr_gate = 0.30`.
- Added `tools/build_pmb_oos_picks.py` as the official purged 3-sleeve P_MB OOS
  picks builder.
- P_MB OOS feature selection excludes forward/future/target/generated columns.
- `run_classifier_retrain.py --purged --embargo-months 9` now actually passes
  purged split controls into `train_entry_classifier()`.
- Removed fold test-set early stopping from `train_entry_classifier()` so the
  test fold is not used for training control.
- `run_kr1000_validation_gate.py` now audits:
  - scored-panel official start coverage;
  - P_MB OOS 8y coverage;
  - P_MB coverage as a required gate for P_MB/hybrid/production jobs.
- Cache-preserving scored-panel rebuild now widens the start to `2018-01-01`
  when the latest compatible cache starts later.
- `pmb_defensive_mdd_gate` is now the official production gate when
  `--strategy-ab` is run. `full` remains a baseline gate.
- GitHub full validation and quarterly backtest now build/use purged P_MB OOS
  picks instead of silently relying on the legacy research CSV.

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

## Validation To Run Before Commit

- `py -3 -m py_compile tools\build_pmb_oos_picks.py tools\run_kr1000_validation_gate.py tools\run_classifier_retrain.py kr_multibagger_classifier.py`
  -> passed.
- `py -3 tests\test_walkforward.py` -> 10 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 7 passed, 0 failed.
- `py -3 tests\test_kr1000_leader.py` -> 11 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --build-pmb-oos-picks --component-ab --strategy-ab --dry-run --out-dir H:\kr_quant_engine\outputs\kr1000_validation_dryrun_pmb_oos`
  -> planned `build_pmb_oos_picks`, 12 broker backtests, target CAGR `0.30`,
  all backtests include `--pmb-oos-picks`.
- `py -3 tools\build_pmb_oos_picks.py --target-start 2018-01-01 --target-end 2026-06-04 --iterations 20 --out H:\kr_quant_engine\outputs\p_mb_oos_picks_purged_3sleeve_smoke.csv --coverage-json H:\kr_quant_engine\outputs\p_mb_oos_picks_purged_3sleeve_smoke.coverage.json`
  -> 1800 picks across 60 months, `2020-01-31` to `2024-12-30`, split gap
  `10` months, coverage failed `60/102` months as expected. Risk sleeve was
  skipped because the current panel lacks `forward_min_return_1m` /
  `forward_return_1m`.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04`
  -> Critical `0`, High `4`, Medium `1`.

## Git / Worktree Notes

- Existing unrelated dirty file remains:
  `research/10_theme_lifecycle/leader_themes_per_quarter.csv`
  Do not stage it unless explicitly asked.
- Backtest/test output directories under `outputs/` are evidence only and are
  gitignored.

Files intended for this commit:

- `.github/workflows/kr1000_data_update_and_validation.yml`
- `.github/workflows/quarterly_backtest.yml`
- `kr_config.py`
- `kr_multibagger_classifier.py`
- `tools/build_pmb_oos_picks.py`
- `tools/run_classifier_retrain.py`
- `tools/run_kr1000_validation_gate.py`
- `tests/test_kr1000_validation_gate.py`
- `tests/test_walkforward.py`
- `CHANGELOG.md`
- `SESSION_HANDOFF.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`

## Next Step

1. Validate and commit/push the gate realignment and purged OOS tooling.
2. Run or schedule full scored-panel rebuild from at least `2018-01-01`,
   preferably `2016-01-01`.
3. Generate purged P_MB OOS picks for `2018-current`.
4. Run official validation with `--component-ab --strategy-ab`.
5. If CAGR remains below `30%`, improve signal quality in this order:
   hybrid `pmb+RS+flow+technical`, sector/theme RS exits, then macro regime
   sleeve scaling. Avoid exposure-only experiments until signal coverage
   improves.
