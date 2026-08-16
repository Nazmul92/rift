"""Ruling 1: task directories are allocated collision-proof, not clock-proof.

The superseded scheme built an id from a truncated timestamp plus a hash of the
inputs. Two invocations of the identical command inside the same timestamp tick
produced the identical id, and the call site used `mkdir(exist_ok=True)`, so the
second task silently appended into the first task's ledger and overwrote its
artifacts.

The correction does not add clock precision and does not rely on randomness.
The sequence number is derived from the directory listing — which stays the sole
source of truth, with no counter file, database or `state.json` — and the claim
itself is `mkdir(exist_ok=False)`, an operation the OS makes atomic across
processes. These tests hold that line under a frozen clock and under genuine
concurrency.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from riftagent.records import (
    TASK_ID_RE,
    Ledger,
    ValidationError,
    allocate_task_dir,
    iter_task_dirs,
    task_fingerprint,
)

FP = task_fingerprint({"n": "tests/test_x.py::test_y", "p": "deadbeef"})


def test_a_fingerprint_is_stable_and_clock_free():
    """Identity of the request, not of the moment."""
    again = task_fingerprint({"n": "tests/test_x.py::test_y", "p": "deadbeef"})
    assert again == FP
    assert len(FP) == 8 and all(c in "0123456789abcdef" for c in FP)


def test_ids_are_well_formed(tmp_path: Path):
    task_id, td = allocate_task_dir(tmp_path, "verify", FP)
    assert TASK_ID_RE.match(task_id), task_id
    assert td.is_dir()
    assert td.name == task_id


def test_identical_requests_under_a_frozen_clock_get_distinct_ids(tmp_path: Path, monkeypatch):
    """The decisive case. The old scheme depended entirely on the clock
    advancing between invocations; here it never advances at all."""
    import riftagent.records as records

    monkeypatch.setattr(records, "utc_now", lambda: "2026-08-15T00:00:00.000000Z")

    ids = [allocate_task_dir(tmp_path, "verify", FP)[0] for _ in range(5)]
    assert len(set(ids)) == 5, ids


def test_two_identical_tasks_get_independent_ledgers_and_artifacts(tmp_path: Path):
    id_a, dir_a = allocate_task_dir(tmp_path, "verify", FP)
    id_b, dir_b = allocate_task_dir(tmp_path, "verify", FP)
    assert id_a != id_b
    assert dir_a != dir_b

    Ledger(dir_a / "ledger.jsonl", id_a)
    Ledger(dir_b / "ledger.jsonl", id_b)
    (dir_a / "change-set.diff").write_text("patch A", encoding="utf-8")
    (dir_b / "change-set.diff").write_text("patch B", encoding="utf-8")

    # Neither task can observe or damage the other's durable state.
    assert (dir_a / "change-set.diff").read_text(encoding="utf-8") == "patch A"
    assert (dir_b / "change-set.diff").read_text(encoding="utf-8") == "patch B"


def test_allocation_never_reuses_a_directory_that_already_holds_a_ledger(tmp_path: Path):
    """The failure the old scheme actually caused: a second task appending into
    a first task's ledger."""
    id_a, dir_a = allocate_task_dir(tmp_path, "verify", FP)
    ledger = Ledger(dir_a / "ledger.jsonl", id_a)
    from riftagent.records import EventKind

    ledger.append(EventKind.TASK_STARTED, {"task_id": id_a})
    before = (dir_a / "ledger.jsonl").read_bytes()

    for _ in range(10):
        _, other = allocate_task_dir(tmp_path, "verify", FP)
        assert other != dir_a
        assert not (other / "ledger.jsonl").exists()

    assert (dir_a / "ledger.jsonl").read_bytes() == before


def test_a_rapid_sequence_stays_unique_and_ordered(tmp_path: Path):
    ids = [allocate_task_dir(tmp_path, "verify", FP)[0] for _ in range(50)]
    assert len(set(ids)) == 50
    seqs = [int(i.rsplit("-", 1)[1]) for i in ids]
    assert seqs == sorted(seqs), "sequence numbers should advance monotonically"
    assert seqs[0] == 0


def test_different_requests_do_not_share_a_sequence_space(tmp_path: Path):
    other = task_fingerprint({"n": "tests/test_other.py::test_z", "p": "cafe"})
    assert other != FP
    a, _ = allocate_task_dir(tmp_path, "verify", FP)
    b, _ = allocate_task_dir(tmp_path, "verify", other)
    assert a.endswith("-0000") and b.endswith("-0000")
    assert a != b


def test_different_verbs_do_not_collide(tmp_path: Path):
    a, _ = allocate_task_dir(tmp_path, "verify", FP)
    b, _ = allocate_task_dir(tmp_path, "why", FP)
    c, _ = allocate_task_dir(tmp_path, "fix", FP)
    assert len({a, b, c}) == 3


# --------------------------------------------------------------------------
# genuine cross-process concurrency
# --------------------------------------------------------------------------


CHILD = """
import sys
from riftagent.records import allocate_task_dir

root, fp, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
for _ in range(n):
    print(allocate_task_dir(root, "verify", fp)[0])
"""


def test_concurrent_processes_never_share_a_task_directory(tmp_path: Path):
    """Separate OS processes, identical request, no coordination between them.
    Only `mkdir(exist_ok=False)` stands between them and a shared ledger."""
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(repo), FP, "20"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        outs += out.split()

    assert len(outs) == 80
    assert len(set(outs)) == 80, "two processes were handed the same task id"
    on_disk = {d.name for d in (repo / ".rift" / "tasks").iterdir()}
    assert on_disk == set(outs)


def test_resume_discovers_every_incomplete_task(tmp_path: Path):
    """Distinct ids are only useful if the resume scan can still see them all.
    Under the old scheme colliding tasks were invisible — there was one
    directory where there should have been several."""
    from riftagent.records import EventKind

    created = []
    for _ in range(6):
        task_id, td = allocate_task_dir(tmp_path, "verify", FP)
        ledger = Ledger(td / "ledger.jsonl", task_id)
        ledger.append(EventKind.TASK_STARTED, {"task_id": task_id})
        created.append(task_id)

    found = [d.name for d in iter_task_dirs(tmp_path)]
    assert sorted(found) == sorted(created)


# --------------------------------------------------------------------------
# bounds and input validation
# --------------------------------------------------------------------------


def test_the_retry_is_bounded_rather_than_unbounded(tmp_path: Path, monkeypatch):
    """A directory that cannot be claimed is an error to report, never a
    directory to reuse and never an infinite spin."""
    base = tmp_path / ".rift" / "tasks"
    base.mkdir(parents=True)
    real = Path.mkdir

    def always_taken(self: Path, *a, **k):
        if self.parent == base:
            raise FileExistsError(str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", always_taken)
    with pytest.raises(ValidationError, match="could not allocate"):
        allocate_task_dir(tmp_path, "verify", FP)


@pytest.mark.parametrize("verb", ["Verify", "ver-ify", "", "verify2", "../escape"])
def test_a_malformed_verb_is_rejected(tmp_path: Path, verb: str):
    with pytest.raises(ValidationError):
        allocate_task_dir(tmp_path, verb, FP)


@pytest.mark.parametrize("fp", ["", "XYZ12345", "0a1b2c3", "0a1b2c3d4", "../../etc"])
def test_a_malformed_fingerprint_is_rejected(tmp_path: Path, fp: str):
    with pytest.raises(ValidationError):
        allocate_task_dir(tmp_path, "verify", fp)


def test_unrelated_directory_entries_do_not_disturb_the_sequence(tmp_path: Path):
    base = tmp_path / ".rift" / "tasks"
    base.mkdir(parents=True)
    (base / f"verify-{FP}-notanumber").mkdir()
    (base / "verify-0a1b2c3d-0007").mkdir()
    (base / "README").write_text("x", encoding="utf-8")

    task_id, _ = allocate_task_dir(tmp_path, "verify", FP)
    assert task_id == f"verify-{FP}-0000"


def test_no_counter_file_or_state_database_is_created(tmp_path: Path):
    """The directory listing is the source of truth. Anything else would be a
    second durable state store, which the architecture forbids."""
    for _ in range(3):
        allocate_task_dir(tmp_path, "verify", FP)

    entries = {p.name for p in (tmp_path / ".rift" / "tasks").iterdir()}
    assert all(TASK_ID_RE.match(name) for name in entries), entries
    assert all(p.is_dir() for p in (tmp_path / ".rift" / "tasks").iterdir())
    assert not list(tmp_path.rglob("state.json"))
    assert not list(tmp_path.rglob("*.db"))
    assert not list(tmp_path.rglob("*counter*"))
