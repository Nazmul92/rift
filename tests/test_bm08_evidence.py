"""BM-08 evidence retention: keep what the summary was derived from.

BM-07 summarised each arm's task ledger and candidate diffs into a result
record, then deleted the disposable worktree. The summary is not re-derivable
from itself, so what the model was actually asked, what it actually returned,
and what each pipeline stage made of it existed only for the length of the run.
A reviewer who wants to check a verdict had nothing to check it against.

This is the only behavioural difference between the BM-07 and BM-08 harnesses.
Evaluation, verification, oracle and transaction semantics are byte-identical,
and the tests here assert that too — a fork that quietly drifted would make the
two benchmarks incomparable, which is the whole reason for forking rather than
editing BM-07 in place.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

BENCH = Path(__file__).parents[1] / "benchmark"
for extra in (BENCH / "bm07", BENCH / "bm08"):
    if str(extra) not in sys.path:
        sys.path.append(str(extra))

import bm07_runner as bm07  # noqa: E402
import bm08_runner as runner  # noqa: E402

BM07_FROZEN_RUNNER = "d1b2fd312d2eacd7436f7401981c302bc2fb025f9bb675680ea15ab93212da48"


# ------------------------------------------------------- BM-07 stays frozen


def test_the_bm07_harness_is_untouched_by_the_fork():
    """BM-07's evidence is verified against the exact bytes that produced it."""
    assert bm07.runner_hash() == BM07_FROZEN_RUNNER
    frozen = json.loads((BENCH / "bm07" / "manifest-executable.json").read_text(encoding="utf-8"))
    assert frozen["runner_hash"] == BM07_FROZEN_RUNNER
    _, drift = bm07.aggregate(BENCH / "bm07" / "results.jsonl", frozen)
    assert drift == [], f"BM-07's own aggregation no longer accepts its evidence: {drift}"


def test_the_two_harnesses_are_separate_identities():
    assert runner.runner_hash() != bm07.runner_hash()
    assert runner.runner_hash() == hashlib.sha256((BENCH / "bm08" / "bm08_runner.py").read_bytes()).hexdigest()


def test_the_fork_kept_the_approved_transaction_semantics():
    """Transaction semantics are shared; the arm set is not one of them.

    This originally asserted `OFFICIAL_ARMS` matched BM-07's, which was true by
    accident rather than by design — BM-08 inherited BM-07's three arms before
    its own arm plan was settled. BM-08 asks whether full RIFT beats the same
    model alone, so its official arms are A and C; arm B answers BM-07's
    question. Pinning the arm set here would have made a scientific-protocol
    decision look like a transaction-integrity regression.

    What must stay identical is the machinery that protects money and evidence:
    terminal states, exit codes, and the reconciliation/completeness surface.
    """
    assert runner.OFFICIAL_ARMS == ("A", "C"), "BM-08's official arms are A and C"
    assert bm07.OFFICIAL_ARMS == ("A", "B", "C"), "BM-07's frozen arms must not have moved"
    assert runner.TERMINAL_STATES == bm07.TERMINAL_STATES
    for name in ("EXIT_OK", "EXIT_REFUSED", "EXIT_RECONCILE", "EXIT_NO_OFFICIAL_SCORE"):
        assert getattr(runner, name) == getattr(bm07, name), name
    for name in ("unreconciled", "terminal_state", "expected_pairs", "official_status", "required_reservation"):
        assert hasattr(runner, name), name


def undo_the_approved_oracle_difference(text: str) -> str:
    """Reverse the single approved BM-08 oracle change: network confinement.

    BM-08 runs repository tests network-denied, matching the environment its
    corpus was admitted in; BM-07 did not. That is a change to *where* the
    project's own tests execute, never to what counts as correct. Reversing it
    textually keeps the equality assertion below exact instead of loosening it
    into a subset check that a second, unapproved drift could hide behind.
    """
    text = text.replace("import confinement\n", "")
    text = re.sub(
        r'(def _pytest\([^)]*\) -> subprocess\.CompletedProcess:\n)    """.*?"""\n',
        r"\1",
        text,
        flags=re.S,
    )
    text = text.replace("return confinement.run_repository_check(", "return _run(")
    text = text.replace(
        '        {"PYTHONPATH": str(src.resolve())},\n        timeout=TIMEOUT,\n',
        '        {"PYTHONPATH": str(src.resolve())},\n',
    )
    return text


def test_the_oracle_fork_is_semantically_identical_to_the_bm07_oracle():
    """Same ground truth, or the two benchmarks measure different things."""
    seven = (BENCH / "bm07" / "bm07_oracle.py").read_text(encoding="utf-8")
    eight = (BENCH / "bm08" / "bm08_oracle.py").read_text(encoding="utf-8")
    normalised = eight.replace("bm08", "bm07").replace("BM-08", "BM-07").replace("BM08", "BM07")

    def code(text: str) -> list[str]:
        # The fork provenance note is the only permitted addition, so top-level
        # comments and the blank lines around them are ignored; everything the
        # oracle actually executes must be identical.
        return [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]

    assert code(undo_the_approved_oracle_difference(normalised)) == code(seven), (
        "the BM-08 oracle drifted from the BM-07 oracle beyond network confinement"
    )


def test_the_only_oracle_difference_is_network_confinement():
    """Pin the exception so a second one cannot hide behind the first."""
    eight = (BENCH / "bm08" / "bm08_oracle.py").read_text(encoding="utf-8")
    assert "confinement.run_repository_check" in eight
    # Correctness criteria are untouched: only the pytest invocation moved.
    assert "_run(" in undo_the_approved_oracle_difference(eight)
    assert "confinement" not in undo_the_approved_oracle_difference(eight)


# ----------------------------------------------------- the retained evidence


def build_task(tree: Path, task_id: str, attempts: int = 1) -> None:
    task = tree / ".rift" / "tasks" / task_id
    task.mkdir(parents=True, exist_ok=True)
    (task / "ledger.jsonl").write_text(json.dumps({"kind": "model_response_received"}) + "\n", encoding="utf-8")
    for n in range(1, attempts + 1):
        attempt = task / f"candidate-attempt-{n:03d}"
        attempt.mkdir(parents=True, exist_ok=True)
        for stage in ("raw", "normalized", "canonical"):
            (attempt / f"{stage}.diff").write_text(f"{stage} bytes for attempt {n}\n", encoding="utf-8")


def test_the_ledger_and_every_pipeline_stage_survive_the_worktree(tmp_path):
    results = tmp_path / "r.jsonl"
    tree = tmp_path / "work" / "case-A"
    build_task(tree, "task-1")

    dest, ok = runner.preserve_arm_evidence(results, "case-1", "A", tree, "task-1")
    assert ok

    assert (dest / "ledger.jsonl").is_file()
    for stage in ("raw", "normalized", "canonical"):
        assert (dest / f"{stage}.diff").read_text(encoding="utf-8") == f"{stage} bytes for attempt 1\n"


def test_the_evidence_lives_outside_the_worktree(tmp_path):
    """It must survive the tree being destroyed — that is the entire point."""
    import shutil

    results = tmp_path / "r.jsonl"
    tree = tmp_path / "work" / "case-A"
    build_task(tree, "task-1")
    dest, ok = runner.preserve_arm_evidence(results, "case-1", "A", tree, "task-1")
    assert ok
    shutil.rmtree(tree)

    assert not tree.exists()
    assert (dest / "canonical.diff").is_file()
    assert runner.evidence_root(results) == tmp_path / "r-evidence"


def test_a_second_attempt_is_kept_without_overwriting_the_first(tmp_path):
    results = tmp_path / "r.jsonl"
    tree = tmp_path / "work" / "case-A"
    build_task(tree, "task-1", attempts=2)

    dest, ok = runner.preserve_arm_evidence(results, "case-1", "A", tree, "task-1")
    assert ok

    assert (dest / "raw.diff").read_text(encoding="utf-8").endswith("attempt 1\n")
    assert (dest / "candidate-attempt-002-raw.diff").read_text(encoding="utf-8").endswith("attempt 2\n")


def test_an_arm_with_no_candidate_still_gets_its_ledger(tmp_path):
    """An abstention is evidence too, and its ledger is the only record of why."""
    results = tmp_path / "r.jsonl"
    tree = tmp_path / "work" / "case-A"
    task = tree / ".rift" / "tasks" / "task-1"
    task.mkdir(parents=True)
    (task / "ledger.jsonl").write_text("{}\n", encoding="utf-8")

    dest, ok = runner.preserve_arm_evidence(results, "case-1", "A", tree, "task-1")
    assert ok

    assert (dest / "ledger.jsonl").is_file()
    assert not (dest / "canonical.diff").exists()


def test_retention_never_fails_the_arm(tmp_path):
    """Losing evidence is bad; failing a paid arm because a copy failed is worse."""
    results = tmp_path / "r.jsonl"
    missing = tmp_path / "work" / "gone"
    dest, ok = runner.preserve_arm_evidence(results, "case-1", "A", missing, "task-1")
    assert dest.is_dir() and ok, "a missing task dir is not a retention failure"
    other, ok2 = runner.preserve_arm_evidence(results, "case-1", "B", missing, "")
    assert other == dest.parent / "B" and ok2


def test_result_json_is_written_beside_the_evidence(tmp_path):
    results = tmp_path / "r.jsonl"
    record = runner.ArmRecord(
        benchmark_id="BM-08",
        case_id="case-1",
        arm="A",
        runtime_hash="rt",
        driver_hash="dv",
        runner_hash="rn",
        oracle_hash="or",
        manifest_hash="mf",
        baseline_tree_hash="bt",
        requested_model="m",
    )
    runner.write_arm_result_json(results, record)
    written = json.loads((runner.evidence_root(results) / "case-1" / "A" / "result.json").read_text(encoding="utf-8"))
    assert written["case_id"] == "case-1" and written["arm"] == "A"


def test_the_durable_results_file_is_still_written_before_result_json():
    """Structural: the append-only record stays the authority."""
    source = Path(runner.__file__).read_text(encoding="utf-8")
    body = source.split("def run(")[1]
    assert body.index("append_record(results, record)") < body.index("write_arm_result_json(")


# ------------------------------- baselines are immune to dirty source checkouts


def commit(repo: Path, message: str) -> str:
    import subprocess

    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
    }

    def run(*args: str) -> str:
        import os

        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env={**os.environ, **env})
        assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
        return p.stdout.strip()

    run("add", "-A")
    run("commit", "-q", "-m", message)
    return run("rev-parse", "HEAD")


def test_a_dirty_source_checkout_cannot_reach_a_baseline(tmp_path):
    """Six BM-08 source repositories carry staged edits from earlier work.

    A baseline built by copying the checkout would silently inherit them, and
    every downstream verdict would be measured against a tree that never existed
    upstream. `git clone --shared` takes committed objects only, and
    `checkout --force <parent>` pins the exact commit — this asserts that rather
    than trusting it.
    """
    import subprocess

    import bm08_driver as driver

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (repo / "tests" / "test_m.py").write_text(
        "from pkg.m import f\n\n\ndef test_old():\n    assert f() == 1\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    parent = commit(repo, "parent")

    (repo / "tests" / "test_m.py").write_text(
        "from pkg.m import f\n\n\ndef test_old():\n    assert f() == 1\n\n\ndef test_new():\n    assert f() == 2\n",
        encoding="utf-8",
    )
    (repo / "pkg" / "m.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    fix = commit(repo, "fix")

    # Now dirty the checkout exactly as the real volume is dirtied: a staged
    # edit that is committed nowhere.
    (repo / "pkg" / "m.py").write_text("def f():\n    return 999  # staged, never committed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)

    case = {
        "case_id": "dirty-check",
        "repository": "repo",
        "parent": parent,
        "fix_commit": fix,
        "test_files": ["tests/test_m.py"],
    }
    dest = driver.materialise_baseline(case, tmp_path, tmp_path / "baseline")

    source = (dest / "pkg" / "m.py").read_text(encoding="utf-8")
    assert "999" not in source, "a staged edit from the source checkout reached the baseline"
    assert source == "def f():\n    return 1\n", "the baseline is not the exact parent source"
    # The reproducer half is present; the source fix is not.
    assert "test_new" in (dest / "tests" / "test_m.py").read_text(encoding="utf-8")


# ------------------------------------------- the cleanup safeguard (v2 §18)


def test_a_failed_evidence_copy_retains_the_worktree(tmp_path, monkeypatch, capsys):
    """The tree is disposable only once its evidence is somewhere else.

    If the copy fails, the worktree *is* the evidence, and deleting it would
    leave a paid arm unreviewable. The arm is neither failed nor retried for
    this: a filesystem problem is not a verdict.
    """
    import shutil as real_shutil

    results = tmp_path / "r.jsonl"
    tree = tmp_path / "work" / "case-A"
    build_task(tree, "task-1")

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(runner.shutil, "copy2", explode)
    dest, ok = runner.preserve_arm_evidence(results, "case-1", "A", tree, "task-1")

    assert ok is False, "a failed copy must report failure so the caller keeps the tree"
    assert "retaining worktree" in capsys.readouterr().out
    assert tree.is_dir(), "the source worktree must still exist"
    assert real_shutil  # the module itself is untouched


def test_the_runner_only_deletes_the_tree_when_retention_succeeded():
    """Structural: every teardown is guarded by the retention result."""
    source = Path(runner.__file__).read_text(encoding="utf-8")
    body = source.split("def run_case_arm(")[1].split("\ndef ")[0]
    for fragment in ("_, kept = preserve_arm_evidence", "_, retained = preserve_arm_evidence"):
        assert fragment in body, fragment
    assert "if kept:" in body and "if retained:" in body


def test_evidence_is_copied_not_moved(tmp_path):
    """A move would empty the worktree before the arm is finished with it."""
    results = tmp_path / "r.jsonl"
    tree = tmp_path / "work" / "case-A"
    build_task(tree, "task-1")

    runner.preserve_arm_evidence(results, "case-1", "A", tree, "task-1")

    task = tree / ".rift" / "tasks" / "task-1"
    assert (task / "ledger.jsonl").is_file(), "the source ledger was moved, not copied"
    assert (task / "candidate-attempt-001" / "canonical.diff").is_file()
