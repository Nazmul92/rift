"""V-09 and V-10: the ledger is the only durable state, and rendering is a
pure projection of it.

The interesting assertions here are the negative ones: no second state file is
created, and killing the renderer changes nothing about the settled output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from riftagent.app import render_settled
from riftagent.records import (
    EventKind,
    GatePhase,
    Ledger,
    LedgerCorrupt,
    Phase,
    read_events,
    reduce,
)
from tests.conftest import SIMPLE_PRESERVE, SIMPLE_TARGET
from tests.test_gate_end_to_end import latest_task


def _run(run_cli, repo, diff_path, *extra):
    return run_cli("--repo", str(repo), "verify", str(diff_path), SIMPLE_TARGET, "--allow-partial-sandbox", *extra)


# ---------------------------------------------------------------- V-09


def test_ledger_is_the_only_durable_state(simple_repo, correct_diff, write_diff, run_cli):
    _run(run_cli, simple_repo, write_diff(correct_diff))
    td = latest_task(simple_repo)
    written = {p.name for p in td.iterdir()}
    forbidden = {"state.json", "checkpoint.db", "state.sqlite", "projection.json", "events2.jsonl", "session.pkl"}
    assert not (written & forbidden), f"secondary state written: {written & forbidden}"
    derived = {
        "receipt.json",
        "receipt.txt",
        "transcript.txt",
        "task-contract.json",
        "check-set.json",
        "change-set.diff",
        "repro.sh",
    }
    assert written == derived | {"ledger.jsonl"}


def test_every_derived_file_is_reproducible_from_the_ledger(simple_repo, correct_diff, write_diff, run_cli):
    _run(run_cli, simple_repo, write_diff(correct_diff))
    td = latest_task(simple_repo)
    events, _ = read_events(td / "ledger.jsonl")
    proj = reduce(events)
    assert proj.contract is not None and proj.checkset is not None and proj.changeset is not None
    assert json.loads((td / "task-contract.json").read_text(encoding="utf-8")) == proj.contract.to_dict()
    assert json.loads((td / "check-set.json").read_text(encoding="utf-8")) == proj.checkset.to_dict()
    assert (td / "change-set.diff").read_text(encoding="utf-8") == proj.changeset.diff


def test_phase_and_budgets_reconstruct_only_from_events(simple_repo, correct_diff, write_diff, run_cli):
    _run(run_cli, simple_repo, write_diff(correct_diff), "--preserve", SIMPLE_PRESERVE)
    events, _ = read_events(latest_task(simple_repo) / "ledger.jsonl")
    proj = reduce(events)
    assert proj.phase is Phase.TERMINAL
    assert proj.commands == sum(1 for e in events if e.kind is EventKind.COMMAND_FINISHED)
    assert proj.commands >= 4
    assert proj.seconds > 0


def test_a_prefix_of_the_ledger_reduces_to_the_phase_reached(simple_repo, correct_diff, write_diff, run_cli):
    """Crash injection: truncating after any durable transition must leave a
    projection that names the next thing to do, never a completed task."""
    _run(run_cli, simple_repo, write_diff(correct_diff))
    events, _ = read_events(latest_task(simple_repo) / "ledger.jsonl")
    for cut in range(1, len(events)):
        proj = reduce(events[:cut])
        assert proj.complete is False, f"prefix of {cut} events looks complete"
        assert isinstance(proj.phase, Phase)
    assert reduce(events).complete is True


def test_torn_final_line_is_tolerated_and_disclosed(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, "t1")
    ledger.append(EventKind.TASK_STARTED, {"task_id": "t1", "verb": "verify", "repo": "/r", "target": "x"})
    ledger.append(EventKind.SANDBOX_PROBED, {"level": "partial", "detail": "d"})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"schema_version": 1, "seq": 3, "task_id": "t1", "ki')
    events, truncated = read_events(path)
    assert truncated is True
    assert len(events) == 2
    assert reduce(events, truncated).truncated_tail is True


def test_a_malformed_middle_line_fails_closed(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, "t1")
    ledger.append(EventKind.TASK_STARTED, {"task_id": "t1", "verb": "verify", "repo": "/r", "target": "x"})
    ledger.append(EventKind.SANDBOX_PROBED, {"level": "partial", "detail": "d"})
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\n{ this is not json }\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        read_events(path)


def test_a_tampered_event_fails_closed(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, "t1")
    ledger.append(EventKind.TASK_STARTED, {"task_id": "t1", "verb": "verify", "repo": "/r", "target": "x"})
    ledger.append(EventKind.SANDBOX_PROBED, {"level": "partial", "detail": "d"})
    lines = path.read_text(encoding="utf-8").splitlines()
    doctored = json.loads(lines[0])
    doctored["payload"]["target"] = "something else"
    path.write_text(json.dumps(doctored) + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        read_events(path)


def test_sequence_breaks_fail_closed(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, "t1")
    ledger.append(EventKind.TASK_STARTED, {"task_id": "t1", "verb": "verify", "repo": "/r", "target": "x"})
    first = path.read_text(encoding="utf-8")
    path.write_text(first + first, encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        read_events(path)


def test_resume_reports_nothing_to_do_for_a_completed_task(simple_repo, correct_diff, write_diff, run_cli):
    _run(run_cli, simple_repo, write_diff(correct_diff))
    code, out = run_cli("--repo", str(simple_repo), "resume")
    assert code == 0
    assert "no incomplete tasks" in out


def test_resume_completes_a_task_interrupted_after_the_baseline(simple_repo, correct_diff, write_diff, run_cli):
    """Truncate a real ledger at the baseline boundary, then resume it."""
    _run(run_cli, simple_repo, write_diff(correct_diff))
    td = latest_task(simple_repo)
    events, _ = read_events(td / "ledger.jsonl")
    cut = next(
        i
        for i, e in enumerate(events, start=1)
        if e.kind is EventKind.GATE_PHASE_FINISHED and e.payload["phase"] == GatePhase.BASELINE.value
    )
    lines = (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
    (td / "ledger.jsonl").write_text("".join(lines[:cut]), encoding="utf-8")
    for derived in ("receipt.json", "receipt.txt", "transcript.txt"):
        (td / derived).unlink()

    proj = reduce(*read_events(td / "ledger.jsonl"))
    assert proj.complete is False and proj.phase is Phase.CANDIDATE

    code, _ = run_cli("--repo", str(simple_repo), "resume")
    assert code == 0
    proj = reduce(*read_events(td / "ledger.jsonl"))
    assert proj.complete is True
    baselines = [
        e
        for e in read_events(td / "ledger.jsonl")[0]
        if e.kind is EventKind.CHECK_RESULT and e.payload["result"]["phase"] == "baseline"
    ]
    assert len(baselines) == 1, "resume repeated work that was already durable"


def test_resume_requires_a_choice_when_several_tasks_are_incomplete(simple_repo, correct_diff, write_diff, run_cli):
    for _ in range(2):
        _run(run_cli, simple_repo, write_diff(correct_diff))
    for td in sorted((simple_repo / ".rift" / "tasks").iterdir()):
        lines = (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
        (td / "ledger.jsonl").write_text("".join(lines[:3]), encoding="utf-8")
    code, _ = run_cli("--repo", str(simple_repo), "resume")
    assert code == 64
    assert "multiple incomplete tasks" in run_cli.err
    assert "active task" not in run_cli.err, "resume must not invent an authoritative pointer"


def test_drift_invalidates_recorded_evidence(simple_repo, correct_diff, write_diff, run_cli):
    _run(run_cli, simple_repo, write_diff(correct_diff))
    td = latest_task(simple_repo)
    events, _ = read_events(td / "ledger.jsonl")
    cut = next(
        i
        for i, e in enumerate(events, start=1)
        if e.kind is EventKind.GATE_PHASE_FINISHED and e.payload["phase"] == GatePhase.BASELINE.value
    )
    lines = (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
    (td / "ledger.jsonl").write_text("".join(lines[:cut]), encoding="utf-8")
    (simple_repo / "src" / "pkg" / "util.py").write_text(
        "def double(x):\n    return x * 2  # drift\n", encoding="utf-8"
    )

    code, _ = run_cli("--repo", str(simple_repo), "resume")
    events, _ = read_events(td / "ledger.jsonl")
    assert any(e.kind is EventKind.DRIFT_DETECTED for e in events)
    baselines = [e for e in events if e.kind is EventKind.CHECK_RESULT and e.payload["result"]["phase"] == "baseline"]
    assert len(baselines) == 2, "drift must force the baseline to be re-established"
    assert code == 0


# ---------------------------------------------------------------- V-10


def test_settled_transcript_replays_byte_identically(simple_repo, correct_diff, write_diff, run_cli):
    _run(run_cli, simple_repo, write_diff(correct_diff), "--preserve", SIMPLE_PRESERVE)
    td = latest_task(simple_repo)
    written = (td / "transcript.txt").read_text(encoding="utf-8")
    events, _ = read_events(td / "ledger.jsonl")
    assert render_settled(events) == written
    assert render_settled(events) == render_settled(read_events(td / "ledger.jsonl")[0])


def test_replay_subcommand_reproduces_the_transcript(simple_repo, correct_diff, write_diff, run_cli):
    _run(run_cli, simple_repo, write_diff(correct_diff))
    td = latest_task(simple_repo)
    code, out = run_cli("--repo", str(simple_repo), "replay", td.name)
    assert code == 0
    assert out == (td / "transcript.txt").read_text(encoding="utf-8")


def test_receipt_text_replays_byte_identically(simple_repo, correct_diff, write_diff, run_cli):
    from riftagent.app import render_receipt

    _run(run_cli, simple_repo, write_diff(correct_diff))
    td = latest_task(simple_repo)
    receipt = json.loads((td / "receipt.json").read_text(encoding="utf-8"))
    rendered = "\n".join(render_receipt(receipt)).lstrip("\n") + "\n"
    assert rendered == (td / "receipt.txt").read_text(encoding="utf-8")


def test_transcript_contains_no_transient_clock_output(simple_repo, correct_diff, write_diff, run_cli):
    _run(run_cli, simple_repo, write_diff(correct_diff))
    text = (latest_task(simple_repo) / "transcript.txt").read_text(encoding="utf-8")
    assert "…" not in text, "a transient spinner frame entered the settled transcript"
    assert "\r" not in text


def test_every_settled_line_comes_from_an_event(simple_repo, correct_diff, write_diff, run_cli):
    from riftagent.app import render_event

    _run(run_cli, simple_repo, write_diff(correct_diff))
    events, _ = read_events(latest_task(simple_repo) / "ledger.jsonl")
    produced = [line for ev in events for line in render_event(ev)]
    written = (latest_task(simple_repo) / "transcript.txt").read_text(encoding="utf-8").splitlines()
    assert produced == written
