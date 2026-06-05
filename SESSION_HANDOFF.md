# Session Handoff - Single Inbox

## Current Status - 2026-06-05 15:10 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation` with
GitHub/GDrive automation, data-store setup, broker validation, component A/B,
and strategy A/B support.

The official user target remains unmet:

- Official target: 8y+ broker-ledger CAGR `>= 35%`, MDD `>= -25%`,
  KOSPI200 excess CAGR `> 0`, Sharpe `> 1.0`, IR `> 0.5`.
- Current best broker-ledger challenger:
  `pmb_defensive_mdd_gate`, 2020-01-31 to 2025-01-09,
  CAGR `25.38%`, MDD `-24.00%`, Sharpe `1.23`, IR `1.00`,
  KOSPI200 excess `+23.12%`.
- This is not an official 8y pass because the scored panel currently covers
  only `2019-01-31` to `2024-12-30`, and CAGR is below `35%`.

## What Changed In The Latest Pass

Added portfolio-level drawdown ladder support to the broker-ledger engine:

- `kr1000_leader.portfolio_drawdown_exposure_scale()`
- `portfolio_drawdown_ladder_enabled`
- `portfolio_drawdown_ladder_thresholds`
- `portfolio_drawdown_ladder_scales`
- daily NAV now records `portfolio_drawdown` and `peak_nav`
- metrics record `avg_gross_exposure_effective` and
  `min_gross_exposure_effective`

Added runner CLI:

- `--portfolio-dd-ladder`
- `--portfolio-dd-thresholds`
- `--portfolio-dd-scales`

Added validation strategy preset:

- `KR1000_STRATEGY_AB_PRESETS["pmb_defensive_mdd_gate"]`
- score profile: `pmb_pre_surge`
- gross exposure: `0.70`
- hard stop: `0.10`
- portfolio DD thresholds: `-0.10,-0.18,-0.24`
- portfolio DD scales: `0.80,0.60,0.35`

GitHub full-mode KR1000 validation now appends `--strategy-ab` whenever
`--component-ab` is enabled.

## Latest Performance Evidence

Same P_MB OOS source, broker-ledger next-close:

- Before rank/NAV fixes: `CAGR -5.61%`, `MDD -34.33%`
- Sparse-rank fix only: `CAGR 9.35%`, `MDD -25.18%`
- Sparse-rank + NAV fix: `CAGR 23.52%`, `MDD -33.07%`
- Prior best MDD-passing defensive run: `CAGR 24.69%`, `MDD -24.74%`
- New `pmb_defensive_mdd_gate`: `CAGR 25.38%`, `MDD -24.00%`,
  Sharpe `1.23`, IR `1.00`, excess `+23.12%`

Standard artifact:

```text
G:\내 드라이브\kr_quant_engine\outputs\kr1000_pmb_oos_mdd_gate_bt_2020_2024
```

Full-score remains a failed signal after proper NAV sizing:

- `G:\내 드라이브\kr_quant_engine\outputs\kr1000_full_navfix_bt_2018_latest`
- actual available window: `2019-01-31` to `2025-01-09`
- CAGR `-5.61%`, MDD `-50.01%`, excess `-7.41%`

## Latest Validation

Passed in this session:

- `py -3 tests/test_kr1000_leader.py` -> 10 passed, 0 failed
- `py -3 tests/test_kr1000_validation_gate.py` -> 3 passed, 0 failed
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --component-ab --strategy-ab --dry-run` -> 12 planned backtests
- `py -3 tests/smoke_test.py --quick` -> 24 passed, 0 failed
- `py -3 tests/smoke_test.py` -> 46 passed, 0 failed
- `py -3 tests/test_kr1000_data_store.py` -> 2 passed, 0 failed
- `git diff --check` -> clean except CRLF warnings

## GitHub State

- Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1
- Latest pushed commit before this handoff: `6769679`
- Current drawdown-ladder changes are local until committed/pushed.
- Smoke Test on `6769679`: success
  https://github.com/wscha231/kr-quant-engine/actions/runs/26996826984
- Quarterly Backtest on latest pushed SHA `6769679` succeeded:
  https://github.com/wscha231/kr-quant-engine/actions/runs/26996905214
- Its KR1000 diagnostic still reports `failed_performance`; data gate has
  Critical `1` because latest scored-panel signal is stale by 521 days
  (`2024-12-30`).
- Existing unrelated dirty file:
  `research/10_theme_lifecycle/leader_themes_per_quarter.csv`.
  Do not stage it unless the user explicitly asks.

## Next Step

1. Run remaining smoke/data-store/diff checks.
2. Commit and push the drawdown-ladder challenger changes.
3. Update PR #1 with the new `25.38% / -24.00%` evidence.
4. After this commit, rerun GitHub Smoke Test automatically via PR push.
5. Rebuild the scored panel through the latest observable KRX close to clear
   the data gate critical issue.
6. Continue alpha-signal work. The broker harness now has a repeatable MDD
   passing challenger, but CAGR still needs roughly +10pp to reach the user's
   `35%` target.
