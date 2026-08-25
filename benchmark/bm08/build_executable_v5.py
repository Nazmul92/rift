"""BM-08-v5 stage F: the executable A+C experiment, derived from the frozen corpus.

`corpus-v5.json` says which 24 bugs the benchmark runs. It says nothing about
which model, at what price, under what ceiling, or which files a candidate may
not touch. This adds exactly that.

Every case is derived from the frozen corpus — never copied from the stale v3
executable manifest, which binds 14 cases, an older population and a superseded
corpus hash. The build refuses unless all three frozen v5 identities verify
first, so an executable manifest cannot exist for a corpus nobody approved.

Official arms are A and C. BM-08 asks whether frozen full RIFT produces more
truth-correct fixes per dollar than the same frozen model alone; arm B answers a
different question and would spend real money on a number no BM-08 conclusion
rests on.

The N=3 stability proof belongs to corpus admission and is carried through as
provenance, not re-run here. Paid preflight makes one fresh observation per case
and requires it to equal the frozen identity.

No model is called and no network is used.
"""

import hashlib
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "src"))

import bm08_driver as driver  # noqa: E402
import bm08_oracle as oracle  # noqa: E402
import bm08_runner as runner  # noqa: E402
import execution_environment as environment  # noqa: E402

from riftagent.sandbox import tree_hash  # noqa: E402

BM08 = pathlib.Path(__file__).parent
CORPUS = BM08 / "corpus-v5.json"
REPO_ROOTS = [pathlib.Path("/repos"), pathlib.Path("/repos-v3"), pathlib.Path("/repos-v5")]
WORK = pathlib.Path("/tmp/bm08-exec-v5")
OUT = pathlib.Path("/s/bm08_manifest_executable_v5.json")

EXPECTED_CORPUS = "e6bdd3f116981bc58daf7f21eb4a5e0a524e9a067227cd2cc40fc994a19ad3f9"
EXPECTED_POPULATION_V5 = "4645de61c549bf8ad06697e1b8279ddfee51d19af24379e1dd45880f350fe0bc"
EXPECTED_EXCLUSION = "d4090113b0670321b1d5a9c48ebe3949adeb60f865e8b07bb414aea21f137e87"

MODEL = "claude-sonnet-4-6"
PRICING = {"input_per_mtok": 3.0, "output_per_mtok": 15.0, "currency": "USD"}
# 24 cases x 2 arms = 48 executions at the frozen $0.48 derived reservation is a
# worst case of $23.04. A $15.00 ceiling could not have reserved the official set
# and would have stranded the run part-way through with money already spent. The
# request and token ceilings are unchanged; only the total is corrected to cover
# what they derive.
BUDGET = {
    "total_usd_ceiling": 25.0,
    "per_case_arm_max_usd": 0.25,
    "max_input_tokens": 60000,
    "max_output_tokens": 4000,
    "max_attempts": 1,
    "reservation_rule": (
        "reserve the maximum possible cost of a request before sending it, settle on "
        "provider-reported usage, and refuse the call when remaining budget is below the reservation"
    ),
}


def corpus_hash(blob: dict) -> str:
    body = {k: v for k, v in blob.items() if k != "corpus_manifest_hash"}
    return hashlib.sha256((json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()


def repo_for(name: str) -> pathlib.Path:
    found = [root / name for root in REPO_ROOTS if (root / name / ".git").exists()]
    if len(found) != 1:
        raise SystemExit(f"BLOCKED_REPOSITORY_RESOLUTION: {name} resolved to {len(found)} locations")
    return found[0]


def stability_evidence_hash(case: dict) -> str:
    """An immutable digest of the three admission observations.

    The observations themselves stay in the corpus; the manifest binds their
    digest so a later run can prove the stability proof it inherited is the one
    that was actually performed.
    """
    payload = json.dumps(case.get("failure_identity_observations", []), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    observed = corpus_hash(corpus)
    if observed != EXPECTED_CORPUS or corpus["corpus_manifest_hash"] != EXPECTED_CORPUS:
        print(f"BLOCKED_CORPUS_IDENTITY: observed {observed}")
        return 2
    if corpus["repository_population_hash_v5"] != EXPECTED_POPULATION_V5:
        print("BLOCKED_CORPUS_IDENTITY: population hash moved")
        return 2
    if corpus["exclusion_set_hash"] != EXPECTED_EXCLUSION:
        print("BLOCKED_CORPUS_IDENTITY: exclusion hash moved")
        return 2
    print(f"corpus identity     : {EXPECTED_CORPUS[:16]}…  verified")
    print(f"population v5       : {EXPECTED_POPULATION_V5[:16]}…  verified")
    print(f"exclusion authority : {EXPECTED_EXCLUSION[:16]}…  verified")

    shutil.rmtree(WORK, ignore_errors=True)
    cases, stops = [], []
    for src in corpus["cases"]:
        cid = src["case_id"]
        repo = repo_for(src["repository"])

        names = set(driver.run(["git", "ls-tree", "-r", "--name-only", src["parent"]], repo).stdout.splitlines())
        config = sorted(p for p in names if pathlib.Path(p).name in oracle.RUNNER_CONFIG_FILES)

        case = {
            "case_id": cid,
            "repository": src["repository"],
            "fix_commit": src["fix_commit"],
            "parent": src["parent"],
            "author_date": src["author_date"],
            "target_node": src["target_node"],
            "additional_targets": src.get("additional_targets", []),
            "preservation_nodes": src["preservation_nodes"],
            "preservation_count": src["preservation_count"],
            "source_files": src["source_files"],
            "test_files": src["test_files"],
            "src_layout": src.get("src_layout", "flat"),
            "baseline_tree_hash": src["baseline_tree_hash"],
            "failure_identity": src["failure_identity"],
            "protected_paths": sorted(set(src["test_files"]) | set(config)),
            "runner_config_paths": config,
            "case_oracle": {
                "correct_iff": [
                    "the target node passes",
                    "every preservation node still passes",
                    "no protected path is modified",
                ]
            },
            # Carried, not re-run: the three-process proof belongs to admission.
            "failure_identity_stable": src["failure_identity_stable"],
            "stability_observations": len(src.get("failure_identity_observations", [])),
            "stability_evidence_hash": stability_evidence_hash(src),
            "categories": src.get("categories", []),
            "prior_exposure_status": src["prior_exposure_status"],
            "duplicate_family_provenance": src["duplicate_family_provenance"],
            "deterministic_order_provenance": src["deterministic_order_provenance"],
        }
        if not case["failure_identity_stable"] or case["stability_observations"] != 3:
            stops.append(f"{cid}: corpus case lacks the N=3 stability proof")
            continue

        tree = WORK / cid
        try:
            driver.materialise_baseline(case, repo.parent, tree)
        except RuntimeError as exc:
            stops.append(f"{cid}: {exc}")
            continue
        rebuilt = tree_hash(tree)
        shutil.rmtree(tree, ignore_errors=True)
        if rebuilt != case["baseline_tree_hash"]:
            stops.append(f"{cid}: baseline {rebuilt[:12]} != corpus {case['baseline_tree_hash'][:12]}")
            continue
        cases.append(case)
        print(f"  ok  {cid:28} protected={len(case['protected_paths']):2} pres={case['preservation_count']:3}")

    if stops:
        for s in stops:
            print(f"  FAIL  {s}")
        print(f"BLOCKED: {len(stops)} case(s) did not reconstruct; no executable manifest written")
        return 1

    manifest = {
        "benchmark_id": "BM-08",
        "protocol_version": 1,
        "arms": {
            "A": "model alone; weak target-pass acceptance",
            "C": "model proposal with the full frozen RIFT protocol",
        },
        "official_arms": list(runner.OFFICIAL_ARMS),
        "expected_official_records": len(cases) * len(runner.OFFICIAL_ARMS),
        "model": {"requested_model_id": MODEL, "required_reported_model_identity": "must equal requested"},
        "pricing": PRICING,
        "budget": BUDGET,
        "runtime_hash": driver.observed_runtime_hash(),
        "driver_hash": driver.driver_hash(),
        "runner_hash": runner.runner_hash(),
        "oracle_hash": oracle.oracle_hash(),
        "corpus_manifest_hash": EXPECTED_CORPUS,
        "repository_population_hash_v5": EXPECTED_POPULATION_V5,
        "exclusion_set_hash": EXPECTED_EXCLUSION,
        "execution_environment_hash": environment.environment_hash(),
        "execution_environment": environment.describe(),
        "stability_rule": corpus["stability_rule"],
        "cases": cases,
    }
    manifest["manifest_hash"] = driver.manifest_hash(manifest)
    OUT.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    problems = driver.validate_manifest(manifest)
    print(f"\ncases                : {len(cases)} across {len({c['repository'] for c in cases})} repositories")
    print(f"official arms        : {manifest['official_arms']}")
    print(f"expected records     : {manifest['expected_official_records']}")
    print(f"runtime_hash         : {manifest['runtime_hash']}")
    print(f"driver_hash          : {manifest['driver_hash']}")
    print(f"runner_hash          : {manifest['runner_hash']}")
    print(f"oracle_hash          : {manifest['oracle_hash']}")
    print(f"manifest_hash        : {manifest['manifest_hash']}")
    print(f"validate_manifest    : {len(problems)} failures {problems[:3]}")
    print(f"budget ceiling       : ${BUDGET['total_usd_ceiling']:.2f}, per arm ${BUDGET['per_case_arm_max_usd']:.2f}")
    reservation = runner.required_reservation(manifest)
    worst = reservation * len(cases) * len(runner.OFFICIAL_ARMS)
    print(f"reservation per arm  : ${reservation:.4f}")
    print(f"worst-case total     : ${worst:.2f} ({len(cases)} x {len(runner.OFFICIAL_ARMS)} x ${reservation:.4f})")
    print(f"ceiling covers worst : {BUDGET['total_usd_ceiling'] >= worst}")
    print(f"execution_environment_hash : {manifest['execution_environment_hash']}")
    print(f"\nwritten: {OUT}", file=sys.stderr)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
