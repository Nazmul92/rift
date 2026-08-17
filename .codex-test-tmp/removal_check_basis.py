"""Feature-removal evidence for the DAR-001 `repair_basis` receipt block."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from removal_check import ROOT, copy_tree, digest  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

REPLAY = "tests/test_repair_basis_replay.py"

MUTATIONS = [
    (
        "the repair basis is not emitted onto the receipt at all",
        "src/riftagent/app.py",
        "    receipt.update(_repair_basis(proj))",
        "    pass",
        f"{REPLAY}::test_cause_supported_replays_byte_identically",
    ),
    (
        "the basis is a constant: the unresolved branch reports cause_supported",
        "src/riftagent/app.py",
        '        "repair_basis": "diagnosis_unresolved",\n        "diagnosis": "unresolved",',
        '        "repair_basis": "cause_supported",\n        "diagnosis": "supported",',
        f"{REPLAY}::test_the_two_bases_differ_on_the_same_code_path",
    ),
    (
        "a located cause is claimed without a frozen reproducer behind it",
        "src/riftagent/app.py",
        "        and proj.reproducer is not None\n    )",
        "    )",
        f"{REPLAY}::test_a_supported_cause_without_a_frozen_reproducer_may_not_claim_the_stronger_basis",
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
            print(proc.stdout[-1200:])
        shutil.rmtree(copy.parent, ignore_errors=True)

    after = digest(ROOT)
    print(f"\nauthoritative digest AFTER : {after}")
    print(f"authoritative tree unchanged: {before == after}")
    print(f"removals not detected: {undetected}")
    return 0 if (undetected == 0 and before == after) else 1


if __name__ == "__main__":
    raise SystemExit(main())
