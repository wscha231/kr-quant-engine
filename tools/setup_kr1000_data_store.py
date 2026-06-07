"""Create or verify the KR1000 data-store directory layout.

The repository keeps code in GitHub and large/private data in Google Drive.
This tool makes that contract explicit and reproducible for local runs and
GitHub Actions runners.

Run:
    py -3 tools/setup_kr1000_data_store.py
    py -3 tools/setup_kr1000_data_store.py --root G:/내 드라이브/kr_quant_engine
    py -3 tools/setup_kr1000_data_store.py --check-only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT  # noqa: E402


REQUIRED_DATA_DIRS = (
    "cache_pykrx",
    "cache_dart",
    "cache_macro",
    "cache_misc",
    "data_raw",
    "data_pit",
    "feature_store",
    "models",
    "outputs",
    "outputs_advisor",
    "backtest_results",
    "state",
)

PRIVATE_STATE_FILES = (
    "state/current_holdings.csv",
    "state/manual_overrides.csv",
)

SYNC_BACK_DIRS = (
    "cache_pykrx",
    "cache_dart",
    "cache_macro",
    "cache_misc",
    "data_pit",
    "feature_store",
    "models",
    "outputs",
    "state",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Setup KR1000 Google Drive data store")
    p.add_argument("--root", default=None,
                   help="Data root. Default=kr_config.DATA_ROOT.")
    p.add_argument("--check-only", action="store_true",
                   help="Do not create missing directories; exit non-zero if required paths are missing.")
    p.add_argument("--no-manifest", action="store_true",
                   help="Do not write outputs/data_store_manifest.json.")
    return p.parse_args()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return value


def _copy_example_holdings(root: Path, create: bool) -> dict[str, Any]:
    src = PROJECT_ROOT / "state" / "current_holdings.example.csv"
    dst = root / "state" / "current_holdings.example.csv"
    payload = {
        "source": src,
        "target": dst,
        "source_exists": src.exists(),
        "target_exists_before": dst.exists(),
        "copied": False,
    }
    if create and src.exists() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        payload["copied"] = True
    payload["target_exists_after"] = dst.exists()
    return payload


def build_data_store_manifest(root: Path, create: bool = True) -> dict[str, Any]:
    root = Path(root).resolve()
    before = {name: (root / name).exists() for name in REQUIRED_DATA_DIRS}
    created = []
    if create:
        for name in REQUIRED_DATA_DIRS:
            path = root / name
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                created.append(name)
    after = {name: (root / name).exists() for name in REQUIRED_DATA_DIRS}
    missing = [name for name, exists in after.items() if not exists]
    private_files = {
        name: {
            "path": root / name,
            "exists": (root / name).exists(),
            "gitignored": True,
        }
        for name in PRIVATE_STATE_FILES
    }
    return {
        "schema_version": "kr1000-data-store-v1",
        "project_root": PROJECT_ROOT,
        "data_root": root,
        "create_mode": bool(create),
        "required_dirs": list(REQUIRED_DATA_DIRS),
        "directory_status_before": before,
        "directory_status_after": after,
        "created_dirs": created,
        "missing_dirs": missing,
        "example_holdings": _copy_example_holdings(root, create),
        "private_state_files": private_files,
        "github": {
            "required_secret": "RCLONE_CONFIG_GDRIVE",
            "remote_root": "gdrive:kr_quant_engine",
            "sync_back_dirs": list(SYNC_BACK_DIRS),
        },
    }


def write_manifest(manifest: dict[str, Any]) -> Path:
    root = Path(manifest["data_root"])
    out_dir = root / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "data_store_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=_jsonable), encoding="utf-8")
    return path


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve() if args.root else DATA_ROOT
    manifest = build_data_store_manifest(root, create=not args.check_only)
    manifest_path = None if args.no_manifest else write_manifest(manifest)
    print("KR1000 data store setup")
    print(f"  root:     {root}")
    print(f"  created:  {manifest['created_dirs']}")
    print(f"  missing:  {manifest['missing_dirs']}")
    if manifest_path:
        print(f"  manifest: {manifest_path}")
    return 1 if manifest["missing_dirs"] else 0


if __name__ == "__main__":
    sys.exit(main())
