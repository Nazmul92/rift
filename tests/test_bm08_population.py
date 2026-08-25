"""BM-08-v3 repository expansion: wider population, identical rules.

v3's only lever is the repository population. Every eligibility, ordering,
deduplication, validation, diversity and threshold rule is carried forward
unchanged, and the tests here exist to make "unchanged" checkable rather than
asserted — a rule that quietly loosened to raise the case count would be
indistinguishable, in the final number, from a genuinely wider population.

The two governance properties that matter most:

**The repository list is frozen before outcomes.** Add a repository, observe zero
valid cases, swap it for a more productive one, and the denominator becomes a
thing you built rather than a thing you measured. The list carries a
deterministic hash and every declared repository stays in the record.

**Rejection accounting conserves.** `post_dedupe == rejected + valid` end to end,
including curation-stage drops. A candidate that disappears between stages is
indistinguishable from one that was never eligible, and the difference is exactly
what a reviewer needs to see.

No model is called and no network is used.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BENCH = Path(__file__).parents[1] / "benchmark"
if str(BENCH / "bm08") not in sys.path:
    sys.path.append(str(BENCH / "bm08"))

import select_corpus  # noqa: E402
from benchmark_modules import load  # noqa: E402

# `validate_cases` exists in both bm07 and bm08; a bare import resolves by
# whichever directory is earlier on sys.path, which collection order decides.
validate_cases = load("bm08", "validate_cases")

BM08 = BENCH / "bm08"
POPULATION = BM08 / "repository-population.json"


def population() -> dict:
    return json.loads(POPULATION.read_text(encoding="utf-8"))


# --------------------------------------------------- history is not rewritten


def test_the_v1_rule_is_marked_historical_and_superseded():
    text = (BM08 / "SELECTION-RULE.md").read_text(encoding="utf-8")
    assert "HISTORICAL BM-08-v1 RULE — SUPERSEDED" in text
    assert "AMENDMENT-V2.md" in text and "AMENDMENT-V3.md" in text
    # The historical body must still be there, not replaced by the notice.
    assert "# BM-08 selection rule — predeclared" in text


def test_the_v2_shortfall_is_preserved():
    """The governed meaning, not a sentence.

    An earlier version asserted an exact phrase from the amendment. That tests
    the prose, not the record: rewording the rationale would fail it while
    changing nothing that matters, and — worse — it invites editing benchmark
    documentation to satisfy a string comparison. What must hold is that both
    superseded verdicts are still recorded and that the amendment still names
    the shortfall against the frozen denominator.
    """
    status = (BM08 / "STATUS.md").read_text(encoding="utf-8")
    assert "BM-08-v1" in status and "CORPUS_INSUFFICIENT" in status
    assert "BM-08-v2" in status and "CORPUS_SHORTFALL" in status

    amendment = (BM08 / "AMENDMENT-V3.md").read_text(encoding="utf-8")
    # v2's outcome and the frozen minimum both appear, in whatever wording.
    assert "8" in amendment and "4 repositories" in amendment
    assert "12" in amendment and "10 repositories" in amendment
    # And the operational thresholds themselves are what actually govern.
    assert (validate_cases.MIN_CASES, validate_cases.MIN_REPOS) == (12, 10)


def test_the_denominator_conditions_are_both_required():
    """Conjunctive: neither count can buy its way past the other."""

    def sufficient(cases: int, repos: int) -> bool:
        return cases >= validate_cases.MIN_CASES and repos >= validate_cases.MIN_REPOS

    assert sufficient(12, 10)
    assert not sufficient(11, 10)
    assert not sufficient(12, 9)
    assert not sufficient(30, 9)


def test_the_v3_amendment_records_that_expansion_predates_any_outcome():
    text = (BM08 / "AMENDMENT-V3.md").read_text(encoding="utf-8")
    assert "cannot be based on A-versus-C performance" in text


# ------------------------------------------ the population is frozen up front


def test_the_repository_population_is_recorded_with_a_deterministic_hash():
    blob = population()
    recorded = blob["repository_population_hash"]
    body = json.dumps({k: v for k, v in blob.items() if k != "repository_population_hash"}, indent=1, sort_keys=True)
    assert recorded == hashlib.sha256((body + "\n").encode("utf-8")).hexdigest()
    assert len(recorded) == 64


def test_every_repository_carries_provenance():
    for row in population()["repositories"]:
        for field in ("repository", "resolved_source", "head_commit", "selection_provenance", "date_added"):
            assert row.get(field), f"{row.get('repository')}: missing {field}"


def test_the_expansion_is_a_population_not_a_threshold_patch():
    """v2 was short by 6 repositories. Adding 6 would optimise to the bar."""
    blob = population()
    assert blob["new_repository_count"] >= 15, "the expansion must be broader than the shortfall"
    assert blob["total_repository_count"] == blob["previous_repository_count"] + blob["new_repository_count"]


def test_no_declared_repository_was_dropped_after_the_fact():
    """Every declared repository stays in the record, productive or not."""
    declared = {name for name, _ in _declared_pairs()}
    recorded = {r["repository"] for r in population()["repositories"]}
    assert declared <= recorded, f"declared repositories missing from the record: {sorted(declared - recorded)}"


def _declared_pairs() -> list[tuple[str, str]]:
    import re

    src = (BM08 / "build_repository_population.py").read_text(encoding="utf-8")
    block = src.split("NEW_REPOSITORIES = [")[1].split("\n]")[0]
    return re.findall(r'\("([^"]+)",\s*"([^"]+)"\)', block)


def test_selection_criteria_are_recorded_and_carry_no_outcome_terms():
    criteria = " ".join(population()["selection_criteria"]).lower()
    for forbidden in ("rift", "succeed", "easy", "desired", "likely"):
        assert forbidden not in criteria, f"a model-performance term reached the criteria: {forbidden}"


# ------------------------------------------------- the frozen rules are frozen


def test_the_author_date_floor_did_not_move():
    assert select_corpus.AUTHOR_DATE_FLOOR == "2018-01-01"


def test_the_minimum_denominator_did_not_move():
    assert (validate_cases.MIN_CASES, validate_cases.MIN_REPOS) == (12, 10)


def test_the_repository_cap_did_not_move():
    assert validate_cases.MAX_PER_REPO == 3


def test_eligibility_still_precedes_deduplication():
    body = (BM08 / "select_corpus.py").read_text(encoding="utf-8").split("def main(")[1]
    assert body.index("eligible_by_author_date") < body.index("collapse_near_duplicates(")


def test_no_pre_validation_repository_cap_reappeared():
    assert "MAX_PER_REPO" not in (BM08 / "select_corpus.py").read_text(encoding="utf-8")


def test_full_history_mining_is_still_in_force():
    """No recency truncation may return under cover of the expansion."""
    source = (BM08 / "mine_corpus.py").read_text(encoding="utf-8")
    assert "kept >= 40" not in source and "kept >= 100" not in source
    assert '"-n", "100000"' in source


def test_the_miner_scans_both_repository_roots():
    source = (BM08 / "mine_corpus.py").read_text(encoding="utf-8")
    assert "REPO_ROOTS" in source and "/repos-v3" in source


# -------------------------------------------- new repositories obey every rule


def test_new_repository_candidates_obey_the_author_date_floor():
    queue = json.loads((BM08 / "queue.json").read_text(encoding="utf-8"))
    assert queue
    assert all(c["author_date"] >= "2018-01-01" for c in queue)


def test_prior_exposure_exclusion_covers_the_whole_population():
    blob = json.loads((BM08 / "exclusions.json").read_text(encoding="utf-8"))
    excluded = set(blob["excluded_commits"])
    official = {e["fix_commit"] for e in blob["named_prior_cases"]["BM-07 official"]}
    assert official <= excluded

    queue = json.loads((BM08 / "queue.json").read_text(encoding="utf-8"))
    survivors = {c["fix_commit"] for c in queue} | {c["parent"] for c in queue}
    assert not (survivors & excluded), "a previously-seen commit reached the queue"


# ------------------------------------------------- rejection accounting


def test_the_rejection_accounting_conserves_end_to_end():
    """post_dedupe == rejected + valid, curation-stage drops included."""
    blob = json.loads((BM08 / "validated.json").read_text(encoding="utf-8"))
    rejected = sum(blob["rejection_breakdown"].values())
    assert blob["post_dedupe_candidates"] == rejected + blob["validated"]
    assert blob["accounting_conserved"] is True


def test_curation_stage_drops_are_counted_as_rejections():
    source = (BM08 / "validate_cases.py").read_text(encoding="utf-8")
    assert "curation_dropped" in source
    assert "rows = curation_dropped + rows" in source


def test_every_rejected_candidate_has_exactly_one_governed_reason():
    blob = json.loads((BM08 / "validated.json").read_text(encoding="utf-8"))
    rejected_rows = [r for r in blob["cases"] if r["curation_status"] != "validated"]
    counted = validate_cases.classify_rejections(blob["cases"])
    assert sum(counted.values()) == len(rejected_rows)
    assert all(r.get("rejection_reason") for r in rejected_rows)


# ------------------------------------------------------ the final corpus shape


def test_the_final_corpus_respects_the_repository_cap():
    blob = json.loads((BM08 / "validated.json").read_text(encoding="utf-8"))
    primary = [r for r in blob["cases"] if r.get("corpus_role") == "primary"]
    counts: dict[str, int] = {}
    for row in primary:
        counts[row["repository"]] = counts.get(row["repository"], 0) + 1
    assert all(n <= validate_cases.MAX_PER_REPO for n in counts.values()), counts
    assert blob["final_primary_cases"] == len(primary)
    assert blob["final_distinct_repositories"] == len(counts)


def test_the_threshold_verdict_matches_the_frozen_rule():
    blob = json.loads((BM08 / "validated.json").read_text(encoding="utf-8"))
    expected = blob["final_primary_cases"] >= 12 and blob["final_distinct_repositories"] >= 10
    assert blob["threshold_passed"] is expected


# ------------------------------- the corpus is bound to its exclusion authority


def test_the_corpus_manifest_binds_the_exclusion_set_identity():
    """ "Unseen" must name *which* authority made it unseen.

    Without this the corpus records that an exclusion set was applied but not
    which one: someone could change `exclusions.json`, re-select a different
    unseen population, and the manifest would carry no evidence that the
    authority had moved. The per-case `prior_exposure_status` string is prose,
    not identity.
    """
    corpus = json.loads((BM08 / "corpus-v3.json").read_text(encoding="utf-8"))
    exclusions = json.loads((BM08 / "exclusions.json").read_text(encoding="utf-8"))

    assert corpus["exclusion_set_hash"] == exclusions["excluded_commit_set_hash"]
    assert corpus["exclusion_set_count"] == exclusions["excluded_commit_count"]
    assert len(corpus["exclusion_set_hash"]) == 64


def test_the_bound_exclusion_hash_is_recomputable_from_the_commit_set():
    """Recomputed from the commits themselves, never trusted from the file."""
    exclusions = json.loads((BM08 / "exclusions.json").read_text(encoding="utf-8"))
    recomputed = hashlib.sha256("\n".join(sorted(exclusions["excluded_commits"])).encode("utf-8")).hexdigest()
    corpus = json.loads((BM08 / "corpus-v3.json").read_text(encoding="utf-8"))
    assert recomputed == corpus["exclusion_set_hash"]


def test_the_corpus_manifest_hash_covers_the_exclusion_binding():
    """Changing the bound authority must change the corpus hash."""
    corpus = json.loads((BM08 / "corpus-v3.json").read_text(encoding="utf-8"))
    body = {k: v for k, v in corpus.items() if k != "corpus_manifest_hash"}
    recomputed = hashlib.sha256((json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()
    assert recomputed == corpus["corpus_manifest_hash"]

    tampered = dict(body)
    tampered["exclusion_set_hash"] = "0" * 64
    moved = hashlib.sha256((json.dumps(tampered, indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()
    assert moved != corpus["corpus_manifest_hash"], "the corpus hash does not cover the exclusion binding"


def test_all_three_identities_are_bound_in_one_artifact():
    """Population, exclusion authority and corpus, in the same signed object."""
    corpus = json.loads((BM08 / "corpus-v3.json").read_text(encoding="utf-8"))
    for field in ("repository_population_hash", "exclusion_set_hash", "corpus_manifest_hash"):
        assert len(corpus[field]) == 64, field
    assert corpus["repository_population_hash"] == population()["repository_population_hash"]


# ------------------------------ BM-08-v4: failure-identity reproducibility


def validated() -> dict:
    return json.loads((BM08 / "validated.json").read_text(encoding="utf-8"))


def test_n_is_frozen_at_three():
    assert validate_cases.STABILITY_OBSERVATIONS == 3
    assert "N = 3" in (BM08 / "AMENDMENT-V4.md").read_text(encoding="utf-8")


def test_each_observation_is_produced_by_a_separate_process():
    """Three observations inside one interpreter would have certified both
    defective cases as reproducible — a memory address is stable within a
    process and varies between processes. The process boundary is the
    measurement, so it is a separate program, not a loop."""
    assert (BM08 / "observe_signature.py").is_file()
    source = (BM08 / "validate_cases.py").read_text(encoding="utf-8")
    assert "OBSERVER" in source and "sys.executable" in source
    body = source.split("def observe_stability(")[1].split("\ndef ")[0]
    assert "run([sys.executable, str(OBSERVER)" in body, "observations must launch a fresh process"


def test_the_observations_run_on_a_fresh_baseline_not_the_fixed_tree():
    """At the end of validation the source fix is applied and the target passes,
    so there is no failure left to observe. Measuring there reported every case
    as having no signature at all."""
    source = (BM08 / "validate_cases.py").read_text(encoding="utf-8")
    body = source.split("def validate(")[1]
    assert "stability_tree" in body
    assert body.index("materialise_baseline(case, repo.parent, stability_tree)") < body.index("observe_stability(")


def test_no_normalisation_of_volatile_values():
    """The amendment adds repeated evidence, not a new judge."""
    for name in ("observe_signature.py", "validate_cases.py"):
        source = (BM08 / name).read_text(encoding="utf-8")
        for forbidden in ("0x%x", "re.sub", "normalize", "normalise", "hexlify", "strip_address"):
            assert forbidden not in source, f"{name} normalises volatile signature content: {forbidden}"


def test_faker_and_pathspec_receive_no_special_case_logic():
    for name in ("validate_cases.py", "observe_signature.py", "curate_queue.py", "select_corpus.py"):
        source = (BM08 / name).read_text(encoding="utf-8")
        assert "faker-5128ae64" not in source, name
        assert "pathspec-b70e3fb4" not in source, name


def test_unstable_failure_identity_is_its_own_rejection_reason():
    labels = {label for _, label in validate_cases.REJECTION_KINDS}
    assert "unstable failure identity" in labels
    # Never merged into a collection, other, or generic identity bucket.
    assert "failure identity unobservable" in labels, "an unobservable signature is not an unstable one"
    assert validated()["rejection_breakdown"].get("unstable failure identity") == 2


def test_every_unstable_case_retains_all_three_identities():
    """A boolean alone cannot be audited; the differing identities are the evidence."""
    unstable = [r for r in validated()["cases"] if "unstable_failure_identity" in (r.get("rejection_reason") or "")]
    assert unstable
    for row in unstable:
        observations = row["failure_identity_observations"]
        assert len(observations) == validate_cases.STABILITY_OBSERVATIONS
        assert all(o["fresh_process"] for o in observations)
        identities = {json.dumps(o["identity"], sort_keys=True) for o in observations}
        assert len(identities) > 1, f"{row['case_id']} was rejected as unstable but its observations agree"
        assert row["failure_identity_stable"] is False


def test_every_valid_case_has_three_identical_observations():
    for row in [r for r in validated()["cases"] if r["curation_status"] == "validated"]:
        observations = row["failure_identity_observations"]
        assert len(observations) == validate_cases.STABILITY_OBSERVATIONS, row["case_id"]
        identities = {json.dumps(o["identity"], sort_keys=True) for o in observations}
        assert len(identities) == 1, row["case_id"]
        assert row["failure_identity_stable"] is True
        assert row["baseline_tree_hash"], row["case_id"]
        assert row["failure_identity"].get("exception_type"), row["case_id"]


def test_already_invalid_candidates_do_not_run_stability_observations():
    """The check applies to otherwise-valid candidates only."""
    wasted = [
        r
        for r in validated()["cases"]
        if r["curation_status"] != "validated"
        and r.get("failure_identity_observations")
        and "unstable_failure_identity" not in (r.get("rejection_reason") or "")
    ]
    assert wasted == [], f"stability observations were spent on already-rejected candidates: {len(wasted)}"


def test_the_v4_accounting_still_conserves():
    blob = validated()
    assert blob["post_dedupe_candidates"] == sum(blob["rejection_breakdown"].values()) + blob["validated"]
    assert blob["accounting_conserved"] is True


# ------------------------------------- BM-08-v5: expanded repository population


def population_v5() -> dict:
    return json.loads((BM08 / "repository-population-v5.json").read_text(encoding="utf-8"))


def corpus_v5() -> dict:
    return json.loads((BM08 / "corpus-v5.json").read_text(encoding="utf-8"))


def test_the_v5_population_hash_is_deterministic():
    blob = population_v5()
    body = {k: v for k, v in blob.items() if not k.startswith("repository_population_hash")}
    recomputed = hashlib.sha256((json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()
    assert recomputed == blob["repository_population_hash_v5"]


def test_the_v5_population_extends_rather_than_replaces():
    """The prior population is carried forward whole, with its hash recorded."""
    blob = population_v5()
    assert blob["prior_population_hash"] == population()["repository_population_hash"]
    assert blob["previous_repository_count"] == 57
    assert blob["total_repository_count"] == blob["previous_repository_count"] + blob["new_repository_count"]
    prior_names = {r["repository"] for r in population()["repositories"]}
    v5_names = {r["repository"] for r in blob["repositories"]}
    assert prior_names <= v5_names, "an existing repository disappeared from the v5 population"


def test_the_v5_expansion_is_broad_not_threshold_tuned():
    """v4 was two repositories short. Adding two would optimise to the bar."""
    assert population_v5()["new_repository_count"] >= 15


def test_a_v5_repository_cannot_be_silently_replaced():
    """Every declared repository stays in the record, productive or not."""
    import re

    src = (BM08 / "build_repository_population_v5.py").read_text(encoding="utf-8")
    block = src.split("NEW_REPOSITORIES = [")[1].split("\n]")[0]
    declared = {name for name, _ in re.findall(r'\("([^"]+)",\s*"([^"]+)"\)', block)}
    recorded = {r["repository"] for r in population_v5()["repositories"]}
    assert declared <= recorded, sorted(declared - recorded)
    # And the builder refuses a batch that re-declares an existing repository.
    assert "re-declares existing repositories" in src


def test_the_v5_corpus_binds_every_frozen_identity():
    corpus = corpus_v5()
    assert corpus["repository_population_hash_v5"] == population_v5()["repository_population_hash_v5"]
    assert (
        corpus["exclusion_set_hash"]
        == json.loads((BM08 / "exclusions.json").read_text(encoding="utf-8"))["excluded_commit_set_hash"]
    )
    assert corpus["author_date_floor"] == "2018-01-01"
    assert corpus["stability_rule"]["observations"] == 3
    assert corpus["stability_rule"]["independent_fresh_processes"] is True
    body = {k: v for k, v in corpus.items() if k != "corpus_manifest_hash"}
    recomputed = hashlib.sha256((json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()
    assert recomputed == corpus["corpus_manifest_hash"]


def test_the_v5_corpus_clears_the_unchanged_threshold():
    corpus = corpus_v5()
    assert corpus["minimum_cases"] == 12 and corpus["minimum_repositories"] == 10
    assert corpus["primary_case_count"] >= 12
    assert corpus["distinct_repositories"] >= 10


def test_every_v5_case_carries_complete_stability_evidence():
    for case in corpus_v5()["cases"]:
        assert case["baseline_tree_hash"], case["case_id"]
        assert case["failure_identity"].get("exception_type"), case["case_id"]
        assert case["failure_identity_stable"] is True, case["case_id"]
        observations = case["failure_identity_observations"]
        assert len(observations) == 3, case["case_id"]
        assert all(o["fresh_process"] for o in observations), case["case_id"]
        identities = {json.dumps(o["identity"], sort_keys=True) for o in observations}
        assert len(identities) == 1, case["case_id"]


def test_the_v5_repository_cap_holds():
    counts: dict[str, int] = {}
    for case in corpus_v5()["cases"]:
        counts[case["repository"]] = counts.get(case["repository"], 0) + 1
    assert all(n <= 3 for n in counts.values()), counts
    assert len(counts) == corpus_v5()["distinct_repositories"]


# ---------------------------- BM-08-v5 executable A+C experiment (no spend)


def executable_v5() -> dict:
    return json.loads((BM08 / "manifest-executable-v5.json").read_text(encoding="utf-8"))


def test_the_executable_manifest_holds_exactly_the_24_v5_cases():
    exe, corpus = executable_v5(), corpus_v5()
    assert len(exe["cases"]) == 24 == corpus["primary_case_count"]
    assert [c["case_id"] for c in exe["cases"]] == [c["case_id"] for c in corpus["cases"]]
    assert len({c["repository"] for c in exe["cases"]}) == 15


def test_the_official_arms_are_exactly_a_and_c():
    import bm08_runner

    assert bm08_runner.OFFICIAL_ARMS == ("A", "C")
    exe = executable_v5()
    assert exe["official_arms"] == ["A", "C"]
    assert set(exe["arms"]) == {"A", "C"}, "arm B is not part of the BM-08 question"


def test_the_expected_official_record_count_is_forty_eight():
    exe = executable_v5()
    assert exe["expected_official_records"] == 48 == len(exe["cases"]) * len(exe["official_arms"])


def test_the_executable_manifest_binds_every_frozen_v5_identity():
    exe, corpus = executable_v5(), corpus_v5()
    assert exe["corpus_manifest_hash"] == corpus["corpus_manifest_hash"]
    assert exe["repository_population_hash_v5"] == corpus["repository_population_hash_v5"]
    assert exe["exclusion_set_hash"] == corpus["exclusion_set_hash"]
    # The v5 name is the identity; no duplicate legacy field was introduced.
    assert "repository_population_hash" not in exe
    for field in ("runtime_hash", "driver_hash", "runner_hash", "oracle_hash", "manifest_hash"):
        assert len(exe[field]) == 64, field


def test_the_stale_v3_executable_manifest_is_gone():
    """It bound 14 cases, an older population and a superseded corpus hash."""
    assert not (BM08 / "manifest-executable.json").exists()


def test_every_executable_case_carries_its_stability_provenance():
    for case in executable_v5()["cases"]:
        assert case["failure_identity_stable"] is True, case["case_id"]
        assert case["stability_observations"] == 3, case["case_id"]
        assert len(case["stability_evidence_hash"]) == 64, case["case_id"]
        assert case["baseline_tree_hash"] and case["failure_identity"].get("exception_type")
        assert case["protected_paths"], case["case_id"]


def test_the_stability_evidence_hash_matches_the_corpus_observations():
    corpus = {c["case_id"]: c for c in corpus_v5()["cases"]}
    for case in executable_v5()["cases"]:
        payload = json.dumps(corpus[case["case_id"]]["failure_identity_observations"], sort_keys=True)
        assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == case["stability_evidence_hash"]


def test_the_model_and_budget_authority_is_frozen_and_single_model():
    exe = executable_v5()
    assert exe["model"]["requested_model_id"] == "claude-sonnet-4-6"
    # One model for both arms; a cheaper model on one side is not a comparison.
    assert "A" not in json.dumps(exe["model"]) and "C" not in exe["model"]
    budget = exe["budget"]
    # 24 cases x 2 arms x the $0.48 derived reservation is a worst case of
    # $23.04. The former $15.00 ceiling could not have reserved the official
    # set and would have stranded the run part-way through with money already
    # spent. Only the total moved; the token and request ceilings that derive
    # the reservation are unchanged.
    assert budget["total_usd_ceiling"] == 25.0
    assert budget["per_case_arm_max_usd"] == 0.25
    assert budget["max_input_tokens"] == 60000 and budget["max_output_tokens"] == 4000
    assert budget["max_attempts"] == 1 and budget["reservation_rule"]
    assert exe["pricing"]["input_per_mtok"] == 3.0 and exe["pricing"]["output_per_mtok"] == 15.0


def test_the_manifest_hash_recomputes():
    import bm08_driver

    exe = executable_v5()
    assert bm08_driver.manifest_hash(exe) == exe["manifest_hash"]


def test_a_manifest_schema_without_arm_b_is_valid():
    """Arm B answers a different question; omitting it is correct, not incomplete."""
    import bm08_driver

    assert bm08_driver.validate_manifest(executable_v5()) == []


def test_the_all_24_preflight_passed_through_the_paid_path():
    log = (BM08 / "preflight-v5-all24.log").read_text(encoding="utf-8")
    assert "ALL 24 CASES PASS" in log
    assert "preflight failures: 0" in log
    for check in (
        "24/24 baseline materialisation PASS",
        "24/24 baseline_tree_hash MATCH",
        "24/24 target baseline FAIL",
        "24/24 fresh failure_identity MATCH",
        "24/24 complete preservation set PASS",
    ):
        assert check in log, check
    assert "repository resolution: 15/15 unique" in log
