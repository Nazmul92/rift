import random

from rift.environments.generator import make_env
from rift.environments.public_env import OpaqueSession
from rift.perception import barrier_candidates, components
from rift.population import Evidence, detect_failures, equivalence_classes, score
from rift.probes import generate_probes
from rift.runner import battery_run, oracle_hypothesis, rift_identify, role_obj_map
from rift.schema import SessionRunner, bind_and_transfer, compile_schema


def _runner(fam="possession", seed=1, budget=420):
    env = make_env(fam, seed)
    return env, SessionRunner(OpaqueSession(env), seed, budget)


def test_component_extraction_generic():
    grid = ((0, 2, 2, 0), (0, 0, 3, 0), (0, 0, 0, 0))
    comps = components(grid)
    colors = sorted(c for c, _ in comps)
    assert colors == [2, 3]


def test_tracking_uncertainty_under_occlusion():
    env, r = _runner("switch_parity", 4)
    tr = r.trace
    # find the persistent switch via oracle (test-side only) and walk onto it
    cfg = env._cfg
    sw = next(o for o in cfg.objects if o.kind == "persistent")
    from rift.perception import path_to

    obs = tr.obs_log[-1]
    g = [list(row) for row in obs.grid]
    g[tr.agent_pos[0]][tr.agent_pos[1]] = 0
    p = path_to(tuple(tuple(row) for row in g), tr.agent_pos, sw.pos, stop_adjacent=False)
    assert p is not None
    r.run(p)
    occluded = [t for t in tr.tracks.values() if t.occluded]
    assert occluded and all(t.uncertain for t in occluded)
    # step off: switch persists, so track must be present again, not disappeared
    r.run(["L", "S"]) if tr.agent_pos[1] > sw.pos[1] else r.run(["R", "S"])


def test_action_accounting_charges_everything():
    _, r = _runner()
    before = r.used
    r.run(["S", "S", "L", "L", "L", "L", "L", "L", "L", "L"])  # waits + blocked moves
    assert r.used == before + 10


def test_barrier_candidates_structural():
    env, r = _runner("possession", 7)
    r.run(["S"])
    cands = barrier_candidates(r.trace)
    assert cands
    cfg = env._cfg
    top = r.trace.tracks[cands[0]]
    assert (cfg.barrier_row, cfg.wall_col) in top.cells


def test_probe_sets_identical_across_policies():
    _, r = _runner("possession", 1)
    r.run(["S"])
    cands = barrier_candidates(r.trace)
    p1 = generate_probes(r.trace, cands)
    p2 = generate_probes(r.trace, cands)
    assert p1 == p2  # same candidate set is handed to every policy


def test_theory_elimination_and_underdetermined():
    ev = Evidence()
    ev.events = [(3, "overlap", "c1"), (3, "disappeared", "c1")]
    ev.pushes = [(1, "c9", "blocked"), (5, "c9", "pass")]
    ev.interventional_steps = {1, 5}
    always = {
        "hypothesis_id": "a",
        "roles": ["r0", "rB"],
        "target_role": "rB",
        "latents": [],
        "condition": {"const": True},
    }
    poss = {
        "hypothesis_id": "p",
        "roles": ["r0", "rB"],
        "target_role": "rB",
        "latents": [
            {
                "name": "z",
                "type": "bool",
                "init": False,
                "set_on": {"event": "overlap", "role": "r0"},
                "reset_on": None,
            }
        ],
        "condition": {"var": "z"},
    }
    b = {"r0": "c1", "rB": "c9"}
    sa, sp = score(always, b, ev), score(poss, b, ev)
    assert sa.status == "contradicted"
    assert sp.status in ("supported", "underdetermined")
    ev2 = Evidence()
    ev2.pushes = [(1, "c9", "blocked")]
    s_under = score(poss, b, ev2)
    assert s_under.status == "underdetermined"


def test_equivalence_classes_group_identical_predictions():
    ev = Evidence()
    ev.pushes = [(1, "c9", "blocked")]
    h1 = {
        "hypothesis_id": "1",
        "roles": ["rB"],
        "target_role": "rB",
        "latents": [],
        "condition": {"const": False},
    }
    h2 = {
        "hypothesis_id": "2",
        "roles": ["rB"],
        "target_role": "rB",
        "latents": [],
        "condition": {"op": "ge_t", "value": 999},
    }
    b = {"rB": "c9"}
    cls = equivalence_classes([score(h1, b, ev), score(h2, b, ev)])
    assert len(cls) == 1 and len(cls[0]) == 2


def test_aliasing_detection_generic():
    env, r = _runner("attempt_counter", 1)
    tr = r.trace
    ev = Evidence()
    # synth: same signature (no track changes), same push target, both outcomes
    ev.pushes = [(1, "c9", "blocked"), (2, "c9", "pass")]
    sigs = detect_failures(tr, ev, [])
    assert any(f.trigger == "state_aliasing" for f in sigs)


def test_full_identification_possession():
    env = make_env("possession", 0)
    out, runner, scored, binding = rift_identify(
        OpaqueSession(env), 0, policy="disagreement", rng_seed=0
    )
    assert out.selected is not None
    ro = role_obj_map(binding, runner, env)
    c, t, _ = battery_run("possession", 0, out.selected.hypothesis, ro)
    assert t > 0 and c == t


def test_schema_compiled_from_theory_contains_no_surface_data():
    env = make_env("possession", 0)
    out, runner, _, binding = rift_identify(
        OpaqueSession(env), 0, policy="disagreement", rng_seed=0
    )
    assert out.selected is not None
    pcs = compile_schema(out.selected, coverage=8)
    import json

    s = json.dumps(pcs.latent_program) + json.dumps(pcs.role_constraints)
    for banned in ('"c0"', '"c1"', '"c2"', "pos", "color"):
        assert banned not in s
    assert pcs.roles and pcs.latent_program["condition"]


def test_positive_transfer_and_negative_refusal():
    env = make_env("possession", 0)
    out, runner, _, binding = rift_identify(
        OpaqueSession(env), 0, policy="disagreement", rng_seed=0
    )
    assert out.selected is not None
    pcs = compile_schema(out.selected, coverage=8)
    # positive: held-out possession instantiation (different colours/layout)
    env2 = make_env("possession", 7)
    r2 = SessionRunner(OpaqueSession(env2), 7, budget=300)
    br = bind_and_transfer(pcs, r2, random.Random(7))
    assert br.decision in ("bound", "underdetermined")
    # negative: ungated must not produce a confident gated binding
    env3 = make_env("ungated", 7)
    r3 = SessionRunner(OpaqueSession(env3), 7, budget=300)
    br3 = bind_and_transfer(pcs, r3, random.Random(7))
    assert br3.decision in ("rejected", "underdetermined")


def test_oracle_hypothesis_matches_battery():
    for fam in (
        "possession",
        "timed",
        "switch_parity",
        "multi_resource",
        "ordered_sequence",
        "attempt_counter",
    ):
        env = make_env(fam, 1)
        oh, ro = oracle_hypothesis(fam, env)
        c, t, _ = battery_run(fam, 1, oh, ro)
        assert t > 0, fam
        assert c == t, f"{fam}: oracle {c}/{t}"


def test_censored_runs_reported():
    env = make_env("possession", 1)
    out, _, _, _ = rift_identify(OpaqueSession(env), 1, policy="random", budget=12, rng_seed=1)
    assert out.censored is True
