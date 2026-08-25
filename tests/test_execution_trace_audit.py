"""Execution-trace premise audit — regressions. Zero cost, no provider.

The audit's conclusion rests on three properties, and each is pinned below:
the right six cases were measured, tracing did not disturb the failure being
observed, and file execution was never allowed to stand in for region execution.

The classification helper gets the most attention. `2/3` becoming "stable true"
would be the single easiest way to turn a shaky result into a confident one, so
the promotion is tested from both sides.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
AUDIT = ROOT / "benchmark" / "analysis" / "execution_trace_audit"
ATTRIBUTION = ROOT / "benchmark" / "analysis" / "context_attribution" / "context-miss-attribution.jsonl"
BM08 = ROOT / "benchmark" / "bm08"
REP = ROOT / "benchmark" / "analysis" / "representation"
sys.path.insert(0, str(Path(__file__).parent))

from benchmark_modules import load  # noqa: E402

audit_mod = load("analysis/execution_trace_audit", "run_trace_audit")

SIX = (
    "click-a17b5447",
    "dnspython-227eace4",
    "dnspython-c9f6c819",
    "isort-49bb9bab",
    "lark-96873d64",
    "mistune-f4237046",
)


def result() -> dict:
    return json.loads((AUDIT / "execution-trace-audit.json").read_text(encoding="utf-8"))


def manifest() -> dict:
    return json.loads((AUDIT / "trace-audit-manifest.json").read_text(encoding="utf-8"))


def cases() -> list[dict]:
    return result()["cases"]


# ---------------------------------------------------------------- case set


def test_the_exact_six_never_discovered_cases_were_audited():
    rows = [json.loads(line) for line in ATTRIBUTION.read_text(encoding="utf-8").splitlines() if line.strip()]
    frozen = sorted(
        r["case_id"]
        for r in rows
        if r["selector_miss_class"] in ("TRACEBACK_DID_NOT_REACH_FIX_FILE", "GREP_DID_NOT_FIND_FIX_REGION")
    )
    assert frozen == sorted(SIX)
    assert sorted(c["case_id"] for c in cases()) == sorted(SIX)
    assert sorted(c["case_id"] for c in manifest()["cases"]) == sorted(SIX)


def test_the_attribution_buckets_are_carried_from_the_frozen_artifact():
    rows = {
        json.loads(line)["case_id"]: json.loads(line)["selector_miss_class"]
        for line in ATTRIBUTION.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    for case in cases():
        assert case["attribution_bucket"] == rows[case["case_id"]]


def test_the_manifest_was_frozen_with_its_thresholds():
    frozen = manifest()
    assert frozen["repeats"] == 3
    rules = frozen["decision_rule"]
    assert rules["EXECUTION_CITATION_STRONGLY_SUPPORTED"].startswith(">=5/6")
    assert rules["EXECUTION_CITATION_NOT_SUPPORTED"].startswith("<=2/6")
    assert frozen["execution_trace_audit_manifest_hash"]
    assert frozen["provider_calls"] == 0 and frozen["additional_spend_usd"] == 0.0


# ------------------------------------------------------- observation discipline


def test_three_observations_per_case_each_in_its_own_process():
    for case in cases():
        assert len(case["observations"]) == 3, case["case_id"]
        assert audit_mod.REPEATS == 3
    source = (AUDIT / "trace_observe.py").read_text(encoding="utf-8")
    assert "process boundary" in source
    # Each observation is a separate interpreter invocation.
    driver = (AUDIT / "run_trace_audit.py").read_text(encoding="utf-8")
    assert "sys.executable" in driver and "for repeat in range(1, REPEATS + 1)" in driver


def test_the_untraced_failure_identity_is_checked_before_tracing():
    driver = (AUDIT / "run_trace_audit.py").read_text(encoding="utf-8")
    body = driver.split('for entry in manifest["cases"]:')[1]
    assert body.index("governed_identity(") < body.index("for repeat in range(1, REPEATS + 1)")
    for case in cases():
        assert "governed_matches_frozen" in case


def test_a_case_whose_governed_identity_misses_is_blocked_not_traced():
    for case in cases():
        if not case["governed_matches_frozen"]:
            assert case["status"] == "BLOCKED_FAILURE_IDENTITY"
            assert case["observations"] == []


def test_every_observation_records_both_identities():
    for case in cases():
        for observation in case["observations"]:
            assert "failure_identity_untraced" in observation
            assert "failure_identity_traced" in observation
            assert "identity_invariant" in observation


def test_a_perturbed_trace_is_marked_invalid_and_contributes_nothing():
    for case in cases():
        for observation in case["observations"]:
            if not observation["identity_invariant"]:
                assert observation["valid"] is False
                assert observation.get("invalid_reason") == "INVALID_OBSERVER_PERTURBATION"
                assert observation["fix_file_executed"] == {}
                assert observation["fix_region_executed"] == {}


def test_the_one_invalid_case_is_reported_invalid_rather_than_relaxed():
    """isort-49bb9bab differs only by a memory address; the rule was kept."""
    isort = next(c for c in cases() if c["case_id"] == "isort-49bb9bab")
    assert isort["status"] == "TRACE_INVALID"
    assert isort["valid_observations"] == 0
    assert isort["file_status"] == audit_mod.TRACE_INVALID
    # Its governed identity nonetheless matched the frozen official one.
    assert isort["governed_matches_frozen"] is True
    text = (AUDIT / "EXECUTION-TRACE-PREMISE-AUDIT.md").read_text(encoding="utf-8")
    assert "not re-run under a relaxed rule" in " ".join(text.split())
    assert "0x" in text, "the memory-address diagnosis should be shown, not asserted"


# ------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("observations", "expected"),
    [
        ([True, True, True], "STABLE_3_OF_3"),
        ([False, False, False], "STABLE_0_OF_3"),
        ([True, True, False], "UNSTABLE_2_OF_3"),
        ([True, False, False], "UNSTABLE_1_OF_3"),
        ([True, True, None], "TRACE_INVALID"),
        ([None, None, None], "TRACE_INVALID"),
    ],
)
def test_stability_classification(observations, expected):
    assert audit_mod.classify(observations) == expected


def test_two_of_three_is_never_promoted_to_stable_true():
    assert audit_mod.classify([True, True, False]) != audit_mod.STABLE_TRUE
    assert audit_mod.classify([True, True, False]) == audit_mod.UNSTABLE_2


def test_recorded_statuses_match_the_recorded_observations():
    for case in cases():
        for path, statuses in (case.get("per_file") or {}).items():
            expected = audit_mod.classify(
                [o["fix_file_executed"].get(path) if o["valid"] else None for o in case["observations"]]
            )
            assert statuses["file_status"] == expected, f"{case['case_id']}/{path}"


# --------------------------------------------- file versus region, kept apart


def test_file_execution_and_region_execution_are_measured_separately():
    for case in cases():
        for observation in case["observations"]:
            if observation["valid"]:
                assert set(observation["fix_file_executed"]) == set(observation["fix_region_executed"])
    # And the audit actually contains a file-yes / region-no result, which is the
    # distinction the separation exists to preserve.
    split = [
        (c["case_id"], path)
        for c in cases()
        for path, v in (c.get("per_file") or {}).items()
        if v["file_status"] == "STABLE_3_OF_3" and v["region_status"] == "STABLE_0_OF_3"
    ]
    assert split, "no file-executed / region-not-executed case survived the join"


def test_multi_file_aggregation_distinguishes_any_from_all():
    for case in cases():
        statuses = [v["file_status"] for v in (case.get("per_file") or {}).values()]
        if not statuses:
            continue
        assert case["all_fix_files_executed"] == all(s == "STABLE_3_OF_3" for s in statuses)
        assert case["any_fix_file_executed"] == ("STABLE_3_OF_3" in statuses)
    # A case with one executed file out of several must not read as full coverage.
    partial = [c for c in cases() if c["any_fix_file_executed"] and not c["all_fix_files_executed"]]
    assert partial, "expected at least one ANY-but-not-ALL multi-file case"


def test_execution_receipts_are_retained_for_positive_observations():
    for case in cases():
        for observation in case["observations"]:
            for path, executed in observation["fix_file_executed"].items():
                if executed:
                    receipt = observation["executed_line_receipt"][path]
                    assert receipt["executed_line_count"] > 0
                    assert receipt["executed_lines_hash"]
                    assert receipt["min_line"] <= receipt["max_line"]
            assert observation["trace_artifact_hash"]
            assert observation["baseline_tree_hash"]


def test_execution_is_measured_conservatively():
    source = (AUDIT / "trace_observe.py").read_text(encoding="utf-8")
    # Line events, not import or discovery.
    assert 'if event == "line"' in source
    assert "sys.settrace" in source
    manifest_rule = manifest()["file_execution_rule"]
    assert "imported, discovered, on sys.path" in manifest_rule


# ------------------------------------------------------------------ leakage


def test_no_model_or_provider_path_exists():
    for name in ("run_trace_audit.py", "trace_observe.py"):
        source = (AUDIT / name).read_text(encoding="utf-8")
        for forbidden in ("import socket", "urllib.request", "urlopen(", "RIFT_LLM", "requests.post", "riftagent.llm"):
            assert forbidden not in source, f"{name} references {forbidden}"
    assert result()["provider_calls"] == 0
    assert result()["additional_spend_usd"] == 0.0


def test_the_historical_fix_never_reaches_a_model_facing_artifact():
    representation = json.dumps(json.loads((REP / "representation-manifest.json").read_text(encoding="utf-8")))
    for marker in ("fix_regions", "fix_files", "fix_commit", "executed_line_receipt"):
        assert marker not in representation, f"{marker} leaked into the representation manifest"
    # The audit writes only inside its own directory.
    driver = (AUDIT / "run_trace_audit.py").read_text(encoding="utf-8")
    assert 'OUT = HERE / "execution-trace-audit.json"' in driver
    assert 'MANIFEST = HERE / "trace-audit-manifest.json"' in driver


def test_no_upstream_replacement_bytes_are_retained():
    blob = json.dumps(result())
    assert "replace" not in blob.lower() or "fix_regions" in blob
    for case in cases():
        for spans in (case.get("fix_regions") or {}).values():
            for span in spans:
                assert isinstance(span, list) and len(span) == 2, "regions are line ranges, not content"


# ---------------------------------------------- nothing frozen was disturbed


def test_the_official_bm08_result_is_unchanged():
    records = [
        json.loads(line) for line in (BM08 / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(records) == 48
    correct = {
        arm: sum(
            1
            for r in records
            if r["arm"] == arm and (r.get("ground_truth") or {}).get("ground_truth_verdict") == "correct"
        )
        for arm in ("A", "C")
    }
    assert correct == {"A": 5, "C": 3}


def test_the_representation_experiment_is_still_on_hold():
    frozen = json.loads((REP / "representation-manifest.json").read_text(encoding="utf-8"))
    assert frozen["budget"]["authorized"] is False
    assert frozen["budget"]["spent"] == 0.0
    assert not (REP / "representation-results.jsonl").exists()
    assert not (REP / "representation-ledger.jsonl").exists()


def test_the_selector_and_canonicalizer_are_untouched():
    import hashlib

    assert (
        hashlib.sha256((ROOT / "src" / "riftagent" / "app.py").read_bytes()).hexdigest()
        == "640d605c5650dd3c31cc57219bcf71e68b2d27115f60e4befff9f6b3af880e7b"
    )
    assert (
        hashlib.sha256((ROOT / "src" / "riftagent" / "records.py").read_bytes()).hexdigest()
        == "507b141fcdc3cadd4cb934753334e6bffa8fc7e7563f43457ccb8e81fb3cba64"
    )


def test_the_audit_did_not_implement_selector_v2():
    """It decides DAR input; it does not change selection."""
    text = (AUDIT / "EXECUTION-TRACE-PREMISE-AUDIT.md").read_text(encoding="utf-8")
    assert "NOT implemented" in text or "not implemented" in text.lower()
    # Whitespace-tolerant: the phrase is line-wrapped in the document.
    flattened = " ".join(text.split())
    assert "fresh historical-bug selector corpus not used in its design" in flattened
    driver = (AUDIT / "run_trace_audit.py").read_text(encoding="utf-8")
    assert "riftagent/app.py" not in driver


# ------------------------------------------------- aggregate authority (blocked)


def aggregates() -> dict:
    """All three candidate aggregates, from the unchanged observations."""
    data = cases()
    per = [v for c in data for v in (c.get("per_file") or {}).values()]
    return {
        "case_any_file": sum(1 for c in data if c.get("any_fix_file_executed")),
        "case_all_file": sum(1 for c in data if c.get("all_fix_files_executed")),
        "case_any_region": sum(1 for c in data if c.get("any_fix_region_executed")),
        "case_all_region": sum(1 for c in data if c.get("all_fix_regions_executed")),
        "per_file": sum(1 for v in per if v["file_status"] == "STABLE_3_OF_3"),
        "per_region": sum(1 for v in per if v["region_status"] == "STABLE_3_OF_3"),
        "files_total": len(per),
    }


def test_the_three_candidate_aggregates_are_pinned():
    """The numbers the escalation rests on, so they cannot drift silently."""
    assert aggregates() == {
        "case_any_file": 5,
        "case_all_file": 3,
        "case_any_region": 5,
        "case_all_region": 2,
        "per_file": 10,
        "per_region": 8,
        "files_total": 13,
    }


def test_the_aggregate_choice_changes_the_verdict():
    """Why the ambiguity is material rather than cosmetic."""

    def band(hits: int, total: int) -> str:
        fraction = hits / total
        if fraction >= 5 / 6:
            return "STRONGLY"
        if fraction >= 3 / 6:
            return "PARTIALLY"
        return "NOT"

    counts = aggregates()
    assert band(counts["case_any_file"], 6) == "STRONGLY"
    assert band(counts["case_all_file"], 6) == "PARTIALLY"
    assert band(counts["per_file"], counts["files_total"]) == "PARTIALLY"
    assert band(counts["case_all_region"], 6) == "NOT"
    # Same observations, three different verdicts.
    assert (
        len(
            {
                band(counts["case_any_file"], 6),
                band(counts["case_all_file"], 6),
                band(counts["case_all_region"], 6),
            }
        )
        == 3
    )


def test_the_frozen_threshold_never_specifies_any_or_all():
    """The manifest's unit word and denominator name different units."""
    rule = manifest()["decision_rule"]["EXECUTION_CITATION_STRONGLY_SUPPORTED"]
    assert "fix files" in rule and "5/6" in rule
    # Six cases, thirteen fix files: /6 cannot be a per-file fraction.
    assert len(manifest()["cases"]) == 6
    assert aggregates()["files_total"] == 13
    for phrase in ("case-level", "per-file", "ANY fix", "ALL fix"):
        assert phrase not in json.dumps(manifest()["decision_rule"])


def test_the_published_classification_used_case_level_any():
    driver = (AUDIT / "run_trace_audit.py").read_text(encoding="utf-8")
    assert 'record["file_status"] = (' in driver
    assert 'if record["any_fix_file_executed"]' in driver


def test_the_block_is_recorded_rather_than_resolved():
    text = (AUDIT / "AGGREGATE-AUTHORITY-BLOCK.md").read_text(encoding="utf-8")
    assert "BLOCKED_AUDIT_AGGREGATE_AUTHORITY" in text
    assert "not chosen here" in text or "no reinterpretation is applied" in text
    finding = (AUDIT / "EXECUTION-TRACE-PREMISE-AUDIT.md").read_text(encoding="utf-8")
    assert "SUPERSEDED" in finding


def test_the_all_files_correction_is_recorded():
    """3 of 6, not 2 of 6; the earlier prose conflated files with regions."""
    assert aggregates()["case_all_file"] == 3
    assert aggregates()["case_all_region"] == 2
    finding = (AUDIT / "EXECUTION-TRACE-PREMISE-AUDIT.md").read_text(encoding="utf-8")
    assert "Three of six cases had every historical fix file execute" in finding
    assert "Correction." in finding


def test_no_selector_v2_dar_was_drafted_as_premise_proven():
    dars = list((ROOT / "benchmark" / "analysis").rglob("*SELECTOR-V2*"))
    assert dars == [], f"a selector-v2 DAR exists while the aggregate is unresolved: {dars}"
