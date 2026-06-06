# Session Handoff - Single Inbox

## Current Status - 2026-06-07 06:45 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this handoff: `859abd6`.
Latest known GitHub Smoke on `859abd6` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27073753866

Active official target:

- broker-ledger next-close backtest over 8y+
- CAGR `>= 30%`
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- daily broker readiness based on actual holdings

CAGR `>= 35%` is stretch only. Do not mark the active goal complete.

## Current Local Change Set

Do not stage or revert this unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

Intended files in this handoff:

- `kr1000_leader.py`
- `tests/test_kr1000_leader.py`
- `CHANGELOG.md`
- `SESSION_HANDOFF.md`
- `MASTER_PLAN.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`

## What Changed

The previous component A/B result was misleading. `full`, `rs_only`,
`rs_flow`, and `rs_flow_technical` collapsed because schema-union component
columns such as `rs_score` and `technical_score` existed as NaN/all-zero
placeholders. `add_leader_component_scores()` skipped recomputation and the
profiles degraded into tie-break behavior.

This handoff fixes that by recomputing component scores when the existing
component column is non-informative but source columns have live
cross-sectional variation. The recompute guard is applied to RS, flow,
technical, quality/growth, valuation, theme/sector, and event/governance.

`generate_trade_plan()` also now returns the standard schema for fully empty
diagnostic months, avoiding `reason_code` errors in cash/no-target paths.

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

Accepted raw exports include `current_holdings_raw.csv`, `broker_holdings.csv`,
and `holdings_export.csv`/`.tsv`.

## Latest Official 8y Diagnostic

After component recomputation, component A/B no longer collapses, but official
performance still fails:

- `pmb_mid_rank_7_23 default`: CAGR `6.65%`, MDD `-44.12%`
- `pmb_pre_surge default`: CAGR `3.99%`, MDD `-35.87%`
- `legacy_p1_blended`: CAGR `-0.28%`, MDD `-64.06%`
- `full`: CAGR `-6.44%`, MDD `-64.11%`
- `rs_flow_technical`: CAGR `-16.99%`, MDD `-86.49%`
- `rs_only` / `rs_flow`: CAGR `-21.86%`, MDD `-92.44%`

Regime/technical diagnostics can reduce MDD near or below the `-25%` gate, but
CAGR remains around `4%`. Current bottleneck is still signal quality, not the
ledger harness or PIT coverage.

## Tests Run In This Work

- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tests\test_kr1000_leader.py` -> 14 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 10 passed, 0 failed.
- `py -3 tests\test_walkforward.py` -> 15 passed, 0 failed.

## Next Engineering Steps

Do not optimize exposure first; it controls MDD but does not create alpha.

1. Diagnose P_MB OOS ranking quality by year/regime, especially 2018-2019 and
   2025-2026.
2. Rebuild a production challenger that uses P_MB as a prior, then requires
   positive KOSPI200-relative strength and trend confirmation before buy.
3. Add sector/theme RS and theme-break exits after stock-level RS has positive
   excess CAGR.
4. Add macro gross-exposure and KOSDAQ/KOSPI sleeve scaling only after the
   base signal has positive excess CAGR.
5. Re-run official 2018-current and full 2016-current broker-ledger gates.
