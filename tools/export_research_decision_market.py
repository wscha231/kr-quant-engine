#!/usr/bin/env python3
"""KR-owned research export; shared pure engine is explicitly hash pinned."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def package_hash(engine_root):
    package = Path(engine_root).resolve() / "tools" / "research_decision_v1"
    files = sorted(package.glob("*.py"))
    if not files or any(p.is_symlink() for p in (package, *package.parents, *files)):
        raise ValueError("engine_package_missing_or_unsafe")
    material = {p.name: p.read_text(encoding="utf-8") for p in files}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--engine-root", required=True)
    parser.add_argument("--engine-source-hash", required=True)
    args = parser.parse_args()
    if package_hash(args.engine_root) != args.engine_source_hash:
        raise ValueError("engine_source_hash_mismatch")
    sys.path.insert(0, str(Path(args.engine_root).resolve()))
    from tools.research_decision_v1.data import export_market
    from tools.research_decision_v1.io import immutable_json, read_json, research_root
    result = export_market(read_json(args.input), "KR")
    path = immutable_json(research_root(ROOT) / "exports" / (result["export_hash"] + ".json"), result)
    admitted = sum(s["data_quality_pass"] for s in result["securities"])
    print(json.dumps({"path": str(path), "engine_source_hash": args.engine_source_hash,
                      "admitted": admitted, "total": len(result["securities"]), "orders_allowed": False}))
    return 0 if admitted == len(result["securities"]) and admitted else 2


if __name__ == "__main__": raise SystemExit(main())
