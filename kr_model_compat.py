"""Classifier artifact identity and engine-version binding.

Fail-closed compatibility checks for persisted multibagger classifiers.
A model file is usable only when its exact bytes, metadata, feature columns,
and engine reuse version agree.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from kr_config import KR_ENGINE_REUSE_VERSION

METADATA_SCHEMA = "classifier-metadata-v2"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ClassifierBindingError(ValueError):
    """Raised when a persisted classifier is not valid for this engine."""


def classifier_metrics_path(model_path: Path) -> Path:
    model_path = Path(model_path)
    name = model_path.name
    if name == "classifier_latest.cbm":
        return model_path.parent / "classifier_latest_metrics.json"
    match = re.fullmatch(r"p_mb_v_(.+)\.cbm", name)
    if match:
        return model_path.parent / f"classifier_metrics_{match.group(1)}.json"
    return model_path.parent / f"{model_path.stem}_metrics.json"


def sha256_file(path: Path) -> str:
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ClassifierBindingError(code)


def load_classifier_binding(
    model_path: Path,
    *,
    required_engine_version: str = KR_ENGINE_REUSE_VERSION,
) -> dict[str, Any]:
    model_path = Path(model_path)
    _require(model_path.is_file(), "classifier_model_missing")
    metrics_path = classifier_metrics_path(model_path)
    _require(metrics_path.is_file(), "classifier_metadata_missing")
    try:
        meta = json.loads(metrics_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ClassifierBindingError("classifier_metadata_invalid_json") from exc
    _require(isinstance(meta, dict), "classifier_metadata_object")
    _require(meta.get("metadata_schema") == METADATA_SCHEMA, "classifier_metadata_schema")
    _require(
        str(meta.get("engine_version") or "") == str(required_engine_version),
        "classifier_engine_version_mismatch",
    )
    feature_cols = meta.get("feature_cols")
    _require(
        isinstance(feature_cols, list)
        and bool(feature_cols)
        and all(isinstance(value, str) and bool(value.strip()) for value in feature_cols),
        "classifier_feature_cols_invalid",
    )
    _require(len(set(feature_cols)) == len(feature_cols), "classifier_feature_cols_duplicate")
    hashes = meta.get("model_sha256s")
    _require(isinstance(hashes, dict), "classifier_model_hash_registry_missing")
    expected = str(hashes.get(model_path.name) or "").lower()
    _require(bool(_HEX64.fullmatch(expected)), "classifier_model_hash_missing")
    actual = sha256_file(model_path)
    _require(actual == expected, "classifier_model_hash_mismatch")
    return {
        "metadata_schema": METADATA_SCHEMA,
        "engine_version": required_engine_version,
        "model_path": str(model_path),
        "metrics_path": str(metrics_path),
        "model_sha256": actual,
        "feature_cols": list(feature_cols),
    }


__all__ = [
    "ClassifierBindingError",
    "METADATA_SCHEMA",
    "classifier_metrics_path",
    "load_classifier_binding",
    "sha256_file",
]
