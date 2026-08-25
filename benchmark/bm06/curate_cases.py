"""Curation: turn reviewed cases into runnable ones, or reject them.

Model-free. Makes no provider request and spends nothing.

A label-reviewed case is not yet a task. It names a repository, a parent and a
fix commit, and a target — but nothing has been materialized at a pinned commit,
no preservation check exists, and for an ordering case no exact precondition
sequence has been established. The driver's validator refuses all fifteen for
exactly those reasons.

This pass supplies what is missing by executing it, and rejects whatever cannot
be supplied:

1. materialize a worktree at the pinned parent commit;
2. reproduce the original failure there and freeze the signature actually
   observed;
3. confirm the project's own fix makes the target pass;
4. find preservation checks that pass on **both** sides — a patch that breaks
   them is a regression the gate must catch;
5. for an ordering case, establish an exact precondition sequence;
6. reject any case missing any of the above, with the reason recorded.

Nothing here is manufactured. A preservation check is a test the repository
already has, and a signature is what the failure actually printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import confirm_cases as cc  # noqa: E402 - the execution primitives are shared

# Enough to constrain a destructive patch, few enough to keep a case cheap. A
# case with none is rejected: nothing would stop a patch that deletes the
# behaviour around the target.
MAX_PRESERVE = 3
# Bounded search for an ordering precondition. A case needing more than this is
# rejected rather than approximated.
MAX_ORDER_CANDIDATES = 25


def detect_layout(worktree: Path) -> str:
    """Where this project's importable package lives, read from the tree.

    The previous run used a hand-maintained `src-layouts.json` that covered only
    the original ten repositories, so five `src/` projects were treated as flat.
    `PYTHONPATH` then pointed at the worktree root, imports resolved to the
    *installed* package instead of the pinned commit, and the results said
    nothing about the worktree. Detecting it removes the whole class of error:
    a list can be incomplete, a directory cannot.
    """
    src = worktree / "src"
    if src.is_dir() and any(child.is_dir() and (child / "__init__.py").is_file() for child in src.iterdir()):
        return "src"
    return ""


def worktree_for(repo: Path, commit: str, dest: Path) -> str:
    """A worktree pinned at one commit, materialized where the manifest says.

    `git worktree` rather than a checkout of the shared clone: cases must not
    move each other's HEAD, and the driver runs them against a path that has to
    stay at the pinned commit for the whole run.
    """
    if dest.exists():
        cc.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(dest)], timeout=300)
    proc = cc.run(["git", "-C", str(repo), "worktree", "add", "--detach", "--force", str(dest), commit], timeout=600)
    return "" if proc.returncode == 0 else (proc.stderr.strip()[-200:] or "worktree add failed")


def outcome_in(worktree: Path, node: str, src: str, python: str) -> tuple[str, str]:
    original = cc.python_for
    cc.python_for = lambda _repo: python  # type: ignore[assignment]
    try:
        return cc.outcome(worktree, node, src)
    finally:
        cc.python_for = original  # type: ignore[assignment]


def nodes_in_file(worktree: Path, rel: str, src: str, python: str) -> list[str]:
    original = cc.python_for
    cc.python_for = lambda _repo: python  # type: ignore[assignment]
    try:
        return cc.node_ids(worktree, [rel], src)
    finally:
        cc.python_for = original  # type: ignore[assignment]


def find_preservation(parent_wt: Path, fix_wt: Path, target: str, src: str, python: str) -> tuple[list[str], str]:
    """Tests that pass on both sides, drawn from the target's own file.

    Same file as the target on purpose: a preservation check three modules away
    constrains almost nothing, while a sibling test in the file being patched is
    what a careless repair actually breaks.
    """
    rel = target.split("::")[0]
    siblings = [n for n in nodes_in_file(fix_wt, rel, src, python) if n != target]
    kept: list[str] = []
    for node in siblings:
        if len(kept) >= MAX_PRESERVE:
            break
        if outcome_in(parent_wt, node, src, python)[0] != "PASSED":
            continue
        if outcome_in(fix_wt, node, src, python)[0] != "PASSED":
            continue
        kept.append(node)
    if kept:
        return kept, ""
    return [], f"no test in {rel} passes at both the parent and the fix; nothing would constrain a destructive patch"


def find_precondition(parent_wt: Path, target: str, src: str, python: str) -> tuple[list[str], str]:
    """An exact precondition sequence for an ordering case.

    The stage-2 record says only "full suite in declared collection order",
    which is not a reproducer a gate can run. A single test file that makes the
    target fail is; anything longer is rejected rather than approximated,
    because an approximate reproducer is the thing this project exists not to
    produce.
    """
    rel = target.split("::")[0]
    directory = str(Path(rel).parent)
    candidates = sorted(
        str(p.relative_to(parent_wt)).replace("\\", "/")
        for p in (parent_wt / directory).glob("test_*.py")
        if str(p.relative_to(parent_wt)).replace("\\", "/") != rel
    )[:MAX_ORDER_CANDIDATES]
    for candidate in candidates:
        original = cc.python_for
        cc.python_for = lambda _repo: python  # type: ignore[assignment]
        try:
            obs = cc.run(
                [python, "-m", "pytest", "-q", "--tb=line", "-rA", "-p", "no:cacheprovider", candidate, target],
                cwd=parent_wt,
                env=cc.pytest_env(parent_wt, src),
                timeout=900,
            )
        finally:
            cc.python_for = original  # type: ignore[assignment]
        text = obs.stdout + obs.stderr
        for line in text.splitlines():
            verdict, _, rest = line.strip().partition(" ")
            if verdict in ("FAILED", "ERROR") and rest.strip().startswith(target):
                signature = ""
                for probe in text.splitlines():
                    if probe.strip().startswith("E "):
                        signature = probe.strip()[1:].strip()[:160]
                return [candidate], signature
    return [], f"no single test file under {directory} reproduces the ordering failure within {MAX_ORDER_CANDIDATES}"


def curate(case: dict, repos: Path, work: Path, layouts: dict) -> dict:
    repo = repos / case["repo"]
    python = cc.python_for(repo)
    rec = dict(case)
    rec["curation"] = {"commands": [], "rejected": ""}
    log = rec["curation"]["commands"]

    parent_wt = work / f"{case['case_id']}-parent"
    fix_wt = work / f"{case['case_id']}-fix"
    for dest, commit, label in ((parent_wt, case["parent"], "parent"), (fix_wt, case["commit"], "fix")):
        problem = worktree_for(repo, commit, dest)
        log.append(f"git worktree add --detach {dest.name} {commit[:12]} ({label})")
        if problem:
            rec["curation"]["rejected"] = f"could not materialize the {label} worktree: {problem}"
            return rec

    # Read from the materialized tree, not from a list that may not mention it.
    src = detect_layout(parent_wt)
    log.append(f"layout detected: {src or 'flat'}")

    # The target may not exist at the parent: most projects add the regression
    # test in the same commit as the fix. Stage 2 applied the commit's test half
    # to the parent for exactly this reason, and a curation that skips it
    # reports "the target does not fail at the parent" when the truth is that
    # the target is not there yet.
    changed = cc.git(repo, "diff", "--name-only", case["parent"], case["commit"]).stdout.split()
    test_files = [f for f in changed if f.endswith(".py") and cc._TEST_PATH.search(f)]
    if test_files:
        patch = cc.git(repo, "diff", f"{case['parent']}..{case['commit']}", "--", *test_files).stdout
        if patch.strip():
            applied = cc.run_input(["git", "-C", str(parent_wt), "apply", "--3way"], patch)
            log.append(f"applied the commit's test half to the parent ({len(test_files)} file(s)) -> rc={applied}")
            if applied != 0:
                rec["curation"]["rejected"] = "the commit's test half does not apply to its parent"
                return rec

    # 5. an ordering case needs an exact sequence before anything else is
    #    meaningful, because its target does not fail without one.
    preconditions: list[str] = []
    if case.get("ordering_precondition"):
        preconditions, detail = find_precondition(parent_wt, case["target"], src, python)
        log.append(f"searched for an exact precondition under {Path(case['target']).parent}")
        if not preconditions:
            rec["curation"]["rejected"] = detail
            return rec
        rec["reproducer"] = preconditions
        rec["signature"] = detail or case.get("signature") or ""

    # 2. reproduce at the parent, and freeze what was actually observed.
    if preconditions:
        observed, signature = "FAILED", rec["signature"]
    else:
        observed, signature = outcome_in(parent_wt, case["target"], src, python)
    log.append(f"pytest {case['target']} at the parent -> {observed}")
    if observed not in ("FAILED", "ERROR"):
        # The observed detail is carried into the reason. "UNOBSERVED" alone
        # cannot be acted on; "no module named filelock.version" says the
        # package needs a build step a source worktree does not perform, which
        # is a different problem from a case that fails to reproduce.
        detail = f": {signature}" if signature else ""
        rec["curation"]["rejected"] = f"the target does not fail at the pinned parent (observed {observed}){detail}"
        return rec
    if not signature:
        rec["curation"]["rejected"] = "the failure produced no signature to freeze"
        return rec
    rec["signature"] = signature

    # 3. the project's own fix must repair it.
    fixed, fixed_detail = outcome_in(fix_wt, case["target"], src, python)
    log.append(f"pytest {case['target']} at the fix -> {fixed}")
    if fixed != "PASSED":
        detail = f": {fixed_detail}" if fixed_detail else ""
        rec["curation"]["rejected"] = f"the project's own fix does not make the target pass (observed {fixed}){detail}"
        return rec

    # 4. preservation.
    preserve, problem = find_preservation(parent_wt, fix_wt, case["target"], src, python)
    log.append(f"preservation candidates from {case['target'].split('::')[0]} -> {len(preserve)} kept")
    if problem:
        rec["curation"]["rejected"] = problem
        return rec

    rec["preserve"] = preserve
    rec["worktree"] = str(parent_wt)
    rec["fix_worktree"] = str(fix_wt)
    rec["src_layout"] = src or "flat"
    rec["curation"]["validated"] = True
    return rec


def main() -> int:
    parser = argparse.ArgumentParser(prog="curate_cases")
    parser.add_argument("--manifest", default="benchmark/bm06/manifest-proposed.json")
    parser.add_argument("--repos", default="/repos")
    parser.add_argument("--work", default="/repos/.cases")
    parser.add_argument("--layouts", default="benchmark/bm06/src-layouts.json")
    parser.add_argument("--out", default="benchmark/bm06/manifest-preliminary.json")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    frozen = json.loads(Path("benchmark/bm06/model-and-pricing.json").read_text(encoding="utf-8"))
    layouts = json.loads(Path(args.layouts).read_text(encoding="utf-8")) if Path(args.layouts).is_file() else {}
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    curated: list[dict] = []
    for case in manifest["cases"]:
        try:
            rec = curate(case, Path(args.repos), work, layouts)
        except Exception as exc:  # noqa: BLE001 - a crash is a rejection with a reason
            rec = dict(case, curation={"rejected": f"harness error: {type(exc).__name__}: {exc}"[:200]})
        curated.append(rec)
        state = "VALIDATED" if rec["curation"].get("validated") else "rejected"
        print(
            f"[{rec['repo']:11} {rec['cause_class']:19}] {state}: {rec['curation'].get('rejected', '')[:70]}",
            flush=True,
        )

    validated = [c for c in curated if c["curation"].get("validated")]

    # The per-arm-run ceiling, from the frozen per-operation reservations rather
    # than a literal. Arm A makes only propose_change; B and C also diagnose.
    # Every arm may make one schema repair, and the ceiling assumes all of them do.
    res = frozen["worst_case_reservation"]["per_operation"]
    pin = frozen["pricing"]["selected"]["input_per_mtok"]
    pout = frozen["pricing"]["selected"]["output_per_mtok"]

    def op_usd(name: str) -> float:
        return res[name]["input_ceiling"] * pin / 1e6 + res[name]["max_output"] * pout / 1e6

    arm_a_usd = op_usd("propose_change") + op_usd("propose_change_repair")
    arm_bc_usd = arm_a_usd + op_usd("propose_handles") + op_usd("propose_hypotheses")

    payload = {
        "schema": 1,
        "name": "preliminary",
        "claim_limit": (
            "A preliminary benchmark. It does NOT satisfy the frozen BM-06 denominator of 30 cases across eight "
            "cause classes and must not be reported as BM-06 or as evidence for the eight-class thesis."
        ),
        "protocol_sha256": manifest.get("protocol_sha256", ""),
        # Read from the frozen configuration rather than copied by hand: the
        # arms must share one model, one price and one ceiling, and a manifest
        # that merely looks complete is what the driver's validator exists to
        # refuse.
        "model": {
            "id": frozen["model"]["id"],
            "price_input_per_mtok": frozen["pricing"]["selected"]["input_per_mtok"],
            "price_output_per_mtok": frozen["pricing"]["selected"]["output_per_mtok"],
            "price_note": (
                f"{frozen['model']['id']} at ${frozen['pricing']['selected']['input_per_mtok']:.2f}/MTok input "
                f"and ${frozen['pricing']['selected']['output_per_mtok']:.2f}/MTok output, verified "
                f"{frozen['pricing']['verified_on']} by documentation lookup; reconfirm before execution"
            ),
            **{k: frozen["runtime_configuration"][k] for k in ("max_probes", "max_attempts", "max_commands")},
            "max_output_tokens": frozen["request_parameters"]["max_output_tokens"],
            "timeout_s": frozen["runtime_configuration"]["timeout_s"],
        },
        "budget": {
            "scope": frozen["runtime_configuration"]["scope"],
            # Proportional to the validated case count, not the 30-case figure:
            # a ceiling sized for a run that cannot happen is not a ceiling.
            # Read from the same frozen reservation the manifest records, so a
            # re-curation cannot quietly emit a ceiling the run no longer has.
            "max_usd": round(
                (arm_a_usd + 2 * arm_bc_usd) * len(validated) + 0.005,
                2,
            ),
            "worst_case_note": (
                f"per-task worst case ${arm_bc_usd:.4f} for arms B and C and ${arm_a_usd:.4f} for arm A at the "
                "frozen caps, each including the one schema-repair request the adapter may make. NOT AUTHORIZED."
            ),
        },
        "arms": {
            "A": {"description": "the model alone; accept when the target passes after applying the patch"},
            "B": {
                "description": "model + ledger + random probe selection, frozen seed",
                "seed": 20260818,
                "policy": "random",
            },
            "C": {"description": "the full kernel: disagreement-per-cost selection and the counterfactual gate"},
        },
        "cases": validated,
        "rejected": [
            {"case_id": c["case_id"], "reason": c["curation"].get("rejected", "")}
            for c in curated
            if not c["curation"].get("validated")
        ],
    }
    Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nvalidated {len(validated)} of {len(curated)}")
    by_class: dict[str, int] = {}
    for c in validated:
        by_class[c["cause_class"]] = by_class.get(c["cause_class"], 0) + 1
    for cls, n in sorted(by_class.items()):
        print(f"  {cls:20} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
