#!/usr/bin/env python3
"""KR-owned research export; shared pure engine is explicitly hash pinned."""
import argparse
import hashlib
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def source_material(engine_root):
    root = Path(engine_root).resolve()
    package = root / "tools" / "research_decision_v1"
    files = sorted(package.rglob("*.py"))
    parent_init = root / "tools" / "__init__.py"
    if parent_init.exists(): files.append(parent_init)
    if not files or any(p.is_symlink() for p in (package, *package.parents, *files)):
        raise ValueError("engine_package_missing_or_unsafe")
    if any(p.suffix in {".so", ".pyd"} for p in package.rglob("*")):
        raise ValueError("unmanifested_executable")
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in files}


def material_hash(material):
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def package_hash(engine_root):
    return material_hash(source_material(engine_root))


@contextmanager
def verified_snapshot(engine_root, expected_hash):
    # Read once. Only these hashed bytes are copied into the executable snapshot.
    material = source_material(engine_root)
    if material_hash(material) != expected_hash: raise ValueError("engine_source_hash_mismatch")
    with tempfile.TemporaryDirectory(prefix="kr-research-engine-") as directory:
        root = Path(directory)
        for relative, content in material.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        # Give an absent parent initializer an empty body to prevent namespace
        # merging with a different tools package already on the search path.
        parent = root / "tools" / "__init__.py"
        if not parent.exists(): parent.write_text("", encoding="utf-8")
        yield root


def execute_export(snapshot, input_path):
    names = [name for name in sys.modules if name == "tools" or name.startswith("tools.")]
    if names: raise ValueError("engine_namespace_already_loaded")
    sys.path.insert(0, str(snapshot))
    try:
        from tools.research_decision_v1.data import export_market
        from tools.research_decision_v1.io import immutable_json, read_json, research_root
        result = export_market(read_json(input_path), "KR")
        path = immutable_json(research_root(ROOT) / "exports" / (result["export_hash"] + ".json"), result)
        return result, path
    finally:
        sys.path.remove(str(snapshot))
        for name in list(sys.modules):
            if name == "tools" or name.startswith("tools."): del sys.modules[name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--engine-root", required=True)
    parser.add_argument("--engine-source-hash", required=True)
    args = parser.parse_args()
    with verified_snapshot(args.engine_root, args.engine_source_hash) as snapshot:
        result, path = execute_export(snapshot, args.input)
    admitted = sum(s["data_quality_pass"] for s in result["securities"])
    print(json.dumps({"path": str(path), "engine_source_hash": args.engine_source_hash,
                      "admitted": admitted, "total": len(result["securities"]), "orders_allowed": False}))
    return 0 if admitted == len(result["securities"]) and admitted else 2


if __name__ == "__main__": raise SystemExit(main())
