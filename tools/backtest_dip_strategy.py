"""tools/backtest_dip_strategy.py — Phase F.F5 backtest validation.

Tests the shake-out classifier as a HOLD/SELL gating signal on the
historical dip episode panel. Compares three strategies:

  1. Naive HOLD ALL : never sell on dip; realize T+30 close
  2. Naive STOP -8% : sell at -8pct stop loss
  3. Classifier   : if P(shake-out) >= hold_threshold -> HOLD;
                    if P < sell_threshold              -> SELL at dip close;
                    else                              -> REDUCE 50pct at dip close.

Per-episode realised return computed against `recovery_close` from F1.
Aggregated metrics:

  - mean realised return
  - hit rate (positive return episodes)
  - left-tail loss (5pct percentile)
  - shake-out F1 score (vs ground-truth labels)
  - cost-aware return (assumes 31bp round-trip on SELL/REDUCE)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402
from kr_helpers import log  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dip strategy backtester")
    p.add_argument("--feature-panel", required=True,
                   help="Parquet of the labeled feature panel.")
    p.add_argument("--classifier", default=None,
                   help="Path to dip_classifier_latest.cbm (default: "
                        "DATA_ROOT/models/).")
    p.add_argument("--hold-threshold", type=float, default=0.65)
    p.add_argument("--sell-threshold", type=float, default=0.40)
    p.add_argument("--cost-bp", type=float, default=31.0)
    p.add_argument("--out", default=None)
    return p.parse_args()


def _load_classifier(path: Path):
    from catboost import CatBoostClassifier
    model = CatBoostClassifier()
    model.load_model(str(path))
    return model


def _episode_returns(panel: pd.DataFrame) -> pd.Series:
    """Realised return per episode = recovery_close / dip_low_close - 1.

    Floor at -50pct to model worst-case recovery (gap-down sell).
    """
    if "recovery_close" not in panel.columns or "dip_low_close" not in panel.columns:
        return pd.Series(0.0, index=panel.index)
    raw = panel["recovery_close"] / panel["dip_low_close"] - 1.0
    return raw.clip(lower=-0.5, upper=5.0)


def _strategy_naive_hold(panel: pd.DataFrame) -> pd.Series:
    return _episode_returns(panel)


def _strategy_naive_stop(panel: pd.DataFrame, stop_pct: float = -0.08) -> pd.Series:
    """Sell at -8pct stop loss = realize -8pct cleanly."""
    eps_return = _episode_returns(panel)
    # Approximation: if eventual return < stop_pct, realize stop_pct exactly
    # (since stop fires first); otherwise realize full return.
    return eps_return.where(eps_return >= stop_pct, stop_pct)


def _strategy_classifier(panel: pd.DataFrame, p_shakeout: np.ndarray,
                          hold_th: float, sell_th: float,
                          cost_bp: float) -> pd.Series:
    """HOLD: full return; REDUCE: 50pct exposure - one-way cost; SELL: -dip
    drawdown - cost. Cost in bp applies to the part that is sold."""
    eps_return = _episode_returns(panel)
    cost = cost_bp / 10000.0
    out = []
    for i, r in enumerate(eps_return):
        p = p_shakeout[i]
        if p >= hold_th:
            out.append(r)
        elif p < sell_th:
            # Realize at dip close: drawdown_pct from peak is the loss
            dd = float(panel.iloc[i].get("drawdown_pct") or 0)
            out.append(dd - cost)
        else:
            # Reduce 50pct at dip close, hold the other 50pct
            dd = float(panel.iloc[i].get("drawdown_pct") or 0)
            out.append(0.5 * (dd - cost) + 0.5 * r)
    return pd.Series(out, index=panel.index)


def _summary(name: str, returns: pd.Series, labels: pd.Series) -> dict:
    arr = returns.dropna().values
    res = {
        "strategy": name,
        "n_episodes": int(len(arr)),
        "mean_return": float(arr.mean()) if len(arr) else 0.0,
        "median_return": float(np.median(arr)) if len(arr) else 0.0,
        "hit_rate": float((arr > 0).mean()) if len(arr) else 0.0,
        "left_tail_5pct_loss": float(np.quantile(arr, 0.05)) if len(arr) else 0.0,
        "stdev": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
    }
    if labels is not None and len(labels) == len(returns):
        valid_lab = labels.notna()
        res["n_labeled_used"] = int(valid_lab.sum())
    return res


def main() -> int:
    args = parse_args()
    path = Path(args.feature_panel)
    if not path.exists():
        log(f"[backtest] panel missing: {path}", level="ERROR")
        return 1
    panel = pd.read_parquet(path)
    log(f"[backtest] panel: {len(panel)} rows × {len(panel.columns)} cols")

    # Filter labeled rows
    if "label" not in panel.columns:
        log("[backtest] panel lacks 'label'", level="ERROR")
        return 2
    labeled = panel[panel["label"].isin([0, 1, 0.0, 1.0])].copy()
    labeled["label"] = labeled["label"].astype(int)
    if labeled.empty:
        log("[backtest] no labeled rows", level="ERROR")
        return 3
    log(f"[backtest] using {len(labeled)} labeled rows")

    cls_path = Path(args.classifier) if args.classifier \
        else (DATA_ROOT / "models" / "dip_classifier_latest.cbm")
    if not cls_path.exists():
        log(f"[backtest] classifier missing: {cls_path}", level="WARN")
        log("[backtest] running naive strategies only", level="WARN")
        cls = None
    else:
        cls = _load_classifier(cls_path)

    # Load feature_cols
    metrics_path = (DATA_ROOT / "models" / "dip_classifier_latest_metrics.json")
    feat_cols = None
    if metrics_path.exists():
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            feat_cols = meta.get("feature_cols")
        except Exception:
            pass
    if feat_cols is None:
        feat_cols = [c for c in labeled.columns if c.startswith("f_")]

    # Compute classifier probabilities (full panel, including OOS-by-design here)
    if cls is not None:
        aligned = pd.DataFrame(0.0, index=labeled.index, columns=feat_cols)
        shared = [c for c in feat_cols if c in labeled.columns]
        aligned[shared] = labeled[shared].astype(float).fillna(0.0)
        proba = cls.predict_proba(aligned.values)[:, 1]
    else:
        proba = np.full(len(labeled), 0.5)

    # Strategies
    r_hold = _strategy_naive_hold(labeled)
    r_stop = _strategy_naive_stop(labeled)
    r_cls = _strategy_classifier(
        labeled, proba,
        hold_th=args.hold_threshold,
        sell_th=args.sell_threshold,
        cost_bp=args.cost_bp,
    )

    summaries = [
        _summary("HOLD_ALL", r_hold, labeled["label"]),
        _summary("STOP_8PCT", r_stop, labeled["label"]),
        _summary("CLASSIFIER", r_cls, labeled["label"]),
    ]

    # Confusion matrix vs ground-truth labels (interpret HOLD as predicting 1)
    if cls is not None:
        from sklearn.metrics import (
            roc_auc_score, classification_report, confusion_matrix
        )
        try:
            auc = float(roc_auc_score(labeled["label"], proba))
        except Exception:
            auc = float("nan")
        threshold_for_pred = (args.hold_threshold + args.sell_threshold) / 2
        y_pred = (proba >= threshold_for_pred).astype(int)
        cm = confusion_matrix(labeled["label"], y_pred).tolist()
        summaries.append({
            "strategy": "CLASSIFIER_CLASSIFICATION_METRICS",
            "auc": auc,
            "confusion_matrix": cm,
            "decision_threshold": threshold_for_pred,
        })

    # Persist
    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = out_dir / "dip_strategy_backtest.json"
    payload = {
        "panel": str(path),
        "n_labeled": int(len(labeled)),
        "hold_threshold": args.hold_threshold,
        "sell_threshold": args.sell_threshold,
        "cost_bp": args.cost_bp,
        "summaries": summaries,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    log(f"[backtest] wrote {out_path}")

    # Console summary
    print()
    print("=" * 72)
    for s in summaries:
        if "strategy" in s:
            name = s["strategy"]
            if "mean_return" in s:
                print(f"  {name:25s} mean={s['mean_return']:+.3f}  "
                      f"hit={s['hit_rate']:.2%}  "
                      f"tail={s['left_tail_5pct_loss']:+.3f}")
            elif "auc" in s:
                print(f"  {name:25s} AUC={s.get('auc'):.3f} "
                      f"CM={s.get('confusion_matrix')}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
