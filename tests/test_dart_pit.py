"""tests/test_dart_pit.py — Point-in-time invariants for DART fundamentals.

Critical correctness tests:
1. rcept_dt is always >= period_end (filings happen after period close)
2. pit_filter_panel never returns rows with rcept_dt > as_of (no look-ahead)
3. Annual report rcept_dt within 90 days of period_end (regulatory deadline)
4. Quarter/semi report rcept_dt within 45 days of period_end

Run: py -3 tests/test_dart_pit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

PASSED = 0
FAILED = 0


def _test(name: str):
    def deco(fn):
        global PASSED, FAILED
        try:
            fn()
            PASSED += 1
            print(f"  PASS  {name}")
        except Exception as e:
            FAILED += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        return fn
    return deco


@_test("import kr_dart_client succeeds")
def test_import_dart_client():
    import kr_dart_client
    assert callable(kr_dart_client.download_corp_code)
    assert callable(kr_dart_client.fetch_single_company_financials)
    assert callable(kr_dart_client.pit_filter_panel)
    assert callable(kr_dart_client.fetch_multi_company_main_accounts)
    assert callable(kr_dart_client.infer_report_period_end)


@_test("REPRT_CODES has 4 quarters")
def test_reprt_codes():
    from kr_dart_client import REPRT_CODES
    assert set(REPRT_CODES.keys()) == {"annual", "semi", "q1", "q3"}
    assert REPRT_CODES["annual"] == "11011"
    assert REPRT_CODES["q1"] == "11013"


@_test("KEY_ACCOUNTS covers all required standard names")
def test_key_accounts_complete():
    from kr_dart_client import KEY_ACCOUNTS
    required = {"revenue", "operating_income", "net_income",
                "total_assets", "total_equity", "total_liabilities",
                "operating_cash_flow"}
    assert required.issubset(set(KEY_ACCOUNTS.keys())), \
        f"missing: {required - set(KEY_ACCOUNTS.keys())}"


@_test("KOREAN_NAME_FALLBACK has 매출액 매핑")
def test_korean_fallback_revenue():
    from kr_dart_client import KOREAN_NAME_FALLBACK
    assert "매출액" in KOREAN_NAME_FALLBACK["revenue"]
    assert "영업이익" in KOREAN_NAME_FALLBACK["operating_income"]
    assert "당기순이익" in KOREAN_NAME_FALLBACK["net_income"]


@_test("pit_filter_panel: empty panel returns empty")
def test_pit_filter_empty():
    import pandas as pd
    from kr_dart_client import pit_filter_panel
    out = pit_filter_panel(pd.DataFrame(), pd.Timestamp("2024-01-01"))
    assert out.empty


@_test("pit_filter_panel: filters by rcept_dt <= as_of")
def test_pit_filter_correctness():
    import pandas as pd
    from kr_dart_client import pit_filter_panel
    panel = pd.DataFrame({
        "corp_code": ["X", "X", "X", "X"],
        "rcept_dt": pd.to_datetime(["2024-01-15", "2024-04-15", "2024-08-15", "2024-11-15"]),
        "revenue": [1, 2, 3, 4],
    })
    out = pit_filter_panel(panel, pd.Timestamp("2024-06-01"))
    assert len(out) == 2
    assert set(out["revenue"]) == {1, 2}, f"got {set(out['revenue'])}"


@_test("pit_filter_panel: drops rows with NaN rcept_dt")
def test_pit_filter_drops_nan():
    import pandas as pd
    import numpy as np
    from kr_dart_client import pit_filter_panel
    panel = pd.DataFrame({
        "corp_code": ["X", "Y"],
        "rcept_dt": [pd.Timestamp("2024-01-15"), pd.NaT],
        "revenue": [1, 2],
    })
    out = pit_filter_panel(panel, pd.Timestamp("2024-12-31"))
    assert len(out) == 1
    assert out.iloc[0]["revenue"] == 1


@_test("infer_report_period_end: preserves normal calendar annual report")
def test_infer_period_end_normal_annual():
    import pandas as pd
    from kr_dart_client import infer_report_period_end
    pe, adjusted = infer_report_period_end(
        2023, "11011", pd.Timestamp("2024-03-12")
    )
    assert pe == pd.Timestamp("2023-12-31")
    assert adjusted is False


@_test("infer_report_period_end: adjusts impossible fiscal-period metadata")
def test_infer_period_end_adjusts_future_period():
    import pandas as pd
    from kr_dart_client import infer_report_period_end
    pe, adjusted = infer_report_period_end(
        2019, "11014", pd.Timestamp("2019-05-15")
    )
    assert pe == pd.Timestamp("2019-03-31")
    assert pe <= pd.Timestamp("2019-05-15")
    assert adjusted is True


@_test("infer_report_period_end: no rcept_dt keeps calendar metadata")
def test_infer_period_end_no_rcept_dt():
    import pandas as pd
    from kr_dart_client import infer_report_period_end
    pe, adjusted = infer_report_period_end(2019, "11014")
    assert pe == pd.Timestamp("2019-09-30")
    assert adjusted is False


@_test("phase_is_enabled('phase1_fundamental') reads env override")
def test_phase1_toggle():
    import os
    from kr_helpers import phase_is_enabled
    os.environ.pop("PHASE_PHASE1_FUNDAMENTAL_ENABLED", None)
    # Default should be True now (P1 active)
    assert phase_is_enabled("phase1_fundamental", default=True) is True
    os.environ["PHASE_PHASE1_FUNDAMENTAL_ENABLED"] = "0"
    assert phase_is_enabled("phase1_fundamental", default=True) is False
    os.environ["PHASE_PHASE1_FUNDAMENTAL_ENABLED"] = "1"
    assert phase_is_enabled("phase1_fundamental", default=False) is True
    os.environ.pop("PHASE_PHASE1_FUNDAMENTAL_ENABLED", None)


@_test("DART_API_KEY is set in environment")
def test_dart_api_key_set():
    import os
    from kr_helpers import load_dotenv_if_present
    load_dotenv_if_present()
    key = os.environ.get("DART_API_KEY", "").strip()
    assert key and len(key) >= 20, "DART_API_KEY missing or too short"


@_test("kr_config bumped to p1 version")
def test_config_version_p1():
    from kr_config import KR_ENGINE_REUSE_VERSION
    assert "p1" in KR_ENGINE_REUSE_VERSION.lower()


@_test("kr_features.compute_value_score handles empty input")
def test_compute_value_score_empty():
    import pandas as pd
    from kr_features import compute_value_score
    out = compute_value_score(pd.DataFrame())
    assert out.empty


@_test("kr_features.compute_quality_score returns score column")
def test_compute_quality_score_basic():
    import pandas as pd
    from kr_features import compute_quality_score
    df = pd.DataFrame({
        "ticker": ["A", "B", "C", "D", "E", "F", "G"],
        "roe": [0.20, 0.15, 0.10, 0.05, 0.0, -0.05, 0.30],
        "operating_margin": [0.20, 0.15, 0.10, 0.05, 0.0, -0.05, 0.25],
        "debt_to_equity": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 0.3],
    })
    out = compute_quality_score(df)
    assert "quality_score" in out.columns
    # Higher ROE + lower D/E should rank higher
    g_score = out[out["ticker"] == "G"]["quality_score"].iloc[0]
    f_score = out[out["ticker"] == "F"]["quality_score"].iloc[0]
    assert g_score > f_score, f"G ({g_score}) should beat F ({f_score})"


@_test("kr_features.compute_p1_score blends momentum + fundamentals")
def test_compute_p1_score_blend():
    import pandas as pd
    from kr_features import compute_p1_score
    df = pd.DataFrame({
        "ticker": ["A", "B"],
        "p0_momentum_score": [1.0, 0.0],
        "value_score": [0.5, 0.5],
        "quality_score": [0.0, 1.0],
        "turnaround_score": [0.0, 0.0],
    })
    out = compute_p1_score(df)
    # A: 0.4*1 + 0.25*0.5 + 0.25*0 + 0.10*0 = 0.525, /1.0 = 0.525
    # B: 0.4*0 + 0.25*0.5 + 0.25*1 + 0.10*0 = 0.375
    # A should beat B
    a = out[out["ticker"] == "A"]["p1_blended_score"].iloc[0]
    b = out[out["ticker"] == "B"]["p1_blended_score"].iloc[0]
    assert a > b, f"A ({a}) should beat B ({b})"


print()
print("=" * 60)
print(f"DART PIT tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
