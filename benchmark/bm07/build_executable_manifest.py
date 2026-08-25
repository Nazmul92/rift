"""Materialise the six frozen baselines and write the executable BM-07 manifest.

Every field is measured here, not copied: each case's baseline is reconstructed
from the pinned parent plus the frozen reproducer, its tree hash is computed from
that tree, and its failure identity is observed through
`riftagent.checks.run_check` — the component that will enforce it during
execution.

A case that cannot reproduce its own curation record is a **stop**, not a
candidate for silent replacement by a fallback.

No provider call is made and no network is used.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "src"))

import bm07_driver as driver  # noqa: E402
import bm07_oracle as oracle  # noqa: E402
import bm07_runner as runner  # noqa: E402

from riftagent.sandbox import probe_isolation, tree_hash  # noqa: E402

ROOT = pathlib.Path("/w")
REPOS = pathlib.Path("/repos")
WORK = pathlib.Path("/tmp/bm07-build")
CURATED = ROOT / "benchmark/bm07/manifest.json"
VALIDATED = ROOT / "benchmark/bm07/validated-cases.json"
OUT = ROOT / "benchmark/bm07/manifest-executable.json"

curated = json.loads(CURATED.read_text(encoding="utf-8"))
records = {c["case_id"]: c for c in json.loads(VALIDATED.read_text(encoding="utf-8"))["cases"]}
probe = probe_isolation()
WORK.mkdir(parents=True, exist_ok=True)

cases = []
stops = []
for frozen in curated["cases"]:
    cid = frozen["case_id"]
    record = records[cid]
    case = {
        "case_id": cid,
        "repository": frozen["repository"],
        "fix_commit": frozen["fix_commit"],
        "parent": frozen["parent"],
        "target_node": frozen["target_node"],
        "preservation_nodes": frozen["preservation_nodes"],
        "preservation_count": frozen["preservation_count"],
        "test_files": record["preservation_nodes"] and [n.split("::")[0] for n in frozen["preservation_nodes"]],
        "src_layout": frozen.get("src_layout", "flat"),
    }
    # The test files the reproducer half comes from: the target's file plus every
    # file the preservation nodes live in.
    case["test_files"] = sorted(
        {frozen["target_node"].split("::")[0]} | {n.split("::")[0] for n in frozen["preservation_nodes"]}
    )
    # Protected: the frozen judge *and* the runner configuration. A candidate
    # may not edit what decides it, and a patch that leaves the target passing by
    # changing pytest's configuration has changed the decision procedure rather
    # than the behaviour. Only files actually present at the parent are listed,
    # so the set names real artifacts rather than a wish.
    present = driver.run(["git", "ls-tree", "-r", "--name-only", frozen["parent"]], REPOS / frozen["repository"]).stdout
    tracked = set(present.split())
    config = sorted(path for path in tracked if pathlib.PurePosixPath(path).name in oracle.RUNNER_CONFIG_FILES)
    case["protected_paths"] = sorted(set(case["test_files"]) | set(config))
    case["runner_config_paths"] = config

    tree = WORK / cid
    try:
        driver.materialise_baseline(case, REPOS, tree)
    except RuntimeError as exc:
        stops.append(f"{cid}: {exc}")
        continue

    case["baseline_tree_hash"] = tree_hash(tree)

    outcome = oracle.run_node(tree, case["target_node"], case["src_layout"])
    if outcome != "fail":
        stops.append(f"{cid}: target does not fail on the constructed baseline ({outcome})")
        shutil.rmtree(tree, ignore_errors=True)
        continue

    signature = driver.observe_failure_identity(tree, case["target_node"], probe)
    if signature is None:
        stops.append(f"{cid}: no failure identity could be observed")
        shutil.rmtree(tree, ignore_errors=True)
        continue
    case["failure_identity"] = signature.to_dict()

    ok, failures, _ = oracle.run_all(tree, list(case["preservation_nodes"]), case["src_layout"])
    if not ok:
        stops.append(f"{cid}: {len(failures)} preservation nodes fail on the constructed baseline")
        shutil.rmtree(tree, ignore_errors=True)
        continue

    # The curated manifest described the oracle as RIFT's own five-phase
    # semantics. That stopped being true when the independent oracle was
    # introduced, and a manifest that misdescribes how correctness is decided is
    # worse than one that says nothing. The three evaluators are named
    # separately here.
    # Frozen once, from a stable digest. `hash()` is randomised per process, so
    # deriving the seed at run time would make arm B unreproducible between runs
    # of the same manifest.
    case["probe_seed"] = int(hashlib.sha256(cid.encode("utf-8")).hexdigest()[:8], 16) % 100000
    case["case_oracle"] = {
        "correct_iff": [
            "the candidate applies cleanly to the frozen baseline",
            "no path in protected_paths is modified (frozen tests and runner configuration)",
            "the target node passes",
            "every node in preservation_nodes passes — the complete set, not a sample",
        ],
        "evaluated_by": "benchmark/bm07/bm07_oracle.py — plain git and pytest; imports no riftagent module",
        "weak_evaluator": "target-pass acceptance only",
        "strong_evaluator": "the frozen RIFT five-phase gate, unmodified, via `rift verify`",
        "ground_truth_evaluator": "the independent oracle above; never derived from the strong verdict",
        "deterministic": True,
        "provider_independent": True,
    }
    case["pre_model_rank_score"] = frozen.get("pre_model_rank_score")
    case["direct_parent_proof"] = frozen["direct_parent_proof"]
    cases.append(case)
    shutil.rmtree(tree, ignore_errors=True)
    print(
        f"  OK  {cid:22} tree={case['baseline_tree_hash'][:12]}  "
        f"sig={signature.exception_type}  pres={case['preservation_count']}"
    )

if stops:
    print("\nSTOP — a frozen case cannot reproduce its own curation record:")
    for s in stops:
        print(f"  {s}")
    raise SystemExit(1)

manifest = {
    "benchmark_id": driver.BENCHMARK_ID,
    "protocol_version": driver.PROTOCOL_VERSION,
    "kind": curated["kind"],
    "arms": curated["protocol"]["arms"],
    "model": {
        "requested_model_id": "claude-sonnet-4-6",
        "required_reported_model_identity": (
            "provider-reported model from MODEL_RESPONSE_RECEIVED in the exact task ledger must equal "
            "requested_model_id; pricing metadata is not proof of identity; schema-repair responses "
            "must satisfy the same rule"
        ),
    },
    "pricing": {"input_per_mtok": 3.0, "output_per_mtok": 15.0, "currency": "USD"},
    "budget": {
        "total_usd_ceiling": 6.0,
        "max_input_tokens": 60000,
        "max_output_tokens": 4000,
        "max_attempts": 1,
        "reservation_rule": (
            "reserve the maximum possible cost of a request before sending it, settle on provider-reported "
            "usage, and refuse the call when remaining budget is below the reservation"
        ),
        "per_case_arm_max_usd": 0.25,
    },
    "primary_metric": curated["primary_metric"],
    "protocol": curated["protocol"],
    "harness_only": curated["harness_only"],
    "selection_rule": curated["selection_rule"],
    "fallback_substitution": curated["fallback_substitution"],
    "validated_fallback": curated["validated_fallback"],
    "runtime_hash": curated["identity"]["runtime_hash"],
    "driver_hash": driver.driver_hash(),
    # The orchestration program is frozen alongside the evaluator: it is the code
    # that decides when a provider call happens.
    "runner_hash": runner.runner_hash(),
    "oracle_hash": oracle.oracle_hash(),
    "reference_environment": curated["identity"]["reference_environment"],
    "cases": cases,
}
manifest["manifest_hash"] = driver.manifest_hash(manifest)
OUT.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")

problems = driver.validate_manifest(manifest)
print(f"\ncases           : {len(cases)}")
print(f"runtime_hash    : {manifest['runtime_hash']}")
print(f"driver_hash     : {manifest['driver_hash']}")
print(f"runner_hash     : {manifest['runner_hash']}")
print(f"oracle_hash     : {manifest['oracle_hash']}")
print(f"manifest_hash   : {manifest['manifest_hash']}")
print(f"validate_manifest failures: {len(problems)}")
for p in problems:
    print(f"  {p}")
print(f"sha256(file)    : {hashlib.sha256(OUT.read_bytes()).hexdigest()}")
raise SystemExit(1 if problems else 0)
