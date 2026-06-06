# Session Handoff - Single Inbox

## Current Status - 2026-06-06 16:36 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this local change set: `12497aa`.
Latest GitHub Smoke on `12497aa` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27055771369

The active user target has been realigned to:

- 8y+ broker-ledger CAGR `>= 30%`
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- IR `> 0.5`
- daily account/broker readiness based on actual holdings

Official metrics must be `broker_ledger_next_close` with next-close fills.
CAGR `>= 35%` is now a stretch target only, not the official pass gate.

## Current Local Change Set

This local change set has been implemented and locally validated. It still
needs commit/push and PR refresh:

- `kr1000_leader_alpha_cfg()["target_cagr_gate"]` changed from `0.35` to
  `0.30`.
- `tools/run_kr1000_validation_gate.py` now renders the official target from
  the configured threshold instead of hard-coding `35%`.
- Added score profile `pmb_mid_rank_7_23`, which scores only PIT-safe P_MB OOS
  ranks `7..23`.
- Added strategy A/B preset `pmb_mid_rank_no_leverage_mdd_gate`, using
  no-leverage gross `1.0`, daily hard stop `10%`, and the existing portfolio
  drawdown ladder.
- Production pass/fail remains tied to locked preset
  `pmb_defensive_mdd_gate`; the mid-rank preset is a challenger, not the
  official gate.
- Tests and `docs/KR1000_GITHUB_OPERATIONS.md` were updated for the `30%`
  official target and the new challenger.

Do not stage the unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

## Data / Performance Facts

- Canonical scored panel in GDrive:
  `feature_store/scored_panel_v0_2019-01-01_2026-06-04_2026-06-05-p1-pit-data-audit_forward_labels.parquet`
- That panel starts at `2019-01-31`, so it still fails the official 8y start
  gate for `2018-01-01`.
- Default legacy P_MB OOS picks:
  `research/06_walkforward_baselines/p_mb_v1_oos_picks.csv`
  covers `2020-01-31` to `2024-12-30`, 60 months, 30 picks/month.
- Legacy P_MB defensive broker-ledger diagnostic, 2020-2024:
  CAGR `25.38%`, MDD `-24.00%`, Sharpe `1.23`, IR `1.00`,
  KOSPI200 excess `+23.12%`.
- Newly identified P_MB mid-rank challenger, 2020-2024:
  CAGR about `28.15%`, MDD about `-22.18%`, Sharpe about `1.29`,
  KOSPI200 excess about `+25.88%`.
- Neither P_MB diagnostic is an official pass yet because both lack 8y OOS
  coverage and remain below the official `30%` CAGR target.
- Full score 2019-2025 broker-ledger run remains poor:
  CAGR `-5.61%`, MDD `-50.01%`.
- Full-panel forward-label P_MB diagnostics are weak and must not replace the
  legacy P_MB challenger:
  - raw 3-sleeve: CAGR `2.44%`, MDD `-31.99%`
  - pre-entry only: CAGR `3.26%`, MDD `-32.39%`
  - no-risk combo: CAGR `2.60%`, MDD `-30.82%`
  - observed-mask 3-sleeve: CAGR `4.26%`, MDD `-28.38%`

## Validation Completed

Completed on 2026-06-06 16:41 KST:

- `py -3 -m py_compile kr1000_leader.py tools\run_kr1000_validation_gate.py kr_config.py`
- `py -3 tests\test_kr1000_leader.py` -> 12 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 7 passed, 0 failed.
- `py -3 tests\test_walkforward.py` -> 15 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --build-pmb-oos-picks --component-ab --strategy-ab --dry-run --out-dir H:\kr_quant_engine\outputs\kr1000_validation_dryrun_cagr30_midrank`
  -> planned 14 broker backtests, target CAGR `0.30`, including
  `pmb_mid_rank_7_23` and `pmb_mid_rank_no_leverage_mdd_gate`.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04`
  -> Critical `0`, High `4`, Medium `1`.

Data-audit high findings still need follow-up before official 8y performance:

- mcap cache has month-level gaps >45 days.
- avg_trading_value cache has gaps >45 days.
- 113 scored rows have `fundamentals_period_end` after `rebalance_date`.
- 293 scored rows have `fundamentals_period_end` after `fundamentals_rcept_dt`;
  audit note says this is likely non-December fiscal-year metadata mapped as
  calendar-year metadata, while `rcept_dt` PIT filtering is checked separately.

## Reproduction Command

The mid-rank diagnostic was reproduced with:

```bash
py -3 tools\run_kr1000_backtest.py --start 2020-01-01 --end 2024-12-31 --score-profile pmb_mid_rank_7_23 --price-panel "G:\내 드라이브\kr_quant_engine\outputs\kr1000_pmb_legacy_current_baseline_bt_2020_2024\leader_price_panel.parquet" --gross-exposure 1.0 --hard-stop-loss-pct 0.10 --portfolio-dd-ladder --portfolio-dd-thresholds "-0.10,-0.18,-0.24" --portfolio-dd-scales "0.80,0.60,0.35" --top-holdings 20 --buy-rank-threshold 20 --hold-rank-threshold 40 --out-dir "G:\내 드라이브\kr_quant_engine\outputs\kr1000_pmb_mid_rank_7_23_g100_bt_2020_2024"
```

Result: CAGR `28.15%`, MDD `-22.18%`, Sharpe `1.29`, KOSPI200 excess
`+25.88%`, 1,276 trades.

## Next Production Steps

1. Commit and push the `30%` gate realignment plus mid-rank challenger after
   validation.
2. Rebuild full-feature scored panel from at least `2018-01-01`, preferably
   `2016-01-01`.
3. Generate purged P_MB OOS picks for `2018-current` with active risk sleeve.
4. Run official validation with `--component-ab --strategy-ab`.
5. If CAGR remains below `30%`, improve signal quality in this order:
   `pmb + RS + flow + technical`, sector/theme RS exits, then macro regime
   sleeve scaling. Avoid exposure-only experiments until the 8y signal
   coverage problem is solved.
