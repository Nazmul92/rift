r"""Every candidate attempt keeps its own evidence, immutably.

DAR-032 gave the three pipeline stages distinct names but one fixed path each:

    <task>/raw-candidate.diff
    <task>/normalized-candidate.diff
    <task>/canonical-candidate.diff

A second proposal wrote the same three files. Attempt 1's ledger event still
named `raw-candidate.diff`, and after attempt 2 that path held attempt 2's
bytes — so the recorded hash no longer matched the file it pointed at, and the
event described something that no longer existed. Every rejected candidate was
requested, charged, and refused on the evidence; the evidence was then
overwritten by the next one.

DAR-033 addresses artifacts by attempt and makes them immutable:

    <task>/candidate-attempt-001/{raw,normalized,canonical}.diff
    <task>/candidate-attempt-002/{raw,normalized,canonical}.diff

The attempt number comes from the repair loop and is passed down, never
recovered from a directory listing, a timestamp, or a hidden counter. Nothing
about the raw -> normalized -> canonical transformations changed; this is about
artifact lifetime.

No provider is configured and no request leaves the process.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import riftagent.app as app
import riftagent.llm as llm
from riftagent.records import (
    CANDIDATE_STAGES,
    CANON_CANONICALIZED,
    CANON_UNCHANGED,
    ChangeSet,
    EventKind,
    Ledger,
    Pricing,
    SpendLedger,
    ValidationError,
    candidate_attempt_dir,
    candidate_record_mismatches,
    canonical_candidate_record,
    content_hash,
    normalized_candidate_record,
    persist_candidate,
    raw_candidate_record,
)

A_RAW = "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
# Attempt B arrives with CRLF and wrong counts, so all three of its stages differ.
B_RAW = A_RAW.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@").replace("return 2", "return 3").replace("\n", "\r\n")
# Attempt C is missing its final newline, so raw != normalized == canonical.
C_RAW = A_RAW.replace("return 2", "return 4").rstrip("\n")


def harness(tmp_path: Path, monkeypatch, diffs: list[str]):
    """A real Flow and SpendLedger over a queue of model diffs."""
    queue = list(diffs)

    def post_chat(config, messages, max_output_tokens, timeout_s=120.0, temperature=None):
        assert queue, "more requests than queued proposals"
        return llm.ModelReply(
            text=json.dumps({"diff": queue.pop(0), "summary": "s"}),
            usage=llm.ModelUsage(input_tokens=10, output_tokens=10),
            model_reported="fake-model",
            finish_reason="stop",
        )

    monkeypatch.setattr(llm, "post_chat", post_chat)
    monkeypatch.setenv("RIFT_LLM_URL", "https://example.invalid/v1/chat/completions")
    monkeypatch.setenv("RIFT_LLM_KEY", "k")
    monkeypatch.setenv("RIFT_LLM_MODEL", "fake-model")

    td = tmp_path / "task"
    td.mkdir()
    flow = app.Flow(Ledger(td / "ledger.jsonl", "t1"), app.LiveRenderer(quiet=True), None, False)
    spend = SpendLedger(
        tmp_path / "spend.jsonl",
        scope="s",
        limit_usd=10.0,
        pricing=Pricing(input_per_mtok=1.0, output_per_mtok=1.0, provider="p", model="fake-model"),
    )
    return td, flow, spend


def run_attempts(tmp_path: Path, monkeypatch, diffs: list[str]):
    """Drive N proposals through the real request path, one per attempt.

    The loop shape mirrors `cmd_fix`: the attempt number is the loop variable,
    passed down, never inferred."""
    td, flow, spend = harness(tmp_path, monkeypatch, diffs)
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    results = []
    for attempt in range(1, len(diffs) + 1):
        results.append(app._request_change(flow, spend, "t1", messages, 4000, attempt, td))

    events = [json.loads(line) for line in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    payloads = [e["payload"] for e in events if e["kind"] == EventKind.CANDIDATE_CANONICALIZED.value]
    return td, payloads, results


def stage_bytes(td: Path, attempt: int) -> tuple[bytes, bytes, bytes]:
    return (
        raw_candidate_record(td, attempt).read_bytes(),
        normalized_candidate_record(td, attempt).read_bytes(),
        canonical_candidate_record(td, attempt).read_bytes(),
    )


# ------------------------------------------------------------- addressing


def test_each_attempt_owns_a_distinct_directory():
    td = Path("/tmp/task")
    assert candidate_attempt_dir(td, 1).name == "candidate-attempt-001"
    assert candidate_attempt_dir(td, 2).name == "candidate-attempt-002"
    assert candidate_attempt_dir(td, 42).name == "candidate-attempt-042"

    paths = {raw_candidate_record(td, n) for n in (1, 2, 3)}
    assert len(paths) == 3, "two attempts share a raw artifact path"
    for n in (1, 2, 3):
        stage_paths = {
            raw_candidate_record(td, n),
            normalized_candidate_record(td, n),
            canonical_candidate_record(td, n),
        }
        assert len(stage_paths) == 3, "two stages share a path within one attempt"


def test_an_attempt_number_below_one_is_refused():
    """Zero or negative would collide across runs and means the caller lost the
    loop variable rather than that a zeroth attempt exists."""
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            candidate_attempt_dir(Path("/tmp/task"), bad)


# ------------------------------------------------------------ immutability


def test_an_existing_artifact_is_never_silently_overwritten(tmp_path):
    path = tmp_path / "candidate-attempt-001" / "raw.diff"
    first = persist_candidate(path, A_RAW)

    # Idempotent for identical bytes: a replayed write is not a revision.
    assert persist_candidate(path, A_RAW) == first
    assert path.read_bytes() == A_RAW.encode("utf-8")

    with pytest.raises(ValidationError, match="immutable"):
        persist_candidate(path, B_RAW)
    assert path.read_bytes() == A_RAW.encode("utf-8"), "the refused write landed anyway"


def test_immutability_holds_for_every_stage(tmp_path):
    for stage in ("raw.diff", "normalized.diff", "canonical.diff"):
        path = tmp_path / "candidate-attempt-002" / stage
        persist_candidate(path, A_RAW)
        with pytest.raises(ValidationError):
            persist_candidate(path, A_RAW + " tampered\n")


# --------------------------------------------------------- two attempts


def test_a_second_attempt_does_not_disturb_the_first(tmp_path, monkeypatch):
    td, payloads, _ = run_attempts(tmp_path, monkeypatch, [A_RAW, B_RAW])
    assert [p["attempt"] for p in payloads] == [1, 2]

    a_raw, a_norm, a_canon = stage_bytes(td, 1)
    b_raw, b_norm, b_canon = stage_bytes(td, 2)

    assert a_raw == A_RAW.encode("utf-8")
    assert b_raw == B_RAW.encode("utf-8")
    assert b"\r\n" in b_raw and b"\r" not in a_raw
    assert a_canon != b_canon

    for payload, (raw, norm, canon) in zip(payloads, [(a_raw, a_norm, a_canon), (b_raw, b_norm, b_canon)], strict=True):
        assert payload["raw_candidate_hash"] == content_hash(raw)
        assert payload["normalized_candidate_hash"] == content_hash(norm)
        assert payload["canonical_candidate_hash"] == content_hash(canon)
        assert candidate_record_mismatches(td, payload) == ()

    recorded = [p[f"{s}_candidate_record"] for p in payloads for s in CANDIDATE_STAGES]
    assert len(set(recorded)) == 6, "two events name the same artifact"


# ------------------------------------------------------- three attempts


def test_three_attempts_each_keep_all_nine_artifacts(tmp_path, monkeypatch):
    """Two attempts would pass even if only the previous one were retained.
    Three is the smallest sequence that catches that."""
    td, payloads, _ = run_attempts(tmp_path, monkeypatch, [A_RAW, B_RAW, C_RAW])
    assert [p["attempt"] for p in payloads] == [1, 2, 3]

    expected_raw = {1: A_RAW, 2: B_RAW, 3: C_RAW}
    for attempt, payload in zip((1, 2, 3), payloads, strict=True):
        raw, norm, canon = stage_bytes(td, attempt)
        assert raw == expected_raw[attempt].encode("utf-8"), f"attempt {attempt} raw bytes were replaced"
        assert payload["raw_candidate_hash"] == content_hash(raw)
        assert payload["normalized_candidate_hash"] == content_hash(norm)
        assert payload["canonical_candidate_hash"] == content_hash(canon)
        assert candidate_record_mismatches(td, payload) == ()

    files = sorted(p.relative_to(td).as_posix() for p in td.rglob("*.diff"))
    assert files == [
        f"candidate-attempt-{n:03d}/{stage}.diff" for n in (1, 2, 3) for stage in ("canonical", "normalized", "raw")
    ], files

    # The three raw candidates are genuinely distinct, so retention is being
    # tested rather than three copies of one patch.
    assert len({content_hash(stage_bytes(td, n)[0]) for n in (1, 2, 3)}) == 3


def test_each_attempt_records_the_transformation_it_actually_needed(tmp_path, monkeypatch):
    """Attempt-addressing must not disturb what DAR-032 established about the
    stages themselves."""
    td, payloads, _ = run_attempts(tmp_path, monkeypatch, [A_RAW, B_RAW, C_RAW])
    first, second, third = payloads

    assert first["normalization"] == {"changed": False, "operations": []}
    assert first["canonicalization"]["status"] == CANON_UNCHANGED

    assert second["normalization"] == {"changed": True, "operations": ["crlf_to_lf"]}
    assert second["canonicalization"]["status"] == CANON_CANONICALIZED
    assert second["canonicalization"]["authorized_byte_changes_only"] is True

    assert third["normalization"] == {"changed": True, "operations": ["ensure_final_newline"]}
    assert third["normalized_candidate_hash"] == third["canonical_candidate_hash"]
    assert third["raw_candidate_hash"] != third["normalized_candidate_hash"]


# ------------------------------------- a rejected attempt stays readable


def test_a_rejected_attempt_survives_the_accepted_one(tmp_path, monkeypatch):
    """The contract. Attempt 1 is refused, attempt 2 is accepted, and attempt
    1's three artifacts still reproduce their recorded hashes afterwards."""
    td, payloads, results = run_attempts(tmp_path, monkeypatch, [A_RAW, B_RAW])
    rejected, accepted = payloads

    # Attempt 1 was refused on the evidence; the evidence is still here.
    for stage in CANDIDATE_STAGES:
        path = td / rejected[f"{stage}_candidate_record"]
        assert path.is_file(), f"attempt 1 {stage} artifact was removed"
        assert content_hash(path.read_bytes()) == rejected[f"{stage}_candidate_hash"]
    assert (td / rejected["raw_candidate_record"]).read_bytes() == A_RAW.encode("utf-8")

    assert rejected["raw_candidate_record"] != accepted["raw_candidate_record"]
    assert candidate_record_mismatches(td, rejected) == ()
    assert candidate_record_mismatches(td, accepted) == ()
    assert results[1] is not None


def test_the_accepted_changeset_is_built_from_its_own_attempts_bytes(tmp_path, monkeypatch):
    """Attempt 1 rejected, attempt 2 accepted: the ChangeSet must address
    attempt 2's canonical artifact and nothing else."""
    td, payloads, results = run_attempts(tmp_path, monkeypatch, [A_RAW, B_RAW])
    rejected, accepted = payloads
    assert results[1] is not None

    changeset = ChangeSet(diff=results[1][0], touched_paths=("m.py",), origin="model")
    assert changeset.patch_hash == accepted["canonical_candidate_hash"]
    assert changeset.patch_hash != rejected["canonical_candidate_hash"]

    canonical_path = td / accepted["canonical_candidate_record"]
    assert canonical_path == canonical_candidate_record(td, 2)
    assert changeset.diff.encode("utf-8") == canonical_path.read_bytes()


def test_the_ledger_is_not_rewritten_when_a_later_attempt_arrives(tmp_path, monkeypatch):
    """Append-only. Attempt 1's event must be byte-identical before and after
    attempt 2, or the earlier evidence has been revised rather than added to."""
    td, flow, spend = harness(tmp_path, monkeypatch, [A_RAW, B_RAW])
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]

    app._request_change(flow, spend, "t1", messages, 4000, 1, td)
    after_first = (td / "ledger.jsonl").read_text(encoding="utf-8")

    app._request_change(flow, spend, "t1", messages, 4000, 2, td)
    after_second = (td / "ledger.jsonl").read_text(encoding="utf-8")

    assert after_second.startswith(after_first), "an earlier ledger event was modified"
    assert len(after_second) > len(after_first)


# ----------------------------------------------- provenance fails closed


def test_a_missing_or_corrupted_artifact_is_reported(tmp_path, monkeypatch):
    td, payloads, _ = run_attempts(tmp_path, monkeypatch, [A_RAW, B_RAW])
    first = payloads[0]

    (td / first["normalized_candidate_record"]).write_bytes(b"tampered\n")
    mismatches = candidate_record_mismatches(td, first)
    assert len(mismatches) == 1 and "does not hash" in mismatches[0]

    (td / first["raw_candidate_record"]).unlink()
    mismatches = candidate_record_mismatches(td, first)
    assert len(mismatches) == 2
    assert any("is recorded but absent" in m for m in mismatches)

    # Attempt 2 is untouched by attempt 1's corruption.
    assert candidate_record_mismatches(td, payloads[1]) == ()


def test_a_hash_recorded_without_a_path_is_a_finding_not_a_pass(tmp_path):
    """Audit tooling must not silently accept an event it cannot check.

    The other two stages are absent here as well, and DAR-034 reports that too —
    see `test_dar034_audit_confinement.py` for the mandatory-stage rules."""
    payload = {"attempt": 1, "raw_candidate_hash": "0" * 64}
    findings = candidate_record_mismatches(tmp_path, payload)
    assert findings[0] == "raw: a hash was recorded with no artifact path"


def test_validation_resolves_the_path_the_event_recorded(tmp_path, monkeypatch):
    """Not a path recomputed from today's naming rule — an event pointing
    somewhere else is exactly the defect DAR-033 closes, and recomputing the
    location would hide it."""
    td, payloads, _ = run_attempts(tmp_path, monkeypatch, [A_RAW])
    payload = dict(payloads[0])
    payload["raw_candidate_record"] = "candidate-attempt-009/raw.diff"
    mismatches = candidate_record_mismatches(td, payload)
    assert len(mismatches) == 1
    # DAR-034 refuses this on attempt ownership before it ever looks for the
    # file, which is the stronger statement: the path would still be wrong if
    # some attempt-009 artifact happened to exist.
    assert mismatches[0] == "raw: 'candidate-attempt-009/raw.diff' does not belong to attempt 001"


def test_no_provider_is_reached_by_candidate_persistence():
    import ast
    import inspect

    source = inspect.getsource(app._canonicalize_proposal)
    tree = ast.parse(source.lstrip())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "llm" not in names
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"_request_change", "_accept_or_repair", "post_chat"}
