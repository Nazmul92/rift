r"""Three stages between the model's diff and the executable candidate.

DAR-031 persisted a file called `raw-candidate.diff` and hashed it. The value
it wrote had already been through `canonical_diff()`, because
`llm.validate_change` returned `canonical_diff(diff)` rather than the diff — so
the artifact named raw was ingestion-normalised, and the model's actual output
existed nowhere on disk. A reviewer diffing it against a provider transcript
would have found line-ending differences with nothing in the ledger to explain
them.

The fix is not to move a call. It is to say what the pipeline actually is:

    exact model diff  ->  raw-candidate.diff
          |  transport normalisation: CRLF, bare CR, final newline
          v
    normalized        ->  normalized-candidate.diff
          |  git-conditioned hunk-count canonicalisation (DAR-031, unchanged)
          v
    canonical         ->  canonical-candidate.diff  ->  ChangeSet, gate

Two transformations with different authorities. Normalisation rewrites line
terminators anywhere in the file. Canonicalisation may alter only the digits
inside a valid `@@` header and must leave every other byte identical. Reported
as one field, a receipt could say only *that* bytes changed; a reviewer asking
*what* changed would have to take the answer on trust.

The end-to-end tests here drive `app._request_change` with a fake adapter, so
they cross the real validation and persistence boundary rather than calling
`_canonicalize_proposal` directly — the defect being fixed lived precisely in
the step a direct call skips. No provider is configured and no request leaves
the process.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import riftagent.app as app
import riftagent.llm as llm
from riftagent.records import (
    CANON_CANONICALIZED,
    CANON_UNCHANGED,
    ChangeSet,
    EventKind,
    Ledger,
    Pricing,
    SpendLedger,
    authorized_change_only,
    candidate_record_mismatches,
    canonical_candidate_record,
    content_hash,
    normalize_candidate,
    normalized_candidate_record,
    raw_candidate_record,
)

LF = "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
BAD_COUNTS = LF.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@")


# ----------------------------------------------------------------- harness


def drive(tmp_path: Path, monkeypatch, diff: str) -> tuple[dict, tuple[str, str] | None]:
    """One `propose_change` through the real path. Returns the event and result."""

    def post_chat(config, messages, max_output_tokens, timeout_s=120.0, temperature=None):
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

    td = tmp_path / "task"
    td.mkdir()
    flow = app.Flow(Ledger(td / "ledger.jsonl", "t1"), app.LiveRenderer(quiet=True), None, False)
    spend = SpendLedger(
        tmp_path / "spend.jsonl",
        scope="s",
        limit_usd=10.0,
        pricing=Pricing(input_per_mtok=1.0, output_per_mtok=1.0, provider="p", model="fake-model"),
    )
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    result = app._request_change(flow, spend, "t1", messages, 4000, 1, td)

    events = [json.loads(line) for line in flow.ledger.path.read_text(encoding="utf-8").splitlines()]
    canonicalized = [e for e in events if e["kind"] == EventKind.CANDIDATE_CANONICALIZED.value]
    assert len(canonicalized) == 1, "exactly one pipeline record per candidate"
    return canonicalized[0], result


def stages(td: Path) -> tuple[bytes, bytes, bytes]:
    """The three artifacts as bytes. Never `read_text` — universal-newline
    translation would hide the very difference these files exist to record."""
    base = td / "task"
    return (
        raw_candidate_record(base, 1).read_bytes(),
        normalized_candidate_record(base, 1).read_bytes(),
        canonical_candidate_record(base, 1).read_bytes(),
    )


def payload_of(event: dict) -> dict:
    return event.get("payload", event)


# ------------------------------------------------- the boundary, in isolation


def test_validate_change_returns_the_models_bytes_unaltered():
    """The defect at its source. This assertion would have failed under
    DAR-031, where the validator returned `canonical_diff(diff)`."""
    crlf = LF.replace("\n", "\r\n")
    diff, summary = llm.validate_change({"diff": crlf, "summary": "s"})
    assert diff == crlf, "validation transformed the diff it was only asked to validate"
    assert summary == "s"

    unterminated = LF.rstrip("\n")
    assert llm.validate_change({"diff": unterminated, "summary": ""})[0] == unterminated


def test_validation_still_refuses_what_it_always_refused():
    """Returning exact bytes is not a relaxation of the schema."""
    for bad in ({"diff": "", "summary": "s"}, {"diff": 5, "summary": "s"}, {"summary": "s"}):
        with pytest.raises(llm.ValidationError):
            llm.validate_change(bad)
    with pytest.raises(llm.ValidationError):
        llm.validate_change({"diff": LF, "summary": "s", "confidence": 0.9})


# ------------------------------------------------------ the normalised stage


@pytest.mark.parametrize(
    ("raw", "operations"),
    [
        (LF, []),
        (LF.replace("\n", "\r\n"), ["crlf_to_lf"]),
        (LF.replace("\n", "\r"), ["cr_to_lf"]),
        (LF.rstrip("\n"), ["ensure_final_newline"]),
        (LF.replace("\n", "\r\n").rstrip("\r\n"), ["crlf_to_lf", "ensure_final_newline"]),
        ("", []),
    ],
    ids=["already-lf", "crlf", "bare-cr", "no-final-newline", "crlf-and-no-newline", "empty"],
)
def test_normalisation_names_exactly_what_it_did(raw: str, operations: list[str]):
    result = normalize_candidate(raw)
    assert list(result.operations) == operations
    assert result.changed == (result.diff != raw)
    assert result.to_dict() == {"changed": result.changed, "operations": operations}


def test_normalisation_is_canonical_diff_and_nothing_more():
    """Called, not reimplemented, so the two cannot drift apart."""
    from riftagent.records import canonical_diff

    for raw in (LF, LF.replace("\n", "\r\n"), LF.rstrip("\n"), BAD_COUNTS, ""):
        assert normalize_candidate(raw).diff == canonical_diff(raw)


def test_normalisation_is_idempotent():
    once = normalize_candidate(LF.replace("\n", "\r\n"))
    twice = normalize_candidate(once.diff)
    assert twice.diff == once.diff
    assert twice.changed is False and twice.operations == ()


# ------------------------------------------------------------- end-to-end


def test_crlf_survives_in_the_raw_artifact_and_nowhere_after_it(tmp_path, monkeypatch):
    """Item 8. The model wrote CRLF; the record of what the model wrote must
    still say CRLF, and every later stage must not."""
    crlf = LF.replace("\n", "\r\n")
    event, result = drive(tmp_path, monkeypatch, crlf)
    raw, normalized, canonical = stages(tmp_path)

    assert raw == crlf.encode("utf-8"), "the raw artifact was normalised before persistence"
    assert b"\r\n" in raw
    assert b"\r" not in normalized
    assert normalized == LF.encode("utf-8")
    assert canonical == normalized, "these counts are correct; canonicalisation had nothing to do"

    p = payload_of(event)
    assert p["normalization"] == {"changed": True, "operations": ["crlf_to_lf"]}
    assert p["canonicalization"]["status"] == CANON_UNCHANGED
    assert p["raw_candidate_hash"] != p["normalized_candidate_hash"]
    assert p["normalized_candidate_hash"] == p["canonical_candidate_hash"]
    assert result is not None and result[0] == LF


def test_a_missing_final_newline_is_added_by_normalisation_only(tmp_path, monkeypatch):
    """Item 9. `git apply` reports the absent byte as `corrupt patch at line N`,
    which reads like a malformed hunk and is not one."""
    unterminated = LF.rstrip("\n")
    event, _ = drive(tmp_path, monkeypatch, unterminated)
    raw, normalized, canonical = stages(tmp_path)

    assert not raw.endswith(b"\n")
    assert raw == unterminated.encode("utf-8")
    assert normalized.endswith(b"\n")
    assert normalized == unterminated.encode("utf-8") + b"\n"
    assert canonical == normalized

    p = payload_of(event)
    assert p["raw_candidate_hash"] != p["normalized_candidate_hash"]
    assert p["normalization"]["operations"] == ["ensure_final_newline"]


def test_crlf_and_wrong_counts_are_repaired_by_the_stage_that_owns_each(tmp_path, monkeypatch):
    """Item 10, the pipeline regression that matters most.

    Two defects arriving together, each fixed by exactly one stage, and each
    stage provably confined to its own authority: raw -> normalized differs only
    in line terminators, normalized -> canonical only in hunk-count spans."""
    combined = BAD_COUNTS.replace("\n", "\r\n")
    event, result = drive(tmp_path, monkeypatch, combined)
    raw, normalized, canonical = stages(tmp_path)
    raw_s = raw.decode("utf-8")
    norm_s = normalized.decode("utf-8")
    canon_s = canonical.decode("utf-8")

    assert raw_s == combined
    assert norm_s == BAD_COUNTS
    assert canon_s == LF

    # raw -> normalized: line terminators only. Strip them from both and the
    # remaining bytes must be identical, which no content edit could survive.
    assert raw_s.replace("\r\n", "\n") == norm_s
    assert [line.rstrip("\r\n") for line in raw_s.splitlines()] == [line.rstrip("\n") for line in norm_s.splitlines()]

    # normalized -> canonical: the DAR-031 byte mask, unweakened.
    assert authorized_change_only(norm_s, canon_s)
    assert "@@ -1,7 +1,7 @@" in norm_s and "@@ -1,2 +1,2 @@" in canon_s

    p = payload_of(event)
    assert p["normalization"] == {"changed": True, "operations": ["crlf_to_lf"]}
    assert p["canonicalization"]["status"] == CANON_CANONICALIZED
    assert p["canonicalization"]["authorized_byte_changes_only"] is True
    assert [op["kind"] for op in p["canonicalization"]["operations"]] == ["recompute_hunk_counts"]
    assert len({p["raw_candidate_hash"], p["normalized_candidate_hash"], p["canonical_candidate_hash"]}) == 3
    assert result is not None and result[0] == LF


def test_an_already_clean_candidate_passes_through_all_three_stages_identically(tmp_path, monkeypatch):
    """Item 11. The common case must cost the candidate nothing at all."""
    event, _ = drive(tmp_path, monkeypatch, LF)
    raw, normalized, canonical = stages(tmp_path)
    assert raw == normalized == canonical == LF.encode("utf-8")

    p = payload_of(event)
    assert p["raw_candidate_hash"] == p["normalized_candidate_hash"] == p["canonical_candidate_hash"]
    assert p["normalization"] == {"changed": False, "operations": []}
    assert p["canonicalization"]["status"] == CANON_UNCHANGED
    assert p["canonicalization"]["changed"] is False


def test_content_that_looks_like_diff_headers_survives_both_stages(tmp_path, monkeypatch):
    """Item 12. Source lines beginning `--`, `++`, `---`, `+++` render as lines
    a prefix-classifier mistakes for file headers. That is what made
    `semantic_lines()` fail open; neither stage may repeat the mistake."""
    body = (
        "--- a/m.py\r\n+++ b/m.py\r\n@@ -1,9 +1,9 @@\r\n"
        " keep\r\n--- was a comment\r\n-- short dashes\r\n+++ is a comment\r\n++ short pluses\r\n"
    )
    event, _ = drive(tmp_path, monkeypatch, body)
    raw, normalized, canonical = stages(tmp_path)

    assert raw == body.encode("utf-8")
    norm_s = normalized.decode("utf-8")
    canon_s = canonical.decode("utf-8")
    for line in ("--- was a comment", "-- short dashes", "+++ is a comment", "++ short pluses"):
        assert f"{line}\n" in norm_s, line
        assert f"{line}\n" in canon_s, line

    assert authorized_change_only(norm_s, canon_s)
    assert "@@ -1,3 +1,3 @@" in canon_s, "the header-shaped content lines were miscounted"
    assert payload_of(event)["canonicalization"]["authorized_byte_changes_only"] is True


def test_semantic_lines_is_still_absent_from_the_runtime():
    """Its removal was correct: proven fail-open, no runtime callers, replaced
    by a stronger byte-span invariant. DAR-032 does not bring it back."""
    import riftagent.app
    import riftagent.kernel
    import riftagent.llm
    import riftagent.records
    import riftagent.sandbox

    for module in (riftagent.records, riftagent.app, riftagent.llm, riftagent.kernel, riftagent.sandbox):
        assert not hasattr(module, "semantic_lines"), module.__name__


# ------------------------------------------------- hashes and provenance


def test_every_recorded_hash_reconstructs_from_the_bytes_on_disk(tmp_path, monkeypatch):
    """Item 13. A digest that describes something other than the file a
    reviewer opens is worse than no digest, because it looks like evidence."""
    event, _ = drive(tmp_path, monkeypatch, BAD_COUNTS.replace("\n", "\r\n"))
    p = payload_of(event)
    base = tmp_path / "task"

    for stage, path in (
        ("raw", raw_candidate_record(base, 1)),
        ("normalized", normalized_candidate_record(base, 1)),
        ("canonical", canonical_candidate_record(base, 1)),
    ):
        assert p[f"{stage}_candidate_record"] == path.relative_to(base).as_posix()
        assert p[f"{stage}_candidate_hash"] == content_hash(path.read_bytes()), stage
    assert candidate_record_mismatches(base, p) == ()


def test_a_corrupted_artifact_is_reported_rather_than_trusted(tmp_path, monkeypatch):
    event, _ = drive(tmp_path, monkeypatch, LF)
    p = payload_of(event)
    base = tmp_path / "task"

    normalized_candidate_record(base, 1).write_bytes(LF.replace("return 2", "return 3").encode("utf-8"))
    mismatches = candidate_record_mismatches(base, p)
    assert len(mismatches) == 1
    assert "candidate-attempt-001/normalized.diff" in mismatches[0]

    raw_candidate_record(base, 1).unlink()
    mismatches = candidate_record_mismatches(base, p)
    assert len(mismatches) == 2
    assert any("is recorded but absent" in m for m in mismatches)


def test_persistence_fails_closed_when_the_bytes_do_not_survive_the_write(tmp_path, monkeypatch):
    """The hash is taken from a read-back, so a write that silently transformed
    its input — line-ending translation being the exact hazard here — raises
    rather than recording a digest of bytes nobody asked for."""
    from riftagent.records import ValidationError, persist_candidate

    path = tmp_path / "c.diff"
    assert persist_candidate(path, LF) == content_hash(path.read_bytes())

    real = Path.write_text

    def truncating(self, data, *a, **kw):
        return real(self, data[:-1], *a, **kw)

    monkeypatch.setattr(Path, "write_text", truncating)
    with pytest.raises(ValidationError):
        persist_candidate(tmp_path / "d.diff", LF)


def test_the_changeset_is_the_canonical_bytes_and_hashes_identically(tmp_path, monkeypatch):
    """Item 7. Raw and normalized are provenance, not executable alternatives.
    The ChangeSet's content address must be the canonical artifact's digest, or
    withdrawal and reapply would be operating on bytes the ledger never named."""
    event, result = drive(tmp_path, monkeypatch, BAD_COUNTS.replace("\n", "\r\n"))
    p = payload_of(event)
    assert result is not None

    changeset = ChangeSet(diff=result[0], touched_paths=("m.py",), origin="model")
    assert changeset.patch_hash == p["canonical_candidate_hash"]
    assert changeset.diff.encode("utf-8") == canonical_candidate_record(tmp_path / "task", 1).read_bytes()
    assert changeset.patch_hash != p["raw_candidate_hash"]
    assert changeset.patch_hash != p["normalized_candidate_hash"]


def test_the_two_transformations_are_reported_separately(tmp_path, monkeypatch):
    """A single ambiguous `changed` flag cannot answer the only question a
    reviewer has when raw and canonical bytes differ."""
    event, _ = drive(tmp_path, monkeypatch, BAD_COUNTS.replace("\n", "\r\n"))
    p = payload_of(event)

    assert p["attempt"] == 1
    assert set(p["normalization"]) == {"changed", "operations"}
    assert {"status", "changed", "operations", "authorized_byte_changes_only"} <= set(p["canonicalization"])
    assert p["normalization"]["changed"] and p["canonicalization"]["changed"]
    # No field mixes the two.
    assert "operations" not in p and "status" not in p
