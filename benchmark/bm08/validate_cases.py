"""Stage C: build each candidate's historical failure and prove it, model-free.

Curation says a case *looks* usable. This runs it. A case is only real when the
whole sequence holds, in order, without anything being repaired by hand:

    fix_commit^ == declared parent
      -> check out the exact parent
      -> apply the fix commit's TEST changes only (the frozen reproducer)
      -> the target FAILS there
      -> the COMPLETE preservation set PASSES there        <-- see below
      -> apply the fix commit's SOURCE changes only (the historical fix)
      -> the target PASSES
      -> the COMPLETE preservation set STILL PASSES

Two properties of that sequence are load-bearing and were previously missing.

**The preservation set is complete, never sampled.** An earlier version ran the
first eight nodes. A candidate that passes eight and breaks the thirty-seventh
has not preserved behaviour, and no amount of care elsewhere recovers a ground
truth computed from a sample.

**Preservation must already pass on the buggy baseline.** Otherwise a node that
was *already* failing before the fix could later mark a model candidate as
regressing behaviour it never had. That would manufacture the exact
weak-ACCEPT/strong-REJECT event BM-08 exists to measure, out of nothing. A node
failing at baseline is not "preserved"; the case is rejected rather than the node
redefined.

Anything short of the full sequence is dropped with the exact reason.
Repositories are never edited to make a case reproduce, and no source is
authored: both halves are the project's own commit, split by path.

No model is called and no network is used.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "src"))
import bm08_driver as driver  # noqa: E402
import confinement  # noqa: E402
from curation import direct_parent_valid, git  # noqa: E402
from repo_resolution import preflight  # noqa: E402

ENV = {
    "GIT_AUTHOR_NAME": "bm08",
    "GIT_AUTHOR_EMAIL": "bm08@riftagent.invalid",
    "GIT_COMMITTER_NAME": "bm08",
    "GIT_COMMITTER_EMAIL": "bm08@riftagent.invalid",
}
TIMEOUT = 1800
# Node ids are batched into one pytest process. The bound exists so a very wide
# preservation surface cannot overrun the command line; it is a chunk size, not
# a sample, and every chunk is executed and recorded.
CHUNK = 40
MIN_PRESERVATION = 3
# BM-08-v4: frozen at 3, declared before revalidation, not changed afterwards.
STABILITY_OBSERVATIONS = 3
OBSERVER = pathlib.Path(__file__).parent / "observe_signature.py"
# BM-08-v2 frozen minimum executable denominator, declared before any v2
# survival count was known. Conjunctive; no discretionary judgement afterwards.
MIN_CASES = 12
MIN_REPOS = 10
MAX_PER_REPO = 3
ASPIRATIONAL = "20-30 cases across 10+ repositories"

# Deterministic rejection taxonomy. Environment failures are never merged with
# task failures — and in this phase there are no model failures at all.
REJECTION_KINDS = (
    ("unrunnable at parent", "collection/import/runtime incompatibility"),
    ("does not fail at parent", "target already passes at baseline"),
    ("not green on the buggy", "baseline preservation fails"),
    ("does not make the target pass", "historical fix leaves target failing"),
    ("not green after the historical fix", "historical fix breaks preservation"),
    ("direct_parent_invalid", "direct-parent/provenance mismatch"),
    ("insufficient_preservation", "preservation set too small"),
    ("insufficient_untouched_preservation", "insufficient untouched preservation"),
    ("no_resolvable_target", "target-resolution failure"),
    ("test half did not apply", "reproducer does not apply to parent"),
    ("source half did not apply", "historical source fix does not apply"),
    ("clone failed", "repository materialisation failure"),
    ("could not check out", "repository materialisation failure"),
    ("unstable_failure_identity", "unstable failure identity"),
    ("failure identity unobservable", "failure identity unobservable"),
    ("stability baseline could not be rebuilt", "stability baseline rebuild failure"),
    ("timed out", "validation timeout"),
    # Deliberately absent: repository resolution. It blocks the run in preflight
    # and is never a candidate verdict.
)


def classify_rejections(rows: list[dict]) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    for row in rows:
        if row["curation_status"] == "validated":
            continue
        why = row.get("rejection_reason", "") or ""
        for needle, label in REJECTION_KINDS:
            if needle in why:
                counts[label] += 1
                break
        else:
            counts["other governed validation failure"] += 1
    return counts


def repo_path(name: str, roots: list[pathlib.Path]) -> pathlib.Path:
    """Resolve a repository name across the v3 mining roots.

    The population spans two roots — the one carried forward from earlier
    benchmarks and the v3 expansion. Resolving against only the first silently
    turns every new-repository candidate into a provenance mismatch, because git
    commands against a nonexistent path fail exactly like a bad parent does.
    Returns the first root that actually holds the repository.
    """
    for root in roots:
        if (root / name / ".git").exists():
            return root / name
    return roots[0] / name


def run(cmd: list[str], cwd: pathlib.Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=TIMEOUT,
        env={**os.environ, **ENV, **(env or {})},
    )


def apply_half(staging: pathlib.Path, source_repo: pathlib.Path, sha: str, paths: list[str]) -> tuple[bool, str]:
    """Apply one half of the fix commit, split by path. Nothing is authored."""
    diff = git(source_repo, "show", "--format=", sha, "--", *paths)
    if not diff.strip():
        return False, "the commit changes nothing under those paths"
    patch = staging.parent / f"{staging.name}.half.diff"
    patch.write_text(diff, encoding="utf-8", newline="")
    proc = run(["git", "apply", "--whitespace=nowarn", str(patch)], staging)
    patch.unlink(missing_ok=True)
    if proc.returncode != 0:
        return False, f"git apply failed: {proc.stderr.strip()[:160]}"
    return True, ""


def pytest_env(staging: pathlib.Path, layout: str) -> dict[str, str]:
    src = staging / layout if layout and layout != "flat" else staging
    return {"PYTHONPATH": str(src.resolve())}


def one_node(staging: pathlib.Path, node: str, layout: str) -> tuple[str, str]:
    """Run one node id. Returns (outcome, detail) — outcome in pass/fail/error."""
    proc = confinement.run_repository_check(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "-x", node],
        staging,
        pytest_env(staging, layout),
        timeout=TIMEOUT,
    )
    tail = (proc.stdout or "")[-400:].replace("\n", " ")
    if proc.returncode == 0:
        return "pass", ""
    if proc.returncode == 1:
        return "fail", tail[:200]
    return "error", f"exit {proc.returncode}: {tail[:180]}"


def run_preservation(staging: pathlib.Path, nodes: list[str], layout: str) -> dict:
    """Execute the complete preservation set, in deterministic chunks.

    Chunks are recorded so the record shows how the set was executed rather than
    implying a single invocation. On any chunk failure the offending nodes are
    identified individually, which costs a rerun only on the failing path.
    """
    chunks = [nodes[i : i + CHUNK] for i in range(0, len(nodes), CHUNK)]
    failures: list[str] = []
    for chunk in chunks:
        proc = confinement.run_repository_check(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "--tb=no", *chunk],
            staging,
            pytest_env(staging, layout),
            timeout=TIMEOUT,
        )
        if proc.returncode != 0:
            for node in chunk:
                outcome, detail = one_node(staging, node, layout)
                if outcome != "pass":
                    failures.append(f"{node}: {outcome} {detail[:60]}")
    return {
        "requested": len(nodes),
        "executed": len(nodes),
        "chunks": [len(c) for c in chunks],
        "all_passed": not failures,
        "failures": failures,
    }


def observe_stability(staging: pathlib.Path, node: str) -> tuple[bool, list[dict]]:
    """Three failure observations, each in its own process.

    The process boundary is the measurement, not an implementation detail. A
    signature containing an object's memory address is stable inside one
    interpreter and differs between interpreters, so three in-process
    observations would have agreed with each other and declared BM-08-v3's
    defective cases reproducible.

    Returns (stable, observations). All three are returned whatever the verdict:
    a boolean alone cannot be audited, and the differing identities are the
    evidence that the case is unusable.
    """
    observations: list[dict] = []
    for index in range(1, STABILITY_OBSERVATIONS + 1):
        proc = run([sys.executable, str(OBSERVER), str(staging), node], staging)
        line = (proc.stdout or "").strip().splitlines()
        try:
            identity = json.loads(line[-1]) if line else {"error": "no output"}
        except Exception:
            identity = {"error": (proc.stdout or proc.stderr or "")[-160:]}
        observations.append(
            {
                "observation": index,
                "fresh_process": True,
                "exit_code": proc.returncode,
                "identity": identity,
            }
        )
    identities = [json.dumps(o["identity"], sort_keys=True) for o in observations]
    stable = len(set(identities)) == 1 and "error" not in observations[0]["identity"]
    return stable, observations


def detect_layout(staging: pathlib.Path) -> str:
    return "src" if (staging / "src").is_dir() else "flat"


def validate(case: dict, repo: pathlib.Path, staging_root: pathlib.Path) -> dict:
    """`repo` is the already-resolved location from the resolution preflight.

    Taking a resolved path rather than a root is deliberate: this function can no
    longer construct a wrong path, so it can no longer manufacture a provenance
    rejection out of a missing checkout.
    """
    cid = case["case_id"]
    sha, parent = case["fix_commit"], case["parent"]
    preservation = list(case["untouched_preservation_nodes"])
    out: dict = {
        "case_id": cid,
        "repository": case["repository"],
        "fix_commit": sha,
        "parent": parent,
        "target_node": case["target_node"],
        "target_resolution_method": case["target_resolution_method"],
        "queue_position": case.get("queue_position"),
        "author_date": case.get("author_date", ""),
        "committer_date": case.get("committer_date", ""),
        "order_key": case.get("order_key", ""),
        "subject": case.get("subject", ""),
        "categories": case.get("categories", []),
        "direct_parent_valid": False,
        "preservation_nodes": preservation,
        "preservation_count": len(preservation),
        "baseline_tree_hash": "",
        "failure_identity": {},
        "failure_identity_stable": None,
        "failure_identity_observations": [],
        "baseline_target_result": None,
        "baseline_preservation_results": None,
        "historical_fix_target_result": None,
        "historical_fix_preservation_results": None,
        "curation_status": "rejected",
        "rejection_reason": "",
    }

    ok, detail = direct_parent_valid(repo, sha, parent)
    if not ok:
        out["rejection_reason"] = f"direct_parent_invalid: {detail}"
        return out
    out["direct_parent_valid"] = True

    if len(preservation) < MIN_PRESERVATION:
        out["rejection_reason"] = f"insufficient_preservation: {len(preservation)} < {MIN_PRESERVATION}"
        return out

    staging = staging_root / cid
    shutil.rmtree(staging, ignore_errors=True)
    if run(["git", "clone", "-q", "--shared", str(repo), str(staging)], staging_root).returncode != 0:
        out["rejection_reason"] = "clone failed"
        return out
    if run(["git", "checkout", "-q", "--force", parent], staging).returncode != 0:
        out["rejection_reason"] = "could not check out the parent commit"
        return out

    ok, why = apply_half(staging, repo, sha, case["test_files"])
    if not ok:
        out["rejection_reason"] = f"test half did not apply: {why}"
        return out
    run(["git", "add", "-A"], staging)
    run(["git", "commit", "-q", "-m", "frozen reproducer"], staging)

    layout = detect_layout(staging)
    out["src_layout"] = layout

    # The baseline's identity, recorded from the tree that was actually built.
    # A case whose manifest carries no baseline hash cannot be shown at
    # execution time to be running the same tree that was validated here.
    try:
        from riftagent.sandbox import probe_isolation, tree_hash

        out["baseline_tree_hash"] = tree_hash(staging)
    except Exception as exc:  # pragma: no cover - environment, not a verdict
        out["rejection_reason"] = f"baseline identity unavailable: {type(exc).__name__}: {exc}"[:180]
        return out

    outcome, detail = one_node(staging, case["target_node"], layout)
    out["baseline_target_result"] = outcome
    if outcome != "fail":
        out["rejection_reason"] = (
            f"target does not fail at parent+reproducer: {outcome} ({detail[:110]})"
            if outcome != "error"
            else f"target unrunnable at parent+reproducer: {detail[:130]}"
        )
        return out

    # Captured through `checks.run_check`, the component the gate itself calls,
    # so the identity frozen here and the identity compared at execution are the
    # same kind of object produced by the same code. A pytest `E` line written
    # into a manifest and compared against a structured signature later would be
    # two vocabularies pretending to be one.
    try:
        signature = driver.observe_failure_identity(staging, case["target_node"], probe_isolation())
    except Exception as exc:  # pragma: no cover - environment, not a verdict
        out["rejection_reason"] = f"failure identity unobservable: {type(exc).__name__}: {exc}"[:180]
        return out
    if signature is None:
        out["rejection_reason"] = "failure-identity mismatch: the governed observer captured no signature"
        return out
    out["failure_identity"] = {
        "exception_type": getattr(signature, "exception_type", "") or "",
        "message": getattr(signature, "message", "") or "",
    }

    # The preservation surface must already be green on the buggy tree, or a
    # node that was failing all along could later be read as a regression.
    baseline = run_preservation(staging, preservation, layout)
    out["baseline_preservation_results"] = baseline
    if not baseline["all_passed"]:
        out["rejection_reason"] = (
            f"preservation not green on the buggy baseline: {len(baseline['failures'])} of "
            f"{baseline['requested']} fail, e.g. {baseline['failures'][:2]}"
        )
        return out

    ok, why = apply_half(staging, repo, sha, case["source_files"])
    if not ok:
        out["rejection_reason"] = f"source half did not apply: {why}"
        return out

    outcome, detail = one_node(staging, case["target_node"], layout)
    out["historical_fix_target_result"] = outcome
    if outcome != "pass":
        out["rejection_reason"] = f"upstream source fix does not make the target pass: {outcome} ({detail[:110]})"
        return out

    fixed = run_preservation(staging, preservation, layout)
    out["historical_fix_preservation_results"] = fixed
    if not fixed["all_passed"]:
        out["rejection_reason"] = (
            f"preservation not green after the historical fix: {len(fixed['failures'])} of "
            f"{fixed['requested']} fail, e.g. {fixed['failures'][:2]}"
        )
        return out

    # BM-08-v4 §11: only candidates that would otherwise be VALID reach here, so
    # no already-rejected candidate spends three further baseline processes.
    #
    # The observations must run on a FRESH BASELINE. By this point `staging` has
    # the historical source fix applied and the target passes, so there is no
    # failure left to observe — measuring there reports every case as having no
    # signature at all. The amendment says "same frozen baseline"; this is it.
    stability_tree = staging.parent / f"{cid}-stability"
    try:
        driver.materialise_baseline(case, repo.parent, stability_tree)
    except RuntimeError as exc:
        out["rejection_reason"] = f"stability baseline could not be rebuilt: {exc}"[:180]
        return out
    stable, observations = observe_stability(stability_tree, case["target_node"])
    shutil.rmtree(stability_tree, ignore_errors=True)

    out["failure_identity_stable"] = stable
    out["failure_identity_observations"] = observations
    # Three identical errors mean the observer saw no failure at all. That is an
    # unobservable signature, not an unstable one, and conflating them would
    # report a harness fault as a property of the case.
    if any("error" in o["identity"] for o in observations):
        out["rejection_reason"] = (
            f"failure identity unobservable across {STABILITY_OBSERVATIONS} fresh processes: "
            f"{observations[0]['identity']}"
        )[:300]
        return out
    if not stable:
        seen = [o["identity"] for o in observations]
        out["rejection_reason"] = (
            f"unstable_failure_identity: {STABILITY_OBSERVATIONS} fresh processes disagreed: {seen}"[:400]
        )
        return out

    out["curation_status"] = "validated"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shortlist", default="/s/bm08_cases.json")
    ap.add_argument("--repos", default="/repos")
    ap.add_argument("--staging", default="/tmp/bm08-staging")
    ap.add_argument("--out", default="/s/bm08_validated.json")
    ap.add_argument("--key", default="cases")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--recent-first", action="store_true")
    args = ap.parse_args()

    data = json.loads(pathlib.Path(args.shortlist).read_text(encoding="utf-8"))
    cases = data[args.key]
    # A candidate dropped during curation is rejected, not vanished. Carrying
    # these through is what makes `post_dedupe == rejected + valid` true end to
    # end rather than only across the validation stage.
    curation_dropped = [
        {
            "case_id": row["case_id"],
            "repository": row["case_id"].rsplit("-", 1)[0],
            "curation_status": "rejected",
            "rejection_reason": row["reason"],
            "stage": "curation",
        }
        for row in data.get("dropped", [])
    ]
    post_dedupe = data.get("attempted", len(cases) + len(curation_dropped))
    if args.recent_first:
        # Commits mined from deep history often cannot import on Python 3.12 at
        # all — `@asyncio.coroutine`, packages predating importlib.metadata,
        # syntax the interpreter no longer accepts. That is a property of the
        # commit's era, not of the case, so newer candidates are tried first.
        cases = sorted(cases, key=lambda c: c.get("date", ""), reverse=True)
    if args.limit:
        cases = cases[: args.limit]

    # §4: every represented repository must resolve uniquely before the first
    # candidate validation begins. Resolution failure blocks the whole run and
    # never enters the scientific rejection breakdown.
    roots = [pathlib.Path(args.repos), pathlib.Path("/repos-v3"), pathlib.Path("/repos-v5")]
    resolution = preflight({c["repository"] for c in cases}, roots, "repository preflight")
    if not resolution.ok:
        return 3

    staging_root = pathlib.Path(args.staging)
    staging_root.mkdir(parents=True, exist_ok=True)

    rows = []
    for case in cases:
        t0 = time.time()
        try:
            row = validate(case, resolution.path(case["repository"]), staging_root)
        except subprocess.TimeoutExpired:
            row = {
                "case_id": case["case_id"],
                "repository": case["repository"],
                "curation_status": "rejected",
                "rejection_reason": f"timed out after {TIMEOUT}s",
            }
        row["seconds"] = round(time.time() - t0, 1)
        rows.append(row)
        shutil.rmtree(staging_root / case["case_id"], ignore_errors=True)
        mark = "OK  " if row["curation_status"] == "validated" else "drop"
        extra = f"pres={row.get('preservation_count', '?')}  " if row["curation_status"] == "validated" else ""
        print(f"  {mark} {row['case_id']:22} {extra}{row.get('rejection_reason', '')[:90]}", flush=True)

    for row in rows:
        row.setdefault("stage", "validation")
    rows = curation_dropped + rows
    validated = [r for r in rows if r["curation_status"] == "validated"]

    # BM-08-v2 §14: the repository cap applies to VALID survivors only, after
    # reproduction. Capping beforehand spends a repository's quota on candidates
    # that turn out to be unrunnable — a repo whose first two candidates fail
    # should contribute its third, fourth and fifth valid ones, not be reduced to
    # whatever survived of the first three.
    #
    # Within a repository, survivors are taken in the frozen deterministic order
    # (SHA-256 of the fix commit, fixed before validation began). No score, no
    # inspection, and nothing a model produced reaches this decision.
    ordered = sorted(validated, key=lambda r: r.get("queue_position") or 0)
    per_repo: collections.Counter = collections.Counter()
    primary_ids: set[str] = set()
    for r in ordered:
        if per_repo[r["repository"]] >= MAX_PER_REPO:
            continue
        per_repo[r["repository"]] += 1
        primary_ids.add(r["case_id"])
    for r in rows:
        if r["curation_status"] == "validated":
            r["corpus_role"] = "primary" if r["case_id"] in primary_ids else "validated_surplus"

    valid_by_repo_before_cap = collections.Counter(r["repository"] for r in validated)
    final_repos = len({r["repository"] for r in ordered if r["case_id"] in primary_ids})
    sufficient = len(primary_ids) >= MIN_CASES and final_repos >= MIN_REPOS

    pathlib.Path(args.out).write_text(
        json.dumps(
            {
                "attempted": len(rows),
                "validated": len(validated),
                "unique_validated_repositories": len({r["repository"] for r in validated}),
                "primary_corpus": sorted(primary_ids),
                "validated_surplus": sorted({r["case_id"] for r in validated} - primary_ids),
                "valid_before_repo_cap": len(validated),
                "valid_by_repository_before_cap": dict(valid_by_repo_before_cap),
                "removed_by_repository_cap": len(validated) - len(primary_ids),
                "final_primary_cases": len(primary_ids),
                "final_distinct_repositories": final_repos,
                "minimum_cases": MIN_CASES,
                "minimum_repositories": MIN_REPOS,
                "max_per_repository": MAX_PER_REPO,
                "threshold_passed": sufficient,
                "rejection_breakdown": dict(classify_rejections(rows)),
                "post_dedupe_candidates": post_dedupe,
                "rejected_total": sum(classify_rejections(rows).values()),
                "accounting_conserved": post_dedupe == sum(classify_rejections(rows).values()) + len(validated),
                "cases": rows,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nattempted {len(rows)}   validated {len(validated)}")
    print(f"unique validated repositories : {len({r['repository'] for r in validated})}")
    print(f"primary corpus                : {len(primary_ids)}")
    print(f"validated surplus             : {len(validated) - len(primary_ids)}")
    print()
    print("REJECTION BREAKDOWN (every post-dedupe candidate accounted for)")
    breakdown = classify_rejections(rows)
    for reason, n in breakdown.most_common():
        print(f"  {n:4}  {reason}")
    rejected_total = sum(breakdown.values())
    conserved = post_dedupe == rejected_total + len(validated)
    print("  ----")
    print(f"  post-dedupe {post_dedupe} = rejected {rejected_total} + valid {len(validated)}  -> {conserved}")
    assert conserved, f"rejection accounting does not conserve: {post_dedupe} != {rejected_total} + {len(validated)}"
    print()
    print(f"POST-VALIDATION REPOSITORY CAP (<={MAX_PER_REPO} VALID cases/repository)")
    print(f"  VALID before repo cap        : {len(validated)}")
    print(f"  per-repository VALID         : {dict(valid_by_repo_before_cap.most_common())}")
    print(f"  removed by repo cap          : {len(validated) - len(primary_ids)}")
    print(f"  VALID after repo cap         : {len(primary_ids)}")
    print()
    print(f"final primary cases           : {len(primary_ids)}")
    print(f"final distinct repositories   : {final_repos}")
    print(f"aspirational target           : {ASPIRATIONAL}")
    print(f"frozen minimum                : >={MIN_CASES} cases AND >={MIN_REPOS} repositories")
    if sufficient:
        print(f"THRESHOLD PASS: {len(primary_ids)} cases across {final_repos} repositories")
    else:
        print(f"CORPUS_SHORTFALL: {len(primary_ids)} cases, {final_repos} repositories. Reported, not padded.")
    if validated:
        widths = [r["preservation_count"] for r in validated]
        print(f"preservation set size         : min {min(widths)}  max {max(widths)}  total {sum(widths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
