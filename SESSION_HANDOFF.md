# Session Handoff - Single Inbox

## Current Status - 2026-06-07 13:03 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Base commit before this pass: `1fecbda`.

Active official target:

- broker-ledger next-close backtest over 8y+
- CAGR `>= 30%` official gate; CAGR `>= 35%` remains stretch objective
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- daily broker readiness based on actual holdings

Do not mark the active goal complete.

## Working Tree Rules

Do not stage or revert this unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

This pass generated local diagnostic outputs under `outputs/`; they are not
intended for git staging unless explicitly requested.

## What Changed In This Pass

New score profiles:

- `kr1000_technical_rs3_mcap_regime`
- `kr1000_technical_value_mcap_regime`

New locked production challenger:

- strategy preset: `kr1000_technical_value_mcap_mdd_gate`
- score profile: `kr1000_technical_value_mcap_regime`
- top holdings: `12`
- buy threshold: `12`
- hold threshold: `24`
- gross exposure: `0.90`
- hard stop: `15%`
- portfolio drawdown ladder thresholds `-0.10,-0.18,-0.24`
- portfolio drawdown ladder scales `0.95,0.75,0.50`

Validation-gate behavior changes:

- `PRODUCTION_GATE_STRATEGY_PRESET` now tracks
  `kr1000_technical_value_mcap_mdd_gate`.
- P_MB OOS coverage is required only for P_MB score profiles. Non-PMB
  production strategy jobs no longer fail solely because a P_MB OOS artifact is
  unavailable or incomplete.
- Strategy A/B commands now set `--max-rank-for-prices` to at least the preset
  `hold_rank_threshold`, so the current top12/hold24 preset fetches prices for
  rank `1-24` candidates instead of inheriting the CLI default `20`.

## Data/Leakage State

Latest rechecked data audit:

```bash
py -3 tools\audit_data_integrity.py --as-of 2026-06-04
```

Result: Critical `0`, High `0`, Medium `0`.

Official P_MB OOS coverage still needs to be enforced for P_MB diagnostics, but
the current production challenger is non-PMB and uses the PIT scored panel plus
broker-ledger next-close path.

## Latest Broker-Ledger Result

Current best challenger:

```bash
py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile kr1000_technical_value_mcap_regime --top-holdings 12 --max-rank-for-prices 24 --buy-rank-threshold 12 --hold-rank-threshold 24 --gross-exposure 0.90 --hard-stop-loss-pct 0.15 --portfolio-dd-ladder --portfolio-dd-thresholds -0.10,-0.18,-0.24 --portfolio-dd-scales 0.95,0.75,0.50 --out-dir outputs\kr1000_bt_technical_value_mcap_regime_top12_g90_ladder_official_2018_20260604 --save-scored-panel
```

Result:

- years `8.34`
- CAGR `21.86%`
- KOSPI200 CAGR `18.57%`
- excess CAGR `+3.30%`
- MDD `-22.94%`
- Sharpe `1.190`
- Information Ratio `0.093`
- trades `748`
- average cash weight `60.07%`
- metric mode `broker_ledger_next_close`, fill mode `next_close`
- valid for production metric: `True`

This is the first current 8y challenger that clears MDD, KOSPI200 excess, and
Sharpe together. It still fails the official CAGR `>=30%` and IR `>0.5` gates.

Prior locked challenger:

- `kr1000_technical_mcap_mdd_gate`: CAGR `17.60%`, MDD `-23.45%`, excess
  `-0.97%`, Sharpe `1.055`, IR `-0.111`.

Failed/superseded sensitivities:

- `kr1000_technical_rs3_mcap_regime` top15/g90 ladder: CAGR `14.93%`, MDD
  `-25.66%`, excess `-3.64%`, Sharpe `0.91`.
- `kr1000_technical_value_mcap_regime` top15/g90 ladder: CAGR `20.37%`, MDD
  `-25.18%`, excess `+1.80%`, Sharpe `1.14`.
- top13/g90 ladder: CAGR `21.38%`, MDD `-23.84%`, excess `+2.81%`,
  Sharpe `1.16`.
- top10/g90 ladder: CAGR `18.37%`, MDD `-20.33%`, excess `-0.19%`.

## Tests Run

- `py -3 -m py_compile kr1000_leader.py tools\run_kr1000_validation_gate.py tests\test_kr1000_leader.py tests\test_kr1000_validation_gate.py` -> passed.
- `py -3 tests\test_kr1000_leader.py` -> 15 passed, 0 failed.
- `py -3 tests\test_kr1000_validation_gate.py` -> 14 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\test_walkforward.py` -> 16 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04` -> Critical `0`, High `0`, Medium `0`.
- `py -3 tools\run_kr1000_validation_gate.py --as-of 2026-06-04 --component-ab --strategy-ab --dry-run --out-dir outputs\kr1000_validation_dryrun_technical_value_production_v2` -> planned `28` broker backtests and emitted the value production command with `--max-rank-for-prices 24`.
- `py -3 tools\run_kr1000_backtest.py --start 2018-01-01 --end 2026-06-04 --initial-cash 100000000 --score-profile kr1000_technical_value_mcap_regime --top-holdings 12 --max-rank-for-prices 24 --buy-rank-threshold 12 --hold-rank-threshold 24 --gross-exposure 0.90 --hard-stop-loss-pct 0.15 --portfolio-dd-ladder --portfolio-dd-thresholds -0.10,-0.18,-0.24 --portfolio-dd-scales 0.95,0.75,0.50 --out-dir outputs\kr1000_bt_technical_value_mcap_regime_top12_g90_ladder_official_2018_20260604 --save-scored-panel` -> CAGR `21.86%`, MDD `-22.94%`, excess `+3.30%`.

## Next Engineering Steps

1. Do not optimize exposure first. The current top12 value profile already has
   high cash and passes MDD; the remaining gap is CAGR/IR signal quality.
2. Analyze months where `kr1000_technical_value_mcap_regime` beats KOSPI200
   versus loses to it. The low IR means excess return is not stable enough.
3. Add a sector/theme RS exit or confirmation overlay against this top12 value
   profile, not against the weaker P_MB-only profiles.
4. Test market breadth and KOSPI/KOSDAQ regime gating carefully. KOSDAQ-only
   checks were worse, so sleeve allocation must be evidence-led.
5. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
