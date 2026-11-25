"""A small, strict validator for the JSON Schemas in schemas/ (the subset we use).

Supports: type (object, array, string, integer, boolean), minimum, required, properties,
additionalProperties: false, items, uniqueItems, minItems, enum. Returns a list of error strings; empty means valid.
No third-party dependency.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def load(name: str) -> dict:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def validate(value, schema: dict, path: str = "$") -> list[str]:
    errors: list[str] = []
    if "enum" in schema:
        if value not in schema["enum"]:
            errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
        return errors
    t = schema.get("type")
    if t == "integer":                              # bool is an int in Python; JSON true is not an integer
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"{path}: expected integer, got {type(value).__name__}"]
        if "minimum" in schema and value < schema["minimum"]:
            return [f"{path}: {value} is below the minimum {schema['minimum']}"]
        return []
    if t and not isinstance(value, _TYPES[t]):
        return [f"{path}: expected {t}, got {type(value).__name__}"]
    if t == "object":
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing '{req}'")
        if schema.get("additionalProperties") is False:
            errors += [f"{path}: unexpected '{k}'" for k in value if k not in props]
        for k, sub in props.items():
            if k in value:
                errors += validate(value[k], sub, f"{path}.{k}")
    elif t == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errors.append(f"{path}: items must be unique")
        for i, item in enumerate(value if "items" in schema else []):
            errors += validate(item, schema["items"], f"{path}[{i}]")
    return errors
