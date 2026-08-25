"""The frozen task must reach every arm and every evaluation path.

A case carries two independent dimensions: an ordering `reproducer` and a frozen
`signature`. The driver coupled them — `--expect-signature` was emitted only
when a reproducer also existed — and put both inside the arm-A branch. All nine
preliminary cases have a signature and no reproducer, so every one of them would
have run without the failure evidence curation froze for it, while the manifest
validator reported the field present. Validated but never consumed.

These tests assert the full cross-product on constructed argv, and then on the
real manifest, because "the field is in the file" and "the field reaches the
command" are different claims and only the second one matters at run time.

No provider is configured and no request is made.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parent.parent / "benchmark" / "bm06" / "driver.py"
RUNTIME_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).resolve().parent.parent / "benchmark" / "bm06" / "manifest-preliminary.json"


def load_driver():
    spec = importlib.util.spec_from_file_location("bm06_driver_prop", DRIVER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bm06_driver_prop"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def pinned_repo(tmp_path: Path, name: str = "demo") -> tuple[str, str]:
    """A real parent -> fix pair, because DAR-023 makes the pin checkable.

    A fixture that names a repository which does not exist is no longer a valid
    manifest, which is the whole point of the invariant.
    """
    import subprocess

    root = tmp_path / "repos" / name
    if (root / ".git").exists():

        def g0(*a: str) -> str:
            return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True).stdout.strip()

        return g0("rev-parse", "HEAD~1"), g0("rev-parse", "HEAD")
    root.mkdir(parents=True, exist_ok=True)

    def g(*a: str) -> str:
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True).stdout.strip()

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (root / "mod.py").write_text("x = 1\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "parent")
    parent = g("rev-parse", "HEAD")
    (root / "mod.py").write_text("x = 2\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "fix")
    return parent, g("rev-parse", "HEAD")


def checkout_at(repos: pathlib.Path | Path, parent: str, dest: Path) -> Path:
    """A worktree whose HEAD is the pinned parent, as the driver requires."""
    import subprocess

    subprocess.run(
        ["git", "clone", "-q", str(Path(repos) / "demo"), str(dest)], capture_output=True, text=True, check=False
    )
    subprocess.run(["git", "-C", str(dest), "checkout", "-q", parent], capture_output=True, text=True, check=False)
    (dest / ".rift").mkdir(parents=True, exist_ok=True)
    return dest


def manifest_with(tmp_path: Path, **case_extra) -> dict:
    parent, commit = pinned_repo(tmp_path)
    worktree = checkout_at(tmp_path / "repos", parent, tmp_path / "repo")
    seed_task_ledger(worktree)
    case = {
        "case_id": "c1",
        "repo": "demo",
        "parent": parent,
        "commit": commit,
        "worktree": str(worktree),
        "target": "tests/test_x.py::test_y",
        "label": "gateable",
        "cause_class": "genuine_source_bug",
        "status": "OK",
        "preserve": ["tests/test_x.py::test_keep"],
        "baseline_tree_hash": load_driver().baseline_tree_hash(worktree),
    }
    case.update(case_extra)
    return {
        "manifest_hash": "deadbeef",
        "arms": {"A": {}, "B": {"seed": 20260818}, "C": {}},
        "budget": {"scope": "prelim", "max_usd": 3.43},
        "model": {
            "id": "claude-sonnet-5",
            "price_input_per_mtok": 2.0,
            "price_output_per_mtok": 10.0,
            "max_output_tokens": 4000,
            "max_probes": 16,
            "max_attempts": 1,
            "max_commands": 400,
            "timeout_s": 600.0,
        },
        "cases": [case],
    }


@pytest.fixture(autouse=True)
def _configured_model(monkeypatch):
    """The manifest fixtures declare this model, and DAR-026 requires the
    configured one to match it before any run is valid."""
    monkeypatch.setenv("RIFT_LLM_MODEL", "claude-sonnet-5")


def fake_proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout, stderr="")


def seed_task_ledger(worktree: Path, task_id: str = "t1", model: str = "claude-sonnet-5") -> None:
    """The provider evidence an arm's receipt points at.

    Since DAR-027 the driver reads `model_reported` from this ledger rather than
    from its own configuration, so a fixture that omits it is an arm whose
    provider identity cannot be attributed — and that fails closed by design.
    """
    import json as _json

    path = Path(worktree) / ".rift" / "tasks" / task_id / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _json.dumps({"kind": "model_response_received", "payload": {"model_reported": model}}) + "\n",
        encoding="utf-8",
    )


def bound_for(d, tmp_path: Path):
    """A `Bound` over a throwaway runtime, with identity checks stood down.

    Runtime binding has its own dedicated tests in `test_dar026_enforcement.py`;
    the tests here are about what the evaluation *issues*, so the identity
    assertion is neutralised rather than satisfied with a real tree.
    """
    root = tmp_path / "rt-for-bound"
    (root / "src" / "riftagent").mkdir(parents=True, exist_ok=True)
    (root / "src" / "riftagent" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    bound = d.Bound(root, d.runtime_hash(root)[0])
    bound.check = lambda cwd, when: None  # type: ignore[method-assign]
    return bound


def gate_argv(d, case: dict, tmp_path: Path, monkeypatch) -> list[str]:
    """The argv an evaluation path actually issues — ground truth and shadow
    share this function, so one capture covers both."""
    issued: list[list[str]] = []

    def fake_rift(args, cwd, timeout=3600.0, env=None):
        if args[:2] == ["fix", "--help"]:
            return fake_proc(stdout="--model-alone --probe-policy --precondition --expect-signature")
        issued.append(args)
        return fake_proc(stdout=json.dumps({"verdict": "verified_against_approved_checks", "task_id": "t1"}))

    monkeypatch.setattr(d, "_rift", fake_rift)
    patch = tmp_path / "p.diff"
    patch.write_text("--- a\n+++ b\n", encoding="utf-8")
    out = d.evaluate_under_gate(case, patch, tmp_path, bound_for(d, tmp_path))
    assert out["evaluated"] is True, out
    return issued[0]


# --------------------------------------------------------- signature only


def test_a_signature_only_case_reaches_every_arm(tmp_path: Path):
    """The nine preliminary cases are exactly this shape, and exactly the shape
    the coupled condition dropped."""
    d = load_driver()
    manifest = manifest_with(tmp_path, signature="AssertionError: boom")
    case = manifest["cases"][0]
    for arm in d.ARMS:
        argv = d.arm_argv(arm, case, manifest, "prelim")
        assert "--expect-signature" in argv, f"arm {arm} dropped the frozen signature: {argv}"
        assert argv[argv.index("--expect-signature") + 1] == "AssertionError: boom"
        assert "--precondition" not in argv, f"arm {arm} invented a precondition: {argv}"


def test_a_signature_only_case_reaches_both_evaluation_paths(tmp_path: Path, monkeypatch):
    d = load_driver()
    case = manifest_with(tmp_path, signature="AssertionError: boom")["cases"][0]
    argv = gate_argv(d, case, tmp_path, monkeypatch)
    assert "--expect-signature" in argv, argv
    assert argv[argv.index("--expect-signature") + 1] == "AssertionError: boom"
    assert "--precondition" not in argv, argv


# --------------------------------------------------------- ordered case


def test_an_ordered_case_reaches_every_arm_identically(tmp_path: Path):
    """A/B/C must reproduce the *same* failure. Arms that reproduce different
    failures are not comparable, which is the point of comparing them."""
    d = load_driver()
    manifest = manifest_with(
        tmp_path,
        signature="AssertionError: registry is dirty",
        reproducer=["tests/test_a.py::test_a", "tests/test_b.py::test_b"],
    )
    case = manifest["cases"][0]
    frozen = {arm: d.frozen_task_args(case) for arm in d.ARMS}
    assert len({tuple(v) for v in frozen.values()}) == 1, frozen

    for arm in d.ARMS:
        argv = d.arm_argv(arm, case, manifest, "prelim")
        # Order preserved: a precondition sequence is a sequence.
        preconditions = [argv[i + 1] for i, a in enumerate(argv) if a == "--precondition"]
        assert preconditions == ["tests/test_a.py::test_a", "tests/test_b.py::test_b"], (arm, argv)
        assert argv[argv.index("--expect-signature") + 1] == "AssertionError: registry is dirty", (arm, argv)


def test_an_ordered_case_reaches_the_evaluation_paths(tmp_path: Path, monkeypatch):
    d = load_driver()
    case = manifest_with(
        tmp_path,
        signature="AssertionError: registry is dirty",
        reproducer=["tests/test_a.py::test_a"],
    )["cases"][0]
    argv = gate_argv(d, case, tmp_path, monkeypatch)
    assert argv[argv.index("--precondition") + 1] == "tests/test_a.py::test_a", argv
    assert argv[argv.index("--expect-signature") + 1] == "AssertionError: registry is dirty", argv


# --------------------------------------------------------- independence


@pytest.mark.parametrize(
    "extra, wants_precondition, wants_signature",
    [
        ({}, False, False),
        ({"signature": "AssertionError: boom"}, False, True),
        ({"reproducer": ["tests/test_a.py::test_a"]}, True, False),
        ({"signature": "AssertionError: boom", "reproducer": ["tests/test_a.py::test_a"]}, True, True),
    ],
)
def test_the_four_combinations_are_independent(tmp_path: Path, extra, wants_precondition, wants_signature):
    """The whole cross-product, on every arm. A case with a signature and no
    reproducer must still carry its signature — that is the defect — and a case
    with a reproducer and no signature must not acquire one."""
    d = load_driver()
    manifest = manifest_with(tmp_path, **extra)
    case = manifest["cases"][0]
    for arm in d.ARMS:
        argv = d.arm_argv(arm, case, manifest, "prelim")
        assert ("--precondition" in argv) is wants_precondition, (arm, argv)
        assert ("--expect-signature" in argv) is wants_signature, (arm, argv)


def test_the_frozen_task_is_not_an_arm_a_privilege(tmp_path: Path):
    """Guards the dormant half: preconditions used to be emitted only inside the
    arm-A branch, so B and C ran an ordering case as a bare target."""
    d = load_driver()
    manifest = manifest_with(tmp_path, signature="E: x", reproducer=["tests/test_a.py::test_a"])
    case = manifest["cases"][0]
    a = [x for x in d.arm_argv("A", case, manifest, "prelim") if x not in ("--model-alone",)]
    b = d.arm_argv("B", case, manifest, "prelim")
    c = d.arm_argv("C", case, manifest, "prelim")
    for other in (b, c):
        assert [x for x in a if x in ("--precondition", "--expect-signature")] == [
            x for x in other if x in ("--precondition", "--expect-signature")
        ], (a, other)


def test_a_missing_signature_capability_refuses_rather_than_dropping_it(tmp_path: Path, monkeypatch):
    """Each dimension is checked against the flag it needs. Refusing on the
    wrong flag, or not refusing at all, silently discards curated evidence."""
    d = load_driver()
    issued: list[list[str]] = []

    def fake_rift(args, cwd, timeout=3600.0, env=None):
        if args[:2] == ["fix", "--help"]:
            return fake_proc(stdout="--model-alone --probe-policy --precondition")  # no --expect-signature
        issued.append(args)
        return fake_proc(stdout=json.dumps({"verdict": "verified_against_approved_checks", "task_id": "t1"}))

    monkeypatch.setattr(d, "_rift", fake_rift)
    case = manifest_with(tmp_path, signature="AssertionError: boom")["cases"][0]
    patch = tmp_path / "p.diff"
    patch.write_text("--- a\n+++ b\n", encoding="utf-8")

    out = d.evaluate_under_gate(case, patch, tmp_path, bound_for(d, tmp_path))
    assert out == {"evaluated": False, "reason": "NOT_RUN_SIGNATURE_UNSUPPORTED"}, out
    assert issued == [], "a verify ran despite the signature capability being absent"


# --------------------------------------------------------- the real manifest


@pytest.mark.skipif(not MANIFEST.is_file(), reason="the preliminary manifest is not present")
def test_every_preliminary_case_carries_its_signature_into_every_arm():
    """The claim that matters at run time, asserted against the real file rather
    than a fixture resembling it."""
    d = load_driver()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["cases"], "the preliminary manifest has no cases"

    for case in manifest["cases"]:
        assert case.get("signature"), f"{case['case_id']} has no frozen signature to propagate"
        for arm in d.ARMS:
            argv = d.arm_argv(arm, case, manifest, manifest["budget"]["scope"])
            assert "--expect-signature" in argv, f"{case['case_id']} arm {arm}: {argv}"
            assert argv[argv.index("--expect-signature") + 1] == case["signature"]


# --------------------------------------------------------- manifest identity


def write_results(path: Path, manifest_hash: str, d=None, **identity) -> Path:
    """A results artifact carrying the run identity the driver would stamp.

    Since DAR-023 that is three hashes, not one, and `--report-only` fails
    closed on any of them — so a helper that stamped only the manifest would
    make every report-only test fail for a reason unrelated to what it asserts.
    """
    d = d or load_driver()
    payload = {
        "manifest_hash": manifest_hash,
        "runtime_hash": identity.get("runtime_hash", d.runtime_hash(RUNTIME_ROOT)[0]),
        "driver_hash": identity.get("driver_hash", d.file_hash(DRIVER)),
        "records": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_results_carry_the_digest_of_the_bytes_actually_loaded(tmp_path: Path, monkeypatch):
    """Not the manifest's own self-declared field.

    A `manifest_hash` written *inside* a manifest is a claim the file makes
    about itself: it does not change when the file does, and it can be copied
    into a different manifest wholesale. Hashing the bytes read makes the stamp
    a measurement.
    """
    import hashlib

    d = load_driver()
    manifest = manifest_with(tmp_path, signature="AssertionError: boom")
    manifest["manifest_hash"] = "a-value-the-file-asserts-about-itself"
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    out = tmp_path / "r.json"

    monkeypatch.setattr(
        d,
        "_rift",
        lambda args, cwd, timeout=3600.0, env=None: fake_proc(
            stdout="usage: rift fix\n" + json.dumps({"verdict": "abstained", "task_id": "t1"})
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "driver",
            "--manifest",
            str(path),
            "--out",
            str(out),
            "--work",
            str(tmp_path),
            "--repos",
            str(tmp_path / "repos"),
        ],
    )
    assert d.main() == 0

    stamped = json.loads(out.read_text(encoding="utf-8"))["manifest_hash"]
    assert stamped == hashlib.sha256(path.read_bytes()).hexdigest()
    assert stamped != manifest["manifest_hash"], "the self-declared field was copied instead of measured"


def test_report_only_accepts_the_manifest_the_run_used(tmp_path: Path, monkeypatch, capsys):
    d = load_driver()
    manifest = manifest_with(tmp_path, signature="AssertionError: boom")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    out = write_results(tmp_path / "r.json", d.load_manifest(path)[1])

    monkeypatch.setattr(
        sys,
        "argv",
        ["driver", "--manifest", str(path), "--out", str(out), "--report-only", "--repos", str(tmp_path / "repos")],
    )
    assert d.main() == 0
    assert "REFUSING" not in capsys.readouterr().out


def test_report_only_refuses_a_different_manifest(tmp_path: Path, monkeypatch, capsys):
    """A report derived from one manifest and labelled with another is worse
    than no report: every per-class figure would be attributed to cases that
    may not be in the file being read."""
    d = load_driver()
    ran = manifest_with(tmp_path, signature="AssertionError: boom")
    ran_path = tmp_path / "ran.json"
    ran_path.write_text(json.dumps(ran), encoding="utf-8")
    out = write_results(tmp_path / "r.json", d.load_manifest(ran_path)[1])

    other = manifest_with(tmp_path, signature="AssertionError: different")
    other_path = tmp_path / "other.json"
    other_path.write_text(json.dumps(other), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "driver",
            "--manifest",
            str(other_path),
            "--out",
            str(out),
            "--report-only",
            "--repos",
            str(tmp_path / "repos"),
        ],
    )
    assert d.main() == 2, "a mismatched manifest was reported against"
    printed = capsys.readouterr().out
    assert "REFUSING TO REPORT" in printed
    assert d.load_manifest(other_path)[1] in printed


def test_a_one_byte_manifest_edit_is_detected(tmp_path: Path, monkeypatch):
    """The check is on bytes, so a change too small to alter any field still
    invalidates the stamp."""
    d = load_driver()
    manifest = manifest_with(tmp_path, signature="AssertionError: boom")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    out = write_results(tmp_path / "r.json", d.load_manifest(path)[1])

    path.write_text(json.dumps(manifest) + " ", encoding="utf-8")  # one trailing space
    monkeypatch.setattr(
        sys,
        "argv",
        ["driver", "--manifest", str(path), "--out", str(out), "--report-only", "--repos", str(tmp_path / "repos")],
    )
    assert d.main() == 2


@pytest.mark.skipif(not MANIFEST.is_file(), reason="the preliminary manifest is not present")
def test_the_preliminary_budget_scope_describes_this_run():
    """The scope must state the corpus that exists.

    It said 27 arm-runs over 9 cases until `icalendar-30ec6eef` was quarantined
    for a fix-parent mismatch (DAR-023). A budget describing a denominator the
    corpus no longer has is the same class of error as the case itself: a
    statement nothing checks.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    scope = manifest["budget"]["scope"]
    valid = len(manifest["cases"])
    assert "90" not in scope, scope
    assert str(valid * 3) in scope, scope
    assert f"{valid} cases" in scope, scope
    assert manifest["corpus_status"]["valid_cases"] == valid
    assert manifest["budget"]["reservation_model"]["arm_runs"] == valid * 3


def test_a_manifest_edited_during_the_run_does_not_change_the_recorded_identity(tmp_path: Path, monkeypatch):
    """The run identity is the bytes this process loaded, not whatever is on
    disk when it finishes.

    Rehashing the file at completion looks equivalent and is not: a manifest
    replaced mid-run would be executed as X and stamped as Y, and
    `--report-only Y` would then accept a report describing an experiment that
    never ran against Y. The mutation below happens between startup and the
    results being written, which is precisely the window that was unguarded.
    """
    import hashlib

    d = load_driver()
    manifest = manifest_with(tmp_path, signature="AssertionError: boom")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    out = tmp_path / "r.json"

    def mutating_rift(args, cwd, timeout=3600.0, env=None):
        # Someone edits the manifest while the arms are running.
        replacement = manifest_with(tmp_path, signature="AssertionError: different")
        path.write_text(json.dumps(replacement), encoding="utf-8")
        return fake_proc(stdout="usage: rift fix\n" + json.dumps({"verdict": "abstained", "task_id": "t1"}))

    monkeypatch.setattr(d, "_rift", mutating_rift)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "driver",
            "--manifest",
            str(path),
            "--out",
            str(out),
            "--work",
            str(tmp_path),
            "--repos",
            str(tmp_path / "repos"),
        ],
    )
    assert d.main() == 0

    replaced = hashlib.sha256(path.read_bytes()).hexdigest()
    assert replaced != original, "the fixture did not actually mutate the manifest; the test would prove nothing"

    stamped = json.loads(out.read_text(encoding="utf-8"))["manifest_hash"]
    assert stamped == original, "the results were stamped with the replacement rather than the manifest that ran"
    assert stamped != replaced


def test_report_only_rejects_the_replacement_after_such_an_edit(tmp_path: Path, monkeypatch, capsys):
    """The consequence of the property above: a report against the replacement
    is refused, because the run never used it."""
    d = load_driver()
    ran = manifest_with(tmp_path, signature="AssertionError: boom")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(ran), encoding="utf-8")
    out = write_results(tmp_path / "r.json", d.load_manifest(path)[1])

    path.write_text(json.dumps(manifest_with(tmp_path, signature="AssertionError: different")), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["driver", "--manifest", str(path), "--out", str(out), "--report-only", "--repos", str(tmp_path / "repos")],
    )
    assert d.main() == 2
    assert "REFUSING TO REPORT" in capsys.readouterr().out
