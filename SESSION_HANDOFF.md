# Session Handoff - Single Inbox

## Current Status - 2026-06-07 08:05 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this handoff: `5ee59fa`.
Latest known GitHub Smoke on `5ee59fa` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27075818477

Active official target:

- broker-ledger next-close backtest over 8y+
- CAGR `>= 30%` official gate; `>= 35%` remains stretch objective
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- daily broker readiness based on actual holdings

Do not mark the active goal complete.

## Current Local Change Set

Do not stage or revert this unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

Intended files in this handoff:

- `.github/workflows/smoke_test.yml`
- `tools/analyze_pmb_oos_quality.py`
- `tests/test_pmb_oos_quality.py`
- `CHANGELOG.md`
- `SESSION_HANDOFF.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`

## What Changed

`tools/analyze_pmb_oos_quality.py` now accepts an optional daily price panel
through `--price-panel`. When available, the audit computes realized
next-rebalance holding returns using broker-like timing:

1. signal after `rebalance_date` close
2. entry at the next available close
3. exit at the next available close after the next monthly signal date

The report adds:

- `realized_entry_date`
- `realized_exit_date`
- `realized_entry_close`
- `realized_exit_close`
- `realized_holding_return`
- `realized_min_return`
- `realized_max_return`
- `realized_holding_days`
- `analysis_return`
- `analysis_min_return`
- `analysis_return_source`

Summaries and correlations use `analysis_return`. That means realized returns
are used when a price panel is present; sparse forward labels remain the
fallback when no realized observations exist.

`tests/test_pmb_oos_quality.py` covers realized-return timing and analysis
source selection without depending on external price/cache data. GitHub Smoke
now runs that test file.

## Current Data/Leakage State

The PIT data repair remains valid:

- `tools\audit_data_integrity.py --as-of 2026-06-04` previously returned
  Critical `0`, High `0`, Medium `0`.
- Canonical scored panel covers `2016-01-29` through `2026-06-04` with no
  missing monthly signals.
- Official purged 3-sleeve P_MB OOS picks cover `102/102` official target
  months from `2018-01` through `2026-06`.

Daily broker readiness is still not production-ready because actual holdings
evidence is missing. Required canonical path:

- `DATA_ROOT/state/current_holdings.csv`

## Latest Realized P_MB OOS Quality Result

Command:

```bash
py -3 tools\analyze_pmb_oos_quality.py --start 2018-01-01 --end 2026-06-04 --out-dir outputs\pmb_oos_quality_realized_2018_20260604
```

Output summary:

- P_MB OOS rows: `2,708`
- months: `102`
- observed forward-label rows: `534`
- observed realized rows: `2,438`
- `analysis_return_source`: `realized_next_rebalance`

Realized filter summary:

- all P_MB OOS: mean `0.758%`, median `-1.606%`, loss `< -10%` rate
  `23.46%`
- rank `7..23`: mean `1.336%`, median `-1.331%`
- benchmark 3m positive: mean `1.493%`, median `-1.364%`
- rank `7..23` + benchmark 3m positive: mean `1.799%`, median `-0.942%`
- `rs_3m_nonpos`: mean `0.560%`, median `-0.810%`, lower loss risk than
  `rs_3m_pos`
- `trend_template_pass`: mean `0.640%`, median `-2.922%`, worse drawdown
  profile
- rank `7..23` + large-cap half: mean `1.551%`, median `-0.363%`

Realized factor correlations are weak:

- `market_cap`: `+0.0399`
- `p_pre_surge`: `+0.0346`
- `rs_1m`: `+0.0175`
- `rs_score`: `+0.0122`
- `technical_score`: `-0.0027`
- `rs_3m`: `-0.0076`
- `rs_6m`: `-0.0086`
- `pmb_oos_rank`: `-0.0112`

Risk diagnostic: high RS/momentum still worsens 10% loss risk. `p_pre_surge`
and `market_cap` modestly reduce loss risk; `technical_score`,
`trend_template_score`, `avg_trading_value_60d`, `rs_1m`, `rs_3m`, and
`rs_score` increase loss risk in this sample.

## Latest Broker-Ledger Results

Still failed:

- best rank-window grid variant `r7_23_antirs`, top15: CAGR `6.235%`,
  MDD `-34.18%`, excess CAGR `-12.33%`, Sharpe `0.421`
- `r7_23_large`, top20: CAGR `4.984%`, MDD `-35.01%`
- `r13_23_antirs`, top10: CAGR `4.400%`, MDD `-37.74%`
- earlier `pmb_mid_rank_regime` top20: CAGR `1.92%`, MDD `-38.10%`
- earlier `pmb_mid_tech_regime` top20: CAGR `1.79%`, MDD `-36.89%`

The target is not met. Current bottleneck is P_MB label/ranking quality plus
drawdown-risk control, not the broker ledger harness.

## Tests Run In This Work

- `py -3 -m py_compile tools\analyze_pmb_oos_quality.py tests\test_pmb_oos_quality.py` -> passed.
- `py -3 tools\analyze_pmb_oos_quality.py --start 2018-01-01 --end 2026-06-04 --out-dir outputs\pmb_oos_quality_realized_2018_20260604` -> passed.
- `py -3 tests\test_pmb_oos_quality.py` -> 3 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tests\test_kr1000_leader.py` -> 15 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 10 passed, 0 failed.

## Next Engineering Steps

1. Inspect P_MB training labels and selected features by fold. The realized
   OOS picks have positive mean but negative median, so ranking quality is the
   main blocker.
2. Build a loss-aware P_MB risk sleeve that directly predicts next-rebalance
   `realized_min_return` / `loss < -10%`, then test it in the same broker
   harness.
3. Rework confirmation logic away from naive positive RS/trend. Current data
   says high RS raises loss risk in P_MB OOS.
4. Test large-cap/liquidity capacity as a risk cap, not as a standalone alpha
   source.
5. Daily readiness still needs real `DATA_ROOT/state/current_holdings.csv`.
