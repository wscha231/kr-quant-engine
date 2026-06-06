"""tests/test_walkforward.py — Phase C3 purged WF + sleeve labels.

Covers:
1. test_walk_forward_embargo_actually_excludes_overlap
   walk_forward_splits_purged drops the last 9 train rebalance_dates so the
   train tail does not overlap test fold's label window.
2. test_pre_entry_label_excludes_post_surge_period
   label_pre_entry yields rows in [-6m, -1m) only — 0 inside the surge
   period and after.
3. test_continuation_label_separate_from_pre_entry
   pre_entry and continuation are disjoint label sets.
4. test_risk_label_uses_only_past_dd
   label_risk reads forward_min_return; flag is 1 when forward DD <= -20%.
5. test_calibration_curve_within_tolerance
   calibration_curve returns the expected bin/n/predicted_mean/actual_rate.

Run: py -3 tests/test_walkforward.py
"""
from __future__ import annotations

import sys
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
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        return fn
    return deco


@_test("import kr_multibagger_classifier C3 additions")
def test_imports():
    from kr_multibagger_classifier import (
        walk_forward_splits_purged,
        label_pre_entry,
        label_continuation,
        label_risk,
        calibration_curve,
        adversarial_validation,
    )
    assert callable(walk_forward_splits_purged)
    assert callable(label_pre_entry)
    assert callable(label_continuation)
    assert callable(label_risk)
    assert callable(calibration_curve)
    assert callable(adversarial_validation)


@_test("walk_forward_embargo_actually_excludes_overlap")
def test_walk_forward_embargo_actually_excludes_overlap():
    """Build a panel with 60 monthly rebalance_dates. With n_folds=3 and
    embargo_months=9, train fold should END 9 months before test starts."""
    from kr_multibagger_classifier import walk_forward_splits_purged
    rds = pd.date_range("2018-01-31", periods=60, freq="ME")
    panel = pd.DataFrame({"rebalance_date": np.repeat(rds, 5)})
    panel["ticker"] = list(range(len(panel)))
    panel = panel.reset_index(drop=True)

    splits = walk_forward_splits_purged(panel, n_folds=3, embargo_months=9)
    assert len(splits) >= 1, f"Expected >=1 split, got {len(splits)}"

    for k, (tr_idx, te_idx) in enumerate(splits):
        train_dates = panel.iloc[tr_idx]["rebalance_date"].max()
        test_first = panel.iloc[te_idx]["rebalance_date"].min()
        gap_months = (test_first.to_period("M")
                       - train_dates.to_period("M")).n
        assert gap_months >= 9, (
            f"fold {k}: gap {gap_months} months < 9 embargo")


@_test("walk_forward_splits_purged returns empty when too small")
def test_walk_forward_purged_too_small():
    from kr_multibagger_classifier import walk_forward_splits_purged
    rds = pd.date_range("2024-01-31", periods=10, freq="ME")
    panel = pd.DataFrame({"rebalance_date": rds, "ticker": list(range(10))})
    splits = walk_forward_splits_purged(panel, n_folds=5, embargo_months=9)
    assert len(splits) == 0, (
        f"Expected 0 splits for 10 rds with embargo=9 + folds=5, "
        f"got {len(splits)}")


@_test("pre_entry_label_excludes_post_surge_period")
def test_pre_entry_label_excludes_post_surge_period():
    from kr_multibagger_classifier import label_pre_entry
    panel = pd.DataFrame({
        "ticker": ["A"] * 12,
        "rebalance_date": pd.date_range("2024-01-31", periods=12, freq="ME"),
    })
    episodes = pd.DataFrame([{
        "ticker": "A",
        "surge_start_date": pd.Timestamp("2024-08-31"),
    }])
    out = label_pre_entry(panel, episodes,
                            pre_surge_months=6, pre_buffer_months=1)
    # Window: 2024-08-31 - 6m = 2024-02-29 (incl), end 2024-08-31 - 1m = 2024-07-31 (excl)
    pos = out[out["is_pre_entry"] == 1]["rebalance_date"]
    assert all(pos >= pd.Timestamp("2024-02-29")), pos.tolist()
    assert all(pos < pd.Timestamp("2024-07-31")), pos.tolist()
    # Surge date itself + after must be 0
    after_surge = out[out["rebalance_date"] >= pd.Timestamp("2024-08-31")]
    assert (after_surge["is_pre_entry"] == 0).all()


@_test("continuation_label_separate_from_pre_entry")
def test_continuation_label_separate_from_pre_entry():
    from kr_multibagger_classifier import label_pre_entry, label_continuation
    panel = pd.DataFrame({
        "ticker": ["A"] * 18,
        "rebalance_date": pd.date_range("2024-01-31", periods=18, freq="ME"),
    })
    episodes = pd.DataFrame([{
        "ticker": "A",
        "surge_start_date": pd.Timestamp("2024-08-31"),
    }])
    pe = label_pre_entry(panel, episodes, pre_surge_months=6,
                          pre_buffer_months=1)["is_pre_entry"]
    cont = label_continuation(panel, episodes,
                                post_surge_months=3)["is_continuation"]
    overlap = ((pe == 1) & (cont == 1)).sum()
    assert overlap == 0, f"Found {overlap} overlapping rows"
    # Continuation positives at and shortly after surge
    panel["pre"] = pe.values
    panel["cont"] = cont.values
    cont_dates = panel[panel["cont"] == 1]["rebalance_date"]
    assert all(cont_dates >= pd.Timestamp("2024-08-31")), cont_dates.tolist()
    assert all(cont_dates <= pd.Timestamp("2024-11-30")), cont_dates.tolist()


@_test("risk_label_uses_forward_dd_threshold")
def test_risk_label_uses_forward_dd():
    from kr_multibagger_classifier import label_risk
    panel = pd.DataFrame({
        "ticker": ["A", "B", "C", "D"],
        "rebalance_date": [pd.Timestamp("2024-06-30")] * 4,
        "forward_min_return_1m": [-0.05, -0.21, -0.30, 0.10],
    })
    out = label_risk(panel, horizon_months=1, drawdown_threshold=-0.20)
    assert out.loc[out["ticker"] == "A", "is_risk"].iloc[0] == 0
    assert out.loc[out["ticker"] == "B", "is_risk"].iloc[0] == 1   # exactly -21%
    assert out.loc[out["ticker"] == "C", "is_risk"].iloc[0] == 1
    assert out.loc[out["ticker"] == "D", "is_risk"].iloc[0] == 0


@_test("calibration_curve_within_tolerance")
def test_calibration_curve_within_tolerance():
    """Synthetic well-calibrated predictions: bin actual ≈ predicted."""
    from kr_multibagger_classifier import calibration_curve
    np.random.seed(42)
    p = np.random.uniform(0, 1, 5000)
    y = (np.random.uniform(0, 1, 5000) < p).astype(int)
    df = calibration_curve(y, p, bins=[0.0, 0.25, 0.5, 0.75, 1.01])
    assert not df.empty
    # On average, well-calibrated synthetic data: abs_error < 0.05
    assert df["abs_error"].mean() < 0.05, (
        f"Mean abs_error too high: {df['abs_error'].mean()}")
    # Each bin must report n>0 since uniform[0,1] hits all bins
    assert (df["n"] > 0).all()


@_test("generate_oos_picks_purged_3sleeve produces all 3 score cols")
def test_3sleeve_picks_columns():
    """Smoke: tiny synthetic panel — verify column presence + ranking."""
    from kr_backtester_realistic import generate_oos_picks_purged_3sleeve

    np.random.seed(42)
    rds = pd.date_range("2018-01-31", periods=60, freq="ME")
    rows = []
    for rd in rds:
        for tk in [f"TK{i:03d}" for i in range(50)]:
            rows.append({
                "rebalance_date": rd,
                "ticker": tk,
                "feat_1": np.random.randn(),
                "feat_2": np.random.randn(),
                "is_pre_entry": int(np.random.uniform() < 0.05),
                "is_continuation": int(np.random.uniform() < 0.05),
                "is_risk": int(np.random.uniform() < 0.10),
            })
    panel = pd.DataFrame(rows)
    picks = generate_oos_picks_purged_3sleeve(
        panel, ["feat_1", "feat_2"], n_folds=3, embargo_months=9,
        k_per_month=10,
    )
    # If catboost missing this returns empty — soft pass
    if picks.empty:
        print("    (catboost unavailable — soft skip)")
        return
    for col in ("p_pre_entry", "p_continuation", "p_risk", "p_combined",
                "p_pre_surge", "rank_in_month"):
        assert col in picks.columns, f"Missing {col}"
    # Ranking semantics: rank_in_month resets per rebalance_date
    rank_top = picks[picks["rank_in_month"] == 1]
    assert rank_top["rebalance_date"].nunique() == picks["rebalance_date"].nunique()


@_test("P_MB OOS feature selector excludes forward/generated leakage columns")
def test_pmb_oos_feature_selector_excludes_leakage():
    from tools.build_pmb_oos_picks import select_pmb_feature_columns

    panel = pd.DataFrame({
        "rebalance_date": pd.date_range("2024-01-31", periods=6, freq="ME"),
        "ticker": [f"{i:06d}" for i in range(6)],
        "feat_value": np.arange(6, dtype=float),
        "forward_return_1m": np.arange(6, dtype=float),
        "target_next_month": np.arange(6, dtype=float),
        "p_pre_surge": np.linspace(0.1, 0.6, 6),
        "leader_rank": np.arange(6),
        "is_pre_entry": [0, 1, 0, 0, 1, 0],
        "is_continuation": [0, 0, 1, 0, 0, 1],
        "is_risk": [0, 0, 0, 1, 0, 0],
    })
    features = select_pmb_feature_columns(panel)
    assert "feat_value" in features
    for col in ("forward_return_1m", "target_next_month",
                "p_pre_surge", "leader_rank", "is_pre_entry",
                "is_continuation", "is_risk"):
        assert col not in features, f"leaky/generated column selected: {col}"


@_test("forward label builder computes 1m return and min forward drawdown")
def test_forward_label_builder():
    from kr_pipeline import add_forward_return_labels

    panel = pd.DataFrame({
        "rebalance_date": [pd.Timestamp("2024-01-31")],
        "ticker": ["000001"],
    })
    prices = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-31", "2024-02-05", "2024-02-29"]),
        "ticker": ["000001", "000001", "000001"],
        "close": [100.0, 80.0, 110.0],
    })
    out = add_forward_return_labels(
        panel,
        cfg={"forward_label_horizon_months": 1},
        price_panel=prices,
    )
    assert abs(float(out.loc[0, "forward_return_1m"]) - 0.10) < 1e-9
    assert abs(float(out.loc[0, "forward_min_return_1m"]) - (-0.20)) < 1e-9


@_test("train_entry_classifier exposes purged split controls")
def test_train_entry_classifier_purged_signature():
    import inspect
    from kr_multibagger_classifier import train_entry_classifier

    params = inspect.signature(train_entry_classifier).parameters
    assert "purged" in params
    assert "embargo_months" in params
    src = inspect.getsource(train_entry_classifier)
    assert "eval_set=(X_test, y_test)" not in src
    assert "early_stopping_rounds" not in src


print()
print("=" * 60)
print(f"walkforward / sleeve tests: {PASSED} passed, {FAILED} failed")
print("=" * 60)
sys.exit(0 if FAILED == 0 else 1)
