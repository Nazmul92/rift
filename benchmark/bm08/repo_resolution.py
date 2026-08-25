"""Repository resolution is infrastructure authority, not a scientific verdict.

The first BM-08-v3 run resolved repositories from one root while the population
spanned two. Every new-repository candidate pointed at a directory that did not
exist, git failed exactly as it does for a genuine bad parent, and 147
candidates were filed under `direct_parent_invalid` — a real governed rejection
reason. The conservation identity still balanced. The accounting was
self-consistent and wrong, and the run reported a plausible shortfall that was
entirely an artifact of a path bug.

The lesson is not "fix the path". It is that **an infrastructure failure must
never be able to wear a scientific reason's clothes**. A missing checkout says
nothing about whether a bug reproduces; recording it as though it did converts a
broken harness into evidence about the corpus.

So resolution runs as a preflight over every repository represented in the
post-dedupe queue, before the first candidate is validated, and it is fatal:

    0 matching locations              -> BLOCKED_REPOSITORY_RESOLUTION
    2+ incompatible matching locations -> BLOCKED_REPOSITORY_RESOLUTION
    exactly 1 identity-matching        -> PASS

Two locations holding the *same* repository — identical HEAD — are not
ambiguous; that is one repository visible twice. Two locations whose HEADs
disagree are two different repositories wearing one name, and silently picking
either would make the corpus unreproducible.

No model is called and no network is used.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

BLOCKED = "BLOCKED_REPOSITORY_RESOLUTION"


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, errors="replace", timeout=120
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


@dataclass
class Resolution:
    """Where every represented repository lives, or why the run cannot start."""

    resolved: dict[str, Path] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def path(self, name: str) -> Path:
        return self.resolved[name]


def resolve_repositories(names, roots: list[Path]) -> Resolution:
    """Resolve every named repository to exactly one location across the roots."""
    out = Resolution()
    for name in sorted(set(names)):
        found = [root / name for root in roots if (root / name / ".git").exists()]
        if not found:
            searched = ", ".join(str(r) for r in roots)
            out.problems.append(f"{name}: no repository found under any approved root ({searched})")
            continue
        heads = {_head(p) for p in found}
        if len(found) > 1 and len(heads - {""}) > 1:
            where = ", ".join(f"{p} @ {_head(p)[:10]}" for p in found)
            out.problems.append(f"{name}: {len(found)} incompatible locations — {where}")
            continue
        if not heads - {""}:
            out.problems.append(f"{name}: repository at {found[0]} has no resolvable HEAD")
            continue
        out.resolved[name] = found[0]
    return out


def preflight(names, roots: list[Path], label: str = "repository resolution") -> Resolution:
    """Run resolution and report it. The caller must stop when it fails."""
    resolution = resolve_repositories(names, roots)
    represented = len(set(names))
    print(f"{label:28}: {len(resolution.resolved)} of {represented} repositories resolved uniquely")
    if resolution.problems:
        for problem in resolution.problems:
            print(f"  FAIL  {problem}")
        print(f"{BLOCKED}: infrastructure failure, not a candidate verdict.")
        print("No candidate was validated and no scientific rejection was recorded.")
    return resolution
