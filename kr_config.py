"""kr_config — pure data constants for kr_quant_engine.

Mirrors r1000_config.py role: PHASE_*_COLUMNS whitelists, DEFAULT_CFG, MACRO
constants, cost-model constants. No business logic, no pandas/numpy import
side effects.

Import discipline: this module imports only from stdlib. Other modules import
from this. Never the reverse.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Engine version (cache invalidation key)
# ---------------------------------------------------------------------------
# Bump this string when any signal formula changes. cache_*, feature_store,
# models artifacts will be regenerated.
KR_ENGINE_REUSE_VERSION = "2026-04-28-p1-dart-fundamentals"


# ---------------------------------------------------------------------------
# Project paths
#
# Code root (PROJECT_ROOT) is always derived from this file's location.
# Data root (DATA_ROOT) defaults to GDrive via KR_DATA_DIR env var, else
# falls back to PROJECT_ROOT for greenfield setups. Caches/outputs/models
# all live under DATA_ROOT.
#
# This split lets us version code in GitHub (H:/codex/kr_quant_engine/) while
# keeping data on GDrive (G:/내 드라이브/kr_quant_engine/) for backup +
# Colab compatibility. Set KR_DATA_DIR=... in .env.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.resolve()


def _resolve_data_root() -> Path:
    """Resolve DATA_ROOT from env or fall back to PROJECT_ROOT.

    Env loading: parse .env at PROJECT_ROOT/.env if KR_DATA_DIR not already set.
    """
    env_val = os.environ.get("KR_DATA_DIR", "").strip().strip('"').strip("'")
    if not env_val:
        # Defer to .env file (parse manually to avoid kr_helpers circular import)
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "KR_DATA_DIR":
                    env_val = v.strip().strip('"').strip("'")
                    break
    if env_val:
        return Path(env_val).resolve()
    return PROJECT_ROOT


DATA_ROOT = _resolve_data_root()

DEFAULT_CACHE_DIRS = {
    "cache_pykrx": DATA_ROOT / "cache_pykrx",
    "cache_dart": DATA_ROOT / "cache_dart",
    "cache_macro": DATA_ROOT / "cache_macro",
    "cache_misc": DATA_ROOT / "cache_misc",
    "feature_store": DATA_ROOT / "feature_store",
    "data_raw": DATA_ROOT / "data_raw",
    "outputs": DATA_ROOT / "outputs",
    "outputs_advisor": DATA_ROOT / "outputs_advisor",
    "models": DATA_ROOT / "models",
    "backtest_results": DATA_ROOT / "backtest_results",
}


# ---------------------------------------------------------------------------
# Universe constants
# ---------------------------------------------------------------------------
EXCHANGES = ("KOSPI", "KOSDAQ")
BACKTEST_START_DATE = "2016-01-01"

# Default soft filters (override via cfg)
DEFAULT_MIN_TRADING_VALUE_60D_KRW = 5e8       # 5억원
DEFAULT_MIN_MARKET_CAP_KRW = 5e10              # 500억원
DEFAULT_MIN_LISTED_MONTHS = 12

# Benchmark tickers (KRX 지수)
BENCHMARK_KOSPI = "1001"      # KOSPI 종합
BENCHMARK_KOSPI200 = "1028"   # KOSPI 200
BENCHMARK_KOSDAQ = "2001"     # KOSDAQ 종합
BENCHMARK_KOSDAQ150 = "2203"  # KOSDAQ 150
DEFAULT_BENCHMARK = BENCHMARK_KOSPI200


# ---------------------------------------------------------------------------
# Cost model (Korean market 2026)
# ---------------------------------------------------------------------------
# 매도 시 증권거래세 + 양방향 수수료 + 슬리피지 = 왕복 비용
TRANSACTION_TAX_SELL = 0.0018         # 0.18% (KOSPI/KOSDAQ 동일, 2026 기준)
BROKERAGE_FEE_ONE_WAY = 0.00015        # 0.015% (저가 retail)
DEFAULT_SLIPPAGE_BP = 5.0              # 5bp (mid-cap 기준)
DEFAULT_ROUND_TRIP_COST = (
    TRANSACTION_TAX_SELL
    + 2 * BROKERAGE_FEE_ONE_WAY
    + 2 * (DEFAULT_SLIPPAGE_BP / 10000)
)  # = 0.0031 ≈ 31bp round trip (mid-cap baseline; large-cap ~25bp, small-cap ~40bp)

# 가격제한폭
PRICE_LIMIT_PCT = 0.30   # ±30%


# ---------------------------------------------------------------------------
# Phase column whitelists (r1000 PHASE2_INDUSTRY_COLUMNS pattern)
#
# When adding a new phase, ALSO append the constant to:
#   - kr_pipeline.build_feature_store(keep_cols=...)
#   - kr_pipeline.hard_sanitize numeric column list
#   - kr_features phase-disabled zero-fill list
# Otherwise the column is silently dropped from feature_store.parquet
# (r1000 phase2-keepcols regression). See CLAUDE.md "feature_store 생존 규칙".
# ---------------------------------------------------------------------------

# P0 baseline momentum (always on at P0 — no toggle)
PHASE0_MOMENTUM_COLUMNS = (
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "ret_12m",
    "ret_12_1m",         # 12-1 month momentum (skip recent month)
    "rs_kospi_3m",
    "rs_kospi_12m",
    "rs_kosdaq_3m",
    "rs_kosdaq_12m",
)

# P1 fundamentals (DART 도착 후 활성)
PHASE1_FUNDAMENTAL_COLUMNS = (
    "per",
    "pbr",
    "psr",
    "ev_ebitda",
    "roe",
    "roa",
    "operating_margin",
    "net_margin",
    "revenue_growth_yoy",
    "operating_income_growth_yoy",
    "debt_to_equity",
    "fcf_yield",
    "dividend_yield",
    "value_score",
    "quality_score",
    "turnaround_score",
)

# P2 Korean-specific alpha (event signals + flow + theme + safety)
# Split into sub-groups for keep_cols whitelist registration.

# P2 DART corporate events (KRW-amount-based scores, 2026-04-28 v2)
PHASE2_DART_EVENT_COLUMNS = (
    "disclosure_event_total_score",         # weighted sum across all events
    "event_treasury_buyback_score",          # +
    "event_capital_increase_score",          # -
    "event_bonus_issue_score",               # +
    "event_treasury_sell_score",             # -
    "event_convertible_bond_score",          # -
    "event_warrant_bond_score",              # -
    "event_insider_holdings_score",          # ± net buy
    "event_major_holders_score",             # ± stkrt change
    "event_merger_score",                    # case
    "event_spinoff_score",                   # - (KR specific)
    "event_capital_reduction_score",         # -
)

# P2 flow signals (P2.5 — kr_flow.py): foreign / institutional / individual
# supply-demand at both market level (broadcast) and ticker level (PIT).
PHASE2_FLOW_COLUMNS = (
    # Ticker-level net buy z-scores (rolling)
    "foreign_net_buy_5d_zscore",
    "foreign_net_buy_20d_zscore",
    "foreign_net_buy_60d_zscore",
    "inst_net_buy_5d_zscore",
    "inst_net_buy_20d_zscore",
    "inst_net_buy_60d_zscore",
    "individual_net_buy_20d_zscore",
    # Foreign ownership pct + change
    "foreign_holding_pct",
    "foreign_holding_change_20d",
    # Buying streak (consecutive days of net buy by foreign)
    "foreign_buying_streak_days",
    "inst_buying_streak_days",
    # Market-level (broadcast)
    "market_foreign_net_buy_20d_kospi",
    "market_foreign_net_buy_20d_kosdaq",
    "market_inst_net_buy_20d_kospi",
    "market_inst_net_buy_20d_kosdaq",
    "market_foreign_cumulative_60d",
    # Composite
    "foreign_inst_combined_zscore_20d",
)

# P2 theme + safety signals (P2.6 / P2.7)
PHASE2_THEME_SAFETY_COLUMNS = (
    "theme_phase_score",
    "theme_phase_label",
    "short_interest_change_5d",
    "chaebol_premium_score",
    "won_export_sensitivity",
    "overheating_avoidance_flag",
)

# Aggregated P2 column whitelist (for keep_cols + hard_sanitize)
PHASE2_KOREA_ALPHA_COLUMNS = (
    PHASE2_DART_EVENT_COLUMNS
    + PHASE2_FLOW_COLUMNS
    + PHASE2_THEME_SAFETY_COLUMNS
)

# P2.6 Derivatives sentiment (kr_derivatives.py): VKOSPI + foreign futures
PHASE2_DERIVATIVES_COLUMNS = (
    "vkospi_level",
    "vkospi_zscore_60d",
    "vkospi_change_5d",
    "vkospi_above_25",
    "foreign_futures_net_oi",
    "foreign_futures_net_5d_change",
    "kospi200_basis_bp",          # (futures - spot) basis points
)

# P3 macro layer (P3.2 — kr_macro.py): KR + global macro broadcast
PHASE3_MACRO_COLUMNS = (
    # 금리 / 환율
    "macro_bok_base_rate",
    "macro_bok_rate_change_60d",
    "macro_ktb_3y_yield",
    "macro_ktb_10y_yield",
    "macro_ktb_10y_3y_spread",
    "macro_usd_krw",
    "macro_usd_krw_zscore_60d",
    "macro_usd_krw_change_20d",
    # KR 경기
    "macro_kr_pmi",
    "macro_kr_pmi_diffusion",         # PMI - 50 (50 above = 확장)
    "macro_consumer_sentiment",
    "macro_business_sentiment",
    "macro_kr_industrial_prod",
    "macro_export_yoy",
    "macro_export_yoy_3m_avg",
    # 글로벌 (FRED + yfinance)
    "macro_us_10y",
    "macro_us_2y",
    "macro_us_10y_2y_spread",
    "macro_dxy",
    "macro_dxy_zscore_60d",
    "macro_vix",
    "macro_wti_close",
    "macro_wti_zscore_60d",
)

# P3 technical indicators (P3.1 — kr_technicals.py)
PHASE3_TECHNICAL_COLUMNS = (
    "ma_5", "ma_20", "ma_50", "ma_60", "ma_150", "ma_200",
    "ma_stack_aligned",
    "dist_from_ma_50", "dist_from_ma_150", "dist_from_ma_200",
    "high_52w", "low_52w",
    "dist_from_52w_high", "dist_from_52w_low",
    "new_52w_high_flag",
    "volume_ma_50", "volume_zscore_50", "volume_dryup_pct",
    "rsi_14", "rsi_overbought", "rsi_oversold",
    "atr_14", "atr_pct",
    "bb_upper_20", "bb_lower_20", "bb_position",
    "vol_contraction",
    "stage_label",
    "trend_template_score", "trend_template_pass",
    "breakout_flag",
)

# P3 regime & risk (P3.2 — kr_macro.py — TBD)
PHASE3_REGIME_COLUMNS = (
    "vkospi_zscore_63d",
    "usd_krw_change_20d",
    "foreign_kospi_cumulative_5d",
    "bok_rate_change_60d",
    "kospi_above_ma200",
    "regime_label_kr",
    "regime_sleeve_multiplier_core",
    "regime_sleeve_multiplier_future",
    "regime_sleeve_multiplier_early",
)

# All phase columns combined (for keep_cols total)
ALL_PHASE_COLUMNS = (
    PHASE0_MOMENTUM_COLUMNS
    + PHASE1_FUNDAMENTAL_COLUMNS
    + PHASE2_KOREA_ALPHA_COLUMNS
    + PHASE2_DERIVATIVES_COLUMNS
    + PHASE3_TECHNICAL_COLUMNS
    + PHASE3_MACRO_COLUMNS
    + PHASE3_REGIME_COLUMNS
)


# ---------------------------------------------------------------------------
# BOK ECOS macro series (P0 BASIC, P3에서 확장)
# ---------------------------------------------------------------------------
# Format: (key_code, item_code, cycle, name)
# 키 코드는 BOK ECOS 통계표 코드 (https://ecos.bok.or.kr/api/StatisticTableList)
MACRO_BOK_SERIES = {
    "bok_base_rate":     ("722Y001", "0101000", "M", "BOK 기준금리"),
    "ktb_3y_yield":      ("817Y002", "010195000", "D", "국고채 3년"),
    "ktb_10y_yield":     ("817Y002", "010210000", "D", "국고채 10년"),
    "usd_krw":           ("731Y001", "0000001", "D", "USD/KRW 매매기준율"),
    "kr_pmi":            ("404Y014", "*AA", "M", "한국 제조업 PMI"),
    "kr_industrial_prod":("901Y033", "I3160AA", "M", "산업생산지수"),
    "kr_export_yoy":     ("901Y013", "FIEED", "M", "수출 YoY"),
    "consumer_sentiment":("511Y002", "FME", "M", "소비자심리지수"),
    "business_sentiment":("512Y014", "AA", "M", "기업경기실사지수"),
    "household_loans":   ("104Y012", "BBHA00", "M", "가계대출"),
}

# FRED / yfinance 보조 (P3에서 활용)
MACRO_FRED_SERIES = {
    "us_10y": "DGS10",
    "us_2y": "DGS2",
    "vix": "VIXCLS",
    "dxy": "DTWEXBGS",
}
MACRO_YF_TICKERS = {
    "vkospi": "^VKOSPI",      # 한국 변동성 지수
    "usdkrw_yf": "KRW=X",     # 보조
    "kospi_yf": "^KS11",
    "kosdaq_yf": "^KQ11",
}


# ---------------------------------------------------------------------------
# Default config dict (cfg["..."])
# ---------------------------------------------------------------------------
DEFAULT_CFG: dict[str, Any] = {
    # Engine
    "engine_reuse_version": KR_ENGINE_REUSE_VERSION,
    "base_dir": str(PROJECT_ROOT),
    "reuse_existing_artifacts": True,
    "resume_partial_walkforward": True,
    "fast_mode": False,

    # Backtest window
    "start_date": BACKTEST_START_DATE,
    "end_date": None,                    # None = today
    "default_backtest_years": 8,
    "embargo_days": 126,

    # Universe filters
    "exchanges": list(EXCHANGES),
    "min_trading_value_60d_krw": DEFAULT_MIN_TRADING_VALUE_60D_KRW,
    "min_market_cap_krw": DEFAULT_MIN_MARKET_CAP_KRW,
    "min_listed_months": DEFAULT_MIN_LISTED_MONTHS,
    "exclude_preferred": True,
    "exclude_etf_etn": True,
    "exclude_spac": True,
    "exclude_managed": True,
    "exclude_reits": True,

    # Portfolio
    "portfolio_size": 30,                # Top-N
    "rebalance_interval_months": 1,
    "weighting_mode": "equal",           # "equal" | "score" | "score_power"
    "kosdaq_max_weight": 1.0,            # P3에서 0.5로 조정 가능
    "single_stock_max_weight": 0.14,     # 14% per-name cap

    # Cost model
    "transaction_tax_sell": TRANSACTION_TAX_SELL,
    "brokerage_fee_one_way": BROKERAGE_FEE_ONE_WAY,
    "slippage_bp": DEFAULT_SLIPPAGE_BP,
    "round_trip_cost": DEFAULT_ROUND_TRIP_COST,
    "apply_price_limit": True,
    "price_limit_pct": PRICE_LIMIT_PCT,

    # Cache
    "pykrx_refresh_days": 1,
    "dart_refresh_days": 1,
    "macro_refresh_days": 7,
    "companyfacts_refresh_days": 30,

    # Benchmark
    "benchmark_ticker": DEFAULT_BENCHMARK,
    "benchmark_name": "KOSPI200",

    # Phase toggles (env var precedence over these)
    "phase0_momentum_enabled": True,
    "phase1_fundamental_enabled": True,     # DART 키 도착 (2026-04-28) → ON
    "phase2_korea_alpha_enabled": False,
    "phase3_regime_enabled": False,
    "phase4_ml_enabled": False,

    # DART config (P1)
    "dart_fund_start_year": 2014,            # 2-year buffer before backtest 2016 start
    "dart_polite_sleep_s": 0.3,              # throttle between API calls
    "dart_use_consolidated_first": True,      # CFS preferred, fall back to OFS
    "dart_refresh_days_corp_code": 1,         # corp_code zip TTL
    "dart_refresh_days_financials": 30,       # historical filings rarely change

    # Multibagger episode mining (P_MB) — r1000 phase11 ported
    "multibagger_return_threshold": 3.0,         # 300% (4x peak/entry)
    "multibagger_window_months": 24,             # max time from entry to peak
    "multibagger_min_mcap_krw": 5e11,            # 5,000억 (mid+ cap only)
    "multibagger_min_trading_value_krw": 5e8,    # 5억원/일 60d avg
    "multibagger_min_listed_months": 12,
    "multibagger_pre_surge_lookback_months": 6,  # pre-signal collection window
    "multibagger_post_surge_include_months": 3,  # surge-still-detectable window
    "multibagger_surge_breakout_pct": 0.20,      # surge_start = first +20% from entry
}


# ---------------------------------------------------------------------------
# Acceptance check thresholds (for run_full_validation_suite)
# ---------------------------------------------------------------------------
ACCEPTANCE_CHECKS = {
    "min_member_count_per_month": 200,    # 너무 작으면 universe builder 버그
    "max_nan_rate_per_column": 0.30,      # 30% 이상 NaN이면 fail
    "min_pit_lag_days": 1,                 # rcept_dt 검증
    "max_member_change_per_month": 0.30,   # 멤버쉽 30% 이상 변동 시 alert
}
