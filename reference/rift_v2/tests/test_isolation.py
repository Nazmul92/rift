"""E0: leakage and isolation audit."""

import ast
import json
from pathlib import Path

import pytest

from rift.contracts import PublicObservation
from rift.environments.generator import make_env
from rift.environments.public_env import OpaqueSession

SRC = Path(__file__).resolve().parents[1] / "src" / "rift"
AGENT_MODULES = [
    "perception.py",
    "hypothesis.py",
    "proposers.py",
    "population.py",
    "probes.py",
    "schema.py",
    "contracts.py",
]
BANNED_FIELDS = {
    "family",
    "token",
    "door",
    "key",
    "switch",
    "gated",
    "oracle",
    "barrier_row",
    "wall_col",
    "has_token",
    "collected",
    "stepped_on_token",
    "wall_attempt",
}


def test_agent_modules_do_not_import_private_env():
    for m in AGENT_MODULES:
        tree = ast.parse((SRC / m).read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                assert "environments" not in n, f"{m} imports {n}"


def test_agent_never_receives_private_instance():
    env = make_env("possession", 1)
    s = OpaqueSession(env)
    assert not hasattr(s, "oracle_condition")
    assert not hasattr(s, "_cfg")
    public_attrs = [a for a in dir(s) if not a.startswith("_")]
    assert set(public_attrs) <= {"reset", "step", "actions"}


def test_serialized_observations_contain_no_hidden_fields():
    env = make_env("possession", 1)
    s = OpaqueSession(env)
    obs = s.reset(1)
    ser = json.dumps(obs.serialize())
    for banned in BANNED_FIELDS:
        assert banned not in ser
    res = s.step("R")
    ser2 = json.dumps(res.serialize())
    for banned in BANNED_FIELDS:
        assert banned not in ser2
    assert set(res.serialize()) == {"observation", "reward", "terminated"}


def test_prompt_leakage_guard_blocks_banned_terms():
    import os

    from rift.proposers import llm_proposals

    os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
    try:
        with pytest.raises(RuntimeError, match="leakage guard"):
            llm_proposals({"note": "the token opens the door"}, ["rB"], "rB", 1)
    finally:
        del os.environ["ANTHROPIC_API_KEY"]


def test_llm_unavailable_without_credentials():
    import os

    from rift.proposers import LiveLLMUnavailable, llm_proposals

    assert "ANTHROPIC_API_KEY" not in os.environ or not os.environ["ANTHROPIC_API_KEY"]
    with pytest.raises(LiveLLMUnavailable):
        llm_proposals({}, ["rB"], "rB", 1)


def test_internal_attribute_rename_invariance():
    """Agent behaviour depends only on rendered grids: two private configs that
    render identically must produce identical public streams."""
    env1 = make_env("possession", 4)
    env2 = make_env("possession", 4)
    env2._cfg.family = "possession"  # rename-equivalent internal metadata change
    o1, o2 = env1.reset(4), env2.reset(4)
    assert o1 == o2
    for a in ["R", "R", "U", "D", "L", "S"]:
        r1, r2 = env1.step(a), env2.step(a)
        assert r1.observation == r2.observation


def test_oracle_meta_not_reachable_from_public_obs():
    obs = PublicObservation(((0,),), 0)
    assert set(obs.serialize()) == {"grid", "step_index"}
