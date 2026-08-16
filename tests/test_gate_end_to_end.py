"""V-02 … V-07: the counterfactual gate against temporary real repositories.

Every test here drives the public CLI. Nothing reaches into the flow.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from riftagent.records import EventKind, GatePhase, Verdict, read_events
from tests.conftest import ORDER_TARGET, SIMPLE_PRESERVE, SIMPLE_TARGET, make_diff, suite_passes


def latest_task(repo: Path) -> Path:
    tasks = sorted((repo / ".rift" / "tasks").iterdir())
    assert tasks, "no task directory was created"
    return tasks[-1]


def receipt_of(repo: Path) -> dict:
    return json.loads((latest_task(repo) / "receipt.json").read_text(encoding="utf-8"))


def events_of(repo: Path):
    return read_events(latest_task(repo) / "ledger.jsonl")[0]


def kinds_of(repo: Path) -> list[str]:
    return [e.kind.value for e in events_of(repo)]


def phases(repo: Path) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for ev in events_of(repo):
        if ev.kind is EventKind.GATE_PHASE_FINISHED:
            out[ev.payload["phase"]] = ev.payload["passed"]
    return out


# ---------------------------------------------------------------- V-02, V-03


def test_v02_baseline_failure_reproduces_and_freezes_its_signature(simple_repo, correct_diff, write_diff, run_cli):
    code, _ = run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    assert code == 0
    frozen = [e for e in events_of(simple_repo) if e.kind is EventKind.SIGNATURE_FROZEN]
    assert len(frozen) == 1, "the baseline signature must be frozen exactly once"
    # pytest reports a bare `assert` without an exception name, so the type is
    # the generic one and the discriminating content is the message.
    signature = frozen[0].payload["signature"]
    assert signature["exception_type"] == "Failure"
    assert signature["message"] == "assert 10 == 11"
    assert phases(simple_repo)[GatePhase.BASELINE.value] is True


def test_v02_named_exception_types_are_captured(order_repo, order_fix_diff, write_diff, run_cli):
    """Where pytest names the exception, the signature must carry it: the
    identity of a failure is the evidence, not merely its existence."""
    run_cli(
        "--repo", str(order_repo), "verify", str(write_diff(order_fix_diff)), ORDER_TARGET, "--allow-partial-sandbox"
    )
    frozen = [e for e in events_of(order_repo) if e.kind is EventKind.SIGNATURE_FROZEN]
    signature = frozen[0].payload["signature"]
    assert signature["exception_type"] == "AttributeError"
    # Captured from the traceback, not the width-truncated short summary.
    assert signature["message"] == "module 'pkg' has no attribute '_impl'"
    assert "..." not in signature["message"]


def test_v03_correct_patch_passes_the_whole_gate(simple_repo, correct_diff, write_diff, run_cli):
    diff_path = write_diff(correct_diff)
    code, out = run_cli(
        "--repo",
        str(simple_repo),
        "verify",
        str(diff_path),
        SIMPLE_TARGET,
        "--preserve",
        SIMPLE_PRESERVE,
        "--allow-partial-sandbox",
    )
    assert code == 0, out
    receipt = receipt_of(simple_repo)
    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert receipt["patch_hash"]
    done = phases(simple_repo)
    for phase in GatePhase:
        assert done.get(phase.value) is True, f"{phase.value} did not pass"


# ---------------------------------------------------------------- V-04, V-05


def test_v04_withdrawal_restores_the_original_failure_signature(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    results = [e.payload["result"] for e in events_of(simple_repo) if e.kind is EventKind.CHECK_RESULT]
    baseline = next(r for r in results if r["phase"] == "baseline")
    withdrawal = next(r for r in results if r["phase"] == "withdrawal")
    assert baseline["outcome"] == "failed" and withdrawal["outcome"] == "failed"
    assert baseline["signature"] == withdrawal["signature"], "withdrawal must restore the same failure, not any failure"


def test_v05_exact_patch_is_reapplied_before_preservation(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo",
        str(simple_repo),
        "verify",
        str(write_diff(correct_diff)),
        SIMPLE_TARGET,
        "--preserve",
        SIMPLE_PRESERVE,
        "--allow-partial-sandbox",
    )
    trees = {}
    order = []
    for ev in events_of(simple_repo):
        if ev.kind is EventKind.GATE_PHASE_FINISHED:
            order.append(ev.payload["phase"])
            if ev.payload["artifacts"].get("tree_hash"):
                trees[ev.payload["phase"]] = ev.payload["artifacts"]["tree_hash"]
        if ev.kind is EventKind.CHECK_RESULT and ev.payload["result"]["phase"] == "preservation":
            assert "reapply" in order, "a preservation check ran before the patch was reapplied"
    assert trees["candidate"] == trees["reapply"], "reapplied tree differs from the gated candidate tree"
    assert trees["withdrawal"] == trees["baseline"], "withdrawal did not restore the baseline tree"


# ---------------------------------------------------------------- V-06


@pytest.mark.parametrize(
    "node_id",
    ["tests/test_calc.py::test_does_not_exist", "tests/test_missing_file.py::test_x"],
)
def test_v06_unobservable_target_cannot_satisfy_the_gate(simple_repo, correct_diff, write_diff, run_cli, node_id):
    code, _ = run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), node_id, "--allow-partial-sandbox"
    )
    receipt = receipt_of(simple_repo)
    assert receipt["verdict"] == Verdict.INFRASTRUCTURE_BLOCKED.value
    assert code == 3
    assert phases(simple_repo).get(GatePhase.BASELINE.value) is False


def test_v06_import_error_is_not_a_target_failure(tmp_path, write_diff, run_cli):
    from tests.conftest import SIMPLE_FILES, build_repo

    broken = dict(SIMPLE_FILES)
    broken["tests/test_calc.py"] = "import does_not_exist_anywhere\n\n\ndef test_total():\n    assert False\n"
    repo = build_repo(tmp_path / "broken", broken)
    diff = make_diff(repo, {"src/pkg/calc.py": "def total():\n    return 11\n"})
    code, _ = run_cli("--repo", str(repo), "verify", str(write_diff(diff)), SIMPLE_TARGET, "--allow-partial-sandbox")
    assert code == 3
    assert receipt_of(repo)["verdict"] == Verdict.INFRASTRUCTURE_BLOCKED.value


def test_already_passing_target_is_not_a_verified_fix(simple_repo, write_diff, run_cli):
    diff = make_diff(simple_repo, {"src/pkg/util.py": "def double(x):\n    return x * 2  # touched\n"})
    code, _ = run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(diff)), SIMPLE_PRESERVE, "--allow-partial-sandbox"
    )
    receipt = receipt_of(simple_repo)
    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert "reproduction was not established" in receipt["reason"]
    assert code == 2


# ---------------------------------------------------------------- V-07


def _apply_to_copy(repo: Path, diff: str, dest: Path) -> Path:
    shutil.copytree(repo, dest)
    patch = dest / "candidate.diff"
    patch.write_text(diff, encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(dest), "apply", str(patch)], check=True, capture_output=True)
    patch.unlink()
    return dest


def test_v07_semantically_inert_order_masked_patch_is_rejected(order_repo, inert_diff, write_diff, run_cli, tmp_path):
    """The measured failure mode, run through the product.

    The standard protocol — apply the change, run the suite — accepts a comment
    as a fix, because a neighbouring module supplies the missing import. The
    gate runs the target in isolation and rejects it.
    """
    patched = _apply_to_copy(order_repo, inert_diff, tmp_path / "protocol_arm")
    assert suite_passes(patched), "fixture is wrong: the suite must accept the inert patch"

    code, _ = run_cli(
        "--repo", str(order_repo), "verify", str(write_diff(inert_diff)), ORDER_TARGET, "--allow-partial-sandbox"
    )
    receipt = receipt_of(order_repo)
    assert receipt["verdict"] == Verdict.UNVERIFIABLE.value
    assert receipt["rejected_phase"] == GatePhase.CANDIDATE.value
    assert code == 2
    assert phases(order_repo)[GatePhase.CANDIDATE.value] is False


def test_v07_real_fix_for_the_same_failure_is_accepted(order_repo, order_fix_diff, write_diff, run_cli):
    """The gate must not simply be strict: the genuine fix has to pass."""
    code, out = run_cli(
        "--repo", str(order_repo), "verify", str(write_diff(order_fix_diff)), ORDER_TARGET, "--allow-partial-sandbox"
    )
    assert code == 0, out
    assert receipt_of(order_repo)["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value


def test_unrelated_patch_is_rejected_at_the_candidate_phase(simple_repo, unrelated_diff, write_diff, run_cli):
    code, _ = run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(unrelated_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    assert code == 2
    assert receipt_of(simple_repo)["rejected_phase"] == GatePhase.CANDIDATE.value


def test_judge_weakening_patch_is_rejected_before_anything_runs(simple_repo, judge_diff, write_diff, run_cli):
    code, _ = run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(judge_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    assert code == 2
    kinds = kinds_of(simple_repo)
    assert EventKind.CHANGESET_REJECTED.value in kinds
    assert EventKind.COMMAND_STARTED.value not in kinds, "a rejected patch must never reach execution"


def test_regression_is_blocked_and_not_silently_repaired(simple_repo, regression_diff, write_diff, run_cli):
    code, _ = run_cli(
        "--repo",
        str(simple_repo),
        "verify",
        str(write_diff(regression_diff)),
        SIMPLE_TARGET,
        "--preserve",
        SIMPLE_PRESERVE,
        "--allow-partial-sandbox",
    )
    receipt = receipt_of(simple_repo)
    assert receipt["verdict"] == Verdict.REGRESSION_BLOCKED.value
    assert code == 2
    assert phases(simple_repo)[GatePhase.CANDIDATE.value] is True, "the change claim itself held"
    assert phases(simple_repo)[GatePhase.PRESERVATION.value] is False


def test_receipt_discloses_that_no_preservation_checks_were_declared(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    receipt = receipt_of(simple_repo)
    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert any("preservation" in item for item in receipt["checks_not_executed"])
    assert any("no preservation checks were declared" in n for n in receipt["remaining_uncertainty"])
    assert any("full repository suite" in item for item in receipt["checks_not_executed"])


def test_no_bare_verified_verdict_exists(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    receipt = receipt_of(simple_repo)
    assert receipt["verdict"] not in ("verified", "done", "ok", "success")
    assert receipt["verdict"].endswith("against_approved_checks")


def test_repro_and_artifacts_are_written(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    td = latest_task(simple_repo)
    for name in (
        "ledger.jsonl",
        "receipt.json",
        "receipt.txt",
        "transcript.txt",
        "task-contract.json",
        "check-set.json",
        "change-set.diff",
        "repro.sh",
    ):
        assert (td / name).is_file(), f"{name} missing"
    assert (td / "change-set.diff").read_text(encoding="utf-8") == correct_diff


def test_environment_allowlist_excludes_credentials(simple_repo, write_diff, run_cli, monkeypatch):
    """A sentinel secret in the parent environment must not reach the child."""
    monkeypatch.setenv("RIFT_LLM_KEY", "sentinel-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sentinel-must-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "sentinel-must-not-leak")
    files_probe = (
        "import os\n\n\ndef test_no_secret():\n"
        "    leaked = [k for k, v in os.environ.items() if 'sentinel' in v]\n"
        "    assert leaked == [], leaked\n"
        "    assert False, 'probe always fails so it is a valid change check'\n"
    )
    (simple_repo / "tests" / "test_env_probe.py").write_text(files_probe, encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(simple_repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(simple_repo), "commit", "-q", "-m", "probe"],
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    diff = make_diff(simple_repo, {"src/pkg/calc.py": "def total():\n    return 11\n"})
    run_cli(
        "--repo",
        str(simple_repo),
        "verify",
        str(write_diff(diff)),
        "tests/test_env_probe.py::test_no_secret",
        "--allow-partial-sandbox",
    )
    results = [e.payload["result"] for e in events_of(simple_repo) if e.kind is EventKind.CHECK_RESULT]
    baseline = next(r for r in results if r["phase"] == "baseline")
    assert baseline["outcome"] == "failed"
    assert "sentinel" not in json.dumps(baseline), "a credential reached the repository process"


def test_ledger_records_no_model_activity(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    blob = (latest_task(simple_repo) / "ledger.jsonl").read_text(encoding="utf-8")
    for marker in ("model_request", "propose_", "llm", "openai", "anthropic", "chat/completions"):
        assert marker not in blob.lower(), f"ledger mentions {marker}"
    assert receipt_of(simple_repo)["tokens"].startswith("not_applicable")
