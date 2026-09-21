from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from kr_model_compat import (
    ClassifierBindingError,
    METADATA_SCHEMA,
    classifier_metrics_path,
    load_classifier_binding,
    sha256_file,
)
from kr_config import KR_ENGINE_REUSE_VERSION


def write_meta(model: Path, *, engine: str = KR_ENGINE_REUSE_VERSION, digest: str | None = None) -> Path:
    metrics = classifier_metrics_path(model)
    payload = {
        "metadata_schema": METADATA_SCHEMA,
        "engine_version": engine,
        "feature_cols": ["ret_12_1m", "quality_score"],
        "model_sha256s": {model.name: digest or sha256_file(model)},
    }
    metrics.write_text(json.dumps(payload), encoding="utf-8")
    return metrics


class ClassifierBindingTests(unittest.TestCase):
    def test_latest_model_exact_binding_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "classifier_latest.cbm"
            model.write_bytes(b"model-v2")
            write_meta(model)
            result = load_classifier_binding(model)
            self.assertEqual(result["engine_version"], KR_ENGINE_REUSE_VERSION)
            self.assertEqual(result["model_sha256"], sha256_file(model))
            self.assertEqual(result["feature_cols"], ["ret_12_1m", "quality_score"])

    def test_dated_model_uses_exact_month_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "p_mb_v_2026-09.cbm"
            model.write_bytes(b"dated")
            metrics = write_meta(model)
            self.assertEqual(metrics.name, "classifier_metrics_2026-09.json")
            load_classifier_binding(model)

    def test_engine_version_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "classifier_latest.cbm"
            model.write_bytes(b"old")
            write_meta(model, engine="old-engine")
            with self.assertRaisesRegex(ClassifierBindingError, "engine_version_mismatch"):
                load_classifier_binding(model)

    def test_missing_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "classifier_latest.cbm"
            model.write_bytes(b"x")
            with self.assertRaisesRegex(ClassifierBindingError, "metadata_missing"):
                load_classifier_binding(model)

    def test_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "classifier_latest.cbm"
            model.write_bytes(b"x")
            write_meta(model, digest="0" * 64)
            with self.assertRaisesRegex(ClassifierBindingError, "hash_mismatch"):
                load_classifier_binding(model)

    def test_missing_hash_registry_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "classifier_latest.cbm"
            model.write_bytes(b"x")
            metrics = classifier_metrics_path(model)
            metrics.write_text(json.dumps({
                "metadata_schema": METADATA_SCHEMA,
                "engine_version": KR_ENGINE_REUSE_VERSION,
                "feature_cols": ["x"],
                "model_sha256s": {},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ClassifierBindingError, "hash_missing"):
                load_classifier_binding(model)

    def test_duplicate_feature_columns_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "classifier_latest.cbm"
            model.write_bytes(b"x")
            metrics = classifier_metrics_path(model)
            metrics.write_text(json.dumps({
                "metadata_schema": METADATA_SCHEMA,
                "engine_version": KR_ENGINE_REUSE_VERSION,
                "feature_cols": ["x", "x"],
                "model_sha256s": {model.name: sha256_file(model)},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ClassifierBindingError, "feature_cols_duplicate"):
                load_classifier_binding(model)

    def test_unknown_custom_model_requires_own_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "custom.cbm"
            model.write_bytes(b"x")
            self.assertEqual(classifier_metrics_path(model).name, "custom_metrics.json")
            write_meta(model)
            load_classifier_binding(model)


if __name__ == "__main__":
    unittest.main()
