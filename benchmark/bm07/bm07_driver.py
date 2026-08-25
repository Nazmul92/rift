"""BM-07 execution infrastructure. Benchmark-only; the RIFT runtime is frozen.

The BM-06 driver cannot execute this benchmark, and shrinking BM-07's manifest
back into the old shape to satisfy it would discard the things BM-07 was built
to carry — the complete preservation set, the constructed-baseline identity, and
a ground truth that does not consult RIFT.

Three properties this module exists to guarantee:

**One candidate, three independent verdicts.** The same canonical bytes are
handed to the weak protocol, to RIFT's frozen gate, and to the RIFT-free oracle.
Each gets its own freshly materialised baseline, so no verdict can observe
another's residue, and all three record the candidate hash they actually judged.

**Ground truth never calls RIFT.** BM-06 derived truth from the gate's own
verdict, which made `strong REJECT -> truth WRONG` true by construction. Here
truth lives in `oracle.py`, which imports no RIFT module at all, so
strong-versus-truth divergence stays observable in both directions.

**Failure identity is captured by the component that enforces it.** The
signature frozen into the manifest is produced by `riftagent.checks.run_check` —
the same call the gate makes — so a manifest signature and an execution
signature are the same kind of object compared by the same rule, rather than two
independently formatted strings that happen to look alike.

No provider call is made anywhere in this module. The dry run uses a fake
adapter and spends nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import bm07_oracle as oracle  # noqa: E402

from riftagent.checks import run_check  # noqa: E402
from riftagent.records import (  # noqa: E402
    Check,
    ClaimType,
    GatePhase,
    Outcome,
    RunnerKind,
    Signature,
    content_hash,
)
from riftagent.sandbox import IsolationProbe, Worktree, probe_isolation, tree_hash  # noqa: E402

PROTOCOL_VERSION = 1
BENCHMARK_ID = "BM-07"
TIMEOUT = 1800
ENV = {
    "GIT_AUTHOR_NAME": "bm07",
    "GIT_AUTHOR_EMAIL": "bm07@riftagent.invalid",
    "GIT_COMMITTER_NAME": "bm07",
    "GIT_COMMITTER_EMAIL": "bm07@riftagent.invalid",
}

ACCEPT, REJECT, ERROR = "accept", "reject", "error"
STRONG_UNRUNNABLE = "strong_unrunnable"
HARMFUL_PREVENTED = "harmful_weak_acceptance_prevented"
STRONG_FALSE_REJECTION = "strong_false_rejection"
BOTH_CORRECT_ACCEPT = "both_correct_accept"
SHARED_FALSE_ACCEPT = "shared_false_accept"
WEAK_REJECT_STRONG_ACCEPT = "weak_reject_strong_accept"
BOTH_REJECT = "both_reject"


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=TIMEOUT,
        env={**os.environ, **ENV, **(env or {})},
    )


# --------------------------------------------------------------- manifest


# Only `runner_hash` is format-checked here. The older identity fields predate
# this rule and their fixtures are frozen evidence; tightening them now would
# rewrite records rather than validate them.
_HEX64 = re.compile(r"[0-9a-f]{64}")

REQUIRED_TOP = (
    "benchmark_id",
    "protocol_version",
    "arms",
    "model",
    "pricing",
    "budget",
    "runtime_hash",
    "driver_hash",
    # The orchestration program decides when provider calls happen, how resume
    # works and when scoring is allowed. Leaving it out of the frozen identity
    # chain meant the code that spends the money could change without any
    # recorded identity changing.
    "runner_hash",
    "oracle_hash",
    "cases",
)
REQUIRED_CASE = (
    "case_id",
    "repository",
    "fix_commit",
    "parent",
    "target_node",
    "preservation_nodes",
    "baseline_tree_hash",
    "failure_identity",
    "protected_paths",
    "case_oracle",
)


def validate_manifest(manifest: dict) -> list[str]:
    """Every reason this manifest cannot be executed. Empty means executable.

    Fails closed on absence: a missing budget or an absent `baseline_tree_hash`
    is not a default to be filled in at run time, it is a manifest that was never
    finished. Preflight that only warns is preflight that gets ignored.
    """
    problems: list[str] = []
    for key in REQUIRED_TOP:
        if key not in manifest:
            problems.append(f"manifest: missing {key!r}")
    if manifest.get("benchmark_id") not in (None, BENCHMARK_ID):
        problems.append(f"manifest: benchmark_id must be {BENCHMARK_ID!r}")
    if manifest.get("protocol_version") not in (None, PROTOCOL_VERSION):
        problems.append(f"manifest: protocol_version must be {PROTOCOL_VERSION}")

    runner = manifest.get("runner_hash")
    if runner is not None and not (isinstance(runner, str) and len(runner) == 64 and _HEX64.fullmatch(runner)):
        problems.append("manifest: runner_hash must be 64 lowercase hex characters")

    arms = manifest.get("arms")
    if arms is not None and (not isinstance(arms, dict) or not {"A", "B", "C"} <= set(arms)):
        problems.append("manifest: arms must define A, B and C")

    model = manifest.get("model") or {}
    for key in ("requested_model_id", "required_reported_model_identity"):
        if not model.get(key):
            problems.append(f"manifest.model: missing {key!r}")

    pricing = manifest.get("pricing") or {}
    for key in ("input_per_mtok", "output_per_mtok"):
        if not isinstance(pricing.get(key), int | float):
            problems.append(f"manifest.pricing: missing or non-numeric {key!r}")

    budget = manifest.get("budget") or {}
    if not isinstance(budget.get("total_usd_ceiling"), int | float):
        problems.append("manifest.budget: missing or non-numeric 'total_usd_ceiling'")
    if not budget.get("reservation_rule"):
        problems.append("manifest.budget: missing 'reservation_rule'")
    # The runner derives its reservation from these; without them a caller would
    # have to supply one, and a supplied reservation can be zero.
    for key in ("per_case_arm_max_usd", "max_input_tokens", "max_output_tokens"):
        if not isinstance(budget.get(key), int | float):
            problems.append(f"manifest.budget: missing or non-numeric {key!r}")

    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        problems.append("manifest: 'cases' must be a non-empty list")
        return problems

    seen: set[str] = set()
    for i, case in enumerate(cases):
        label = case.get("case_id") or f"cases[{i}]"
        for key in REQUIRED_CASE:
            if key not in case:
                problems.append(f"{label}: missing {key!r}")
        if case.get("case_id") in seen:
            problems.append(f"{label}: duplicate case_id")
        seen.add(case.get("case_id"))

        nodes = case.get("preservation_nodes")
        if not isinstance(nodes, list) or not nodes:
            problems.append(f"{label}: preservation_nodes must be a non-empty list")
        elif case.get("preservation_count") not in (None, len(nodes)):
            # A count that disagrees with the list is the truncation defect
            # coming back; refuse rather than trust either number.
            problems.append(f"{label}: preservation_count {case['preservation_count']} != {len(nodes)} nodes listed")
        sig = case.get("failure_identity")
        if not isinstance(sig, dict) or "exception_type" not in sig:
            problems.append(f"{label}: failure_identity must be a captured signature")
        if not isinstance(case.get("baseline_tree_hash"), str) or not case.get("baseline_tree_hash"):
            problems.append(f"{label}: baseline_tree_hash must be a non-empty string")
        seed = case.get("probe_seed")
        if not isinstance(seed, int) or isinstance(seed, bool):
            problems.append(f"{label}: probe_seed must be a frozen integer")
        protected = case.get("protected_paths")
        if not isinstance(protected, list) or not protected:
            problems.append(f"{label}: protected_paths must be a non-empty list")
    return problems


def manifest_hash(manifest: dict) -> str:
    body = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    return hashlib.sha256((json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()


def observed_runtime_hash() -> str:
    """The governed runtime's identity, recomputed from the tree it will run."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("bm06_driver", Path(__file__).parents[1] / "bm06" / "driver.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.runtime_hash(Path(__file__).parents[2])[0]


def oracle_hash() -> str:
    """The identity of the program that defines ground truth."""
    return oracle.oracle_hash()


def driver_hash() -> str:
    """This file's own bytes. Frozen into the manifest after the dry run."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


# ------------------------------------------------------- baseline identity


def materialise_baseline(case: dict, repos: Path, dest: Path) -> Path:
    """Exact parent plus the frozen reproducer — the task, not just a checkout.

    The benchmark baseline is the parent commit *with the fix commit's test half
    applied*, because that is what makes the target fail. Reconstructing only the
    parent would produce a tree whose target passes and whose identity does not
    match the manifest.
    """
    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    repo = repos / case["repository"]
    if run(["git", "clone", "-q", "--shared", str(repo), str(dest)], dest.parent).returncode != 0:
        raise RuntimeError(f"{case['case_id']}: clone failed")
    if run(["git", "checkout", "-q", "--force", case["parent"]], dest).returncode != 0:
        raise RuntimeError(f"{case['case_id']}: cannot check out parent")

    diff = run(["git", "show", "--format=", case["fix_commit"], "--", *case["test_files"]], repo).stdout
    if not diff.strip():
        raise RuntimeError(f"{case['case_id']}: the fix commit changes no test file")
    patch = dest.parent / f"{dest.name}.tests.diff"
    patch.write_text(diff, encoding="utf-8", newline="")
    applied = run(["git", "apply", "--whitespace=nowarn", str(patch)], dest)
    patch.unlink(missing_ok=True)
    if applied.returncode != 0:
        raise RuntimeError(f"{case['case_id']}: reproducer did not apply: {applied.stderr.strip()[:120]}")
    run(["git", "add", "-A"], dest)
    run(["git", "commit", "-q", "-m", "bm07 frozen reproducer"], dest)
    return dest


def observe_failure_identity(tree: Path, node: str, probe: IsolationProbe) -> Signature | None:
    """Capture the target's failure through the component that enforces it.

    `checks.run_check` is what the gate calls, so the signature frozen here and
    the signature compared at execution are the same kind of object produced by
    the same code. Writing a pytest `E` line into the manifest and comparing it
    against a structured signature later would be two vocabularies pretending to
    be one.
    """
    check = Check(
        check_id="bm07-target",
        claim_type=ClaimType.CHANGE,
        runner=RunnerKind.PYTEST,
        node_id=node,
        expected_baseline=Outcome.FAILED,
        expected_candidate=Outcome.PASSED,
        timeout_s=600.0,
        scope="bm07",
    )
    with Worktree(tree, "bm07-observe") as wt:
        result, _ = run_check(check, wt, GatePhase.BASELINE, probe)
    return result.signature


# ------------------------------------------------------------- evaluators


@dataclass
class Verdicts:
    case_id: str
    candidate_hash: str
    weak_verdict: str = ""
    weak_target_result: str = ""
    weak_candidate_hash: str = ""
    strong_verdict: str = ""
    strong_gate_receipt: dict = field(default_factory=dict)
    strong_candidate_hash: str = ""
    ground_truth: dict = field(default_factory=dict)
    truth_candidate_hash: str = ""
    classification: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "candidate_hash": self.candidate_hash,
            "weak_verdict": self.weak_verdict,
            "weak_target_result": self.weak_target_result,
            "weak_candidate_hash": self.weak_candidate_hash,
            "strong_verdict": self.strong_verdict,
            "strong_gate_receipt": self.strong_gate_receipt,
            "strong_candidate_hash": self.strong_candidate_hash,
            **self.ground_truth,
            "truth_candidate_hash": self.truth_candidate_hash,
            "classification": self.classification,
        }


def evaluate_weak(tree: Path, candidate: str, case: dict) -> tuple[str, str]:
    """Target-pass acceptance, and nothing else. The incumbent practice."""
    patch = tree.parent / f"{tree.name}.weak.diff"
    patch.write_text(candidate, encoding="utf-8", newline="")
    applied = run(["git", "apply", "--whitespace=nowarn", str(patch)], tree)
    patch.unlink(missing_ok=True)
    if applied.returncode != 0:
        return REJECT, "not_applied"
    outcome = oracle.run_node(tree, case["target_node"], case.get("src_layout", "flat"))
    return (ACCEPT if outcome == "pass" else REJECT), outcome


def evaluate_strong(tree: Path, candidate: str, case: dict, patch_path: Path) -> tuple[str, dict]:
    """RIFT's frozen five-phase gate, invoked exactly as `rift verify` does.

    Nothing about the gate is configured differently for the benchmark; the
    complete frozen preservation set is passed through `--preserve`, so strong
    and truth are asked about the same behaviour.
    """
    patch_path.write_text(candidate, encoding="utf-8", newline="")
    layout = case.get("src_layout", "flat")
    env = {k: v for k, v in os.environ.items() if not k.startswith("RIFT_LLM_")}
    # riftagent itself must always be importable by the child, and a src-layout
    # repository additionally needs its own package root. Setting this only for
    # non-flat layouts left `python -m riftagent` unable to start on flat cases,
    # which produced no receipt at all — and a missing receipt is not a verdict.
    entries = [str(Path(__file__).parents[2] / "src")]
    if layout != "flat":
        entries.insert(0, str((tree / layout).resolve()))
    env["PYTHONPATH"] = os.pathsep.join(entries)
    argv = [
        sys.executable,
        "-m",
        "riftagent",
        "--repo",
        str(tree),
        "--json",
        "verify",
        str(patch_path),
        case["target_node"],
        "--allow-partial-sandbox",
    ]
    for node in case["preservation_nodes"]:
        argv += ["--preserve", node]
    proc = run(argv, tree, env)
    receipt: dict = {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict) and "verdict" in parsed:
            receipt = parsed
            break
    verdict = receipt.get("verdict")
    detail = {
        "verdict": verdict or "<no receipt>",
        "exit_code": proc.returncode,
        "seconds": receipt.get("seconds"),
        "stderr_tail": (proc.stderr or "")[-300:],
    }
    if verdict is None:
        # The gate did not run. Recording that as a rejection would invent a
        # false-rejection rate out of a harness failure, which is precisely the
        # kind of fabricated result BM-07 exists to avoid.
        return ERROR, detail
    return (ACCEPT if verdict == "verified_against_approved_checks" else REJECT), detail


def classify(weak: str, strong: str, truth: str) -> str:
    """The four-cell matrix, kept as distinct outcomes rather than one boolean.

    A strong evaluation that could not run is its own outcome. Folding it into
    `reject` would report harness breakage as a verification result.
    """
    if strong == ERROR:
        return STRONG_UNRUNNABLE
    correct = truth == oracle.CORRECT
    if weak == ACCEPT and strong == REJECT:
        return STRONG_FALSE_REJECTION if correct else HARMFUL_PREVENTED
    if weak == ACCEPT and strong == ACCEPT:
        return BOTH_CORRECT_ACCEPT if correct else SHARED_FALSE_ACCEPT
    if weak == REJECT and strong == ACCEPT:
        return WEAK_REJECT_STRONG_ACCEPT
    return BOTH_REJECT


def evaluate_candidate(case: dict, candidate: str, repos: Path, work: Path) -> Verdicts:
    """One canonical candidate, three fresh baselines, three independent verdicts.

    The candidate is hashed once, before any evaluation, and each verdict records
    the hash of the bytes it was actually given. A caller that canonicalised
    separately per evaluator — or reused a mutated tree — shows up here as a
    mismatch rather than as a quietly different experiment.
    """
    digest = content_hash(candidate.encode("utf-8"))
    out = Verdicts(case_id=case["case_id"], candidate_hash=digest)
    baseline = case["baseline_tree_hash"]

    for role in ("weak", "strong", "truth"):
        tree = work / f"{case['case_id']}-{role}"
        materialise_baseline(case, repos, tree)
        observed = tree_hash(tree)
        if observed != baseline:
            raise RuntimeError(
                f"{case['case_id']}: {role} baseline identity {observed[:12]} != manifest {baseline[:12]}"
            )
        if role == "weak":
            out.weak_verdict, out.weak_target_result = evaluate_weak(tree, candidate, case)
            out.weak_candidate_hash = digest
        elif role == "strong":
            out.strong_verdict, out.strong_gate_receipt = evaluate_strong(
                tree, candidate, case, work / f"{case['case_id']}.candidate.diff"
            )
            out.strong_candidate_hash = digest
        else:
            out.ground_truth = oracle.evaluate(tree, candidate, case).to_dict()
            out.truth_candidate_hash = digest
        shutil.rmtree(tree, ignore_errors=True)

    out.classification = classify(out.weak_verdict, out.strong_verdict, out.ground_truth["ground_truth_verdict"])
    return out


# ---------------------------------------------------------------- preflight


def preflight(manifest: dict, repos: Path, work: Path) -> list[str]:
    """Reconstruct every case and refuse to spend if anything has moved."""
    problems = validate_manifest(manifest)
    if problems:
        return problems

    probe = probe_isolation()
    for case in manifest["cases"]:
        cid = case["case_id"]
        tree = work / f"preflight-{cid}"
        try:
            materialise_baseline(case, repos, tree)
        except RuntimeError as exc:
            problems.append(str(exc))
            continue
        observed = tree_hash(tree)
        if observed != case["baseline_tree_hash"]:
            problems.append(f"{cid}: baseline_tree_hash {observed[:12]} != manifest {case['baseline_tree_hash'][:12]}")
        layout = case.get("src_layout", "flat")
        if oracle.run_node(tree, case["target_node"], layout) != "fail":
            problems.append(f"{cid}: target does not fail on the reconstructed baseline")
        else:
            frozen = Signature.from_dict(case["failure_identity"])
            if not frozen.matches(observe_failure_identity(tree, case["target_node"], probe)):
                problems.append(f"{cid}: observed failure identity does not match the frozen signature")
        ok, failures, _ = oracle.run_all(tree, list(case["preservation_nodes"]), layout)
        if not ok:
            problems.append(f"{cid}: {len(failures)} preservation nodes fail on the reconstructed baseline")
        shutil.rmtree(tree, ignore_errors=True)
    return problems


def budget_preflight(
    manifest: dict,
    *,
    configured_model: str,
    spent_usd: float,
    reserve_usd: float,
    observed_runtime_hash: str,
    observed_driver_hash: str,
) -> list[str]:
    """Everything that must hold before a single provider call is made."""
    problems = []
    if manifest["model"]["requested_model_id"] != configured_model:
        problems.append(
            f"model identity: manifest {manifest['model']['requested_model_id']!r} != configured {configured_model!r}"
        )
    remaining = manifest["budget"]["total_usd_ceiling"] - spent_usd
    if remaining < reserve_usd:
        problems.append(f"budget: remaining ${remaining:.4f} < reservation ${reserve_usd:.4f}")
    if manifest["runtime_hash"] != observed_runtime_hash:
        problems.append("runtime identity: manifest runtime_hash != observed")
    if manifest["driver_hash"] != observed_driver_hash:
        problems.append("driver identity: manifest driver_hash != observed")
    if manifest.get("manifest_hash") and manifest["manifest_hash"] != manifest_hash(manifest):
        problems.append("manifest identity: recorded manifest_hash != recomputed")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["validate", "preflight", "driver-hash"])
    ap.add_argument("--manifest", default=str(Path(__file__).parent / "manifest-executable.json"))
    ap.add_argument("--repos", default="/repos")
    ap.add_argument("--work", default="/tmp/bm07-work")
    args = ap.parse_args()

    if args.command == "driver-hash":
        print(driver_hash())
        return 0

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    problems = (
        validate_manifest(manifest)
        if args.command == "validate"
        else preflight(manifest, Path(args.repos), Path(args.work))
    )
    for p in problems:
        print(f"  FAIL  {p}")
    print(f"{args.command}: {len(problems)} failures")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
