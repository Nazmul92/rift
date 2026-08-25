"""EXECUTION-TRACE PREMISE AUDIT — ZERO COST, EVALUATOR-ONLY, NO PROVIDER.

One conditional selector-v2 proposal needs its premise tested before it is
built:

    for the 6 BM-08 cases whose historical fix file was never discovered, would
    deterministic execution tracing during the frozen failing reproduction have
    exposed the fix file and/or the fix region?

Two measurements, kept apart on purpose. A file can execute while the fix region
inside it never runs, and that distinction is the difference between "tracing
would have found the file" and "tracing would have ranked the right definition".
Collapsing them would answer a question nobody asked.

Order of operations is the discipline here:

    freeze the manifest  ->  verify the untraced failure identity  ->  trace

The manifest, including the classification thresholds, is written before a
single trace exists. The untraced reproduction is verified against the frozen
official identity first, because tracing a *different* failure and reporting
where it executed would be evidence about nothing. Each traced repeat must then
reproduce that same identity, or it is discarded as observer perturbation rather
than counted.

The historical fix is evaluator-only: it is read to ask "did this region run",
and it reaches no prompt, no context, and no runtime artifact. No model is used.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "benchmark" / "bm08"))
sys.path.insert(0, str(ROOT / "src"))

import bm08_driver as driver  # noqa: E402
import confinement  # noqa: E402
import execution_environment as environment  # noqa: E402

from riftagent.sandbox import tree_hash  # noqa: E402

BANNER = "EXECUTION-TRACE PREMISE AUDIT — ZERO COST — EVALUATOR-ONLY — NOT A SELECTOR CHANGE"
BM08 = ROOT / "benchmark" / "bm08"
ATTRIBUTION = ROOT / "benchmark" / "analysis" / "context_attribution" / "context-miss-attribution.jsonl"
OBSERVER = BM08 / "observe_signature.py"
TRACER = HERE / "trace_observe.py"
MANIFEST = HERE / "trace-audit-manifest.json"
OUT = HERE / "execution-trace-audit.json"
WORK = pathlib.Path("/tmp/trace-audit")
REPO_ROOTS = (pathlib.Path("/repos"), pathlib.Path("/repos-v3"), pathlib.Path("/repos-v5"))

NEVER_DISCOVERED = ("TRACEBACK_DID_NOT_REACH_FIX_FILE", "GREP_DID_NOT_FIND_FIX_REGION")
REPEATS = 3

STABLE_TRUE = "STABLE_3_OF_3"
STABLE_FALSE = "STABLE_0_OF_3"
UNSTABLE_2 = "UNSTABLE_2_OF_3"
UNSTABLE_1 = "UNSTABLE_1_OF_3"
TRACE_INVALID = "TRACE_INVALID"


class AuditBlocked(RuntimeError):
    pass


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def repo_for(name: str) -> pathlib.Path:
    found = [root / name for root in REPO_ROOTS if (root / name / ".git").is_dir()]
    if len(found) != 1:
        raise AuditBlocked(f"BLOCKED: {name} resolved to {len(found)} repository roots")
    return found[0]


def attribution_rows() -> list[dict]:
    return [json.loads(line) for line in ATTRIBUTION.read_text(encoding="utf-8").splitlines() if line.strip()]


def classify(observations: list[bool | None]) -> str:
    """Three observations to one status. 2/3 never becomes true."""
    valid = [o for o in observations if o is not None]
    if len(valid) < REPEATS:
        return TRACE_INVALID
    hits = sum(1 for o in valid if o)
    return {3: STABLE_TRUE, 2: UNSTABLE_2, 1: UNSTABLE_1, 0: STABLE_FALSE}[hits]


def identity_of(payload: dict) -> tuple[str, str]:
    identity = payload.get("identity") or {}
    return (identity.get("exception_type", ""), identity.get("message", ""))


def region_hit(executed: dict, path: str, spans: list) -> bool:
    """Executed-line overlap with the historical fix region. Conservative."""
    lines = set(executed.get(path) or [])
    if not lines:
        return False
    return any(any(int(start) <= line <= int(end) for line in lines) for start, end in spans)


def governed_identity(tree: pathlib.Path, node: str) -> dict:
    """The frozen governed observer, unchanged, in its own process."""
    proc = confinement.run_repository_check([sys.executable, str(OBSERVER), str(tree), node], tree, timeout=1800)
    try:
        return json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"error": (proc.stderr or proc.stdout or "no output")[:300]}


def trace_run(tree: pathlib.Path, node: str, mode: str, out: pathlib.Path) -> dict:
    proc = confinement.run_repository_check(
        [sys.executable, str(TRACER), str(tree), node, mode, str(out)], tree, timeout=3600
    )
    if not out.is_file():
        return {"error": (proc.stderr or proc.stdout or "no trace artifact")[:300]}
    return json.loads(out.read_text(encoding="utf-8"))


def freeze_manifest(cases: list[dict], manifest_cases: dict) -> dict:
    """Everything decidable before a trace exists, including the thresholds."""
    body = {
        "label": BANNER,
        "question": (
            "For the 6 never-discovered BM-08 cases, would deterministic execution tracing "
            "during the frozen failing reproduction have exposed the historical fix file "
            "and/or the historical fix region?"
        ),
        "evaluator_only": True,
        "not_a_selector_change": "This audit decides only whether execution citation belongs in a future selector DAR.",
        "cases": [
            {
                "case_id": row["case_id"],
                "repository": row["repository"],
                "parent": manifest_cases[row["case_id"]]["parent"],
                "baseline_tree_hash": row["baseline_tree_hash"],
                "target_node": manifest_cases[row["case_id"]]["target_node"],
                "frozen_failure_identity": manifest_cases[row["case_id"]]["failure_identity"],
                "attribution_bucket": row["selector_miss_class"],
                "fix_files": row["code_fix_files"],
                # Evaluator-side region identity only; no upstream bytes.
                "fix_region_identity": {
                    path: sha_text(json.dumps(spans, sort_keys=True))
                    for path, spans in row["fix_regions"].items()
                    if path in row["code_fix_files"]
                },
                "fix_regions": {
                    path: spans for path, spans in row["fix_regions"].items() if path in row["code_fix_files"]
                },
            }
            for row in cases
        ],
        "trace_mechanism": "stdlib sys.settrace + threading.settrace, line events, repository-tree filtered",
        "trace_implementation_hash": hashlib.sha256(TRACER.read_bytes()).hexdigest(),
        "governed_observer_hash": hashlib.sha256(OBSERVER.read_bytes()).hexdigest(),
        "python_version": sys.version.split()[0],
        "execution_environment_hash": environment.environment_hash(),
        "execution_environment": environment.describe(),
        "repeats": REPEATS,
        "fresh_process_rule": "each observation runs in its own process; no coverage state is reused",
        "failure_identity_comparison_rule": (
            "the untraced reproduction must reproduce the frozen official failure identity "
            "(exception type); each traced repeat must reproduce the untraced identity exactly, "
            "or that observation is INVALID_OBSERVER_PERTURBATION and is not counted"
        ),
        "file_execution_rule": (
            "a fix file counts as executed only if the tracer recorded an executed line inside it "
            "during the failing reproduction; imported, discovered, on sys.path or statically "
            "present does not count"
        ),
        "region_execution_rule": (
            "a fix region counts as executed only if an executed line number falls inside the "
            "historical fix-touched span"
        ),
        "stability_classification_rule": {
            "STABLE_3_OF_3": "executed in all three valid observations",
            "STABLE_0_OF_3": "executed in none",
            "UNSTABLE_2_OF_3": "two of three; never promoted to stable",
            "UNSTABLE_1_OF_3": "one of three",
            "TRACE_INVALID": "fewer than three valid observations",
        },
        "decision_rule": {
            "EXECUTION_CITATION_STRONGLY_SUPPORTED": ">=5/6 fix files STABLE_3_OF_3 with identity preserved",
            "EXECUTION_CITATION_PARTIALLY_SUPPORTED": "3-4/6 fix files STABLE_3_OF_3",
            "EXECUTION_CITATION_NOT_SUPPORTED": "<=2/6 fix files STABLE_3_OF_3",
            "INCONCLUSIVE_TRACE_PERTURBATION": "perturbation or invalidity prevents reliable measurement",
        },
        "provider_calls": 0,
        "additional_spend_usd": 0.0,
    }
    body["execution_trace_audit_manifest_hash"] = hashlib.sha256(
        (json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")
    ).hexdigest()
    MANIFEST.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return body


def main() -> int:
    print(BANNER)
    print("=" * len(BANNER))

    rows = attribution_rows()
    six = sorted((r for r in rows if r["selector_miss_class"] in NEVER_DISCOVERED), key=lambda r: r["case_id"])
    if len(six) != 6:
        print(f"BLOCKED_ATTRIBUTION_IDENTITY: expected 6 never-discovered cases, found {len(six)}")
        return 2
    manifest_cases = {
        c["case_id"]: c for c in json.loads((BM08 / "manifest-executable-v5.json").read_text(encoding="utf-8"))["cases"]
    }
    print("the six never-discovered cases:")
    for row in six:
        print(f"  {row['case_id']:26} {row['selector_miss_class']}")

    if MANIFEST.is_file():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        print(f"\nmanifest already frozen: {manifest['execution_trace_audit_manifest_hash']}")
    else:
        manifest = freeze_manifest(six, manifest_cases)
        print(f"\nmanifest FROZEN before any trace: {manifest['execution_trace_audit_manifest_hash']}")

    shutil.rmtree(WORK, ignore_errors=True)
    results = []
    for entry in manifest["cases"]:
        case_id = entry["case_id"]
        case = manifest_cases[case_id]
        tree = WORK / case_id
        driver.materialise_baseline(case, repo_for(case["repository"]).parent, tree)
        observed_tree = tree_hash(tree)
        if observed_tree != entry["baseline_tree_hash"]:
            print(f"BLOCKED_BASELINE_IDENTITY: {case_id}")
            return 3

        node = entry["target_node"]
        # 1. the governed frozen observer, untraced, must reproduce the frozen identity
        governed = governed_identity(tree, node)
        frozen_type = (entry["frozen_failure_identity"] or {}).get("exception_type", "")
        governed_ok = governed.get("exception_type", "") == frozen_type and frozen_type != ""

        # 2. the untraced reference through the tracer harness, for like-for-like comparison
        untraced = trace_run(tree, node, "untraced", WORK / f"{case_id}-untraced.json")
        untraced_identity = identity_of(untraced)

        record = {
            "case_id": case_id,
            "repository": entry["repository"],
            "attribution_bucket": entry["attribution_bucket"],
            "baseline_tree_hash": observed_tree,
            "baseline_tree_hash_verified": True,
            "target_node": node,
            "frozen_failure_identity": entry["frozen_failure_identity"],
            "governed_untraced_identity": governed,
            "governed_matches_frozen": governed_ok,
            "untraced_identity": {"exception_type": untraced_identity[0], "message": untraced_identity[1]},
            "fix_files": entry["fix_files"],
            "fix_regions": entry["fix_regions"],
            "observations": [],
        }
        if not governed_ok:
            record["status"] = "BLOCKED_FAILURE_IDENTITY"
            print(f"  {case_id:26} BLOCKED_FAILURE_IDENTITY (governed {governed} vs frozen {frozen_type!r})")
            results.append(record)
            shutil.rmtree(tree, ignore_errors=True)
            continue

        for repeat in range(1, REPEATS + 1):
            artifact = WORK / f"{case_id}-traced-{repeat}.json"
            traced = trace_run(tree, node, "traced", artifact)
            traced_identity = identity_of(traced)
            invariant = traced_identity == untraced_identity and traced_identity[0] != ""
            executed = traced.get("executed") or {}
            observation = {
                "repeat": repeat,
                "baseline_tree_hash": observed_tree,
                "failure_identity_untraced": {
                    "exception_type": untraced_identity[0],
                    "message": untraced_identity[1],
                },
                "failure_identity_traced": {
                    "exception_type": traced_identity[0],
                    "message": traced_identity[1],
                },
                "identity_invariant": invariant,
                "valid": invariant,
                "trace_artifact_hash": sha_text(json.dumps(traced, sort_keys=True)),
                "executed_file_count": len(executed),
                "fix_file_executed": {},
                "fix_region_executed": {},
                "executed_line_receipt": {},
            }
            if not invariant:
                observation["invalid_reason"] = "INVALID_OBSERVER_PERTURBATION"
            else:
                for path in entry["fix_files"]:
                    lines = executed.get(path) or []
                    observation["fix_file_executed"][path] = bool(lines)
                    observation["fix_region_executed"][path] = region_hit(
                        executed, path, entry["fix_regions"].get(path, [])
                    )
                    if lines:
                        observation["executed_line_receipt"][path] = {
                            "executed_line_count": len(lines),
                            "min_line": min(lines),
                            "max_line": max(lines),
                            "executed_lines_hash": sha_text(json.dumps(lines, sort_keys=True)),
                        }
            record["observations"].append(observation)
            flag = (
                "invalid"
                if not invariant
                else (
                    "file+region"
                    if any(observation["fix_region_executed"].values())
                    else ("file" if any(observation["fix_file_executed"].values()) else "neither")
                )
            )
            print(f"  {case_id:26} repeat {repeat}  identity_invariant={invariant}  {flag}")

        valid = [o for o in record["observations"] if o["valid"]]
        record["valid_observations"] = len(valid)
        record["per_file"] = {}
        for path in entry["fix_files"]:
            record["per_file"][path] = {
                "file_status": classify(
                    [o["fix_file_executed"].get(path) if o["valid"] else None for o in record["observations"]]
                ),
                "region_status": classify(
                    [o["fix_region_executed"].get(path) if o["valid"] else None for o in record["observations"]]
                ),
            }
        file_statuses = [v["file_status"] for v in record["per_file"].values()]
        region_statuses = [v["region_status"] for v in record["per_file"].values()]
        record["all_fix_files_executed"] = bool(file_statuses) and all(s == STABLE_TRUE for s in file_statuses)
        record["any_fix_file_executed"] = STABLE_TRUE in file_statuses
        record["all_fix_regions_executed"] = bool(region_statuses) and all(s == STABLE_TRUE for s in region_statuses)
        record["any_fix_region_executed"] = STABLE_TRUE in region_statuses
        record["file_status"] = (
            STABLE_TRUE
            if record["any_fix_file_executed"]
            else classify(
                [any(o["fix_file_executed"].values()) if o["valid"] else None for o in record["observations"]]
            )
        )
        record["region_status"] = (
            STABLE_TRUE
            if record["any_fix_region_executed"]
            else classify(
                [any(o["fix_region_executed"].values()) if o["valid"] else None for o in record["observations"]]
            )
        )
        record["status"] = "TRACE_INVALID" if len(valid) < REPEATS else "MEASURED"
        results.append(record)
        shutil.rmtree(tree, ignore_errors=True)

    payload = {
        "label": BANNER,
        "execution_trace_audit_manifest_hash": manifest["execution_trace_audit_manifest_hash"],
        "trace_implementation_hash": manifest["trace_implementation_hash"],
        "provider_calls": 0,
        "additional_spend_usd": 0.0,
        "cases": results,
    }
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwritten: {OUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditBlocked as exc:
        print(str(exc))
        raise SystemExit(4) from exc
