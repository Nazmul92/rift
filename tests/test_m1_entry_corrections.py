"""F2 and F3: the M1 entry corrections.

F2 — a declared node that cannot be collected alone is observed through its
containing file, and only the declared target's own report line counts.

F3 — reapplication reloads the ChangeSet from its durable record and compares
independently derived hashes, so a tampered record is caught rather than
papered over by an in-memory object comparing equal to itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from riftagent.checks import PytestRunner
from riftagent.kernel import decide_reapply
from riftagent.records import EventKind, GatePhase, Verdict, read_events, reduce
from tests.conftest import SIMPLE_FILES, SIMPLE_TARGET, build_repo, make_diff
from tests.test_gate_end_to_end import events_of, latest_task, receipt_of

# A conftest that fails collection when exactly one item is selected. This is
# the shape the chardet cases hit: the node is fine, collecting it *alone* is
# not. Widening by one file makes the target observable again.
SINGLE_NODE_HOSTILE_CONFTEST = (
    "def pytest_collection_modifyitems(config, items):\n"
    "    if len(items) == 1:\n"
    "        raise RuntimeError('single-item collection is unsupported in this fixture')\n"
)


@pytest.fixture
def hostile_repo(tmp_path: Path) -> Path:
    files = dict(SIMPLE_FILES)
    files["tests/conftest.py"] = SINGLE_NODE_HOSTILE_CONFTEST
    files["tests/test_calc.py"] = (
        "from pkg.calc import total\n\n\n"
        "def test_total():\n    assert total() == 11\n\n\n"
        "def test_neighbour():\n    assert total() >= 0\n"
    )
    return build_repo(tmp_path / "hostile", files)


# ------------------------------------------------------------------ F2


def test_containing_file_is_one_step_never_the_suite():
    assert PytestRunner.containing_file("tests/test_x.py::TestC::test_y[p]") == "tests/test_x.py"
    assert PytestRunner.containing_file("tests/test_x.py") is None, "a bare file must not widen further"
    assert PytestRunner.containing_file("") is None


def test_f2_single_node_collection_failure_falls_back_to_the_containing_file(hostile_repo, write_diff, run_cli):
    diff = make_diff(hostile_repo, {"src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n"})
    code, out = run_cli(
        "--repo", str(hostile_repo), "verify", str(write_diff(diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    assert code == 0, out
    receipt = receipt_of(hostile_repo)
    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value

    fallbacks = [e for e in events_of(hostile_repo) if e.kind is EventKind.CHECK_FALLBACK]
    assert fallbacks, "the widening step was not recorded in the ledger"
    assert fallbacks[0].payload["selector"] == "tests/test_calc.py"
    assert fallbacks[0].payload["scope_expansion"] == "single node → its containing file"


def test_f2_widened_observation_is_disclosed_in_the_receipt(hostile_repo, write_diff, run_cli):
    diff = make_diff(hostile_repo, {"src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n"})
    run_cli("--repo", str(hostile_repo), "verify", str(write_diff(diff)), SIMPLE_TARGET, "--allow-partial-sandbox")
    receipt = receipt_of(hostile_repo)
    assert any("could not be collected as a single node" in n for n in receipt["remaining_uncertainty"]), (
        "a widened observation scope must never reach a receipt silently"
    )
    widened = [r for r in receipt["results"] if r["fallback"]]
    assert widened and all(r["fallback"] == "tests/test_calc.py" for r in widened)


def test_f2_every_executed_command_is_charged(hostile_repo, write_diff, run_cli):
    """The retry is a real command and must appear in the ledger and the spend."""
    diff = make_diff(hostile_repo, {"src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n"})
    run_cli("--repo", str(hostile_repo), "verify", str(write_diff(diff)), SIMPLE_TARGET, "--allow-partial-sandbox")
    events = events_of(hostile_repo)
    started = [e for e in events if e.kind is EventKind.COMMAND_STARTED]
    finished = [e for e in events if e.kind is EventKind.COMMAND_FINISHED]
    assert len(started) == len(finished)
    selectors = {e.payload["selector"] for e in started}
    assert SIMPLE_TARGET in selectors and "tests/test_calc.py" in selectors
    assert receipt_of(hostile_repo)["commands"] == len(finished)


def test_f2_fallback_still_reads_the_declared_targets_own_line(hostile_repo, write_diff, run_cli):
    """A neighbour passing cannot stand in for the target.

    The declared node here does not exist. Widening finds the file, the file's
    other test passes, and the target still has no report line — so no
    evidence exists and the gate refuses rather than borrowing the neighbour's
    result.
    """
    diff = make_diff(hostile_repo, {"src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n"})
    code, _ = run_cli(
        "--repo",
        str(hostile_repo),
        "verify",
        str(write_diff(diff)),
        "tests/test_calc.py::test_does_not_exist",
        "--allow-partial-sandbox",
    )
    assert code == 3
    receipt = receipt_of(hostile_repo)
    assert receipt["verdict"] == Verdict.INFRASTRUCTURE_BLOCKED.value
    assert "could not observe the target" in receipt["reason"]


def test_f2_fallback_is_not_used_when_the_node_is_observable(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    assert not [e for e in events_of(simple_repo) if e.kind is EventKind.CHECK_FALLBACK]
    assert all(not r["fallback"] for r in receipt_of(simple_repo)["results"])


# ------------------------------------------------------------------ F3


def test_f3_decide_reapply_refuses_a_missing_hash():
    """Guards the call site: a hash that was never derived proves nothing."""
    assert decide_reapply("tree", "tree", "", "abc").passed is False
    assert decide_reapply("tree", "tree", "abc", "").passed is False
    assert decide_reapply("tree", "tree", "", "").infrastructure is True


def test_f3_decide_reapply_rejects_divergent_bytes_and_trees():
    assert decide_reapply("tree", "tree", "aaa", "bbb").passed is False
    assert "no longer hashes" in decide_reapply("tree", "tree", "aaa", "bbb").reason
    assert decide_reapply("treeA", "treeB", "aaa", "aaa").passed is False
    assert decide_reapply("tree", "tree", "aaa", "aaa").passed is True


def test_f3_changeset_record_is_written_before_acceptance_is_recorded(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    td = latest_task(simple_repo)
    assert (td / "change-set.diff").read_text(encoding="utf-8") == correct_diff
    reloaded = [e for e in events_of(simple_repo) if e.kind is EventKind.CHANGESET_RELOADED]
    assert len(reloaded) == 1, "reapplication must record the reload it performed"
    registered = next(e for e in events_of(simple_repo) if e.kind is EventKind.CHANGESET_REGISTERED)
    assert reloaded[0].payload["reloaded_patch_hash"] == registered.payload["changeset"]["patch_hash"]
    assert registered.seq < reloaded[0].seq


def _truncate_after_withdrawal(td: Path) -> None:
    events, _ = read_events(td / "ledger.jsonl")
    cut = next(
        i
        for i, e in enumerate(events, start=1)
        if e.kind is EventKind.GATE_PHASE_FINISHED and e.payload["phase"] == GatePhase.WITHDRAWAL.value
    )
    lines = (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
    (td / "ledger.jsonl").write_text("".join(lines[:cut]), encoding="utf-8")
    for derived in ("receipt.json", "receipt.txt", "transcript.txt"):
        if (td / derived).exists():
            (td / derived).unlink()


def test_f3_tampered_durable_changeset_is_caught_on_reload(simple_repo, correct_diff, write_diff, run_cli):
    """The attack this exists to stop: swap the accepted bytes between
    acceptance and reapplication."""
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    td = latest_task(simple_repo)
    _truncate_after_withdrawal(td)
    (td / "change-set.diff").write_text(correct_diff + "\n# tampered\n", encoding="utf-8", newline="\n")

    code, _ = run_cli("--repo", str(simple_repo), "resume")
    receipt = json.loads((td / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert "no longer hashes to its accepted value" in receipt["reason"]
    assert receipt["rejected_phase"] == GatePhase.REAPPLY.value
    assert code == 2


def test_f3_deleted_durable_changeset_blocks_rather_than_improvises(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    td = latest_task(simple_repo)
    _truncate_after_withdrawal(td)
    (td / "change-set.diff").unlink()

    code, _ = run_cli("--repo", str(simple_repo), "resume")
    receipt = json.loads((td / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["verdict"] == Verdict.INFRASTRUCTURE_BLOCKED.value
    assert "durable ChangeSet record could not be read" in receipt["reason"]
    assert code == 3


def test_f3_crash_and_reload_reaches_the_same_verdict(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    td = latest_task(simple_repo)
    original = json.loads((td / "receipt.json").read_text(encoding="utf-8"))["verdict"]
    _truncate_after_withdrawal(td)

    code, _ = run_cli("--repo", str(simple_repo), "resume")
    assert code == 0
    assert json.loads((td / "receipt.json").read_text(encoding="utf-8"))["verdict"] == original
    proj = reduce(*read_events(td / "ledger.jsonl"))
    assert proj.complete is True


def test_f3_receipt_is_not_rewritten_over_a_tampered_record(simple_repo, correct_diff, write_diff, run_cli):
    """Artifact regeneration must not quietly repair the durable record."""
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    td = latest_task(simple_repo)
    _truncate_after_withdrawal(td)
    tampered = correct_diff + "\n# tampered\n"
    (td / "change-set.diff").write_text(tampered, encoding="utf-8", newline="\n")
    run_cli("--repo", str(simple_repo), "resume")
    assert (td / "change-set.diff").read_text(encoding="utf-8") == tampered
