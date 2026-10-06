"""Convert Pydantic JSON schemas into the strict subset accepted by structured-output APIs.

Strict mode (Anthropic output_config.format / OpenAI strict json_schema) requires every object to
set additionalProperties=false and list every property as required, and rejects numeric/string
constraint keywords. Optional values stay expressible because Pydantic renders ``X | None`` as
``anyOf: [X, null]``; defaults are dropped, so the model always emits every key.
"""

import copy
from typing import Any

from pydantic import BaseModel

_DROP_KEYS = {
    "default",
    "title",
    "examples",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "format",
}


def _walk(node: Any) -> Any:
    if isinstance(node, list):
        return [_walk(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in _DROP_KEYS:
            continue
        if key in ("properties", "$defs"):
            out[key] = {k: _walk(v) for k, v in value.items()}
        else:
            out[key] = _walk(value)
    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties", {})
        out["type"] = "object"
        out["additionalProperties"] = False
        out["required"] = list(props.keys())
    # A $ref may not carry sibling keywords such as description in strict mode.
    if "$ref" in out and len(out) > 1:
        out = {"$ref": out["$ref"]}
    return out


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    return _walk(copy.deepcopy(model.model_json_schema()))


def coerce_to_schema(data: Any, schema: dict[str, Any]) -> Any:
    """Bend almost-valid model output into shape for a strict schema.

    Providers that validate output after generating it (Groq) reject a whole response for one off-list
    enum value, an extra key or a missing optional one. This repairs exactly those deviations so the
    result can still be validated normally: unknown keys are dropped, missing keys get null/empty values,
    off-list enum values become the safest option ("inferred", "custom", "medium"...).
    """
    defs = schema.get("$defs", {})

    def fallback(options: list) -> Any:
        for safe in ("inferred", "custom", "medium", "scope", "text", "other"):
            if safe in options:
                return safe
        return options[0]

    def resolve(node: dict) -> dict:
        while "$ref" in node:
            node = defs[node["$ref"].split("/")[-1]]
        return node

    def empty(node: dict) -> Any:
        node = resolve(node)
        if "anyOf" in node:
            return None if any(resolve(b).get("type") == "null" for b in node["anyOf"]) else empty(node["anyOf"][0])
        if "enum" in node:
            return fallback(node["enum"])
        return {"array": [], "string": "", "boolean": False, "integer": 0, "number": 0.0, "object": {}}.get(
            node.get("type"), None
        )

    def walk(value: Any, node: dict) -> Any:
        node = resolve(node)
        if "anyOf" in node:
            branches = [resolve(b) for b in node["anyOf"]]
            if value is None and any(b.get("type") == "null" for b in branches):
                return None
            for b in branches:
                t = b.get("type")
                if (t == "object" and isinstance(value, dict)) or (t == "array" and isinstance(value, list)):
                    return walk(value, b)
            non_null = [b for b in branches if b.get("type") != "null"]
            return walk(value, non_null[0]) if non_null else value
        if "enum" in node:
            return value if value in node["enum"] else fallback(node["enum"])
        t = node.get("type")
        if t == "object":
            if not isinstance(value, dict):
                value = {}
            props = node.get("properties", {})
            return {k: walk(value[k], p) if k in value else empty(p) for k, p in props.items()}
        if t == "array":
            items = node.get("items", {})
            return [walk(v, items) for v in value] if isinstance(value, list) else []
        if t == "string":
            return value if isinstance(value, str) else ("" if value is None else str(value))
        if t in ("integer", "number"):
            try:
                return int(value) if t == "integer" else float(value)
            except (TypeError, ValueError):
                return 0
        if t == "boolean":
            return bool(value)
        return value

    return walk(data, schema)
