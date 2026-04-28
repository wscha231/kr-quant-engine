"""kr_helpers — pure utility functions for kr_quant_engine.

Mirrors r1000_helpers.py role. Numpy/pandas only, no business logic.

Functions:
- phase_is_enabled(phase_key, default) — env var PHASE_<KEY>_ENABLED gate
- cross_sectional_robust_z(series) — winsorized z-score per row
- percentile_rank(series) — 0..1 rank, NaN → 0.5
- safe_float(value, default) — defensive coerce
- log(*args) — timestamped log
- load_dotenv_if_present(path) — load .env into os.environ
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# .env loader (no python-dotenv dependency at import time)
# ---------------------------------------------------------------------------
def load_dotenv_if_present(path: str | Path | None = None) -> dict[str, str]:
    """Parse .env at `path` (or PROJECT_ROOT/.env) into os.environ.

    Returns the loaded dict. Silent if file absent.
    """
    if path is None:
        from kr_config import PROJECT_ROOT
        path = PROJECT_ROOT / ".env"
    p = Path(path)
    if not p.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if not k:
            continue
        os.environ.setdefault(k, v)
        loaded[k] = v
    return loaded


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(*args: Any, level: str = "INFO") -> None:
    """Timestamped log to stdout. Mirrors r1000.log()."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = " ".join(str(a) for a in args)
    print(f"[{ts}] [{level}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Phase toggle (r1000 pattern)
# ---------------------------------------------------------------------------
def phase_is_enabled(phase_key: str, default: bool = True) -> bool:
    """Check env var PHASE_{KEY}_ENABLED. Returns `default` when unset.

    Truthy: "1", "true", "yes", "on", "enabled"
    Falsy:  "0", "false", "no", "off", "disabled"
    Empty/unset: returns default.
    """
    env_name = f"PHASE_{phase_key.upper()}_ENABLED"
    raw = os.environ.get(env_name, "")
    val = str(raw).strip().lower()
    if val == "":
        return bool(default)
    if val in ("0", "false", "no", "off", "disabled"):
        return False
    if val in ("1", "true", "yes", "on", "enabled"):
        return True
    return bool(default)


# ---------------------------------------------------------------------------
# Defensive coercion
# ---------------------------------------------------------------------------
def safe_float(value: Any, default: float = float("nan")) -> float:
    """Coerce value to float, return default on any failure."""
    try:
        if value is None:
            return default
        f = float(value)
        if np.isfinite(f):
            return f
        return default
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """Coerce value to int, return default on any failure."""
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Cross-sectional normalization (r1000 robust_z pattern)
# ---------------------------------------------------------------------------
def cross_sectional_robust_z(
    series: pd.Series,
    winsor_p: tuple[float, float] = (0.01, 0.99),
    clip: float = 4.0,
) -> pd.Series:
    """Winsorized + median-based z-score.

    Steps:
      1. Coerce to numeric (errors='coerce')
      2. Winsorize at (1%, 99%) percentiles
      3. Subtract median, divide by MAD * 1.4826 (robust std)
      4. Clip at ±clip

    NaN input → NaN output (not zero — caller decides fill behavior).
    """
    s = pd.to_numeric(series, errors="coerce").astype(float)
    if s.notna().sum() < 5:
        return pd.Series(np.nan, index=series.index, dtype=float)

    lo, hi = s.quantile(winsor_p[0]), s.quantile(winsor_p[1])
    s_w = s.clip(lower=lo, upper=hi)

    med = s_w.median()
    mad = (s_w - med).abs().median()
    if mad < 1e-12:
        # Degenerate: all values identical
        return pd.Series(0.0, index=series.index, dtype=float)

    z = (s_w - med) / (mad * 1.4826)
    return z.clip(lower=-clip, upper=clip)


def percentile_rank(series: pd.Series, fill_na: float = 0.5) -> pd.Series:
    """0..1 percentile rank. NaN → fill_na (default 0.5 = neutral).

    Average rank method (ties get mean rank).
    """
    s = pd.to_numeric(series, errors="coerce")
    ranked = s.rank(pct=True, method="average")
    return ranked.fillna(fill_na)


# ---------------------------------------------------------------------------
# Sign-flip detection (r1000 _sign_flip_pos pattern, P1 turnaround signal)
# ---------------------------------------------------------------------------
def sign_flip_pos(prev: pd.Series, curr: pd.Series) -> pd.Series:
    """1.0 where prev < 0 and curr > 0, else 0.0. NaN-safe.

    Used for "loss → profit" sign-flip detection in turnaround signals.
    """
    p = pd.to_numeric(prev, errors="coerce")
    c = pd.to_numeric(curr, errors="coerce")
    flag = ((p < 0) & (c > 0)).astype(float)
    flag.loc[p.isna() | c.isna()] = 0.0
    return flag


def sign_flip_neg(prev: pd.Series, curr: pd.Series) -> pd.Series:
    """1.0 where prev > 0 and curr < 0, else 0.0. NaN-safe."""
    p = pd.to_numeric(prev, errors="coerce")
    c = pd.to_numeric(curr, errors="coerce")
    flag = ((p > 0) & (c < 0)).astype(float)
    flag.loc[p.isna() | c.isna()] = 0.0
    return flag


# ---------------------------------------------------------------------------
# Hard sanitize (numeric column hygiene)
# ---------------------------------------------------------------------------
def hard_sanitize(
    df: pd.DataFrame,
    columns: list[str],
    fill_value: float = 0.0,
) -> pd.DataFrame:
    """Replace ±inf and NaN with fill_value for listed numeric columns.

    Mirrors r1000 hard_sanitize. Used in build_feature_store + feature builders.
    """
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            continue
        s = pd.to_numeric(out[col], errors="coerce")
        s = s.replace([np.inf, -np.inf], np.nan).fillna(fill_value)
        out[col] = s.astype(float)
    return out


# ---------------------------------------------------------------------------
# Date utilities
# ---------------------------------------------------------------------------
def to_month_end(date: Any) -> pd.Timestamp:
    """Coerce input to pandas Timestamp at month-end."""
    return pd.Timestamp(date).normalize() + pd.offsets.MonthEnd(0)


def previous_business_day(date: Any) -> pd.Timestamp:
    """Previous KRX business day (using pandas business day, naive)."""
    return pd.Timestamp(date).normalize() - pd.offsets.BDay(1)


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------
def get_paths(cfg: dict) -> dict[str, Path]:
    """Resolve cache/output paths from cfg, defaulting to PROJECT_ROOT subdirs."""
    from kr_config import DEFAULT_CACHE_DIRS

    base_dir = Path(cfg.get("base_dir", "."))
    paths = {}
    for key, default_path in DEFAULT_CACHE_DIRS.items():
        # If cfg specifies override, use it; else default relative to base_dir
        override = cfg.get(f"{key}_dir")
        if override:
            paths[key] = Path(override)
        else:
            # Replace PROJECT_ROOT with base_dir
            rel = default_path.relative_to(default_path.parents[0])
            paths[key] = base_dir / rel
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths
