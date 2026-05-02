"""tools/run_classifier_retrain.py — monthly classifier retrain CLI.

Triggered by GitHub Actions on the 1st of every month. Loads the persisted
labeled feature panel (feature_store/labeled_panel_*.parquet) — or the
scored_panel + multibagger_episodes — re-trains the multibagger entry
classifier with the latest fold, and writes the updated CatBoost model.

Outputs:
  models/p_mb_v_<YYYY-MM>.cbm                 (datestamped)
  models/classifier_latest.cbm                (overwrite each month)
  models/classifier_metrics_<YYYY-MM>.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, KR_ENGINE_REUSE_VERSION  # noqa: E402
from kr_helpers import log  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Monthly classifier retrain")
    p.add_argument("--panel", default=None,
                   help="Override path to labeled feature panel (parquet).")
    p.add_argument("--episodes", default=None,
                   help="Override path to multibagger episodes parquet.")
    p.add_argument("--n-folds", type=int, default=5)
    p.add_argument("--purged", action="store_true",
                   help="Use walk_forward_splits_purged (Phase C3).")
    p.add_argument("--embargo-months", type=int, default=9)
    return p.parse_args()


def _find_latest(glob: str, dir_path: Path) -> Path | None:
    if not dir_path.exists():
        return None
    paths = sorted(dir_path.glob(glob), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    return paths[0] if paths else None


def main() -> int:
    args = parse_args()

    feature_store = DATA_ROOT / "feature_store"
    panel_path = (Path(args.panel) if args.panel
                   else _find_latest("scored_panel_*.parquet", feature_store))
    eps_path = (Path(args.episodes) if args.episodes
                  else _find_latest("multibagger_episodes_*.parquet",
                                     feature_store))
    if panel_path is None or not panel_path.exists():
        log(f"[retrain] no scored_panel found in {feature_store}", level="ERROR")
        return 1
    if eps_path is None or not eps_path.exists():
        log(f"[retrain] no multibagger_episodes found in {feature_store}",
            level="ERROR")
        return 1

    log(f"[retrain] panel = {panel_path.name}")
    log(f"[retrain] episodes = {eps_path.name}")
    panel = pd.read_parquet(panel_path)
    episodes = pd.read_parquet(eps_path)

    # Label
    from kr_multibagger_classifier import (
        label_pre_surge,
        select_feature_columns,
        train_entry_classifier,
    )
    labeled = label_pre_surge(panel, episodes,
                                pre_surge_months=6, post_surge_months=3)
    feat_cols = select_feature_columns(labeled)
    if not feat_cols:
        log("[retrain] no feature columns selected", level="ERROR")
        return 1
    log(f"[retrain] {len(feat_cols)} features, training {args.n_folds}-fold CV")

    result = train_entry_classifier(
        labeled, feature_cols=feat_cols, n_folds=args.n_folds,
    )
    if "error" in result:
        log(f"[retrain] training failed: {result['error']}", level="ERROR")
        return 1

    # Persist
    models_dir = DATA_ROOT / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m")
    model_path = models_dir / f"p_mb_v_{stamp}.cbm"
    latest_path = models_dir / "classifier_latest.cbm"

    model = result.get("model")
    if model is None:
        log("[retrain] no model returned by trainer", level="ERROR")
        return 1
    try:
        model.save_model(str(model_path))
        model.save_model(str(latest_path))
    except Exception as ex:
        log(f"[retrain] save failed: {ex}", level="ERROR")
        return 1

    metrics = {
        "stamp": stamp,
        "engine_version": KR_ENGINE_REUSE_VERSION,
        "n_features": len(feat_cols),
        "n_panel_rows": int(len(labeled)),
        "n_positive": int(labeled.get("is_pre_surge", pd.Series()).sum()),
        "fold_aucs": result.get("fold_aucs"),
        "mean_auc": result.get("mean_auc"),
    }
    metrics_path = models_dir / f"classifier_metrics_{stamp}.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)

    log(f"[retrain] wrote {model_path.name} + {latest_path.name}")
    log(f"[retrain] mean AUC = {metrics.get('mean_auc')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
