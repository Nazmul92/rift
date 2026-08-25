"""Classify with git as the authority, not a regex.

`git apply` distinguishes two failures in its own words, and the distinction is
exactly the one this experiment turns on:

  "corrupt patch at line N"  -> the diff cannot be parsed: structurally invalid
  "patch does not apply"     -> the diff parses, and the tree disagrees with it

A regex can guess at the first; only git decides it. `--recount` is the probe
for the specific structural fault this experiment can repair — it tells git to
ignore the header counts and derive them from the body, so a patch that fails
plain and succeeds under `--recount` has *only* a metadata defect.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

STRUCTURALLY_INVALID = "STRUCTURALLY_INVALID"
PARSEABLE_NON_APPLICABLE = "PARSEABLE_NON_APPLICABLE"
OTHER = "OTHER"


def git_apply(worktree: Path, patch: Path, *extra: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(worktree), "apply", "--check", *extra, str(patch)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc.returncode, (proc.stderr or proc.stdout).strip()


def classify(worktree: Path, patch: Path) -> dict:
    """What git says about this patch, plain and under `--recount`."""
    code, err = git_apply(worktree, patch)
    if code == 0:
        return {
            "classification": OTHER,
            "git_error": "",
            "recount_applies": True,
            "note": "applies cleanly now; it did not at run time",
        }

    corrupt = "corrupt patch" in err
    kind = STRUCTURALLY_INVALID if corrupt else PARSEABLE_NON_APPLICABLE
    recount_code, recount_err = git_apply(worktree, patch, "--recount")
    return {
        "classification": kind,
        "git_error": err[:300],
        "recount_applies": recount_code == 0,
        "recount_error": recount_err[:300] if recount_code else "",
    }
