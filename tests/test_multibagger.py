"""tests/test_multibagger.py — Episode discovery logic verification.

Synthetic price panels with planted patterns to verify:
- All planted multibaggers are recovered (recall)
- Few/no false positives on stable/declining stocks (precision)
- surge_start_date matches the intended breakout point
- Edge cases: short history, NaN, zero prices, decline-then-recovery

Run: py -3 tests/test_multibagger.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

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
            import traceback
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
        return fn
    return deco


# ---------------------------------------------------------------------------
# Mock data factory
# ---------------------------------------------------------------------------
def make_months(start="2018-01-31", end="2024-12-31") -> pd.DatetimeIndex:
    return pd.date_range(start, end, freq="ME")


def make_random_walk_series(months: pd.DatetimeIndex, start_price=10000.0,
                             vol=0.05, seed=0) -> pd.Series:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, vol, len(months))
    return pd.Series(start_price * np.cumprod(1 + rets), index=months)


def plant_multibagger(
    months: pd.DatetimeIndex,
    accumulation_start_idx: int,
    surge_start_idx: int,
    peak_idx: int,
    peak_multiple: float = 5.0,
    base_price: float = 10000.0,
    seed: int = 0,
) -> pd.Series:
    """Plant a multibagger pattern.

    accumulation: idx 0 → accumulation_start_idx → surge_start_idx (flat-ish)
    surge:        surge_start_idx → peak_idx (linear rise to peak_multiple × base)
    decline:      peak_idx → end (random walk down)
    """
    rng = np.random.default_rng(seed)
    s = np.ones(len(months)) * base_price

    # Pre-accumulation: random walk
    for i in range(1, accumulation_start_idx):
        s[i] = s[i - 1] * (1 + rng.normal(0, 0.03))

    # Accumulation: drift down 0-20%
    for i in range(accumulation_start_idx, surge_start_idx):
        s[i] = s[i - 1] * (1 + rng.normal(-0.005, 0.03))

    # Surge: linear rise + noise (inclusive of peak_idx)
    surge_len = peak_idx - surge_start_idx + 1   # +1 = include peak_idx
    if surge_len > 0:
        target = base_price * peak_multiple
        start = s[surge_start_idx - 1] if surge_start_idx > 0 else base_price
        path = np.linspace(start, target, surge_len + 1)[1:]
        for i, p in enumerate(path):
            s[surge_start_idx + i] = p * (1 + rng.normal(0, 0.04))

    # Decline: random walk down (starts from peak_idx + 1)
    for i in range(peak_idx + 1, len(months)):
        s[i] = s[i - 1] * (1 + rng.normal(-0.01, 0.05))

    return pd.Series(s, index=months)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@_test("import kr_multibagger module")
def test_import():
    import kr_multibagger as mb
    assert callable(mb.find_episodes_for_ticker)
    assert callable(mb.define_multibagger_episodes)
    assert callable(mb.identify_surge_start)
    assert callable(mb.add_surge_start_dates)
    assert callable(mb.quality_filter_episodes)
    assert callable(mb.build_episode_panel)
    assert callable(mb.episode_summary_stats)


@_test("find_episodes_for_ticker: detects 5x bagger in 24m")
def test_basic_episode():
    import kr_multibagger as mb
    months = make_months()
    s = plant_multibagger(months, accumulation_start_idx=10,
                           surge_start_idx=15, peak_idx=35,
                           peak_multiple=5.0, base_price=10000, seed=1)
    eps = mb.find_episodes_for_ticker(s, threshold=3.0, window_months=24)
    assert len(eps) >= 1, f"expected >=1 episode, got {len(eps)}"
    ep = eps[0]
    assert ep["max_return"] >= 3.0, f"max_return = {ep['max_return']:.2f}"
    assert ep["months_to_peak"] <= 24
    # Entry should be at or before idx 15 (surge start)
    entry_idx = months.get_loc(ep["entry_date"])
    assert entry_idx <= 15, f"entry idx {entry_idx} > 15"


@_test("find_episodes_for_ticker: rejects 2x in 24m (below threshold)")
def test_below_threshold():
    import kr_multibagger as mb
    months = make_months()
    s = plant_multibagger(months, 10, 15, 35, peak_multiple=2.0, seed=2)
    eps = mb.find_episodes_for_ticker(s, threshold=3.0, window_months=24)
    assert len(eps) == 0, f"expected 0 episodes (2x below 3x threshold), got {len(eps)}"


@_test("find_episodes_for_ticker: rejects 5x but over 30m window")
def test_outside_window():
    import kr_multibagger as mb
    months = make_months()
    # Surge over 30 months (idx 5 → idx 36) — outside 24m window
    s = plant_multibagger(months, 0, 5, 36, peak_multiple=5.0, seed=3)
    eps = mb.find_episodes_for_ticker(s, threshold=3.0, window_months=24)
    # We may still find a sub-episode if 24m window catches partial surge.
    # Check no episode reports months_to_peak > 24.
    for ep in eps:
        assert ep["months_to_peak"] <= 24, \
            f"months_to_peak {ep['months_to_peak']:.1f} > 24"


@_test("find_episodes_for_ticker: handles NaN at start")
def test_nan_start():
    import kr_multibagger as mb
    months = make_months()
    s = plant_multibagger(months, 12, 17, 37, peak_multiple=5.0, seed=4)
    s.iloc[:6] = np.nan   # ticker not yet listed
    eps = mb.find_episodes_for_ticker(s, threshold=3.0, window_months=24)
    assert len(eps) >= 1


@_test("find_episodes_for_ticker: empty / too short series returns []")
def test_empty():
    import kr_multibagger as mb
    s = pd.Series([], dtype=float)
    assert mb.find_episodes_for_ticker(s) == []
    s2 = pd.Series([100, 110, 120], index=pd.date_range("2024-01-31", periods=3, freq="ME"))
    assert mb.find_episodes_for_ticker(s2, window_months=24) == []


@_test("find_episodes_for_ticker: zero/negative prices ignored")
def test_zero_prices():
    import kr_multibagger as mb
    months = make_months()
    s = plant_multibagger(months, 12, 17, 37, peak_multiple=5.0, seed=5)
    s.iloc[5:7] = 0
    s.iloc[8] = -100
    eps = mb.find_episodes_for_ticker(s, threshold=3.0, window_months=24)
    # Should still find the multibagger; just not anchor entry on the bad bars
    assert len(eps) >= 1


@_test("find_episodes_for_ticker: collapses overlapping episodes")
def test_overlap_collapse():
    import kr_multibagger as mb
    months = make_months()
    # Pure linear surge from idx 5 to idx 30, 5x. Many overlapping (entry_idx, peak_idx)
    # candidates should collapse to one episode.
    base = 10000.0
    target = 50000.0
    s = pd.Series(np.linspace(base, target, len(months)), index=months)
    eps = mb.find_episodes_for_ticker(s, threshold=3.0, window_months=24)
    # Greedy collapse: should give a small number, not dozens
    assert len(eps) < 5, f"expected <5 collapsed episodes, got {len(eps)}"


@_test("find_episodes_for_ticker: random walk gives few false positives")
def test_random_walk_few_fp():
    import kr_multibagger as mb
    months = make_months()
    fp_count = 0
    for seed in range(20):
        s = make_random_walk_series(months, start_price=10000, vol=0.05, seed=seed)
        eps = mb.find_episodes_for_ticker(s, threshold=3.0, window_months=24)
        fp_count += len(eps)
    # Random walk with vol 5% should rarely produce 4x in 24 months
    assert fp_count <= 4, f"too many false positives on random walk: {fp_count}/20"


@_test("define_multibagger_episodes: panel input")
def test_panel():
    import kr_multibagger as mb
    months = make_months()
    panel = pd.DataFrame(index=months)
    panel["BORING"] = make_random_walk_series(months, vol=0.04, seed=10)
    panel["BAGGER1"] = plant_multibagger(months, 8, 13, 30, peak_multiple=5.0, seed=11)
    panel["BAGGER2"] = plant_multibagger(months, 30, 35, 55, peak_multiple=4.0, seed=12)
    eps = mb.define_multibagger_episodes(panel, threshold=3.0, window_months=24)
    assert len(eps) >= 2
    tickers = set(eps["ticker"])
    assert "BAGGER1" in tickers
    assert "BAGGER2" in tickers


@_test("identify_surge_start: finds correct breakout point")
def test_surge_start():
    import kr_multibagger as mb
    months = make_months()
    s = plant_multibagger(months, 10, 15, 35, peak_multiple=5.0, seed=20)
    entry_dt = months[10]
    peak_dt = months[35]
    ssd = mb.identify_surge_start(s, entry_dt, peak_dt, breakout_pct=0.20)
    assert ssd is not None
    assert pd.Timestamp(months[15]) <= ssd <= pd.Timestamp(months[20]), \
        f"surge_start {ssd} should be in [{months[15]}, {months[20]}]"


@_test("identify_surge_start: missing entry_date returns None")
def test_surge_start_missing():
    import kr_multibagger as mb
    months = make_months()
    s = plant_multibagger(months, 10, 15, 35, peak_multiple=5.0, seed=21)
    ssd = mb.identify_surge_start(s, pd.Timestamp("1990-01-01"),
                                   pd.Timestamp("2024-12-31"), 0.20)
    assert ssd is None


@_test("add_surge_start_dates: enriches episodes DataFrame")
def test_add_surge_start_dates():
    import kr_multibagger as mb
    months = make_months()
    panel = pd.DataFrame(index=months)
    panel["BAGGER"] = plant_multibagger(months, 10, 15, 30, peak_multiple=5.0, seed=22)
    eps = mb.define_multibagger_episodes(panel, threshold=3.0, window_months=24)
    eps2 = mb.add_surge_start_dates(eps, panel)
    assert "surge_start_date" in eps2.columns
    assert "accumulation_months" in eps2.columns
    assert eps2["surge_start_date"].notna().all()
    # Accumulation should be 0-12 months typically
    assert (eps2["accumulation_months"] >= 0).all()
    assert (eps2["accumulation_months"] <= 24).all()


@_test("episode_summary_stats: returns valid summary")
def test_summary_stats():
    import kr_multibagger as mb
    months = make_months()
    panel = pd.DataFrame(index=months)
    panel["BAGGER1"] = plant_multibagger(months, 5, 10, 28, peak_multiple=4.0, seed=30)
    panel["BAGGER2"] = plant_multibagger(months, 20, 25, 45, peak_multiple=6.0, seed=31)
    eps = mb.define_multibagger_episodes(panel, threshold=3.0, window_months=24)
    stats = mb.episode_summary_stats(eps)
    assert stats["total_episodes"] >= 2
    assert stats["return_p50"] >= 3.0


@_test("episode_summary_stats: empty input returns count 0")
def test_summary_empty():
    import kr_multibagger as mb
    stats = mb.episode_summary_stats(pd.DataFrame())
    assert stats == {"count": 0}


@_test("define_multibagger_episodes: empty panel returns empty DataFrame")
def test_define_empty():
    import kr_multibagger as mb
    eps = mb.define_multibagger_episodes(pd.DataFrame())
    assert eps.empty
    assert "ticker" in eps.columns
    assert "max_return" in eps.columns


@_test("kr_config: multibagger constants registered")
def test_config_constants():
    from kr_config import DEFAULT_CFG
    assert DEFAULT_CFG.get("multibagger_return_threshold") == 3.0
    assert DEFAULT_CFG.get("multibagger_window_months") == 24
    assert DEFAULT_CFG.get("multibagger_min_mcap_krw") == 5e11


print()
print("=" * 60)
print(f"Multibagger tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
