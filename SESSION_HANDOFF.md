# Session Handoff - Single Inbox

## Current Status - 2026-06-07 18:18 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest committed base before this pass: `f8d77db`.

Active official target:

- broker-ledger next-close backtest over 8y+
- CAGR `>= 30%`; CAGR `>= 35%` remains a stretch objective
- MDD `>= -25%`
- KOSPI200 excess CAGR `> 0`
- Sharpe `> 1.0`
- Information Ratio `> 0.5`
- daily broker readiness based on actual holdings

Do not mark the active goal complete.

## Working Tree Rules

Do not stage or revert this unrelated dirty file:

- `research/10_theme_lifecycle/leader_themes_per_quarter.csv`

Local diagnostic CSVs/parquets under `outputs/` are research artifacts and are
not meant for git staging unless explicitly requested.

## Current Best Broker-Ledger Result

The active production preset remains:

- strategy preset: `kr1000_technical_value_mcap_mdd_gate`
- score profile: `kr1000_technical_value_mcap_regime`
- top holdings: `10`
- buy threshold: `10`
- hold threshold: `20`
- single stock max weight: `10%`
- gross exposure: `1.00`
- hard stop: `15%`
- portfolio drawdown ladder: disabled
- `sell_before_buy_same_day`: disabled

Latest reproduced default result:

- output `outputs\kr1000_bt_technical_value_mcap_cash_reason_default_2018_20260604`
- years `8.34`
- CAGR `25.46%`
- KOSPI200 CAGR `18.57%`
- excess CAGR `+6.89%`
- MDD `-21.90%`
- Sharpe `1.259`
- Information Ratio `0.251`
- trades `583`
- insufficient-cash orders `127`
- average cash weight `59.88%`
- metric mode `broker_ledger_next_close`, fill mode `next_close`

This remains below the official CAGR `>=30%` and IR `>0.5` goals.

## This Pass

Rank-risk diagnostic:

- Added `tools/analyze_kr1000_rank_risk.py`.
- It is diagnostic-only and uses `forward_return_1m` only to audit hypotheses;
  do not feed its forward-return calculations into official scoring.
- Added `tests.test_kr1000_data_repair_tools.test_rank_risk_diagnostic_feature_bins`.

Diagnostic outputs:

- Top10:
  - output `outputs\kr1000_rank_risk_top10_2018_20260604`
  - rows `145`
  - months `39`
  - mean 1m forward return `2.59%`
  - median `-0.69%`
  - loss rate `50.3%`
- Top20:
  - output `outputs\kr1000_rank_risk_top20_2018_20260604`
  - rows `309`
  - months `40`
  - mean 1m forward return `1.91%`
  - median `-0.84%`
  - loss rate `52.8%`

Important finding:

- Simple overextension penalties are not supported by the full-window evidence.
- In top10 and top20, high `ret_1m`, `rs_1m`, `ret_3m`, and `rs_3m` bins were
  stronger, not weaker.
- High `volume_zscore_50` and high `market_cap` bins were comparatively weak.
- Next scoring work should avoid blindly removing continuation. Focus instead
  on volume-spike/reversal risk and on differentiating weak volume-driven
  rallies from persistent RS leaders.

Failed experiments carried forward:

- Simple mcap/RS/momentum tie-break profiles were weaker than the locked
  liquidity-first profile over the 8y broker ledger.
- Same-day sell-before-buy buying power failed the MDD gate.
- DD-ladder variants reduced CAGR too much.

## Tests Run

- `py -3 -m py_compile tools\analyze_kr1000_rank_risk.py tests\test_kr1000_data_repair_tools.py` -> passed.
- `py -3 tests\test_kr1000_data_repair_tools.py` -> 19 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\analyze_kr1000_rank_risk.py --run-dir outputs\kr1000_bt_technical_value_mcap_cash_reason_default_2018_20260604 --top-n 10 --out-dir outputs\kr1000_rank_risk_top10_2018_20260604` -> completed.
- `py -3 tools\analyze_kr1000_rank_risk.py --run-dir outputs\kr1000_bt_technical_value_mcap_cash_reason_default_2018_20260604 --top-n 20 --out-dir outputs\kr1000_rank_risk_top20_2018_20260604` -> completed.

## Next Engineering Steps

1. Commit and push the rank-risk diagnostic work.
2. Keep the top10/cap10/no-ladder value preset as the active production gate
   until a challenger clears the full official target.
3. Implement a volume-spike/reversal risk profile candidate:
   - keep continuation/RS positives,
   - penalize only abnormal volume spike plus weak persistence,
   - prove it over 2018-current broker-ledger before promoting.
4. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
   Without it, production daily broker readiness must remain blocked.
