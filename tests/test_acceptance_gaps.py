"""The M1 acceptance rows the per-row walk found without dedicated evidence.

One file, one test per row, each named for its row.

M1-F15 and M1-F16 were originally in this file as *unreachable* — the
observational branch was correct and no run could enter it. That was missing
runtime capability, not a missing test. The assertion-observation path now
exists, and those two rows are exercised end to end in
`test_observational_finding.py`. What remains here is the part that is still
true: an assertion is never an *intervention*, so it can never support a gated
cause.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from riftagent import kernel
from riftagent.checks import compile_handles
from riftagent.llm import ModelResponseInvalid, validate_handles
from riftagent.records import (
    FAIL,
    PASS,
    Check,
    CheckResult,
    ClaimType,
    Diagnosis,
    GatePhase,
    GateStatus,
    Handle,
    Outcome,
    Primitive,
    RunnerKind,
    Signature,
    Support,
    ValidationError,
    Verdict,
)
from riftagent.sandbox import Worktree
from tests.conftest import ORDER_TARGET, build_repo

IS_WINDOWS = sys.platform.startswith("win")


# ==========================================================================
# M1-F02 — a wrong-signature failure cannot satisfy baseline
# ==========================================================================


def _check(predicted: Signature | None) -> Check:
    return Check(
        check_id="c1",
        claim_type=ClaimType.CHANGE,
        runner=RunnerKind.PYTEST,
        node_id="tests/test_target.py::test_x",
        expected_baseline=Outcome.FAILED,
        expected_candidate=Outcome.PASSED,
        timeout_s=60.0,
        scope="one change check",
        predicted_signature=predicted,
    )


def _result(signature: Signature | None, outcome: Outcome = Outcome.FAILED) -> CheckResult:
    return CheckResult(
        check_id="c1",
        node_id="tests/test_target.py::test_x",
        phase=GatePhase.BASELINE,
        outcome=outcome,
        signature=signature,
        duration_s=0.1,
        exit_code=1,
    )


def test_f02_a_failure_with_the_wrong_signature_cannot_satisfy_baseline():
    """The row's branch, with a positive control on both sides of it.

    Asserted at the kernel boundary rather than through the CLI because no CLI
    path builds a predicted signature today: `build_checkset` leaves it None for
    `fix` and `verify`, and only an M2 `propose_spec` supplies one. That is a
    limitation of the fixture surface, not of the rule, and it is stated rather
    than hidden by an end-to-end test that would not reach the branch.
    """
    predicted = Signature(exception_type="AssertionError", message="assert 10 == 11")
    other = Signature(exception_type="TypeError", message="unsupported operand type(s)")

    wrong = kernel.decide_baseline(_check(predicted), _result(other))
    assert not wrong.passed
    assert "different reason than predicted" in wrong.reason
    assert "TypeError" in wrong.reason and "AssertionError" in wrong.reason

    # Control 1: the same failure, matching the prediction, is accepted. Without
    # this the assertion above would also hold if the branch rejected every
    # baseline.
    right = kernel.decide_baseline(_check(predicted), _result(predicted))
    assert right.passed, right.reason

    # Control 2: a passing target is refused for a different, stated reason, so
    # the two refusals are not one branch wearing two messages.
    passing = kernel.decide_baseline(_check(predicted), _result(None, Outcome.PASSED))
    assert not passing.passed
    assert "already passes" in passing.reason

    # Control 3: with no prediction there is nothing to mismatch, so any
    # observed failure establishes reproduction.
    assert kernel.decide_baseline(_check(None), _result(other)).passed


# ==========================================================================
# M1-F05 — disagreement-per-cost selects the highest-value probe, deterministically
# ==========================================================================


def _evidence_with_one_run() -> tuple[kernel.Evidence, list[str], dict[str, Handle]]:
    handles = [Handle(Primitive.ENV, "TOKEN"), Handle(Primitive.FIRST, "tests/test_a.py")]
    mapping, roles = kernel.role_map(handles)
    ev = kernel.Evidence()
    ev.record((), 1, FAIL, interventional=False)
    return ev, roles, mapping


def test_f05_disagreement_per_cost_is_deterministic_and_prefers_the_informative_probe():
    ev, roles, _ = _evidence_with_one_run()
    probes = kernel.generate_probes(roles)
    scored = [kernel.score(h, ev) for h in kernel.code_grammar(roles)]
    live = [s for s in scored if s.status != "contradicted"]
    assert len(live) > 1, "the fixture must leave real ambiguity or selection decides nothing"

    import random

    # 1. deterministic: the same inputs choose the same probe every time, and
    #    the rng is not consulted by this policy.
    picks = {kernel.select_probe("disagreement", probes, live, ev, random.Random(seed)).name for seed in range(8)}
    assert len(picks) == 1, f"disagreement selection is not deterministic: {picks}"
    chosen = kernel.select_probe("disagreement", probes, live, ev, random.Random(0))

    # 2. it chose a probe the surviving theories actually disagree about. A
    #    probe every live theory predicts identically eliminates nothing.
    predictions = {kernel.predict_probe(s, chosen, ev) for s in live}
    assert len(predictions) > 1, f"{chosen.name} is predicted identically by every live theory"

    # 3. and it is not merely the cheapest — that is a different policy, and the
    #    two must be distinguishable or the benchmark's B-versus-C arms measure
    #    nothing.
    cheapest = kernel.select_probe("cheapest", probes, live, ev, random.Random(0))
    assert cheapest.est_cost <= chosen.est_cost
    assert {kernel.predict_probe(s, cheapest, ev) for s in live} != predictions or cheapest.name != chosen.name

    # 4. every policy is offered the identical probe list, which is what makes
    #    the comparison a test of selection rather than of availability.
    assert kernel.generate_probes(roles) == probes


# ==========================================================================
# M1-F07 / M1-X02 — a novel executable primitive, and executable keys, are refused
# ==========================================================================


@pytest.mark.parametrize(
    "kind",
    ["exec", "shell", "run", "python", "download", "curl", "sudo", "eval"],
)
def test_f07_a_novel_executable_primitive_is_refused(kind: str):
    with pytest.raises((ValidationError, ModelResponseInvalid)):
        validate_handles({"handles": [{"kind": kind, "arg": "anything"}]}, [])


@pytest.mark.parametrize("banned", ["command", "argv", "script", "code", "shell", "run", "exec"])
def test_x02_a_handle_carrying_an_executable_key_is_refused(banned: str):
    """The row's other half: a handle that smuggles a command alongside a legal
    primitive. The kind is valid here, so only the key check can reject it."""
    raw = {"handles": [{"kind": "env", "arg": "TOKEN", banned: "rm -rf /"}]}
    with pytest.raises((ValidationError, ModelResponseInvalid)) as excinfo:
        validate_handles(raw, [])
    assert banned in str(excinfo.value)


def test_x02_the_approved_primitives_are_still_accepted():
    """The positive control. Without it every assertion above would also hold if
    `validate_handles` rejected everything."""
    accepted = validate_handles(
        {"handles": [{"kind": "env", "arg": "TOKEN"}, {"kind": "first", "arg": "tests/test_a.py"}]},
        [],
    )
    assert [h.label for h in accepted] == ["env:TOKEN", "first:tests/test_a.py"]


@pytest.mark.parametrize("escape", ["/etc/passwd", "../outside", "a;rm -rf /", "$(whoami)", "a`b`"])
def test_x02_a_handle_argument_cannot_carry_a_path_escape_or_shell_metacharacter(escape: str):
    with pytest.raises((ValidationError, ModelResponseInvalid)):
        validate_handles({"handles": [{"kind": "clear", "arg": escape}]}, [])


# ==========================================================================
# M1-F09 — `why` proposes no patch, applies none, and runs no gate
# ==========================================================================


def test_f09_a_why_ledger_contains_no_patch_and_no_gate_phase(tmp_path: Path, capsys):
    """The row asks for a call or ledger assertion. A receipt field saying
    `gate: not_applicable` is a claim; the absence of the events is evidence."""
    from riftagent.app import main

    repo = build_repo(tmp_path / "why-no-gate", {**_ORDER_FILES})
    code = main(["--repo", str(repo), "--json", "why", ORDER_TARGET, "--allow-partial-sandbox"])
    capsys.readouterr()
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    kinds = [json.loads(x)["kind"] for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]

    for forbidden in ("changeset_registered", "changeset_rejected", "gate_phase_finished", "signature_frozen"):
        assert forbidden not in kinds, f"`why` emitted {forbidden}"
    assert "model_request_started" not in kinds, "`why` made a model request with no provider configured"
    # The positive control: the ledger is not empty of the things `why` *does*
    # do, so the absences above are meaningful.
    assert "diagnosis_emitted" in kinds and "handles_discovered" in kinds and "probe_selected" in kinds
    assert not (td / "change-set.diff").exists()
    assert code in (0, 3, 4, 5)


_ORDER_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/_impl.py": "VALUE = 42\n",
    "src/pkg/api.py": "import pkg\n\n\ndef get():\n    return pkg._impl.VALUE\n",
    "tests/test_helper.py": "import pkg._impl\n\n\ndef test_helper():\n    assert pkg._impl.VALUE == 42\n",
    "tests/test_target.py": "from pkg.api import get\n\n\ndef test_target():\n    assert get() == 42\n",
}


# ==========================================================================
# M1-F15 / M1-F16 — the observational branch is unreachable, not untested
# ==========================================================================


def test_f15_the_observational_rule_is_correct_when_it_is_fed():
    """Half of the row: given an assertion-only supported cause, the kernel
    classifies it observationally and refuses it a gate."""
    handles = [Handle(Primitive.DEP_ASSERT, "yaml")]
    mapping, roles = kernel.role_map(handles)
    hypothesis = {
        "hypothesis_id": "assert_only",
        "roles": roles,
        "target_role": "rT",
        "latents": [
            {"name": "z", "type": "bool", "init": False, "set_on": {"event": "applied", "role": "r0"}, "reset_on": None}
        ],
        "condition": {"var": "z"},
    }
    ev = kernel.Evidence()
    ev.record((), 1, FAIL, interventional=False)
    scored = kernel.Scored(hypothesis, "supported", 0.1, 3, 4, ((1, FAIL),))

    diagnosis = kernel.derive_diagnosis([scored], kernel.generate_probes(roles), ev, mapping, [])
    assert diagnosis.status is Verdict.DIAGNOSIS_SUPPORTED
    assert diagnosis.support is Support.OBSERVATIONAL
    assert diagnosis.gate is GateStatus.NOT_APPLICABLE
    assert [c.label for c in diagnosis.causes] == ["dep_assert:yaml"]
    assert diagnosis.remediation_unverified.startswith("UNVERIFIED:")

    # Positive control: the identical shape over an *intervention* handle is
    # interventional, so the classification is reading the primitive rather than
    # always saying observational.
    inter_map, inter_roles = kernel.role_map([Handle(Primitive.ENV, "TOKEN")])
    inter = kernel.Scored(dict(hypothesis, roles=inter_roles), "supported", 0.1, 3, 4, ((1, FAIL),))
    control = kernel.derive_diagnosis([inter], kernel.generate_probes(inter_roles), kernel.Evidence(), inter_map, [])
    assert control.support is Support.INTERVENTIONAL


# `test_f15_no_runtime_path_can_ever_reach_that_rule` lived here and asserted
# that `discover_handles` never yields an assertion primitive. That was true, and
# it was the defect: it is why the observational verdict could not be produced.
# The assertion-observation path now exists, so the test is removed rather than
# inverted, and the bounded-discovery rule it should have been is asserted in
# `test_observational_finding.py::test_f15_assertions_are_discovered_only_from_explicit_evidence`.


def test_f15_no_theory_over_an_assertion_role_can_be_supported():
    """The inference the two mechanical facts above license, asserted directly.

    An assertion compiles to nothing, so every probe that "applies" one observes
    exactly what applying nothing observes. That is the only trace the runtime
    can produce, and against it no theory whose condition depends on the
    assertion role survives.
    """
    mapping, roles = kernel.role_map([Handle(Primitive.DEP_ASSERT, "yaml")])
    ev = kernel.Evidence()
    for applied in ((), ("r0",), (), ("r0",)):
        # Identical outcomes, because applying the assertion changed nothing.
        ev.record(applied, 1, FAIL)
        ev.new_episode()

    scored = [kernel.score(h, ev) for h in kernel.code_grammar(roles)]

    # Individual theories *can* score `supported` off this trace while naming
    # the assertion role: `z0 and not n0`, with both latents set by the same
    # role, is behaviourally constant-False yet mentions r0, so `cause_of`
    # returns the assertion. What keeps that out of the diagnosis is the
    # description-length tiebreak — the behaviourally identical `const False` is
    # simpler and wins `min(j, dl)`. That tiebreak is therefore load-bearing for
    # this row, not cosmetic, and it is asserted rather than assumed.
    supported = [s for s in scored if s.status == "supported"]
    naming = [s for s in supported if kernel.cause_of(s.hypothesis, mapping)]
    assert naming, "the fixture no longer exercises the case the tiebreak protects against"
    best = min([s for s in scored if s.status != "contradicted"], key=lambda s: (s.j, s.dl))
    assert best.hypothesis["condition"].get("const") is False, best.hypothesis["condition"]
    assert all(best.dl <= s.dl for s in naming), "a role-naming theory is now at least as simple as `const False`"

    diagnosis = kernel.derive_diagnosis(scored, kernel.generate_probes(roles), ev, mapping, [])
    assert diagnosis.support is not Support.OBSERVATIONAL
    assert not [c for c in diagnosis.causes if not c.is_intervention]

    # Positive control on the trace, not on the verdict: the identical shape
    # over a role whose application genuinely flips the outcome contradicts
    # `const False` outright, so the simplest surviving theory is no longer the
    # "nothing here helps" one. That is what distinguishes a no-op trace from a
    # real intervention, and without it the assertions above would also hold for
    # a kernel that always answered `representation_inadequate`.
    inter_map, inter_roles = kernel.role_map([Handle(Primitive.ENV, "TOKEN")])
    ev2 = kernel.Evidence()
    for applied, outcome in (((), FAIL), (("r0",), PASS), ((), FAIL), (("r0",), PASS)):
        ev2.record(applied, 1, outcome)
        ev2.new_episode()
    inter_scored = [kernel.score(h, ev2) for h in kernel.code_grammar(inter_roles)]
    inter_live = [s for s in inter_scored if s.status != "contradicted"]
    assert inter_live, "the control trace contradicted every theory"
    inter_best = min(inter_live, key=lambda s: (s.j, s.dl))
    assert inter_best.hypothesis["condition"].get("const") is not False, (
        "an intervention that flips the outcome still selects 'nothing here helps'"
    )
    control = kernel.derive_diagnosis(inter_scored, kernel.generate_probes(inter_roles), ev2, inter_map, [])
    assert control.status is not Verdict.REPRESENTATION_INADEQUATE, control.status.value
    assert not [c for c in control.causes if not c.is_intervention]


def test_f15_an_assertion_handle_compiles_to_exactly_nothing(tmp_path: Path):
    """And the deeper reason: even supplied by a model, an assertion is a no-op,
    so no probe can produce evidence that an assertion changed the outcome, and
    no theory over an assertion role can ever be supported."""
    repo = build_repo(tmp_path / "noop", {"src/pkg/__init__.py": "", "tests/test_a.py": "def test_a():\n    pass\n"})
    wt = Worktree(repo, "noop")
    try:
        nothing = compile_handles((), wt)
        for assertion in (Handle(Primitive.DEP_ASSERT, "yaml"), Handle(Primitive.FILE_ASSERT, "config/settings.ini")):
            assert compile_handles((assertion,), wt) == nothing, f"{assertion.label} is not a no-op"
        # Positive control: an intervention handle does compile to something,
        # so the equality above is a property of assertions, not of the function.
        assert compile_handles((Handle(Primitive.ENV, "TOKEN"),), wt) != nothing
    finally:
        wt.dispose()


def test_f16_fix_stops_before_patch_generation_on_an_observational_diagnosis(tmp_path: Path, capsys, monkeypatch):
    """M1-F16 and DAR-002, exercised at the only seam that can reach them.

    `run_diagnosis` is substituted with one that returns an observational
    diagnosis, because no repository can produce one (see the tests above). The
    branch under test is the real one in `cmd_fix`.
    """
    from riftagent import app
    from riftagent.app import main

    observational = Diagnosis(
        Verdict.DIAGNOSIS_SUPPORTED,
        Support.OBSERVATIONAL,
        GateStatus.NOT_APPLICABLE,
        (Handle(Primitive.DEP_ASSERT, "yaml"),),
        1,
        (),
        ("an executable assertion supports this finding",),
        remediation_unverified="UNVERIFIED: remediation for dep_assert:yaml was not applied.",
    )
    monkeypatch.setattr(app, "run_diagnosis", lambda *a, **k: observational)

    repo = build_repo(tmp_path / "observational", {**_ORDER_FILES})
    code = main(["--repo", str(repo), "--json", "fix", ORDER_TARGET, "--allow-partial-sandbox", "--max-usd", "1.00"])
    receipt = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    events = [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = [e["kind"] for e in events]

    # no patch was requested, produced, or gated
    assert "model_request_started" not in kinds, "a request was spent on a diagnosis no gate could verify"
    assert "changeset_registered" not in kinds
    assert not (td / "change-set.diff").exists()
    # and the receipt records that, rather than leaving the reader to infer it
    stop = [e["payload"] for e in events if e["kind"] == "gate_phase_finished"]
    assert len(stop) == 1 and not stop[0]["passed"]
    assert "no patch was generated and none was gated" in stop[0]["reason"]
    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code != 0


# ==========================================================================
# M1-R09 — an interrupted model request is never automatically repeated
# ==========================================================================


def test_r09_a_resumed_task_repeats_no_interrupted_model_request(tmp_path: Path, capsys, monkeypatch):
    """The design's rule: a `model_request_started` with no durable response
    means the outcome and the cost are unknown, so resume may not simply try
    again."""
    from riftagent import app, llm
    from riftagent.app import main

    repo = build_repo(tmp_path / "interrupted", {**_ORDER_FILES})
    monkeypatch.setenv("RIFT_LLM_URL", "http://127.0.0.1:1/v1/chat/completions")
    monkeypatch.setenv("RIFT_LLM_KEY", "sk-SENTINEL-fake")
    monkeypatch.setenv("RIFT_LLM_MODEL", "fake")

    # Interrupt the run after the request has been recorded as started.
    def die(*a: Any, **k: Any):
        raise KeyboardInterrupt("interrupted while the request was in flight")

    monkeypatch.setattr(llm, "post_chat", die)
    # `main` converts the interrupt into an exit code and leaves the ledger
    # complete up to the last durable event, which is the behaviour being relied
    # on here rather than an exception escaping.
    interrupted = main(
        ["--repo", str(repo), "--json", "fix", ORDER_TARGET, "--allow-partial-sandbox", "--max-usd", "1.00"]
    )
    assert interrupted != 0
    capsys.readouterr()

    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    before = [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    started = [e for e in before if e["kind"] == "model_request_started"]
    settled = [e for e in before if e["kind"] == "spend_settled"]
    assert started, "the fixture did not reach a started request"
    assert len(settled) < len(started), "the fixture is not actually an interrupted request"

    # Resume must not re-issue it. A call that reaches the adapter at all fails
    # the test, whatever it would have returned.
    calls: list[Any] = []

    def spy(*a: Any, **k: Any):
        calls.append(a)
        raise AssertionError("resume repeated an interrupted model request")

    monkeypatch.setattr(llm, "post_chat", spy)
    monkeypatch.setattr(app, "run_diagnosis", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-diagnosed")))
    main(["--repo", str(repo), "--json", "resume", td.name])
    out = capsys.readouterr().out
    assert not calls, "resume repeated an interrupted model request"

    after = [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e for e in after if e["kind"] == "model_request_started"] == started, "a new request was started"
    assert out.strip(), "resume reported nothing about the interrupted task"


# ==========================================================================
# M1-F06 — all hypotheses contradicted triggers one bounded propose_handles call
# ==========================================================================


class _HandlesFake(http.server.BaseHTTPRequestHandler):
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))).decode())
        type(self).seen.append(body)
        system = body["messages"][0]["content"].lower()
        if "propose measurements" in system:
            content = json.dumps({"handles": [{"kind": "env", "arg": "RIFT_TEST_WIDENED"}]})
        elif "closed intermediate representation" in system:
            content = json.dumps({"hypotheses": []})
        else:
            content = json.dumps({"diff": "", "summary": "decides nothing"})
        payload = json.dumps(
            {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "model": "fake",
                "usage": {"prompt_tokens": 110, "completion_tokens": 20},
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
def handles_provider(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _HandlesFake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _HandlesFake.seen = []
    monkeypatch.setenv("RIFT_LLM_URL", f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
    monkeypatch.setenv("RIFT_LLM_KEY", "sk-SENTINEL-fake-for-tests")
    monkeypatch.setenv("RIFT_LLM_MODEL", "fake")
    try:
        yield _HandlesFake
    finally:
        server.shutdown()
        server.server_close()


def _one_theory(monkeypatch) -> None:
    """Narrow the theory space to `const True`, which the first observed failure
    contradicts outright. The trigger, widening, rebuild and re-scoring under
    test are all the production ones."""
    from riftagent import kernel as k

    monkeypatch.setattr(k, "code_grammar", lambda roles, max_conj=2: [k._hyp(0, roles, [], {"const": True})])


def test_f06_all_contradicted_triggers_one_bounded_handles_request(tmp_path, capsys, monkeypatch, handles_provider):
    """The row's trigger, which the runtime previously did not implement.

    `propose_handles` used to be issued once per task before any probing. That
    is bounded, but it is not the signal the design names. It now fires on the
    representation-inadequate signal: every theory contradicted, or no handle
    discovered at all.
    """
    from riftagent.app import main

    _one_theory(monkeypatch)
    repo = build_repo(tmp_path / "widen", _ORDER_FILES)
    code = main(["--repo", str(repo), "--json", "why", ORDER_TARGET, "--allow-partial-sandbox", "--max-usd", "1.00"])
    receipt = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    events = [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]

    # 1. exactly one propose_handles request
    started = [e["payload"] for e in events if e["kind"] == "model_request_started"]
    assert [p["operation"] for p in started].count("propose_handles") == 1, [p["operation"] for p in started]

    # 2. triggered by the contradiction rather than issued up front. The
    #    discriminator is ordering against the evidence that did the
    #    contradicting: the deterministic handles come first, then the target is
    #    observed and the theory space is scored, and only then is the request
    #    made. Before this change there was one handles event and it always
    #    preceded `hypotheses_proposed`.
    kinds = [e["kind"] for e in events]
    handles_at = [i for i, kind in enumerate(kinds) if kind == "handles_discovered"]
    assert len(handles_at) == 2, kinds
    proposed_at = kinds.index("hypotheses_proposed")
    observed_at = kinds.index("check_result")
    assert handles_at[0] < proposed_at, "the deterministic handles were not discovered first"
    assert handles_at[1] > proposed_at, "the widening did not follow the theory space it responds to"
    assert handles_at[1] > observed_at, "the widening did not follow the observation that contradicted everything"

    # 3. the widened set is the deterministic one plus the model's handle, in
    #    that order, so r0..rN still name the handles every recorded observation
    #    was made against
    before = [h["kind"] + ":" + h["arg"] for h in events[handles_at[0]]["payload"]["handles"]]
    after = [h["kind"] + ":" + h["arg"] for h in events[handles_at[1]]["payload"]["handles"]]
    assert after[: len(before)] == before, (before, after)
    assert after[len(before) :] == ["env:RIFT_TEST_WIDENED"]
    assert "after every enumerated theory was contradicted" in events[handles_at[1]]["payload"]["origin"]

    # 4. and it stays honest: still contradicted after widening is
    #    `representation_inadequate`, which attributes nothing to the repository
    assert receipt["verdict"] == Verdict.REPRESENTATION_INADEQUATE.value, receipt["verdict"]
    assert any("widened once" in n for n in receipt["diagnosis"]["notes"]), receipt["diagnosis"]["notes"]
    assert code != 0


def test_f06_the_receipt_sums_every_request_not_just_the_last(tmp_path, capsys, monkeypatch, handles_provider):
    """The multi-request half of the spend sum, relocated here from
    `test_fix_and_spend.py` because this is where more than one request occurs."""
    from riftagent.app import main
    from riftagent.records import spend_ledger_path

    _one_theory(monkeypatch)
    repo = build_repo(tmp_path / "sum", _ORDER_FILES)
    main(["--repo", str(repo), "--json", "why", ORDER_TARGET, "--allow-partial-sandbox", "--max-usd", "1.00"])
    receipt = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    rows = [json.loads(x) for x in spend_ledger_path(repo).read_text(encoding="utf-8").splitlines()]
    settled = [r for r in rows if r["kind"] == "settled"]
    assert len(settled) > 1, "the fixture no longer makes more than one request"
    total = sum(r["charged_usd"] for r in settled)
    assert receipt["spend"]["charged_usd"] == pytest.approx(total)
    assert total > max(r["charged_usd"] for r in settled), "the receipt reported one request rather than the sum"


# ==========================================================================
# M1-R07 — an interrupt kills the child process tree
# ==========================================================================


CHILD = (
    "import os, subprocess, sys, time\n"
    "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "open(sys.argv[1], 'w').write(str(os.getpgid(0)) + ' ' + str(kid.pid))\n"
    "time.sleep(120)\n"
)


def _running(pid: int) -> bool:
    """Is this pid a *running* process?

    Not `os.kill(pid, 0)`, and not process-group membership. A process that has
    been killed but not yet reaped is a zombie: it still answers signal 0 and
    still counts as a member of its group, while being exactly as dead as the
    row requires. The first version of this test asserted group membership and
    was therefore flaky — it passed in isolation, where the zombie was reaped
    promptly, and failed under full-suite load, where it was not. `/proc`
    distinguishes the two: a zombie reports state `Z`.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        return stat.rsplit(")", 1)[1].split()[0] not in ("Z", "X", "x")
    except IndexError:
        return False


@pytest.mark.skipif(
    IS_WINDOWS or not Path("/proc").is_dir(),
    reason="NOT_RUN_PROCFS_UNAVAILABLE: liveness is read from /proc; Windows uses a tested Job Object (M1-X05)",
)
def test_r07_an_interrupt_kills_the_child_process_tree(tmp_path, monkeypatch):
    """The half of the row that had neither a test nor an implementation.

    `run_argv` killed the tree only on `TimeoutExpired`. The child is started in
    its own session precisely so a stray signal cannot reach it — which also
    means a terminal's Ctrl-C never does, so an interrupt left the whole tree
    running. The interrupt is raised where a real one lands: inside the blocking
    `communicate` call.
    """
    import subprocess as sp

    from riftagent import sandbox
    from riftagent.sandbox import Worktree, build_env, probe_isolation

    pidfile = tmp_path / "pgid.txt"
    state: dict[str, Any] = {}

    class Interrupting(sp.Popen):
        def communicate(self, *a: Any, **k: Any):
            # Exactly one interrupt. `subprocess` is shared, so every later
            # caller — the worktree's own cleanup included — must be left alone,
            # or the simulated Ctrl-C escapes the code under test.
            if state.get("fired"):
                return super().communicate(*a, **k)
            state["fired"] = True
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not pidfile.exists():
                time.sleep(0.05)
            assert pidfile.exists(), "the child never started"
            pgid, grandchild = (int(x) for x in pidfile.read_text(encoding="utf-8").split())
            state["pgid"] = pgid
            state["grandchild"] = grandchild
            # The control: the child and its descendant are both running here.
            # Without it the assertions after the interrupt would pass against
            # pids that never ran.
            assert _running(self.pid) and _running(grandchild), "the tree was not running before the interrupt"
            state["alive_before"] = True
            raise KeyboardInterrupt("simulated Ctrl-C while the child was running")

    repo = build_repo(tmp_path / "tree", {"tests/test_a.py": "def test_a():\n    pass\n"})
    wt = Worktree(repo, "interrupt")
    try:
        argv = [sys.executable, "-c", CHILD, str(pidfile)]
        env = build_env(wt.path, wt.tmpdir, {})
        # Patched only now: `subprocess` is the module every caller shares, so
        # patching it earlier would have interrupted the fixture's own git calls
        # rather than the process under test.
        monkeypatch.setattr(sandbox.subprocess, "Popen", Interrupting)
        with pytest.raises(KeyboardInterrupt):
            sandbox.run_argv(argv, wt.path, env, 120.0, probe_isolation(), False)
    finally:
        wt.dispose()

    assert state.get("alive_before"), "the control never observed a running process tree"
    grandchild = state["grandchild"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not _running(grandchild):
            # The descendant is what the row is about: killing the child alone
            # would leave the grandchild orphaned and still running.
            return
        time.sleep(0.1)
    try:
        os.killpg(state["pgid"], 9)
    except OSError:
        pass
    raise AssertionError("a descendant of the interrupted command survived")
