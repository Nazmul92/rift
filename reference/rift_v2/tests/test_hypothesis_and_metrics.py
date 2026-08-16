import pytest

from rift.hypothesis import (
    MAX_DEPTH,
    IRValidationError,
    behaviourally_equivalent,
    description_length,
    execute,
    validate,
)
from rift.metrics import bootstrap_ci, classification_report, paired_diff_ci
from rift.proposers import fake_proposals, grammar_proposals


def _h(cond, latents=None, roles=("r0", "rB")):
    return {
        "hypothesis_id": "t",
        "roles": list(roles),
        "target_role": "rB",
        "latents": latents or [],
        "condition": cond,
    }


def test_ir_valid_and_invalid_parse():
    validate(_h({"const": True}))
    with pytest.raises(IRValidationError):
        validate(_h({"op": "nope"}))
    with pytest.raises(IRValidationError):
        validate(_h({"const": True}) | {"target_role": "missing"})
    with pytest.raises(IRValidationError):
        validate(_h({"op": "ge", "var": "k", "value": "x"}))


def test_ir_depth_and_op_limits():
    deep = {"const": True}
    for _ in range(MAX_DEPTH + 2):
        deep = {"op": "not", "arg": deep}
    with pytest.raises(IRValidationError):
        validate(_h(deep))
    lats = [
        {
            "name": f"z{i}",
            "type": "bool",
            "init": False,
            "set_on": {"event": "tick", "role": None},
            "reset_on": None,
        }
        for i in range(6)
    ]
    with pytest.raises(IRValidationError):
        validate(_h({"const": True}, latents=lats))


def test_unsupported_operation_rejected():
    with pytest.raises(IRValidationError):
        validate(_h({"op": "xor", "args": [{"const": True}, {"const": False}]}))
    bad_event = [
        {
            "name": "z",
            "type": "bool",
            "init": False,
            "set_on": {"event": "teleported", "role": "r0"},
            "reset_on": None,
        }
    ]
    with pytest.raises(IRValidationError):
        validate(_h({"var": "z"}, latents=bad_event))


def test_deterministic_execution_and_possession_semantics():
    h = _h(
        {"var": "z"},
        latents=[
            {
                "name": "z",
                "type": "bool",
                "init": False,
                "set_on": {"event": "overlap", "role": "r0"},
                "reset_on": None,
            }
        ],
    )
    binding = {"r0": "c1", "rB": "c2"}
    events = [(1, "tick", None), (3, "overlap", "c1"), (3, "disappeared", "c1")]
    pushes = [(2, "c2", "blocked"), (5, "c2", "pass")]
    r1 = execute(h, binding, events, pushes)
    r2 = execute(h, binding, events, pushes)
    assert r1.predictions == r2.predictions == [(2, "blocked"), (5, "pass")]


def test_counter_and_parity_and_window():
    hk = _h(
        {"op": "ge", "var": "k", "value": 2},
        latents=[
            {
                "name": "k",
                "type": "counter",
                "max": 16,
                "inc_on": {"event": "blocked", "role": "rB"},
            }
        ],
    )
    b = {"r0": "c1", "rB": "c2"}
    pushes = [(1, "c2", "blocked"), (2, "c2", "blocked"), (3, "c2", "pass")]
    events = [(1, "blocked", "c2"), (2, "blocked", "c2")]
    assert execute(hk, b, events, pushes).predictions == [
        (1, "blocked"),
        (2, "blocked"),
        (3, "pass"),
    ]
    hp = _h(
        {"op": "parity", "var": "p", "value": 1},
        latents=[{"name": "p", "type": "parity", "toggle_on": {"event": "overlap", "role": "r0"}}],
    )
    ev = [(1, "overlap", "c1"), (4, "overlap", "c1")]
    ps = [(2, "c2", "pass"), (6, "c2", "blocked")]
    assert execute(hp, b, ev, ps).predictions == [(2, "pass"), (6, "blocked")]
    hw = _h({"op": "window", "period": 6, "lo": 0, "hi": 2})
    ps2 = [(1, "c2", "pass"), (3, "c2", "blocked"), (6, "c2", "pass")]
    assert execute(hw, b, [], ps2).predictions == [(1, "pass"), (3, "blocked"), (6, "pass")]


def test_ordered_guard():
    lats = [
        {
            "name": "za",
            "type": "bool",
            "init": False,
            "set_on": {"event": "overlap", "role": "r0"},
            "reset_on": None,
        },
        {
            "name": "zb",
            "type": "bool",
            "init": False,
            "set_on": {"event": "overlap", "role": "r1"},
            "guard": {"var": "za"},
            "reset_on": None,
        },
    ]
    h = _h({"var": "zb"}, latents=lats, roles=("r0", "r1", "rB"))
    b = {"r0": "c1", "r1": "c2", "rB": "c3"}
    ev_good = [(1, "overlap", "c1"), (2, "overlap", "c2")]
    ev_bad = [(1, "overlap", "c2"), (2, "overlap", "c1")]
    ps = [(4, "c3", "?")]
    assert execute(h, b, ev_good, ps).predictions == [(4, "pass")]
    assert execute(h, b, ev_bad, ps).predictions == [(4, "blocked")]


def test_behavioural_equivalence():
    a = [(1, "pass"), (2, "blocked")]
    assert behaviourally_equivalent(a, list(a))
    assert not behaviourally_equivalent(a, [(1, "pass"), (2, "pass")])


def test_description_length_monotone():
    small = description_length(_h({"const": True}))
    big = description_length(
        _h({"op": "and", "args": [{"const": True}, {"op": "ge_t", "value": 5}]})
    )
    assert big > small


def test_grammar_no_duplicates_and_valid():
    pool = grammar_proposals(["r0", "r1", "rB"], "rB", budget=500)
    assert len(pool) > 30
    from rift.hypothesis import canonical

    cs = [canonical(h) for h in pool]
    assert len(cs) == len(set(cs))
    for h in pool[:50]:
        validate(h)


def test_fake_provider_labels_fixtures_and_flags_invalid():
    good = _h({"const": True})
    bad = _h({"op": "bogus"})
    out = fake_proposals([good, bad])
    assert out[0].valid and out[0].meta["fixture"]
    assert not out[1].valid and out[1].failure


def test_no_silent_fallback_between_providers():
    """llm_proposals must raise (never return grammar results) w/o creds."""
    import os

    from rift.proposers import LiveLLMUnavailable, llm_proposals

    assert not os.environ.get("ANTHROPIC_API_KEY")
    with pytest.raises(LiveLLMUnavailable):
        llm_proposals({}, ["rB"], "rB", 3)


def test_metric_correctness():
    rep = classification_report(tp=8, fp=2, tn=6, fn=4)
    assert abs(rep["precision"] - 0.8) < 1e-9
    assert abs(rep["recall"] - 8 / 12) < 1e-9
    assert abs(rep["balanced_accuracy"] - (8 / 12 + 6 / 8) / 2) < 1e-9
    assert rep["confusion"] == {"tp": 8, "fp": 2, "tn": 6, "fn": 4}
    lo, hi = bootstrap_ci([1.0] * 10)
    assert lo == hi == 1.0
    d = paired_diff_ci([2.0, 2.0], [1.0, 1.0])
    assert d["mean_diff"] == 1.0


def test_reproducibility_of_grammar_and_execution():
    p1 = grammar_proposals(["r0", "rB"], "rB", budget=200)
    p2 = grammar_proposals(["r0", "rB"], "rB", budget=200)
    assert p1 == p2
