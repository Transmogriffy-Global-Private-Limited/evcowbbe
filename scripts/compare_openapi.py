#!/usr/bin/env python3
"""Conservative diff gate: old FE-facing operations cannot disappear or mutate silently.

Usage: python scripts/compare_openapi.py old-go.yaml new-python.json
This is NOT a substitute for behavioral integration testing.
"""
import argparse
import json
from pathlib import Path
import yaml

METHODS={"get","post","put","patch","delete","head","options"}


def load(path):
    with Path(path).open(encoding="utf-8") as f:
        if str(path).endswith(".json"):
            return json.load(f)
        return yaml.safe_load(f)


def operations(doc):
    return {(path,method):details for path,item in doc.get("paths",{}).items()
            for method,details in item.items() if method in METHODS}


def normalize_doc(doc):
    # Compare only actual declarative FE contract semantics, not descriptions.
    results={}
    for key,operation in operations(doc).items():
        results[key]={name:operation.get(name) for name in
             ("requestBody","responses","parameters","security","deprecated")}
    return results


def diff(old,new):
    original=normalize_doc(old)
    candidate=normalize_doc(new)
    missing=sorted(set(original)-set(candidate))
    changed=sorted(k for k in set(original)&set(candidate) if original[k]!=candidate[k])
    new_ops=sorted(set(candidate)-set(original))
    return missing,changed,new_ops


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("old");p.add_argument("new")
    args=p.parse_args()
    missing,changed,added=diff(load(args.old),load(args.new))
    for title,items in (("MISSING",missing),("CONTRACT_CHANGED",changed),("ADDED",added)):
        print(f"{title}: {len(items)}")
        for path,method in items:
            print(f"  {method.upper()} {path}")
    if missing or changed:
        raise SystemExit("FE_COMPATIBILITY_GATE_FAILED")
    print("Source-level operation comparison passed. Behavioral parity still required.")

if __name__=="__main__":main()
