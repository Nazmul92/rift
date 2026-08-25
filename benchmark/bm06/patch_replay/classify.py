"""Classify each frozen candidate patch. Deterministic, offline, read-only.

Post-hoc diagnostic over the completed preliminary run. Nothing here writes to
the benchmark's own artifacts: the records, patches, verdicts and identities are
immutable inputs.
"""

from __future__ import annotations

import re

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

STRUCTURALLY_INVALID = "STRUCTURALLY_INVALID"
PARSEABLE_NON_APPLICABLE = "PARSEABLE_NON_APPLICABLE"
OTHER = "OTHER"


def hunks(diff: str) -> list[dict]:
    """Every hunk, with its header counts and its body lines."""
    out: list[dict] = []
    current: dict | None = None
    path = ""
    for line in diff.splitlines(keepends=True):
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current = None
        elif line.startswith("@@"):
            m = HUNK.match(line)
            current = {
                "path": path,
                "header": line,
                "match": bool(m),
                "old_start": int(m.group(1)) if m else 0,
                "old_count": (int(m.group(2)) if m and m.group(2) is not None else (1 if m else 0)),
                "new_start": int(m.group(3)) if m else 0,
                "new_count": (int(m.group(4)) if m and m.group(4) is not None else (1 if m else 0)),
                "body": [],
            }
            out.append(current)
        elif current is not None and line[:1] in (" ", "+", "-", "\\"):
            current["body"].append(line)
        elif current is not None and line.strip() == "":
            # A bare empty line inside a hunk is a context line whose single
            # leading space the generator dropped. Recorded as-is; whether that
            # is safe to normalise is decided later, not here.
            current["body"].append(line)
    return out


def counted(body: list[str]) -> tuple[int, int]:
    """Old-side and new-side line counts implied by a hunk body."""
    old = sum(1 for ln in body if ln[:1] in (" ", "-") or ln.strip() == "")
    new = sum(1 for ln in body if ln[:1] in (" ", "+") or ln.strip() == "")
    return old, new


def classify(diff: str) -> tuple[str, list[str]]:
    """`(classification, reasons)` for one candidate patch."""
    reasons: list[str] = []
    hs = hunks(diff)
    if not hs:
        return STRUCTURALLY_INVALID, ["no parseable hunk header"]

    for i, h in enumerate(hs):
        if not h["match"]:
            reasons.append(f"hunk {i}: malformed header {h['header'].strip()!r}")
            continue
        old, new = counted(h["body"])
        if old != h["old_count"] or new != h["new_count"]:
            reasons.append(
                f"hunk {i} ({h['path']}): header says -{h['old_count']} +{h['new_count']}, body has -{old} +{new}"
            )
        bare = [ln for ln in h["body"] if ln.strip() == "" and ln not in ("\n", "\r\n", "")]
        if bare:
            reasons.append(f"hunk {i}: {len(bare)} line(s) missing a diff prefix")

    if not diff.endswith("\n"):
        reasons.append("no trailing newline")

    return (STRUCTURALLY_INVALID, reasons) if reasons else (PARSEABLE_NON_APPLICABLE, [])
