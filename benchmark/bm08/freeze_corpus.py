"""BM-08-v3 stage E: freeze the primary corpus, but only if the threshold passes.

Runs after model-free validation and does no selecting of its own. Every case it
writes was already chosen by the frozen pipeline; this binds each one to its
provenance and computes the deterministic corpus hash.

It refuses to emit a manifest below the frozen minimum. That refusal is the
point: a corpus manifest is an artifact people cite, and one that exists for a
corpus which never cleared its own predeclared bar is worse than no manifest at
all — it launders a shortfall into something that looks executable.

No model is called and no network is used.
"""

import collections
import hashlib
import json
import pathlib
import sys

S = pathlib.Path("/s")
BM08 = pathlib.Path(__file__).parent
VALIDATED = S / "bm08_validated.json"
CASES = S / "bm08_cases.json"
POPULATION = BM08 / "repository-population-v5.json"
EXCLUSIONS = S / "bm08_exclusions.json"
OUT = S / "bm08_v5_corpus.json"

MIN_CASES = 12
MIN_REPOS = 10
EXPECTED_POPULATION_HASH = "4645de61c549bf8ad06697e1b8279ddfee51d19af24379e1dd45880f350fe0bc"
EXPECTED_EXCLUSION_HASH = "d4090113b0670321b1d5a9c48ebe3949adeb60f865e8b07bb414aea21f137e87"


def exclusion_hash(blob: dict) -> str:
    """Recomputed from the commit set itself, never trusted from the file.

    Without this in the manifest, "unseen" is a prose claim: the corpus records
    that an exclusion set was applied but not *which one*. Someone could change
    `exclusions.json`, re-select a different unseen population, and the corpus
    manifest would carry no evidence that the authority had moved.
    """
    return hashlib.sha256("\n".join(sorted(blob["excluded_commits"])).encode("utf-8")).hexdigest()


def population_hash(blob: dict) -> str:
    """Recomputed over the declaration minus its own hash field.

    The field is `repository_population_hash_v5` in the v5 manifest; a helper
    that stripped only the v3 name would fold the hash into its own input and
    could never reproduce it.
    """
    body = json.dumps(
        {k: v for k, v in blob.items() if not k.startswith("repository_population_hash")},
        indent=1,
        sort_keys=True,
    )
    return hashlib.sha256((body + "\n").encode("utf-8")).hexdigest()


def main() -> int:
    pop = json.loads(POPULATION.read_text(encoding="utf-8"))
    observed = population_hash(pop)
    if observed != EXPECTED_POPULATION_HASH or pop["repository_population_hash_v5"] != EXPECTED_POPULATION_HASH:
        print(f"BLOCKED_POPULATION_IDENTITY: observed {observed}")
        return 2

    exclusions = json.loads(EXCLUSIONS.read_text(encoding="utf-8"))
    observed_exclusion = exclusion_hash(exclusions)
    recorded_exclusion = exclusions.get("excluded_commit_set_hash", "")
    if observed_exclusion != EXPECTED_EXCLUSION_HASH or recorded_exclusion != EXPECTED_EXCLUSION_HASH:
        print(f"BLOCKED_EXCLUSION_IDENTITY: observed {observed_exclusion}, recorded {recorded_exclusion}")
        return 2

    validated = json.loads(VALIDATED.read_text(encoding="utf-8"))
    detail = {c["case_id"]: c for c in json.loads(CASES.read_text(encoding="utf-8"))["cases"]}
    primary = [r for r in validated["cases"] if r.get("corpus_role") == "primary"]
    repos = sorted({r["repository"] for r in primary})

    print(f"final primary cases         : {len(primary)}")
    print(f"final distinct repositories : {len(repos)}")
    print(f"frozen minimum              : >={MIN_CASES} cases AND >={MIN_REPOS} repositories")

    if len(primary) < MIN_CASES or len(repos) < MIN_REPOS:
        print(f"\nCORPUS_SHORTFALL: {len(primary)} cases, {len(repos)} repositories. No manifest written.")
        return 1

    per_repo: collections.Counter = collections.Counter(r["repository"] for r in primary)
    assert all(n <= 3 for n in per_repo.values()), f"repository cap violated: {dict(per_repo)}"

    cases = []
    for row in sorted(primary, key=lambda r: r.get("queue_position") or 0):
        src = detail[row["case_id"]]
        cases.append(
            {
                "case_id": row["case_id"],
                "repository": row["repository"],
                "fix_commit": row["fix_commit"],
                "parent": row["parent"],
                "author_date": src.get("author_date", ""),
                "committer_date": src.get("committer_date", ""),
                "baseline_tree_hash": row.get("baseline_tree_hash", ""),
                "target_node": row["target_node"],
                "target_resolution_method": row.get("target_resolution_method", ""),
                "additional_targets": src.get("additional_targets", []),
                "preservation_nodes": row["preservation_nodes"],
                "preservation_count": row["preservation_count"],
                "source_files": src.get("source_files", []),
                "test_files": src.get("test_files", []),
                "src_layout": row.get("src_layout", "flat"),
                "failure_identity": row.get("failure_identity", {}),
                "baseline_target_result": row.get("baseline_target_result"),
                "baseline_preservation_results": row.get("baseline_preservation_results"),
                "historical_fix_target_result": row.get("historical_fix_target_result"),
                "historical_fix_preservation_results": row.get("historical_fix_preservation_results"),
                "prior_exposure_status": "unseen: excluded set applied to fix_commit and parent",
                "duplicate_family_provenance": {
                    "primary_source_file": sorted(src.get("source_files", ["?"]))[0],
                    "primary_test_file": sorted(src.get("test_files", ["?"]))[0],
                    "rule": "one case per (repository, primary source file) and (repository, primary test file)",
                },
                "deterministic_order_provenance": {
                    "order_key": src.get("order_key", ""),
                    "queue_position": row.get("queue_position"),
                    "rule": "SHA-256(fix_commit), fixed before validation began",
                },
                "categories": src.get("categories", []),
                "subject": src.get("subject", ""),
                # BM-08-v4 evidence: three independent fresh-process observations.
                "failure_identity_stable": row.get("failure_identity_stable"),
                "failure_identity_observations": row.get("failure_identity_observations", []),
            }
        )

    manifest = {
        "benchmark_id": "BM-08-v5",
        "protocol": "A = model alone, C = full RIFT; ordinary unseen historical Python bugs",
        "repository_population_hash_v5": EXPECTED_POPULATION_HASH,
        "repository_population_count": pop["total_repository_count"],
        # Binds the corpus to the exact prior-exposure authority that produced
        # its "unseen" population. Without it, "unseen" is prose.
        "exclusion_set_hash": EXPECTED_EXCLUSION_HASH,
        "exclusion_set_count": exclusions["excluded_commit_count"],
        "exclusion_set_authority": exclusions["authority"],
        "author_date_floor": "2018-01-01",
        "stability_rule": {
            "observations": 3,
            "independent_fresh_processes": True,
            "equality": "exact governed failure identity, no normalisation",
            "rejection_reason": "unstable_failure_identity",
        },
        "minimum_cases": MIN_CASES,
        "minimum_repositories": MIN_REPOS,
        "max_per_repository": 3,
        "primary_case_count": len(cases),
        "distinct_repositories": len(repos),
        "repositories": repos,
        "cases": cases,
    }
    body = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    manifest["corpus_manifest_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    OUT.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"\nper-repository              : {dict(per_repo.most_common())}")
    labels = collections.Counter(k for c in cases for k in c["categories"])
    print(f"categories                  : {dict(labels.most_common())}")
    print(f"exclusion_set_hash          : {EXPECTED_EXCLUSION_HASH}")
    print(f"corpus_manifest_hash        : {manifest['corpus_manifest_hash']}")
    print(f"\nTHRESHOLD PASS -> corpus frozen: {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
