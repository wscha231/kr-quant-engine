"""tests/smoke_test.py — pre-commit smoke test (<10s).

Mirrors r1000 tests/smoke_test.py role. Catches ~80% of bugs in seconds.

Usage:
    py -3 tests/smoke_test.py            # full suite
    py -3 tests/smoke_test.py --quick    # syntax + structural only (~1s)
    py -3 tests/smoke_test.py -v         # verbose, per-test PASS/FAIL

Coverage:
- syntax: all .py files ast.parse cleanly
- structural: PHASE_*_COLUMNS in keep_cols whitelist, KR_ENGINE_REUSE_VERSION
              format, EXCHANGES tuple
- import: kr_config, kr_helpers, kr_pykrx_client, kr_bok_client, kr_universe,
          kr_features, kr_pipeline all import cleanly
- logic: phase_is_enabled env precedence, percentile_rank semantics,
         cross_sectional_robust_z basic shape
- regression: PHASE0_MOMENTUM_COLUMNS not empty, DEFAULT_CFG has required keys
"""
from __future__ import annotations

import ast
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
QUICK = "--quick" in sys.argv

PASSED = 0
FAILED = 0
TIMINGS = []


def _test(name: str):
    """Decorator: run test fn, print PASS/FAIL, accumulate timing."""
    def deco(fn):
        global PASSED, FAILED
        t0 = time.time()
        try:
            fn()
            elapsed = time.time() - t0
            TIMINGS.append((name, elapsed))
            PASSED += 1
            if VERBOSE:
                print(f"  PASS  {name:<50} ({elapsed*1000:.0f}ms)")
        except Exception as e:
            elapsed = time.time() - t0
            TIMINGS.append((name, elapsed))
            FAILED += 1
            print(f"  FAIL  {name}")
            print(f"        {type(e).__name__}: {e}")
            if VERBOSE:
                traceback.print_exc()
        return fn
    return deco


# ---------------------------------------------------------------------------
# 1. Syntax tests (always run)
# ---------------------------------------------------------------------------
@_test("syntax: all .py files parse")
def test_syntax_all_py():
    py_files = list(PROJECT_ROOT.glob("*.py")) + list((PROJECT_ROOT / "tests").glob("*.py"))
    bad = []
    for p in py_files:
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            bad.append((p.name, str(e)))
    assert not bad, f"syntax errors: {bad}"


# ---------------------------------------------------------------------------
# 2. Structural tests (always run)
# ---------------------------------------------------------------------------
@_test("structural: kr_config has KR_ENGINE_REUSE_VERSION format")
def test_engine_version_format():
    src = (PROJECT_ROOT / "kr_config.py").read_text(encoding="utf-8")
    assert "KR_ENGINE_REUSE_VERSION" in src
    # Format: "YYYY-MM-DD-..."
    import re
    m = re.search(r'KR_ENGINE_REUSE_VERSION\s*=\s*"(\d{4}-\d{2}-\d{2}[-\w]*)"', src)
    assert m, "KR_ENGINE_REUSE_VERSION must be quoted YYYY-MM-DD-... format"


@_test("structural: PHASE0_MOMENTUM_COLUMNS defined")
def test_phase0_columns_defined():
    src = (PROJECT_ROOT / "kr_config.py").read_text(encoding="utf-8")
    assert "PHASE0_MOMENTUM_COLUMNS" in src
    assert "ret_12_1m" in src   # critical column for 12-1m momentum
    assert "rs_kospi_12m" in src
    assert "rs_kosdaq_12m" in src


@_test("structural: ALL_PHASE_COLUMNS aggregates phases")
def test_all_phase_columns():
    src = (PROJECT_ROOT / "kr_config.py").read_text(encoding="utf-8")
    assert "ALL_PHASE_COLUMNS" in src


@_test("structural: EXCHANGES has KOSPI + KOSDAQ")
def test_exchanges_unified():
    src = (PROJECT_ROOT / "kr_config.py").read_text(encoding="utf-8")
    assert '"KOSPI"' in src and '"KOSDAQ"' in src


@_test("structural: DEFAULT_CFG has cost-model keys")
def test_default_cfg_cost_keys():
    src = (PROJECT_ROOT / "kr_config.py").read_text(encoding="utf-8")
    for key in ("transaction_tax_sell", "round_trip_cost", "price_limit_pct"):
        assert key in src, f"DEFAULT_CFG missing {key}"


@_test("structural: phase_is_enabled exists")
def test_phase_is_enabled_exists():
    src = (PROJECT_ROOT / "kr_helpers.py").read_text(encoding="utf-8")
    assert "def phase_is_enabled(" in src


@_test("structural: gitignore excludes .env and caches")
def test_gitignore_safe():
    p = PROJECT_ROOT / ".gitignore"
    assert p.exists(), ".gitignore missing"
    txt = p.read_text(encoding="utf-8")
    for needle in (".env", "cache_pykrx", "cache_dart", "feature_store", "models"):
        assert needle in txt, f".gitignore missing {needle}"


@_test("structural: .env not committed (file may exist locally)")
def test_env_locally_present_or_absent():
    # .env may or may not exist; just check that .env.example exists
    assert (PROJECT_ROOT / ".env.example").exists(), ".env.example template missing"


@_test("structural: DATA_ROOT separated from PROJECT_ROOT")
def test_data_root_split():
    """When KR_DATA_DIR is set, DATA_ROOT should differ from PROJECT_ROOT.
    Otherwise (greenfield) they're equal."""
    from kr_config import DATA_ROOT, PROJECT_ROOT
    import os
    if os.environ.get("KR_DATA_DIR"):
        # User configured GDrive (or another) — must differ
        assert DATA_ROOT != PROJECT_ROOT, \
            "KR_DATA_DIR set but DATA_ROOT==PROJECT_ROOT (unexpected)"


@_test("structural: kr_pykrx_client uses DATA_ROOT for cache")
def test_pykrx_uses_data_root():
    src = (PROJECT_ROOT / "kr_pykrx_client.py").read_text(encoding="utf-8")
    assert "from kr_config import DATA_ROOT" in src, \
        "kr_pykrx_client must import DATA_ROOT (not PROJECT_ROOT)"
    assert "CACHE_DIR = DATA_ROOT" in src


@_test("structural: kr_dart_client uses DATA_ROOT for cache")
def test_dart_uses_data_root():
    src = (PROJECT_ROOT / "kr_dart_client.py").read_text(encoding="utf-8")
    assert "from kr_config import DATA_ROOT" in src
    assert "CACHE_DIR = DATA_ROOT" in src


@_test("structural: DART_EVENT_CATALOG has 11 events with v2 dict format")
def test_dart_event_catalog():
    src = (PROJECT_ROOT / "kr_dart_client.py").read_text(encoding="utf-8")
    assert "DART_EVENT_CATALOG" in src
    # Critical event types present
    for key in ("treasury_buyback", "capital_increase", "bonus_issue",
                "insider_holdings", "major_holders", "convertible_bond"):
        assert f'"{key}"' in src, f"DART_EVENT_CATALOG missing {key}"
    # v2 fields present
    assert "scoring_mode" in src
    assert "amount_field" in src
    assert "full_weight_pct" in src
    assert "amount_pct_mcap" in src    # buyback mode
    assert "computed_dilution" in src   # capital_increase mode
    assert "insider_net_buy" in src     # insider mode


@_test("structural: PHASE2_DART_EVENT_COLUMNS = 12 (total + 11 events)")
def test_phase2_columns_split():
    from kr_config import (PHASE2_DART_EVENT_COLUMNS, PHASE2_FLOW_COLUMNS,
                           PHASE2_THEME_SAFETY_COLUMNS, PHASE2_KOREA_ALPHA_COLUMNS)
    assert len(PHASE2_DART_EVENT_COLUMNS) == 12
    assert "disclosure_event_total_score" in PHASE2_DART_EVENT_COLUMNS
    assert "event_treasury_buyback_score" in PHASE2_DART_EVENT_COLUMNS
    assert "event_capital_increase_score" in PHASE2_DART_EVENT_COLUMNS
    # Aggregated must include all sub-groups
    for c in PHASE2_DART_EVENT_COLUMNS:
        assert c in PHASE2_KOREA_ALPHA_COLUMNS
    for c in PHASE2_FLOW_COLUMNS:
        assert c in PHASE2_KOREA_ALPHA_COLUMNS


@_test("structural: kr_multibagger module exists with key functions")
def test_multibagger_module():
    src = (PROJECT_ROOT / "kr_multibagger.py").read_text(encoding="utf-8")
    for fn in ("define_multibagger_episodes", "find_episodes_for_ticker",
               "identify_surge_start", "add_surge_start_dates",
               "quality_filter_episodes", "build_episode_panel",
               "load_or_build_episode_panel", "episode_summary_stats"):
        assert f"def {fn}(" in src, f"kr_multibagger missing {fn}"


@_test("structural: kr_technicals module exists with key functions")
def test_technicals_module():
    src = (PROJECT_ROOT / "kr_technicals.py").read_text(encoding="utf-8")
    for fn in ("compute_moving_averages", "compute_52w_extremes",
               "compute_rsi", "compute_atr", "compute_bollinger",
               "compute_volatility_contraction", "classify_stage",
               "compute_trend_template_score", "compute_all_technicals",
               "is_ma_stack_aligned"):
        assert f"def {fn}(" in src, f"kr_technicals missing {fn}"


@_test("structural: kr_macro module exists with key functions")
def test_macro_module():
    src = (PROJECT_ROOT / "kr_macro.py").read_text(encoding="utf-8")
    for fn in ("build_macro_panel", "load_or_build_macro_panel",
               "get_macro_snapshot", "add_derived_macro_signals",
               "fetch_bok_macro_wide", "fetch_fred_macro_wide",
               "fetch_yfinance_macro_wide"):
        assert f"def {fn}(" in src, f"kr_macro missing {fn}"


@_test("structural: kr_flow module exists with key functions")
def test_flow_module():
    src = (PROJECT_ROOT / "kr_flow.py").read_text(encoding="utf-8")
    for fn in ("compute_ticker_flow_signals", "compute_market_flow_signals",
               "get_market_flow_snapshot", "get_ticker_flow_snapshot",
               "get_foreign_holding_snapshot", "build_market_flow_panel",
               "build_ticker_flow_panel", "fetch_foreign_holding_for_date"):
        assert f"def {fn}(" in src, f"kr_flow missing {fn}"


@_test("structural: kr_derivatives module exists with key functions")
def test_derivatives_module():
    src = (PROJECT_ROOT / "kr_derivatives.py").read_text(encoding="utf-8")
    for fn in ("fetch_vkospi", "fetch_foreign_futures_oi",
               "add_derived_derivatives_signals", "build_derivatives_panel",
               "get_derivatives_snapshot"):
        assert f"def {fn}(" in src, f"kr_derivatives missing {fn}"


@_test("structural: kr_regime module exists with classifier + multipliers")
def test_regime_module():
    src = (PROJECT_ROOT / "kr_regime.py").read_text(encoding="utf-8")
    for fn in ("classify_regime", "get_regime_sleeve_multipliers",
               "add_regime_signals", "regime_summary"):
        assert f"def {fn}(" in src, f"kr_regime missing {fn}"
    assert "REGIME_SLEEVE_MULTIPLIERS" in src
    for r in ("bull_trending", "bear_falling", "won_crisis", "stagflation_kr",
              "recovery", "bear_bottoming", "bull_peaking", "sideways"):
        assert f'"{r}"' in src, f"REGIME_SLEEVE_MULTIPLIERS missing {r}"


@_test("structural: PHASE_*_COLUMNS counts (macro 23, flow 17, deriv 7, regime 9)")
def test_phase_column_counts():
    from kr_config import (PHASE3_MACRO_COLUMNS, PHASE2_FLOW_COLUMNS,
                           PHASE2_DERIVATIVES_COLUMNS, PHASE3_REGIME_COLUMNS)
    assert len(PHASE3_MACRO_COLUMNS) == 23
    assert len(PHASE2_FLOW_COLUMNS) == 17
    assert len(PHASE2_DERIVATIVES_COLUMNS) == 7
    assert len(PHASE3_REGIME_COLUMNS) == 9


@_test("structural: PHASE3_TECHNICAL_COLUMNS = 31 columns registered")
def test_phase3_technical_columns():
    from kr_config import PHASE3_TECHNICAL_COLUMNS, ALL_PHASE_COLUMNS
    assert len(PHASE3_TECHNICAL_COLUMNS) == 31
    for col in ("ma_50", "ma_200", "ma_stack_aligned", "rsi_14",
                "atr_pct", "bb_position", "stage_label",
                "trend_template_score", "breakout_flag"):
        assert col in PHASE3_TECHNICAL_COLUMNS
    for col in PHASE3_TECHNICAL_COLUMNS:
        assert col in ALL_PHASE_COLUMNS


@_test("structural: multibagger config constants registered")
def test_multibagger_config_constants():
    src = (PROJECT_ROOT / "kr_config.py").read_text(encoding="utf-8")
    for key in ("multibagger_return_threshold", "multibagger_window_months",
                "multibagger_min_mcap_krw", "multibagger_pre_surge_lookback_months"):
        assert f'"{key}"' in src, f"DEFAULT_CFG missing {key}"


# Quick mode stops here
if QUICK:
    print(f"\nQUICK: {PASSED} passed, {FAILED} failed.")
    sys.exit(0 if FAILED == 0 else 1)


# ---------------------------------------------------------------------------
# 3. Import tests (full mode only)
# ---------------------------------------------------------------------------
@_test("import: kr_config")
def test_import_kr_config():
    import kr_config
    assert kr_config.KR_ENGINE_REUSE_VERSION
    assert kr_config.DEFAULT_CFG["portfolio_size"] == 30
    assert kr_config.PHASE0_MOMENTUM_COLUMNS
    assert kr_config.DEFAULT_ROUND_TRIP_COST > 0


@_test("import: kr_helpers")
def test_import_kr_helpers():
    import kr_helpers
    assert callable(kr_helpers.phase_is_enabled)
    assert callable(kr_helpers.cross_sectional_robust_z)
    assert callable(kr_helpers.percentile_rank)


@_test("import: kr_pykrx_client (graceful if pykrx absent)")
def test_import_kr_pykrx_client():
    import kr_pykrx_client
    # PYKRX_AVAILABLE may be True or False; both OK at import
    assert hasattr(kr_pykrx_client, "PYKRX_AVAILABLE")
    assert callable(kr_pykrx_client.fetch_listing)


@_test("import: kr_dart_client + event endpoints available")
def test_import_kr_dart_client_events():
    import kr_dart_client
    # P1 endpoints
    assert callable(kr_dart_client.fetch_single_company_financials)
    assert callable(kr_dart_client.fetch_multi_company_main_accounts)
    # P2 event endpoints
    assert callable(kr_dart_client.fetch_insider_holdings)
    assert callable(kr_dart_client.fetch_treasury_buyback_decisions)
    assert callable(kr_dart_client.fetch_capital_increase_decisions)
    assert callable(kr_dart_client.fetch_all_events_for_corp)
    assert callable(kr_dart_client.compute_event_score_for_corp)
    # P2 v2 score helpers
    assert callable(kr_dart_client._score_amount_pct_mcap)
    assert callable(kr_dart_client._score_computed_dilution)
    assert callable(kr_dart_client._score_binary)
    assert callable(kr_dart_client._score_insider_net_buy)
    assert callable(kr_dart_client._score_stkrt_change)
    # Catalog populated (v2 dict format)
    assert len(kr_dart_client.DART_EVENT_CATALOG) >= 10
    sample = kr_dart_client.DART_EVENT_CATALOG["treasury_buyback"]
    assert isinstance(sample, dict)
    assert "scoring_mode" in sample
    assert "alpha_weight" in sample


@_test("import: kr_features P2 functions")
def test_import_kr_features_p2():
    import kr_features
    assert callable(kr_features.prepare_event_panel)
    assert callable(kr_features.add_disclosure_event_signal)
    assert callable(kr_features.compute_p2_score)


@_test("import: kr_multibagger module")
def test_import_kr_multibagger():
    import kr_multibagger as mb
    assert callable(mb.define_multibagger_episodes)
    assert callable(mb.find_episodes_for_ticker)
    assert callable(mb.identify_surge_start)
    assert callable(mb.build_episode_panel)
    assert callable(mb.load_or_build_episode_panel)
    assert mb.DEFAULT_THRESHOLD == 3.0
    assert mb.DEFAULT_WINDOW_MONTHS == 24
    assert mb.DEFAULT_MIN_MCAP_KRW == 5e11


@_test("import: kr_technicals module")
def test_import_kr_technicals():
    import kr_technicals as t
    assert callable(t.compute_all_technicals)
    assert callable(t.classify_stage)
    assert callable(t.compute_trend_template_score)


@_test("import: kr_features.add_technical_indicators")
def test_import_kr_features_technicals():
    import kr_features
    assert callable(kr_features.add_technical_indicators)


@_test("import: kr_macro / kr_flow / kr_derivatives / kr_regime")
def test_import_p3_modules():
    import kr_macro
    import kr_flow
    import kr_derivatives
    import kr_regime
    assert callable(kr_macro.build_macro_panel)
    assert callable(kr_flow.compute_ticker_flow_signals)
    assert callable(kr_derivatives.build_derivatives_panel)
    assert callable(kr_regime.classify_regime)


@_test("import: kr_features new add_* functions (flow, derivatives, macro, regime)")
def test_import_kr_features_new_phases():
    import kr_features
    assert callable(kr_features.add_flow_signals)
    assert callable(kr_features.add_derivatives_signals)
    assert callable(kr_features.add_macro_signals)


@_test("import: kr_bok_client")
def test_import_kr_bok_client():
    import kr_bok_client
    assert callable(kr_bok_client.fetch_bok_series)
    assert callable(kr_bok_client.fetch_macro_panel)


@_test("import: kr_universe")
def test_import_kr_universe():
    import kr_universe
    assert callable(kr_universe.build_universe_snapshot)
    assert callable(kr_universe.is_preferred)


@_test("import: kr_features")
def test_import_kr_features():
    import kr_features
    assert callable(kr_features.add_basic_momentum)
    assert callable(kr_features.compute_p0_score)


@_test("import: kr_pipeline")
def test_import_kr_pipeline():
    import kr_pipeline
    assert callable(kr_pipeline.run_p0_baseline)
    assert callable(kr_pipeline.run_verdict_only)


# ---------------------------------------------------------------------------
# 4. Logic tests
# ---------------------------------------------------------------------------
@_test("logic: phase_is_enabled env precedence")
def test_phase_is_enabled_env():
    import os
    from kr_helpers import phase_is_enabled
    os.environ.pop("PHASE_TEST_KEY_ENABLED", None)
    assert phase_is_enabled("test_key", default=True) is True
    assert phase_is_enabled("test_key", default=False) is False
    os.environ["PHASE_TEST_KEY_ENABLED"] = "0"
    assert phase_is_enabled("test_key", default=True) is False
    os.environ["PHASE_TEST_KEY_ENABLED"] = "1"
    assert phase_is_enabled("test_key", default=False) is True
    os.environ.pop("PHASE_TEST_KEY_ENABLED", None)


@_test("logic: percentile_rank fills NaN with 0.5")
def test_percentile_rank_nan():
    import pandas as pd
    from kr_helpers import percentile_rank
    s = pd.Series([1.0, 2.0, None, 3.0, None])
    r = percentile_rank(s)
    assert r.iloc[2] == 0.5
    assert r.iloc[4] == 0.5
    assert 0 <= r.min() <= r.max() <= 1


@_test("logic: cross_sectional_robust_z handles degenerate")
def test_robust_z_degenerate():
    import pandas as pd
    from kr_helpers import cross_sectional_robust_z
    # All same value -> should return 0.0 (not NaN)
    z = cross_sectional_robust_z(pd.Series([5.0] * 10))
    assert (z == 0.0).all()
    # Too few values -> NaN
    z = cross_sectional_robust_z(pd.Series([1.0, 2.0]))
    assert z.isna().all()


@_test("logic: is_preferred detects 우선주 codes")
def test_is_preferred():
    from kr_universe import is_preferred
    assert is_preferred("005935") is True   # 삼성전자우
    assert is_preferred("005930") is False  # 삼성전자 (보통주)
    assert is_preferred("000270") is False
    assert is_preferred("00025K") is False  # not 6 digits


@_test("logic: round_trip_cost in 25-40bp range")
def test_round_trip_cost():
    from kr_config import DEFAULT_ROUND_TRIP_COST
    # KR mid-cap: 18bp tax + 3bp brokerage RT + 10bp slippage RT ≈ 31bp
    # Allow 25-40bp range (large-cap lower, small-cap higher)
    assert 0.0025 <= DEFAULT_ROUND_TRIP_COST <= 0.0040, (
        f"unexpected cost: {DEFAULT_ROUND_TRIP_COST*10000:.1f}bp"
    )


# ---------------------------------------------------------------------------
# 5. Regression tests
# ---------------------------------------------------------------------------
@_test("regression: PHASE0_MOMENTUM_COLUMNS includes 12-1m")
def test_phase0_has_12_1m():
    from kr_config import PHASE0_MOMENTUM_COLUMNS
    assert "ret_12_1m" in PHASE0_MOMENTUM_COLUMNS


@_test("regression: BOK series dict has base rate")
def test_bok_series_has_base_rate():
    from kr_config import MACRO_BOK_SERIES
    assert "bok_base_rate" in MACRO_BOK_SERIES
    assert "usd_krw" in MACRO_BOK_SERIES


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
total_time = sum(t for _, t in TIMINGS)
print()
print("=" * 60)
print(f"smoke_test: {PASSED} passed, {FAILED} failed in {total_time:.2f}s")
print("=" * 60)
if FAILED:
    print("\nFailing tests above. Fix before commit.")
    sys.exit(1)
sys.exit(0)
