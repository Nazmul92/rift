import ast
import random
import tempfile
from pathlib import Path

import pytest

from riftcode.contracts import FAIL, PASS, Budget, Intervention
from riftcode.harness.injector import FAULTS, ORACLE_CAUSE, TARGET, build_repo
from riftcode.loop import localize, verify
from riftcode.observe import EvidenceBuilder, discover_interventions, role_map
from riftcode.probes import code_grammar, generate_probes, select_probe
from riftcode.sandbox import Sandbox

SRC = Path(__file__).resolve().parents[1] / "src" / "riftcode"
AGENT_MODULES = ["contracts.py", "sandbox.py", "observe.py", "probes.py", "loop.py"]


def _sandbox(fault, budget=None, seed=0):
    tmp = Path(tempfile.mkdtemp(prefix="tmpl_"))
    build_repo(fault, tmp, seed)
    return Sandbox(tmp, budget or Budget(max_commands=60, max_seconds=240))


def test_agent_modules_do_not_import_harness():
    for m in AGENT_MODULES:
        tree = ast.parse((SRC / m).read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                assert "harness" not in n, f"{m} imports {n}"


def test_discovery_is_generic_and_names_are_anonymised():
    handles = discover_interventions(
        "AssertionError: missing configuration APP_TOKEN",
        [".appcache/", ".appcache/state", "tests/test_seed.py"],
        ["tests/test_seed.py::test_seed", TARGET],
        TARGET,
    )
    labels = [h.label for h in handles]
    assert "env:APP_TOKEN" in labels
    assert "clear:.appcache" in labels
    assert "first:tests/test_seed.py" in labels
    mapping, roles = role_map(handles)
    assert roles[-1] == "rT"
    # the hypothesis language sees only r0..rN, never the handle names
    import json

    pool = code_grammar(roles)
    blob = json.dumps(pool)
    assert "APP_TOKEN" not in blob and "appcache" not in blob


def test_grammar_covers_required_shapes():
    _, roles = role_map([Intervention("env", "A"), Intervention("clear", ".c")])
    pool = code_grammar(roles)
    conds = [h["condition"] for h in pool]
    assert {"const": False} in conds
    assert any(c.get("op") == "ge" for c in conds)
    assert any(c.get("op") == "and" for c in conds)
    from rift.hypothesis import validate

    for h in pool:
        validate(h)


def test_probe_set_identical_across_policies():
    _, roles = role_map([Intervention("env", "A")])
    p1 = generate_probes(roles)
    p2 = generate_probes(roles)
    assert p1 == p2
    eb = EvidenceBuilder()
    eb.record((), 1, FAIL, interventional=False)
    chosen = select_probe("random", p1, [], eb, random.Random(0))
    assert chosen in p1


def test_budget_charges_every_command():
    sb = _sandbox("code_defect", Budget(max_commands=3, max_seconds=120))
    sb.run_target(TARGET)
    sb.run_target(TARGET)
    assert sb.budget.commands == 2 and sb.budget.seconds > 0
    sb.dispose()


def test_fresh_episode_discards_runtime_state():
    sb = _sandbox("retry_flake")
    assert sb.run_target(TARGET).outcome == FAIL
    assert sb.run_target(TARGET).outcome == FAIL
    assert sb.run_target(TARGET).outcome == PASS  # 3rd attempt in this episode
    sb.fresh()
    assert sb.run_target(TARGET).outcome == FAIL  # counter reset with the episode
    sb.dispose()


@pytest.mark.parametrize("fault", ["env_gated", "cache_stale", "order_dependent"])
def test_localizes_environmental_causes(fault):
    sb = _sandbox(fault)
    diag, _, _ = localize(sb, TARGET, rng_seed=0)
    assert diag.status == "identified", diag.notes
    ok = ORACLE_CAUSE[fault]
    assert diag.cause is not None and ok is not None and diag.cause.label in ok
    sb.dispose()


def test_decoy_is_not_mistaken_for_cause():
    """The failure message names APP_TOKEN; the cause is the stale cache."""
    sb = _sandbox("decoy_correlated")
    diag, _, _ = localize(sb, TARGET, rng_seed=0)
    assert diag.cause is not None
    assert diag.cause.label in (ORACLE_CAUSE["decoy_correlated"] or ())
    sb.dispose()


def test_code_defect_yields_no_environmental_cause():
    sb = _sandbox("code_defect")
    diag, _, _ = localize(sb, TARGET, rng_seed=0)
    assert diag.cause is None
    assert diag.hypothesis is not None
    assert diag.hypothesis["condition"] == {"const": False}
    sb.dispose()


def test_retry_flake_detected_as_nondeterminism_not_as_a_handle():
    sb = _sandbox("retry_flake")
    diag, eb, _ = localize(sb, TARGET, rng_seed=0)
    assert diag.cause is None
    assert diag.hypothesis is not None
    assert diag.hypothesis["condition"].get("op") == "ge"
    sb.dispose()


def test_gate_accepts_real_fix_and_rejects_false_one():
    sb = _sandbox("env_gated")
    diag, _, _ = localize(sb, TARGET, rng_seed=0)
    assert verify(sb, TARGET, diag)["verdict"] == "verified"
    # a wrong "fix" (an unrelated env var) must not pass the gate
    bogus = Intervention("env", "UNRELATED_FLAG")
    from riftcode.contracts import Diagnosis

    fake = Diagnosis("identified", bogus, None, 1, 0, 0.0)
    assert verify(sb, TARGET, fake)["verdict"] == "rejected"
    sb.dispose()


def test_gate_not_applicable_without_environmental_cause():
    sb = _sandbox("code_defect")
    diag, _, _ = localize(sb, TARGET, rng_seed=0)
    assert verify(sb, TARGET, diag)["verdict"] == "not_applicable"
    sb.dispose()


def test_all_fault_families_build_and_reproduce():
    for fault in FAULTS:
        sb = _sandbox(fault)
        assert sb.run_target(TARGET).outcome == FAIL, fault
        sb.dispose()
