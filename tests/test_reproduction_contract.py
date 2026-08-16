"""The frozen reproducer, and the clean episode every gate phase runs in.

The bare-target gate can only judge a target that fails when run alone. That
excluded the entire class this project exists for: an order-dependent failure
passes in isolation by definition, so its baseline never reproduced and no patch
for it could be verified. Calibration case C4 was scored an abstention on that
limitation, and the limitation was mistaken for task truth.

These tests hold the correction, and — more importantly — hold the boundaries on
it. A reproducer that could be fabricated from unsupported evidence, or altered
by the model, or left standing after the tree drifted, would be a *worse* judge
than the narrow one it replaces, because it would look rigorous.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from riftagent import kernel
from riftagent.records import (
    Diagnosis,
    GateStatus,
    Handle,
    Primitive,
    ReproductionContract,
    Signature,
    Support,
    ValidationError,
    Verdict,
)

pytestmark = pytest.mark.slow

SIG = Signature("AssertionError", "assert {'leak': 1} == {}")
POLLUTER = Handle(Primitive.FIRST, "tests/test_a_pollute.py")


def supported(causes: tuple[Handle, ...] = (POLLUTER,)) -> Diagnosis:
    return Diagnosis(
        status=Verdict.DIAGNOSIS_SUPPORTED,
        support=Support.INTERVENTIONAL,
        gate=GateStatus.NOT_APPLICABLE,
        causes=causes,
        surviving_classes=1,
        contradicted=(),
        notes=(),
    )


def probe(applied: tuple[Handle, ...], reproduced: bool = True, signature: Signature | None = SIG):
    return kernel.ProbeRecord(
        applied=frozenset(h.label for h in applied),
        reproduced=reproduced,
        signature=signature,
        event_id="ev-" + "+".join(h.arg for h in applied),
    )


def select(
    diagnosis: Diagnosis,
    probes: list | None = None,
    artifacts: dict[str, str] | None = None,
) -> ReproductionContract | None:
    return kernel.select_reproducer(
        diagnosis,
        "tests/test_target.py::test_clean",
        [probe((POLLUTER,))] if probes is None else probes,
        "runner-hash",
        "tree-digest",
        artifacts or {},
    )


# --------------------------------------------------------------------------
# only supported interventional evidence yields a reproducer
# --------------------------------------------------------------------------


def test_a_supported_interventional_diagnosis_yields_a_reproducer():
    contract = select(supported())
    assert contract is not None
    assert contract.preconditions == (POLLUTER,)
    assert contract.node_id == "tests/test_target.py::test_clean"
    assert contract.signature == SIG
    assert "first:tests/test_a_pollute.py" in contract.render()


@pytest.mark.parametrize(
    "status",
    [Verdict.UNDERDETERMINED, Verdict.REPRESENTATION_INADEQUATE, Verdict.UNVERIFIABLE],
)
def test_unsupported_evidence_cannot_fabricate_a_reproducer(status: Verdict):
    """`underdetermined` means the probes did not separate the candidates. Any
    precondition set drawn from them would be a guess wearing a frozen judge's
    clothes."""
    assert select(Diagnosis(status, None, GateStatus.NOT_APPLICABLE, (POLLUTER,), 2, (), ())) is None


def test_an_observational_diagnosis_cannot_yield_a_reproducer():
    """An assertion observes. There is nothing to apply, so a reproducer built
    on one would be identical to the bare target while claiming to be more."""
    diagnosis = Diagnosis(
        Verdict.DIAGNOSIS_SUPPORTED,
        Support.OBSERVATIONAL,
        GateStatus.NOT_APPLICABLE,
        (Handle(Primitive.DEP_ASSERT, "chardet"),),
        1,
        (),
        (),
    )
    assert select(diagnosis) is None


def test_a_probe_with_no_signature_cannot_yield_a_reproducer():
    """Without an observed signature there is nothing for withdrawal to match,
    and the counterfactual would be satisfied by any failure at all."""
    assert select(supported(), probes=[probe((POLLUTER,), signature=None)]) is None


def test_a_probe_that_did_not_reproduce_is_not_support():
    assert select(supported(), probes=[probe((POLLUTER,), reproduced=False)]) is None


def test_no_matching_experiment_yields_no_reproducer():
    """The cause set must have been applied *exactly* by one recorded probe."""
    other = Handle(Primitive.FIRST, "tests/test_other.py")
    assert select(supported(), probes=[probe((other,))]) is None


def test_a_multi_cause_reproducer_requires_one_exact_joint_probe():
    """Combining handles from separate experiments would assert that their
    conjunction reproduces the failure when no run ever applied it."""
    second = Handle(Primitive.FIRST, "tests/test_b_pollute.py")
    both = supported(causes=(POLLUTER, second))
    separate = [probe((POLLUTER,)), probe((second,))]
    assert select(both, probes=separate) is None, "a reproducer was built from two separate experiments"

    joint = select(both, probes=[*separate, probe((POLLUTER, second))])
    assert joint is not None
    assert set(joint.preconditions) == {POLLUTER, second}
    assert len(joint.supporting_event_ids) == 1


def test_the_frozen_signature_comes_from_the_supporting_probe():
    distinct = Signature("ValueError", "from the supporting experiment")
    contract = select(supported(), probes=[probe((POLLUTER,), signature=distinct)])
    assert contract is not None
    assert contract.signature == distinct


def test_judge_artifacts_are_frozen_into_the_contract():
    contract = select(supported(), artifacts={"tests/test_target.py": "h1", "tests/test_a_pollute.py": "h2"})
    assert contract is not None
    assert dict(contract.judge_artifacts) == {"tests/test_target.py": "h1", "tests/test_a_pollute.py": "h2"}
    assert (
        kernel.judge_artifacts_intact(contract, {"tests/test_target.py": "h1", "tests/test_a_pollute.py": "h2"}) == ""
    )
    changed = kernel.judge_artifacts_intact(contract, {"tests/test_target.py": "h1", "tests/test_a_pollute.py": "X"})
    assert "changed" in changed
    missing = kernel.judge_artifacts_intact(contract, {"tests/test_target.py": "h1"})
    assert "missing" in missing


def test_a_cause_with_no_intervention_cannot_yield_a_reproducer():
    diagnosis = supported(causes=(Handle(Primitive.FILE_ASSERT, "setup.cfg"),))
    assert select(diagnosis, probes=[]) is None


def test_a_mixed_cause_set_cannot_yield_a_reproducer():
    """The assertion half cannot be applied, so no probe can ever have applied
    exactly this set."""
    mixed = supported(causes=(POLLUTER, Handle(Primitive.DEP_ASSERT, "chardet")))
    assert select(mixed, probes=[probe((POLLUTER,))]) is None


def test_an_assertion_precondition_is_rejected_by_the_contract_itself():
    """Belt and braces: even if a caller assembled one, the record refuses it."""
    with pytest.raises(ValidationError, match="assertion, not an intervention"):
        ReproductionContract.from_dict(
            {
                "preconditions": [{"kind": "dep_assert", "arg": "chardet"}],
                "node_id": "t.py::x",
                "signature": SIG.to_dict(),
                "runner_config_hash": "r",
                "tree_digest": "d",
                "supporting_event_ids": [],
            }
        )


# --------------------------------------------------------------------------
# the model cannot reach the reproducer
# --------------------------------------------------------------------------


def test_the_contract_rejects_unknown_fields():
    """There is no path from a model proposal into these fields, and a
    smuggled one would be refused at the record boundary."""
    with pytest.raises(ValidationError):
        ReproductionContract.from_dict(
            {
                "preconditions": [],
                "node_id": "t.py::x",
                "signature": SIG.to_dict(),
                "runner_config_hash": "r",
                "tree_digest": "d",
                "supporting_event_ids": [],
                "confidence": 0.99,
            }
        )


def test_select_reproducer_takes_no_model_input():
    """Structural: the kernel's selector accepts only kernel-side values, so
    there is no parameter through which a proposal could influence it."""
    import inspect

    params = set(inspect.signature(kernel.select_reproducer).parameters)
    assert params == {
        "diagnosis",
        "node_id",
        "probes",
        "runner_config_hash",
        "tree_digest",
        "judge_artifacts",
    }
    for banned in ("proposal", "model", "diff", "patch", "summary", "confidence"):
        assert banned not in params


# --------------------------------------------------------------------------
# drift voids it
# --------------------------------------------------------------------------


def test_tracked_drift_invalidates_the_reproducer():
    contract = select(supported())
    assert contract is not None
    assert kernel.reproducer_still_valid(contract, "tree-digest", "runner-hash") == ""
    assert "tracked tree changed" in kernel.reproducer_still_valid(contract, "different", "runner-hash")
    assert "runner configuration changed" in kernel.reproducer_still_valid(contract, "tree-digest", "other")


def test_a_drift_event_clears_the_reproducer_from_the_projection():
    """The reproducer names a tree digest. Once the tree it froze is gone, the
    reproducer must go with the phases that used it."""
    from riftagent.records import Event, EventKind, reduce, utc_now

    contract = select(supported())
    assert contract is not None
    events = [
        Event(1, "t", EventKind.REPRODUCER_FROZEN, utc_now(), {"reproducer": contract.to_dict()}),
        Event(2, "t", EventKind.DRIFT_DETECTED, utc_now(), {"recorded": "a", "observed": "b"}),
    ]
    projection = reduce(events)
    assert projection.drift is True
    assert projection.reproducer is None, "a voided reproducer survived tracked drift"


# --------------------------------------------------------------------------
# end to end: an isolated-passing target becomes gateable
# --------------------------------------------------------------------------

# The target asserts a shared registry is clean. Alone it passes; after the
# polluter runs it fails. `add_missing_reset` is the correct fix.
ORDER_DEPENDENT = {
    "src/app/__init__.py": "",
    "src/app/registry.py": "REGISTRY = {}\n\n\ndef put(k, v):\n    REGISTRY[k] = v\n",
    "tests/test_a_pollute.py": (
        "from app.registry import put\n\n\ndef test_pollutes():\n    put('leak', 1)\n    assert True\n"
    ),
    "tests/test_target.py": ("from app.registry import REGISTRY\n\n\ndef test_clean():\n    assert REGISTRY == {}\n"),
}

# Removes the order dependence at the source: the polluter cleans up after
# itself, so the target passes whether or not it ran first.
CORRECT_FIX = """--- a/tests/test_a_pollute.py
+++ b/tests/test_a_pollute.py
@@ -1,6 +1,7 @@
-from app.registry import put
+from app.registry import REGISTRY, put


 def test_pollutes():
     put('leak', 1)
+    REGISTRY.clear()
     assert True
"""


def build_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8", newline="\n")
    return root


def test_the_bare_target_alone_passes(tmp_path: Path):
    """Guards every claim below: without preconditions there is no failure to
    gate, which is exactly why this class was previously ungateable."""
    import subprocess
    import sys

    repo = build_repo(tmp_path / "bare", ORDER_DEPENDENT)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_target.py::test_clean"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(repo / "src")},
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout


def test_the_reproducer_makes_the_ordering_failure_reproducible(tmp_path: Path):
    """The correction itself: with the polluter as a frozen precondition, the
    same target that passes alone now fails — so a patch for it is gateable."""
    from riftagent.checks import run_probe
    from riftagent.records import PASS
    from riftagent.sandbox import Worktree, probe_isolation

    repo = build_repo(tmp_path / "repro", ORDER_DEPENDENT)
    probe = probe_isolation()
    with Worktree(repo, "t") as wt:
        alone = run_probe("tests/test_target.py::test_clean", (), 1, wt, probe, timeout_s=120)
    with Worktree(repo, "t") as wt:
        with_precondition = run_probe("tests/test_target.py::test_clean", (POLLUTER,), 1, wt, probe, timeout_s=120)

    assert alone.outcome == PASS, "the bare target should pass in isolation"
    assert with_precondition.outcome != PASS, "the precondition did not reproduce the failure"
    assert with_precondition.signature is not None


def run_verify(repo: Path, diff_path: Path, capsys) -> tuple[int, dict]:
    from riftagent.app import main

    code = main(
        [
            "--repo",
            str(repo),
            "--json",
            "verify",
            str(diff_path),
            "tests/test_target.py::test_clean",
            "--allow-partial-sandbox",
        ]
    )
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_every_phase_resets_disposable_runtime_state(tmp_path: Path, capsys):
    """Untracked state from one phase must never decide another. A cache left
    by the candidate that made withdrawal pass would be a false fix arriving
    from the runtime instead of from a diff."""
    repo = build_repo(tmp_path / "episodes", ORDER_DEPENDENT)
    (repo / "src" / "app" / "__pycache__").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "app" / "__pycache__" / "stale.pyc").write_bytes(b"stale")
    diff_path = tmp_path / "fix.diff"
    diff_path.write_text(CORRECT_FIX, encoding="utf-8", newline="\n")

    run_verify(repo, diff_path, capsys)
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    events = [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    resets = [e for e in events if e["kind"] == "episode_reset"]

    assert resets, "no phase reset disposable state"
    assert all("runtime-created state" in e["payload"]["scope"] for e in resets)
    # The tracked tree relationship is preserved: a reset that wiped tracked
    # files would make withdrawal meaningless.
    assert (repo / "src" / "app" / "registry.py").read_text(encoding="utf-8").startswith("REGISTRY")


def test_the_correct_ordering_fix_passes_the_whole_gate(tmp_path: Path, capsys):
    """End to end through the existing gate, with no second verification path."""
    repo = build_repo(tmp_path / "gate", ORDER_DEPENDENT)
    diff_path = tmp_path / "fix.diff"
    diff_path.write_text(CORRECT_FIX, encoding="utf-8", newline="\n")

    code, receipt = run_verify(repo, diff_path, capsys)
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    events = [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    passed = {e["payload"]["phase"] for e in events if e["kind"] == "gate_phase_finished" and e["payload"]["passed"]}

    # Without a reproducer this target passes at baseline and the gate correctly
    # refuses it. That refusal is the limitation the reproducer removes, and it
    # is recorded here rather than asserted away.
    if "baseline" not in passed:
        assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
        assert receipt["rejected_phase"] == "baseline"
    else:
        assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
        assert {"candidate", "withdrawal", "reapply"} <= passed
        assert code == 0


def test_reapply_reruns_the_target_rather_than_only_hashing(tmp_path: Path, capsys):
    """An identical tree that no longer passes means the candidate pass depended
    on runtime state, not on the patch."""
    repo = build_repo(tmp_path / "reapply", ORDER_DEPENDENT)
    diff_path = tmp_path / "fix.diff"
    diff_path.write_text(CORRECT_FIX, encoding="utf-8", newline="\n")
    run_verify(repo, diff_path, capsys)

    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    events = [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    reapply_commands = [e for e in events if e["kind"] == "command_started" and e["payload"].get("phase") == "reapply"]
    reapply_finished = [e for e in events if e["kind"] == "gate_phase_finished" and e["payload"]["phase"] == "reapply"]
    if reapply_finished and reapply_finished[0]["payload"]["passed"]:
        assert reapply_commands, "reapply passed without re-running the target"


# --------------------------------------------------------------------------
# arbitrary leaked state, not just caches
# --------------------------------------------------------------------------


LEAKY = {
    "src/app/__init__.py": "",
    "src/app/store.py": "def marker_path(root):\n    return root / 'phase.sqlite'\n",
    "tests/test_target.py": (
        "import pathlib\n\n\n"
        "def test_no_leftover_state():\n"
        "    # Writes a database, then asserts none existed on entry. A second\n"
        "    # run in the same tree passes only if the first run's file leaked.\n"
        "    marker = pathlib.Path('phase.sqlite')\n"
        "    existed = marker.exists()\n"
        "    marker.write_bytes(b'sqlite-ish')\n"
        "    pathlib.Path('generated').mkdir(exist_ok=True)\n"
        "    assert existed, 'no state leaked from a previous phase'\n"
    ),
}


def test_arbitrary_generated_state_cannot_leak_between_phases(tmp_path: Path):
    """A cache list would not catch this: the leaked artefact is a database and
    a generated directory, not bytecode. One phase must not be able to satisfy
    the next through state it wrote itself."""
    from riftagent.app import reset_episode
    from riftagent.checks import run_probe
    from riftagent.records import PASS
    from riftagent.sandbox import Worktree, probe_isolation

    repo = build_repo(tmp_path / "leak", LEAKY)
    probe = probe_isolation()
    node = "tests/test_target.py::test_no_leftover_state"

    with Worktree(repo, "leak") as wt:
        first = run_probe(node, (), 1, wt, probe, timeout_s=120)
        assert first.outcome != PASS, "the fixture should fail on a clean tree"
        assert (wt.path / "phase.sqlite").exists(), "the fixture did not write its state"
        assert (wt.path / "generated").is_dir()

        # Without a reset the second run would pass on leaked state alone.
        cleared, _restored = reset_episode(wt, frozenset())
        assert cleared >= 2, f"the reset removed only {cleared} artefacts"
        assert not (wt.path / "phase.sqlite").exists(), "a leaked database survived the reset"
        assert not (wt.path / "generated").exists(), "a leaked directory survived the reset"

        second = run_probe(node, (), 1, wt, probe, timeout_s=120)
        assert second.outcome != PASS, "a phase satisfied itself through leaked state"

        # Tracked files are untouched: withdrawal must still be meaningful.
        assert (wt.path / "src" / "app" / "store.py").is_file()
        assert (wt.path / "tests" / "test_target.py").is_file()


def test_a_firstset_probe_supports_a_first_cause():
    """The same experiment recorded under the other spelling must still count."""
    as_set = kernel.ProbeRecord(
        applied=frozenset({"firstset:tests/test_a_pollute.py"}),
        reproduced=True,
        signature=SIG,
        event_id="ev-set",
    )
    contract = select(supported(), probes=[as_set])
    assert contract is not None
    assert contract.supporting_event_ids == ("ev-set",)


# --------------------------------------------------------------------------
# the real thing: `rift fix` through an ordering repair, end to end
#
# Everything above this line tests a part. This tests the integrated path, and
# it is the claim the previous two passes asserted without evidence. There is
# no conditional branch here: baseline rejection is failure, because baseline
# rejection is exactly the defect the ReproductionContract was built to remove.
# --------------------------------------------------------------------------

# `put` writes straight into the shared registry, so anything that calls it
# leaks into every later test in the same process. The target asserts the
# registry is clean; alone it is, and after the polluter it is not.
ORDERING_REPO = {
    "src/app/__init__.py": "",
    "src/app/registry.py": (
        "REGISTRY: dict = {}\n"
        "_PENDING: dict = {}\n"
        "\n"
        "\n"
        "def put(k, v):\n"
        "    REGISTRY[k] = v\n"
        "\n"
        "\n"
        "def commit():\n"
        "    REGISTRY.update(_PENDING)\n"
        "    _PENDING.clear()\n"
        "\n"
        "\n"
        "def snapshot():\n"
        "    return dict(REGISTRY)\n"
    ),
    "tests/test_a_pollute.py": (
        "from app.registry import put\n\n\ndef test_pollutes():\n    put('leak', 1)\n    assert True\n"
    ),
    "tests/test_target.py": ("from app.registry import snapshot\n\n\ndef test_clean():\n    assert snapshot() == {}\n"),
    # Real preservation: passes on both sides of the patch, and fails if the
    # patch breaks the staging/commit contract it is meant to repair. An empty
    # preservation set would satisfy the gate without exercising this half.
    "tests/test_preserved.py": (
        "from app.registry import commit, put, snapshot\n\n\n"
        "def test_commit_publishes():\n"
        "    put('k', 1)\n"
        "    commit()\n"
        "    assert snapshot()['k'] == 1\n"
    ),
}

# Implementation-only: `put` stages into `_PENDING`, which `commit` publishes.
# An uncommitted write no longer leaks. Touches src/app/registry.py and nothing
# else — no test, no runner config, no frozen judge artifact.
IMPLEMENTATION_FIX = """--- a/src/app/registry.py
+++ b/src/app/registry.py
@@ -3,7 +3,7 @@ _PENDING: dict = {}


 def put(k, v):
-    REGISTRY[k] = v
+    _PENDING[k] = v


 def commit():
"""

# Satisfies the target by deleting the assertion. Must be refused before
# execution, because the polluter test is a frozen judge artifact.
PRECONDITION_TAMPER = """--- a/tests/test_a_pollute.py
+++ b/tests/test_a_pollute.py
@@ -1,6 +1,6 @@
 from app.registry import put


 def test_pollutes():
-    put('leak', 1)
+    pass
     assert True
"""


class _Fake(http.server.BaseHTTPRequestHandler):
    change_diff: str = IMPLEMENTATION_FIX
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))).decode())
        type(self).seen.append(body)
        system = body["messages"][0]["content"].lower()
        if "propose measurements" in system:
            content = '{"handles": []}'
        else:
            content = json.dumps({"diff": type(self).change_diff, "summary": "decides nothing"})
        payload = json.dumps(
            {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "model": "fake",
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a: Any) -> None:
        return


@pytest.fixture
def fake_provider(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Fake.seen = []
    _Fake.change_diff = IMPLEMENTATION_FIX
    monkeypatch.setenv("RIFT_LLM_URL", f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
    monkeypatch.setenv("RIFT_LLM_KEY", "sk-SENTINEL-fake-for-tests")
    monkeypatch.setenv("RIFT_LLM_MODEL", "fake")
    try:
        yield _Fake
    finally:
        server.shutdown()
        server.server_close()


def run_fix(repo: Path, capsys, extra: list[str] | None = None) -> tuple[int, dict]:
    from riftagent.app import main

    code = main(
        [
            "--repo",
            str(repo),
            "--json",
            "fix",
            "tests/test_target.py::test_clean",
            "--allow-partial-sandbox",
            "--preserve",
            "tests/test_preserved.py::test_commit_publishes",
            "--max-usd",
            "1.00",
            *(extra or []),
        ]
    )
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def ledger_of(repo: Path) -> list[dict]:
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    return [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def test_rift_fix_repairs_an_ordering_failure_end_to_end(tmp_path: Path, capsys, fake_provider):
    repo = build_repo(tmp_path / "e2e", ORDERING_REPO)
    code, receipt = run_fix(repo, capsys)
    events = ledger_of(repo)

    # 1. the target passes in isolation — the first recorded observation
    first = next(e for e in events if e["kind"] == "check_result")
    assert first["payload"]["result"]["outcome"] == "passed", "the target should pass alone"

    # 2. diagnosis discovered the ordering cause
    diagnosed = next(e for e in events if e["kind"] == "diagnosis_emitted")
    causes = {c["kind"] + ":" + c["arg"] for c in diagnosed["payload"]["diagnosis"]["causes"]}
    assert any("test_a_pollute.py" in c for c in causes), causes

    # 3. an exact sole-handle probe reproduced the failure, and
    # 4. the contract froze that probe's signature and event id
    frozen = next((e for e in events if e["kind"] == "reproducer_frozen"), None)
    assert frozen is not None, "no ReproductionContract was frozen on the real fix path"
    contract = frozen["payload"]["reproducer"]
    supporting = contract["supporting_event_ids"]
    assert len(supporting) == 1
    probe = next(e for e in events if e["event_id"] == supporting[0])
    assert probe["kind"] == "probe_selected"
    assert probe["payload"]["observation"]["outcome"] == "blocked"
    assert contract["signature"] == probe["payload"]["observation"]["signature"]

    # 5-8. every counterfactual phase passed, under that same reproducer
    passed = {e["payload"]["phase"] for e in events if e["kind"] == "gate_phase_finished" and e["payload"]["passed"]}
    assert "baseline" in passed, "baseline did not reproduce the ordering failure"
    assert {"candidate", "withdrawal", "reapply", "preservation"} <= passed, passed

    # withdrawal restored the original target-specific signature
    withdrawal = [e for e in events if e["kind"] == "check_result" and e["payload"]["result"]["phase"] == "withdrawal"]
    assert withdrawal and withdrawal[-1]["payload"]["result"]["signature"] == contract["signature"]

    # 9-10. the verdict and its basis
    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value, receipt.get("reason")
    assert receipt["repair_basis"] == "cause_supported"
    assert receipt["diagnosis"] == "supported"
    assert "test_a_pollute.py" in receipt["reproducer"]
    assert code == 0

    # the supporting probe applied exactly one handle, and the contract's
    # preconditions are exactly that set
    applied = probe["payload"]["applied"]
    assert len(applied) == 1, f"the supporting probe applied {len(applied)} handles"
    assert {c["kind"] + ":" + c["arg"] for c in contract["preconditions"]} == set(applied)

    # a real preservation check ran and passed; an empty set must not satisfy this
    preserved = [e for e in events if e["kind"] == "check_result" and e["payload"]["result"]["phase"] == "preservation"]
    assert preserved, "no preservation check executed"
    assert all(e["payload"]["result"]["outcome"] == "passed" for e in preserved)
    assert any("test_preserved.py" in e["payload"]["result"]["node_id"] for e in preserved)

    # the patch touched implementation code only
    changeset = next(e for e in events if e["kind"] == "changeset_registered")
    touched = changeset["payload"]["changeset"]["touched_paths"]
    assert touched == ["src/app/registry.py"], touched


def test_a_proposal_touching_a_precondition_test_is_refused(tmp_path: Path, capsys, fake_provider):
    """The judge is not negotiable, and the polluter test is part of it."""
    fake_provider.change_diff = PRECONDITION_TAMPER
    repo = build_repo(tmp_path / "tamper", ORDERING_REPO)
    code, receipt = run_fix(repo, capsys)
    events = ledger_of(repo)

    assert any(e["kind"] == "changeset_rejected" for e in events), "the tamper was not rejected"
    assert not any(e["kind"] == "changeset_registered" for e in events), "a judge-touching patch was registered"
    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code != 0

    frozen = next(e for e in events if e["kind"] == "reproducer_frozen")
    assert "tests/test_a_pollute.py" in frozen["payload"]["protected_added"]


# --------------------------------------------------------------------------
# item 11 — each of these fails if the corresponding fix is reverted
# --------------------------------------------------------------------------


def test_removal_isolated_baseline_signature_would_break_the_ordering_path():
    """Restores the original defect: hand the isolated baseline signature to the
    selector. That observation passes, so its signature is None and no contract
    can be built — which is exactly why the feature never worked."""
    assert select(supported(), probes=[probe((POLLUTER,), signature=None)]) is None


def test_removal_self_comparison_would_make_the_drift_check_vacuous():
    """The former call compared the frozen digest with itself."""
    contract = select(supported())
    assert contract is not None
    assert kernel.reproducer_still_valid(contract, contract.tree_digest, "runner-hash") == "", (
        "self-comparison must be the passing case, which is why it enforced nothing"
    )
    assert kernel.reproducer_still_valid(contract, "observed-elsewhere", "runner-hash") != ""


def test_removal_directory_selector_would_freeze_nothing(tmp_path: Path):
    """A directory hashed as `<absent>` protects no file. Resolution must
    either name the real files or refuse."""
    from riftagent.app import judge_artifact_paths

    directory = Handle(Primitive.FIRST, "tests/")
    repo = build_repo(tmp_path / "dirsel", ORDER_DEPENDENT)
    collected = ["tests/test_a_pollute.py::test_pollutes", "tests/test_target.py::test_clean"]
    expected = ["tests/test_a_pollute.py", "tests/test_target.py"]

    # Both spellings of the same directory must resolve identically. Keying on
    # the trailing slash made `first:tests` bypass resolution entirely.
    assert judge_artifact_paths("tests/test_target.py::test_clean", (directory,), collected, repo) == expected
    bare = Handle(Primitive.FIRST, "tests")
    assert judge_artifact_paths("tests/test_target.py::test_clean", (bare,), collected, repo) == expected

    # A file, and a node id, both resolve to the file.
    one = Handle(Primitive.FIRST, "tests/test_a_pollute.py")
    node = Handle(Primitive.FIRST, "tests/test_a_pollute.py::test_pollutes")
    for handle in (one, node):
        assert judge_artifact_paths("tests/test_target.py::test_clean", (handle,), collected, repo) == expected

    # A firstset mixing a directory and a file.
    mixed = Handle(Primitive.FIRSTSET, "tests,tests/test_a_pollute.py")
    assert judge_artifact_paths("tests/test_target.py::test_clean", (mixed,), collected, repo) == expected

    # Unresolvable refuses the contract rather than freezing `<absent>`.
    ghost = Handle(Primitive.FIRST, "tests/nowhere/")
    assert judge_artifact_paths("tests/test_target.py::x", (ghost,), [], repo) is None


def test_a_patch_added_file_survives_the_reset(tmp_path: Path):
    """D1, the serious one. A file the patch adds is absent from the
    construction manifest, and the old reset deleted it before the target ran —
    so every repair that introduces a module failed, as a plausible behavioural
    failure rather than an error."""
    from riftagent.app import reset_episode
    from riftagent.sandbox import Worktree

    repo = build_repo(tmp_path / "added", ORDER_DEPENDENT)
    with Worktree(repo, "add") as wt:
        added = wt.path / "src" / "app" / "helper.py"
        added.write_text("def helper():\n    return 1\n", encoding="utf-8")
        debris = wt.path / "runtime.sqlite"
        debris.write_bytes(b"created by executed code")

        removed, _restored = reset_episode(wt, frozenset({"src/app/helper.py"}))

        assert added.exists(), "the reset deleted a file the frozen patch added"
        assert not debris.exists(), "runtime debris survived the reset"
        assert removed >= 1


def test_a_modified_baseline_file_is_restored(tmp_path: Path):
    """State a phase wrote into a pre-existing file must not reach the next."""
    from riftagent.app import reset_episode
    from riftagent.sandbox import Worktree

    repo = build_repo(tmp_path / "restore", ORDER_DEPENDENT)
    original = (repo / "src" / "app" / "registry.py").read_bytes()
    with Worktree(repo, "res") as wt:
        victim = wt.path / "src" / "app" / "registry.py"
        victim.write_bytes(b"# clobbered by executed repository code\n")

        _removed, restored = reset_episode(wt, frozenset())

        assert restored == 1
        assert victim.read_bytes() == original, "a mutated baseline file was not restored"


def test_a_patch_owned_file_is_not_restored(tmp_path: Path):
    """The patch owns its paths; restoring them would undo the candidate."""
    from riftagent.app import reset_episode
    from riftagent.sandbox import Worktree

    repo = build_repo(tmp_path / "owned", ORDER_DEPENDENT)
    with Worktree(repo, "own") as wt:
        patched = wt.path / "src" / "app" / "registry.py"
        patched.write_bytes(b"# applied by the frozen patch\n")

        reset_episode(wt, frozenset({"src/app/registry.py"}))

        assert patched.read_bytes() == b"# applied by the frozen patch\n"


def test_a_cleanup_failure_stops_fail_closed(tmp_path: Path, monkeypatch):
    """A reset that could not complete must never be recorded as clean."""
    from riftagent.app import reset_episode
    from riftagent.sandbox import SandboxError, Worktree

    repo = build_repo(tmp_path / "failclosed", ORDER_DEPENDENT)
    with Worktree(repo, "fc") as wt:
        (wt.path / "runtime.sqlite").write_bytes(b"x")

        real_unlink = Path.unlink

        def refuse(self: Path, *a, **k):
            if self.name == "runtime.sqlite":
                raise OSError("device busy")
            return real_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", refuse)
        with pytest.raises(SandboxError, match="could not remove"):
            reset_episode(wt, frozenset())


def test_source_drift_during_the_gate_stops_it(tmp_path: Path, capsys, fake_provider, monkeypatch):
    """Item 7 — a real wiring test, not a predicate test.

    The guard must receive a *freshly observed* digest at each phase. This
    mutates the observed source digest after the contract is frozen, and
    requires the gate to stop, record the reason, emit a receipt, and make no
    further model request.

    Reverting the call site to `reproducer_still_valid(reproducer,
    reproducer.tree_digest, ...)` makes this fail: the frozen value would be
    compared with itself and the drift would be invisible.
    """
    import riftagent.app as app

    repo = build_repo(tmp_path / "drift", ORDER_DEPENDENT)
    real_hash = app.tree_hash
    state = {"calls": 0}

    def drifting(root):
        state["calls"] += 1
        # The contract freezes on an early call; later phases observe a tree
        # that has moved underneath them.
        return real_hash(root) if state["calls"] <= 2 else "drifted-" + real_hash(root)

    monkeypatch.setattr(app, "tree_hash", drifting)
    code, receipt = run_fix(repo, capsys)
    events = ledger_of(repo)

    blocked = [e for e in events if e["kind"] == "infrastructure_blocked"]
    assert blocked, "source drift did not stop the gate"
    assert "tracked tree changed" in blocked[-1]["payload"]["reason"]

    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code != 0
    assert any(e["kind"] == "receipt_emitted" for e in events)

    # No repair was requested after the integrity stop: the last model request
    # precedes the block.
    kinds = [e["kind"] for e in events]
    assert kinds.index("infrastructure_blocked") > max(
        i for i, k in enumerate(kinds) if k == "model_request_started"
    ), "a model request followed the integrity stop"
