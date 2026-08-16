"""Fixtures: temporary real git repositories and real diffs.

Diffs are produced by `git diff` against the actual fixture rather than
hand-written, so a test can never pass because its context lines happened to
be wrong in the same way the parser is.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from riftagent.records import IsolationLevel
from riftagent.sandbox import IsolationProbe

# ---------------------------------------------------------------- fixture source

SIMPLE_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/calc.py": "def total():\n    return sum(range(5))\n",
    "src/pkg/util.py": "def double(x):\n    return x * 2\n",
    "tests/test_calc.py": "from pkg.calc import total\n\n\ndef test_total():\n    assert total() == 11\n",
    "tests/test_other.py": "from pkg.util import double\n\n\ndef test_double():\n    assert double(3) == 6\n",
}

# The click R1 shape: `pkg._impl` is only reachable as an attribute once some
# other module has imported it, so the target passes in a full-suite run and
# fails in isolation. Any verification protocol that runs the suite accepts an
# irrelevant edit as a fix.
ORDER_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/_impl.py": "VALUE = 42\n",
    "src/pkg/api.py": "import pkg\n\n\ndef get():\n    return pkg._impl.VALUE\n",
    "tests/test_helper.py": "import pkg._impl\n\n\ndef test_helper():\n    assert pkg._impl.VALUE == 42\n",
    "tests/test_target.py": "from pkg.api import get\n\n\ndef test_target():\n    assert get() == 42\n",
}

SIMPLE_TARGET = "tests/test_calc.py::test_total"
SIMPLE_PRESERVE = "tests/test_other.py::test_double"
ORDER_TARGET = "tests/test_target.py::test_target"


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "rift tests",
            "GIT_AUTHOR_EMAIL": "tests@riftagent.invalid",
            "GIT_COMMITTER_NAME": "rift tests",
            "GIT_COMMITTER_EMAIL": "tests@riftagent.invalid",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


def build_repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def make_diff(repo: Path, edits: dict[str, str]) -> str:
    """Write edits, capture a real unified diff, restore the tree."""
    for rel, body in edits.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    diff = _git(repo, "diff", "--no-color", "--no-ext-diff")
    _git(repo, "checkout", "--", ".")
    return diff


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def simple_repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path / "simple", SIMPLE_FILES)


@pytest.fixture
def order_repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path / "order", ORDER_FILES)


@pytest.fixture
def correct_diff(simple_repo: Path) -> str:
    return make_diff(simple_repo, {"src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n"})


@pytest.fixture
def unrelated_diff(simple_repo: Path) -> str:
    return make_diff(simple_repo, {"src/pkg/util.py": "def double(x):\n    # unrelated edit\n    return x * 2\n"})


@pytest.fixture
def regression_diff(simple_repo: Path) -> str:
    return make_diff(
        simple_repo,
        {
            "src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n",
            "src/pkg/util.py": "def double(x):\n    return x * 3\n",
        },
    )


@pytest.fixture
def judge_diff(simple_repo: Path) -> str:
    """The patch that edits the test stating the claim."""
    return make_diff(
        simple_repo,
        {"tests/test_calc.py": "from pkg.calc import total\n\n\ndef test_total():\n    assert total() == 10\n"},
    )


@pytest.fixture
def inert_diff(order_repo: Path) -> str:
    """Semantically inert: a comment. Accepted by suite-based protocols."""
    return make_diff(
        order_repo,
        {"src/pkg/api.py": "import pkg\n\n\ndef get():\n    # fix applied here\n    return pkg._impl.VALUE\n"},
    )


@pytest.fixture
def order_fix_diff(order_repo: Path) -> str:
    return make_diff(
        order_repo,
        {"src/pkg/api.py": "from pkg import _impl\n\n\ndef get():\n    return _impl.VALUE\n"},
    )


@pytest.fixture
def write_diff(tmp_path: Path):
    counter = {"n": 0}

    def _write(diff: str) -> Path:
        counter["n"] += 1
        path = tmp_path / f"patch{counter['n']}.diff"
        path.write_text(diff, encoding="utf-8", newline="\n")
        return path

    return _write


@pytest.fixture
def partial_probe(monkeypatch):
    """Force the partial-isolation tier so authority rules are testable on any
    platform, rather than only where bubblewrap happens to be missing."""
    probe = IsolationProbe(IsolationLevel.PARTIAL, "forced partial for test", rlimits=False, tree_kill=True)
    monkeypatch.setattr("riftagent.app.probe_isolation", lambda: probe)
    return probe


@pytest.fixture
def run_cli(capsys):
    from riftagent.app import main

    def _run(*args: str) -> tuple[int, str]:
        code = main(list(args))
        captured = capsys.readouterr()
        _run.err = captured.err  # type: ignore[attr-defined]
        return code, captured.out

    _run.err = ""  # type: ignore[attr-defined]
    return _run


def suite_passes(repo: Path) -> bool:
    """The 'standard protocol' arm: apply the change, run the whole suite."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(repo / "src")},
    )
    return proc.returncode == 0
