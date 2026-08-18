"""BM-06 driver — runs arms A, B and C against a validated manifest.

Benchmark infrastructure, not a runtime module. It reuses the shipped runtime:
arms are `rift fix` invocations, shadow evaluation and ground truth are `rift
verify`, and no verdict is decided here.

## What was wrong before, and what changed

The first version of this file had tests and no experiment. Review found eleven
defects in the consuming path; the ones that mattered:

* all three arms invoked the identical `rift fix` command, so there were no arms;
* arm B's random draw was recorded but never selected anything, and its seed came
  from `hash()`, which is not stable across processes;
* arm A's patch was never captured, so shadow evaluation always received `None`
  and evaluated nothing;
* acceptance was inferred from a process return code;
* ground-truth correctness was never computed on a live run;
* spend was copied into result rows instead of referenced in the ledger.

The tests passed throughout, because they exercised `report()` arithmetic and a
dry run that substitutes the CLI. Helper tests over an experiment that does not
run are not evidence that it runs.

## Fail-closed

`validate_manifest` runs before anything else and **no provider request is made
if it returns a failure**. An arm whose orchestration the shipped CLI cannot
express is refused by name, never silently replaced by another arm's command —
three identical runs reported as three arms is the failure this file exists to
avoid repeating.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ARMS = ("A", "B", "C")

# An arm is expressible only if the shipped CLI offers the flag that makes it
# differ. Probed from `--help` rather than assumed, so this file starts working
# the day the flag exists and refuses honestly until then.
ARM_REQUIRES = {
    "A": "--model-alone",  # propose without kernel diagnosis; accept on target pass
    "B": "--probe-policy",  # random selection from the identical probe pool
    "C": "",  # the shipped default path
}


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rift(args: list[str], cwd: Path, timeout: float = 3600.0) -> subprocess.CompletedProcess:
    """One invocation of the shipped CLI. The benchmark never reaches for a
    private helper to get a code path a user could not take."""
    return subprocess.run(
        [sys.executable, "-m", "riftagent", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )


def cli_supports(flag: str, cwd: Path) -> bool:
    if not flag:
        return True
    help_text = rift(["fix", "--help"], cwd=cwd, timeout=120)
    return flag in (help_text.stdout + help_text.stderr)


def receipt_of(proc: subprocess.CompletedProcess | None) -> dict:
    """The last JSON object the CLI printed, or an empty dict."""
    if proc is None or not proc.stdout.strip():
        return {}
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


# ------------------------------------------------------------------ validation


def validate_manifest(manifest: dict, work: Path) -> list[str]:
    """Everything that must hold before a single request is made.

    Fail-closed and specific: each failure names the case and the missing
    property, because a validation message that only says "invalid" invites the
    reader to fix it by deleting the check.
    """
    failures: list[str] = []
    if not manifest.get("arms"):
        failures.append("manifest.arms is empty; the arms are not defined")
    if not manifest.get("budget"):
        failures.append("manifest.budget is empty; there is no authorized ceiling")
    else:
        budget = manifest["budget"]
        if not budget.get("scope"):
            failures.append("manifest.budget.scope is missing; spend cannot be attributed")
        if budget.get("max_usd") in (None, 0, 0.0):
            failures.append("manifest.budget.max_usd is unset or zero")
    if not manifest.get("model", {}).get("id"):
        failures.append("manifest.model.id is missing; the arms would not share a model")
    if not manifest.get("cases"):
        failures.append("manifest.cases is empty")

    # Each arm must be defined, not merely present, and arm B's seed must be
    # frozen in the manifest: an unrecorded seed makes a rerun of B a different
    # experiment rather than a repetition of the same one.
    arms = manifest.get("arms") or {}
    for arm in ARMS:
        if arm not in arms:
            failures.append(f"manifest.arms.{arm} is not defined")
    if "B" in arms and arms["B"].get("seed") in (None, ""):
        failures.append("manifest.arms.B.seed is missing; arm B would not be reproducible")
    # Shared across arms by definition: if these differ or are absent, the arms
    # are not comparable and no amount of running them fixes it.
    model = manifest.get("model") or {}
    for field in ("price_input_per_mtok", "price_output_per_mtok", "max_output_tokens", "max_probes", "max_attempts"):
        if model.get(field) is None:
            failures.append(f"manifest.model.{field} is missing; the arms would not share a budget")

    for case in manifest.get("cases", []):
        cid = case.get("case_id", "<unnamed>")
        if case.get("status") == "GROUND_TRUTH_DISPUTED":
            failures.append(f"{cid}: GROUND_TRUTH_DISPUTED cases may not enter the scored set")
        if not case.get("target"):
            failures.append(f"{cid}: no target")
        if not case.get("signature"):
            failures.append(f"{cid}: no expected signature to match the reproduction against")
        if not case.get("preserve"):
            failures.append(f"{cid}: preservation checks are empty; nothing would constrain a destructive patch")
        if case.get("ordering_precondition") and not case.get("reproducer"):
            failures.append(f"{cid}: order-dependent case carries no exact reproducer; the bare node id will not fail")
        worktree = case.get("worktree")
        if not worktree:
            failures.append(f"{cid}: no worktree; the manifest names a repository but no materialized checkout")
        elif not (work / worktree).is_dir() and not Path(worktree).is_dir():
            failures.append(f"{cid}: worktree {worktree!r} does not exist; materialize it before running")
    return failures


# ------------------------------------------------------------------ arms


def probe_seed(manifest: dict, case: dict) -> int:
    """Arm B's frozen seed, stable across processes.

    Derived by SHA-256 from the manifest seed and the case id. The previous
    version used `hash(case_id)`, which Python randomises per process — a rerun
    of B would have been a different experiment, which is the one thing the
    frozen seed exists to prevent.
    """
    material = f"{manifest['arms']['B']['seed']}:{case['case_id']}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def arm_argv(arm: str, case: dict, manifest: dict, scope: str) -> list[str]:
    """The command for one arm. Differs per arm by construction."""
    model = manifest["model"]
    argv = [
        "--repo",
        case["worktree"],
        "--json",
        "fix",
        case["target"],
        "--allow-partial-sandbox",
        "--max-usd",
        str(manifest["budget"]["max_usd"]),
        "--scope",
        scope,
        "--price-input",
        str(model["price_input_per_mtok"]),
        "--price-output",
        str(model["price_output_per_mtok"]),
        "--max-output-tokens",
        str(model["max_output_tokens"]),
        "--max-probes",
        str(model["max_probes"]),
        "--max-attempts",
        str(model["max_attempts"]),
        "--max-commands",
        str(model["max_commands"]),
        "--timeout",
        str(model["timeout_s"]),
        *[a for node in case.get("preserve", []) for a in ("--preserve", node)],
    ]
    if arm == "A":
        # The incumbent: same model, same context budget, no kernel diagnosis,
        # accepted on the target passing. It still receives the *same frozen
        # task* as B and C — without the reproducer its baseline runs bare, an
        # order-dependent target passes there, and arm A reports nothing to
        # repair while B and C work the real failure.
        argv.append("--model-alone")
        argv += [a for node in (case.get("reproducer") or ()) for a in ("--precondition", node)]
        if case.get("reproducer") and case.get("signature"):
            argv += ["--expect-signature", case["signature"]]
    elif arm == "B":
        # Identical probe pool and budgets; only the selection policy differs.
        argv += ["--probe-policy", "random", "--probe-seed", str(probe_seed(manifest, case))]
    return argv


def orchestration_key(arm: str, case: dict, manifest: dict, scope: str) -> str:
    """A fingerprint of what an arm actually executes.

    Two arms with the same key are the same experiment whatever they are called.
    Tests assert these differ; that assertion is the one that would have caught
    three arms running one command.
    """
    return " ".join(arm_argv(arm, case, manifest, scope))


# ------------------------------------------------------------------ evidence


def task_dir(repo: Path, receipt: dict) -> Path | None:
    task_id = receipt.get("task_id") or receipt.get("task")
    if not task_id:
        return None
    candidate = repo / ".rift" / "tasks" / str(task_id)
    return candidate if candidate.is_dir() else None


def capture_patch(repo: Path, receipt: dict, out_dir: Path, arm: str, case_id: str) -> Path | None:
    """The exact durable patch bytes an arm produced, copied out verbatim.

    `change-set.diff` is what the runtime wrote when the patch was accepted. It
    is copied rather than regenerated: a regenerated patch is a different
    artifact, and shadow evaluation is only meaningful on the same bytes.
    """
    td = task_dir(repo, receipt)
    if td is None:
        return None
    source = td / "change-set.diff"
    if not source.is_file() or not source.read_bytes().strip():
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"{case_id}-{arm}.diff"
    destination.write_bytes(source.read_bytes())
    return destination


def spend_event_ids(repo: Path, scope: str, since: int) -> dict:
    """A reference into `.rift/spend.jsonl`, not a copy of a number.

    The ledger is authoritative. Recording ids and letting the report read the
    ledger means a figure in a report can always be traced to the events that
    produced it, and a hand-maintained total can never drift from them.
    """
    path = repo / ".rift" / "spend.jsonl"
    if not path.is_file():
        return {"ledger": str(path), "event_ids": [], "present": False}
    ids: list[int] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if index < since or not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("scope") in (scope, None):
            ids.append(index)
    return {"ledger": str(path), "event_ids": ids, "present": True}


def spend_from_ledger(reference: dict) -> float | None:
    """Sum the referenced events. Returns None when the ledger is unreadable —
    never 0.0, which would report an unmeasured run as a free one."""
    path = Path(reference.get("ledger", ""))
    if not reference.get("present") or not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    total = 0.0
    for index in reference.get("event_ids", []):
        if index >= len(lines):
            continue
        try:
            row = json.loads(lines[index])
        except json.JSONDecodeError:
            continue
        total += float(row.get("charged_usd") or 0.0)
    return total


def evaluate_under_gate(case: dict, patch: Path | None, work: Path) -> dict:
    """Score a patch through C's gate with `rift verify` — the same gate a user
    gets. Used for both shadow evaluation and ground truth.

    The case's **exact frozen reproducer** is passed, never the bare target. An
    order-dependent failure passes when run alone, so a bare-target evaluation
    would report a passing baseline and score every such case as already fixed.
    That defect has surfaced four times; there is no fallback here, by design.
    """
    if patch is None:
        return {"evaluated": False, "reason": "no patch was produced"}
    cwd = work / case["worktree"] if (work / case["worktree"]).is_dir() else Path(case["worktree"])
    reproducer = list(case.get("reproducer") or ())
    if reproducer and not cli_supports("--precondition", cwd):
        # Refused by name. Evaluating without the preconditions would silently
        # measure a different experiment.
        return {"evaluated": False, "reason": "NOT_RUN_REPRODUCER_UNSUPPORTED"}
    proc = rift(
        [
            "--repo",
            str(cwd),
            "--json",
            "verify",
            str(patch),
            case["target"],
            "--allow-partial-sandbox",
            *[a for node in reproducer for a in ("--precondition", node)],
            *(["--expect-signature", case["signature"]] if reproducer and case.get("signature") else []),
            *[a for node in case.get("preserve", []) for a in ("--preserve", node)],
        ],
        cwd=cwd,
    )
    verdict = receipt_of(proc).get("verdict")
    return {"evaluated": True, "verdict": verdict, "exit_code": proc.returncode}


def record(case: dict, arm: str, proc: subprocess.CompletedProcess | None, extra: dict) -> dict:
    receipt = receipt_of(proc)
    diagnosis = receipt.get("diagnosis") if isinstance(receipt.get("diagnosis"), dict) else {}
    return {
        "case_id": case["case_id"],
        "arm": arm,
        "label": case["label"],
        "cause_class": case["cause_class"],
        "status": case.get("status", "OK"),
        "expected_diagnostic_scope": case.get("expected_diagnostic_scope"),
        "verdict": receipt.get("verdict"),
        "repair_basis": receipt.get("repair_basis"),
        # The phase a lost case was lost at. This is the measurement that decides
        # whether a bounded repair loop is justified by data rather than by
        # intuition, so it is recorded even when the case succeeded.
        "failed_phase": receipt.get("rejected_phase"),
        "support": diagnosis.get("support"),
        "gate": diagnosis.get("gate"),
        "commands": receipt.get("commands"),
        "seconds": receipt.get("seconds"),
        "tokens": receipt.get("tokens"),
        "exit_code": None if proc is None else proc.returncode,
        **extra,
    }


# ------------------------------------------------------------------ report


def report(records: list[dict], manifest: dict) -> dict:
    """Every figure recomputed from the raw records. No stored aggregate."""
    scored = [r for r in records if r["status"] == "OK"]
    excluded = [r for r in records if r["status"] != "OK"]
    out: dict = {
        "per_arm": {},
        "excluded": [{"case_id": r["case_id"], "status": r["status"]} for r in excluded],
        "arms_refused": sorted({r["arm"] for r in records if r.get("arm_unavailable")}),
    }

    for arm in ARMS:
        rows = [r for r in scored if r["arm"] == arm]
        gateable = [r for r in rows if r["label"] == "gateable"]
        accepted = [r for r in rows if r.get("accepted")]
        correct = [r for r in accepted if r.get("ground_truth_correct")]
        observational = [r for r in rows if r["label"] == "observationally_diagnosable"]
        diagnosed = [r for r in observational if r.get("gate") == "not_applicable" and r.get("support")]

        charges = [spend_from_ledger(r.get("spend_reference") or {}) for r in rows]
        measured = [c for c in charges if c is not None]
        charged = sum(measured) if measured else None
        out["per_arm"][arm] = {
            "attempted": len(rows),
            # Abstentions are attempted tasks and stay in the denominator.
            "gateable_attempted": len(gateable),
            "accepted": len(accepted),
            "correct": len(correct),
            "false_fix_acceptance": (len(accepted) - len(correct)) / len(accepted) if accepted else None,
            "verified_fix_yield": len(correct) / len(gateable) if gateable else None,
            "observational_diagnosis_yield": len(diagnosed) / len(observational) if observational else None,
            # Zero correct outcomes is undefined, never zero.
            "cost_per_correct_fix": (charged / len(correct)) if correct and charged is not None else None,
            "cost_per_correct_fix_note": None if (correct and charged is not None) else "undefined",
            "charged_usd": None if charged is None else round(charged, 8),
            "charged_usd_note": None if charged is not None else "not measured: spend ledger unreadable",
            "spend_source": ".rift/spend.jsonl, by referenced event id",
            "failed_phases": sorted({r["failed_phase"] for r in rows if r.get("failed_phase")}),
        }

    shadows = [r for r in scored if r.get("arm") == "A" and (r.get("shadow") or {}).get("evaluated")]
    out["shadow_evaluation"] = {
        "arm_a_patches_evaluated": len(shadows),
        "accepted_by_c_gate": sum(
            1 for r in shadows if (r.get("shadow") or {}).get("verdict") == "verified_against_approved_checks"
        ),
        "note": (
            "Acceptance authority in isolation: arm A's own accepted patch bytes, re-scored under "
            "C's gate without re-proposing. A-versus-C remains the complete product effect."
        ),
    }
    return out


# ------------------------------------------------------------------ main


def main() -> int:
    parser = argparse.ArgumentParser(prog="bm06-driver")
    parser.add_argument("--manifest", default="benchmark/bm06/manifest.json")
    parser.add_argument("--out", default="benchmark/bm06/results.json")
    parser.add_argument("--work", default=".", help="root the manifest's worktree paths resolve against")
    parser.add_argument("--patches", default="benchmark/bm06/patches")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    manifest = load(Path(args.manifest))
    work = Path(args.work)

    if args.report_only:
        print(json.dumps(report(load(Path(args.out))["records"], manifest), indent=1, sort_keys=True))
        return 0

    failures = validate_manifest(manifest, work)
    if failures:
        print("MANIFEST INVALID — no provider request was made:")
        for failure in failures:
            print(f"  {failure}")
        return 2
    if args.validate_only:
        print("manifest valid")
        return 0

    scope = manifest["budget"]["scope"]
    available = {arm: cli_supports(ARM_REQUIRES[arm], work) for arm in ARMS}
    for arm, ok in available.items():
        if not ok:
            print(f"arm {arm}: NOT_RUN_ARM_UNSUPPORTED — the shipped CLI has no {ARM_REQUIRES[arm]}", flush=True)

    records: list[dict] = []
    for case in manifest["cases"]:
        repo = work / case["worktree"] if (work / case["worktree"]).is_dir() else Path(case["worktree"])
        patches: dict[str, Path | None] = {}
        for arm in ARMS:
            if not available[arm]:
                # Refused by name. Never substituted with another arm's command.
                records.append(record(case, arm, None, {"arm_unavailable": ARM_REQUIRES[arm], "accepted": False}))
                continue
            spend_path = repo / ".rift" / "spend.jsonl"
            before = len(spend_path.read_text(encoding="utf-8").splitlines()) if spend_path.is_file() else 0
            proc = rift(arm_argv(arm, case, manifest, scope), cwd=repo)
            receipt = receipt_of(proc)
            patches[arm] = capture_patch(repo, receipt, Path(args.patches), arm, case["case_id"])
            extra: dict = {
                # Acceptance is the arm's own verdict, read from the receipt.
                # A process exit code says whether the program ran, not whether
                # a patch was accepted.
                "accepted": receipt.get("verdict") in ("verified_against_approved_checks", "accepted_by_target_pass"),
                "patch": None if patches[arm] is None else str(patches[arm]),
                "spend_reference": spend_event_ids(repo, scope, before),
                "orchestration": orchestration_key(arm, case, manifest, scope),
            }
            # Ground truth is an independent evaluation of the arm's patch, never
            # the arm's own opinion of itself and never a return code.
            truth = evaluate_under_gate(case, patches[arm], work)
            extra["ground_truth_evaluation"] = truth
            extra["ground_truth_correct"] = truth.get("verdict") == "verified_against_approved_checks"
            if arm == "A":
                extra["shadow"] = truth
            records.append(record(case, arm, proc, extra))

    payload = {"manifest_hash": manifest.get("manifest_hash"), "records": records}
    Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report(records, manifest), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
