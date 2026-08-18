"""A signature-only reproducer must carry the same authority as a precondition one.

The previous correction called `_validate_reproducer` after execution and passed
it the digest captured *before* execution together with `expected_tree=None`.
That is the shape of a fix without its substance: the call exists, and both
authorities it applies are disabled — the source-drift comparison checks a value
against itself, and the phase-state comparison is skipped entirely.

So the tests here are behavioural. The fixture's target rewrites an ordinary
implementation file while it runs. That file is not a judge artifact, so artifact
hashing cannot see it; only a freshly observed digest and the phase-state hash
can. Two provenance tests then confirm each authority is load-bearing by
removing it and requiring a named test to go red.

No provider is configured and no request is made.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import build_repo, make_diff

MUTATING_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/impl.py": "VALUE = 1\n",
    "tests/test_mutator.py": (
        "import pathlib\n\n\n"
        "def test_mutator():\n"
        "    pathlib.Path('src/pkg/impl.py').write_text('VALUE = 2\\n')\n"
        "    assert False, 'boom'\n"
    ),
    "tests/test_keep.py": "def test_keep():\n    assert True\n",
}
TARGET = "tests/test_mutator.py::test_mutator"

# The anchor both provenance mutations edit. Kept in one place so a refactor
# that moves it fails loudly here rather than silently disarming the checks.
ANCHOR = (
    "            tree_hash(repo_root) if repo_root is not None else source_digest,\n"
    "            expected_tree,\n"
    '            "after",'
)


@pytest.fixture
def mutating_repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path / "mutating", MUTATING_FILES)


def events_of(repo: Path) -> list[dict]:
    out: list[dict] = []
    for path in (repo / ".rift").rglob("ledger.jsonl"):
        out.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return out


def receipt_of(out: str) -> dict:
    for line in reversed(out.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def test_a_signature_only_run_detects_a_phase_state_mutation(mutating_repo, run_cli, write_diff):
    """The behavioural check, asserted in the *baseline* phase.

    Later phases have their own integrity checks, so asserting merely that some
    detection occurred somewhere would pass with this authority removed.
    """
    diff = make_diff(mutating_repo, {"src/pkg/impl.py": "VALUE = 3\n"})
    code, out = run_cli(
        "--repo",
        str(mutating_repo),
        "--json",
        "verify",
        str(write_diff(diff)),
        TARGET,
        "--allow-partial-sandbox",
        "--expect-signature",
        "AssertionError",
    )
    events = events_of(mutating_repo)
    kinds = [e["kind"] for e in events]

    # The before-check passed and repository code actually ran.
    assert "episode_reset" in kinds, kinds
    assert "command_finished" in kinds, kinds

    baseline_end = next((i for i, e in enumerate(events) if e["kind"] == "gate_phase_finished"), len(events))
    detected = [
        i for i, e in enumerate(events[:baseline_end]) if e["kind"] in ("infrastructure_blocked", "reproducer_invalid")
    ]
    assert detected, f"the mutation was not detected during baseline: {[e['kind'] for e in events[:baseline_end]]}"
    assert receipt_of(out).get("verdict") != "verified_against_approved_checks"

    # Nothing supported was recorded after detection. An outcome recorded first
    # is evidence about a judge that has already changed.
    after = events[detected[0] + 1 :]
    assert not [e for e in after if e["kind"] == "check_result"], [e["kind"] for e in after]
    assert not [e for e in after if e["kind"] == "gate_phase_finished" and e["payload"].get("passed")], after


def test_the_digest_compared_after_execution_is_freshly_observed(mutating_repo, run_cli, write_diff, monkeypatch):
    """Provenance for the source digest.

    The phase-state authority alone would still catch the fixture above, so this
    asserts the other one directly: a tree observation must happen *after* the
    command finished. With the cached pre-execution digest, none does.
    """
    import riftagent.app as app

    trace: list[str] = []
    original_hash = app.tree_hash
    original_append = app.Flow.append

    monkeypatch.setattr(app, "tree_hash", lambda root: (trace.append("tree_hash"), original_hash(root))[1])
    monkeypatch.setattr(
        app.Flow,
        "append",
        lambda self, kind, payload=None: (trace.append(kind.value), original_append(self, kind, payload))[1],
    )

    diff = make_diff(mutating_repo, {"src/pkg/impl.py": "VALUE = 3\n"})
    run_cli(
        "--repo",
        str(mutating_repo),
        "--json",
        "verify",
        str(write_diff(diff)),
        TARGET,
        "--allow-partial-sandbox",
        "--expect-signature",
        "AssertionError",
    )
    assert "command_finished" in trace, trace[:40]
    tail = trace[trace.index("command_finished") :]
    assert "tree_hash" in tail, f"no tree was observed after execution: {tail[:20]}"


@pytest.mark.parametrize(
    "mutation, replacement, guarded_by",
    [
        (
            "the cached pre-execution digest replaces the fresh one",
            '            source_digest,\n            expected_tree,\n            "after",',
            "test_the_digest_compared_after_execution_is_freshly_observed",
        ),
        (
            "expected_tree is dropped",
            "            tree_hash(repo_root) if repo_root is not None else source_digest,\n"
            "            None,\n"
            '            "after",',
            "test_a_signature_only_run_detects_a_phase_state_mutation",
        ),
    ],
)
def test_each_after_check_authority_is_load_bearing(tmp_path, mutation, replacement, guarded_by):
    """Each mutation keeps the call and removes exactly one authority. If the
    named test still passes, that authority was decorative."""
    root = Path(__file__).resolve().parent.parent
    dst = tmp_path / "mutated"
    shutil.copytree(root, dst, ignore=shutil.ignore_patterns(".git", ".rift", "__pycache__", "*.egg-info", "build"))
    path = dst / "src" / "riftagent" / "app.py"
    source = path.read_text(encoding="utf-8")
    assert ANCHOR in source, f"anchor missing for {mutation!r}; the mutation would prove nothing"
    path.write_text(source.replace(ANCHOR, replacement, 1), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            f"tests/test_signature_only_integrity.py::{guarded_by}",
            "-q",
            "-p",
            "no:cacheprovider",
            "--no-header",
        ],
        cwd=dst,
        capture_output=True,
        text=True,
        timeout=900,
        # The mutated copy must be what the test imports; without this the edit
        # lands in a tree nobody loads and every mutation reads as undetected.
        env={**os.environ, "PYTHONPATH": str(dst / "src")},
    )
    assert proc.returncode != 0, f"{mutation} was not detected by {guarded_by}:\n{proc.stdout[-1200:]}"
