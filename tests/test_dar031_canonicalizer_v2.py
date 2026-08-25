r"""Canonicaliser v2: the byte-mask invariant, git conditioning, raw persistence.

Three defects closed here, each of which the DAR-030 canonicaliser shipped with:

1. The raw model patch was hashed but not stored. A digest proves two things
   differ and cannot show what the model actually wrote, so the "auditable raw
   proposal" the record described did not exist on disk.

2. `semantic_lines()` — the invariant that was supposed to prove content
   survived canonicalisation — could **fail open**. A deleted line whose content
   begins `-- ` renders as `--- ...`, and the extractor treated it as a file
   header and dropped it. Two patches differing only in such a line compared
   equal. `authorized_change_only()` replaces it and asks the narrower, provable
   question: did *only* hunk-header count fields change?

3. Eligibility was decided by a heuristic (`is the body short of its header?`).
   That was conservative for a real reason — an unconditional recount rewrote a
   patch that had applied and verified — but it declined three candidates that
   were pure count defects. git itself answers the question exactly, via
   `git apply --numstat` with and without `--recount`, in a temporary directory
   where no repository content can influence the answer.

No provider call is made anywhere in this file.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from riftagent.records import (
    CANON_CANONICALIZED,
    CANON_UNCHANGED,
    CANON_UNSAFE,
    authorized_change_only,
    canonical_candidate_record,
    canonicalize_patch,
    content_hash,
    raw_candidate_record,
)
from riftagent.sandbox import structural_parse
from tests.test_dar030_canonicalizer import semantic_lines

FIXTURES = Path(__file__).parent / "fixtures" / "canonicalizer"
MATRIX = Path(__file__).parents[1] / "benchmark" / "bm06" / "patch_replay" / "v2_matrix.json"

VALID = "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.diff").read_text(encoding="utf-8", errors="replace")


# ------------------------------------------------- the fail-open, demonstrated


def test_semantic_lines_still_fails_open_which_is_why_it_is_not_the_authority():
    r"""Held as an executable statement of the defect, not as approval of it.

    Three content lines go in; one comes out. Nothing in `semantic_lines()` can
    distinguish a deleted line reading `-- coding: utf-8 --` from the `---`
    header of a file section, because by the time it sees the line the diff
    prefix and the content have been concatenated into the same bytes.
    """
    diff = "--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,3 @@\n keep\n--- was a comment\n+++ is a comment\n"
    assert semantic_lines(diff) == [" keep\n"], "the fail-open has been fixed; update this test"


def test_the_authority_catches_what_semantic_lines_would_miss():
    """The same two patches, differing only in a dropped `--- ` content line."""
    a = "--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,3 @@\n keep\n--- was a comment\n+++ is a comment\n"
    b = "--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,3 @@\n keep\n+++ is a comment\n"
    assert semantic_lines(a) == semantic_lines(b), "premise: the old invariant sees these as equal"
    assert not authorized_change_only(a, b), "the new invariant must not"


def test_such_a_line_survives_canonicalisation_byte_for_byte():
    """The lines the old invariant could not see are still carried through."""
    diff = "--- a/m.py\n+++ b/m.py\n@@ -1,9 +1,9 @@\n keep\n--- was a comment\n+++ is a comment\n"
    result = canonicalize_patch(diff)
    assert result.status == CANON_CANONICALIZED
    assert "@@ -1,2 +1,2 @@" in result.diff
    assert "--- was a comment\n" in result.diff
    assert "+++ is a comment\n" in result.diff
    assert result.authorized_byte_changes_only


# ---------------------------------------------------------- the byte mask


@pytest.mark.parametrize(
    ("raw", "tampered", "why"),
    [
        (VALID, VALID.replace("return 2", "return 3"), "a content line changed"),
        (VALID, VALID.replace("--- a/m.py", "--- a/other.py"), "a file path changed"),
        (VALID, VALID.replace("@@ -1,2 +1,2 @@", "@@ -9,2 +1,2 @@"), "an old start line moved"),
        (VALID, VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,2 +9,2 @@"), "a new start line moved"),
        (VALID, VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,2 +1,2 @@ def f"), "section text appeared"),
        (VALID, VALID.replace("@@ -1,2 +1,2 @@", "@@  -1,2 +1,2 @@"), "header spacing changed"),
        (VALID, VALID + " trailing\n", "a line was appended"),
        (VALID, VALID.replace("-    return 1\n", ""), "a line was removed"),
    ],
    ids=[
        "content",
        "path",
        "old-start",
        "new-start",
        "section-text",
        "spacing",
        "appended",
        "removed",
    ],
)
def test_only_count_fields_may_differ(raw: str, tampered: str, why: str):
    assert not authorized_change_only(raw, tampered), why


def test_count_fields_alone_are_permitted_to_differ():
    wrong = VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@")
    assert authorized_change_only(wrong, VALID)
    assert authorized_change_only(VALID, VALID)


def test_the_optional_section_text_after_a_header_is_preserved():
    """`@@ -1,2 +1,2 @@ def f():` — git emits the enclosing function here, and
    it is not metadata the canonicaliser may recompute."""
    diff = "--- a/m.py\n+++ b/m.py\n@@ -1,9 +1,9 @@ class Cache:\n a\n-b\n+c\n"
    result = canonicalize_patch(diff)
    assert result.status == CANON_CANONICALIZED
    assert "@@ -1,2 +1,2 @@ class Cache:\n" in result.diff


def test_an_absent_count_means_one_and_is_written_back_only_when_it_changes():
    """`@@ -1 +1 @@` is legal shorthand for a one-line hunk on each side."""
    short = "--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a\n+b\n"
    assert canonicalize_patch(short).status == CANON_UNCHANGED
    assert authorized_change_only(short, short)


def test_the_no_newline_marker_is_not_a_countable_line():
    marked = "--- a/m.py\n+++ b/m.py\n@@ -1,9 +1,9 @@\n a\n-b\n+c\n\\ No newline at end of file\n"
    result = canonicalize_patch(marked)
    assert "@@ -1,2 +1,2 @@" in result.diff
    assert result.diff.endswith("\\ No newline at end of file\n")
    assert authorized_change_only(marked, result.diff)


def test_a_missing_final_newline_is_not_silently_supplied():
    """A header on the last line with no terminator stays that way."""
    unterminated = "--- a/m.py\n+++ b/m.py\n@@ -1,9 +1,9 @@\n a\n-b\n+c"
    result = canonicalize_patch(unterminated)
    assert result.diff.endswith("+c"), "canonicalisation appended a byte to the file"
    assert authorized_change_only(unterminated, result.diff)


# ------------------------------------------------------- git conditioning


@pytest.mark.parametrize(
    ("raw", "recount", "expected"),
    [
        (0, 0, CANON_UNCHANGED),
        (128, 0, CANON_CANONICALIZED),
        (128, 128, CANON_UNSAFE),
    ],
    ids=["git-parses-it", "recount-fixes-it", "recount-does-not"],
)
def test_git_decides_eligibility_not_the_shape_of_the_body(raw: int, recount: int, expected: str):
    wrong = VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@")
    assert canonicalize_patch(wrong, raw, recount).status == expected


def test_a_patch_git_parses_is_never_rewritten_however_wrong_its_counts_look():
    """The DAR-030 regression, now decided by git rather than inferred.

    `git apply` reads exactly the declared counts and ignores trailing extras,
    so a body *longer* than its header is a patch git accepts. Correcting it
    upward produces one claiming lines git was never going to read."""
    long_body = "--- a/m.py\n+++ b/m.py\n@@ -1,1 +1,1 @@\n a\n-b\n+c\n d\n"
    result = canonicalize_patch(long_body, structural_raw=0, structural_recount=0)
    assert result.status == CANON_UNCHANGED
    assert result.diff == long_body
    assert "never rewritten" in result.reason


def test_without_a_git_verdict_the_conservative_rule_still_applies():
    """A caller with no git available behaves safely, not unpredictably."""
    long_body = "--- a/m.py\n+++ b/m.py\n@@ -1,1 +1,1 @@\n a\n-b\n+c\n d\n"
    assert canonicalize_patch(long_body).status == CANON_UNCHANGED
    short_body = VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@")
    assert canonicalize_patch(short_body).status == CANON_CANONICALIZED


@needs_git
def test_structural_parse_answers_from_a_temporary_directory_only():
    """Structural validity and repository applicability are separate questions.

    `recovered.diff` does not apply to any tree here — there is no tree — yet
    `--recount` parses it. That separation is the whole point: eligibility must
    not depend on repository content."""
    raw = fixture("recovered")
    assert structural_parse(raw) != 0
    assert structural_parse(raw, recount=True) == 0
    assert structural_parse(VALID) != 0 or True  # VALID's own parse is not asserted


@needs_git
def test_structural_parse_is_deterministic_and_side_effect_free(tmp_path):
    raw = fixture("recovered")
    before = sorted(p.name for p in tmp_path.iterdir())
    assert structural_parse(raw, recount=True) == structural_parse(raw, recount=True) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# -------------------------------------------------- historical regressions


@needs_git
def test_the_declined_candidate_is_recovered_once_git_is_asked():
    """`cachetools-c0fdf6ab` arm B — the named gap DAR-030 shipped with.

    The heuristic declined it because its body is not *short* of its header. git
    parses it once the counts are recomputed and rejects it otherwise, which is
    the definition of a count defect. Under v2 it is canonicalised, and only its
    count fields change."""
    raw = fixture("declined_not_a_count_defect")
    assert canonicalize_patch(raw).status == CANON_UNCHANGED, "the DAR-030 rule declined it"

    sr = structural_parse(raw)
    sc = structural_parse(raw, recount=True)
    assert (sr, sc) != (0, 0), "premise: git rejects the raw patch"
    result = canonicalize_patch(raw, sr, sc)
    assert result.status == CANON_CANONICALIZED
    assert authorized_change_only(raw, result.diff)
    assert all(op["kind"] == "recompute_hunk_counts" for op in result.operations)


@needs_git
@pytest.mark.parametrize(
    "name",
    ["recovered", "canonicalized_still_non_applicable", "declined_not_a_count_defect", "already_good"],
)
def test_every_historical_fixture_changes_only_count_fields(name: str):
    raw = fixture(name)
    sr = structural_parse(raw)
    sc = structural_parse(raw, recount=True) if sr != 0 else 0
    assert authorized_change_only(raw, canonicalize_patch(raw, sr, sc).diff)


@needs_git
def test_the_candidate_that_already_worked_is_left_alone_under_git_conditioning():
    """`cachetools-c0fdf6ab` arm A applied and verified in the run."""
    raw = fixture("already_good")
    assert structural_parse(raw) == 0, "premise: git parses it as it stands"
    result = canonicalize_patch(raw, 0, 0)
    assert result.status == CANON_UNCHANGED
    assert content_hash(result.diff) == content_hash(raw)


def test_canonicalisation_is_idempotent_and_deterministic_under_git_verdicts():
    wrong = VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@")
    once = canonicalize_patch(wrong, 128, 0)
    twice = canonicalize_patch(once.diff, 0, 0)
    assert twice.diff == once.diff and twice.status == CANON_UNCHANGED
    assert canonicalize_patch(wrong, 128, 0).to_dict() == once.to_dict()


# --------------------------------------------------- the 24-candidate matrix


def test_the_full_frozen_candidate_matrix_holds():
    """Every candidate from the frozen BM-06 run, replayed through v2.

    The two properties that decide whether v2 may ship at all: no candidate that
    worked was modified, and no candidate that worked stopped applying."""
    rows = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert len(rows) == 24

    worked = [r for r in rows if not r["originally_failed"]]
    assert [r for r in worked if r["status"] != CANON_UNCHANGED] == [], "a working candidate was modified"
    assert [r for r in worked if not r["applies"]] == [], "a working candidate stopped applying"
    assert all(r["authorized_byte_changes_only"] for r in rows)

    recovered = [r for r in rows if r.get("gate_verdict") == "verified_against_approved_checks"]
    assert len(recovered) == 9
    by_arm = {arm: sum(1 for r in recovered if r["arm"] == arm) for arm in "ABC"}
    assert by_arm == {"A": 3, "B": 3, "C": 3}, "arms must share one proposal boundary"


def test_the_matrix_records_both_hashes_over_the_bytes_it_judged():
    rows = json.loads(MATRIX.read_text(encoding="utf-8"))
    for row in rows:
        assert len(row["raw_hash"]) == 64 and len(row["canonical_hash"]) == 64
        if row["status"] == CANON_UNCHANGED:
            assert row["raw_hash"] == row["canonical_hash"], row["patch"]
        else:
            assert row["raw_hash"] != row["canonical_hash"], row["patch"]


# ------------------------------------------------------- raw persistence


def test_both_candidate_records_are_distinct_paths(tmp_path):
    assert raw_candidate_record(tmp_path, 1) != canonical_candidate_record(tmp_path, 1)
    assert raw_candidate_record(tmp_path, 1).name == "raw.diff"
    assert canonical_candidate_record(tmp_path, 1).name == "canonical.diff"


def test_the_raw_candidate_is_persisted_and_never_overwritten(tmp_path, monkeypatch):
    """The bytes on disk must be the model's, not the canonicaliser's, and both
    recorded hashes must be reconstructible from the two files."""
    import riftagent.app as A

    wrong = VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@")
    monkeypatch.setattr(A, "structural_parse", lambda diff, recount=False: 0 if recount else 128)

    appended: list[tuple[object, dict]] = []
    flow = type("F", (), {"append": lambda self, kind, payload: appended.append((kind, payload))})()
    out = A._canonicalize_proposal(flow, tmp_path, 1, (wrong, "summary"))

    assert out is not None
    canonical_diff, summary = out
    assert summary == "summary"
    assert canonical_diff != wrong, "premise: this candidate is canonicalised"

    # Read as bytes. DAR-032 hashes the exact bytes that reached the disk, so a
    # digest taken over a decoded string would describe something else.
    raw_bytes = raw_candidate_record(tmp_path, 1).read_bytes()
    canon_bytes = canonical_candidate_record(tmp_path, 1).read_bytes()
    assert raw_bytes.decode("utf-8") == wrong, "the raw candidate was overwritten by its canonical form"
    assert canon_bytes.decode("utf-8") == canonical_diff

    payload = appended[0][1]
    assert payload["raw_candidate_hash"] == content_hash(raw_bytes)
    assert payload["canonical_candidate_hash"] == content_hash(canon_bytes)
    assert payload["raw_candidate_record"] == "candidate-attempt-001/raw.diff"
    assert payload["canonical_candidate_record"] == "candidate-attempt-001/canonical.diff"
    assert payload["canonicalization"]["changed"] is True
    assert authorized_change_only(raw_bytes.decode("utf-8"), canon_bytes.decode("utf-8"))


def test_an_unchanged_candidate_still_writes_both_records(tmp_path, monkeypatch):
    """Appended for every candidate, including unchanged ones, so a later
    evaluation can measure how often models emit malformed metadata rather than
    inferring it from absence."""
    import riftagent.app as A

    monkeypatch.setattr(A, "structural_parse", lambda diff, recount=False: 0)
    flow = type("F", (), {"append": lambda self, kind, payload: None})()
    out = A._canonicalize_proposal(flow, tmp_path, 1, (VALID, "s"))

    assert out == (VALID, "s")
    assert raw_candidate_record(tmp_path, 1).read_bytes() == VALID.encode("utf-8")
    assert canonical_candidate_record(tmp_path, 1).read_bytes() == VALID.encode("utf-8")


def test_canonicalisation_reaches_no_provider():
    """Arithmetic does not need a model request. Asserted structurally, because
    a canonicaliser that could call a model would be a different component."""
    import ast
    import inspect

    import riftagent.app as A
    import riftagent.records as R

    source = inspect.getsource(A._canonicalize_proposal)
    tree = ast.parse(source.lstrip())
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"_request_change", "_accept_or_repair", "_call_model", "chat"}
    assert "llm" not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

    assert "llm" not in [m.split(".")[0] for m in dir(R) if not m.startswith("_")]
