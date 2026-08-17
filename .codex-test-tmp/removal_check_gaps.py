"""Feature-removal evidence for the two runtime changes made closing the gaps."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from removal_check import ROOT, copy_tree, digest  # noqa: E402

GAPS = "tests/test_acceptance_gaps.py"

MUTATIONS = [
    (
        "M1-R07: the interrupt no longer kills the child process tree",
        "src/riftagent/sandbox.py",
        "    except BaseException:",
        "    except (SystemExit, GeneratorExit):",
        f"{GAPS}::test_r07_an_interrupt_kills_the_child_process_tree",
    ),
    (
        "M1-F06: propose_handles is issued up front again instead of on the signal",
        "src/riftagent/app.py",
        "            if not live and not asked_handles and req.use_model and spend is not None:",
        "            if False:",
        f"{GAPS}::test_f06_all_contradicted_triggers_one_bounded_handles_request",
    ),
    (
        "M1-F06: the widened handles are not merged into the theory space",
        "src/riftagent/app.py",
        "                if len(widened) > len(handles):",
        "                if False:",
        f"{GAPS}::test_f06_all_contradicted_triggers_one_bounded_handles_request",
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
