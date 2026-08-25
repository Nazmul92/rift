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
weak-ACCEPT/strong-REJECT event BM-07 exists to measure, out of nothing. A node
failing at baseline is not "preserved"; the case is rejected rather than the node
redefined.

Anything short of the full sequence is dropped with the exact reason.
Repositories are never edited to make a case reproduce, and no source is
authored: both halves are the project's own commit, split by path.

No model is called and no network is used.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from curation import direct_parent_valid, git  # noqa: E402

ENV = {
    "GIT_AUTHOR_NAME": "bm07",
    "GIT_AUTHOR_EMAIL": "bm07@riftagent.invalid",
    "GIT_COMMITTER_NAME": "bm07",
    "GIT_COMMITTER_EMAIL": "bm07@riftagent.invalid",
}
TIMEOUT = 1800
# Node ids are batched into one pytest process. The bound exists so a very wide
# preservation surface cannot overrun the command line; it is a chunk size, not
# a sample, and every chunk is executed and recorded.
CHUNK = 40
MIN_PRESERVATION = 3


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
    proc = run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "-x", node],
        staging,
        pytest_env(staging, layout),
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
        proc = run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "--tb=no", *chunk],
            staging,
            pytest_env(staging, layout),
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


def detect_layout(staging: pathlib.Path) -> str:
    return "src" if (staging / "src").is_dir() else "flat"


def validate(case: dict, repos: pathlib.Path, staging_root: pathlib.Path) -> dict:
    cid = case["case_id"]
    repo = repos / case["repository"]
    sha, parent = case["fix_commit"], case["parent"]
    preservation = list(case["untouched_preservation_nodes"])
    out: dict = {
        "case_id": cid,
        "repository": case["repository"],
        "fix_commit": sha,
        "parent": parent,
        "target_node": case["target_node"],
        "target_resolution_method": case["target_resolution_method"],
        "pre_model_score": case.get("score"),
        "direct_parent_valid": False,
        "preservation_nodes": preservation,
        "preservation_count": len(preservation),
        "baseline_target_result": None,
        "baseline_preservation_results": None,
        "historical_fix_target_result": None,
        "historical_fix_preservation_results": None,
        "discrimination_rationale": case["shortcut_hypotheses"][0],
        "discrimination_potential": case["discrimination_potential"],
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

    outcome, detail = one_node(staging, case["target_node"], layout)
    out["baseline_target_result"] = outcome
    if outcome != "fail":
        out["rejection_reason"] = (
            f"target does not fail at parent+reproducer: {outcome} ({detail[:110]})"
            if outcome != "error"
            else f"target unrunnable at parent+reproducer: {detail[:130]}"
        )
        return out

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

    out["curation_status"] = "validated"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shortlist", default="/w/benchmark/bm07/shortlist.json")
    ap.add_argument("--repos", default="/repos")
    ap.add_argument("--staging", default="/tmp/bm07-staging")
    ap.add_argument("--out", default="/w/benchmark/bm07/validated-cases.json")
    ap.add_argument("--key", default="all_passing")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--recent-first", action="store_true")
    args = ap.parse_args()

    data = json.loads(pathlib.Path(args.shortlist).read_text(encoding="utf-8"))
    cases = data[args.key]
    if args.recent_first:
        # Commits mined from deep history often cannot import on Python 3.12 at
        # all — `@asyncio.coroutine`, packages predating importlib.metadata,
        # syntax the interpreter no longer accepts. That is a property of the
        # commit's era, not of the case, so newer candidates are tried first.
        cases = sorted(cases, key=lambda c: c.get("date", ""), reverse=True)
    if args.limit:
        cases = cases[: args.limit]

    staging_root = pathlib.Path(args.staging)
    staging_root.mkdir(parents=True, exist_ok=True)

    rows = []
    for case in cases:
        t0 = time.time()
        try:
            row = validate(case, pathlib.Path(args.repos), staging_root)
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

    validated = [r for r in rows if r["curation_status"] == "validated"]

    # Primary corpus: the highest pre-model-ranked validated case per repository.
    # Chosen from the frozen curation score, before any provider call, so it can
    # never be influenced by how a model performs on a case.
    best: dict[str, dict] = {}
    for r in validated:
        cur = best.get(r["repository"])
        if cur is None or (r.get("pre_model_score") or 0) > (cur.get("pre_model_score") or 0):
            best[r["repository"]] = r
    primary_ids = {r["case_id"] for r in best.values()}
    for r in rows:
        if r["curation_status"] == "validated":
            r["corpus_role"] = "primary" if r["case_id"] in primary_ids else "validated_fallback"

    pathlib.Path(args.out).write_text(
        json.dumps(
            {
                "attempted": len(rows),
                "validated": len(validated),
                "unique_validated_repositories": len({r["repository"] for r in validated}),
                "primary_corpus": sorted(primary_ids),
                "validated_fallback": sorted({r["case_id"] for r in validated} - primary_ids),
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
    print(f"validated fallback            : {len(validated) - len(primary_ids)}")
    if validated:
        widths = [r["preservation_count"] for r in validated]
        print(f"preservation set size         : min {min(widths)}  max {max(widths)}  total {sum(widths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
