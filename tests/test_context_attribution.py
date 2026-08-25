"""BM-08 context-miss attribution — regressions. Zero cost, no provider.

The attribution joins three frozen sources: the coverage audit, the official
BM-08 outcomes, and the selector's own retained decision trace. Its value rests
entirely on copying those faithfully, so what follows checks the joins rather
than the conclusions — a miscopied outcome would produce a confident
cross-tabulation of the wrong benchmark.

The leakage tests matter for the same reason they did in the audit: the
historical fix is read here to attribute misses, and it must reach no
model-facing artifact.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
ATTR = ROOT / "benchmark" / "analysis" / "context_attribution"
AUDIT = ROOT / "benchmark" / "analysis" / "fix_region_audit" / "fix-region-coverage.json"
BM08 = ROOT / "benchmark" / "bm08"
PROBE = ROOT / "benchmark" / "analysis" / "source_recall_probe"
REP = ROOT / "benchmark" / "analysis" / "representation"
sys.path.insert(0, str(Path(__file__).parent))

from benchmark_modules import load  # noqa: E402

attribute = load("analysis/context_attribution", "attribute_misses")

COVERAGE = ("COVERED", "PARTIALLY_COVERED", "NOT_COVERED")


def rows() -> list[dict]:
    path = ATTR / "context-miss-attribution.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audit() -> dict:
    return json.loads(AUDIT.read_text(encoding="utf-8"))


def official() -> dict:
    out = {}
    for line in (BM08 / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            out[(record["case_id"], record["arm"])] = record
    return out


# ------------------------------------------------------------------ integrity


def test_all_24_official_cases_appear_exactly_once():
    ids = [r["case_id"] for r in rows()]
    assert len(ids) == 24
    assert len(set(ids)) == 24
    manifest = json.loads((BM08 / "manifest-executable-v5.json").read_text(encoding="utf-8"))
    assert set(ids) == {c["case_id"] for c in manifest["cases"]}


def test_the_frozen_audit_identity_verifies():
    blob = audit()
    assert attribute.audit_hash(blob) == blob["audit_manifest_hash"]


def test_coverage_status_matches_the_frozen_audit():
    frozen = {r["case_id"]: r["coverage_status"] for r in audit()["cases"]}
    for row in rows():
        assert row["coverage"] == frozen[row["case_id"]], row["case_id"]


def test_coverage_counts_are_the_frozen_ones():
    counts = {status: sum(1 for r in rows() if r["coverage"] == status) for status in COVERAGE}
    assert counts == {"COVERED": 2, "PARTIALLY_COVERED": 8, "NOT_COVERED": 14}


def test_official_outcomes_are_copied_exactly():
    records = official()
    for row in rows():
        for arm in ("A", "C"):
            source = records[(row["case_id"], arm)]
            truth = (source.get("ground_truth") or {}).get("ground_truth_verdict") == "correct"
            assert row[f"arm_{arm}"]["truth_correct"] is truth, f"{row['case_id']}/{arm}"
            assert row[f"arm_{arm}"]["arm_verdict"] == source.get("arm_verdict", "")


def test_the_official_totals_are_unchanged():
    records = official()
    assert len(records) == 48
    correct = {
        arm: sum(
            1
            for (_, a), r in records.items()
            if a == arm and (r.get("ground_truth") or {}).get("ground_truth_verdict") == "correct"
        )
        for arm in ("A", "C")
    }
    assert correct == {"A": 5, "C": 3}
    assert round(sum(r["actual_usd"] for r in records.values()), 4) == 1.8366
    # And the attribution reproduces them from its own copies.
    assert sum(1 for r in rows() if r["arm_A"]["truth_correct"]) == 5
    assert sum(1 for r in rows() if r["arm_C"]["truth_correct"]) == 3


def test_canonical_applicability_is_copied_from_the_replay():
    replay = {
        (r["case_id"], r["arm"]): r
        for r in json.loads((BM08 / "canonicalization-replay.json").read_text(encoding="utf-8"))["records"]
    }
    for row in rows():
        for arm in ("A", "C"):
            source = replay.get((row["case_id"], arm))
            if source is None:
                continue
            assert row[f"arm_{arm}"]["canonical_apply_ok"] == source["canonical_apply_ok"]


# ------------------------------------------------------- selector attribution


def test_every_miss_is_attributed_from_the_frozen_trace():
    for row in rows():
        assert row["selector_miss_class"], row["case_id"]
        assert row["selector_trace_hash"], row["case_id"]
        # The trace itself travelled with the record, so the class is checkable.
        assert row["selector_stages"], row["case_id"]
        assert row["selector_caps"]["cap_files"], row["case_id"]


def test_no_miss_is_unresolved():
    unresolved = [
        r["case_id"] for r in rows() if r["selector_miss_class"] == attribute.UNRESOLVED_FROM_RETAINED_EVIDENCE
    ]
    assert unresolved == [], f"unattributed misses: {unresolved}"


def test_the_trace_hash_matches_the_retained_selector_event():
    import probe_context

    for row in rows()[:4]:  # spot check; hashing all 24 re-reads every ledger
        arm = next(a["context_arm"] for a in audit()["cases"] if a["case_id"] == row["case_id"])
        trace = probe_context.recorded_context(BM08 / "results-evidence" / row["case_id"] / arm)
        expected = hashlib.sha256((json.dumps(trace, indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()
        assert row["selector_trace_hash"] == expected, row["case_id"]


def test_discovery_and_budget_failures_are_disjoint():
    for row in rows():
        flags = [row["never_discovered"], row["excluded_by_budget_or_cap"], row["selected_file_wrong_region"]]
        assert sum(bool(f) for f in flags) <= 1, row["case_id"]


def test_the_discovery_versus_budget_split_partitions_the_misses():
    data = rows()
    never = sum(1 for r in data if r["never_discovered"])
    excluded = sum(1 for r in data if r["excluded_by_budget_or_cap"])
    wrong = sum(1 for r in data if r["selected_file_wrong_region"])
    covered = sum(1 for r in data if r["selector_miss_class"] == attribute.COVERED_NO_MISS)
    assert never + excluded + wrong + covered == len(data) == 24
    assert (never, excluded, wrong, covered) == (6, 1, 7, 10)


def test_every_not_covered_case_has_a_concrete_miss_class():
    for row in rows():
        if row["coverage"] == "NOT_COVERED":
            assert row["selector_miss_class"] != attribute.COVERED_NO_MISS, row["case_id"]
            assert row["selector_miss_detail"].strip()


# ---------------------------------------------------------- the counterexample


def test_the_truth_correct_not_covered_case_is_present():
    """The evidence that NOT_COVERED does not mean unsolvable."""
    found = [
        (r["case_id"], arm)
        for r in rows()
        for arm in ("A", "C")
        if r["coverage"] == "NOT_COVERED" and r[f"arm_{arm}"]["truth_correct"]
    ]
    assert ("lark-adad165e", "A") in found
    assert ("lark-adad165e", "C") in found


def test_the_finding_refuses_the_unsolvable_reading():
    text = (ATTR / "BM08-CONTEXT-MISS-ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "does **not** mean the bug was unsolvable" in text or "not read as" in text
    assert "lark-adad165e" in text
    assert "concentration, not a cause" in text.lower() or "not causation" in text.lower()


# ------------------------------------------------------------- probe overlay


def test_the_probe_overlay_uses_the_exact_six_frozen_probe_cases():
    frozen = {c["case_id"] for c in json.loads((PROBE / "probe-manifest.json").read_text(encoding="utf-8"))["cases"]}
    overlay = {r["case_id"] for r in rows() if r["probe_case"]}
    assert overlay == frozen
    assert len(overlay) == 6


def test_the_probe_overlay_copies_the_probe_results():
    probe = {}
    for line in (PROBE / "probe-results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            probe[(record["case_id"], record["condition"])] = record
    for row in rows():
        if not row["probe_case"]:
            continue
        assert row["probe_U_apply"] == probe[(row["case_id"], "U")]["canonical_apply_ok"]
        assert row["probe_S_quote_valid"] == probe[(row["case_id"], "S")]["exact_source_quote_valid"]


def test_every_probe_case_was_context_starved():
    """The reason the format signal and the context signal stay separable."""
    for row in rows():
        if row["probe_case"]:
            assert row["coverage"] in {"NOT_COVERED", "PARTIALLY_COVERED"}, row["case_id"]


# -------------------------------------------------------------------- leakage


def test_the_historical_fix_never_enters_a_model_facing_artifact():
    representation = json.loads((REP / "representation-manifest.json").read_text(encoding="utf-8"))
    blob = json.dumps(representation)
    for marker in ("fix_commit", "fix_touched_files", "fix_touched_regions", "fix_regions", "code_fix_files"):
        assert marker not in blob, f"{marker} leaked into the representation manifest"
    probe_manifest = json.dumps(json.loads((PROBE / "probe-manifest.json").read_text(encoding="utf-8")))
    for marker in ("fix_commit", "fix_touched", "fix_regions"):
        assert marker not in probe_manifest, f"{marker} leaked into the probe manifest"


def test_the_attribution_does_not_write_into_model_facing_paths():
    """Reading the probe manifest for the overlay is fine; writing is not."""
    source = (ATTR / "attribute_misses.py").read_text(encoding="utf-8")
    # Exactly one write target, inside this analysis directory.
    assert source.count("OUT.open(") == 1
    assert source.count("write_text(") == 0
    assert source.count("write_bytes(") == 0
    assert 'OUT = HERE / "context-miss-attribution.jsonl"' in source
    # Every reference to a model-facing artifact is a read.
    for marker in ("probe-manifest.json", "probe-results.jsonl", "results.jsonl"):
        for line in source.splitlines():
            if marker in line:
                assert "read_text" in line or "splitlines" in line, line


def test_no_provider_is_reachable_from_the_attribution():
    for name in ("attribute_misses.py", "attribution_report.py"):
        source = (ATTR / name).read_text(encoding="utf-8")
        for forbidden in ("import socket", "urllib.request", "urlopen(", "RIFT_LLM", "requests.post"):
            assert forbidden not in source, f"{name} references {forbidden}"


def test_no_representation_sample_was_executed():
    """The paid study stays on HOLD."""
    assert not (REP / "representation-results.jsonl").exists()
    assert not (REP / "representation-ledger.jsonl").exists()
    manifest = json.loads((REP / "representation-manifest.json").read_text(encoding="utf-8"))
    assert manifest["budget"]["authorized"] is False
    assert manifest["budget"]["spent"] == 0.0
    assert manifest["expected_samples"] == 144


def test_the_representation_preparation_is_unmodified():
    manifest = json.loads((REP / "representation-manifest.json").read_text(encoding="utf-8"))
    assert manifest["representation_experiment_manifest_hash"] == (
        "76cf6dca742851d1dd9c12cf7b731e7ab92358b40c0baabdd7a85ebb5ec82ae8"
    )
    assert manifest["u_prompt_hash"] == "a1b817d86862b02bf2f9b6b2624186aab0d5c375de4e6f781528c30b1445f4d8"
    assert manifest["s_prompt_hash"] == "cb85ec45b15d368e0d2e195ebc0f879e4e01352ee7c7f3b517b34bc16aeeccd8"
    assert manifest["compiler_hash"] == "d6a2c39b51e9c2e286c2bd44b7132c5696269b8c78e86e04800e2dd81ed8ef5a"
    assert len(manifest["samples"]) == 144
