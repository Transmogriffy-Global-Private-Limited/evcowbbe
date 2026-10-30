#!/usr/bin/env python3
"""Conservative diff gate: old FE-facing operations cannot disappear or mutate silently.

Usage: python scripts/compare_openapi.py old-go.yaml new-python.json
This is NOT a substitute for behavioral integration testing.
"""

import argparse
import json
from pathlib import Path

import yaml

METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def load(path):
    with Path(path).open(encoding="utf-8") as f:
        if str(path).endswith(".json"):
            return json.load(f)
        return yaml.safe_load(f)


def operations(doc):
    return {
        (path, method): details
        for path, item in doc.get("paths", {}).items()
        for method, details in item.items()
        if method in METHODS
    }


def normalize_doc(doc):
    # Compare only actual declarative FE contract semantics, not descriptions.
    results = {}
    for key, operation in operations(doc).items():
        results[key] = {
            name: operation.get(name)
            for name in ("requestBody", "responses", "parameters", "security", "deprecated")
        }
    return results


def diff(old, new):
    original = normalize_doc(old)
    candidate = normalize_doc(new)
    missing = sorted(set(original) - set(candidate))
    changed = sorted(k for k in set(original) & set(candidate) if original[k] != candidate[k])
    new_ops = sorted(set(candidate) - set(original))
    return missing, changed, new_ops


def _resolve(document, value):
    while isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        if not ref.startswith("#/"):
            return value
        value = document
        for part in ref[2:].split("/"):
            value = value[part]
    return value or {}


def _shape(document, value):
    if isinstance(value, list):
        return [_shape(document, item) for item in value]
    value = _resolve(document, value)
    if not isinstance(value, dict):
        return value
    if "name" in value and "in" in value and "schema" in value:
        return {
            "in": value["in"],
            "name": value["name"],
            "required": bool(value.get("required", False)),
            "schema": _shape(document, value["schema"]),
        }
    if not {"type", "properties", "items", "required", "enum", "format"} & set(value):
        return {name: _shape(document, item) for name, item in sorted(value.items())}
    return {
        "type": value.get("type"),
        "required": sorted(value.get("required", []))
        if isinstance(value.get("required", []), list)
        else value.get("required"),
        "properties": {
            name: _shape(document, field)
            for name, field in sorted(value.get("properties", {}).items())
        },
        "items": _shape(document, value["items"]) if "items" in value else None,
        "enum": value.get("enum"),
        "format": value.get("format"),
    }


def _security_shape(document, requirements):
    """Compare the effective authentication mechanism, not local scheme labels."""
    schemes = document.get("components", {}).get("securitySchemes", {})
    result = []
    for requirement in requirements or []:
        normalized = []
        for name, scopes in sorted(requirement.items()):
            scheme = schemes.get(name, {})
            normalized.append(
                {
                    "type": scheme.get("type"),
                    "scheme": scheme.get("scheme"),
                    "bearerFormat": scheme.get("bearerFormat"),
                    "scopes": sorted(scopes),
                }
            )
        result.append(normalized)
    return result


def _response_summary(document, responses):
    resolved = _resolve(document, responses)
    return {
        str(status): {
            "headers": sorted((_resolve(document, details).get("headers") or {}).keys()),
            "body": _shape(document, (_resolve(document, details).get("content") or {})),
        }
        for status, details in sorted(resolved.items())
    }


def scoped_report(old, new, paths):
    """Field-level report for selected operations; no existence-only parity claim.

    Authentication is normalized to OpenAPI scheme semantics. Generated-document
    gaps, including omitted headers and error responses, remain visible as
    representation differences and never imply behavioral parity.
    """
    old_ops = operations(old)
    new_ops = operations(new)
    selected = {key for key in old_ops if key[0] in paths}
    report = {"missing": [], "behavioral_differences": [], "representation_differences": []}
    for key in sorted(selected):
        if key not in new_ops:
            report["missing"].append(f"{key[1].upper()} {key[0]}")
            continue
        before, after = old_ops[key], new_ops[key]
        before_security = _security_shape(old, before.get("security"))
        after_security = _security_shape(new, after.get("security"))
        if before_security != after_security:
            report["behavioral_differences"].append(
                {
                    "operation": key,
                    "field": "security",
                    "old": before_security,
                    "new": after_security,
                }
            )
        for field in ("parameters", "requestBody"):
            if _shape(old, before.get(field, {})) != _shape(new, after.get(field, {})):
                report["representation_differences"].append(
                    {
                        "operation": key,
                        "field": field,
                        "old": _shape(old, before.get(field, {})),
                        "new": _shape(new, after.get(field, {})),
                    }
                )
        before_responses = _response_summary(old, before.get("responses", {}))
        after_responses = _response_summary(new, after.get("responses", {}))
        if before_responses != after_responses:
            report["representation_differences"].append(
                {
                    "operation": key,
                    "field": "responses",
                    "old": before_responses,
                    "new": after_responses,
                }
            )
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument(
        "--scope",
        action="append",
        default=[],
        help="path to include in field-level report; repeatable",
    )
    args = p.parse_args()
    missing, changed, added = diff(load(args.old), load(args.new))
    for title, items in (("MISSING", missing), ("CONTRACT_CHANGED", changed), ("ADDED", added)):
        print(f"{title}: {len(items)}")
        for path, method in items:
            print(f"  {method.upper()} {path}")
    if args.scope:
        print("SCOPED_FIELD_REPORT:")
        print(
            json.dumps(
                scoped_report(load(args.old), load(args.new), set(args.scope)),
                indent=2,
                default=list,
            )
        )
    if missing or changed:
        raise SystemExit("FE_COMPATIBILITY_GATE_FAILED")
    print("Source-level operation comparison passed. Behavioral parity still required.")


if __name__ == "__main__":
    main()
