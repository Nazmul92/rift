"""Freeze the BM-07 manifest — only after model-free validation is green.

Every field is recomputed here from the validated case records and the tree, so
the manifest cannot inherit a stale number from a document. It binds what the run
must not be allowed to change afterwards: the cases, their complete preservation
sets, the oracle, and the identity of the runtime that will execute them.

The manifest contains no historical source patch content. The upstream fix, the
shortcut hypotheses and the preservation answers are harness-only evidence and
are never placed anywhere a model can see them.

No model is called and no network is used.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path("/w")
VALIDATED = ROOT / "benchmark/bm07/validated-cases.json"
OUT = ROOT / "benchmark/bm07/manifest.json"

sys.path.insert(0, str(ROOT / "benchmark" / "bm06"))
import driver  # noqa: E402  # benchmark/bm06/driver.py — runtime_hash lives there

data = json.loads(VALIDATED.read_text(encoding="utf-8"))
validated = [c for c in data["cases"] if c["curation_status"] == "validated"]
primary = [c for c in validated if c.get("corpus_role") == "primary"]
fallback = [c for c in validated if c.get("corpus_role") == "validated_fallback"]

if not primary:
    print("no validated primary cases; manifest not frozen")
    raise SystemExit(1)

rt_hash, rt_files = driver.runtime_hash(ROOT)
driver_hash = hashlib.sha256((ROOT / "benchmark/bm06/driver.py").read_bytes()).hexdigest()

cases = []
for c in sorted(primary, key=lambda c: c["case_id"]):
    cases.append(
        {
            "case_id": c["case_id"],
            "repository": c["repository"],
            "fix_commit": c["fix_commit"],
            "parent": c["parent"],
            "direct_parent_proof": "fix_commit^ == parent, verified and failed closed at curation",
            "target_node": c["target_node"],
            "target_resolution_method": c["target_resolution_method"],
            "preservation_nodes": c["preservation_nodes"],
            "preservation_count": c["preservation_count"],
            "src_layout": c.get("src_layout", "flat"),
            "pre_model_rank_score": c.get("pre_model_score"),
            "historical_fix_evidence": {
                "baseline_target_result": c["baseline_target_result"],
                "baseline_preservation_all_passed": c["baseline_preservation_results"]["all_passed"],
                "baseline_preservation_executed": c["baseline_preservation_results"]["executed"],
                "historical_fix_target_result": c["historical_fix_target_result"],
                "historical_fix_preservation_all_passed": c["historical_fix_preservation_results"]["all_passed"],
                "historical_fix_preservation_executed": c["historical_fix_preservation_results"]["executed"],
            },
            "case_oracle": {
                "correct_iff": [
                    "the candidate applies to the frozen baseline",
                    "the target node passes",
                    "every node in preservation_nodes passes — the complete set, not a sample",
                    "no frozen check file or runner configuration is modified",
                ],
                "evaluated_by": "the existing RIFT five-phase verification semantics; no bespoke oracle",
                "deterministic": True,
                "provider_independent": True,
            },
        }
    )

manifest = {
    "benchmark": "BM-07",
    "kind": "mechanism/discrimination benchmark — not representative general coding performance",
    "primary_corpus_size": len(cases),
    "validated_total": len(validated),
    "validated_fallback": sorted(c["case_id"] for c in fallback),
    "fallback_substitution": "not permitted; no replacement conditions are predefined, so fallbacks are unused",
    "selection_rule": (
        "highest pre-model curation score per repository, among model-free-validated cases, "
        "chosen before any provider call"
    ),
    "primary_metric": {
        "name": "same-candidate harmful weak acceptances prevented",
        "definition": "weak = ACCEPT, strong shadow gate = REJECT, ground truth = WRONG",
        "reported_with": "same-candidate strong REJECT on ground-truth CORRECT (false rejection)",
        "matrix": "the full weak x strong x ground-truth table is reported whole",
    },
    "protocol": {
        "shadow_evaluation": "every canonical arm-A candidate is judged twice on the same bytes",
        "additional_model_calls": 0,
        "arms": {
            "A": "strong model + common proposal infrastructure -> target-pass acceptance",
            "B": "same proposal infrastructure -> random-probe kernel",
            "C": "same proposal infrastructure -> full RIFT kernel",
        },
        "arm_results_are": "secondary",
        "canonicalizer_parity": (
            "model -> raw persistence -> normalization -> deterministic canonicalizer -> "
            "arm/protocol evaluation; identical for A, B and C"
        ),
    },
    "identity": {
        "runtime_hash": rt_hash,
        "runtime_files": sorted(rt_files),
        "driver_hash": driver_hash,
        "reference_environment": "python:3.12-slim, Python 3.12.14, linux/amd64, git 2.47.3",
        "model_identity_requirement": (
            "provider-reported model must be recorded from MODEL_RESPONSE_RECEIVED in the task "
            "ledger and must equal the configured model"
        ),
    },
    "harness_only": [
        "historical source patch content",
        "preservation node expectations",
        "shortcut hypotheses",
        "task-required ground truth",
    ],
    "cases": cases,
}

body = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
manifest_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
OUT.write_text(
    json.dumps({**manifest, "manifest_hash": manifest_hash}, indent=1, sort_keys=True) + "\n",
    encoding="utf-8",
)

print(f"primary corpus     : {len(cases)}")
print(f"validated total    : {len(validated)}")
print(f"fallback pool      : {len(fallback)}")
print(f"preservation nodes : {sum(c['preservation_count'] for c in cases)} across the primary corpus")
print(f"runtime_hash       : {rt_hash}")
print(f"driver_hash        : {driver_hash}")
print(f"manifest_hash      : {manifest_hash}")
for c in cases:
    print(f"  {c['case_id']:22} pres={c['preservation_count']:3}  {c['target_node'][:64]}")
