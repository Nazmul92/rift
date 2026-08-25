"""Does the model-visible context contain what the fix actually requires?

**$0. No provider is configured and no model request is made.**

Two separations make this an audit rather than a leak:

* the upstream fix is read **here, in the harness**, to score a selection that
  was made without it. It never enters a prompt, and this script never calls a
  model at all;
* the configuration audited is the product's, not a copy of it. Protected paths
  come from `app.build_checkset`, the same function `cmd_fix` calls, so the
  audit cannot drift into measuring an approximation. An earlier version passed
  `case.get("preserve_files", [])` — a key the manifest does not have — and so
  audited an empty protected set against a product that protects the target and
  every preservation test. It agreed with nothing.

## Task-required ground truth

"Every line the upstream commit changed" is the wrong criterion. A release
commit touches forty files; a context bounded to six can never contain it, and
scoring against it measures the commit's breadth rather than the task's.

What the task requires is narrower and is derived mechanically:

1. start at the exact pinned parent;
2. take the upstream fix patch, source hunks only — the judge is frozen, so a
   candidate may not touch test files and the selector is not asked to offer
   them;
3. for each hunk, apply the patch *without* it and ask whether the frozen target
   still passes and the frozen preservation checks still hold. A hunk that can
   be dropped is not required;
4. keep the hunks that cannot be dropped, then verify that this set **alone**
   still satisfies target and preservation.

Step 4 is what makes the result honest. If each hunk is individually droppable
but the survivors together do not satisfy the checks, the required set is not a
single minimal subset and the procedure has no principled answer. That is
recorded as `AMBIGUOUS` and the case fails closed — it is not resolved by
picking one.

A case is `COVERED` when every required parent-side region is inside the
selected model-visible context.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from riftagent import app  # noqa: E402

PYTEST_TIMEOUT = 900


def env_for(case: dict, worktree: pathlib.Path) -> dict[str, str]:
    """The import environment the case actually needs.

    Six of the corpus repositories keep their package under `src/`, which is not
    importable from the worktree root. Running bare `pytest` there produced
    `ModuleNotFoundError` for every check — so the "failure text" fed to
    selection was an import error rather than the case's failure, and hunk
    minimization concluded that no hunk mattered because nothing ever passed.

    The manifest records the layout per case. Using it is the difference between
    auditing the case and auditing a broken invocation.
    """
    env = dict(os.environ)
    layout = case.get("src_layout") or "flat"
    if layout and layout != "flat":
        root = (worktree / layout).as_posix()
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def git(repo: pathlib.Path | str, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=600).stdout


def source_hunks(repo: pathlib.Path, parent: str, commit: str) -> list[dict]:
    """The fix, split into individually applicable source hunks.

    Test files are excluded because the frozen judge may not be edited by a
    candidate patch, so a hunk touching one is not a region the model could
    ever be asked to change.
    """
    diff = git(repo, "diff", parent, commit, "--", "*.py", ":(exclude)*test*")
    hunks: list[dict] = []
    header: list[str] = []
    current: dict | None = None
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git"):
            header = [line]
            current = None
        elif header and line.startswith(("index ", "--- ", "+++ ", "old mode", "new mode", "similarity", "rename")):
            header.append(line)
            if line.startswith("+++ "):
                current = {"file": line[6:].strip(), "header": "".join(header)}
        elif line.startswith("@@") and current is not None:
            span = line.split("@@")[1].strip().split(" ")[0].lstrip("-")
            start_s, _, count_s = span.partition(",")
            start, count = int(start_s), int(count_s) if count_s else 1
            hunks.append(
                {
                    "file": current["file"],
                    "header": current["header"],
                    "body": line,
                    # The region as it exists in the *parent* — the tree the
                    # model is shown and must patch.
                    "region": [start, start + max(count, 1) - 1] if count else [start, start + 1],
                }
            )
        elif hunks and line.startswith((" ", "+", "-", "\\")) and current is not None:
            hunks[-1]["body"] += line
    return hunks


def patch_of(hunks: list[dict]) -> str:
    """One patch from a subset of hunks, grouped by file, headers preserved."""
    out: list[str] = []
    for header in dict.fromkeys(h["header"] for h in hunks):
        out.append(header)
        out.extend(h["body"] for h in hunks if h["header"] == header)
    return "".join(out)


def checks_pass(
    worktree: pathlib.Path, patch: str, target: str, preserve: list[str], tmp: pathlib.Path, env: dict[str, str]
) -> bool:
    """Apply `patch` to a clean tree and run the frozen target and preservation
    checks. Restores the tree afterwards, whatever happened."""
    git(worktree, "checkout", "--", ".")
    if patch.strip():
        blob = tmp / "subset.diff"
        blob.write_text(patch, encoding="utf-8")
        applied = subprocess.run(
            ["git", "-C", str(worktree), "apply", str(blob)], capture_output=True, text=True, timeout=300
        )
        if applied.returncode != 0:
            git(worktree, "checkout", "--", ".")
            return False
    try:
        for node, expect_pass in [(target, True), *[(p, True) for p in preserve]]:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-x", "-q", "--no-header", node],
                cwd=str(worktree),
                capture_output=True,
                text=True,
                timeout=PYTEST_TIMEOUT,
                env=env,
            )
            if (proc.returncode == 0) is not expect_pass:
                return False
        return True
    finally:
        git(worktree, "checkout", "--", ".")


def required_hunks(
    worktree: pathlib.Path,
    hunks: list[dict],
    target: str,
    preserve: list[str],
    tmp: pathlib.Path,
    env: dict[str, str],
):
    """The hunks that cannot be dropped, or a reason the question has no answer."""
    if not hunks:
        return [], "the fix contains no source hunks"
    if not checks_pass(worktree, patch_of(hunks), target, preserve, tmp, env):
        return None, "the full upstream source patch does not satisfy the frozen target and preservation checks"

    required = []
    for i, hunk in enumerate(hunks):
        without = [h for j, h in enumerate(hunks) if j != i]
        if not checks_pass(worktree, patch_of(without), target, preserve, tmp, env):
            required.append(hunk)
    if not required:
        return None, "AMBIGUOUS: every hunk is individually droppable, so no single minimal subset is implied"
    if not checks_pass(worktree, patch_of(required), target, preserve, tmp, env):
        return None, (
            "AMBIGUOUS: the individually-required hunks do not satisfy the checks on their own, so the required "
            "set is not a single minimal subset"
        )
    return required, ""


def audit(case: dict, repos: pathlib.Path, tmp: pathlib.Path) -> dict:
    worktree = pathlib.Path(case["worktree"])
    repo = repos / case["repo"]
    target, preserve = case["target"], list(case.get("preserve") or [])

    # The product's own judge, so the protected set is the product's.
    checkset = app.build_checkset(target, tuple(preserve), worktree, 600.0)
    protected = checkset.protected_paths
    env = env_for(case, worktree)

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "--no-header", target],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        timeout=PYTEST_TIMEOUT,
        env=env,
    )
    # The same bounded excerpt `Flow.check_payload` makes durable, from the same
    # combined output `run_check` observes.
    failure_text = (proc.stdout + ("\n" + proc.stderr if proc.stderr else ""))[-app.FAILURE_EXCERPT_CHARS :]

    _sources, selection = app.select_context(worktree, failure_text, target, protected)
    selected = {rel: [tuple(r) for r in rs] for rel, rs in selection["line_ranges"].items()}

    hunks = source_hunks(repo, case["parent"], case["commit"])
    required, problem = required_hunks(worktree, hunks, target, preserve, tmp, env)

    row = {
        "case": case["case_id"],
        "target": target,
        "protected_paths": list(protected),
        "selected_files": selection["files"],
        "selected_ranges": {k: [list(r) for r in v] for k, v in selected.items()},
        "selected_chars": selection["chars"],
        # Mandatory alongside the verdict. Coverage alone cannot distinguish
        # "the budget is the limit" from "the selector is under-spending it",
        # and those two want opposite remedies. The stopped run spent 1.3%.
        "pct_of_global_budget": round(100.0 * selection["chars"] / app.MAX_CONTEXT_CHARS, 1),
        "global_budget_chars": app.MAX_CONTEXT_CHARS,
        "per_file_budget_chars": app.MAX_FILE_CHARS,
        "file_cap": app.MAX_CONTEXT_FILES,
        "selection_reason": selection["selection_reason"],
        "stages": selection["stages"],
        "ground_truth_method": (
            "upstream source hunks at the pinned parent, mechanically minimized against the frozen target and "
            "preservation checks; never shown to a model"
        ),
        "upstream_source_hunks": len(hunks),
        "src_layout": case.get("src_layout") or "flat",
    }
    if required is None:
        row.update({"verdict": "NOT_COVERED", "reason": problem, "required_regions": None})
        return row

    regions: dict[str, list[list[int]]] = {}
    for hunk in required:
        regions.setdefault(hunk["file"], []).append(hunk["region"])
    missing = []
    for path, spans in regions.items():
        got = selected.get(path)
        if got is None:
            missing.append(f"{path} was not selected at all")
            continue
        for lo, hi in spans:
            if not any(a <= lo and hi <= b for a, b in got):
                missing.append(f"{path}:{lo}-{hi} is outside the selected ranges {got}")
    row.update(
        {
            "required_regions": regions,
            "required_hunks": len(required),
            "verdict": "COVERED" if not missing else "NOT_COVERED",
            "reason": "; ".join(missing) or "every task-required region is inside the selected context",
        }
    )
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="benchmark/bm06/manifest-preliminary.json")
    parser.add_argument("--out", default="benchmark/bm06/context-audit.json")
    parser.add_argument("--repos", default="/repos")
    parser.add_argument("--tmp", default="/tmp/audit")
    args = parser.parse_args()

    manifest = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8"))
    tmp = pathlib.Path(args.tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    rows = []
    for case in manifest["cases"]:
        row = audit(case, pathlib.Path(args.repos), tmp)
        rows.append(row)
        print(
            f"{row['verdict']:12} {row['case'][:40]:40} {row['selected_chars']:>6} chars "
            f"({row['pct_of_global_budget']:>4.1f}% of budget)  {row['reason'][:42]}",
            flush=True,
        )

    pathlib.Path(args.out).write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    covered = sum(1 for r in rows if r["verdict"] == "COVERED")
    quarantined = len(manifest.get("quarantined") or [])
    print(f"\nCOVERED {covered}/{len(rows)} valid cases ({quarantined} quarantined, not audited)")
    print("model requests made by this audit: 0")
    return 0 if covered == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
