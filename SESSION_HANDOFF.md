# Session Handoff - Single Inbox

## Current Status - 2026-06-07 08:50 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest pushed commit before this handoff: `6456375`.
Latest known GitHub Smoke on `6456375` succeeded:
https://github.com/wscha231/kr-quant-engine/actions/runs/27076953261

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
- `tools/build_pmb_broad_realized_rerank_picks.py`
- `tests/test_pmb_broad_realized_rerank.py`
- `CHANGELOG.md`
- `SESSION_HANDOFF.md`
- `docs/KR1000_GITHUB_OPERATIONS.md`

## What Changed

Added a broad KR1000 realized-history P_MB reranker:

- `tools/build_pmb_broad_realized_rerank_picks.py`
- trains realized return/loss models on the full scored KR1000 panel
- scores only PIT-safe P_MB OOS candidates for each test month
- uses pre-embargo rows only: `rebalance_date <= test_day - embargo_months`
- writes sparse P_MB picks compatible with `tools/run_kr1000_backtest.py`
- does not overwrite legacy or official P_MB OOS picks

The broad reranker uses broker-like realized next-rebalance labels:

- signal date: monthly `rebalance_date`
- entry: next available close after signal date
- exit: next available close after the next signal date
- labels: `analysis_return`, `analysis_min_return`, `analysis_holding_days`

Added `tests/test_pmb_broad_realized_rerank.py` and wired it into GitHub Smoke.

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

## Latest Broad Realized Reranker Output

Command pattern:

```bash
py -3 tools\build_pmb_broad_realized_rerank_picks.py --scored-panel <GDrive scored panel> --price-panel <GDrive price panel> --pmb-oos-picks <GDrive P_MB OOS picks> --target-start 2018-01-01 --target-end 2026-06-04 --out outputs\p_mb_oos_picks_broad_realized_rerank_2018_20260604.csv --k-per-month 30 --embargo-months 3 --min-train-rows 5000 --pmb-weight 0.04 --return-weight 1.0 --risk-penalty 0.10 --fail-on-coverage-gap
```

Output:

- `outputs/p_mb_oos_picks_broad_realized_rerank_2018_20260604.csv`
- rows: `3,060`
- window: `2018-01-31` to `2026-06-04`
- coverage: `102/102` official months passed
- broad observed labels: `102,227/159,005`
- features used: `18/20`

## Latest Broker-Ledger Results

Still failed:

- `pmb_pre_surge` strict OOS top20: CAGR `3.99%`, MDD `-35.87%`,
  excess CAGR `-14.57%`, Sharpe `0.302`
- `pmb_pre_entry` top20: CAGR `3.37%`, MDD `-29.00%`, excess CAGR `-15.20%`
- `pmb_pre_entry_defensive` top15 + DD ladder: CAGR `3.38%`, MDD `-26.29%`,
  excess CAGR `-15.19%`
- `pmb_pre_entry_blend_regime` top15: CAGR `1.64%`, MDD `-23.80%`,
  excess CAGR `-16.93%`
- selected-only realized-history rerank top20: CAGR `2.30%`, MDD `-39.30%`
- broad realized-history rerank top20: CAGR `2.86%`, MDD `-38.83%`,
  excess CAGR `-15.71%`, Sharpe `0.248`
- broad realized-history rerank top15: CAGR `-0.84%`, MDD `-41.34%`,
  excess CAGR `-19.41%`, Sharpe `0.053`
- broad realized-history rerank top20 + DD ladder: CAGR `-2.08%`,
  MDD `-29.03%`, excess CAGR `-20.64%`, Sharpe `-0.146`
- broad `base_plus_loss` top20: CAGR `3.32%`, MDD `-38.62%`,
  excess CAGR `-15.25%`, Sharpe `0.270`

The target is not met. Current bottleneck is not the broker ledger and not a
simple pre-entry/risk rerank. It is also not fixed by linear broad-universe
realized return/loss reranking. The P_MB label design and false-positive model
need to be rebuilt.

## Tests Run In This Work

- `py -3 -m py_compile tools\build_pmb_broad_realized_rerank_picks.py tests\test_pmb_broad_realized_rerank.py` -> passed.
- `py -3 tests\test_pmb_broad_realized_rerank.py` -> 2 passed, 0 failed.
- `py -3 tools\build_pmb_broad_realized_rerank_picks.py ... --fail-on-coverage-gap` -> passed.

Run before commit:

```bash
py -3 tests\smoke_test.py --quick
py -3 tests\smoke_test.py
py -3 tests\test_kr1000_leader.py
py -3 tests\test_kr1000_validation_gate.py
py -3 tests\test_pmb_realized_rerank.py
py -3 tests\test_pmb_broad_realized_rerank.py
```

## Next Engineering Steps

1. Inspect P_MB false positives by fold and year. Focus on names selected by
   `p_pre_surge` that realized `analysis_return <= -10%` or
   `analysis_min_return <= -15%`.
2. Rebuild P_MB labels so pre-surge winners are separated from ordinary
   high-volatility stocks and post-surge continuation names.
3. Prove the loss/risk label has realized correlation before using it in the
   broker ledger.
4. Add regime features that explain 2018/2022/2024 failures before trying more
   exposure changes.
5. Daily readiness still needs real `DATA_ROOT/state/current_holdings.csv`.
