r"""The provenance auditor, made as strict as the invariant it claims to check.

`candidate_record_mismatches` was the one part of the candidate-provenance work
that did not fail closed. Two ways it could return "no mismatch" about a record
it had not actually verified:

1. **An incomplete record passed.** `candidate_record_mismatches(td, {})`
   returned `()`. A `CANDIDATE_CANONICALIZED` event describes three mandatory
   stages, so a stage naming neither a path nor a hash is a *missing* stage, not
   an absent question. The auditor was agreeing with a record that said nothing.

2. **A recorded path could leave the task directory.** The resolution was
   `td / recorded_path` with no confinement, so `"../outside.txt"` paired with
   that file's real hash passed. An event for attempt 2 could also be satisfied
   by attempt 1's artifact.

Neither was reachable from the product: `_canonicalize_proposal` constructs safe
relative attempt-owned paths itself. But an independent auditor that is more
permissive than the governance language is not an auditor — it is a second
opinion that agrees by default, and the whole point of DAR-031 through DAR-033
was that a digest is evidence only when it provably describes the right bytes.

The strengthened invariant:

    event says attempt N
      -> all three stages present, with a path and a hash
      -> every path relative, confined to the task directory, owned by N
      -> every file exists
      -> every hash reconstructs

No provider is configured and no request leaves the process.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import riftagent.llm as llm
from riftagent.records import (
    CANDIDATE_STAGES,
    EventKind,
    candidate_attempt_dir,
    candidate_record_mismatches,
    content_hash,
    raw_candidate_record,
)

GOOD = "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"


def written_record(td: Path, attempt: int, text: str = GOOD) -> dict:
    """A complete, honest record for one attempt, with the bytes on disk."""
    payload: dict = {"attempt": attempt}
    for stage, name in zip(CANDIDATE_STAGES, ("raw", "normalized", "canonical"), strict=True):
        path = candidate_attempt_dir(td, attempt) / f"{name}.diff"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        payload[f"{stage}_candidate_record"] = path.relative_to(td).as_posix()
        payload[f"{stage}_candidate_hash"] = content_hash(path.read_bytes())
    return payload


def test_a_complete_honest_record_still_passes(tmp_path):
    """The baseline every case below is a deviation from."""
    assert candidate_record_mismatches(tmp_path, written_record(tmp_path, 1)) == ()
    assert candidate_record_mismatches(tmp_path, written_record(tmp_path, 2)) == ()


# --------------------------------------------------- an incomplete record


def test_an_empty_record_is_a_finding_not_a_pass(tmp_path):
    """This returned `()` before DAR-034."""
    findings = candidate_record_mismatches(tmp_path, {})
    assert findings, "the auditor agreed with a record that said nothing"
    assert "attempt" in findings[0]


@pytest.mark.parametrize(
    "attempt", [None, 0, -1, "1", 1.0, True], ids=["none", "zero", "negative", "str", "float", "bool"]
)
def test_a_record_without_a_usable_attempt_cannot_be_checked(tmp_path, attempt):
    """`True` is an `int` in Python and would address `candidate-attempt-001`.
    Accepting it would let a boolean stand in for provenance."""
    payload = written_record(tmp_path, 1)
    payload["attempt"] = attempt
    findings = candidate_record_mismatches(tmp_path, payload)
    assert len(findings) == 1 and findings[0].startswith("attempt:")


@pytest.mark.parametrize("stage", CANDIDATE_STAGES)
def test_every_stage_is_mandatory(tmp_path, stage):
    payload = written_record(tmp_path, 1)
    del payload[f"{stage}_candidate_record"]
    del payload[f"{stage}_candidate_hash"]
    findings = candidate_record_mismatches(tmp_path, payload)
    assert findings == (f"{stage}: the stage is absent from the record; all three stages are mandatory",)


@pytest.mark.parametrize("stage", CANDIDATE_STAGES)
def test_a_path_without_a_hash_is_a_finding(tmp_path, stage):
    payload = written_record(tmp_path, 1)
    del payload[f"{stage}_candidate_hash"]
    assert candidate_record_mismatches(tmp_path, payload) == (f"{stage}: an artifact path was recorded with no hash",)


@pytest.mark.parametrize("bad", [None, "", 5, ["a"]], ids=["none", "empty", "int", "list"])
def test_a_hash_without_a_usable_path_is_a_finding(tmp_path, bad):
    payload = written_record(tmp_path, 1)
    payload["raw_candidate_record"] = bad
    findings = candidate_record_mismatches(tmp_path, payload)
    assert len(findings) == 1
    assert "no artifact path" in findings[0] or "absent from the record" in findings[0]


# ------------------------------------------------- task-directory confinement


def test_a_path_escaping_the_task_directory_is_refused_even_when_its_hash_matches(tmp_path):
    """The reported case. A real file, a correct hash, and a path that points
    at something which is not a candidate artifact at all."""
    td = tmp_path / "task"
    td.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not a candidate\n")

    payload = written_record(td, 1)
    payload["raw_candidate_record"] = "../outside.txt"
    payload["raw_candidate_hash"] = content_hash(outside.read_bytes())

    findings = candidate_record_mismatches(td, payload)
    assert len(findings) == 1
    assert "traverses outside" in findings[0], findings


@pytest.mark.parametrize(
    "bad",
    [
        "../outside.txt",
        "candidate-attempt-001/../../outside.txt",
        "/etc/passwd",
        "\\\\server\\share\\raw.diff",
        "C:/Windows/raw.diff",
    ],
    ids=["parent", "buried-parent", "absolute-posix", "unc", "absolute-windows"],
)
def test_unconfined_paths_are_refused_before_any_bytes_are_read(tmp_path, bad):
    """Checked before the file is opened: a hash computed over the wrong file is
    a passing check and a false statement."""
    td = tmp_path / "task"
    td.mkdir()
    payload = written_record(td, 1)
    payload["raw_candidate_record"] = bad
    findings = candidate_record_mismatches(td, payload)
    assert len(findings) == 1 and findings[0].startswith("raw: "), findings
    assert "does not hash" not in findings[0], "the bytes were read before the path was judged"


def test_a_path_belonging_to_another_attempt_is_refused(tmp_path):
    """Attempt 1's artifact exists and hashes correctly. It is still not
    attempt 2's evidence, and before DAR-034 this passed whenever the file
    happened to be there."""
    written_record(tmp_path, 1)
    payload = written_record(tmp_path, 2)
    payload["raw_candidate_record"] = "candidate-attempt-001/raw.diff"
    payload["raw_candidate_hash"] = content_hash(raw_candidate_record(tmp_path, 1).read_bytes())

    findings = candidate_record_mismatches(tmp_path, payload)
    assert findings == ("raw: 'candidate-attempt-001/raw.diff' does not belong to attempt 002",)


def test_a_path_outside_any_attempt_directory_is_refused(tmp_path):
    """Inside the task directory but not in an attempt directory — the DAR-032
    layout, which is exactly what DAR-033 replaced."""
    payload = written_record(tmp_path, 1)
    stray = tmp_path / "raw-candidate.diff"
    stray.write_bytes(GOOD.encode("utf-8"))
    payload["raw_candidate_record"] = "raw-candidate.diff"
    payload["raw_candidate_hash"] = content_hash(stray.read_bytes())

    findings = candidate_record_mismatches(tmp_path, payload)
    assert findings == ("raw: 'raw-candidate.diff' does not belong to attempt 001",)


def test_corruption_and_absence_are_still_reported_distinctly(tmp_path):
    payload = written_record(tmp_path, 1)
    (tmp_path / payload["normalized_candidate_record"]).write_bytes(b"tampered\n")
    (tmp_path / payload["canonical_candidate_record"]).unlink()

    findings = candidate_record_mismatches(tmp_path, payload)
    assert len(findings) == 2
    assert any("does not hash" in f for f in findings)
    assert any("is recorded but absent" in f for f in findings)


# ------------------------------------------- the real retry loop, end to end


def test_the_fix_loop_rejects_one_candidate_and_accepts_the_next(
    simple_repo, run_cli, monkeypatch, correct_diff, judge_diff
):
    """`cmd_fix` driven through the CLI with `--max-attempts 2`, model faked.

    The two-attempt DAR-033 test called `_request_change` twice and built the
    ChangeSet by hand. That proves the mechanism and not the wiring: it could
    not have caught a loop that registered the wrong attempt's bytes. Here the
    product chooses, and the assertion is that what it registered is attempt 2's
    canonical artifact — verified against the file on disk, not against the
    value the test passed in.

    Attempt 1 edits the frozen judge, which `kernel.validate_patch` refuses
    before anything executes — a real product rejection path, and the only one
    that retries. A gate failure does not produce a second attempt: the repair
    loop is deferred, and this test does not quietly assume otherwise.
    """
    queue = [judge_diff, correct_diff]
    seen: list[str] = []

    def post_chat(config, messages, max_output_tokens, timeout_s=120.0, temperature=None):
        system = messages[0]["content"]
        if llm._CHANGE_SYSTEM not in system:
            # Hypotheses/handles requests are not what this test drives.
            raise llm.ModelUnavailable("not exercised by this test")
        diff = queue.pop(0)
        seen.append(diff)
        return llm.ModelReply(
            text=json.dumps({"diff": diff, "summary": "s"}),
            usage=llm.ModelUsage(input_tokens=10, output_tokens=10),
            model_reported="fake-model",
            finish_reason="stop",
        )

    monkeypatch.setattr(llm, "post_chat", post_chat)
    monkeypatch.setenv("RIFT_LLM_URL", "https://example.invalid/v1/chat/completions")
    monkeypatch.setenv("RIFT_LLM_KEY", "k")
    monkeypatch.setenv("RIFT_LLM_MODEL", "fake-model")

    run_cli(
        "--repo",
        str(simple_repo),
        "--json",
        "fix",
        "tests/test_calc.py::test_total",
        "--allow-partial-sandbox",
        "--max-attempts",
        "2",
    )

    assert len(seen) == 2, f"the loop did not make two proposals: {len(seen)}"

    ledger = next((simple_repo / ".rift" / "tasks").glob("*/ledger.jsonl"))
    td = ledger.parent
    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    pipeline = [e["payload"] for e in events if e["kind"] == EventKind.CANDIDATE_CANONICALIZED.value]
    registered = [e["payload"] for e in events if e["kind"] == EventKind.CHANGESET_REGISTERED.value]
    rejections = [e["payload"] for e in events if e["kind"] == EventKind.CHANGESET_REJECTED.value]

    assert [p["attempt"] for p in pipeline] == [1, 2]
    assert len(rejections) == 1 and rejections[0]["attempt"] == 1
    assert len(registered) == 1, "exactly one ChangeSet should reach the gate"

    # Both attempts audit clean, including the one the product threw away.
    for payload in pipeline:
        assert candidate_record_mismatches(td, payload) == ()

    # The registered ChangeSet is attempt 2's canonical artifact, byte-for-byte.
    accepted = pipeline[1]
    changeset = registered[0]["changeset"]
    assert changeset["patch_hash"] == accepted["canonical_candidate_hash"]
    assert changeset["patch_hash"] != pipeline[0]["canonical_candidate_hash"]
    on_disk = (td / accepted["canonical_candidate_record"]).read_bytes()
    assert changeset["diff"].encode("utf-8") == on_disk

    # Attempt 1's refused proposal is still exactly what the model sent.
    assert (td / pipeline[0]["raw_candidate_record"]).read_bytes() == judge_diff.encode("utf-8")


def test_the_auditor_reaches_no_provider():
    import ast
    import inspect

    from riftagent.records import _confined_candidate_path

    for fn in (candidate_record_mismatches, _confined_candidate_path):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert not names & {"llm", "post_chat", "requests", "urllib"}
    import riftagent.records as records

    assert not hasattr(records, "llm"), "the records module must not reach a provider"


# ------------------------------------------------------- stage identity


def test_a_stage_pointing_at_another_stages_artifact_is_refused(tmp_path):
    """The relabelling case. Every file is real, inside the right attempt, and
    hashes correctly — and the event still claims the normalised bytes are what
    the model wrote.

    DAR-032 and DAR-033 created three distinct stages precisely so raw could be
    told from normalised. An auditor that accepts any of them for any stage
    gives that distinction back (DAR-035)."""
    payload = written_record(tmp_path, 1)
    normalized = tmp_path / payload["normalized_candidate_record"]
    payload["raw_candidate_record"] = payload["normalized_candidate_record"]
    payload["raw_candidate_hash"] = content_hash(normalized.read_bytes())

    findings = candidate_record_mismatches(tmp_path, payload)
    assert findings == (
        "raw: 'candidate-attempt-001/normalized.diff' is not the raw artifact; attempt 001's raw stage is raw.diff",
    )


@pytest.mark.parametrize("stage", CANDIDATE_STAGES)
def test_an_arbitrary_filename_inside_the_attempt_directory_is_refused(tmp_path, stage):
    """Attempt membership is not stage identity. A file can sit in the right
    directory with the right hash and still not be the artifact it claims."""
    payload = written_record(tmp_path, 1)
    stray = candidate_attempt_dir(tmp_path, 1) / "foo.diff"
    stray.write_bytes(b"FOO\n")
    payload[f"{stage}_candidate_record"] = "candidate-attempt-001/foo.diff"
    payload[f"{stage}_candidate_hash"] = content_hash(stray.read_bytes())

    findings = candidate_record_mismatches(tmp_path, payload)
    assert len(findings) == 1
    assert f"is not the {stage} artifact" in findings[0], findings


def test_a_symlinked_artifact_is_refused_even_at_the_right_path(tmp_path):
    r"""`Path.is_file()` follows symlinks, so a link named `raw.diff` pointing at
    other bytes satisfies both the path check and the hash. An immutable record
    that can be re-aimed after the fact is not immutable."""
    payload = written_record(tmp_path, 1)
    attempt_dir = candidate_attempt_dir(tmp_path, 1)
    target = attempt_dir / "actualraw.diff"
    target.write_bytes(b"SNEAKY\n")
    link = attempt_dir / "raw.diff"
    link.unlink()
    link.symlink_to(target)
    payload["raw_candidate_hash"] = content_hash(target.read_bytes())

    findings = candidate_record_mismatches(tmp_path, payload)
    assert findings == (
        "raw: 'candidate-attempt-001/raw.diff' is a symlink; candidate artifacts must be regular files",
    )


def test_a_symlinked_attempt_directory_is_refused(tmp_path):
    """The same re-aiming one level up."""
    real = tmp_path / "elsewhere"
    real.mkdir()
    payload = {"attempt": 7}
    for stage in CANDIDATE_STAGES:
        (real / f"{stage}.diff").write_bytes(GOOD.encode("utf-8"))
        payload[f"{stage}_candidate_record"] = f"candidate-attempt-007/{stage}.diff"
        payload[f"{stage}_candidate_hash"] = content_hash(GOOD.encode("utf-8"))
    (tmp_path / "candidate-attempt-007").symlink_to(real, target_is_directory=True)

    findings = candidate_record_mismatches(tmp_path, payload)
    assert len(findings) == len(CANDIDATE_STAGES)
    assert all("symlinked attempt directory" in f for f in findings), findings


def test_the_auditor_expects_the_paths_the_product_writes(tmp_path):
    """`STAGE_RECORDS` is the same mapping `_canonicalize_proposal` persists
    through, so the auditor and the writer cannot drift into disagreeing about
    where a stage lives."""
    from riftagent.records import STAGE_RECORDS

    assert tuple(STAGE_RECORDS) == CANDIDATE_STAGES
    for stage, record in STAGE_RECORDS.items():
        assert record(tmp_path, 3) == candidate_attempt_dir(tmp_path, 3) / f"{stage}.diff"
