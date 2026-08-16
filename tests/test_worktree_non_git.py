"""The non-git path: a repository that is only a directory.

`Worktree` prefers `git worktree add` and falls back to a disposable copy.
The fallback had no coverage, which meant half of the documented sandbox
behaviour was assumed rather than executed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from riftagent.records import Verdict
from riftagent.sandbox import Worktree, is_git_repo, tree_hash
from tests.conftest import SIMPLE_FILES, SIMPLE_TARGET, make_diff
from tests.test_gate_end_to_end import receipt_of


def _plain_dir(tmp_path: Path) -> Path:
    root = tmp_path / "plain"
    for rel, body in SIMPLE_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    return root


def test_worktree_falls_back_to_a_disposable_copy(tmp_path: Path):
    root = _plain_dir(tmp_path)
    assert not is_git_repo(root)
    with Worktree(root, "copy") as wt:
        assert wt.path.is_dir()
        assert wt.path.resolve() != root.resolve(), "the copy must not be the repository itself"
        assert (wt.path / "src" / "pkg" / "calc.py").read_text(encoding="utf-8") == SIMPLE_FILES["src/pkg/calc.py"]
        assert not (wt.path / ".git").exists()
        assert wt.hash() == tree_hash(root)
        disposable = wt.path
    assert not disposable.exists(), "the copy must be removed on disposal"


def test_copy_worktree_applies_and_reverses_a_patch(tmp_path: Path, simple_repo: Path):
    """Patches apply outside a git repository too: `git apply` operates on a
    directory, so the counterfactual works without version control."""
    diff = make_diff(simple_repo, {"src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n"})
    root = _plain_dir(tmp_path)
    before = tree_hash(root)
    with Worktree(root, "copy") as wt:
        wt.apply_patch(diff)
        patched = (wt.path / "src" / "pkg" / "calc.py").read_text(encoding="utf-8")
        assert "+ 1" in patched
        assert wt.hash() != before
        wt.apply_patch(diff, reverse=True)
        assert wt.hash() == before, "withdrawal must restore the original tree"


def test_verify_gates_a_non_git_repository_end_to_end(tmp_path: Path, simple_repo: Path, run_cli):
    diff = make_diff(simple_repo, {"src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n"})
    root = _plain_dir(tmp_path)
    patch = tmp_path / "fix.diff"
    patch.write_text(diff, encoding="utf-8", newline="\n")
    code, out = run_cli("--repo", str(root), "verify", str(patch), SIMPLE_TARGET, "--allow-partial-sandbox")
    assert code == 0, out
    assert receipt_of(root)["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value


def test_rift_state_never_enters_the_copy(tmp_path: Path):
    """`.rift/` is the agent's own state and must not become part of the tree
    under test, or a task would observe its own evidence."""
    root = _plain_dir(tmp_path)
    (root / ".rift" / "tasks").mkdir(parents=True)
    (root / ".rift" / "tasks" / "marker.json").write_text("{}", encoding="utf-8")
    hashed_with_state = tree_hash(root)
    with Worktree(root, "copy") as wt:
        assert not (wt.path / ".rift").exists()
    shutil.rmtree(root / ".rift")
    assert tree_hash(root) == hashed_with_state, ".rift must be excluded from the tree hash"
