"""Extraction is decided by the operation's validator, not by `json.loads`.

The defect these tests exist to prevent was observed live, not imagined. One
real `propose_change` reply from `claude-sonnet-4-6` contained:

* at offset 243, `{1: 5}` — inside the model's echo of the pytest message
  ``1 unexpectedly found in TLRUCache({1: 5}, maxsize=2, currsize=1)``;
* at offset 1,342, a complete `{"diff": ..., "summary": ...}` that passes
  `validate_change` and produces the right diff.

The old extractor parsed the first balanced span, failed on `{1: 5}`, and
raised — throwing away a good proposal 1,100 characters later, and then buying
a repair request to be told the same thing again.

`tests/fixtures/dar021-captured-reply.txt` is that reply, byte for byte. A
hand-written approximation would drift from what a model actually does; this
cannot.

Parsing alone is not enough either. `{}` is valid JSON and is not a proposal,
so a reply mentioning one before its real answer would still lose. The
acceptance test has to be the frozen typed contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import riftagent.llm as llm

CAPTURED = Path(__file__).parent / "fixtures" / "dar021-captured-reply.txt"

PROPOSAL = {"diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n", "summary": "b"}
OTHER = {"diff": "--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-c\n+d\n", "summary": "d"}


def obj(d: dict) -> str:
    return json.dumps(d)


# ----------------------------------------------------------- the real reply


def test_the_captured_reply_yields_its_proposal():
    """The regression. This reply must now succeed with no repair."""
    text = CAPTURED.read_text(encoding="utf-8")
    assert "{1: 5}" in text, "the fixture no longer contains the brace that caused the defect"

    diff, summary = llm.extract_validated(text, llm.validate_change)
    assert "src/cachetools/__init__.py" in diff
    assert "TLRUCache" in summary


def test_the_captured_reply_has_exactly_one_valid_proposal_among_its_braces():
    text = CAPTURED.read_text(encoding="utf-8")
    assert text.count("{") == 2, "the fixture changed shape"
    candidates = llm.json_candidates(text)
    # `{1: 5}` is not JSON at all, so only one candidate is even parseable.
    assert len(candidates) == 1
    assert sorted(candidates[0]) == ["diff", "summary"]


# -------------------------------------------------------- the required cases


def test_a_non_json_brace_in_prose_does_not_hide_the_proposal():
    text = "The error shows TLRUCache({1: 5}, maxsize=2).\n\n" + obj(PROPOSAL)
    diff, _ = llm.extract_validated(text, llm.validate_change)
    assert diff.startswith("--- a/x.py")


def test_a_valid_but_meaningless_object_does_not_hide_the_proposal():
    """`{}` parses. It is not a proposal, and deciding on parseability alone
    would take it and then buy a repair for a reply that was already correct."""
    text = "The previous state was {}\nHere is my proposal:\n" + obj(PROPOSAL)
    diff, _ = llm.extract_validated(text, llm.validate_change)
    assert diff.startswith("--- a/x.py")


def test_an_object_that_only_looks_like_a_proposal_does_not_hide_the_real_one():
    """A near-miss — right keys, wrong types — must be rejected by the
    validator and stepped over, not accepted because it parsed."""
    text = 'Draft: {"diff": 5, "summary": null}\n\nFinal:\n' + obj(PROPOSAL)
    diff, _ = llm.extract_validated(text, llm.validate_change)
    assert diff.startswith("--- a/x.py")


def test_a_reply_with_no_valid_proposal_still_fails():
    """The repair entitlement is not made redundant — it stops being spent on
    replies that were already correct."""
    with pytest.raises(llm.ModelResponseInvalid):
        llm.extract_validated("I could not work out a fix. {1: 5} {}", llm.validate_change)


def test_a_single_quoted_object_still_fails():
    with pytest.raises(llm.ModelResponseInvalid):
        llm.extract_validated("{'diff': '--- a/x.py', 'summary': 's'}", llm.validate_change)


def test_a_clean_object_is_accepted():
    diff, summary = llm.extract_validated(obj(PROPOSAL), llm.validate_change)
    assert diff.startswith("--- a/x.py") and summary == "b"


def test_a_fenced_object_is_accepted():
    diff, _ = llm.extract_validated("```json\n" + obj(PROPOSAL) + "\n```", llm.validate_change)
    assert diff.startswith("--- a/x.py")


def test_prose_then_a_fenced_object_is_accepted():
    text = "Because `{self.maxsize}` is wrong:\n```json\n" + obj(PROPOSAL) + "\n```"
    diff, _ = llm.extract_validated(text, llm.validate_change)
    assert diff.startswith("--- a/x.py")


def test_two_different_valid_proposals_fail_closed_as_ambiguous():
    """There is no principled way to choose. Picking by position would be
    guessing at which one the model meant."""
    text = "Option one:\n" + obj(PROPOSAL) + "\n\nOr option two:\n" + obj(OTHER)
    with pytest.raises(llm.ModelResponseInvalid, match="ambiguous"):
        llm.extract_validated(text, llm.validate_change)


def test_the_same_proposal_stated_twice_is_not_ambiguous():
    """A reply that gives its answer in prose and again in a fenced block has
    said one thing. Failing closed on that would buy a repair for agreement."""
    text = obj(PROPOSAL) + "\n\nTo restate:\n```json\n" + obj(PROPOSAL) + "\n```"
    diff, _ = llm.extract_validated(text, llm.validate_change)
    assert diff.startswith("--- a/x.py")


# ------------------------------------------------------------- other shapes


def test_an_object_nested_inside_the_accepted_one_is_not_a_rival_candidate():
    nested = {"diff": PROPOSAL["diff"], "summary": "s", "meta": {"diff": "x", "summary": "y"}}
    # `meta` makes it fail strict_fields, so the whole thing is rejected —
    # the point is that the *nested* object is never offered on its own.
    with pytest.raises(llm.ModelResponseInvalid):
        llm.extract_validated(json.dumps(nested), llm.validate_change)


def test_a_proposal_inside_an_unparseable_span_is_still_reachable():
    """Resuming inside a span that failed to parse, rather than past it."""
    text = "{not json, but wrapping " + obj(PROPOSAL) + "}"
    diff, _ = llm.extract_validated(text, llm.validate_change)
    assert diff.startswith("--- a/x.py")


def test_an_unterminated_object_is_not_accepted():
    with pytest.raises(llm.ModelResponseInvalid):
        llm.extract_validated('{"diff": "--- a/x.py", "summary": ', llm.validate_change)


def test_a_scalar_or_empty_array_is_not_a_proposal():
    for text in ("[]", '"just a string"', "42", "[1, 2, 3]"):
        with pytest.raises(llm.ModelResponseInvalid):
            llm.extract_validated(text, llm.validate_change)


def test_a_proposal_wrapped_in_a_single_element_array_is_accepted():
    """A deliberate change from the old contract, which required the *top-level*
    value to be an object and so rejected this outright.

    Under the frozen invariant the acceptance test is the operation's validator:
    here exactly one object in the reply satisfies it, and the surrounding
    brackets are a serialisation quirk rather than a second meaning. A list of
    two *different* valid proposals is still ambiguous and still fails closed,
    which is the case where the wrapper would actually carry meaning."""
    diff, _ = llm.extract_validated("[" + obj(PROPOSAL) + "]", llm.validate_change)
    assert diff.startswith("--- a/x.py")

    with pytest.raises(llm.ModelResponseInvalid, match="ambiguous"):
        llm.extract_validated("[" + obj(PROPOSAL) + ", " + obj(OTHER) + "]", llm.validate_change)


def test_candidate_scanning_is_bounded():
    """An adversarial reply cannot make extraction quadratic without limit."""
    text = "{" * 5000 + obj(PROPOSAL)
    assert len(llm.json_candidates(text, limit=8)) <= 8
