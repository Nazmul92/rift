"""Semantics-preserving unified-diff metadata normalisation. Deterministic, offline.

The only edits permitted are to diff *control* metadata:

  * hunk `@@ -a,b +c,d @@` counts recomputed from the hunk's own body;
  * a missing trailing newline added.

The content lines — every context, added and deleted line, with its prefix and
its bytes — are copied through untouched. `semantic_lines()` extracts exactly
that sequence, and `normalize()` guarantees it is byte-identical before and
after. A normalisation that cannot hold that guarantee returns UNSAFE instead.

What this deliberately does **not** do, because each would be guessing at intent
rather than correcting a representation:

  * supply a missing `+`/`-`/space prefix on a content line;
  * change, add or drop any content line;
  * relocate a hunk, or adjust its start line to make it fit;
  * consult the repository to reconstruct context the model got wrong;
  * ask a model anything.

A patch is normalised or it is refused. There is no partial repair.
"""

from __future__ import annotations

import re

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

SAFE = "NORMALIZED"
UNCHANGED = "ALREADY_VALID"
UNSAFE = "NORMALIZATION_UNSAFE"


def semantic_lines(diff: str) -> list[str]:
    """Every content line of every hunk, in order, with prefix and bytes intact.

    This is the model's proposal. Normalisation must leave it identical; the
    regression asserts exactly that.
    """
    out: list[str] = []
    inside = False
    for line in diff.splitlines(keepends=True):
        if line.startswith("@@"):
            inside = True
            continue
        if line.startswith(("diff --git", "index ", "--- ", "+++ ", "old mode", "new mode", "similarity", "rename")):
            inside = False
            continue
        if inside:
            out.append(line)
    return out


def normalize(diff: str) -> tuple[str, str, list[str]]:
    """`(normalised diff, status, notes)`.

    UNSAFE is returned rather than a best guess whenever a body line carries no
    diff prefix, a header will not parse, or a hunk has no body at all.
    """
    notes: list[str] = []
    lines = diff.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    changed = False

    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            out.append(line)
            i += 1
            continue

        m = HUNK.match(line.rstrip("\n"))
        if not m:
            return diff, UNSAFE, [f"hunk header will not parse: {line.strip()!r}"]

        body: list[str] = []
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if nxt.startswith("@@") or nxt.startswith(("diff --git", "--- ", "+++ ", "index ")):
                break
            if nxt[:1] not in (" ", "+", "-", "\\"):
                # A content line with no prefix. Deciding whether it is context
                # or an addition is deciding what the model meant.
                return diff, UNSAFE, [f"body line {j + 1} has no diff prefix: {nxt[:40]!r}"]
            body.append(nxt)
            j += 1

        if not body:
            return diff, UNSAFE, [f"hunk at line {i + 1} has no body"]

        old = sum(1 for b in body if b[:1] in (" ", "-"))
        new = sum(1 for b in body if b[:1] in (" ", "+"))
        old_start, new_start, tail = int(m.group(1)), int(m.group(3)), m.group(5)
        stated_old = int(m.group(2)) if m.group(2) is not None else 1
        stated_new = int(m.group(4)) if m.group(4) is not None else 1

        if (stated_old, stated_new) != (old, new):
            notes.append(f"hunk at {old_start}: counts -{stated_old},+{stated_new} -> -{old},+{new}")
            changed = True

        out.append(f"@@ -{old_start},{old} +{new_start},{new} @@{tail}\n")
        out.extend(body)
        i = j

    text = "".join(out)
    if not text.endswith("\n"):
        # A diff whose final content line has no newline is *not* a metadata
        # defect. In unified-diff a missing final newline is stated explicitly
        # with `\\ No newline at end of file`, so appending one changes what the
        # patch says about the file's last byte — and the invariant check below
        # catches it, which is how this allowance was found and removed rather
        # than the invariant being loosened to accommodate it.
        return diff, UNSAFE, ["final content line has no newline; that is content, not metadata"]

    if semantic_lines(text) != semantic_lines(diff):
        # Belt and braces: the invariant is asserted here as well as in tests,
        # because a normaliser that silently altered content would be worse than
        # one that failed.
        return diff, UNSAFE, ["normalisation would have altered content lines"]

    return text, (SAFE if changed else UNCHANGED), notes
