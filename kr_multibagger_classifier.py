"""kr_multibagger_classifier — Pre-surge entry classifier (P_MB.2).

Trains a binary classifier on the multibagger episode panel:
  Y = 1 if (rebalance_date, ticker) is within [surge_start - 6m, surge_start + 3m]
  X = pre-surge feature vector (P0 + P1 + P2 + P2.5 + P2.6 + P3 features)

Walk-forward 5-fold cross-validation. CatBoost binary objective.

Outputs:
  - research/06_walkforward_baselines/p_mb_v1_classifier_metrics.json
    AUC, precision@K, top-K monthly picks
  - research/06_walkforward_baselines/p_mb_v1_feature_importance.csv
  - research/06_walkforward_baselines/p_mb_v1_top_picks.csv

Pipeline:
  1. Load episode panel (kr_multibagger.load_or_build_episode_panel)
  2. For each (ticker, rebalance_date) in scored_panel, label is_pre_surge
  3. Drop ID/non-numeric columns; fill NaN
  4. Walk-forward time-series CV
  5. Train CatBoost; record AUC + feature importance
  6. Predict for full panel; rank Top-K per month → save picks
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from kr_config import DATA_ROOT, DEFAULT_CFG, KR_ENGINE_REUSE_VERSION, PROJECT_ROOT
from kr_helpers import log


# ---------------------------------------------------------------------------
# Label generation
# ---------------------------------------------------------------------------
def label_pre_surge(
    scored_panel: pd.DataFrame,
    episodes: pd.DataFrame,
    pre_surge_months: int = 6,
    post_surge_months: int = 3,
) -> pd.DataFrame:
    """Add `is_pre_surge` (0/1) label to scored_panel.

    For each episode (ticker, surge_start_date), label rows where:
      ticker matches AND
      surge_start - pre_surge_months <= rebalance_date <= surge_start + post_surge_months
    """
    if scored_panel.empty:
        return scored_panel

    out = scored_panel.copy()
    out["is_pre_surge"] = 0

    if episodes is None or episodes.empty or "surge_start_date" not in episodes.columns:
        return out

    n_labeled = 0
    for _, ep in episodes.iterrows():
        if pd.isna(ep.get("surge_start_date")):
            continue
        ssd = pd.Timestamp(ep["surge_start_date"])
        window_start = ssd - pd.DateOffset(months=pre_surge_months)
        window_end = ssd + pd.DateOffset(months=post_surge_months)
        tk = str(ep["ticker"])
        mask = (
            (out["ticker"].astype(str) == tk)
            & (out["rebalance_date"] >= window_start)
            & (out["rebalance_date"] <= window_end)
        )
        out.loc[mask, "is_pre_surge"] = 1
        n_labeled += int(mask.sum())

    log(f"[mb-classifier] labeled {n_labeled} positive (pre-surge) rows "
        f"out of {len(out)} total")
    return out


# ---------------------------------------------------------------------------
# Feature column selection
# ---------------------------------------------------------------------------
EXCLUDE_COLS_DEFAULT = {
    # ID + meta
    "ticker", "name", "exchange", "rebalance_date", "corp_code",
    "exclude_reason", "eligible", "stage_label", "regime_label_kr",
    # Label
    "is_pre_surge",
    # Targets / leakage
    "p0_momentum_score", "p1_blended_score", "p2_blended_score",
    "p1_momentum_score",   # alias
}


def select_feature_columns(
    panel: pd.DataFrame,
    nan_threshold: float = 0.50,
    extra_excludes: Optional[set] = None,
) -> list[str]:
    """Pick numeric+bool columns with NaN rate < threshold.

    Drops ID columns, label, and any columns >50% NaN.
    """
    if panel.empty:
        return []
    excl = set(EXCLUDE_COLS_DEFAULT)
    if extra_excludes:
        excl |= extra_excludes

    candidates = []
    for col in panel.columns:
        if col in excl:
            continue
        s = panel[col]
        if not (pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s)):
            continue
        nan_rate = float(s.isna().mean())
        if nan_rate > nan_threshold:
            continue
        candidates.append(col)
    return candidates


# ---------------------------------------------------------------------------
# Walk-forward CV split
# ---------------------------------------------------------------------------
def walk_forward_splits(
    panel: pd.DataFrame, n_folds: int = 5, embargo_days: int = 126
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Time-series walk-forward splits on rebalance_date.

    Returns list of (train_idx, test_idx). Each test window starts after the
    previous one + embargo_days lookahead.

    NOTE: Despite the `embargo_days` argument, this legacy implementation does
    NOT actually drop train rows that overlap the test fold's label window —
    the label window for the multibagger task is ±9 months wide, so the last
    train fold can leak into the test fold. Use walk_forward_splits_purged
    (Phase C3) for the strict version.
    """
    if panel.empty:
        return []
    rds = sorted(panel["rebalance_date"].unique())
    n = len(rds)
    if n < n_folds + 1:
        return []
    fold_size = n // (n_folds + 1)
    splits = []
    panel = panel.reset_index(drop=True)
    for k in range(n_folds):
        train_end_idx = (k + 1) * fold_size
        test_start_idx = train_end_idx
        test_end_idx = min(train_end_idx + fold_size, n)
        if test_start_idx >= n:
            break
        train_dates = set(rds[:train_end_idx])
        test_dates = set(rds[test_start_idx:test_end_idx])
        train_idx = panel.index[panel["rebalance_date"].isin(train_dates)].to_numpy()
        test_idx = panel.index[panel["rebalance_date"].isin(test_dates)].to_numpy()
        if len(train_idx) > 100 and len(test_idx) > 10:
            splits.append((train_idx, test_idx))
    return splits


def walk_forward_splits_purged(
    panel: pd.DataFrame,
    n_folds: int = 5,
    embargo_months: int = 9,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Strict purged walk-forward (López de Prado, applied to monthly panel).

    The multibagger label spans (surge_start - 6m, surge_start + 3m), i.e. a
    9-month window. Without an explicit embargo, the last 9 months of train
    can label-leak into the first month of test. This function drops the
    final `embargo_months` of train rows preceding each test fold.

    Args:
        panel: long-format DataFrame with `rebalance_date` column.
        n_folds: number of forward folds.
        embargo_months: rolling buffer between train end and test start
            (default 9 = label half-width).

    Returns:
        List of (train_idx, test_idx) tuples on the panel's positional index.
        Returns [] if the panel has fewer than n_folds + embargo months.
    """
    if panel.empty:
        return []
    rds = sorted(panel["rebalance_date"].unique())
    n = len(rds)
    if n < n_folds + embargo_months + 1:
        return []
    fold_size = n // (n_folds + 1)
    splits = []
    panel = panel.reset_index(drop=True)
    for k in range(n_folds):
        train_end_pos = (k + 1) * fold_size
        # Embargo: drop the last `embargo_months` train rebalance_dates
        train_dates = set(rds[: max(0, train_end_pos - embargo_months)])
        test_dates = set(rds[train_end_pos: train_end_pos + fold_size])
        if not train_dates or not test_dates:
            continue
        train_idx = panel.index[panel["rebalance_date"].isin(train_dates)].to_numpy()
        test_idx = panel.index[panel["rebalance_date"].isin(test_dates)].to_numpy()
        if len(train_idx) > 100 and len(test_idx) > 10:
            splits.append((train_idx, test_idx))
    return splits


# ---------------------------------------------------------------------------
# Three-label sleeve separation (Phase C3)
# ---------------------------------------------------------------------------
def label_pre_entry(
    scored_panel: pd.DataFrame,
    episodes: pd.DataFrame,
    pre_surge_months: int = 6,
    pre_buffer_months: int = 1,
) -> pd.DataFrame:
    """Pre-entry label: surge_start - pre_surge_months <= rd < surge_start - pre_buffer_months.

    Captures the "early" window (-6m to -1m) where value/turnaround signals
    discriminate; excludes the immediate pre-surge run-up so the model does
    not learn pure short-momentum.
    """
    if scored_panel.empty:
        return scored_panel
    out = scored_panel.copy()
    out["is_pre_entry"] = 0
    if episodes is None or episodes.empty or "surge_start_date" not in episodes.columns:
        return out
    n_labeled = 0
    for _, ep in episodes.iterrows():
        if pd.isna(ep.get("surge_start_date")):
            continue
        ssd = pd.Timestamp(ep["surge_start_date"])
        window_start = ssd - pd.DateOffset(months=pre_surge_months)
        window_end = ssd - pd.DateOffset(months=pre_buffer_months)
        tk = str(ep["ticker"])
        mask = (
            (out["ticker"].astype(str) == tk)
            & (out["rebalance_date"] >= window_start)
            & (out["rebalance_date"] < window_end)
        )
        out.loc[mask, "is_pre_entry"] = 1
        n_labeled += int(mask.sum())
    log(f"[mb-classifier] label_pre_entry: {n_labeled} positive rows "
        f"out of {len(out)}")
    return out


def label_continuation(
    scored_panel: pd.DataFrame,
    episodes: pd.DataFrame,
    post_surge_months: int = 3,
) -> pd.DataFrame:
    """Continuation label: surge_start <= rd <= surge_start + post_surge_months.

    Captures the "ride" window where momentum / RS / trend-template signals
    work. Disjoint from label_pre_entry by construction so a single model
    is not asked to do both jobs.
    """
    if scored_panel.empty:
        return scored_panel
    out = scored_panel.copy()
    out["is_continuation"] = 0
    if episodes is None or episodes.empty or "surge_start_date" not in episodes.columns:
        return out
    n_labeled = 0
    for _, ep in episodes.iterrows():
        if pd.isna(ep.get("surge_start_date")):
            continue
        ssd = pd.Timestamp(ep["surge_start_date"])
        window_start = ssd
        window_end = ssd + pd.DateOffset(months=post_surge_months)
        tk = str(ep["ticker"])
        mask = (
            (out["ticker"].astype(str) == tk)
            & (out["rebalance_date"] >= window_start)
            & (out["rebalance_date"] <= window_end)
        )
        out.loc[mask, "is_continuation"] = 1
        n_labeled += int(mask.sum())
    log(f"[mb-classifier] label_continuation: {n_labeled} positive rows "
        f"out of {len(out)}")
    return out


def label_risk(
    scored_panel: pd.DataFrame,
    horizon_months: int = 1,
    drawdown_threshold: float = -0.20,
) -> pd.DataFrame:
    """Risk label: 1 when forward `horizon_months` realized drawdown <= -20%.

    Requires `forward_min_return_<H>m` (or close-based forward MDD) column;
    when absent, falls back to forward_return_<H>m <= drawdown_threshold.

    The risk model is the defensive sleeve's gating signal — high probability
    forward DD steers the position toward cash.
    """
    if scored_panel.empty:
        return scored_panel
    out = scored_panel.copy()
    out["is_risk"] = 0
    fwd_min_col = f"forward_min_return_{horizon_months}m"
    fwd_ret_col = f"forward_return_{horizon_months}m"
    use_col = None
    if fwd_min_col in out.columns:
        use_col = fwd_min_col
    elif fwd_ret_col in out.columns:
        use_col = fwd_ret_col
    if use_col is None:
        log(f"[mb-classifier] label_risk: no {fwd_min_col} or {fwd_ret_col} "
            f"column -> all is_risk=0", level="WARN")
        return out
    fwd = pd.to_numeric(out[use_col], errors="coerce")
    out["is_risk"] = (fwd <= drawdown_threshold).fillna(False).astype(int)
    pos = int(out["is_risk"].sum())
    log(f"[mb-classifier] label_risk: {pos} positive (DD<={drawdown_threshold:.0%}) "
        f"using {use_col} ({pos/max(len(out),1):.2%} prevalence)")
    return out


# ---------------------------------------------------------------------------
# Calibration + adversarial validation helpers (Phase C3)
# ---------------------------------------------------------------------------
def calibration_curve(
    y_true: np.ndarray,
    y_proba: np.ndarray,
    bins: Optional[list[float]] = None,
) -> pd.DataFrame:
    """Predicted-probability vs realized-frequency table.

    Returns DataFrame: bin_low, bin_high, n, predicted_mean, actual_rate,
    abs_error.
    """
    if bins is None:
        bins = [0.0, 0.1, 0.3, 0.5, 0.7, 0.85, 1.01]
    rows = []
    y_true = np.asarray(y_true).astype(int)
    y_proba = np.asarray(y_proba).astype(float)
    for low, high in zip(bins[:-1], bins[1:]):
        mask = (y_proba >= low) & (y_proba < high)
        n = int(mask.sum())
        if n == 0:
            continue
        actual = float(y_true[mask].mean())
        pred = float(y_proba[mask].mean())
        rows.append({
            "bin_low": float(low),
            "bin_high": float(high),
            "n": n,
            "predicted_mean": pred,
            "actual_rate": actual,
            "abs_error": float(abs(pred - actual)),
        })
    return pd.DataFrame(rows)


def adversarial_validation(
    panel: pd.DataFrame,
    feature_cols: list[str],
    train_mask: np.ndarray,
    test_mask: np.ndarray,
) -> dict:
    """Train binary classifier to distinguish train vs test rows.

    AUC > ~0.7 indicates substantial distribution shift (train and test
    are easily separable on features alone). Use to flag features that
    likely won't generalize forward.

    Returns dict: auc, top_drift_features (names sorted by importance).
    """
    try:
        from catboost import CatBoostClassifier
        from sklearn.metrics import roc_auc_score
    except ImportError:
        return {"error": "catboost / sklearn missing"}

    if panel.empty or not feature_cols:
        return {"error": "empty input"}

    n_train = int(train_mask.sum())
    n_test = int(test_mask.sum())
    if n_train < 50 or n_test < 50:
        return {"error": f"insufficient size (train={n_train}, test={n_test})"}

    X_all = panel.loc[train_mask | test_mask, feature_cols].fillna(0).values
    y_all = np.concatenate([
        np.zeros(n_train, dtype=int),
        np.ones(n_test, dtype=int),
    ])
    # Random shuffle to mix
    perm = np.random.permutation(len(y_all))
    X_all = X_all[perm]
    y_all = y_all[perm]

    cb = CatBoostClassifier(
        iterations=200, depth=5, learning_rate=0.05, verbose=0,
        random_seed=42, loss_function="Logloss", eval_metric="AUC",
    )
    cut = len(y_all) // 2
    cb.fit(X_all[:cut], y_all[:cut])
    proba = cb.predict_proba(X_all[cut:])[:, 1]
    auc = float(roc_auc_score(y_all[cut:], proba))

    importances = cb.get_feature_importance()
    top_pairs = sorted(
        zip(feature_cols, importances), key=lambda t: -t[1]
    )[:10]
    return {
        "auc": auc,
        "top_drift_features": [name for name, _ in top_pairs],
        "top_drift_importances": [float(v) for _, v in top_pairs],
    }


# ---------------------------------------------------------------------------
# Train + evaluate
# ---------------------------------------------------------------------------
def train_entry_classifier(
    labeled_panel: pd.DataFrame,
    feature_cols: list[str],
    n_folds: int = 5,
    cb_params: Optional[dict] = None,
) -> dict:
    """Walk-forward train CatBoost binary classifier.

    Returns dict with metrics + per-fold AUC + final model.
    """
    try:
        from catboost import CatBoostClassifier
    except ImportError:
        log("[mb-classifier] catboost not installed", level="ERROR")
        return {"error": "catboost not installed"}

    if "is_pre_surge" not in labeled_panel.columns:
        return {"error": "is_pre_surge label missing"}
    if not feature_cols:
        return {"error": "no feature columns selected"}

    pos = int(labeled_panel["is_pre_surge"].sum())
    neg = int(len(labeled_panel) - pos)
    log(f"[mb-classifier] positives: {pos} / negatives: {neg} "
        f"(prevalence {pos/max(len(labeled_panel),1):.3%})")

    if pos < 50:
        return {"error": f"too few positive examples: {pos}"}

    cb_default = {
        "iterations": 400,
        "learning_rate": 0.05,
        "depth": 6,
        "loss_function": "Logloss",
        "eval_metric": "AUC",
        "verbose": 0,
        "random_seed": 42,
        "auto_class_weights": "Balanced",   # imbalanced data
    }
    cb_params = {**cb_default, **(cb_params or {})}

    splits = walk_forward_splits(labeled_panel, n_folds=n_folds)
    if not splits:
        return {"error": "insufficient data for walk-forward"}

    fold_aucs = []
    fold_precision_at_30 = []
    final_importance = None
    for k, (train_idx, test_idx) in enumerate(splits, 1):
        X_train = labeled_panel.iloc[train_idx][feature_cols].fillna(0).values
        y_train = labeled_panel.iloc[train_idx]["is_pre_surge"].values
        X_test = labeled_panel.iloc[test_idx][feature_cols].fillna(0).values
        y_test = labeled_panel.iloc[test_idx]["is_pre_surge"].values

        if y_train.sum() < 5 or y_test.sum() < 1:
            log(f"[mb-classifier] fold {k} skipped (insufficient positives)", level="WARN")
            continue

        model = CatBoostClassifier(**cb_params)
        model.fit(X_train, y_train, eval_set=(X_test, y_test), early_stopping_rounds=50)
        proba = model.predict_proba(X_test)[:, 1]

        from sklearn.metrics import roc_auc_score
        try:
            auc = float(roc_auc_score(y_test, proba))
        except Exception:
            auc = float("nan")
        fold_aucs.append(auc)

        # Precision @ K=30 per test fold
        k_int = min(30, len(proba))
        top_k_idx = np.argsort(proba)[-k_int:]
        precision_at_k = float(y_test[top_k_idx].mean())
        fold_precision_at_30.append(precision_at_k)

        log(f"[mb-classifier] fold {k}/{len(splits)}: AUC={auc:.4f}, "
            f"P@30={precision_at_k:.3f}")

        if k == len(splits):
            # Save importance from last fold
            try:
                importance = model.get_feature_importance()
                final_importance = pd.DataFrame({
                    "feature": feature_cols,
                    "importance": importance,
                }).sort_values("importance", ascending=False).reset_index(drop=True)
            except Exception as e:
                log(f"[mb-classifier] importance fail: {e}", level="WARN")

    if not fold_aucs:
        return {"error": "no folds completed"}

    out = {
        "n_folds": len(fold_aucs),
        "fold_auc": fold_aucs,
        "auc_mean": float(np.mean(fold_aucs)),
        "auc_std": float(np.std(fold_aucs)),
        "fold_precision_at_30": fold_precision_at_30,
        "p_at_30_mean": float(np.mean(fold_precision_at_30)),
        "n_features": len(feature_cols),
        "n_positives": int(pos),
        "n_total": int(len(labeled_panel)),
        "feature_importance_top20": (
            final_importance.head(20).to_dict(orient="records")
            if final_importance is not None else []
        ),
    }
    return out


# ---------------------------------------------------------------------------
# Top-K per-month picks (live mode after training)
# ---------------------------------------------------------------------------
def predict_topk_picks(
    labeled_panel: pd.DataFrame,
    feature_cols: list[str],
    k_per_month: int = 30,
    cb_params: Optional[dict] = None,
) -> pd.DataFrame:
    """Train on full data, predict P(pre_surge) for all rows, return top-K per month."""
    try:
        from catboost import CatBoostClassifier
    except ImportError:
        return pd.DataFrame()

    if labeled_panel.empty or "is_pre_surge" not in labeled_panel.columns:
        return pd.DataFrame()

    cb_default = {
        "iterations": 400, "learning_rate": 0.05, "depth": 6,
        "loss_function": "Logloss", "verbose": 0,
        "random_seed": 42, "auto_class_weights": "Balanced",
    }
    cb_params = {**cb_default, **(cb_params or {})}

    X = labeled_panel[feature_cols].fillna(0).values
    y = labeled_panel["is_pre_surge"].values
    if y.sum() < 50:
        return pd.DataFrame()

    model = CatBoostClassifier(**cb_params)
    model.fit(X, y)
    proba = model.predict_proba(X)[:, 1]

    out = labeled_panel[["rebalance_date", "ticker"]].copy()
    if "name" in labeled_panel.columns:
        out["name"] = labeled_panel["name"]
    if "market_cap" in labeled_panel.columns:
        out["market_cap"] = labeled_panel["market_cap"]
    out["p_pre_surge"] = proba

    # Top-K per month
    out = out.sort_values(["rebalance_date", "p_pre_surge"], ascending=[True, False])
    out["rank_in_month"] = out.groupby("rebalance_date")["p_pre_surge"].rank(
        method="first", ascending=False,
    ).astype(int)
    return out[out["rank_in_month"] <= k_per_month].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------
def run_p_mb_v1(cfg: Optional[dict] = None) -> dict:
    """Full pipeline: load episodes + scored_panel → label → train → save outputs.

    Requires:
      - feature_store/scored_panel_v0_*.parquet (from kr_pipeline.build_scored_panel_v0)
      - feature_store/multibagger_episodes_v0_*.parquet (from kr_multibagger)
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}

    from kr_multibagger import load_or_build_episode_panel

    # 1. Load scored panel
    fs_dir = DATA_ROOT / "feature_store"
    scored_files = sorted(fs_dir.glob("scored_panel_v0_*.parquet"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
    if not scored_files:
        return {"error": "no scored_panel found — run kr_pipeline.build_scored_panel_v0 first"}

    scored = pd.read_parquet(scored_files[0])
    log(f"[mb-classifier] loaded scored panel: {scored.shape}")

    # 2. Load episodes
    episodes = load_or_build_episode_panel(cfg=cfg, refresh=False)
    if episodes.empty:
        return {"error": "no episodes — run kr_multibagger first"}
    log(f"[mb-classifier] loaded episodes: {len(episodes)}")

    # 3. Label
    pre_surge_m = int(cfg.get("multibagger_pre_surge_lookback_months", 6))
    post_surge_m = int(cfg.get("multibagger_post_surge_include_months", 3))
    labeled = label_pre_surge(scored, episodes, pre_surge_m, post_surge_m)

    # 4. Feature selection
    feature_cols = select_feature_columns(labeled)
    log(f"[mb-classifier] selected {len(feature_cols)} feature columns")

    # 5. Train
    metrics = train_entry_classifier(labeled, feature_cols, n_folds=5)
    if "error" in metrics:
        return metrics

    # 6. Top-K picks
    picks = predict_topk_picks(labeled, feature_cols, k_per_month=30)

    # 7. Save outputs
    out_dir = PROJECT_ROOT / "research" / "06_walkforward_baselines"
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics_path = out_dir / "p_mb_v1_classifier_metrics.json"
    importance_path = out_dir / "p_mb_v1_feature_importance.csv"
    picks_path = out_dir / "p_mb_v1_top_picks.csv"

    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str, ensure_ascii=False)
    if metrics.get("feature_importance_top20"):
        pd.DataFrame(metrics["feature_importance_top20"]).to_csv(importance_path, index=False)
    if not picks.empty:
        picks.to_csv(picks_path, index=False)

    log(f"[mb-classifier] saved:\n  - {metrics_path}\n  - {importance_path}\n  - {picks_path}")

    return {
        "metrics": metrics,
        "metrics_path": str(metrics_path),
        "importance_path": str(importance_path),
        "picks_path": str(picks_path),
        "n_picks": len(picks),
    }


if __name__ == "__main__":
    import sys
    result = run_p_mb_v1()
    if "error" in result:
        print(f"FAIL: {result['error']}")
        sys.exit(1)
    print("=" * 60)
    print("P_MB.2 Multibagger Classifier — V1 results")
    print("=" * 60)
    m = result["metrics"]
    print(f"  AUC mean    : {m['auc_mean']:.4f} (std {m['auc_std']:.4f})")
    print(f"  P@30 mean   : {m['p_at_30_mean']:.3f}")
    print(f"  N folds     : {m['n_folds']}")
    print(f"  N features  : {m['n_features']}")
    print(f"  N positives : {m['n_positives']} / {m['n_total']}")
    print(f"  Top picks   : {result['n_picks']}")
    print("\n  Top 10 features by importance:")
    for r in m.get("feature_importance_top20", [])[:10]:
        print(f"    {r['feature']:40s} = {r['importance']:.3f}")
