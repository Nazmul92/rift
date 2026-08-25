"""Sanity tests for the post-hoc canonicalization replay.

The replay exists to answer why BM-08's canonical candidates applied less often
than BM-07's. It is forensic: it must measure retained bytes without changing
them, without touching a measured result, and without contacting a provider.

These tests pin the properties that make its numbers trustworthy — stage
independence, fresh identity-checked baselines, correct transition
classification, correct rate denominators, retained diagnostics, and a
conservative fallback class — and they check the replay artifact it produced
against the official records it must not have disturbed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).parents[1] / "benchmark"
BM08 = BENCH / "bm08"
if str(BM08) not in sys.path:
    sys.path.append(str(BM08))

import canonicalization_replay as replay  # noqa: E402


def artifact() -> dict:
    return json.loads((BM08 / "canonicalization-replay.json").read_text(encoding="utf-8"))


def records() -> list[dict]:
    return artifact()["records"]


def official() -> dict:
    out = {}
    for line in (BM08 / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[(r["case_id"], r["arm"])] = r
    return out


# ------------------------------------------------------------ stage independence


def test_each_stage_gets_its_own_fresh_baseline_copy():
    """Testing `normalized` on a tree already mutated by `raw` would measure the
    wrong thing, so a copy is made per stage rather than per arm."""
    source = (BM08 / "canonicalization_replay.py").read_text(encoding="utf-8")
    body = source.split("def replay_arm(")[1].split("\ndef ")[0]
    assert "for stage in STAGES:" in body
    copy_line = body.index("shutil.copytree(master, copy")
    loop_line = body.index("for stage in STAGES:")
    assert copy_line > loop_line, "the baseline copy must happen inside the per-stage loop"


def test_every_stage_copy_had_its_identity_verified():
    for record in records():
        for stage in ("raw", "normalized", "canonical"):
            assert record[f"{stage}_baseline_verified"] is True, f"{record['case_id']}/{stage}"


def test_the_replay_only_ever_checks_and_never_applies():
    source = (BM08 / "canonicalization_replay.py").read_text(encoding="utf-8")
    assert '"git", "apply", "--check"' in source
    assert '"git", "apply",\n' not in source
    # No write mode anywhere near the retained patches.
    assert "open(" not in source or "write_bytes" not in source.split("def apply_check")[1]


def test_stages_are_recorded_independently():
    for record in records():
        for stage in ("raw", "normalized", "canonical"):
            assert f"{stage}_apply_ok" in record
            assert f"{stage}_exit_code" in record
            assert f"{stage}_git_diagnostic" in record
            assert f"failure_class_{stage}" in record


# --------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("raw_ok", "canonical_ok", "expected"),
    [
        (False, True, "RESCUED"),
        (True, True, "PRESERVED"),
        (True, False, "DAMAGED"),
        (False, False, "UNRECOVERED"),
    ],
)
def test_transition_classification(raw_ok, canonical_ok, expected):
    assert replay.transition(raw_ok, canonical_ok) == expected


def test_recorded_transitions_match_the_recorded_stage_outcomes():
    for record in records():
        assert record["primary_transition"] == replay.transition(
            record["raw_apply_ok"], record["canonical_apply_ok"]
        ), record["case_id"]


def test_rate_denominators_are_the_governed_ones():
    """rescue over raw failures, damage over raw applicable — not over n."""
    recs = records()
    raw_ok = sum(1 for r in recs if r["raw_apply_ok"])
    raw_fail = len(recs) - raw_ok
    rescued = sum(1 for r in recs if r["primary_transition"] == "RESCUED")
    unrecovered = sum(1 for r in recs if r["primary_transition"] == "UNRECOVERED")
    preserved = sum(1 for r in recs if r["primary_transition"] == "PRESERVED")
    damaged = sum(1 for r in recs if r["primary_transition"] == "DAMAGED")
    assert rescued + unrecovered == raw_fail, "rescue and unrecovered must partition the raw failures"
    assert preserved + damaged == raw_ok, "preserved and damaged must partition the raw applicable"


def test_a_zero_denominator_reports_not_applicable_rather_than_zero():
    import replay_report

    assert replay_report.rate(0, 0) == "N/A"
    assert replay_report.rate(3, 10) == "3/10 = 30.0%"


# ------------------------------------------------------------- failure classes


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        ("error: corrupt patch at line 42", "invalid_or_corrupt_patch"),
        ("error: patch fragment without header at line 3", "bad_hunk_count_or_malformed_header"),
        ("error: src/x.py: does not exist in index", "file_not_found_or_wrong_path"),
        ("error: while searching for:\n    def f():", "context_mismatch"),
        ("error: patch failed: a/b.py:12", "patch_does_not_apply_at_location"),
        ("error: b.py: already exists", "new_file_path_conflict"),
    ],
)
def test_failure_classes_come_from_the_actual_diagnostic(diagnostic, expected):
    assert replay.classify_failure(diagnostic) == expected


def test_an_unrecognised_diagnostic_falls_into_the_conservative_class():
    assert replay.classify_failure("error: something nobody has seen before") == "other_git_apply_failure"
    assert replay.classify_failure("") == "other_git_apply_failure"


def test_every_failure_retained_its_diagnostic_text():
    for record in records():
        for stage in ("raw", "normalized", "canonical"):
            if not record[f"{stage}_apply_ok"]:
                assert record[f"{stage}_git_diagnostic"].strip(), f"{record['case_id']}/{stage}"


def test_representation_and_source_defects_are_kept_apart():
    """'Non-applicable' must never be read as 'canonicalizer failure'."""
    assert replay.defect_level("bad_hunk_count_or_malformed_header") == "representation"
    assert replay.defect_level("invalid_or_corrupt_patch") == "representation"
    assert replay.defect_level("context_mismatch") == "source_or_context"
    assert replay.defect_level("file_not_found_or_wrong_path") == "source_or_context"
    assert replay.defect_level("other_git_apply_failure") == "unclassified"


# --------------------------------------------------- the bytes were not mutated


def test_replayed_bytes_still_match_the_hashes_the_benchmark_recorded():
    from riftagent.records import content_hash

    off = official()
    for record in records():
        recorded = off[(record["case_id"], record["arm"])]
        for stage in ("raw", "normalized", "canonical"):
            path = BM08 / "results-evidence" / record["case_id"] / record["arm"] / f"{stage}.diff"
            assert content_hash(path.read_bytes()) == record[f"{stage}_hash"]
            expected = recorded.get(f"{stage}_candidate_hash")
            if expected:
                assert record[f"{stage}_hash"] == expected, f"{record['case_id']}/{record['arm']}/{stage}"


def test_no_provider_is_reachable_from_the_replay():
    source = (BM08 / "canonicalization_replay.py").read_text(encoding="utf-8")
    for forbidden in ("riftagent.llm", "RIFT_LLM", "propose_change", "requests", "urllib.request"):
        assert forbidden not in source, forbidden
    assert artifact()["provider_calls"] == 0
    assert artifact()["additional_spend_usd"] == 0.0


def test_the_replay_does_not_canonicalize_anything_itself():
    """It measures retained bytes; it must not create replacement bytes."""
    source = (BM08 / "canonicalization_replay.py").read_text(encoding="utf-8")
    for forbidden in ("canonicalize_patch", "normalize_candidate", "canonical_diff("):
        assert forbidden not in source, forbidden


# ------------------------------------------------- the official result is intact


def test_the_official_bm08_result_is_untouched():
    off = official()
    assert len(off) == 48
    correct = {
        arm: sum(
            1
            for (cid, a), r in off.items()
            if a == arm and (r.get("ground_truth") or {}).get("ground_truth_verdict") == "correct"
        )
        for arm in ("A", "C")
    }
    assert correct == {"A": 5, "C": 3}, "the measured BM-08 result changed"
    assert round(sum(r["actual_usd"] for r in off.values()), 4) == 1.8366


def test_the_official_bm07_result_is_untouched():
    recs = [
        json.loads(line)
        for line in (BENCH / "bm07" / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(recs) == 18
    correct = sum(1 for r in recs if (r.get("ground_truth") or {}).get("ground_truth_verdict") == "correct")
    assert correct == 10


def test_the_replay_artifact_is_labelled_as_post_hoc():
    assert artifact()["label"] == replay.BANNER
    assert "NOT AN OFFICIAL BENCHMARK RERUN" in replay.BANNER


def test_bm07_stage_bytes_are_genuinely_absent_rather_than_skipped():
    """The reason BM-07 columns read N/A. `bm07_runner.py` read the stage files
    from the task directory, hashed them, and let the worktree be destroyed."""
    runner = (BENCH / "bm07" / "bm07_runner.py").read_text(encoding="utf-8")
    assert "preserve_arm_evidence" not in runner
    assert not list((BENCH / "bm07").rglob("*.diff"))
    assert not list((BENCH / "bm07").rglob("ledger.jsonl"))
    # ...while the hashes it did record are present, which is why canonical
    # applicability is still recoverable for BM-07 and raw applicability is not.
    first = json.loads((BENCH / "bm07" / "results.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first["raw_candidate_hash"] and first["canonical_candidate_hash"]


# ------------------------------------------------- damage opportunity, corrected


def test_no_raw_applicable_candidate_was_ever_transformed():
    """The damage denominator, stated honestly.

    Reporting `damage_rate = 0/5 = 0%` implied five patches were transformed and
    survived. None were. `canonicalize_patch` returns a git-parseable patch
    UNCHANGED, so a raw-applicable candidate is never at risk and the five
    PRESERVED cases are instances of that guard rather than evidence about it.
    """
    opportunities = [r for r in records() if r["raw_apply_ok"] and r["raw_hash"] != r["canonical_hash"]]
    assert opportunities == [], "a raw-applicable candidate was transformed; damage is now measurable"


def test_the_five_preserved_cases_are_byte_identical_across_all_three_stages():
    preserved = [r for r in records() if r["primary_transition"] == "PRESERVED"]
    assert len(preserved) == 5
    for record in preserved:
        assert record["raw_hash"] == record["normalized_hash"] == record["canonical_hash"], record["case_id"]


def test_a_patch_git_accepts_is_returned_byte_identical():
    """The governing guard, exercised directly rather than inferred."""
    from riftagent.records import CANON_UNCHANGED, canonicalize_patch

    diff = "--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n a\n-b\n+c\n d\n"
    # structural_raw == 0 is git saying the patch parses.
    result = canonicalize_patch(diff, structural_raw=0, structural_recount=0)
    assert result.status == CANON_UNCHANGED
    assert result.diff == diff, "a patch git accepts must never be rewritten"


def test_a_patch_git_rejects_even_after_recount_is_left_alone_as_unsafe():
    from riftagent.records import CANON_UNSAFE, canonicalize_patch

    diff = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-b\n+c\n"
    result = canonicalize_patch(diff, structural_raw=1, structural_recount=1)
    assert result.status == CANON_UNSAFE
    assert result.diff == diff


def test_canonical_applicability_agrees_with_the_official_ground_truth():
    """The replay reproduces the benchmark rather than reinterpreting it."""
    off = official()
    disagreements = []
    for record in records():
        truth = (off[(record["case_id"], record["arm"])].get("ground_truth") or {}).get("applied")
        if truth is None:
            continue
        if bool(truth) != record["canonical_apply_ok"]:
            disagreements.append(f"{record['case_id']}/{record['arm']}")
    assert disagreements == []
