# Session Handoff - Single Inbox

## Current Status - 2026-06-05 11:34 KST

KR1000 Leader Alpha now has an official validation gate for the user's current
target:

- 8y+ broker-ledger backtest
- CAGR `>= 35%`
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- official metric path: `broker_ledger_next_close`

Production is still blocked by stale scored-panel data. The latest visible
signal remains `2024-12-30`, so no performance result should be treated as
official until the scored panel is rebuilt through the latest fully observable
KRX close.

## What Changed

Score A/B support:
- `kr1000_leader.KR1000_SCORE_PROFILES`
- `kr1000_leader.apply_kr1000_score_profile()`
- supported profiles:
  - `full`
  - `rs_only`
  - `rs_flow`
  - `rs_flow_technical`
  - `legacy_p1_blended`
  - `pmb_pre_surge`
  - `hybrid_pmb_rs`

Backtest runner:
- `tools/run_kr1000_backtest.py` now defaults to `--start 2018-01-01`.
- Added `--score-profile {full,rs_only,rs_flow,rs_flow_technical}`.
- Official production profile remains `full`.

Validation gate:
- Added `tools/run_kr1000_validation_gate.py`.
- It can run or dry-run:
  - optional daily data refresh
  - optional scored-panel rebuild via `run_local.py`
  - data integrity audit
  - daily broker readiness check
  - official 8y backtest
  - 2016-current full backtest
  - 2020-2022, 2023-current, recent-1y stress windows
  - component A/B on the official 8y window
- Outputs:
  - `kr1000_validation_gate.json`
  - `kr1000_validation_gate.md`
  - nested data-audit and broker-check reports

Config:
- `kr1000_leader_alpha_cfg()` now carries official gate fields:
  - `target_cagr_gate = 0.35`
  - `target_mdd_gate = -0.25`
  - `target_excess_cagr_gate = 0.0`
  - `target_sharpe_gate = 1.0`
  - `target_information_ratio_gate = 0.5`
  - `target_min_backtest_years = 8.0`
  - `score_profile = full`

Tests:
- Added `tests/test_kr1000_data_store.py`.
- `tests/test_kr1000_leader.py` now covers score profiles.
- Added `tests/test_kr1000_validation_gate.py`.
- `tests/smoke_test.py` now syntax-checks `tools/*.py`.

Data store:
- Added `tools/setup_kr1000_data_store.py`.
- Canonical local data root resolves to `G:/내 드라이브/kr_quant_engine`.
- Required folders include `cache_pykrx`, `cache_dart`, `cache_macro`,
  `cache_misc`, `data_raw`, `data_pit`, `feature_store`, `models`, `outputs`,
  `outputs_advisor`, `backtest_results`, and `state`.
- Private account files remain gitignored; only
  `state/current_holdings.example.csv` is copied into the data store.

GitHub automation:
- Added `.github/workflows/kr1000_data_update_and_validation.yml`.
- Weekday light mode refreshes latest market/PIT data and runs daily readiness.
- Weekly full mode rebuilds scored panel, refreshes DART/feature-store inputs,
  runs official 8y validation, and runs KR1000 component/challenger A/B.
- All KR1000 GitHub workflows now run `tools/setup_kr1000_data_store.py` before
  refresh/backtest steps.
- Updated `.github/workflows/quarterly_backtest.yml` to call the validation
  gate without a hard-coded end date.
- Added `docs/KR1000_GITHUB_OPERATIONS.md` for other agents.

## Latest Validation

Passed:
- `py -3 tests/smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests/test_kr1000_data_store.py` -> pending after latest edit.
- `py -3 tests/test_kr1000_leader.py` -> 6 passed, 0 failed.
- `py -3 tests/test_kr1000_validation_gate.py` -> 2 passed, 0 failed.
- `py -3 tests/smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools/run_kr1000_backtest.py --help` -> OK.
- `py -3 tools/run_kr1000_validation_gate.py --as-of 2026-06-04 --component-ab --dry-run` -> planned 8 backtests.
- `py -3 tools/run_kr1000_validation_gate.py --as-of 2026-06-04 --refresh-data --rebuild-scored-panel --skip-avg-value-refresh --skip-collector --component-ab --dry-run` -> planned 2 upstream commands + 8 backtests.
- Git branch for publication: `codex/kr1000-github-automation`.

Expected non-zero:
- `py -3 tools/run_kr1000_validation_gate.py --as-of 2026-06-04 --skip-backtests`
  - status: `blocked`
  - audit summary: Critical `1`, High `4`, Medium `0`
  - blockers:
    - `data_integrity_audit_has_critical`
    - `daily_broker_check_failed_rc_2`

Latest generated validation report:
- `G:/내 드라이브/kr_quant_engine/outputs/kr1000_validation_gate_20260604/kr1000_validation_gate.md`

## Next Step

Do not tune factors yet. First make the data gate pass.

Recommended full command:

```bash
py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --refresh-data --rebuild-scored-panel --component-ab
```

If the upstream data refresh is too slow, first run:

```bash
py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --refresh-data --skip-avg-value-refresh --rebuild-scored-panel --skip-collector --skip-backtests
```

Target after rebuild:
- data audit Critical `0`
- daily broker check `completed`
- official 8y backtest can run and produce `official_8y_full/leader_backtest_metrics.json`

Only after that should component A/B be used to improve signal quality toward
CAGR `>= 35%` and MDD `>= -25%`.

## Known Dirty/Untracked Files

Pre-existing or unrelated:
- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`
- `AGENTS.md`

KR1000 implementation files are still untracked unless staged later:
- `.github/workflows/kr1000_data_update_and_validation.yml`
- `kr1000_leader.py`
- `docs/KR1000_GITHUB_OPERATIONS.md`
- `tools/setup_kr1000_data_store.py`
- `tools/run_kr1000_leader.py`
- `tools/run_kr1000_backtest.py`
- `tools/run_kr1000_daily_broker_check.py`
- `tools/refresh_kr1000_daily_data.py`
- `tools/audit_data_integrity.py`
- `tools/run_kr1000_validation_gate.py`
- `tests/test_kr1000_leader.py`
- `tests/test_kr1000_data_store.py`
- `tests/test_kr1000_validation_gate.py`
- `state/`
