"""REPRESENTATION EXPERIMENT — strict symmetric U/S response schemas.

PREPARATION ONLY. No provider call is made from this module.

The exploratory probe asked U for bare text and S for JSON. That is not a fair
comparison of representations: it also compares "prose allowed" against
"structure required", and any advantage S showed could have come from the
structure rather than from the edit format. Here both conditions return a strict
JSON object, validated by the same code, with the same single schema-repair
opportunity.

    U  {"diff": "..."}                          one unified diff, verbatim
    S  {"edits": [{path, search, replace}, …]}   exact edits, compiled to a diff

Schema repair re-asks for the *shape* of the answer. It never re-asks the
question, and it is granted to both conditions or neither.
"""

from __future__ import annotations

import hashlib
import json

U_SCHEMA = {
    "name": "unified_diff_response",
    "version": 1,
    "type": "object",
    "required": ["diff"],
    "additionalProperties": False,
    "properties": {"diff": {"type": "string", "minLength": 1, "maxBytes": 100_000}},
}

S_SCHEMA = {
    "name": "exact_edit_response",
    "version": 1,
    "type": "object",
    "required": ["edits"],
    "additionalProperties": False,
    "properties": {
        "edits": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "required": ["path", "search", "replace"],
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "search": {"type": "string", "minLength": 1, "maxBytes": 20_000},
                    "replace": {"type": "string"},
                },
            },
        }
    },
}

SCHEMA_VALID = "SCHEMA_VALID"
SCHEMA_INVALID = "SCHEMA_INVALID"

# One repair for U, one for S. Symmetric by construction rather than by promise.
MAX_REQUESTS_PER_SAMPLE = 2


def schema_hash(schema: dict) -> str:
    return hashlib.sha256((json.dumps(schema, indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()


def _bytes_ok(value: str, limit: int | None) -> bool:
    return limit is None or len(value.encode("utf-8")) <= limit


def validate(condition: str, payload: object) -> tuple[bool, str]:
    """Validate a decoded response against the frozen schema for its condition."""
    if condition == "U":
        return _validate_u(payload)
    if condition == "S":
        return _validate_s(payload)
    return False, f"unknown condition {condition!r}"


def _validate_u(payload: object) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "response is not a JSON object"
    extra = set(payload) - {"diff"}
    if extra:
        return False, f"unsupported field(s): {sorted(extra)}"
    diff = payload.get("diff")
    if not isinstance(diff, str):
        return False, "'diff' is missing or not a string"
    if not diff.strip():
        return False, "'diff' is empty"
    if not _bytes_ok(diff, U_SCHEMA["properties"]["diff"]["maxBytes"]):
        return False, "'diff' exceeds the byte limit"
    return True, ""


def _validate_s(payload: object) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "response is not a JSON object"
    extra = set(payload) - {"edits"}
    if extra:
        return False, f"unsupported field(s): {sorted(extra)}"
    edits = payload.get("edits")
    spec = S_SCHEMA["properties"]["edits"]
    if not isinstance(edits, list):
        return False, "'edits' is missing or not a list"
    if not (spec["minItems"] <= len(edits) <= spec["maxItems"]):
        return False, f"'edits' must hold between {spec['minItems']} and {spec['maxItems']} items"
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return False, f"edits[{i}] is not an object"
        unknown = set(edit) - {"path", "search", "replace"}
        if unknown:
            return False, f"edits[{i}] has unsupported field(s): {sorted(unknown)}"
        for key in ("path", "search", "replace"):
            if not isinstance(edit.get(key), str):
                return False, f"edits[{i}].{key} is missing or not a string"
        if not edit["path"].strip():
            return False, f"edits[{i}].path is empty"
        if not edit["search"]:
            return False, f"edits[{i}].search is empty"
        if not _bytes_ok(edit["search"], 20_000):
            return False, f"edits[{i}].search exceeds the byte limit"
    return True, ""


def extract_json(text: str) -> object | None:
    """Decode the JSON object from a response body. No content repair."""
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None


def schema_repair_instruction(condition: str, reason: str) -> str:
    """The one governed repair. Identical wording for both conditions."""
    shape = (
        '{"diff": "<unified diff>"}'
        if condition == "U"
        else '{"edits": [{"path": ..., "search": ..., "replace": ...}]}'
    )
    return (
        "Your previous response did not satisfy the required response schema "
        f"({reason}). Return only a JSON object of exactly this shape, with no prose "
        f"and no code fences: {shape}"
    )
