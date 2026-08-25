"""BM-08-v3 stage F: turn the frozen corpus into an executable manifest. Model-free.

`corpus-v3.json` records *which bugs* the benchmark runs. It says nothing about
which model, at what price, under what ceiling, or which files a candidate may
not touch. This adds exactly that, and nothing about case selection: the case
list, its order and its provenance are copied through unchanged, and the build
refuses if the corpus identity chain does not verify first.

Protected paths are computed the way BM-07 computed them — the case's test files
plus whatever runner configuration actually exists at the parent commit. A
candidate that edits the judge is not a fix, and a path list that names files a
repository does not have would silently protect nothing.

Arm B's probe seed is derived once from `SHA256(case_id)`. Never `hash()`:
BM-07's arm B was briefly unreproducible because `hash()` is randomised per
process, and that mistake is not worth making twice.

All three arms are built. The frozen runner's `OFFICIAL_ARMS` is (A, B, C), so an
A/C-only manifest could never produce an official score — B is the ablation, it
costs little, and running it is cheaper than editing a frozen orchestration
program.

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

from riftagent.sandbox import tree_hash  # noqa: E402

BM08 = pathlib.Path(__file__).parent
CORPUS = BM08 / "corpus-v3.json"
REPO_ROOTS = [pathlib.Path("/repos"), pathlib.Path("/repos-v3")]
WORK = pathlib.Path("/tmp/bm08-exec")
OUT = pathlib.Path("/s/bm08_manifest_executable.json")

EXPECTED_CORPUS = "82871bf45960b443665d5c53523c4dae5dcfaa6b54d45b39d502631052ca8250"
EXPECTED_POPULATION = "e9c410a683b46679dae92f7837149812b8d2a72d3256ab1551b9707d076a65ab"
EXPECTED_EXCLUSION = "d4090113b0670321b1d5a9c48ebe3949adeb60f865e8b07bb414aea21f137e87"

MODEL = "claude-sonnet-4-6"
PRICING = {"input_per_mtok": 3.0, "output_per_mtok": 15.0, "currency": "USD"}
# 14 cases x 3 arms = 42 arm executions. The per-arm cap is BM-07's; the total
# ceiling is sized so the worst case (every arm at its cap) still leaves more
# than one reservation of headroom before the final arm.
BUDGET = {
    "total_usd_ceiling": 12.0,
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
    for root in REPO_ROOTS:
        if (root / name / ".git").exists():
            return root / name
    raise SystemExit(f"BLOCKED_REPOSITORY_RESOLUTION: {name} not found under {REPO_ROOTS}")


def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    observed = corpus_hash(corpus)
    if observed != EXPECTED_CORPUS or corpus["corpus_manifest_hash"] != EXPECTED_CORPUS:
        print(f"BLOCKED_CORPUS_IDENTITY: observed {observed}")
        return 2
    if corpus["repository_population_hash"] != EXPECTED_POPULATION:
        print("BLOCKED_POPULATION_IDENTITY")
        return 2
    if corpus["exclusion_set_hash"] != EXPECTED_EXCLUSION:
        print("BLOCKED_EXCLUSION_IDENTITY")
        return 2
    print(f"corpus identity      : {EXPECTED_CORPUS[:16]}…  verified")
    print(f"population identity  : {EXPECTED_POPULATION[:16]}…  verified")
    print(f"exclusion identity   : {EXPECTED_EXCLUSION[:16]}…  verified")

    shutil.rmtree(WORK, ignore_errors=True)
    cases, stops = [], []
    for src in corpus["cases"]:
        cid = src["case_id"]
        repo = repo_for(src["repository"])

        # Runner configuration that actually exists at the parent, not a list of
        # names a repository may not have.
        names = driver.run(["git", "ls-tree", "-r", "--name-only", src["parent"]], repo).stdout.splitlines()
        present = set(names)
        config = sorted(p for p in present if pathlib.Path(p).name in oracle.RUNNER_CONFIG_FILES)

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
            # Frozen in the manifest, never derived at run time.
            "probe_seed": int(hashlib.sha256(cid.encode()).hexdigest()[:8], 16) % 100000,
            "case_oracle": {
                "correct_iff": [
                    "the target node passes",
                    "every preservation node still passes",
                    "no protected path is modified",
                ]
            },
            "categories": src.get("categories", []),
            "prior_exposure_status": src["prior_exposure_status"],
            "duplicate_family_provenance": src["duplicate_family_provenance"],
            "deterministic_order_provenance": src["deterministic_order_provenance"],
        }

        # Reconstruct once, model-free, and confirm the recorded baseline
        # identity is reproducible from this manifest alone.
        tree = WORK / cid
        try:
            driver.materialise_baseline(case, repo.parent, tree)
        except RuntimeError as exc:
            stops.append(f"{cid}: {exc}")
            continue
        observed_tree = tree_hash(tree)
        shutil.rmtree(tree, ignore_errors=True)
        if observed_tree != case["baseline_tree_hash"]:
            stops.append(f"{cid}: baseline {observed_tree[:12]} != corpus {case['baseline_tree_hash'][:12]}")
            continue
        cases.append(case)
        print(f"  ok  {cid:26} protected={len(case['protected_paths']):2} pres={case['preservation_count']:3}")

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
            "B": "model proposal with the frozen random-probe protocol (ablation)",
            "C": "model proposal with the full frozen RIFT protocol",
        },
        "model": {"requested_model_id": MODEL, "required_reported_model_identity": "must equal requested"},
        "pricing": PRICING,
        "budget": BUDGET,
        "runtime_hash": driver.observed_runtime_hash(),
        "driver_hash": driver.driver_hash(),
        "runner_hash": runner.runner_hash(),
        "oracle_hash": oracle.oracle_hash(),
        "corpus_manifest_hash": EXPECTED_CORPUS,
        "repository_population_hash": EXPECTED_POPULATION,
        "exclusion_set_hash": EXPECTED_EXCLUSION,
        "cases": cases,
    }
    manifest["manifest_hash"] = driver.manifest_hash(manifest)
    OUT.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    problems = driver.validate_manifest(manifest)
    print(f"\ncases              : {len(cases)} across {len({c['repository'] for c in cases})} repositories")
    print(f"arm executions     : {len(cases) * 3}")
    print(f"runtime_hash       : {manifest['runtime_hash']}")
    print(f"driver_hash        : {manifest['driver_hash']}")
    print(f"runner_hash        : {manifest['runner_hash']}")
    print(f"oracle_hash        : {manifest['oracle_hash']}")
    print(f"manifest_hash      : {manifest['manifest_hash']}")
    print(f"validate_manifest  : {len(problems)} failures {problems[:3]}")
    print(f"budget ceiling     : ${BUDGET['total_usd_ceiling']:.2f}, per arm ${BUDGET['per_case_arm_max_usd']:.2f}")
    print(f"reservation per arm: ${runner.required_reservation(manifest):.4f}")
    print(f"\nwritten: {OUT}", file=sys.stderr)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
