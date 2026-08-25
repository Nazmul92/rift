"""REPRESENTATION EXPERIMENT — preparation regressions. No provider is contacted.

The experiment claims that patch metadata is deterministic bookkeeping the model
should not be asked to produce. That claim is only testable if the compiler is
exact, the two conditions are genuinely symmetric, the design is what it says it
is, and settled spend has one name. Each of those is pinned here.

The compiler tests carry the most weight. A compiler that resolved a near-miss,
normalised whitespace, or applied edits sequentially would manufacture exactly
the advantage the experiment is trying to measure, so the forbidden behaviours
are asserted absent rather than assumed absent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
REP = ROOT / "benchmark" / "analysis" / "representation"
AUDIT = ROOT / "benchmark" / "analysis" / "fix_region_audit"
sys.path.insert(0, str(Path(__file__).parent))

from benchmark_modules import load  # noqa: E402

edit_compiler = load("analysis/representation", "edit_compiler")
schemas = load("analysis/representation", "schemas")
design = load("analysis/representation", "design")
analysis_plan = load("analysis/representation", "analysis_plan")
cost = load("analysis/representation", "cost")
tx = load("analysis/representation", "rep_transactions")

HAVE_GIT = bool(__import__("shutil").which("git"))
needs_git = pytest.mark.skipif(not HAVE_GIT, reason="git unavailable")

SAMPLE = "def compute(value):\n    return value + 1\n\n\ndef helper(x):\n    return x * 2\n"


def tree_with(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="")
    return root


def edits(*items: tuple[str, str, str]) -> dict:
    return {"edits": [{"path": p, "search": s, "replace": r} for p, s, r in items]}


# ------------------------------------------------------------------- compiler


@needs_git
def test_an_exact_unique_match_compiles(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": SAMPLE})
    result = edit_compiler.compile_edits(base, edits(("m.py", "value + 1", "value + 2")), tmp_path / "w")
    assert result.ok and result.status == edit_compiler.APPLIED
    assert result.compiled_diff.strip(), "git produced no diff"
    assert "@@" in result.compiled_diff


@needs_git
def test_git_generates_the_diff_rather_than_local_arithmetic():
    """No hunk arithmetic in this module: the header comes from git."""
    source = (REP / "edit_compiler.py").read_text(encoding="utf-8")
    assert '"diff",' in source and '"--no-index",' in source
    # A hand-rolled engine would have to compute these; none appear.
    for forbidden in ("@@ -", "hunk_count", "old_start", "recount"):
        assert forbidden not in source.replace('"@@" in', ""), forbidden


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (edits(("missing.py", "x", "y")), edit_compiler.PATH_NOT_FOUND),
        (edits(("m.py", "value + 99", "z")), edit_compiler.SEARCH_NOT_FOUND),
        (edits(("m.py", "return ", "return  ")), edit_compiler.SEARCH_AMBIGUOUS),
    ],
)
def test_invalid_edits_are_rejected_by_class(tmp_path, payload, expected):
    base = tree_with(tmp_path / "b", {"m.py": SAMPLE})
    result = edit_compiler.compile_edits(base, payload, tmp_path / "w")
    assert result.status == expected
    assert not result.ok


def test_overlapping_matches_are_rejected(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": SAMPLE})
    payload = edits(
        ("m.py", "def compute(value):\n    return value + 1", "A"),
        ("m.py", "return value + 1", "B"),
    )
    result = edit_compiler.compile_edits(base, payload, tmp_path / "w")
    assert result.status == edit_compiler.SEARCH_OVERLAP


def test_one_invalid_edit_causes_zero_mutation(tmp_path):
    base = tree_with(tmp_path / "b", {"a.py": "keep\n", "b.py": "also\n"})
    result = edit_compiler.compile_edits(
        base, edits(("a.py", "keep", "changed"), ("b.py", "absent", "x")), tmp_path / "w"
    )
    assert not result.ok
    assert (base / "a.py").read_text(encoding="utf-8") == "keep\n"
    assert (base / "b.py").read_text(encoding="utf-8") == "also\n"


@needs_git
def test_every_search_resolves_against_the_original_baseline(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": "one\ntwo\n"})
    result = edit_compiler.compile_edits(base, edits(("m.py", "one", "two"), ("m.py", "two", "three")), tmp_path / "w")
    assert result.ok, "both searches exist exactly once in the ORIGINAL"


def test_a_replacement_cannot_create_a_later_search_match(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": "alpha\n"})
    result = edit_compiler.compile_edits(
        base, edits(("m.py", "alpha", "beta"), ("m.py", "beta", "gamma")), tmp_path / "w"
    )
    assert result.status == edit_compiler.SEARCH_NOT_FOUND
    assert (base / "m.py").read_text(encoding="utf-8") == "alpha\n"


@needs_git
def test_utf8_is_matched_exactly(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": "s = 'café'\n"})
    ok = edit_compiler.compile_edits(base, edits(("m.py", "'café'", "'tea'")), tmp_path / "w")
    assert ok.ok
    fresh = tree_with(tmp_path / "b2", {"m.py": "s = 'café'\n"})
    # A different Unicode spelling of the same glyph must NOT match.
    decomposed = "café"
    miss = edit_compiler.compile_edits(fresh, edits(("m.py", f"'{decomposed}'", "x")), tmp_path / "w2")
    assert miss.status == edit_compiler.SEARCH_NOT_FOUND, "no Unicode normalization is permitted"


def test_lf_and_crlf_are_distinct(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": "a = 1\r\nb = 2\r\n"})
    result = edit_compiler.compile_edits(base, edits(("m.py", "a = 1\nb = 2", "x")), tmp_path / "w")
    assert result.status == edit_compiler.SEARCH_NOT_FOUND, "LF must not match CRLF"


@needs_git
def test_crlf_matches_itself(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": "a = 1\r\nb = 2\r\n"})
    result = edit_compiler.compile_edits(base, edits(("m.py", "a = 1\r\nb = 2", "a = 9\r\nb = 2")), tmp_path / "w")
    assert result.ok


def test_a_final_newline_is_part_of_the_bytes(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": "value = 1\n"})
    result = edit_compiler.compile_edits(base, edits(("m.py", "value = 1\n", "value = 2\n")), tmp_path / "w")
    assert result.ok
    assert (tmp_path / "w" / "after" / "m.py").read_bytes() == b"value = 2\n"


def test_a_binary_file_is_unsupported(tmp_path):
    base = tmp_path / "b"
    base.mkdir()
    (base / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    result = edit_compiler.compile_edits(base, edits(("blob.bin", "binary", "x")), tmp_path / "w")
    assert result.status == edit_compiler.UNSUPPORTED_OPERATION


@needs_git
def test_only_declared_paths_differ_between_before_and_after(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": SAMPLE, "other.py": "CONST = 3\n"})
    work = tmp_path / "w"
    result = edit_compiler.compile_edits(base, edits(("m.py", "value + 1", "value + 2")), work)
    assert result.ok
    scoped, detail = edit_compiler.bytes_changed_only_where_declared(work, result)
    assert scoped, detail


@needs_git
def test_the_compiled_diff_reproduces_the_declared_after_tree(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": SAMPLE})
    work = tmp_path / "w"
    result = edit_compiler.compile_edits(base, edits(("m.py", "value + 1", "value + 2")), work)
    ok, detail = edit_compiler.verify_round_trip(base, result, work)
    assert ok, detail


@needs_git
def test_compilation_is_deterministic_across_runs(tmp_path):
    hashes = set()
    for name in ("a", "b"):
        base = tree_with(tmp_path / name, {"m.py": SAMPLE})
        result = edit_compiler.compile_edits(base, edits(("m.py", "value + 1", "value + 2")), tmp_path / f"w{name}")
        assert result.ok
        hashes.add(result.compiled_diff_hash)
    assert len(hashes) == 1


def test_the_authority_contract_forbids_every_guessing_behaviour():
    forbidden = set(edit_compiler.AUTHORITY_CONTRACT["forbidden"])
    for item in ("fuzzy matching", "whitespace normalization", "regex interpretation", "nearest-match choice"):
        assert item in forbidden
    assert edit_compiler.AUTHORITY_CONTRACT["new_files"].startswith("not supported")


def test_a_receipt_is_persisted_for_every_edit(tmp_path):
    base = tree_with(tmp_path / "b", {"m.py": SAMPLE})
    result = edit_compiler.compile_edits(base, edits(("m.py", "value + 1", "value + 2")), tmp_path / "w")
    payload = result.to_dict()
    assert payload["compiler_hash"] and payload["authority_contract_hash"]
    receipt = payload["edits"][0]
    for field in ("search_hash", "replace_hash", "match_count", "match_start_byte", "matched_bytes_hash"):
        assert receipt[field] not in ("", -1), field


# ------------------------------------------------------------ schema symmetry


@pytest.mark.parametrize("condition", ["U", "S"])
def test_both_conditions_have_a_strict_validated_schema(condition):
    valid = (
        {"diff": "--- a/x\n+++ b/x\n"}
        if condition == "U"
        else {"edits": [{"path": "x.py", "search": "a", "replace": "b"}]}
    )
    ok, _ = schemas.validate(condition, valid)
    assert ok
    bad, detail = schemas.validate(condition, {"unexpected": 1})
    assert not bad and detail


@pytest.mark.parametrize("condition", ["U", "S"])
def test_both_conditions_get_exactly_one_schema_repair(condition):
    assert schemas.MAX_REQUESTS_PER_SAMPLE == 2
    instruction = schemas.schema_repair_instruction(condition, "reason")
    assert "JSON object" in instruction
    assert "no prose" in instruction


def test_the_repair_instruction_never_re_asks_the_question():
    for condition in ("U", "S"):
        instruction = schemas.schema_repair_instruction(condition, "reason")
        assert "fix" not in instruction.lower() and "try again" not in instruction.lower()


def test_the_runner_counts_and_retains_every_request():
    source = (REP / "rep_runner.py").read_text(encoding="utf-8")
    assert "raw_hashes.append(raw_hash)" in source
    assert "requests_made += 1" in source
    assert "raw_response_hashes" in source


def test_the_runner_grants_repair_symmetrically():
    """One loop serves both conditions; neither can have a private allowance."""
    source = (REP / "rep_runner.py").read_text(encoding="utf-8")
    body = source.split("def run_sample(")[1].split(chr(10) + "def ")[0]
    # A single request loop, and the repair instruction issued once inside it,
    # parameterised by condition rather than branched on it — a per-condition
    # branch is how an asymmetry would enter without anyone deciding to add one.
    assert body.count("while requests_made <") == 1
    assert body.count("schema_repair_instruction(") == 1
    loop = body[body.index("while requests_made <") : body.index("record: dict")]
    assert loop.count(chr(39) + chr(39) + chr(39)) == 0
    assert 'condition == "S"' not in loop
    assert 'condition == "U"' not in loop


# ------------------------------------------------------------------- design


def test_the_schedule_is_144_samples_over_24_cases():
    cases = [f"case-{i:02d}" for i in range(24)]
    samples = design.build(cases)
    assert len(samples) == design.EXPECTED_SAMPLES == 144
    assert design.schedule_problems(samples, cases) == []


def test_order_is_counterbalanced():
    cases = [f"case-{i:02d}" for i in range(24)]
    balance = design.order_balance(design.build(cases))
    assert balance["U_first"] == balance["S_first"] == 36
    assert balance["pairs"] == 72


def test_each_pair_holds_one_u_and_one_s():
    cases = [f"case-{i:02d}" for i in range(24)]
    samples = design.build(cases)
    by_pair: dict[str, list[str]] = {}
    for sample in samples:
        by_pair.setdefault(sample["pair_id"], []).append(sample["condition"])
    assert all(sorted(v) == ["S", "U"] for v in by_pair.values())


def test_a_broken_schedule_is_detected():
    cases = [f"case-{i:02d}" for i in range(24)]
    samples = design.build(cases)
    assert design.schedule_problems(samples[:-1], cases), "a missing sample must be caught"


# ---------------------------------------------------------------- analysis


def test_the_unit_of_generalization_is_cases_not_samples():
    plan = analysis_plan.as_dict()
    assert "n = 24" in plan["unit_of_generalization"]
    assert plan["powered"] is False
    assert "144" not in plan["primary_estimand"]


def test_pairing_is_case_by_repeat():
    assert "case x repeat" in analysis_plan.PAIRING


def test_failed_generations_score_zero_and_stay_in_the_denominator():
    rule = analysis_plan.TRUTH_OUTCOME_RULE
    for key in ("no_candidate", "non_applicable_candidate", "schema_invalid_after_governed_repair", "oracle_wrong"):
        assert rule[key] == 0
    assert "stay in the primary denominator" in rule["note"]


def test_infrastructure_failure_is_not_silently_a_scientific_zero():
    assert "INFRASTRUCTURE_FAILURE" in analysis_plan.MISSINGNESS_RULE
    assert "never merged" in analysis_plan.MISSINGNESS_RULE


def test_the_interval_resamples_cases_not_samples():
    assert "cases with replacement" in analysis_plan.CI_METHOD["resample"]
    # Six samples from two cases must give a 2-case interval, not a 6-sample one.
    samples = [
        {"pair_id": "p1", "case_id": "a", "condition": "U", "truth_correct": False},
        {"pair_id": "p1", "case_id": "a", "condition": "S", "truth_correct": True},
        {"pair_id": "p2", "case_id": "b", "condition": "U", "truth_correct": False},
        {"pair_id": "p2", "case_id": "b", "condition": "S", "truth_correct": False},
    ]
    differences = analysis_plan.per_case_differences(samples)
    assert set(differences) == {"a", "b"}
    assert analysis_plan.bootstrap_interval(differences, iterations=200)["cases"] == 2


def test_the_analysis_is_deterministic():
    differences = {"a": 1.0, "b": 0.0, "c": -1.0}
    first = analysis_plan.bootstrap_interval(differences, iterations=500)
    second = analysis_plan.bootstrap_interval(differences, iterations=500)
    assert first == second


def test_the_detectable_effect_is_reported_before_spending():
    detectable = analysis_plan.detectable_effect()
    assert "approximate_detectable_difference" in detectable
    assert detectable["minimum_effect_of_interest"] == 0.15
    # The design is honest about being unable to separate the effect of interest.
    assert detectable["design_can_distinguish_minimum_effect"] is False
    assert "not a powered study" in detectable["verdict"]


def test_coverage_is_a_stratification_variable_not_a_stop_rule():
    strat = analysis_plan.STRATIFICATION
    assert strat["role"].startswith("preregistered stratification")
    assert "not a solvability claim" in strat["not"]
    assert "not an exclusion criterion" in strat["not"]


def test_the_scoped_claim_is_bounded_to_the_observed_corpus():
    assert "previously observed BM-08 mechanism corpus" in analysis_plan.SCOPED_CLAIM
    assert "fresh unseen corpus" in analysis_plan.FORBIDDEN_CLAIM


# --------------------------------------------------------------------- cost


def test_actual_usd_is_the_only_authoritative_field():
    assert cost.AUTHORITATIVE_FIELD == "actual_usd"
    assert cost.settled_spend({"actual_usd": 0.25}) == 0.25


def test_an_estimate_cannot_stand_in_for_settled_spend():
    with pytest.raises(cost.CostAuthorityError):
        cost.settled_spend({"estimated_usd": 0.25})
    with pytest.raises(cost.CostAuthorityError):
        cost.settled_spend({"reserved_usd": 0.25})


def test_an_estimate_that_disagrees_with_actual_is_flagged():
    problems = cost.cost_field_problems([{"actual_usd": 0.1, "estimated_usd": 0.9, "case_id": "c"}])
    assert problems and "must never override" in problems[0]


def test_the_worst_case_study_reservation_is_derived():
    derived = cost.worst_case_study(
        cases=24,
        repeats=3,
        conditions=2,
        max_requests_per_sample=2,
        max_input_tokens=30000,
        max_output_tokens=4000,
        pricing={"input_per_mtok": 3.0, "output_per_mtok": 15.0},
    )
    assert derived["per_request_usd"] == pytest.approx(0.15)
    assert derived["samples"] == 144
    assert derived["total_worst_case_usd"] == pytest.approx(43.2)
    assert derived["authorized"] is False and derived["spent"] == 0.0


# --------------------------------------------------------------- transactions


def test_request_started_is_durable_before_the_provider(tmp_path):
    ledger = tx.StudyLedger(tmp_path / "l.jsonl")
    ledger.start_request(
        manifest_hash="M",
        sample_id="s1",
        case_id="c",
        repeat=1,
        condition="U",
        ordinal=1,
        prompt_hash="p",
        requested_model="m",
        reserved_usd=0.1,
    )
    on_disk = json.loads((tmp_path / "l.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert on_disk["kind"] == tx.REQUEST_STARTED


def test_the_runner_starts_the_request_before_calling_the_provider():
    body = (REP / "rep_runner.py").read_text(encoding="utf-8").split("def run_sample(")[1]
    assert body.index("ledger.start_request(") < body.index("provider(condition")


def test_an_unresolved_request_stops_the_study_globally(tmp_path):
    ledger = tx.StudyLedger(tmp_path / "l.jsonl")
    ledger.start_request(
        manifest_hash="M",
        sample_id="s1",
        case_id="c",
        repeat=1,
        condition="U",
        ordinal=1,
        prompt_hash="p",
        requested_model="m",
        reserved_usd=0.1,
    )
    with pytest.raises(tx.UnreconciledRequest):
        ledger.start_request(
            manifest_hash="M",
            sample_id="s2",
            case_id="c2",
            repeat=1,
            condition="S",
            ordinal=1,
            prompt_hash="p2",
            requested_model="m",
            reserved_usd=0.1,
        )


def test_terminal_state_requires_persisted_result(tmp_path):
    ledger = tx.StudyLedger(tmp_path / "l.jsonl")
    with pytest.raises(tx.ResultDurabilityError):
        ledger.sample_terminal("s1")
    ledger.result_persisted("s1", "h")
    ledger.sample_terminal("s1")


def test_the_runner_persists_before_terminal():
    source = (REP / "rep_runner.py").read_text(encoding="utf-8")
    assert source.index("handle.write(line)") < source.index("ledger.sample_terminal(")


def test_a_model_mismatch_blocks_the_result():
    source = (REP / "rep_runner.py").read_text(encoding="utf-8")
    assert "ModelIdentityError" in source
    assert 'if reported != manifest["model"]["requested_model_id"]' in source


def sample_key(i: int) -> tuple[str, int, str]:
    return (f"case-{i // 6:02d}", (i // 2) % 3 + 1, "U" if i % 2 == 0 else "S")


def make_results(n: int = 144) -> list[dict]:
    out = []
    for i in range(n):
        case_id, repeat, condition = sample_key(i)
        record = {
            "representation_experiment_manifest_hash": "M",
            "case_id": case_id,
            "repeat": repeat,
            "condition": condition,
            "pair_id": f"{case_id}-{repeat}",
            "request_position": 1,
            "baseline_tree_hash": "b",
            "context_hash": "c",
            "historical_fix_region_coverage": "COVERED",
            "prompt_hash": "p",
            "compiler_authority_contract_hash": "a",
            "canonicalizer_identity": {},
            "execution_environment_hash": "e",
            "requested_model": "m",
            "reported_model": "m",
            "raw_response_hash": "r",
            "actual_usd": 0.01,
            "input_tokens": 1,
            "output_tokens": 1,
            "request_count": 1,
        }
        if condition == "S":
            record["compiler_hash"] = "ch"
        out.append(record)
    return out


EXPECTED = {sample_key(i) for i in range(144)}


def test_exactly_144_is_accepted():
    assert tx.completeness_problems(make_results(), "M", EXPECTED) == []


@pytest.mark.parametrize("n", [143, 145])
def test_off_by_one_counts_are_rejected(n):
    records = make_results(144)
    records = records[:143] if n == 143 else [*records, records[0]]
    assert tx.completeness_problems(records, "M", EXPECTED)


def test_duplicates_unknown_and_mixed_identity_are_rejected():
    records = make_results()
    records[5] = {**records[5], **dict(zip(("case_id", "repeat", "condition"), records[4].values(), strict=False))}
    assert tx.completeness_problems(records, "M", EXPECTED)

    mixed = make_results()
    mixed[7] = {**mixed[7], "representation_experiment_manifest_hash": "OTHER"}
    assert any("mixed" in p for p in tx.completeness_problems(mixed, "M", EXPECTED))

    model = make_results()
    model[9] = {**model[9], "reported_model": "elsewhere"}
    assert any("model identity mismatch" in p for p in tx.completeness_problems(model, "M", EXPECTED))

    raw = make_results()
    raw[11] = {**raw[11], "raw_response_hash": ""}
    assert any("missing raw response" in p for p in tx.completeness_problems(raw, "M", EXPECTED))


def test_an_infrastructure_failure_cannot_be_aggregated():
    records = make_results()
    records[3] = {**records[3], "outcome_class": tx.INFRASTRUCTURE_FAILURE}
    assert any("infrastructure failure" in p for p in tx.completeness_problems(records, "M", EXPECTED))


# ------------------------------------------------------- the frozen manifest


def manifest() -> dict:
    return json.loads((REP / "representation-manifest.json").read_text(encoding="utf-8"))


def test_the_manifest_is_frozen_and_not_authorized():
    frozen = manifest()
    assert frozen["expected_samples"] == 144
    assert len(frozen["cases"]) == 24
    assert len(frozen["samples"]) == 144
    assert frozen["powered"] is False
    assert frozen["budget"]["authorization_status"].startswith("NOT AUTHORIZED")
    assert frozen["budget"]["authorized"] is False and frozen["budget"]["spent"] == 0.0


def test_the_manifest_binds_every_required_identity():
    frozen = manifest()
    for field in (
        "u_schema_hash",
        "s_schema_hash",
        "u_prompt_hash",
        "s_prompt_hash",
        "compiler_hash",
        "compiler_authority_contract_hash",
        "canonicalizer_identity",
        "driver_hash",
        "runner_hash",
        "oracle_hash",
        "execution_environment_hash",
        "transaction_implementation_hash",
        "analysis_plan_hash",
        "coverage_audit_hash",
        "representation_experiment_manifest_hash",
    ):
        assert frozen.get(field), field
    assert frozen["compiler_hash"] == edit_compiler.compiler_hash()
    assert frozen["compiler_authority_contract_hash"] == edit_compiler.authority_contract_hash()
    assert frozen["u_schema_hash"] == schemas.schema_hash(schemas.U_SCHEMA)
    assert frozen["analysis_plan_hash"] == analysis_plan.plan_hash()


def test_every_case_carries_its_coverage_stratum():
    for case in manifest()["cases"]:
        assert case["historical_fix_region_coverage"] in {"COVERED", "PARTIALLY_COVERED", "NOT_COVERED"}


def test_the_dry_run_passed_144_of_144():
    report = json.loads((REP / "dry-run-report.json").read_text(encoding="utf-8"))
    assert report["samples"] == 144
    assert report["completeness_problems"] == []
    assert report["cost_field_problems"] == []
    assert report["unreconciled_requests"] == 0
    assert report["provider_calls"] == 0 and report["additional_spend_usd"] == 0.0


def test_the_dry_run_exercised_every_governed_fault_class():
    report = json.loads((REP / "dry-run-report.json").read_text(encoding="utf-8"))
    plans = report["fault_plans"]
    for required in (
        "U:valid",
        "U:repair_then_valid",
        "U:invalid_twice",
        "S:valid",
        "S:repair_then_valid",
        "S:invalid_twice",
        "S:search_not_found",
        "S:search_ambiguous",
        "S:search_overlap",
        "S:path_not_found",
    ):
        assert plans.get(required), f"fault class never exercised: {required}"
    for compiler_class in ("SEARCH_NOT_FOUND", "SEARCH_AMBIGUOUS", "SEARCH_OVERLAP", "PATH_NOT_FOUND"):
        assert report["compile_status_s"].get(compiler_class), compiler_class


def test_no_provider_is_reachable_from_the_preparation_modules():
    for name in (
        "edit_compiler.py",
        "schemas.py",
        "design.py",
        "analysis_plan.py",
        "cost.py",
        "rep_transactions.py",
        "dry_run.py",
    ):
        source = (REP / name).read_text(encoding="utf-8")
        for forbidden in ("import socket", "urllib.request", "urlopen(", "RIFT_LLM", "requests.post"):
            assert forbidden not in source, f"{name} references {forbidden}"


def test_the_runner_never_opens_a_socket_itself():
    """The provider is injected, so the dry run exercises the paid path."""
    source = (REP / "rep_runner.py").read_text(encoding="utf-8")
    for forbidden in ("import socket", "urllib.request", "urlopen(", "RIFT_LLM", "requests.post"):
        assert forbidden not in source, forbidden
    assert "provider: ProviderCall" in source


# ----------------------------------------------------- coverage audit + leakage


def coverage() -> dict:
    return json.loads((AUDIT / "fix-region-coverage.json").read_text(encoding="utf-8"))


def test_the_audit_covers_all_24_cases():
    rows = coverage()["cases"]
    assert len(rows) == 24
    assert len({r["case_id"] for r in rows}) == 24


def test_the_audit_is_evaluator_only_and_disclaims_solvability():
    data = coverage()
    assert data["evaluator_only"] is True
    assert "not necessarily the only valid" in data["not_a_solvability_claim"]
    assert data["provider_calls"] == 0 and data["additional_spend_usd"] == 0.0


def test_the_historical_fix_never_enters_a_model_facing_artifact():
    """Leakage check: the fix commit is read by the audit and by nothing else."""
    frozen = manifest()
    blob = json.dumps(frozen)
    for marker in ("fix_commit", "fix_touched_regions", "fix_touched_files", "historical_fix"):
        if marker == "historical_fix":
            continue
        assert marker not in blob, f"{marker} leaked into the representation manifest"
    # Only the coverage *label* travels, never the fix content.
    for case in frozen["cases"]:
        assert set(case) & {"fix_commit", "fix_touched_files", "fix_touched_regions"} == set()

    for name in ("freeze_representation.py", "rep_runner.py"):
        source = (REP / name).read_text(encoding="utf-8")
        assert "fix_commit" not in source, f"{name} reads the historical fix"
        assert "fix_touched" not in source, f"{name} reads the historical fix"


def test_the_prompts_contain_no_fix_content():
    frozen = manifest()
    for template in (frozen["u_prompt_template"], frozen["s_prompt_template"]):
        assert "{context}" in template
        for forbidden in ("fix_commit", "upstream", "historical", "the correct fix"):
            assert forbidden not in template.lower()


def test_coverage_counts_are_reported_both_ways():
    rows = coverage()["cases"]
    primary = [r["coverage_status"] for r in rows]
    code_only = [r["code_only_coverage_status"] for r in rows]
    assert primary.count("NOT_COVERED") == code_only.count("NOT_COVERED") == 14
    assert "secondary_view_disclosure" in coverage()


# ------------------------------------------------------------- environment


def test_the_environment_boundary_is_preserved():
    frozen = manifest()
    policy = frozen["execution_environment"]["network_policy"]
    assert policy["repository_controlled_execution"] == "denied"
    assert policy["provider_and_controller"] == "allowed"
    assert frozen["execution_environment"]["rift_repository_isolation"]["level"] == "full"


@pytest.mark.skipif(not __import__("shutil").which("unshare"), reason="unshare unavailable")
def test_a_repository_child_is_still_network_denied(tmp_path):
    sys.path.insert(0, str(ROOT / "benchmark" / "bm08"))
    import confinement

    ok, detail = confinement.prove_isolation(sys.executable, tmp_path)
    assert ok, detail


def test_the_official_bm08_result_is_untouched():
    records = [
        json.loads(line)
        for line in (ROOT / "benchmark" / "bm08" / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
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


def test_git_is_the_diff_producer_in_the_frozen_contract():
    assert "deterministic Git diff production" in edit_compiler.AUTHORITY_CONTRACT["permitted"]
    proc = subprocess.run(["git", "--version"], capture_output=True, text=True)
    assert proc.returncode == 0 or not HAVE_GIT
