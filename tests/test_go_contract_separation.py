"""Go compatibility snapshot must remain independent of extended API docs."""

from pathlib import Path
import yaml

from cow.go_wire import SPEC_PATH, go_routes

METHODS = {"get", "post", "put", "patch", "delete"}

def operations(document):
    return {
        (path, method): operation
        for path, item in document["paths"].items()
        for method, operation in item.items()
        if method in METHODS
    }

def test_frozen_go_operations_remain_unchanged():
    frozen = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    expanded_path = (
        Path(__file__).resolve().parents[1]
        / "docs/contracts/auth.openapi.yaml"
    )
    expanded = yaml.safe_load(
        expanded_path.read_text(encoding="utf-8")
    )

    original = operations(frozen)
    current = operations(expanded)

    assert len(original) == 82
    assert len(go_routes()) == 82
    assert len(current) > len(original)

    for key, operation in original.items():
        assert current.get(key) == operation, (
            f"Frozen Go operation drift: {key}"
        )
