"""Ruling 4: the hypothesis IR is closed at every nesting level.

The property under test is *"closed unless explicitly allowed"*, not "rejects
the field names we happened to think of". So the central test does not enumerate
smuggling attempts: it walks a valid hypothesis, inserts an arbitrary unknown
key at every dict in the tree, and requires each one to be rejected. A future
field added to the IR without a matching entry in `CLOSED_FIELDS` turns these
red.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from riftagent.records import (
    BANNED_FIELDS,
    MAX_COUNTER,
    MAX_LATENTS,
    IRValidationError,
    validate_hypothesis,
)


def valid() -> dict[str, Any]:
    """A hypothesis exercising every node type the IR has, so the mutation
    walk below reaches all of them."""
    return {
        "hypothesis_id": "h0",
        "roles": ["r0", "r1", "rT"],
        "target_role": "rT",
        "latents": [
            {
                "name": "z0",
                "type": "bool",
                "init": False,
                "set_on": {"event": "applied", "role": "r0"},
                "reset_on": {"event": "absent", "role": "r1"},
            },
            {
                "name": "k2",
                "type": "counter",
                "max": 8,
                "inc_on": {"event": "run", "role": None},
            },
        ],
        "condition": {
            "op": "and",
            "args": [
                {"op": "not", "arg": {"var": "z0"}},
                {
                    "op": "or",
                    "args": [
                        {"op": "ge", "var": "k2", "value": 2},
                        {"const": True},
                    ],
                },
            ],
        },
    }


def test_the_reference_hypothesis_is_accepted():
    """Guards the mutation tests: if this were invalid they would all pass
    vacuously."""
    validate_hypothesis(valid())


# --------------------------------------------------------------------------
# closed-by-default at every level
# --------------------------------------------------------------------------


def _dict_paths(obj: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Every dict in the structure, addressed by its path from the root."""
    found: list[tuple[Any, ...]] = []
    if isinstance(obj, dict):
        found.append(path)
        for k, v in obj.items():
            found += _dict_paths(v, (*path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += _dict_paths(v, (*path, i))
    return found


def _at(obj: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        obj = obj[step]
    return obj


ALL_PATHS = _dict_paths(valid())


def test_the_walk_reaches_every_nesting_level():
    """The mutation suite is only as good as its coverage of the tree."""
    depths = {len(p) for p in ALL_PATHS}
    assert len(ALL_PATHS) >= 10, f"only {len(ALL_PATHS)} dicts found"
    assert max(depths) >= 4, f"deepest dict is at depth {max(depths)}"


@pytest.mark.parametrize("path", ALL_PATHS, ids=lambda p: "root" if not p else ".".join(map(str, p)))
@pytest.mark.parametrize("field", ["nonce_xyz", "note", "explanation", "_meta", "priority"])
def test_an_unknown_field_at_any_level_is_rejected(path: tuple[Any, ...], field: str):
    h = copy.deepcopy(valid())
    _at(h, path)[field] = "anything"
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


@pytest.mark.parametrize("path", ALL_PATHS, ids=lambda p: "root" if not p else ".".join(map(str, p)))
@pytest.mark.parametrize("field", sorted(BANNED_FIELDS))
def test_confidence_family_fields_are_rejected_at_any_level(path: tuple[Any, ...], field: str):
    """Model confidence must not be expressible as *input* anywhere, so no
    later reader can start consulting it by accident."""
    h = copy.deepcopy(valid())
    _at(h, path)[field] = 0.99
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


def test_a_non_string_key_is_rejected():
    h = valid()
    h[1] = "x"  # type: ignore[index]
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


# --------------------------------------------------------------------------
# exact types — booleans are not integers
# --------------------------------------------------------------------------


def test_true_is_not_accepted_as_a_counter_maximum():
    """`isinstance(True, int)` is True in Python. A count is a count."""
    h = valid()
    h["latents"][1]["max"] = True
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


def test_true_is_not_accepted_as_a_ge_threshold():
    h = valid()
    h["condition"]["args"][1]["args"][0]["value"] = True
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


@pytest.mark.parametrize("bad", [1, 0, "true", None, [], {}])
def test_const_must_be_an_actual_boolean(bad: Any):
    h = valid()
    h["condition"] = {"const": bad}
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


def test_counter_maximum_out_of_range_is_rejected():
    h = valid()
    h["latents"][1]["max"] = MAX_COUNTER + 1
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


# --------------------------------------------------------------------------
# reference integrity
# --------------------------------------------------------------------------


def test_a_condition_cannot_read_an_undeclared_latent():
    h = valid()
    h["condition"] = {"var": "ghost"}
    with pytest.raises(IRValidationError, match="not a declared latent"):
        validate_hypothesis(h)


def test_a_ge_cannot_read_an_undeclared_latent():
    h = valid()
    h["condition"] = {"op": "ge", "var": "ghost", "value": 1}
    with pytest.raises(IRValidationError, match="not a declared latent"):
        validate_hypothesis(h)


def test_a_nested_reference_is_checked_too():
    """Reference checking is part of the recursion, not a top-level pass."""
    h = valid()
    h["condition"]["args"][0]["arg"] = {"var": "ghost"}
    with pytest.raises(IRValidationError, match="not a declared latent"):
        validate_hypothesis(h)


def test_duplicate_role_names_are_rejected():
    h = valid()
    h["roles"] = ["r0", "r0", "rT"]
    with pytest.raises(IRValidationError, match="unique"):
        validate_hypothesis(h)


def test_duplicate_latent_names_are_rejected():
    h = valid()
    h["latents"][1] = dict(h["latents"][1], name="z0", type="counter")
    with pytest.raises(IRValidationError, match="duplicate"):
        validate_hypothesis(h)


def test_an_event_cannot_reference_an_undeclared_role():
    h = valid()
    h["latents"][0]["set_on"]["role"] = "r9"
    with pytest.raises(IRValidationError, match="not a declared role"):
        validate_hypothesis(h)


def test_target_role_must_be_declared():
    h = valid()
    h["target_role"] = "rZ"
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


# --------------------------------------------------------------------------
# vocabulary and budgets
# --------------------------------------------------------------------------


def test_an_invented_event_kind_is_rejected():
    h = valid()
    h["latents"][0]["set_on"]["event"] = "imported"
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


def test_an_invented_latent_type_is_rejected():
    h = valid()
    h["latents"][0]["type"] = "string"
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


def test_an_invented_operator_is_rejected():
    h = valid()
    h["condition"] = {"op": "xor", "args": [{"const": True}, {"const": False}]}
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


def test_bool_latent_fields_may_not_be_used_on_a_counter():
    """Per-type closed field sets, not one permissive union."""
    h = valid()
    h["latents"][1]["set_on"] = {"event": "run", "role": None}
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


def test_too_many_latents_is_rejected():
    h = valid()
    h["latents"] = [
        {"name": f"z{i}", "type": "bool", "init": False, "set_on": {"event": "run", "role": None}}
        for i in range(MAX_LATENTS + 1)
    ]
    h["condition"] = {"var": "z0"}
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


def test_over_deep_nesting_is_rejected():
    cond: dict[str, Any] = {"const": True}
    for _ in range(12):
        cond = {"op": "not", "arg": cond}
    h = valid()
    h["condition"] = cond
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


@pytest.mark.parametrize("bad", ["", None, 3, [], {}])
def test_a_hypothesis_that_is_not_an_object_is_rejected(bad: Any):
    with pytest.raises(IRValidationError):
        validate_hypothesis(bad)


def test_a_missing_condition_is_rejected():
    """Absent is not vacuously true."""
    h = valid()
    del h["condition"]
    with pytest.raises(IRValidationError):
        validate_hypothesis(h)


def test_the_kernel_generated_grammar_satisfies_the_validator():
    """The kernel's own enumeration is expressible in the IR it shares with the
    adapter. If these ever diverged, the model would be held to a contract the
    kernel does not itself meet."""
    from riftagent.kernel import code_grammar

    for h in code_grammar(["r0", "r1", "rT"]):
        validate_hypothesis(h)
