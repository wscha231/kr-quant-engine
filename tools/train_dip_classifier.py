"""tools/train_dip_classifier.py — Phase F.F3 trainer.

Trains the shake-out vs distribution classifier on the dip episode panel
produced by F1 (tools/mine_dip_episodes.py) and the 42-feature panel
from F2 (kr_dip_features.prepare_dip_feature_panel).

Default:
  - CatBoost binary as primary
  - LightGBM ranker for blend (when lightgbm available)
  - Purged walk-forward CV by dip_date (9-month embargo, matches outcome
    window upper bound — closer to standard 30 trading days but bigger
    embargo because dip event clusters within sectors)

Persists:
  data_pit/models/dip_classifier_<stamp>.cbm
  data_pit/models/dip_classifier_latest.cbm
  data_pit/models/dip_classifier_latest_metrics.json (with feature_cols)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, KR_ENGINE_REUSE_VERSION  # noqa: E402
from kr_helpers import log  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dip classifier trainer")
    p.add_argument("--feature-panel", required=True,
                   help="Parquet path of the 42-feature panel "
                        "(output of kr_dip_features.prepare_dip_feature_panel).")
    p.add_argument("--n-folds", type=int, default=5)
    p.add_argument("--embargo-days", type=int, default=60,
                   help="Embargo window in business days (default 60 = 3m).")
    p.add_argument("--blend-weights", default="0.7,0.3",
                   help="Comma-separated weights for CatBoost,LightGBM "
                        "(default 0.7,0.3 since CatBoost handles tabular better).")
    p.add_argument("--out-dir", default=None,
                   help="Output dir (default DATA_ROOT/models).")
    return p.parse_args()


def _purged_splits_by_date(
    panel: pd.DataFrame,
    n_folds: int,
    embargo_days: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Walk-forward purged splits keyed by `dip_date`.

    Differs from the existing kr_multibagger_classifier purged splits
    (which assume a monthly rebalance_date column) — here dips happen on
    arbitrary trading days, so we sort all unique dates and split by
    quantiles, dropping the last `embargo_days` of each train fold.
    """
    if panel.empty or "dip_date" not in panel.columns:
        return []
    dates = pd.to_datetime(panel["dip_date"])
    unique_dates = np.sort(dates.unique())
    n = len(unique_dates)
    if n < n_folds + 1:
        return []
    # Allocate dates into n_folds + 1 chunks; last chunk = test of fold k
    chunk = n // (n_folds + 1)
    splits = []
    panel_idx = panel.reset_index(drop=True)
    for k in range(n_folds):
        train_end_pos = (k + 1) * chunk
        embargo_pos = max(0, train_end_pos - embargo_days)
        train_dates = set(unique_dates[: embargo_pos])
        test_dates = set(unique_dates[train_end_pos: train_end_pos + chunk])
        if not train_dates or not test_dates:
            continue
        train_idx = panel_idx.index[dates.isin(train_dates)].to_numpy()
        test_idx = panel_idx.index[dates.isin(test_dates)].to_numpy()
        if len(train_idx) > 100 and len(test_idx) > 30:
            splits.append((train_idx, test_idx))
    return splits


def main() -> int:
    args = parse_args()
    panel_path = Path(args.feature_panel)
    if not panel_path.exists():
        log(f"[trainer] feature panel not found: {panel_path}", level="ERROR")
        return 1
    panel = pd.read_parquet(panel_path)
    log(f"[trainer] panel: {len(panel)} rows × {len(panel.columns)} cols")

    # Restrict to LABELED rows (drop ambiguous)
    if "label" not in panel.columns:
        log("[trainer] panel lacks 'label' column", level="ERROR")
        return 2
    labeled = panel[panel["label"].isin([0.0, 1.0, 0, 1])].copy()
    labeled["label"] = labeled["label"].astype(int)
    log(f"[trainer] labeled rows: {len(labeled)} "
        f"(shake-out={int((labeled['label'] == 1).sum())}, "
        f"distribution={int((labeled['label'] == 0).sum())})")

    feat_cols = [c for c in labeled.columns if c.startswith("f_")]
    if not feat_cols:
        log("[trainer] no f_* columns found", level="ERROR")
        return 3
    log(f"[trainer] using {len(feat_cols)} features")

    # Drop columns with > 70% NaN (improves CatBoost stability)
    nan_rate = labeled[feat_cols].isna().mean()
    too_sparse = nan_rate[nan_rate > 0.7].index.tolist()
    if too_sparse:
        log(f"[trainer] dropping {len(too_sparse)} sparse features (>70pct NaN): "
            f"{too_sparse[:5]}...", level="WARN")
        feat_cols = [c for c in feat_cols if c not in too_sparse]

    splits = _purged_splits_by_date(
        labeled, n_folds=args.n_folds, embargo_days=args.embargo_days,
    )
    if not splits:
        log("[trainer] insufficient panel for purged splits", level="ERROR")
        return 4
    log(f"[trainer] {len(splits)} purged folds, embargo={args.embargo_days}d")

    # Train per fold
    try:
        from catboost import CatBoostClassifier
        from sklearn.metrics import roc_auc_score, log_loss
    except ImportError as e:
        log(f"[trainer] missing dep: {e}", level="ERROR")
        return 5

    try:
        import lightgbm as lgb
        HAS_LGBM = True
    except ImportError:
        HAS_LGBM = False
        log("[trainer] lightgbm not available -> CatBoost only blend",
            level="WARN")

    cb_params = {
        "iterations": 500, "depth": 6, "learning_rate": 0.04,
        "loss_function": "Logloss", "eval_metric": "AUC",
        "verbose": 0, "random_seed": 42, "auto_class_weights": "Balanced",
    }
    cb_aucs, lgbm_aucs, blend_aucs = [], [], []
    fold_logs = []
    for k, (tr_idx, te_idx) in enumerate(splits, 1):
        X_tr = labeled.iloc[tr_idx][feat_cols].fillna(0).values
        y_tr = labeled.iloc[tr_idx]["label"].values
        X_te = labeled.iloc[te_idx][feat_cols].fillna(0).values
        y_te = labeled.iloc[te_idx]["label"].values
        if y_tr.sum() < 5 or y_te.sum() < 1:
            continue
        cb = CatBoostClassifier(**cb_params)
        cb.fit(X_tr, y_tr)
        p_cb = cb.predict_proba(X_te)[:, 1]
        auc_cb = float(roc_auc_score(y_te, p_cb))
        cb_aucs.append(auc_cb)

        if HAS_LGBM:
            lgbm = lgb.LGBMClassifier(
                n_estimators=400, learning_rate=0.04, num_leaves=31,
                class_weight="balanced", random_state=42, verbose=-1,
            )
            lgbm.fit(X_tr, y_tr)
            p_lgbm = lgbm.predict_proba(X_te)[:, 1]
            auc_lgbm = float(roc_auc_score(y_te, p_lgbm))
            lgbm_aucs.append(auc_lgbm)
            w_cb, w_lg = [float(x) for x in args.blend_weights.split(",")]
            p_blend = w_cb * p_cb + w_lg * p_lgbm
            auc_blend = float(roc_auc_score(y_te, p_blend))
            blend_aucs.append(auc_blend)
        else:
            auc_lgbm = float("nan")
            auc_blend = auc_cb
            blend_aucs.append(auc_cb)

        fold_logs.append({
            "fold": k,
            "n_train": int(len(tr_idx)),
            "n_test": int(len(te_idx)),
            "auc_cb": auc_cb,
            "auc_lgbm": auc_lgbm if HAS_LGBM else None,
            "auc_blend": auc_blend,
        })
        log(f"[trainer] fold {k}/{len(splits)}: CB={auc_cb:.3f}"
            + (f" LGBM={auc_lgbm:.3f} blend={auc_blend:.3f}" if HAS_LGBM else ""))

    if not blend_aucs:
        log("[trainer] no folds completed", level="ERROR")
        return 6

    # Refit on full data for production
    X_full = labeled[feat_cols].fillna(0).values
    y_full = labeled["label"].values
    cb_full = CatBoostClassifier(**cb_params)
    cb_full.fit(X_full, y_full)
    log(f"[trainer] final CB fit on {len(labeled)} rows")

    out_dir = Path(args.out_dir) if args.out_dir else DATA_ROOT / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    cb_path = out_dir / f"dip_classifier_{stamp}.cbm"
    cb_latest = out_dir / "dip_classifier_latest.cbm"
    cb_full.save_model(str(cb_path))
    cb_full.save_model(str(cb_latest))

    if HAS_LGBM:
        lgbm_full = lgb.LGBMClassifier(
            n_estimators=400, learning_rate=0.04, num_leaves=31,
            class_weight="balanced", random_state=42, verbose=-1,
        )
        lgbm_full.fit(X_full, y_full)
        lgbm_path = out_dir / f"dip_classifier_lgbm_{stamp}.txt"
        lgbm_latest = out_dir / "dip_classifier_lgbm_latest.txt"
        # LightGBM C library cannot write to paths containing non-ASCII
        # characters on Windows. Workaround: write to a temp ASCII path
        # and copy to the destination.
        try:
            lgbm_full.booster_.save_model(str(lgbm_path))
            lgbm_full.booster_.save_model(str(lgbm_latest))
        except Exception as e:
            log(f"[trainer] direct LGBM save failed ({e}); using temp+copy",
                level="WARN")
            import tempfile, shutil
            tmp = Path(tempfile.gettempdir()) / f"dip_classifier_lgbm_{stamp}.txt"
            try:
                lgbm_full.booster_.save_model(str(tmp))
                shutil.copy2(tmp, lgbm_path)
                shutil.copy2(tmp, lgbm_latest)
                tmp.unlink(missing_ok=True)
                log(f"[trainer] LGBM saved via temp")
            except Exception as e2:
                log(f"[trainer] LGBM save failed: {e2}", level="WARN")

    metrics = {
        "stamp": stamp,
        "engine_version": KR_ENGINE_REUSE_VERSION,
        "n_panel_rows": int(len(labeled)),
        "n_features": len(feat_cols),
        "feature_cols": feat_cols,
        "n_folds": len(blend_aucs),
        "embargo_days": args.embargo_days,
        "fold_results": fold_logs,
        "auc_cb_mean": float(np.mean(cb_aucs)) if cb_aucs else None,
        "auc_lgbm_mean": float(np.mean(lgbm_aucs)) if lgbm_aucs else None,
        "auc_blend_mean": float(np.mean(blend_aucs)),
        "blend_weights": args.blend_weights,
        "label_balance": {
            "shakeout": int((labeled["label"] == 1).sum()),
            "distribution": int((labeled["label"] == 0).sum()),
        },
    }
    metrics_path = out_dir / f"dip_classifier_metrics_{stamp}.json"
    metrics_latest = out_dir / "dip_classifier_latest_metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)
    with open(metrics_latest, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)

    log(f"[trainer] mean blend AUC = {metrics['auc_blend_mean']:.4f}")
    log(f"[trainer] wrote {cb_path.name} + {metrics_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
