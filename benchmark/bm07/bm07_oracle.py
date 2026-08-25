"""The BM-07 ground-truth oracle. Deliberately knows nothing about RIFT.

BM-06 computed ground truth by asking the gate, then compared the gate's verdict
to it. That made `strong REJECT -> truth WRONG` true by construction, and any
"RIFT prevented a harmful acceptance" count derived from it was circular.

BM-07's third verdict must therefore be reachable without RIFT. This module
imports `git`, `pytest` and the standard library, and nothing else. It contains
no acceptance policy of its own beyond the frozen case oracle:

    the candidate applies cleanly
    no protected path is modified
    the target node passes
    every node in the COMPLETE frozen preservation set passes

Those four are what the corpus was validated against, so a candidate satisfying
them has reproduced the historical behaviour whether or not it resembles the
historical patch. A different implementation is allowed; nothing here compares
candidate text to the upstream fix, and the upstream fix is never read.

If this file ever imports `riftagent`, the benchmark's independence is gone and
a test in `tests/test_bm07_driver.py` fails.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

CHUNK = 40
TIMEOUT = 1800

# The frozen runner-configuration policy. A candidate that edits any of these
# has changed what decides it, which BM-07's correctness contract forbids
# regardless of whether the target still passes. Kept here, beside the code that
# enforces it, rather than only in the manifest builder.
RUNNER_CONFIG_FILES = ("conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
DIFF_PATH = re.compile(r"^\+\+\+ b/(.+)$")

CORRECT = "correct"
WRONG = "wrong"


@dataclass
class TruthVerdict:
    """Why the oracle decided what it decided, in full."""

    verdict: str
    applied: bool = False
    protected_paths_ok: bool = True
    modified_paths: list[str] = field(default_factory=list)
    target_result: str | None = None
    preservation_requested: int = 0
    preservation_executed: int = 0
    preservation_all_passed: bool | None = None
    preservation_failures: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "ground_truth_verdict": self.verdict,
            "applied": self.applied,
            "ground_truth_protected_path_result": "ok" if self.protected_paths_ok else "violated",
            "modified_paths": self.modified_paths,
            "ground_truth_target_result": self.target_result,
            "ground_truth_preservation_results": {
                "requested": self.preservation_requested,
                "executed": self.preservation_executed,
                "all_passed": self.preservation_all_passed,
                "failures": self.preservation_failures,
            },
            "reason": self.reason,
        }


def oracle_hash() -> str:
    """SHA-256 of this file's exact bytes.

    The oracle *defines* ground truth, so its identity has to be frozen and
    checked alongside the runtime, driver and manifest. A benchmark that pins
    everything except the program deciding right from wrong has not pinned the
    result.
    """
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=TIMEOUT,
        env={**os.environ, **(env or {})},
    )


def changed_paths(diff: str) -> list[str]:
    """Paths a unified diff *claims* to write. Retained for reporting only.

    Not used to decide protected-path violations any more: a header parser sees
    what the patch says, and a deletion, a rename or a mode change can leave a
    protected file altered without `+++ b/<path>` naming it the way the parser
    expects. `changed_paths_from_git` asks the repository instead.
    """
    out = []
    for line in diff.splitlines():
        m = DIFF_PATH.match(line)
        if m and m.group(1) != "/dev/null":
            out.append(m.group(1))
    return sorted(set(out))


def changed_paths_from_git(tree: Path) -> list[str]:
    """Every path the working tree differs from HEAD in, according to git.

    Authoritative because it is the repository's own answer rather than an
    interpretation of the patch text. Modifications, deletions, renames (both
    sides), copies and new files all appear; `--porcelain` is stable output
    intended for machines, unlike the human diff summary.
    """
    proc = _run(["git", "status", "--porcelain", "--untracked-files=all"], tree)
    out: set[str] = set()
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        payload = line[3:]
        if " -> " in payload:  # a rename or copy touches both names
            before, after = payload.split(" -> ", 1)
            out.add(before.strip().strip('"'))
            out.add(after.strip().strip('"'))
        else:
            out.add(payload.strip().strip('"'))
    return sorted(out)


def _pytest(tree: Path, nodes: list[str], layout: str, extra: list[str]) -> subprocess.CompletedProcess:
    src = tree / layout if layout and layout != "flat" else tree
    return _run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", *extra, *nodes],
        tree,
        {"PYTHONPATH": str(src.resolve())},
    )


def run_node(tree: Path, node: str, layout: str) -> str:
    proc = _pytest(tree, [node], layout, ["-x"])
    if proc.returncode == 0:
        return "pass"
    return "fail" if proc.returncode == 1 else "error"


def run_all(tree: Path, nodes: list[str], layout: str) -> tuple[bool, list[str], int]:
    """The complete set, in deterministic chunks. Never a sample."""
    failures: list[str] = []
    for i in range(0, len(nodes), CHUNK):
        chunk = nodes[i : i + CHUNK]
        if _pytest(tree, chunk, layout, ["--tb=no"]).returncode != 0:
            failures.extend(n for n in chunk if run_node(tree, n, layout) != "pass")
    return (not failures), failures, len(nodes)


def evaluate(tree: Path, candidate: str, case: dict) -> TruthVerdict:
    """Judge one candidate against the frozen case oracle, without RIFT.

    `tree` must be a **fresh** materialised baseline; this function mutates it by
    applying the candidate and never restores it. Callers give each verdict its
    own copy rather than reusing one and cleaning up, so no verdict can observe
    another's residue.
    """
    layout = case.get("src_layout", "flat")
    protected = set(case.get("protected_paths") or [])

    patch = tree.parent / f"{tree.name}.truth.diff"
    patch.write_text(candidate, encoding="utf-8", newline="")
    applied = _run(["git", "apply", "--whitespace=nowarn", str(patch)], tree)
    patch.unlink(missing_ok=True)
    if applied.returncode != 0:
        return TruthVerdict(WRONG, applied=False, reason=f"candidate does not apply: {applied.stderr.strip()[:120]}")

    # Ask git what actually changed rather than what the patch header claimed.
    touched = changed_paths_from_git(tree)
    violations = sorted(set(touched) & protected)
    if violations:
        return TruthVerdict(
            WRONG,
            applied=True,
            protected_paths_ok=False,
            modified_paths=touched,
            reason=f"candidate modifies protected paths: {violations}",
        )

    target = run_node(tree, case["target_node"], layout)
    if target != "pass":
        return TruthVerdict(
            WRONG,
            applied=True,
            modified_paths=touched,
            target_result=target,
            reason=f"target does not pass with the candidate applied: {target}",
        )

    nodes = list(case["preservation_nodes"])
    ok, failures, executed = run_all(tree, nodes, layout)
    return TruthVerdict(
        CORRECT if ok else WRONG,
        applied=True,
        modified_paths=touched,
        target_result=target,
        preservation_requested=len(nodes),
        preservation_executed=executed,
        preservation_all_passed=ok,
        preservation_failures=failures,
        reason="" if ok else f"{len(failures)} of {len(nodes)} preservation nodes fail",
    )
