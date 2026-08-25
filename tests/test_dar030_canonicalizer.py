r"""The deterministic diff canonicaliser, and the boundary it sits on.

A model that chooses the right change and then miscounts the lines it wrote
produces a patch `git apply` calls *corrupt* — a representation failure wearing
the costume of a wrong answer. The preliminary benchmark measured 13 of 15
candidate failures as exactly that, and a post-hoc replay showed that
recomputing `@@ -a,b +c,d @@` from the body each header describes made 9 of them
applicable, all 9 of which then passed the full gate (DAR-029).

So the product recomputes counts. It does nothing else, and the tests below
exist mostly to hold that line: every case where the counts alone cannot fix the
patch must return UNSAFE with the bytes untouched, because the alternative is
deciding what the model meant.

The fixtures under `fixtures/canonicalizer/` are real candidates from the run,
copied byte-for-byte, so the product implementation is checked against the
mechanism that actually recovered the historical fixes rather than against a
hand-written imitation.

No provider call is made anywhere in this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from riftagent.records import (
    CANON_CANONICALIZED,
    CANON_UNCHANGED,
    CANON_UNSAFE,
    canonicalize_patch,
    content_hash,
)


def semantic_lines(diff: str) -> list[str]:
    r"""The DAR-030 safety invariant, kept here because it is no longer product.

    It extracted every hunk content line and compared the list before and after
    canonicalisation. It could **fail open**: a deleted line whose content
    begins `-- ` renders as `--- ...` and was mistaken for a file header, so
    two patches differing only in such a line compared equal (DAR-031).

    `records.authorized_change_only()` is the authority now, and this was
    removed from the runtime rather than left available to reach for. The tests
    below still use it to show what the DAR-030 rule did see.
    """
    out: list[str] = []
    inside = False
    for line in diff.splitlines(keepends=True):
        if line.startswith("@@"):
            inside = True
            continue
        if line.startswith(("diff --git", "index ", "--- ", "+++ ", "old mode", "new mode", "similarity", "rename")):
            inside = False
            continue
        if inside:
            out.append(line)
    return out


FIXTURES = Path(__file__).parent / "fixtures" / "canonicalizer"

VALID = "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"


# ------------------------------------------------------------------ A, B


def test_a_valid_diff_is_left_byte_identical():
    result = canonicalize_patch(VALID)
    assert result.status == CANON_UNCHANGED
    assert result.diff == VALID
    assert content_hash(result.diff) == content_hash(VALID)
    assert result.operations == ()


def test_wrong_counts_are_corrected_and_nothing_else_moves():
    wrong = VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@")
    result = canonicalize_patch(wrong)

    assert result.status == CANON_CANONICALIZED
    assert "@@ -1,2 +1,2 @@" in result.diff
    assert semantic_lines(result.diff) == semantic_lines(wrong)
    assert result.authorized_byte_changes_only
    assert [op["kind"] for op in result.operations] == ["recompute_hunk_counts"]
    assert result.operations[0]["from"] == [7, 7] and result.operations[0]["to"] == [2, 2]


def test_the_start_line_is_never_adjusted():
    """Moving a hunk is choosing a different place to edit."""
    wrong = VALID.replace("@@ -1,2 +1,2 @@", "@@ -400,9 +400,9 @@")
    result = canonicalize_patch(wrong)
    assert result.status == CANON_CANONICALIZED
    assert "@@ -400,2 +400,2 @@" in result.diff


# ------------------------------------------------------------------ C, D


def test_each_hunk_is_counted_independently():
    two = "--- a/m.py\n+++ b/m.py\n@@ -1,9 +1,9 @@\n a\n-b\n+c\n@@ -40,9 +40,9 @@\n x\n y\n-z\n+w\n+v\n"
    result = canonicalize_patch(two)
    assert result.status == CANON_CANONICALIZED
    assert "@@ -1,2 +1,2 @@" in result.diff
    assert "@@ -40,3 +40,4 @@" in result.diff
    assert semantic_lines(result.diff) == semantic_lines(two)
    assert len(result.operations) == 2


def test_multiple_file_sections_are_preserved_exactly():
    multi = (
        "diff --git a/one.py b/one.py\nindex 111..222 100644\n--- a/one.py\n+++ b/one.py\n"
        "@@ -1,9 +1,9 @@\n a\n-b\n+c\n"
        "diff --git a/two.py b/two.py\nindex 333..444 100644\n--- a/two.py\n+++ b/two.py\n"
        "@@ -5,9 +5,9 @@\n d\n-e\n+f\n"
    )
    result = canonicalize_patch(multi)
    assert result.status == CANON_CANONICALIZED
    for header in ("diff --git a/one.py b/one.py", "index 111..222 100644", "--- a/two.py", "+++ b/two.py"):
        assert header in result.diff, header
    assert semantic_lines(result.diff) == semantic_lines(multi)


# ------------------------------------------------------------ E, F, G, H


@pytest.mark.parametrize(
    ("broken", "why"),
    [
        ("--- a/m.py\n+++ b/m.py\n@@ this is not a header @@\n a\n", "will not parse"),
        (VALID.replace("-    return 1\n", "return 1 unprefixed\n"), "no diff prefix"),
        ("--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n", "no body"),
    ],
    ids=["unsafe-header", "prefixless-body-line", "empty-hunk"],
)
def test_what_counts_cannot_fix_is_refused_untouched(broken: str, why: str):
    result = canonicalize_patch(broken)
    assert result.status == CANON_UNSAFE
    assert result.diff == broken, "an unsafe patch was modified anyway"
    assert why in result.reason


def test_the_no_newline_marker_is_carried_through_and_not_counted():
    r"""`\ No newline at end of file` annotates the line above it. It belongs to
    neither side's count, and supplying the missing newline would change what
    the patch says about the file's last byte — content, not metadata."""
    marked = "--- a/m.py\n+++ b/m.py\n@@ -1,9 +1,9 @@\n a\n-b\n+c\n\\ No newline at end of file\n"
    result = canonicalize_patch(marked)
    assert result.status == CANON_CANONICALIZED
    assert "@@ -1,2 +1,2 @@" in result.diff, "the marker was counted as a content line"
    assert "\\ No newline at end of file\n" in result.diff
    assert semantic_lines(result.diff) == semantic_lines(marked)


# ------------------------------------------------------------------ I, J


def test_canonicalisation_is_idempotent():
    wrong = VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@")
    once = canonicalize_patch(wrong)
    twice = canonicalize_patch(once.diff)
    assert twice.diff == once.diff
    assert twice.status == CANON_UNCHANGED


def test_canonicalisation_is_deterministic():
    wrong = VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,9 +1,9 @@")
    a, b = canonicalize_patch(wrong), canonicalize_patch(wrong)
    assert a.diff == b.diff
    assert a.to_dict() == b.to_dict()


def test_the_authorization_invariant_is_enforced_at_runtime(monkeypatch):
    """Not merely asserted in a test. A future rule that altered anything but a
    count must be caught by the code, because no later check could tell an
    altered patch from a model that proposed something else.

    This replaced a `semantic_lines()` comparison that could fail open — see
    `test_dar031_canonicalizer_v2.py` for the case that exposed it."""
    import riftagent.records as R

    monkeypatch.setattr(R, "authorized_change_only", lambda raw, canonical: False)
    wrong = VALID.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@")
    result = R.canonicalize_patch(wrong)
    assert result.status == CANON_UNSAFE
    assert result.authorized_byte_changes_only is False
    assert result.diff == wrong


# --------------------------------------------- historical BM-06 regressions


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.diff").read_text(encoding="utf-8", errors="replace")


def test_the_historical_index_records_where_each_fixture_came_from():
    index = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))
    assert set(index) == {
        "recovered",
        "canonicalized_still_non_applicable",
        "declined_not_a_count_defect",
        "already_good",
    }
    for entry in index.values():
        assert entry["from"].endswith(".diff")


def test_a_historical_candidate_recovered_by_count_repair():
    """`icalendar-60a10375` arm A: a header claiming more lines than its body
    held. git called it corrupt; recomputing the counts makes it applicable."""
    raw = fixture("recovered")
    result = canonicalize_patch(raw)
    assert result.status == CANON_CANONICALIZED
    assert result.authorized_byte_changes_only
    assert semantic_lines(result.diff) == semantic_lines(raw)
    assert all(op["kind"] == "recompute_hunk_counts" for op in result.operations)
    for op in result.operations:
        assert op["to"][0] <= op["from"][0] or op["to"][1] <= op["from"][1], (
            "a header was corrected upward, which is the unsafe direction"
        )


def test_a_historical_candidate_that_canonicalises_and_still_does_not_apply():
    """Count repair is not a promise of applicability. This one's counts were
    short *and* its context does not match the tree."""
    raw = fixture("canonicalized_still_non_applicable")
    result = canonicalize_patch(raw)
    assert result.status == CANON_CANONICALIZED
    assert semantic_lines(result.diff) == semantic_lines(raw)


def test_a_historical_failure_the_canonicaliser_declines():
    """`cachetools-c0fdf6ab` arm B failed at the candidate phase and git called
    it corrupt, yet its body is not *short* of its header — so under the DAR-030
    rule the defect is not one recomputation can prove safe to fix, and the
    patch is left alone.

    This is the DAR-030 rule in isolation, called without a git verdict, and it
    is still the behaviour when git is unavailable. DAR-031 recovers this
    candidate by asking git directly; see
    `test_dar031_canonicalizer_v2.py::test_the_declined_candidate_is_recovered_once_git_is_asked`.
    """
    raw = fixture("declined_not_a_count_defect")
    result = canonicalize_patch(raw)
    assert result.status == CANON_UNCHANGED
    assert result.diff == raw


def test_a_historical_good_candidate_is_byte_identical():
    """`cachetools-c0fdf6ab` arm A applied and verified in the run. An earlier
    version of this canonicaliser rewrote its hunk counts upward — git had read
    exactly the declared lines and ignored the trailing extras — which would
    have broken a working patch. Adding a canonicaliser must not disturb what
    already worked."""
    raw = fixture("already_good")
    result = canonicalize_patch(raw)
    assert result.status == CANON_UNCHANGED
    assert result.diff == raw
    assert content_hash(result.diff) == content_hash(raw)


@pytest.mark.parametrize(
    "name",
    ["recovered", "canonicalized_still_non_applicable", "declined_not_a_count_defect", "already_good"],
)
def test_every_historical_fixture_preserves_its_content(name: str):
    raw = fixture(name)
    assert semantic_lines(canonicalize_patch(raw).diff) == semantic_lines(raw)
