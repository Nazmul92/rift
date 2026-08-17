"""Feature-removal evidence for the `propose_hypotheses` wiring.

Each mutation runs in a fresh disposable copy of the exact tree, deleted before
the next begins. Two guards exist because both have already produced false
evidence in this project:

  * the authoritative tree is digested before and after and must be unchanged;
  * the copy asserts `riftagent.__file__` resolves under itself before running,
    because the editable install otherwise points every import at the original.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/w")
SKIP = {".git", ".rift", "__pycache__", ".mypy_cache", ".ruff_cache", ".codex-test-tmp", "build", ".pytest_cache"}


def digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if any(part in SKIP for part in rel.parts) or "pytest-cache-files" in str(rel):
            continue
        h.update(str(rel).replace("\\", "/").encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def copy_tree() -> Path:
    dest = Path(tempfile.mkdtemp(prefix="removal-")) / "tree"
    shutil.copytree(
        ROOT,
        dest,
        ignore=shutil.ignore_patterns(*SKIP, "pytest-cache-files-*"),
    )
    return dest


# (label, file, old, new, test to run)
MUTATIONS = [
    (
        "the ambiguity-point call site is removed (the loop simply breaks)",
        "src/riftagent/app.py",
        "                settled_now = kernel.derive_diagnosis(scored, probes, ev, mapping, [])",
        "                break\n                settled_now = kernel.derive_diagnosis(scored, probes, ev, mapping, [])",
        "tests/test_propose_hypotheses.py::test_valid_hypotheses_are_requested_at_the_ambiguity_point_and_scored",
    ),
    (
        "the returned theories are not merged into the theory space",
        "src/riftagent/app.py",
        "                hypotheses = hypotheses + extra",
        "                hypotheses = list(hypotheses)",
        "tests/test_propose_hypotheses.py::test_valid_hypotheses_are_requested_at_the_ambiguity_point_and_scored",
    ),
    (
        "the no-downgrade guard is removed (a supported diagnosis is asked about anyway)",
        "src/riftagent/app.py",
        "                if settled_now.status is Verdict.DIAGNOSIS_SUPPORTED:",
        "                if False:",
        "tests/test_propose_hypotheses.py::test_a_supported_diagnosis_is_never_put_at_risk_by_a_model_request",
    ),
    (
        "`why` is given no spend ledger, so the shared flow can make no request",
        "src/riftagent/app.py",
        "        diagnosis = run_diagnosis(flow, req, td, spend=spend)",
        "        diagnosis = run_diagnosis(flow, req, td)",
        "tests/test_propose_hypotheses.py::test_valid_hypotheses_are_requested_at_the_ambiguity_point_and_scored",
    ),
    (
        "the response is used without passing through validate_hypotheses",
        "src/riftagent/app.py",
        "        proposed = llm.validate_hypotheses(llm.extract_json(reply.text), roles)",
        '        proposed = llm.extract_json(reply.text)["hypotheses"]',
        "tests/test_propose_hypotheses.py::test_an_invalid_response_is_refused_and_the_diagnosis_is_unchanged",
    ),
]


def main() -> int:
    before = digest(ROOT)
    print(f"authoritative digest BEFORE: {before}")
    undetected = 0
    for label, rel, old, new, test in MUTATIONS:
        copy = copy_tree()
        target = copy / rel
        source = target.read_text(encoding="utf-8")
        assert old in source, f"splice target absent for {label!r}"
        assert source.count(old) == 1, f"splice target is not unique for {label!r}"
        mutated = source.replace(old, new)
        assert mutated != source, f"splice was a no-op for {label!r}"
        target.write_text(mutated, encoding="utf-8", newline="\n")

        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": str(copy / "src"),
            "HOME": "/root",
            "LANG": "C.UTF-8",
        }
        located = subprocess.run(
            [sys.executable, "-c", "import riftagent; print(riftagent.__file__)"],
            cwd=copy,
            env=env,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert located.startswith(str(copy)), f"the copy imported {located}, not its own package"

        proc = subprocess.run(
            [sys.executable, "-m", "pytest", test, "-q", "-p", "no:cacheprovider", "--no-header", "-x"],
            cwd=copy,
            env=env,
            capture_output=True,
            text=True,
        )
        detected = proc.returncode != 0
        undetected += not detected
        print(f"\nmutation : {label}")
        print(f"test     : {test.split('::')[-1]}")
        print(f"imported : {located}")
        print(f"result   : {'RED (expected)' if detected else 'GREEN - NOT DETECTED'}, exit={proc.returncode}")
        if not detected:
            print(proc.stdout[-1500:])
        shutil.rmtree(copy.parent, ignore_errors=True)

    after = digest(ROOT)
    print(f"\nauthoritative digest AFTER : {after}")
    print(f"authoritative tree unchanged: {before == after}")
    print(f"removals not detected: {undetected}")
    return 0 if (undetected == 0 and before == after) else 1


if __name__ == "__main__":
    raise SystemExit(main())
