r"""BM-07 curation measurement, against real git history.

Two defects in the first curation pass, both in measurement rather than in the
product:

1. A target node id was assembled from the diff alone, so a method added to a
   class that **already existed** came out as `tests/t.py::test_x` instead of
   `tests/t.py::TestThing::test_x`. The diff shows `+    def test_x(self):` and
   nothing else; the class declaration is context it never carried.

2. Preservation surface counted every test that existed at the parent, including
   ones the fix commit rewrote. A test the fix modified cannot witness that a
   candidate preserved anything — it is part of what changed.

Both are measurement errors that would have produced a corpus whose targets do
not run and whose preservation counts overstate the surface. Neither touches
`src/riftagent`.

Every fixture here is a real repository built by `git`, so the resolution is
tested against the same bytes `git show` produces in curation rather than against
a hand-written diff.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from benchmark_modules import load  # noqa: E402

# Resolved from bm07's own file rather than by inserting its directory at
# sys.path[0], which used to leak into every later test in the session and
# silently give the BM-08 suites bm07's `validate_cases`.
curation = load("bm07", "curation")

added_def_lines = curation.added_def_lines
collect_tests = curation.collect_tests
direct_parent_valid = curation.direct_parent_valid
preservation_candidates = curation.preservation_candidates
resolve_targets = curation.resolve_targets
touched_old_lines = curation.touched_old_lines

ENV = {
    "GIT_AUTHOR_NAME": "bm07",
    "GIT_AUTHOR_EMAIL": "bm07@riftagent.invalid",
    "GIT_COMMITTER_NAME": "bm07",
    "GIT_COMMITTER_EMAIL": "bm07@riftagent.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str) -> str:
    import os

    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **ENV},
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr}")
    return proc.stdout


def history(tmp_path: Path, before: dict[str, str], after: dict[str, str]) -> tuple[Path, str, str]:
    """A two-commit repository: the parent, then the fix."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "master")
    for name, text in before.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "parent")
    parent = git(repo, "rev-parse", "HEAD").strip()

    for name, text in after.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "fix")
    return repo, git(repo, "rev-parse", "HEAD").strip(), parent


# ------------------------------------------------- target node resolution


CLASS_BEFORE = """\
class TestCache:
    def test_existing(self):
        assert True
"""
CLASS_AFTER = """\
class TestCache:
    def test_existing(self):
        assert True

    def test_boundary(self):
        assert True
"""


def test_a_method_added_to_an_existing_class_keeps_its_class(tmp_path):
    """The reported defect. The diff carries only the `def` line; the class name
    exists solely in the file at the fix commit."""
    repo, sha, _ = history(tmp_path, {"tests/test_cache.py": CLASS_BEFORE}, {"tests/test_cache.py": CLASS_AFTER})

    diff = git(repo, "show", "--format=", "-U0", sha, "--", "tests/test_cache.py")
    # git's hunk header happens to echo the enclosing class here, but that is a
    # heuristic that shows the nearest enclosing *definition* — for a method it
    # often shows the previous method instead. The declaration itself is not
    # part of the change, which is what makes diff-only assembly unsound.
    assert not any(ln.startswith("+class ") for ln in diff.splitlines()), (
        "premise: the fix does not add the class declaration"
    )

    resolved, excluded = resolve_targets(repo, sha, ["tests/test_cache.py"])
    assert excluded == []
    assert [r.node_id for r in resolved] == ["tests/test_cache.py::TestCache::test_boundary"]
    assert resolved[0].method == "ast_at_fix_commit_class_method"


def test_a_module_level_test_resolves_without_a_class(tmp_path):
    repo, sha, _ = history(
        tmp_path,
        {"tests/test_x.py": "def test_old():\n    assert True\n"},
        {"tests/test_x.py": "def test_old():\n    assert True\n\n\ndef test_new():\n    assert True\n"},
    )
    resolved, _ = resolve_targets(repo, sha, ["tests/test_x.py"])
    assert [r.node_id for r in resolved] == ["tests/test_x.py::test_new"]
    assert resolved[0].method == "ast_at_fix_commit_module_level"


def test_a_method_inside_a_newly_added_class_resolves(tmp_path):
    repo, sha, _ = history(
        tmp_path,
        {"tests/test_x.py": "def test_old():\n    assert True\n"},
        {
            "tests/test_x.py": (
                "def test_old():\n    pass\n\n\nclass TestNew:\n    def test_inner(self):\n        pass\n"
            )
        },
    )
    resolved, _ = resolve_targets(repo, sha, ["tests/test_x.py"])
    assert [r.node_id for r in resolved] == ["tests/test_x.py::TestNew::test_inner"]


def test_same_method_name_in_two_classes_stays_distinct(tmp_path):
    """Name alone is ambiguous; the `def` line number is not."""
    before = "class TestA:\n    def test_shared(self):\n        assert True\n\n\nclass TestB:\n    pass\n"
    after = (
        "class TestA:\n    def test_shared(self):\n        assert True\n\n\n"
        "class TestB:\n    def test_shared(self):\n        assert True\n"
    )
    repo, sha, _ = history(tmp_path, {"tests/test_x.py": before}, {"tests/test_x.py": after})
    resolved, excluded = resolve_targets(repo, sha, ["tests/test_x.py"])
    assert [r.node_id for r in resolved] == ["tests/test_x.py::TestB::test_shared"], (
        [r.node_id for r in resolved],
        [e.reason for e in excluded],
    )


def test_an_async_test_resolves(tmp_path):
    repo, sha, _ = history(
        tmp_path,
        {"tests/test_x.py": "def test_old():\n    assert True\n"},
        {"tests/test_x.py": "def test_old():\n    assert True\n\n\nasync def test_async_thing():\n    assert True\n"},
    )
    resolved, _ = resolve_targets(repo, sha, ["tests/test_x.py"])
    assert [r.node_id for r in resolved] == ["tests/test_x.py::test_async_thing"]
    assert [d.is_async for d in collect_tests("async def test_a():\n    pass\n")] == [True]


def test_decorators_above_a_test_do_not_shift_its_identity(tmp_path):
    after = (
        "import pytest\n\n\nclass TestThing:\n    def test_old(self):\n        assert True\n\n"
        "    @pytest.mark.parametrize('n', [1, 2])\n    @pytest.mark.slow\n"
        "    def test_decorated(self, n):\n        assert n\n"
    )
    before = "import pytest\n\n\nclass TestThing:\n    def test_old(self):\n        assert True\n"
    repo, sha, _ = history(tmp_path, {"tests/test_x.py": before}, {"tests/test_x.py": after})
    resolved, excluded = resolve_targets(repo, sha, ["tests/test_x.py"])
    assert [r.node_id for r in resolved] == ["tests/test_x.py::TestThing::test_decorated"], [e.reason for e in excluded]

    # The span starts at the first decorator, which is what preservation
    # accounting intersects against.
    d = next(t for t in collect_tests(after) if t.name == "test_decorated")
    assert after.splitlines()[d.start - 1].strip().startswith("@pytest.mark.parametrize")


def test_a_test_nested_beyond_one_class_is_excluded_not_guessed(tmp_path):
    """`Outer::Inner::test_x` is not a node id pytest collects from a diff-derived
    guess, so the case is dropped rather than described wrongly."""
    before = "class Outer:\n    pass\n"
    after = "class Outer:\n    class Inner:\n        def test_deep(self):\n            assert True\n"
    repo, sha, _ = history(tmp_path, {"tests/test_x.py": before}, {"tests/test_x.py": after})
    resolved, excluded = resolve_targets(repo, sha, ["tests/test_x.py"])
    assert resolved == []
    assert len(excluded) == 1 and "no node id" in excluded[0].reason


def test_a_test_defined_inside_a_function_is_excluded(tmp_path):
    before = "def helper():\n    pass\n"
    after = "def helper():\n    def test_inner():\n        assert True\n\n    return test_inner\n"
    repo, sha, _ = history(tmp_path, {"tests/test_x.py": before}, {"tests/test_x.py": after})
    resolved, excluded = resolve_targets(repo, sha, ["tests/test_x.py"])
    assert resolved == []
    assert excluded and "no node id" in excluded[0].reason


def test_every_resolved_node_exists_in_the_fix_commit_source(tmp_path):
    """The node must name something really there — the property the old
    extractor could violate."""
    repo, sha, _ = history(tmp_path, {"tests/test_cache.py": CLASS_BEFORE}, {"tests/test_cache.py": CLASS_AFTER})
    resolved, _ = resolve_targets(repo, sha, ["tests/test_cache.py"])
    source = git(repo, "show", f"{sha}:tests/test_cache.py")
    present = {d.node_id("tests/test_cache.py") for d in collect_tests(source)}
    assert {r.node_id for r in resolved} <= present


def test_added_def_lines_tracks_new_file_numbering(tmp_path):
    repo, sha, _ = history(tmp_path, {"tests/test_cache.py": CLASS_BEFORE}, {"tests/test_cache.py": CLASS_AFTER})
    diff = git(repo, "show", "--format=", "-U0", sha, "--", "tests/test_cache.py")
    assert added_def_lines(diff) == {5}


# ------------------------------------------------ preservation accounting


PRESERVE_BEFORE = """\
def test_a():
    assert 1 == 1


def test_b():
    assert 2 == 2


def test_c():
    assert 3 == 3


def test_d():
    assert 4 == 4
"""


def test_only_untouched_pre_existing_tests_are_preservation_candidates(tmp_path):
    """The reported defect, in the ruling's own shape: four pre-existing tests,
    the fix modifies two and adds one, so two remain."""
    after = (
        PRESERVE_BEFORE.replace("assert 1 == 1", "assert 1 == 1  # adjusted").replace(
            "assert 2 == 2", "assert 2 == 2  # adjusted"
        )
        + "\n\ndef test_new():\n    assert True\n"
    )
    repo, sha, parent = history(tmp_path, {"tests/t.py": PRESERVE_BEFORE}, {"tests/t.py": after})

    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert p.nodes == ["tests/t.py::test_c", "tests/t.py::test_d"]
    assert set(p.touched) == {"tests/t.py::test_a", "tests/t.py::test_b"}
    assert p.added == ["tests/t.py::test_new"]
    assert "tests/t.py::test_new" not in p.nodes, "an added test was counted as preservation"


def test_a_changed_line_inside_a_body_marks_the_test_touched(tmp_path):
    """The declaration line is unchanged; the body is not. Matching on the `def`
    line alone would call this untouched."""
    after = PRESERVE_BEFORE.replace("assert 3 == 3", "assert 3 == 3 or True")
    repo, sha, parent = history(tmp_path, {"tests/t.py": PRESERVE_BEFORE}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert "tests/t.py::test_c" in p.touched
    assert "tests/t.py::test_c" not in p.nodes


def test_lines_inserted_into_a_body_mark_the_test_touched(tmp_path):
    """A pure insertion deletes nothing, so intersecting only removed lines would
    miss it."""
    after = PRESERVE_BEFORE.replace("    assert 3 == 3\n", "    x = 3\n    assert x == 3\n")
    repo, sha, parent = history(tmp_path, {"tests/t.py": PRESERVE_BEFORE}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert "tests/t.py::test_c" in p.touched


def test_a_changed_decorator_marks_the_test_touched(tmp_path):
    before = (
        "import pytest\n\n\n@pytest.mark.slow\ndef test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n"
    )
    after = before.replace("@pytest.mark.slow", "@pytest.mark.skip")
    repo, sha, parent = history(tmp_path, {"tests/t.py": before}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert p.touched == ["tests/t.py::test_a"]
    assert p.nodes == ["tests/t.py::test_b"]


def test_a_renamed_test_is_not_a_preservation_candidate(tmp_path):
    after = PRESERVE_BEFORE.replace("def test_a():", "def test_a_renamed():")
    repo, sha, parent = history(tmp_path, {"tests/t.py": PRESERVE_BEFORE}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert "tests/t.py::test_a" not in p.nodes
    assert "tests/t.py::test_a" in p.removed
    assert "tests/t.py::test_a_renamed" in p.added


def test_a_deleted_test_is_not_a_preservation_candidate(tmp_path):
    after = PRESERVE_BEFORE.replace("def test_a():\n    assert 1 == 1\n\n\n", "")
    repo, sha, parent = history(tmp_path, {"tests/t.py": PRESERVE_BEFORE}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert "tests/t.py::test_a" not in p.nodes
    assert "tests/t.py::test_a" in p.removed


def test_a_module_level_helper_change_does_not_taint_the_tests(tmp_path):
    """A helper edited outside every test is not evidence that any particular
    test changed behaviour."""
    before = (
        "def helper():\n    return 1\n\n\ndef test_a():\n    assert helper() == 1\n\n\ndef test_b():\n    assert True\n"
    )
    after = before.replace("    return 1", "    return 1  # clarified")
    repo, sha, parent = history(tmp_path, {"tests/t.py": before}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert set(p.nodes) == {"tests/t.py::test_a", "tests/t.py::test_b"}
    assert p.touched == []


def test_a_class_level_change_taints_every_test_in_that_class(tmp_path):
    """`setUp`, a class attribute or a shared fixture changes what the methods
    observe, so the whole class is excluded conservatively — and a class the
    change did not touch is left alone."""
    before = (
        "class TestA:\n    value = 1\n\n    def test_one(self):\n        assert self.value == 1\n\n"
        "    def test_two(self):\n        assert True\n\n\n"
        "class TestB:\n    def test_three(self):\n        assert True\n"
    )
    after = before.replace("    value = 1", "    value = 2")
    repo, sha, parent = history(tmp_path, {"tests/t.py": before}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert set(p.touched) == {"tests/t.py::TestA::test_one", "tests/t.py::TestA::test_two"}
    assert p.nodes == ["tests/t.py::TestB::test_three"]


def test_an_untouched_file_contributes_all_of_its_pre_existing_tests(tmp_path):
    repo, sha, parent = history(
        tmp_path,
        {"tests/t.py": PRESERVE_BEFORE, "tests/other.py": "def test_z():\n    assert True\n"},
        {"tests/t.py": PRESERVE_BEFORE + "\n\ndef test_new():\n    assert True\n"},
    )
    p = preservation_candidates(repo, sha, parent, ["tests/other.py"])
    assert p.nodes == ["tests/other.py::test_z"]
    assert p.touched == []


def test_touched_old_lines_records_deletions_and_insertion_points(tmp_path):
    repo, sha, _ = history(
        tmp_path,
        {"tests/t.py": PRESERVE_BEFORE},
        {"tests/t.py": PRESERVE_BEFORE.replace("assert 1 == 1", "assert 1 == 1  # x")},
    )
    diff = git(repo, "show", "--format=", "-U0", sha, "--", "tests/t.py")
    assert touched_old_lines(diff) == {2}


# ------------------------------------------------------------ provenance


def test_the_direct_parent_invariant_holds_and_fails_closed(tmp_path):
    repo, sha, parent = history(tmp_path, {"tests/t.py": PRESERVE_BEFORE}, {"tests/t.py": PRESERVE_BEFORE + "\n"})
    ok, detail = direct_parent_valid(repo, sha, parent)
    assert ok and detail == parent

    ok, why = direct_parent_valid(repo, sha, "0" * 40)
    assert not ok and "!=" in why

    ok, why = direct_parent_valid(repo, parent, "0" * 40)
    assert not ok and "root commit" in why

    ok, why = direct_parent_valid(repo, "deadbeef" * 5, parent)
    assert not ok and "unresolvable" in why

    ok, why = direct_parent_valid(tmp_path / "nope", sha, parent)
    assert not ok and "missing" in why


def test_a_merge_commit_is_refused_until_separately_governed(tmp_path):
    repo, sha, parent = history(tmp_path, {"tests/t.py": PRESERVE_BEFORE}, {"tests/t.py": PRESERVE_BEFORE + "\n"})
    git(repo, "checkout", "-q", "-b", "side", parent)
    (repo / "side.py").write_text("y = 2\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "side")
    git(repo, "checkout", "-q", "master")
    git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")
    merge = git(repo, "rev-parse", "HEAD").strip()
    assert len(git(repo, "rev-list", "--parents", "-n", "1", merge).split()) == 3, "fixture is not a merge"

    ok, why = direct_parent_valid(repo, merge, sha)
    assert not ok and "merge commit" in why


def test_curation_tooling_touches_no_runtime_module():
    """This phase is measurement. If it imported the product it could change it."""
    source = Path(curation.__file__).read_text(encoding="utf-8")
    assert "riftagent" not in source
    for banned in ("import riftagent", "from riftagent"):
        assert banned not in source


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def test_a():\n    pass\n", [(None, "test_a")]),
        ("class TestX:\n    def test_a(self):\n        pass\n", [("TestX", "test_a")]),
        ("class TestX:\n    class Inner:\n        def test_a(self):\n            pass\n", []),
        ("def helper():\n    pass\n", []),
        ("async def test_a():\n    pass\n", [(None, "test_a")]),
    ],
    ids=["module", "method", "nested-class", "not-a-test", "async"],
)
def test_only_runnable_shapes_are_collected(source: str, expected: list[tuple[str | None, str]]):
    assert [(d.cls, d.name) for d in collect_tests(source)] == expected


# ------------------------------------ nested targets must not alias runnable ones


def test_a_nested_test_never_aliases_the_outer_test_of_the_same_name(tmp_path):
    """A `def test_outer` defined inside `test_outer` shares its name and sits
    inside its span, so name-and-span matching resolved it to the outer,
    collectable node — a target that runs something else entirely.

    Added `def` lines now bind to the AST node declared at exactly that line,
    with no fallback."""
    before = "def test_outer():\n    x = 1\n    assert x\n"
    after = "def test_outer():\n    x = 1\n\n    def test_outer():\n        return 2\n\n    assert x\n"
    repo, sha, _ = history(tmp_path, {"tests/test_x.py": before}, {"tests/test_x.py": after})

    resolved, excluded = resolve_targets(repo, sha, ["tests/test_x.py"])
    assert resolved == [], [r.node_id for r in resolved]
    assert len(excluded) == 1 and "no node id" in excluded[0].reason


def test_a_nested_test_inside_a_method_is_refused(tmp_path):
    before = "class TestThing:\n    def test_a(self):\n        assert True\n"
    after = (
        "class TestThing:\n    def test_a(self):\n        def test_a():\n            return 1\n\n        assert True\n"
    )
    repo, sha, _ = history(tmp_path, {"tests/test_x.py": before}, {"tests/test_x.py": after})
    resolved, excluded = resolve_targets(repo, sha, ["tests/test_x.py"])
    assert resolved == []
    assert excluded and "no node id" in excluded[0].reason


def test_a_class_qualified_target_still_resolves_after_the_nesting_fix(tmp_path):
    """The narrowing must not cost the case it was built for."""
    repo, sha, _ = history(tmp_path, {"tests/test_cache.py": CLASS_BEFORE}, {"tests/test_cache.py": CLASS_AFTER})
    resolved, excluded = resolve_targets(repo, sha, ["tests/test_cache.py"])
    assert [r.node_id for r in resolved] == ["tests/test_cache.py::TestCache::test_boundary"]
    assert excluded == []


# ---------------------------------------- class-level pure insertions taint


CLASS_TESTS = """\
class TestUser:
    def test_name(self):
        assert True

    def test_age(self):
        assert True
"""


def test_an_inserted_setup_method_taints_every_test_in_the_class(tmp_path):
    """No test body changes, yet both methods can now behave differently. A
    deletion-based rule called them untouched."""
    after = (
        "class TestUser:\n    def setup_method(self):\n        self.user = 1\n\n"
        "    def test_name(self):\n        assert True\n\n    def test_age(self):\n        assert True\n"
    )
    repo, sha, parent = history(tmp_path, {"tests/t.py": CLASS_TESTS}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert p.nodes == []
    assert set(p.touched) == {"tests/t.py::TestUser::test_name", "tests/t.py::TestUser::test_age"}


def test_an_inserted_class_attribute_taints_every_test_in_the_class(tmp_path):
    after = CLASS_TESTS.replace("class TestUser:\n", "class TestUser:\n    limit = 5\n")
    repo, sha, parent = history(tmp_path, {"tests/t.py": CLASS_TESTS}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert p.nodes == []
    assert len(p.touched) == 2


def test_an_inserted_class_helper_taints_every_test_in_the_class(tmp_path):
    after = CLASS_TESTS + "    def _helper(self):\n        return 1\n"
    repo, sha, parent = history(tmp_path, {"tests/t.py": CLASS_TESTS}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert p.nodes == [], p.nodes


def test_a_body_edit_taints_only_that_test(tmp_path):
    """The conservative class rule must not swallow the precise case."""
    after = CLASS_TESTS.replace(
        "    def test_name(self):\n        assert True", "    def test_name(self):\n        assert 1 == 1"
    )
    repo, sha, parent = history(tmp_path, {"tests/t.py": CLASS_TESTS}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert p.touched == ["tests/t.py::TestUser::test_name"]
    assert p.nodes == ["tests/t.py::TestUser::test_age"]


def test_appending_a_new_test_method_does_not_taint_its_siblings(tmp_path):
    """Adding a test is not changing shared class behaviour, and treating it as
    such would empty the preservation surface of every case that adds a method."""
    after = CLASS_TESTS + "\n    def test_email(self):\n        assert True\n"
    repo, sha, parent = history(tmp_path, {"tests/t.py": CLASS_TESTS}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert set(p.nodes) == {"tests/t.py::TestUser::test_name", "tests/t.py::TestUser::test_age"}
    assert p.added == ["tests/t.py::TestUser::test_email"]
    assert "tests/t.py::TestUser::test_email" not in p.nodes


def test_the_complete_preservation_set_is_returned_untruncated(tmp_path):
    """20 discovered, 20 returned. A sampled surface cannot witness a candidate
    that breaks node 17."""
    body = "".join(f"def test_{i:02d}():\n    assert True\n\n\n" for i in range(20))
    after = body + "def test_new():\n    assert True\n"
    repo, sha, parent = history(tmp_path, {"tests/t.py": body}, {"tests/t.py": after})
    p = preservation_candidates(repo, sha, parent, ["tests/t.py"])
    assert len(p.nodes) == 20, len(p.nodes)
    assert p.nodes[-1] == "tests/t.py::test_19"
