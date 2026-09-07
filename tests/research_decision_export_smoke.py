"""The KR export must not execute a different shared engine under a trusted pin."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kr_research_export", ROOT / "tools/export_research_decision_market.py")
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class ExportPinTests(unittest.TestCase):
    def test_changed_source_changes_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "tools/research_decision_v1"; package.mkdir(parents=True)
            p = package / "data.py"; p.write_text("VERSION = 1\n")
            old = module.package_hash(directory)
            self.assertEqual(old, module.package_hash(directory))
            p.write_text("VERSION = 2\n")
            self.assertNotEqual(old, module.package_hash(directory))

    def test_missing_and_symlink_engine_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError): module.package_hash(directory)
            package = Path(directory) / "tools/research_decision_v1"; package.mkdir(parents=True)
            target = Path(directory) / "outside.py"; target.write_text("VERSION = 1\n")
            (package / "data.py").symlink_to(target)
            with self.assertRaises(ValueError): module.package_hash(directory)


if __name__ == "__main__": unittest.main()
