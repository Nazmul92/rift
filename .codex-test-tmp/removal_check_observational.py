"""Feature-removal evidence for the assertion-observation path and its three
required corrections."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from removal_check import ROOT, copy_tree, digest  # noqa: E402

OBS = "tests/test_observational_finding.py"

MUTATIONS = [
    (
        "the observational verdict is never produced",
        "src/riftagent/app.py",
        "            diagnosis = kernel.observational_diagnosis(absent, notes, diagnosis.contradicted) or diagnosis",
        "            diagnosis = diagnosis",
        f"{OBS}::test_f15_a_missing_file_is_an_observational_finding",
    ),
    (
        "the collection-error path is dropped, so a missing import is only infrastructure",
        "src/riftagent/app.py",
        "            if found is not None:\n                return found",
        "            if False:\n                return found",
        f"{OBS}::test_f15_a_missing_dependency_is_an_observational_finding",
    ),
    (
        "correction 1: discovery bypasses Handle.from_dict validation",
        "src/riftagent/kernel.py",
        '                assert_c.append(Handle.from_dict({"kind": kind.value, "arg": arg}))',
        "                assert_c.append(Handle(kind, arg))",
        f"{OBS}::test_a_hostile_failure_message_cannot_produce_an_escaping_handle",
    ),
    (
        "correction 2: an unobservable measurement is treated as an absence",
        "src/riftagent/checks.py",
        "    if res.timed_out or res.exit_code not in (0, 1) or first not in (PRESENT, ABSENT):\n        return UNOBSERVABLE, res",
        "    if False:\n        return UNOBSERVABLE, res",
        f"{OBS}::test_an_unobservable_measurement_is_not_evidence_of_absence",
    ),
    (
        "correction 3: the assertion command is not announced before it runs",
        "src/riftagent/app.py",
        '            EventKind.COMMAND_STARTED,\n            {"display": f"assert {handle.label}"',
        '            EventKind.CONTEXT_SELECTED,\n            {"display": f"assert {handle.label}"',
        f"{OBS}::test_the_assertion_command_is_announced_before_it_runs",
    ),
    (
        "correction A: a sandbox failure leaves its command unclosed",
        "src/riftagent/app.py",
        '                "exit_code": res.exit_code if res else -1,',
        '                "exit_code": res.exit_code,',
        f"{OBS}::test_a_sandbox_failure_closes_the_command_and_supports_no_finding",
    ),
    (
        "correction A2: a refused measurement is closed as a success",
        "src/riftagent/app.py",
        '                "successful": res is not None and outcome != UNOBSERVABLE,',
        '                "successful": True,',
        f"{OBS}::test_a_sandbox_failure_closes_the_command_and_supports_no_finding",
    ),
    (
        "correction B: the audit harness directory is archived",
        "src/riftagent/records.py",
        '    | {".codex-test-tmp"}',
        "    | set()",
        f"{OBS}::test_the_archive_excludes_the_audit_harness_directory",
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
