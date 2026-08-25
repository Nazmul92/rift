"""POST-HOC SOURCE-RECALL / PROPOSAL-FORMAT PROBE — model-free regressions.

The probe compares a unified-diff proposal against an exact search/replace
proposal on the same frozen source. That comparison is only worth anything if
condition S is not quietly the easier language, so most of what follows pins the
executor's atomic contract: every `search` resolved against the original
baseline, matched exactly once, validated in full before a byte is written, and
abandoned entirely if any edit fails.

The rest pins the transaction discipline — durable `REQUEST_STARTED` before the
provider is touched, a global stop on an unreconciled request, result evidence
before terminal state, and exact 12/12 completeness.

Nothing here contacts a provider.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PROBE = ROOT / "benchmark" / "analysis" / "source_recall_probe"
sys.path.insert(0, str(Path(__file__).parent))

from benchmark_modules import load  # noqa: E402

search_replace = load("analysis/source_recall_probe", "search_replace")
transactions = load("analysis/source_recall_probe", "transactions")


def tree_with(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return tmp_path


SAMPLE = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"


# --------------------------------------------------------------- the S executor


def test_a_valid_edit_set_applies_atomically(tmp_path):
    tree = tree_with(tmp_path, {"m.py": SAMPLE})
    result = search_replace.apply_edits(
        tree, {"edits": [{"path": "m.py", "search": "return 1", "replace": "return 11"}]}
    )
    assert result.status == search_replace.APPLIED
    assert result.apply_ok and result.exact_source_quote_valid
    assert (tree / "m.py").read_text(encoding="utf-8") == SAMPLE.replace("return 1", "return 11")


def test_a_new_file_request_is_rejected(tmp_path):
    tree = tree_with(tmp_path, {"m.py": SAMPLE})
    result = search_replace.apply_edits(tree, {"edits": [{"path": "new.py", "search": "x", "replace": "y"}]})
    assert result.status == search_replace.PATH_NOT_FOUND
    assert not (tree / "new.py").exists()


def test_a_missing_path_is_rejected(tmp_path):
    tree = tree_with(tmp_path, {"m.py": SAMPLE})
    result = search_replace.apply_edits(tree, {"edits": [{"path": "pkg/absent.py", "search": "a", "replace": "b"}]})
    assert result.status == search_replace.PATH_NOT_FOUND
    assert not result.exact_source_quote_valid


def test_zero_search_matches_is_rejected(tmp_path):
    tree = tree_with(tmp_path, {"m.py": SAMPLE})
    result = search_replace.apply_edits(
        tree, {"edits": [{"path": "m.py", "search": "return 42", "replace": "return 43"}]}
    )
    assert result.status == search_replace.SEARCH_TEXT_NOT_FOUND
    assert (tree / "m.py").read_text(encoding="utf-8") == SAMPLE


def test_more_than_one_search_match_is_rejected(tmp_path):
    tree = tree_with(tmp_path, {"m.py": "a = 1\nb = 1\n"})
    result = search_replace.apply_edits(tree, {"edits": [{"path": "m.py", "search": "= 1", "replace": "= 2"}]})
    assert result.status == search_replace.SEARCH_TEXT_AMBIGUOUS
    assert (tree / "m.py").read_text(encoding="utf-8") == "a = 1\nb = 1\n"


def test_overlapping_search_regions_are_rejected(tmp_path):
    tree = tree_with(tmp_path, {"m.py": "alpha beta gamma\n"})
    result = search_replace.apply_edits(
        tree,
        {
            "edits": [
                {"path": "m.py", "search": "alpha beta", "replace": "A"},
                {"path": "m.py", "search": "beta gamma", "replace": "B"},
            ]
        },
    )
    assert result.status == search_replace.SEARCH_REGIONS_OVERLAP
    assert (tree / "m.py").read_text(encoding="utf-8") == "alpha beta gamma\n"


def test_every_search_is_resolved_against_the_original_baseline(tmp_path):
    """Edit two never sees edit one's replacement."""
    tree = tree_with(tmp_path, {"m.py": "one\ntwo\n"})
    result = search_replace.apply_edits(
        tree,
        {
            "edits": [
                {"path": "m.py", "search": "one", "replace": "two"},
                {"path": "m.py", "search": "two", "replace": "three"},
            ]
        },
    )
    # "two" occurs exactly once in the ORIGINAL, so both edits validate and both
    # apply against original offsets.
    assert result.status == search_replace.APPLIED
    assert (tree / "m.py").read_text(encoding="utf-8") == "two\nthree\n"


def test_an_earlier_replacement_cannot_create_a_later_search_match(tmp_path):
    """Sequential semantics would let this through; atomic semantics must not."""
    tree = tree_with(tmp_path, {"m.py": "alpha\n"})
    result = search_replace.apply_edits(
        tree,
        {
            "edits": [
                {"path": "m.py", "search": "alpha", "replace": "beta"},
                {"path": "m.py", "search": "beta", "replace": "gamma"},
            ]
        },
    )
    assert result.status == search_replace.SEARCH_TEXT_NOT_FOUND
    assert (tree / "m.py").read_text(encoding="utf-8") == "alpha\n", "no edit may be applied"


def test_one_invalid_edit_causes_zero_mutations(tmp_path):
    tree = tree_with(tmp_path, {"a.py": "keep me\n", "b.py": "also keep\n"})
    result = search_replace.apply_edits(
        tree,
        {
            "edits": [
                {"path": "a.py", "search": "keep me", "replace": "changed"},
                {"path": "b.py", "search": "not present", "replace": "x"},
            ]
        },
    )
    assert not result.apply_ok
    assert (tree / "a.py").read_text(encoding="utf-8") == "keep me\n"
    assert (tree / "b.py").read_text(encoding="utf-8") == "also keep\n"


def test_all_edits_are_validated_before_any_mutation(tmp_path):
    tree = tree_with(tmp_path, {"a.py": "x\n"})
    result = search_replace.apply_edits(
        tree,
        {
            "edits": [
                {"path": "a.py", "search": "x", "replace": "y"},
                {"path": "missing.py", "search": "q", "replace": "r"},
            ]
        },
    )
    assert result.status == search_replace.PATH_NOT_FOUND
    assert (tree / "a.py").read_text(encoding="utf-8") == "x\n"


def test_repeated_application_is_deterministic(tmp_path):
    edits = {"edits": [{"path": "m.py", "search": "return 1", "replace": "return 9"}]}
    outputs = []
    for name in ("t1", "t2"):
        tree = tree_with(tmp_path / name, {"m.py": SAMPLE})
        result = search_replace.apply_edits(tree, edits)
        assert result.apply_ok
        outputs.append((tree / "m.py").read_text(encoding="utf-8"))
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"edits": []},
        {"edits": "nope"},
        {"edits": [{"path": "m.py", "search": "a"}]},
        {"edits": [{"path": "m.py", "search": "", "replace": "b"}]},
        {"edits": [{"path": "/etc/passwd", "search": "a", "replace": "b"}]},
        {"edits": [{"path": "../escape.py", "search": "a", "replace": "b"}]},
        "not an object",
    ],
)
def test_schema_invalid_payloads_are_rejected(tmp_path, payload):
    tree = tree_with(tmp_path, {"m.py": SAMPLE})
    result = search_replace.apply_edits(tree, payload)
    assert result.status == search_replace.SCHEMA_INVALID
    assert not result.exact_source_quote_valid


def test_exact_source_quote_valid_requires_the_whole_set_to_validate(tmp_path):
    tree = tree_with(tmp_path, {"m.py": SAMPLE})
    good = search_replace.apply_edits(tree, {"edits": [{"path": "m.py", "search": "return 1", "replace": "return 1"}]})
    assert good.exact_source_quote_valid is True
    fresh = tree_with(tmp_path / "second", {"m.py": SAMPLE})
    mixed = search_replace.apply_edits(
        fresh,
        {
            "edits": [
                {"path": "m.py", "search": "return 1", "replace": "return 1"},
                {"path": "m.py", "search": "nowhere", "replace": "x"},
            ]
        },
    )
    assert mixed.exact_source_quote_valid is False


# ------------------------------------------------------------- transactions


def test_request_started_is_durable_before_the_provider_is_invoked(tmp_path):
    ledger = transactions.ProbeLedger(tmp_path / "l.jsonl")
    ledger.start_request(
        probe_manifest_hash="m",
        case_id="c",
        condition="U",
        ordinal=1,
        prompt_hash="p",
        requested_model="claude-sonnet-4-6",
        reserved_usd=0.1,
    )
    on_disk = [json.loads(line) for line in (tmp_path / "l.jsonl").read_text(encoding="utf-8").splitlines()]
    assert on_disk[0]["kind"] == transactions.REQUEST_STARTED
    assert on_disk[0]["case_id"] == "c"


def test_the_probe_calls_the_provider_only_after_the_durable_record(tmp_path):
    source = (PROBE / "run_probe.py").read_text(encoding="utf-8")
    body = source.split("def run_condition(")[1].split("\ndef ")[0]
    assert body.index("ledger.start_request(") < body.index("call_provider("), (
        "the provider must not be called before REQUEST_STARTED is durable"
    )


def test_a_failed_start_record_prevents_the_provider_call(tmp_path, monkeypatch):
    ledger = transactions.ProbeLedger(tmp_path / "l.jsonl")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "append", boom)
    with pytest.raises(OSError):
        ledger.start_request(
            probe_manifest_hash="m",
            case_id="c",
            condition="U",
            ordinal=1,
            prompt_hash="p",
            requested_model="m",
            reserved_usd=0.1,
        )


def test_an_unsettled_prior_request_stops_the_probe_globally(tmp_path):
    ledger = transactions.ProbeLedger(tmp_path / "l.jsonl")
    ledger.start_request(
        probe_manifest_hash="m",
        case_id="c",
        condition="U",
        ordinal=1,
        prompt_hash="p",
        requested_model="m",
        reserved_usd=0.1,
    )
    assert len(ledger.unreconciled()) == 1
    with pytest.raises(transactions.UnreconciledRequest):
        ledger.require_reconciled()
    with pytest.raises(transactions.UnreconciledRequest):
        ledger.start_request(
            probe_manifest_hash="m",
            case_id="other",
            condition="S",
            ordinal=1,
            prompt_hash="p2",
            requested_model="m",
            reserved_usd=0.1,
        )


def test_a_settled_request_reconciles(tmp_path):
    ledger = transactions.ProbeLedger(tmp_path / "l.jsonl")
    rid = ledger.start_request(
        probe_manifest_hash="m",
        case_id="c",
        condition="U",
        ordinal=1,
        prompt_hash="p",
        requested_model="m",
        reserved_usd=0.1,
    )
    ledger.response(rid, reported_model="m", raw_hash="h", usage={}, cost_usd=0.01)
    ledger.require_reconciled()


def test_terminal_state_requires_the_result_to_be_persisted_first(tmp_path):
    ledger = transactions.ProbeLedger(tmp_path / "l.jsonl")
    with pytest.raises(RuntimeError, match="before the result was persisted"):
        ledger.condition_terminal("c", "U")
    ledger.result_persisted("c", "U", "rh")
    ledger.condition_terminal("c", "U")


def test_the_runner_persists_the_result_before_marking_terminal():
    source = (PROBE / "run_probe.py").read_text(encoding="utf-8")
    assert source.index("handle.write(json.dumps(record") < source.index("ledger.condition_terminal(")


def test_the_runner_retains_the_complete_raw_response():
    source = (PROBE / "run_probe.py").read_text(encoding="utf-8")
    assert "write_bytes(raw)" in source
    assert "raw_response_hash" in source


def test_a_model_identity_mismatch_blocks_the_result():
    source = (PROBE / "run_probe.py").read_text(encoding="utf-8")
    assert "BLOCKED_MODEL_IDENTITY" in source
    assert "if reported != config.model" in source


# ------------------------------------------------------------- completeness


def result(case: str, condition: str, **over) -> dict:
    base = {
        "case_id": case,
        "condition": condition,
        "probe_manifest_hash": "M",
        "requested_model": "claude-sonnet-4-6",
        "reported_model": "claude-sonnet-4-6",
        "raw_response_hash": "abc",
    }
    base.update(over)
    return base


PAIRS = {(f"case{i}", c) for i in range(6) for c in ("U", "S")}


def twelve() -> list[dict]:
    return [result(case, condition) for case, condition in sorted(PAIRS)]


def test_exactly_twelve_is_accepted():
    assert transactions.completeness_problems(twelve(), "M", PAIRS) == []


def test_eleven_of_twelve_is_rejected():
    problems = transactions.completeness_problems(twelve()[:-1], "M", PAIRS)
    assert any("expected exactly 12" in p for p in problems)
    assert any("missing case-condition pair" in p for p in problems)


def test_thirteen_of_twelve_is_rejected():
    problems = transactions.completeness_problems([*twelve(), result("case0", "U")], "M", PAIRS)
    assert any("expected exactly 12" in p for p in problems)
    assert any("duplicate" in p for p in problems)


def test_a_duplicate_pair_is_rejected():
    records = twelve()[:-1] + [result("case0", "U")]
    assert any("duplicate" in p for p in transactions.completeness_problems(records, "M", PAIRS))


def test_an_unknown_pair_is_rejected():
    records = twelve()[:-1] + [result("intruder", "U")]
    assert any("unknown case-condition pair" in p for p in transactions.completeness_problems(records, "M", PAIRS))


def test_mixed_manifest_identity_is_rejected():
    records = twelve()
    records[3] = {**records[3], "probe_manifest_hash": "OTHER"}
    assert any("mixed probe_manifest_hash" in p for p in transactions.completeness_problems(records, "M", PAIRS))


def test_mixed_model_identity_is_rejected():
    records = twelve()
    records[2] = {**records[2], "reported_model": "something-else"}
    assert any("model identity mismatch" in p for p in transactions.completeness_problems(records, "M", PAIRS))


def test_a_missing_raw_response_is_rejected():
    records = twelve()
    records[5] = {**records[5], "raw_response_hash": ""}
    assert any("missing raw response" in p for p in transactions.completeness_problems(records, "M", PAIRS))


# --------------------------------------------------------- the frozen manifest


def manifest() -> dict:
    return json.loads((PROBE / "probe-manifest.json").read_text(encoding="utf-8"))


def test_the_manifest_freezes_six_cases_in_a_counterbalanced_order():
    frozen = manifest()
    assert len(frozen["cases"]) == 6
    labels = [c["order_label"] for c in frozen["cases"]]
    assert labels.count("U->S") == 3 and labels.count("S->U") == 3
    assert frozen["expected_result_count"] == 12


def test_the_selection_prefers_four_context_mismatches_and_two_path_failures():
    classes = [c["failure_class"] for c in manifest()["cases"]]
    assert classes.count("context_mismatch") == 4
    assert classes.count("file_not_found_or_wrong_path") == 2


def test_every_selected_case_is_distinct():
    ids = [c["case_id"] for c in manifest()["cases"]]
    assert len(set(ids)) == 6


def test_the_manifest_binds_every_required_identity():
    frozen = manifest()
    for field in (
        "probe_manifest_hash",
        "u_prompt_hash",
        "s_prompt_hash",
        "search_replace_executor_hash",
        "transaction_implementation_hash",
        "canonicalizer_identity",
        "selection_rule",
        "counterbalance_rule",
    ):
        assert frozen.get(field), field
    assert frozen["search_replace_executor_hash"] == search_replace.executor_hash()
    assert frozen["transaction_implementation_hash"] == transactions.implementation_hash()


def test_the_budget_covers_the_full_frozen_design():
    budget = manifest()["budget"]
    worst = budget["worst_case_usd"]
    assert budget["total_usd_ceiling"] >= worst, "ceiling cannot reserve the frozen design"
    assert budget["max_requests_per_condition"] == 2


def test_the_probe_is_labelled_as_exploratory_everywhere():
    frozen = manifest()
    assert "NOT BM-08" in frozen["label"] and "NOT CAUSAL" in frozen["label"]
    assert frozen["not_official"] is True


def test_probe_artifacts_stay_out_of_the_official_benchmark_paths():
    for name in ("probe-manifest.json", "probe-results.jsonl", "probe-ledger.jsonl"):
        assert not (ROOT / "benchmark" / "bm08" / name).exists()
    assert PROBE.is_dir()
