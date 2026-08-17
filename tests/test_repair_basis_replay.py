"""DAR-001 receipt evidence: both `repair_basis` values, replayed from the ledger.

DAR-001 governs two provenances for one patch. A patch proposed from a located
cause under a frozen reproducer and a patch proposed from a reproducible failure
with no supported diagnosis pass the *identical* gate; the receipt is the only
place the difference is visible. So the evidence that matters is not that the
field is present — it is that its value is a **projection of the ledger**.

Every assertion below therefore re-reads the ledger from disk, reduces it, and
recomputes the block. If `repair_basis` were carried in a variable rather than
derived, the recomputation would differ and these tests would fail.

The two runs must also produce *different* values from the same code path, or
neither assertion discriminates anything. That comparison is the last test here.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from riftagent.app import _repair_basis, render_receipt, render_settled
from riftagent.records import (
    Authorities,
    Budgets,
    Diagnosis,
    GateStatus,
    Handle,
    IsolationLevel,
    Primitive,
    ReproductionContract,
    Signature,
    Support,
    TaskContract,
    TaskProjection,
    Verb,
    Verdict,
    read_events,
    reduce,
)
from tests.conftest import build_repo, make_diff
from tests.test_propose_hypotheses import UNRESOLVED_FILES
from tests.test_reproduction_contract import IMPLEMENTATION_FIX, ORDERING_REPO

CAUSE_SUPPORTED_TARGET = "tests/test_target.py::test_clean"
CAUSE_SUPPORTED_PRESERVE = "tests/test_preserved.py::test_commit_publishes"
UNRESOLVED_TARGET = "tests/test_calc.py::test_total"
UNRESOLVED_PRESERVE = "tests/test_other.py::test_double"


class _Fake(http.server.BaseHTTPRequestHandler):
    change_diff: str = ""

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))).decode())
        system = body["messages"][0]["content"].lower()
        if "propose measurements" in system:
            content = '{"handles": []}'
        elif "closed intermediate representation" in system:
            # No theories are offered here. The unresolved run must reach
            # `propose_change` on the frozen failure alone, which is the whole
            # second basis DAR-001 governs.
            content = '{"hypotheses": []}'
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
def provider(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Fake.change_diff = ""
    monkeypatch.setenv("RIFT_LLM_URL", f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
    monkeypatch.setenv("RIFT_LLM_KEY", "sk-SENTINEL-fake-for-tests")
    monkeypatch.setenv("RIFT_LLM_MODEL", "fake")
    try:
        yield _Fake
    finally:
        server.shutdown()
        server.server_close()


def latest_task(repo: Path) -> Path:
    return sorted((repo / ".rift" / "tasks").iterdir())[-1]


def run_fix(repo: Path, capsys, target: str, preserve: str, extra: list[str] | None = None) -> dict:
    from riftagent.app import main

    main(
        [
            "--repo",
            str(repo),
            "--json",
            "fix",
            target,
            "--allow-partial-sandbox",
            "--preserve",
            preserve,
            "--max-usd",
            "1.00",
            *(extra or []),
        ]
    )
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def assert_replays_byte_identically(repo: Path, receipt: dict) -> dict:
    """The shared replay assertion, run for both bases.

    Returns the block recomputed from the ledger so the caller can compare the
    two runs against each other.
    """
    td = latest_task(repo)
    events, truncated = read_events(td / "ledger.jsonl")
    assert not truncated
    proj = reduce(events)

    # 1. the receipt on disk is the receipt the ledger carries
    on_disk = json.loads((td / "receipt.json").read_text(encoding="utf-8"))
    from_ledger = proj.receipt
    assert from_ledger is not None
    assert on_disk == from_ledger == receipt

    # 2. the block is derived, not carried: recomputing it from a projection
    #    built out of the file must reproduce the same values
    recomputed = _repair_basis(proj)
    assert recomputed, "no repair basis was derived from the ledger"
    for key, value in recomputed.items():
        assert receipt[key] == value, f"{key} is not a projection of the ledger"

    # 3. rendering is a pure projection: transcript and receipt text replay
    #    byte-for-byte from the events alone
    assert render_settled(events) == (td / "transcript.txt").read_text(encoding="utf-8")
    rendered = "\n".join(render_receipt(from_ledger)).lstrip("\n") + "\n"
    assert rendered == (td / "receipt.txt").read_text(encoding="utf-8")

    # 4. and killing the renderer changes nothing: a second reduction of the
    #    same bytes produces identical output
    again, _ = read_events(td / "ledger.jsonl")
    assert render_settled(again) == render_settled(events)
    assert _repair_basis(reduce(again)) == recomputed
    return recomputed


def test_cause_supported_replays_byte_identically(tmp_path: Path, capsys, provider):
    repo = build_repo(tmp_path / "supported", ORDERING_REPO)
    provider.change_diff = IMPLEMENTATION_FIX
    receipt = run_fix(repo, capsys, CAUSE_SUPPORTED_TARGET, CAUSE_SUPPORTED_PRESERVE)

    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value, receipt.get("reason")
    basis = assert_replays_byte_identically(repo, receipt)

    assert basis["repair_basis"] == "cause_supported"
    assert basis["diagnosis"] == "supported"
    # The stronger basis names the frozen reproducer it was gated against, and
    # that hash is the one the REPRODUCER_FROZEN event recorded.
    events, _ = read_events(latest_task(repo) / "ledger.jsonl")
    frozen = [e for e in events if e.kind.value == "reproducer_frozen"]
    assert len(frozen) == 1
    assert basis["reproducer_hash"] == frozen[0].payload["reproducer_hash"]
    assert basis["reproducer"] != "bare target"
    assert "applied and withheld across probes" in basis["claim_scope"]


def test_diagnosis_unresolved_replays_byte_identically(tmp_path: Path, capsys, provider):
    """An unconditional defect: no handle changes the outcome, so no cause is
    located. The repair is proposed from the frozen failure alone and passes the
    identical gate — and the receipt must say so."""
    repo = build_repo(tmp_path / "unresolved", UNRESOLVED_FILES)
    # Generated mechanically from the fixture repository, never hand-written,
    # and restored before the run so the patch applies to the tree the gate sees.
    provider.change_diff = make_diff(repo, {"src/pkg/calc.py": "def total():\n    return 11\n"})
    receipt = run_fix(repo, capsys, UNRESOLVED_TARGET, UNRESOLVED_PRESERVE, ["--max-probes", "16"])

    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value, receipt.get("reason")
    basis = assert_replays_byte_identically(repo, receipt)

    assert basis["repair_basis"] == "diagnosis_unresolved"
    assert basis["diagnosis"] == "unresolved"
    # No cause was located, so no reproducer could be frozen and the gate ran on
    # the bare target. The claim scope must not borrow the stronger standing.
    events, _ = read_events(latest_task(repo) / "ledger.jsonl")
    assert not [e for e in events if e.kind.value == "reproducer_frozen"]
    assert basis["reproducer"] == "bare target"
    assert basis["reproducer_hash"] == ""
    assert "no cause was located" in basis["claim_scope"]
    assert "nothing about why the failure occurred" in basis["claim_scope"]

    # The diagnosis that produced this basis is itself recorded as unresolved,
    # so the receipt field cannot disagree with the evidence behind it.
    diagnosis = [e for e in events if e.kind.value == "diagnosis_emitted"][-1].payload["diagnosis"]
    assert diagnosis["status"] in {Verdict.REPRESENTATION_INADEQUATE.value, Verdict.UNDERDETERMINED.value}
    assert not diagnosis["causes"]


def _fix_projection(diagnosis: Diagnosis, reproducer: ReproductionContract | None) -> TaskProjection:
    proj = TaskProjection()
    proj.contract = TaskContract(
        task_id="t",
        verb=Verb.FIX,
        request="fix x",
        repo_root="/nowhere",
        baseline_tree_hash="",
        scope="",
        budgets=Budgets(),
        requested_sandbox=IsolationLevel.PARTIAL,
        actual_sandbox=IsolationLevel.PARTIAL,
        authorities=Authorities(),
    )
    proj.diagnosis = diagnosis
    proj.reproducer = reproducer
    return proj


def test_a_supported_cause_without_a_frozen_reproducer_may_not_claim_the_stronger_basis():
    """The conjunct no end-to-end fixture reaches.

    `select_reproducer` refuses to issue a contract for a mixed or unmatched
    cause set, and a diagnosis can be supported and interventional while that
    refusal stands. The stronger basis claims the patch "was gated against a
    reproducer frozen from that evidence", so without one it is a claim about an
    experiment the gate never ran.

    This asserts at the projection boundary rather than through the CLI, because
    no fixture in this suite produces that state. Stated as the limitation it
    is: it proves the rule, not that the runtime reaches it.
    """
    cause = Handle(kind=Primitive.FIRST, arg="tests/test_a.py")
    supported = Diagnosis(
        Verdict.DIAGNOSIS_SUPPORTED, Support.INTERVENTIONAL, GateStatus.NOT_APPLICABLE, (cause,), 1, (), ()
    )
    without = _repair_basis(_fix_projection(supported, None))
    assert without["repair_basis"] == "diagnosis_unresolved"
    assert without["reproducer"] == "bare target"

    # Positive control: the only difference is the frozen contract, and it is
    # what moves the receipt to the stronger basis. Without this the assertion
    # above would also hold if the function ignored the diagnosis entirely.
    frozen = ReproductionContract(
        preconditions=(cause,),
        node_id="tests/test_target.py::test_clean",
        signature=Signature(exception_type="AssertionError", message="boom"),
        runner_config_hash="rc",
        tree_digest="td",
        supporting_event_ids=("e1",),
    )
    with_contract = _repair_basis(_fix_projection(supported, frozen))
    assert with_contract["repair_basis"] == "cause_supported"
    assert with_contract["reproducer_hash"] == frozen.content_hash


def test_the_two_bases_differ_on_the_same_code_path(tmp_path: Path, capsys, provider):
    """The positive control for both tests above.

    If `_repair_basis` returned a constant, each test would still pass on its
    own. This one runs both fixtures through the same `fix` command and asserts
    the derived blocks differ in exactly the fields DAR-001 makes load-bearing,
    while the verdict — which the gate decides, not the provenance — is the same.
    """
    supported_repo = build_repo(tmp_path / "a-supported", ORDERING_REPO)
    provider.change_diff = IMPLEMENTATION_FIX
    supported = run_fix(supported_repo, capsys, CAUSE_SUPPORTED_TARGET, CAUSE_SUPPORTED_PRESERVE)

    unresolved_repo = build_repo(tmp_path / "b-unresolved", UNRESOLVED_FILES)
    provider.change_diff = make_diff(unresolved_repo, {"src/pkg/calc.py": "def total():\n    return 11\n"})
    unresolved = run_fix(unresolved_repo, capsys, UNRESOLVED_TARGET, UNRESOLVED_PRESERVE, ["--max-probes", "16"])

    assert supported["verdict"] == unresolved["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert supported["repair_basis"] != unresolved["repair_basis"]
    assert supported["diagnosis"] != unresolved["diagnosis"]
    assert supported["reproducer"] != unresolved["reproducer"]
    assert supported["claim_scope"] != unresolved["claim_scope"]
    # Same gate, same authority: the verdict does not weaken with the weaker
    # basis, and the receipt does not let the weaker basis borrow the stronger
    # one's standing.
    assert supported["reproducer_hash"] and not unresolved["reproducer_hash"]
