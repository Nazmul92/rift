"""M1-F15 and M1-F16: an assertion-supported environmental finding.

The branch these rows require was previously unreachable — `discover_handles`
never produced an assertion primitive and `compile_handles` turned one into a
no-op — so the rows could not be closed by any test (DAR-011). This exercises
the observation path end to end through the real CLI.

Every test carries a positive control, because the failure mode that matters
here is the opposite of the usual one: a runtime that reported *everything* as
missing would satisfy a naive missing-dependency assertion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from riftagent.records import GateStatus, Support, Verdict
from tests.conftest import build_repo

# The target imports a module that is not installed. No env var, cache directory
# or test-ordering handle can change that, so the intervention grammar cannot
# explain it and the enumerated space ends `representation_inadequate`.
MISSING_DEP = {
    "src/pkg/__init__.py": "",
    "src/pkg/api.py": "import riftagent_absent_dependency\n\n\ndef get():\n    return 1\n",
    "tests/test_target.py": "from pkg.api import get\n\n\ndef test_target():\n    assert get() == 1\n",
}

# The control: same shape, but the import is present. Nothing is missing, so
# nothing may be diagnosed as missing.
PRESENT_DEP = {
    "src/pkg/__init__.py": "",
    "src/pkg/api.py": "import json\n\n\ndef get():\n    return json.loads('2')\n",
    "tests/test_target.py": "from pkg.api import get\n\n\ndef test_target():\n    assert get() == 1\n",
}

MISSING_FILE = {
    "src/pkg/__init__.py": "",
    "src/pkg/api.py": ("def get():\n    with open('config/settings.ini') as fh:\n        return fh.read()\n"),
    "tests/test_target.py": "from pkg.api import get\n\n\ndef test_target():\n    assert get()\n",
}

TARGET = "tests/test_target.py::test_target"


def run_why(repo: Path, capsys, extra: list[str] | None = None) -> tuple[int, dict]:
    from riftagent.app import main

    code = main(["--repo", str(repo), "--json", "why", TARGET, "--allow-partial-sandbox", *(extra or [])])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def events_of(repo: Path) -> list[dict]:
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    return [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def test_f15_a_missing_dependency_is_an_observational_finding(tmp_path: Path, capsys):
    repo = build_repo(tmp_path / "missing-dep", MISSING_DEP)
    code, receipt = run_why(repo, capsys)
    diagnosis = receipt["diagnosis"]

    assert diagnosis["status"] == Verdict.DIAGNOSIS_SUPPORTED.value, diagnosis
    assert diagnosis["support"] == Support.OBSERVATIONAL.value
    assert diagnosis["gate"] == GateStatus.NOT_APPLICABLE.value
    assert [c["kind"] + ":" + c["arg"] for c in diagnosis["causes"]] == ["dep_assert:riftagent_absent_dependency"]
    assert diagnosis["remediation_unverified"].startswith("UNVERIFIED:")

    # the finding rests on an executed observation, recorded durably
    observed = [e["payload"] for e in events_of(repo) if e["kind"] == "assertion_observed"]
    assert observed, "no assertion was executed"
    absent = [o for o in observed if o["absent"]]
    assert [o["handle"]["arg"] for o in absent] == ["riftagent_absent_dependency"]
    assert absent[0]["outcome"] == "absent"
    # every measurement records which of the three outcomes it was, so an
    # unobservable one can never be read back as an absence
    assert {o["outcome"] for o in observed} <= {"present", "absent", "unobservable"}
    # `why` located a cause, so it succeeded. The verdict is still not a fix:
    # that distinction is carried by `gate: not_applicable` and the unverified
    # remediation above, and by `fix` refusing to gate anything (F16 below).
    assert code == 0


def test_f15_a_dependency_that_is_present_is_never_reported_missing(tmp_path: Path, capsys):
    """The positive control. A runtime that reported everything absent would
    pass the test above and fail this one."""
    repo = build_repo(tmp_path / "present-dep", PRESENT_DEP)
    _, receipt = run_why(repo, capsys)
    diagnosis = receipt["diagnosis"]

    assert diagnosis["support"] != Support.OBSERVATIONAL.value, diagnosis
    assert not [c for c in diagnosis["causes"] if c["kind"] in ("dep_assert", "file_assert")]
    for payload in [e["payload"] for e in events_of(repo) if e["kind"] == "assertion_observed"]:
        assert not payload["absent"], f"{payload['handle']} was reported missing but is present"


def test_f15_a_missing_file_is_an_observational_finding(tmp_path: Path, capsys):
    repo = build_repo(tmp_path / "missing-file", MISSING_FILE)
    _, receipt = run_why(repo, capsys)
    diagnosis = receipt["diagnosis"]

    assert diagnosis["support"] == Support.OBSERVATIONAL.value, diagnosis
    assert [c["arg"] for c in diagnosis["causes"]] == ["config/settings.ini"]


def test_f16_fix_stops_before_propose_change_and_earns_no_verified_credit(tmp_path: Path, capsys, monkeypatch):
    """The row's other half, now reachable from a real repository rather than a
    substituted diagnosis."""
    from riftagent.app import main

    monkeypatch.setenv("RIFT_LLM_URL", "http://127.0.0.1:1/v1/chat/completions")
    monkeypatch.setenv("RIFT_LLM_KEY", "sk-SENTINEL-must-not-be-used")
    monkeypatch.setenv("RIFT_LLM_MODEL", "fake")

    repo = build_repo(tmp_path / "fix-observational", MISSING_DEP)
    code = main(["--repo", str(repo), "--json", "fix", TARGET, "--allow-partial-sandbox", "--max-usd", "1.00"])
    receipt = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    events = events_of(repo)
    kinds = [e["kind"] for e in events]

    # no patch was requested, produced or gated
    assert "propose_change" not in [
        e["payload"].get("operation") for e in events if e["kind"] == "model_request_started"
    ]
    assert "changeset_registered" not in kinds
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    assert not (td / "change-set.diff").exists()

    # the stop is recorded, and the outcome earns no verified-fix credit
    stop = [e["payload"] for e in events if e["kind"] == "gate_phase_finished"]
    assert len(stop) == 1 and not stop[0]["passed"]
    assert "no patch was generated and none was gated" in stop[0]["reason"]
    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code != 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("E   ModuleNotFoundError: No module named 'yaml'", ["dep_assert:yaml"]),
        ("E   FileNotFoundError: [Errno 2] No such file or directory: 'config/a.ini'", ["file_assert:config/a.ini"]),
        ("E   AssertionError: assert 10 == 11", []),
        ("E   ValueError: something went wrong", []),
    ],
    ids=["missing module", "missing file", "wrong value", "unrelated error"],
)
def test_f15_assertions_are_discovered_only_from_explicit_evidence(text: str, expected: list[str]):
    """Bounded discovery: the interpreter must name the missing thing outright.
    An ordinary assertion failure yields no assertion handle, so this cannot
    become a general-purpose guess."""
    from riftagent import kernel

    handles = kernel.discover_handles(text, ["src/"], ["tests/test_target.py::test_target"], TARGET)
    assert [h.label for h in handles if not h.is_intervention] == expected


# ==========================================================================
# discovery is validated, not merely pattern-matched
# ==========================================================================


@pytest.mark.parametrize(
    "arg",
    [
        "/etc/passwd",
        "/etc/shadow",
        "../../secrets.env",
        "../outside/config.ini",
        "a/../../b",
        "/absolute/with/../traversal",
    ],
    ids=["absolute", "absolute shadow", "traversal", "traversal relative", "embedded traversal", "both"],
)
def test_a_hostile_failure_message_cannot_produce_an_escaping_handle(arg: str):
    """A failure message is untrusted text.

    Nothing stops a repository printing `No such file or directory: '/etc/passwd'`
    — from a test fixture, a dependency, or deliberately. Discovery builds
    handles from that text, so it must pass the same contract a model proposal
    passes; a handle constructed directly would skip the traversal and
    absolute-path rules that exist for exactly this.
    """
    from riftagent import kernel

    text = f"E   FileNotFoundError: [Errno 2] No such file or directory: '{arg}'"
    handles = kernel.discover_handles(text, ["src/"], ["tests/test_target.py::test_target"], TARGET)
    assert [h.label for h in handles if not h.is_intervention] == [], [h.label for h in handles]


@pytest.mark.parametrize("arg", ["config/settings.ini", "data.json", "ffmpeg", "pkg/sub/file.txt"])
def test_a_repository_relative_path_is_still_discovered(arg: str):
    """The positive control. Without it the test above would also pass if
    discovery had simply stopped producing assertion handles at all."""
    from riftagent import kernel

    text = f"E   FileNotFoundError: [Errno 2] No such file or directory: '{arg}'"
    handles = kernel.discover_handles(text, ["src/"], ["tests/test_target.py::test_target"], TARGET)
    assert [h.label for h in handles if not h.is_intervention] == [f"file_assert:{arg}"]


def test_a_shell_metacharacter_in_a_named_path_is_refused():
    from riftagent import kernel

    text = "E   FileNotFoundError: [Errno 2] No such file or directory: 'a;rm -rf /'"
    handles = kernel.discover_handles(text, ["src/"], ["tests/test_target.py::test_target"], TARGET)
    assert [h.label for h in handles if not h.is_intervention] == []


# ==========================================================================
# an unobservable measurement is not an absence
# ==========================================================================


def test_an_unobservable_measurement_is_not_evidence_of_absence(tmp_path: Path, monkeypatch):
    """The distinction the three outcomes exist for.

    An import machinery failure, a timeout, or a sandbox fault says nothing
    about whether the thing exists. Only an executed, valid `absent` may support
    a finding.
    """
    from riftagent import checks
    from riftagent.checks import ABSENT, PRESENT, UNOBSERVABLE, evaluate_assertion
    from riftagent.records import Handle, Primitive
    from riftagent.sandbox import CommandResult, Worktree, probe_isolation

    repo = build_repo(tmp_path / "unobservable", {"tests/test_a.py": "def test_a():\n    pass\n"})
    handle = Handle(Primitive.DEP_ASSERT, "somemodule")

    def result(stdout: str, exit_code: int, timed_out: bool = False) -> CommandResult:
        return CommandResult(
            argv=("python",), exit_code=exit_code, stdout=stdout, stderr="", duration_s=0.1, timed_out=timed_out
        )

    cases = [
        ("error ModuleNotFoundError\n", 2, False, UNOBSERVABLE),
        ("", 2, False, UNOBSERVABLE),
        ("", 0, True, UNOBSERVABLE),
        ("absent\n", 0, False, UNOBSERVABLE),  # word and exit code disagree
        ("present\n", 1, False, UNOBSERVABLE),  # word and exit code disagree
        ("absent\n", 1, False, ABSENT),
        ("present\n", 0, False, PRESENT),
    ]
    wt = Worktree(repo, "unobservable")
    try:
        for stdout, code, timed_out, expected in cases:
            monkeypatch.setattr(
                checks, "run_argv", lambda *a, _o=stdout, _c=code, _t=timed_out, **k: result(_o, _c, _t)
            )
            outcome, _ = evaluate_assertion(handle, wt, probe_isolation(), 10.0)
            assert outcome == expected, (stdout, code, timed_out, outcome)
    finally:
        wt.dispose()


def test_an_unobservable_assertion_supports_no_finding():
    """The rule the outcomes feed: only ABSENT reaches the kernel's list."""
    from riftagent import kernel
    from riftagent.records import Handle, Primitive

    assert kernel.observational_diagnosis([], []) is None
    found = kernel.observational_diagnosis([Handle(Primitive.DEP_ASSERT, "yaml")], [])
    assert found is not None and found.support is Support.OBSERVATIONAL


# ==========================================================================
# the assertion command obeys the ledger contract
# ==========================================================================


def test_the_assertion_command_is_announced_before_it_runs(tmp_path: Path, capsys):
    """Same contract as every other command: started, then finished, then what
    was observed. A command that appeared only once it had completed would break
    the live view's identity with the settled transcript."""
    repo = build_repo(tmp_path / "order", MISSING_DEP)
    run_why(repo, capsys)
    kinds = [e["kind"] for e in events_of(repo)]

    observed_at = [i for i, k in enumerate(kinds) if k == "assertion_observed"]
    assert observed_at, "no assertion was measured"
    for index in observed_at:
        before = kinds[:index]
        assert before.count("command_started") > before.count("command_finished") - 1
        # the finish immediately precedes the observation, and a start precedes
        # that finish
        assert kinds[index - 1] == "command_finished", kinds[max(0, index - 4) : index + 1]
        assert "command_started" in kinds[:index]

    # every command_finished in the run has a command_started before it
    depth = 0
    for kind in kinds:
        if kind == "command_started":
            depth += 1
        elif kind == "command_finished":
            depth -= 1
            assert depth >= 0, "a command finished that was never announced"

    # and the assertion command is charged like any other
    receipt = json.loads((sorted((repo / ".rift" / "tasks").iterdir())[-1] / "receipt.json").read_text("utf-8"))
    assert receipt["commands"] == kinds.count("command_finished")


# ==========================================================================
# a sandbox failure closes its own command, and supports nothing
# ==========================================================================


def test_a_sandbox_failure_closes_the_command_and_supports_no_finding(tmp_path: Path, capsys, monkeypatch):
    """The one path where the measurement never ran at all.

    `evaluate_assertion` returns `(UNOBSERVABLE, None)` when the sandbox itself
    refuses to execute. The command was still announced, so it must still be
    closed — an announced command that never finishes leaves the ledger
    unbalanced and the attempt uncharged — and it must be closed as
    unsuccessful, because nothing was observed.
    """
    from riftagent import checks
    from riftagent.app import render_settled
    from riftagent.records import read_events
    from riftagent.sandbox import SandboxError

    real = checks.run_argv

    def refuse(argv, *a, **k):
        # Only the assertion program is refused. Failing every command would
        # break the run long before an assertion was ever discovered, and would
        # test the probe path rather than this one.
        if any(checks._ASSERT_SRC == part for part in argv):
            raise SandboxError("the sandbox could not execute the assertion")
        return real(argv, *a, **k)

    monkeypatch.setattr(checks, "run_argv", refuse)

    repo = build_repo(tmp_path / "sandbox-refused", MISSING_FILE)
    _, receipt = run_why(repo, capsys)
    events = events_of(repo)
    kinds = [e["kind"] for e in events]

    # 1. the command was announced
    started = [
        e for e in events if e["kind"] == "command_started" and e["payload"].get("display", "").startswith("assert ")
    ]
    assert started, "the assertion command was never announced"

    # 2. and closed, once, immediately after
    observed = [i for i, k in enumerate(kinds) if k == "assertion_observed"]
    assert observed, "no assertion was recorded"
    for index in observed:
        assert kinds[index - 1] == "command_finished", kinds[max(0, index - 3) : index + 1]

    # every started command in the whole run has a matching finish
    assert kinds.count("command_started") == kinds.count("command_finished"), (
        kinds.count("command_started"),
        kinds.count("command_finished"),
    )

    # 3. closed as unsuccessful, and never as success
    closes = [e["payload"] for e in events if e["kind"] == "command_finished" and "outcome" in e["payload"]]
    assert closes, "the assertion command was closed without recording its outcome"
    for payload in closes:
        assert payload["outcome"] == "unobservable", payload
        assert payload["successful"] is False, payload
        assert payload["exit_code"] == -1, payload

    # 4. the observation says unobservable, and is not an absence
    for payload in [e["payload"] for e in events if e["kind"] == "assertion_observed"]:
        assert payload["outcome"] == "unobservable", payload
        assert payload["absent"] is False, payload

    # 5. nothing was supported by it
    assert receipt["diagnosis"]["support"] != Support.OBSERVATIONAL.value, receipt["diagnosis"]
    assert not [c for c in receipt["diagnosis"]["causes"] if c["kind"] in ("dep_assert", "file_assert")]

    # 6. and the attempt is charged like any other command
    assert receipt["commands"] == kinds.count("command_finished")

    # 7. replay is byte-identical
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    replayed = render_settled(read_events(td / "ledger.jsonl")[0])
    assert replayed == (td / "transcript.txt").read_text(encoding="utf-8")
    assert replayed == render_settled(read_events(td / "ledger.jsonl")[0])


def test_the_normal_outcomes_still_close_successfully(tmp_path: Path, capsys):
    """The positive control for the test above. A runtime that marked every
    assertion command unsuccessful would satisfy it and fail this."""
    repo = build_repo(tmp_path / "normal-close", MISSING_FILE)
    run_why(repo, capsys)
    events = events_of(repo)
    closes = [e["payload"] for e in events if e["kind"] == "command_finished" and "outcome" in e["payload"]]
    assert closes, "no assertion command was closed"
    assert any(p["successful"] and p["outcome"] in ("present", "absent") for p in closes), closes
    kinds = [e["kind"] for e in events]
    assert kinds.count("command_started") == kinds.count("command_finished")


# ==========================================================================
# the archive rule excludes the audit harnesses
# ==========================================================================


def test_the_archive_excludes_the_audit_harness_directory():
    """`.codex-test-tmp` holds mutation and gate harnesses. They are evidence
    for a review, never part of a handoff, and their exclusion is structural
    rather than a matter of remembering."""
    from riftagent.records import ARCHIVE_EXCLUDE_DIRS, archive_manifest

    root = Path(__file__).resolve().parent.parent
    assert ".codex-test-tmp" in ARCHIVE_EXCLUDE_DIRS

    members = archive_manifest(root)
    assert members, "the archive manifest is empty"
    for member in members:
        assert ".codex-test-tmp" not in Path(member).parts, member

    # The control: the directory exists in this tree, so the assertion above is
    # about the rule rather than about an absent directory.
    if (root / ".codex-test-tmp").is_dir():
        present = [p for p in (root / ".codex-test-tmp").rglob("*.py")]
        assert present, "the harness directory is empty; the exclusion proves nothing here"
