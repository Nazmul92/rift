"""HISTORICAL-FIX-REGION COVERAGE AUDIT — EVALUATOR-ONLY — $0.00, NO PROVIDER.

One narrow question, asked of all 24 official BM-08 cases:

    was the source region the historical upstream fix touched present in the
    exact frozen context the model was given?

That is all it answers. The upstream fix is **one** valid solution, not
necessarily the only valid repair location, so `NOT_COVERED` means only *the
model was not shown this known fix region* — never *no correct repair was
possible from what it had*. Nothing here measures solvability, and nothing here
measures comprehension.

The historical fix is evaluator-only. It is read from git to compute coverage
and it never touches a prompt, a context, or any model-facing artifact; the
leakage tests in `tests/test_fix_region_audit.py` assert that structurally.

Context is reconstructed, not re-selected: RIFT's own `context_selected` event
records the files and line ranges it chose, so the audit compares the historical
fix against the window the model actually received rather than one this script
invented.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "benchmark" / "bm08"))
sys.path.insert(0, str(ROOT / "benchmark" / "analysis" / "source_recall_probe"))
sys.path.insert(0, str(ROOT / "src"))

import bm08_driver as driver  # noqa: E402
import probe_context  # noqa: E402

from riftagent.sandbox import tree_hash  # noqa: E402

BANNER = "HISTORICAL-FIX-REGION COVERAGE AUDIT — EVALUATOR-ONLY — NOT A SOLVABILITY CLAIM"
BM08 = ROOT / "benchmark" / "bm08"
OUT = HERE / "fix-region-coverage.json"
WORK = pathlib.Path("/tmp/fix-region-audit")
REPO_ROOTS = (pathlib.Path("/repos"), pathlib.Path("/repos-v3"), pathlib.Path("/repos-v5"))

COVERED = "COVERED"
PARTIALLY_COVERED = "PARTIALLY_COVERED"
NOT_COVERED = "NOT_COVERED"


class ContextIdentityError(RuntimeError):
    """Reconstruction did not reproduce the frozen context. Audit stops."""


def repo_for(name: str) -> pathlib.Path:
    found = [root / name for root in REPO_ROOTS if (root / name / ".git").is_dir()]
    if len(found) != 1:
        raise ContextIdentityError(f"{name} resolved to {len(found)} repository roots")
    return found[0]


def git(args: list[str], cwd: pathlib.Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise ContextIdentityError(f"git {' '.join(args)} failed: {(proc.stderr or '').strip()[:200]}")
    return proc.stdout


def fix_regions(repo: pathlib.Path, case: dict) -> dict[str, list[tuple[int, int]]]:
    """Which pre-fix lines the historical commit touched, per source file.

    Read from the parent side of the diff (`-` line numbers), because the
    question is which region of the *baseline the model saw* the fix altered.
    Test files are excluded: the model is forbidden from editing them, so a fix
    region inside one is not a place it could have been shown a repair.
    """
    raw = git(["diff", "--unified=0", f"{case['parent']}..{case['fix_commit']}"], repo)
    regions: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    protected = set(case.get("test_files") or [])
    for line in raw.splitlines():
        if line.startswith("--- "):
            path = line[4:].strip()
            current = path[2:] if path.startswith("a/") else (None if path == "/dev/null" else path)
            if current in protected:
                current = None
        elif line.startswith("@@") and current:
            # @@ -old_start,old_count +new_start,new_count @@
            body = line.split("@@")[1].strip()
            old = body.split(" ")[0]
            start_text, _, count_text = old.lstrip("-").partition(",")
            start = int(start_text)
            count = int(count_text) if count_text else 1
            if count == 0:
                # Pure insertion: the region is the seam between start and start+1.
                regions.setdefault(current, []).append((start, start + 1))
            else:
                regions.setdefault(current, []).append((start, start + count - 1))
    return regions


# A CHANGELOG entry is a region of the historical commit, but it is not a place
# a model could have been shown a repair: editing it cannot change a test
# outcome. Counting it inflates the denominator and can turn a case whose code
# region was fully covered into PARTIALLY_COVERED.
#
# DISCLOSURE: this secondary view was defined AFTER the primary counts were
# computed (2 COVERED / 8 PARTIALLY / 14 NOT_COVERED). The primary
# classification is retained unchanged and reported first; this is recorded
# beside it, not in place of it.
def code_only(regions: dict[str, list[tuple[int, int]]]) -> dict[str, list[tuple[int, int]]]:
    """Fix regions restricted to Python source the model could plausibly edit."""
    return {
        path: spans
        for path, spans in regions.items()
        if path.endswith(".py") and not path.startswith("docs/") and pathlib.PurePosixPath(path).name != "setup.py"
    }


def overlap(a: tuple[int, int], spans: list[list[int]]) -> bool:
    return any(a[0] <= int(end) and int(start) <= a[1] for start, end in spans)


def classify(regions: dict[str, list[tuple[int, int]]], selection: dict) -> tuple[str, str]:
    """COVERED / PARTIALLY_COVERED / NOT_COVERED, with the reason recorded."""
    if not regions:
        return NOT_COVERED, "the historical fix touched no non-test source file"
    selected_files = set(selection["files"])
    line_ranges = selection["line_ranges"]

    total = sum(len(spans) for spans in regions.values())
    seen = 0
    missing_files: list[str] = []
    for path, spans in regions.items():
        if path not in selected_files:
            missing_files.append(path)
            continue
        for span in spans:
            if overlap(span, line_ranges.get(path, [])):
                seen += 1

    if seen == total:
        return COVERED, f"all {total} fix-touched region(s) fall inside the selected spans"
    if seen == 0:
        if missing_files:
            return NOT_COVERED, f"fix-touched file(s) absent from the context: {', '.join(sorted(missing_files))}"
        return NOT_COVERED, "fix-touched file(s) selected, but no fix region falls inside the selected line ranges"
    detail = f"{seen} of {total} fix-touched region(s) inside the selected spans"
    if missing_files:
        detail += f"; file(s) absent: {', '.join(sorted(missing_files))}"
    return PARTIALLY_COVERED, detail


def main() -> int:
    print(BANNER)
    print("=" * len(BANNER))
    manifest = json.loads((BM08 / "manifest-executable-v5.json").read_text(encoding="utf-8"))
    replay = {
        (r["case_id"], r["arm"]): r
        for r in json.loads((BM08 / "canonicalization-replay.json").read_text(encoding="utf-8"))["records"]
    }
    official = {}
    for line in (BM08 / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            official[(record["case_id"], record["arm"])] = record

    shutil.rmtree(WORK, ignore_errors=True)
    rows: list[dict] = []
    for case in manifest["cases"]:
        repo = repo_for(case["repository"])
        tree = WORK / case["case_id"]
        driver.materialise_baseline(case, repo.parent, tree)
        observed = tree_hash(tree)
        if observed != case["baseline_tree_hash"]:
            print(f"BLOCKED_CONTEXT_IDENTITY: {case['case_id']} baseline {observed[:12]} != frozen")
            return 2

        # The context the model actually received, for the arm whose evidence
        # retained it. Arm C runs the full protocol, so its selection is the
        # governed one; arm A is the fallback when C has none.
        selection = None
        used_arm = ""
        for arm in ("C", "A"):
            evidence = BM08 / "results-evidence" / case["case_id"] / arm
            try:
                selection = probe_context.recorded_context(evidence)
                used_arm = arm
                break
            except probe_context.ContextIdentityError:
                continue
        if selection is None:
            print(f"BLOCKED_CONTEXT_IDENTITY: {case['case_id']} has no retained context selection")
            return 2
        rendered = probe_context.render_context(tree, selection)
        context_hash = probe_context.context_hash(rendered)

        regions = fix_regions(repo, case)
        status, reason = classify(regions, selection)
        code_regions = code_only(regions)
        code_status, code_reason = classify(code_regions, selection)
        arm_record = official.get((case["case_id"], "A"), {})
        c_record = official.get((case["case_id"], "C"), {})
        rows.append(
            {
                "case_id": case["case_id"],
                "repository": case["repository"],
                "baseline_tree_hash": case["baseline_tree_hash"],
                "context_hash": context_hash,
                "context_arm": used_arm,
                "selected_files": list(selection["files"]),
                "selected_line_ranges": selection["line_ranges"],
                "selected_chars": len(rendered),
                "context_limits": {
                    "cap_files": selection.get("cap_files"),
                    "cap_chars": selection.get("cap_chars"),
                    "cap_file_chars": selection.get("cap_file_chars"),
                },
                "fix_touched_files": sorted(regions),
                "fix_touched_regions": {path: [list(s) for s in spans] for path, spans in regions.items()},
                "coverage_status": status,
                "coverage_reason": reason,
                "code_only_coverage_status": code_status,
                "code_only_coverage_reason": code_reason,
                "code_only_fix_files": sorted(code_regions),
                "official_A_truth": (arm_record.get("ground_truth") or {}).get("ground_truth_verdict") or "absent",
                "official_C_truth": (c_record.get("ground_truth") or {}).get("ground_truth_verdict") or "absent",
                "canonical_class_A": replay.get((case["case_id"], "A"), {}).get("failure_class_canonical", ""),
                "canonical_class_C": replay.get((case["case_id"], "C"), {}).get("failure_class_canonical", ""),
                "canonical_applied_A": replay.get((case["case_id"], "A"), {}).get("canonical_apply_ok"),
                "canonical_applied_C": replay.get((case["case_id"], "C"), {}).get("canonical_apply_ok"),
            }
        )
        shutil.rmtree(tree, ignore_errors=True)
        print(f"  {case['case_id']:28} {status:18} code-only={code_status:18} {reason[:44]}")

    body = {
        "label": BANNER,
        "evaluator_only": True,
        "not_a_solvability_claim": (
            "The upstream historical fix is one valid solution, not necessarily the only valid "
            "repair location. NOT_COVERED means the model was not shown this known fix region; "
            "it does not prove no alternative correct repair was possible from the available context."
        ),
        "treatment": "preregistered stratification / explanatory variable for the representation analysis",
        "secondary_view_disclosure": (
            "code_only_coverage_status was defined after the primary counts were computed "
            "(2 COVERED / 8 PARTIALLY_COVERED / 14 NOT_COVERED). It excludes changelogs, "
            "packaging and docs files, which cannot change a test outcome. The primary "
            "classification is unchanged and is the one the stratification uses unless a "
            "reviewer directs otherwise."
        ),
        "stop_rule": (
            "The audit blocks only for an infrastructure defect: context that cannot be "
            "reconstructed, wrong context identity, historical-fix leakage into model-facing "
            "artifacts, or a systematic reconstruction defect. It does not block because some "
            "historical regions are NOT_COVERED, and no numerical coverage threshold is defined."
        ),
        "provider_calls": 0,
        "additional_spend_usd": 0.0,
        "corpus_manifest_hash": manifest["corpus_manifest_hash"],
        "cases": rows,
    }
    body["audit_manifest_hash"] = hashlib.sha256(
        (json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")
    ).hexdigest()
    OUT.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    from collections import Counter

    counts = Counter(r["coverage_status"] for r in rows)
    print(f"\ntotal {len(rows)}")
    for status in (COVERED, PARTIALLY_COVERED, NOT_COVERED):
        print(f"  {status:20} {counts.get(status, 0)}")
    print(f"\naudit_manifest_hash: {body['audit_manifest_hash']}")
    print(f"written: {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContextIdentityError as exc:
        print(f"BLOCKED_CONTEXT_IDENTITY: {exc}")
        raise SystemExit(2) from exc
