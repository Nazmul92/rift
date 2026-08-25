"""BM-07 curation measurement: resolve targets and preservation surface exactly.

Four defects have been found in this layer, all in *measurement* rather than in
the product, and each one would have produced a corpus that looked fine and was
not:

1. **Target nodes could omit an enclosing class.** The node id was assembled from
   the diff alone, so a method added to a class that already existed produced
   `tests/test_cache.py::test_boundary` instead of
   `tests/test_cache.py::TestCache::test_boundary`. The class declaration is
   context the diff never carried.

2. **A nested test could alias a runnable one.** Matching added `def` lines to
   tests by name and span let a `def test_outer` defined *inside* `test_outer`
   resolve to the outer, collectable node. The nested function is not a pytest
   node at all, so the corpus would have carried a target that runs something
   else.

3. **Preservation counted tests the fix had modified.** "Existed at the parent"
   is not the property that matters: a test the fix rewrote is part of what
   changed, not evidence that anything was preserved.

4. **Class-level pure insertions did not taint their class.** Inserting a
   `setup_method`, a class attribute or an autouse fixture changes what every
   method in that class observes, while touching no test body. Deletion-based
   accounting called those tests untouched.

All four are fixed by reading structure rather than inferring it: files are
parsed with `ast` and **parent identity is tracked explicitly**, never recovered
by name, span or indentation. Anything that cannot be resolved unambiguously is
excluded rather than guessed. Stage C keeps final authority by running the node.

No model is called and no network is used.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace", timeout=300
    )
    return proc.stdout if proc.returncode == 0 else ""


def git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=300).returncode == 0


# --------------------------------------------------------------- structure


@dataclass(frozen=True)
class TestDef:
    """One runnable pytest test, with the source span it really occupies."""

    name: str
    cls: str | None
    start: int  # first decorator line if decorated, else the `def` line
    end: int
    def_line: int  # the `def` line itself, which is what a diff shows
    is_async: bool

    def node_id(self, path: str) -> str:
        return f"{path}::{self.cls}::{self.name}" if self.cls else f"{path}::{self.name}"


@dataclass
class Resolution:
    """A resolved target, or the reason it could not be resolved."""

    node_id: str | None
    method: str
    reason: str = ""


def _span(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> tuple[int, int]:
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return start, (node.end_lineno or node.lineno)


def collect_tests(source: str) -> list[TestDef]:
    """Every runnable pytest test in a module, with its enclosing class.

    Only two shapes are addressable as `path::name` or `path::Class::name`: a
    module-level function, and a method in the direct body of a *top-level*
    class. The module body is walked directly rather than with `ast.walk`, so a
    function nested inside another function, a method of a nested class, or a
    closure can never appear here — they have no such node id, and returning one
    for them is worse than returning nothing.
    """
    tree = ast.parse(source)
    out: list[TestDef] = []

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test"):
            start, end = _span(node)
            out.append(TestDef(node.name, None, start, end, node.lineno, isinstance(node, ast.AsyncFunctionDef)))
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name.startswith("test"):
                    start, end = _span(child)
                    out.append(
                        TestDef(
                            child.name,
                            node.name,
                            start,
                            end,
                            child.lineno,
                            isinstance(child, ast.AsyncFunctionDef),
                        )
                    )
    return out


def runnable_by_def_line(source: str) -> dict[int, TestDef]:
    """`def` line -> the runnable test declared there, keyed by exact position.

    Built from `collect_tests`, so only structurally runnable tests are present.
    A nested `def test_x` sitting inside another function has its own distinct
    `lineno` and simply is not a key, which is what stops it aliasing an outer
    test of the same name.
    """
    return {d.def_line: d for d in collect_tests(source)}


def class_spans(source: str) -> dict[str, tuple[int, int]]:
    """Top-level classes only — the ones whose methods pytest can address."""
    tree = ast.parse(source)
    return {n.name: _span(n) for n in tree.body if isinstance(n, ast.ClassDef)}


# ------------------------------------------------------------------ diffs


def added_def_lines(diff: str) -> set[int]:
    """New-file line numbers of `def`/`async def` lines the diff adds."""
    added: set[int] = set()
    new_line = 0
    for raw in diff.splitlines():
        m = HUNK.match(raw)
        if m:
            new_line = int(m.group(3))
            continue
        if raw.startswith(("+++", "---")):
            continue
        if raw.startswith("+"):
            if re.match(r"^\+\s*(async\s+)?def\s+test", raw):
                added.add(new_line)
            new_line += 1
        elif raw.startswith("-"):
            continue
        else:
            new_line += 1
    return added


def touched_old_lines(diff: str) -> set[int]:
    """Old-file line numbers the diff removes or rewrites."""
    touched: set[int] = set()
    for raw in diff.splitlines():
        m = HUNK.match(raw)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        if count:
            touched.update(range(start, start + count))
    return touched


def insertion_anchors(diff: str) -> set[int]:
    """Old-file lines that pure insertions land after.

    A pure insertion removes nothing, so deletions alone would call a test
    untouched after lines were added inside its body — or after a `setup_method`
    was added to its class. `@@ -a,0 +c,d @@` places the new lines after old
    line `a`.
    """
    return {
        int(m.group(1))
        for m in (HUNK.match(line) for line in diff.splitlines())
        if m and (int(m.group(2)) if m.group(2) is not None else 1) == 0
    }


# ------------------------------------------------------------- resolution


def resolve_targets(repo: Path, sha: str, test_files: list[str]) -> tuple[list[Resolution], list[Resolution]]:
    """Pytest node ids for the tests this commit adds, resolved from full source.

    The diff says *which* lines are new. The file at the fix commit says what
    they are part of. An added `def` line is bound to the AST node **declared at
    exactly that line**, and only resolves if that node is structurally runnable;
    there is no fallback to name or span matching, because that is precisely how
    a nested function came to alias the outer test sharing its name.
    """
    resolved: list[Resolution] = []
    excluded: list[Resolution] = []

    for path in test_files:
        source = git(repo, "show", f"{sha}:{path}")
        if not source.strip():
            excluded.append(Resolution(None, "unavailable", f"{path}: not present at the fix commit"))
            continue
        try:
            runnable = runnable_by_def_line(source)
        except SyntaxError as exc:
            excluded.append(Resolution(None, "unparsable", f"{path}: {exc.msg}"))
            continue

        for line in sorted(added_def_lines(git(repo, "show", "--format=", "-U0", sha, "--", path))):
            found = runnable.get(line)
            if found is None:
                excluded.append(
                    Resolution(
                        None,
                        "unresolvable",
                        f"{path}:{line}: the added test is not a module-level function or a method of a "
                        f"top-level class, so pytest has no node id for it",
                    )
                )
                continue
            resolved.append(
                Resolution(
                    found.node_id(path),
                    "ast_at_fix_commit" + ("_class_method" if found.cls else "_module_level"),
                )
            )
    return resolved, excluded


# ---------------------------------------------------------- preservation


@dataclass
class Preservation:
    nodes: list[str] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _affects(start: int, end: int, changed: set[int], anchors: set[int]) -> bool:
    """Did the fix change anything inside this span?

    Rewritten lines anywhere in the span count. An insertion counts only when it
    falls strictly inside: one anchored on the span's last line lands *after* the
    span — that is what appending a new test to a file looks like, and it leaves
    the previous test untouched.
    """
    rewritten = any(start <= line <= end for line in changed)
    inserted_inside = any(start <= anchor < end for anchor in anchors)
    return rewritten or inserted_inside


def class_level_changed(before: str, after: str) -> set[str]:
    """Top-level classes whose non-test body differs between two revisions.

    Insertion anchors cannot answer this. Appending a `setup_method` and
    appending a new test method land on the *same* anchor — the last line of the
    previous test — so a position-based rule must taint both or neither, and
    both answers are wrong. What distinguishes them is *what* was added.

    So the class body is compared structurally: every direct member that is not a
    test method — `setup_method`, `teardown_class`, attributes, autouse
    fixtures, shared helpers — is rendered to source and compared, along with
    base classes and class decorators. Any difference taints every test in that
    class, because all of them can observe it. Adding a test method changes none
    of those members and taints nothing.
    """

    def members(source: str) -> dict[str, tuple[str, ...]]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return {}
        out: dict[str, tuple[str, ...]] = {}
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            body = [
                ast.get_source_segment(source, child) or ast.dump(child)
                for child in node.body
                if not (isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name.startswith("test"))
            ]
            head = [ast.dump(b) for b in node.bases] + [ast.dump(d) for d in node.decorator_list]
            out[node.name] = tuple(head + body)
        return out

    old, new = members(before), members(after)
    return {name for name, body in old.items() if name in new and new[name] != body}


def preservation_candidates(repo: Path, sha: str, parent: str, test_files: list[str]) -> Preservation:
    """Tests that existed at the parent and the fix commit left alone.

    A candidate must exist at the parent, still exist at the fix commit, and have
    no changed line anywhere in its own span — decorators included. Two further
    rules, both deliberately conservative:

    * a change **or insertion** inside a top-level class but outside every test
      in it marks every test in that class touched. `setup_method`, a class
      attribute, an autouse fixture or a shared helper changes what those methods
      observe while touching no test body, so deletion-based accounting alone
      would wrongly call them untouched;
    * a change at module level outside any class marks nothing touched on its
      own. A helper being edited is not evidence that a particular test was.

    Tests added by the fix are never preservation candidates: they are what the
    fix asserts, not what it preserved. Appending a new method to a class anchors
    the insertion inside the previous test's span, so it does not taint the
    class — only genuinely class-level regions do.

    The **complete** set is returned. Callers must not truncate it: a candidate
    that passes the first eight nodes and breaks the thirty-seventh has not
    preserved behaviour, and a sampled surface cannot say so.
    """
    out = Preservation()
    for path in test_files:
        before = git(repo, "show", f"{parent}:{path}")
        after = git(repo, "show", f"{sha}:{path}")
        if not before.strip():
            continue  # the file is new; nothing in it pre-existed
        try:
            old_defs = collect_tests(before)
            new_defs = collect_tests(after) if after.strip() else []
            old_classes = class_spans(before)
        except SyntaxError:
            continue

        old_ids = {(d.cls, d.name) for d in old_defs}
        new_ids = {(d.cls, d.name) for d in new_defs}
        out.added += [d.node_id(path) for d in new_defs if (d.cls, d.name) not in old_ids]
        out.removed += [d.node_id(path) for d in old_defs if (d.cls, d.name) not in new_ids]

        diff = git(repo, "show", "--format=", "-U0", sha, "--", path)
        changed = touched_old_lines(diff)
        anchors = insertion_anchors(diff)
        if not changed and not anchors:
            out.nodes += [d.node_id(path) for d in old_defs if (d.cls, d.name) in new_ids]
            continue

        # Class-level behaviour is compared structurally rather than by line
        # position, because appending a helper and appending a test method share
        # an insertion anchor and must not share a verdict.
        tainted_classes = class_level_changed(before, after)
        # A rewritten region inside a class but outside every test is class-level
        # too, and position catches that because it deletes or changes lines.
        covered = {line for d in old_defs for line in range(d.start, d.end + 1)}
        tainted_classes |= {
            name
            for name, (cstart, cend) in old_classes.items()
            if any(cstart <= line <= cend and line not in covered for line in changed)
        }

        for d in old_defs:
            node = d.node_id(path)
            if (d.cls, d.name) not in new_ids:
                continue  # renamed or deleted: not the same test any more
            if _affects(d.start, d.end, changed, anchors) or (d.cls is not None and d.cls in tainted_classes):
                out.touched.append(node)
            else:
                out.nodes.append(node)
    return out


# ------------------------------------------------------------- provenance


def direct_parent_valid(repo: Path, sha: str, declared_parent: str) -> tuple[bool, str]:
    """`fix_commit^` must be the declared parent. Fail closed on everything else."""
    if not (repo / ".git").exists():
        return False, "repository missing"
    if not git_ok(repo, "cat-file", "-e", f"{sha}^{{commit}}"):
        return False, "fix commit unresolvable"
    parents = git(repo, "rev-list", "--parents", "-n", "1", sha).split()[1:]
    if not parents:
        return False, "root commit has no parent"
    if len(parents) > 1:
        return False, f"merge commit with {len(parents)} parents; not governed"
    actual = git(repo, "rev-parse", f"{sha}^").strip()
    if not actual:
        return False, "parent unresolvable"
    if actual != declared_parent:
        return False, f"declared parent {declared_parent[:10]} != fix_commit^ {actual[:10]}"
    return True, actual
