# Session Handoff - Single Inbox

## Current Status - 2026-06-07 07:35 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this handoff: `c59e83d`.
Latest known GitHub Smoke on `c59e83d` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27074801422

Active official target:

- broker-ledger next-close backtest over 8y+
- CAGR `>= 30%` official gate; active stretch objective still wants `35%`
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

- `kr1000_leader.py`
- `tools/analyze_pmb_oos_quality.py`
- `tests/test_kr1000_leader.py`
- `tests/test_kr1000_validation_gate.py`
- `CHANGELOG.md`
- `SESSION_HANDOFF.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`

## What Changed

Added two diagnostic P_MB regime profiles:

- `pmb_mid_rank_regime`: P_MB OOS ranks `7..23` only when benchmark 3m return
  is positive.
- `pmb_mid_tech_regime`: same, plus positive `technical_score`.

These profiles use `score_profile_eligible_flag`, now respected by
`compute_leader_scores()`, so a profile can go to cash when there are no
qualified names instead of buying zero-score filler rows.

Added `tools/analyze_pmb_oos_quality.py`, which rebuilds the official prepared
panel, merges PIT-safe purged P_MB OOS picks, and exports by-year,
rank-bucket, filter, and factor-correlation diagnostics.

Existing `pmb_pre_surge` and `pmb_mid_rank_7_23` behavior is preserved. The
cash-off behavior is only in the new challenger profiles.

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

## Latest P_MB OOS Quality Result

Command:

```bash
py -3 tools\analyze_pmb_oos_quality.py --start 2018-01-01 --end 2026-06-04 --out-dir outputs\pmb_oos_quality_2018_20260604
```

Output summary:

- P_MB OOS rows: `2,708`
- months: `102`
- observed forward-label rows: `534`
- all P_MB OOS mean 1m `0.66%`, median `-2.19%`
- rank `7..23` mean 1m `1.32%`, median `-1.85%`
- rank `7..23` + benchmark 3m positive mean 1m `2.61%`, median `-1.58%`
- `trend_template_pass` mean 1m `-0.32%`, average min 1m drawdown worse than
  all P_MB OOS
- P_MB internal factor correlations are negative for recent momentum:
  `rs_6m -0.118`, `rs_3m -0.101`, `rs_score -0.078`

Conclusion: positive RS/trend confirmation is not working for P_MB OOS in the
observed label sample. The next signal work should investigate early reversal,
liquidity/large-cap tilt, and risk labeling rather than adding positive RS.

## Latest Broker-Ledger Results

Still failed:

- `pmb_mid_rank_regime` top20: CAGR `1.92%`, MDD `-38.10%`
- `pmb_mid_tech_regime` top20: CAGR `1.79%`, MDD `-36.89%`
- compact `pmb_mid_rank_7_23` grid best: top15/gross `0.70` CAGR `9.49%`,
  MDD `-38.64%`
- ad-hoc anti-momentum grid best: mid-rank + large-cap tilt top15 CAGR
  `5.52%`, MDD `-40.98%`
- mid-rank + anti-RS top15: CAGR `5.03%`, MDD `-33.16%`

The target is not met. Current bottleneck is P_MB label/ranking quality.

## Tests Run In This Work

- `py -3 -m py_compile kr1000_leader.py tools\analyze_pmb_oos_quality.py tests\test_kr1000_leader.py` -> passed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tests\test_kr1000_leader.py` -> 15 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 10 passed, 0 failed.

## Next Engineering Steps

1. Inspect P_MB training labels and selected features by fold. The OOS picks
   have weak positive mean but negative median, so ranking quality is the main
   blocker.
2. Audit why 2020, 2023, 2025, and 2026 ledger years work better while 2018,
   2019, 2022, and 2024 drag down CAGR/MDD.
3. Test early-reversal and liquidity/large-cap tilt as formal score profiles,
   but only keep them if 2018-current broker-ledger improves both CAGR and MDD.
4. Revisit risk sleeve labeling; `p_risk` is not yet cutting enough drawdown.
5. Daily readiness still needs real `DATA_ROOT/state/current_holdings.csv`.
