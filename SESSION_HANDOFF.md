# Session Handoff - Single Inbox

## Current Status - 2026-06-07 16:31 KST

KR1000 Leader Alpha is on branch `codex/kr1000-github-automation`.
Draft PR: https://github.com/wscha231/kr-quant-engine/pull/1

Latest committed base before this pass: `e0d6e3d`.

Active target:

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

Local diagnostic CSVs/parquets under `outputs/` are research artifacts and are
not meant for git staging unless explicitly requested.

## Current Best Broker-Ledger Result

The active production challenger remains:

- strategy preset: `kr1000_technical_value_mcap_mdd_gate`
- score profile: `kr1000_technical_value_mcap_regime`
- top holdings: `10`
- buy threshold: `10`
- hold threshold: `20`
- single stock max weight: `10%`
- gross exposure: `1.00`
- hard stop: `15%`
- portfolio drawdown ladder: disabled

Official runner result:

- years `8.34`
- CAGR `25.46%`
- KOSPI200 CAGR `18.57%`
- excess CAGR `+6.89%`
- MDD `-21.90%`
- Sharpe `1.259`
- Information Ratio `0.251`
- trades `583`
- average cash weight `59.88%`
- metric mode `broker_ledger_next_close`, fill mode `next_close`

This remains below the official CAGR `>=30%`, IR `>0.5`, and stretch CAGR
`>=35%` goals.

## This Pass

Benchmark/data audit:

- Added fast `--index-cache-only` and `--skip-source-panels` paths to
  `tools/audit_data_integrity.py`.
- Added KOSPI/KOSPI200 index cache sanity metrics with bounded cache reads.
- Initial KOSPI/KOSPI200 2025-2026 spike looked suspicious, but FDR/Yahoo
  `^KS200`, Yahoo `^KS11`, and KODEX200 `069500.KS` spot checks matched the
  same move. Treat the benchmark cache as usable; the audit now blocks only
  more extreme broad-index anomalies.
- Verified:
  `py -3 tools\audit_data_integrity.py --as-of 2026-06-04 --index-cache-only --out-dir outputs\kr1000_index_cache_audit_20260604_v2`
  -> Critical `0`, High `0`, Medium `0`.

Weekly overlay A/B:

- Added `--overlay-mode technical_value` to
  `tools/build_kr1000_weekly_price_overlay.py`.
- 2018-current top-250 `rs_technical`:
  - output `outputs\kr1000_weekly_overlay_2018_250_v1`
  - loaded `244/250` tickers
  - CAGR `16.87%`
  - MDD `-26.01%`
  - excess CAGR `-1.83%`
- 2018-current top-250 `technical_value`:
  - output `outputs\kr1000_weekly_overlay_2018_250_technical_value_v1`
  - CAGR `7.67%`
  - MDD `-31.44%`
  - excess CAGR `-11.03%`
- Conclusion: do not spend the next pass scaling these exact weekly overlay
  modes to 500/full KR1000. They are weaker than the locked production
  challenger.

Production challenger loss-month diagnostic:

- Added `tools/analyze_kr1000_challenger_loss_months.py`.
- Output: `outputs\kr1000_challenger_loss_months_2018_20260604`.
- Result:
  - months `102`
  - underperform months `52` (`51.0%`)
  - mean monthly active return `0.39%`
  - median monthly active return `-0.04%`
  - worst active month `2026-05`, active `-19.66%`
- Key finding: several worst underperformance months are near-100% cash while
  KOSPI200 rallies (`2020-11`, `2023-11`, `2020-04`, `2022-10/11`,
  `2025-01`). The next credible improvement path is a tradable benchmark or
  large-cap sleeve, or a better risk-on re-entry gate.
- `2026-05` is different: the strategy was invested and up `15.69%`, but
  KOSPI200 rose `35.34%`. That needs sector/theme breadth or index-leader
  participation diagnostics, not only a cash sleeve.

Daily broker usability:

- Production daily readiness still needs actual
  `DATA_ROOT/state/current_holdings.csv`.

## Tests Run

- `py -3 -m py_compile tools\analyze_kr1000_challenger_loss_months.py tools\build_kr1000_weekly_price_overlay.py tools\audit_data_integrity.py tests\test_kr1000_data_repair_tools.py` -> passed.
- `py -3 tests\test_kr1000_data_repair_tools.py` -> 18 passed, 0 failed.
- `py -3 tests\smoke_test.py --quick` -> 24 passed, 0 failed.
- `py -3 tests\smoke_test.py` -> 46 passed, 0 failed.
- `py -3 tools\audit_data_integrity.py --as-of 2026-06-04 --index-cache-only --out-dir outputs\kr1000_index_cache_audit_20260604_v2` -> Critical `0`.
- `py -3 tools\build_kr1000_weekly_price_overlay.py --scored-panel "G:\내 드라이브\kr_quant_engine\feature_store\scored_panel_v0_2018-01-01_2026-06-04_2026-06-05-p1-pit-data-audit.parquet" --start 2018-01-01 --end 2026-06-04 --max-tickers 250 --run-backtest --out-dir outputs\kr1000_weekly_overlay_2018_250_v1` -> completed, research-only.
- `py -3 tools\build_kr1000_weekly_price_overlay.py --scored-panel "G:\내 드라이브\kr_quant_engine\feature_store\scored_panel_v0_2018-01-01_2026-06-04_2026-06-05-p1-pit-data-audit.parquet" --start 2018-01-01 --end 2026-06-04 --max-tickers 250 --overlay-mode technical_value --run-backtest --out-dir outputs\kr1000_weekly_overlay_2018_250_technical_value_v1` -> completed, research-only.
- `py -3 tools\analyze_kr1000_challenger_loss_months.py --out-dir outputs\kr1000_challenger_loss_months_2018_20260604 --worst-count 20` -> completed.

## Next Engineering Steps

1. Keep the top10/cap10/no-ladder value preset as the active challenger; it is
   not a pass.
2. Use `outputs\kr1000_challenger_loss_months_2018_20260604` before changing
   score weights.
3. Prototype a broker-ledger integrated tradable benchmark/large-cap sleeve for
   months where the strategy is almost all cash but KOSPI200 trend is strong.
   The previous diagnostic-only cash sleeve failed MDD, so this must be an
   actual order/fill/cost-aware sleeve with stricter regime entry and exit.
4. Separately analyze invested underperformance months such as `2026-05` for
   sector/theme breadth and index-leader participation.
5. Daily readiness still needs actual `DATA_ROOT/state/current_holdings.csv`.
   Without it, production daily broker readiness must remain blocked.
