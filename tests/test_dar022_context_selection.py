"""Context selection sends complete, actionable source units.

The defect these tests exist to prevent was observed in a paid run, not
imagined. For `cachetools-c0fdf6ab` the selector sent **770 characters of a
23,272 character file** — lines 575-599 — because a regex matched
`class TLRUCache` on line 587 and a window of ±12 lines was drawn around it.
The class actually occupies lines 587-713, so `__setitem__`, the method the fix
had to change, was never shown. The model then invented the hunk context
(`self.__ttu(key, value, self.timer())` where the file says
`self.__ttu(key, value, time)`) and `git apply --check` rejected the patch at
every strip level.

The global budget was 60,000 characters. 1.3% of it was used.

A second asymmetry sat alongside it: arm A never runs diagnosis, so its only
baseline record carried a signature and no frames, and it was shown less source
than arms B and C for the same failure. The arms differed in what they could
see rather than in what they did with it.

Nothing here makes a model call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import build_repo

import riftagent.app as app

# A file too large to send whole, so the complete-definition path is exercised
# rather than the whole-file shortcut. `Target` sits after a large sibling, the
# way `TLRUCache` sits after `TTLCache`.
FILLER = "\n".join(f"    def pad_{i}(self):\n        return {i}\n" for i in range(900))
BIG_MODULE = f'''"""A module larger than the per-file budget."""


class Earlier:
{FILLER}


class Target:
    """The class under test."""

    def __init__(self, size):
        self.size = size

    def helper(self):
        return self.size

    def setitem(self, key, value, timer):
        expires = self.ttu(key, value, timer)
        if expires is None:
            raise ValueError("expiry must not be None")
        return expires
'''


def test_a_large_files_selection_is_the_complete_definition(tmp_path):
    """The cachetools regression, in miniature and by the same mechanism."""
    source = BIG_MODULE
    assert len(source) > app.MAX_FILE_CHARS, "the fixture must exceed the per-file budget"

    text, ranges, why = app.excerpt(source, cited=[], wanted={"Target"}, budget=app.MAX_FILE_CHARS)

    assert "class Target:" in text
    assert "def setitem" in text, "the method the fix would change was omitted"
    assert "expires = self.ttu(key, value, timer)" in text, "the real line was not sent"
    assert "complete definitions" in why
    # And not a narrow window around the declaration, which is what shipped.
    lo, hi = ranges[0]
    assert hi - lo > 12, f"a {hi - lo}-line window is the old defect"


def test_a_traceback_line_selects_its_whole_enclosing_definition(tmp_path):
    source = BIG_MODULE
    line = next(i for i, ln in enumerate(source.splitlines(), 1) if "raise ValueError" in ln)
    text, _, why = app.excerpt(source, cited=[line], wanted=set(), budget=app.MAX_FILE_CHARS)
    # Outermost, so the class rather than only the method: a frame inside a
    # method is usually fixed by seeing the class it belongs to.
    assert "class Target:" in text
    assert "def setitem" in text and "def helper" in text
    assert "encloses cited line" in why


def test_a_small_file_is_sent_whole(tmp_path):
    source = "def total():\n    return 4\n\n\nclass Small:\n    pass\n"
    text, ranges, why = app.excerpt(source, cited=[1], wanted=set(), budget=app.MAX_FILE_CHARS)
    assert text == source
    assert ranges == [(1, len(source.splitlines()))]
    assert "whole file" in why


def test_a_definition_larger_than_the_budget_falls_back_without_breaking_the_cap():
    huge = "class Huge:\n" + "\n".join(f"    x{i} = {i}" for i in range(5000))
    text, ranges, why = app.excerpt(huge, cited=[2500], wanted={"Huge"}, budget=2_000)
    assert len(text) <= 2_000 + 200, "the disclosure line is the only allowance"
    assert ranges, "a bounded window is the floor, not nothing"
    assert "bounded windows" in why
    assert "only the ranges above were sent" in text, "truncation must be disclosed"


def test_an_unparseable_file_still_yields_bounded_windows():
    broken = "def f(:\n" + "\n".join(f"line {i}" for i in range(400))
    text, ranges, why = app.excerpt(broken, cited=[200], wanted={"f"}, budget=1_000)
    assert ranges and "bounded windows" in why


def test_definition_spans_include_decorators():
    src = "import functools\n\n\n@functools.cache\ndef wrapped():\n    return 1\n"
    spans = app.definition_spans(src)
    start, end, name = next(s for s in spans if s[2] == "wrapped")
    assert name == "wrapped"
    assert "@functools.cache" in "\n".join(src.splitlines()[start - 1 : end])


def test_excerpt_is_deterministic():
    a = app.excerpt(BIG_MODULE, [10], {"Target"}, app.MAX_FILE_CHARS)
    b = app.excerpt(BIG_MODULE, [10], {"Target"}, app.MAX_FILE_CHARS)
    assert a == b


def test_no_line_is_sent_twice():
    """Overlapping units merge; a reader never sees a repeated region."""
    line = next(i for i, ln in enumerate(BIG_MODULE.splitlines(), 1) if "def setitem" in ln)
    _, ranges, _ = app.excerpt(BIG_MODULE, [line], {"Target"}, app.MAX_FILE_CHARS)
    for (a_lo, a_hi), (b_lo, b_hi) in zip(ranges, ranges[1:], strict=False):
        assert a_hi < b_lo, f"ranges {(a_lo, a_hi)} and {(b_lo, b_hi)} overlap"


# ------------------------------------------------------------ re-export / grep

REEXPORT = {
    "src/pkg/__init__.py": "",
    "src/pkg/plugins/__init__.py": "from pkg.plugins.other import SpecialThing\n\n__all__ = ['SpecialThing']\n",
    "src/pkg/plugins/other.py": (
        "class SpecialThing:\n    def render(self, data):\n        return data.decode() + 'x'\n"
    ),
    "tests/test_thing.py": (
        "from pkg.plugins import SpecialThing\n\n\ndef test_render():\n    assert SpecialThing().render(b'a') == 'ax'\n"
    ),
}

LAZY = {
    "src/pkg2/__init__.py": "",
    # No import statement names it: the package builds its map at run time, so
    # the AST hop cannot see it and only grep can.
    "src/pkg2/plugins/__init__.py": (
        "_MAP = {'Hidden': ('pkg2.plugins.impl', 'Hidden')}\n\n\ndef load(name):\n    return _MAP[name]\n"
    ),
    "src/pkg2/plugins/impl.py": "class Hidden:\n    def go(self):\n        return 1\n",
    "tests/test_hidden.py": "from pkg2.plugins import Hidden\n\n\ndef test_go():\n    assert Hidden().go() == 1\n",
}


def test_a_reexported_symbol_resolves_to_its_implementation(tmp_path):
    """`from pkg.plugins import SpecialThing` must reach `plugins/other.py`.

    Generic by construction — no repository is named in the implementation."""
    repo = build_repo(tmp_path / "reexport", REEXPORT)
    found = app.reexport_sources(repo, "src/pkg/plugins/__init__.py", {"SpecialThing"})
    assert "src/pkg/plugins/other.py" in found


def test_the_reexported_implementation_reaches_the_prompt(tmp_path):
    repo = build_repo(tmp_path / "reexport2", REEXPORT)
    sources, manifest = app.select_context(repo, "", "tests/test_thing.py::test_render", ())
    files = [rel for rel, _ in sources]
    assert "src/pkg/plugins/other.py" in files, files
    body = dict(sources)["src/pkg/plugins/other.py"]
    assert "def render" in body
    assert "src/pkg/plugins/other.py" in manifest["stages"]["reexports"]


def test_grep_finds_a_symbol_no_import_statement_names(tmp_path):
    """The frozen fallback stage. The package resolves its plugins at run time,
    so no AST hop can reach the implementation."""
    repo = build_repo(tmp_path / "lazy", LAZY)
    assert app.grep_definitions(repo, {"Hidden"}) == ["src/pkg2/plugins/impl.py"]

    sources, manifest = app.select_context(repo, "", "tests/test_hidden.py::test_go", ())
    files = [rel for rel, _ in sources]
    assert "src/pkg2/plugins/impl.py" in files, files
    assert "src/pkg2/plugins/impl.py" in manifest["stages"]["grep"]


def test_grep_runs_only_for_names_the_earlier_stages_left_unresolved(tmp_path):
    """Grep is the floor, not the strategy. A symbol the import graph explains
    must never reach it."""
    repo = build_repo(tmp_path / "resolved", REEXPORT)
    _, manifest = app.select_context(repo, "", "tests/test_thing.py::test_render", ())
    assert manifest["stages"]["grep"] == [], "grep ran for an already-resolved symbol"


def test_grep_is_bounded_and_deterministic(tmp_path):
    files = {f"src/mod{i}.py": f"class Thing{i}:\n    pass\n" for i in range(40)}
    files["tests/test_x.py"] = "def test_x():\n    assert True\n"
    repo = build_repo(tmp_path / "many", files)
    names = {f"Thing{i}" for i in range(40)}
    first = app.grep_definitions(repo, names)
    assert first == app.grep_definitions(repo, names), "grep is not deterministic"
    assert len(first) <= 3, "grep is not bounded"


# ------------------------------------------------------------------- budgets

BUDGET_FILES = {
    "src/pkg3/__init__.py": "",
    **{f"src/pkg3/mod{i}.py": f"class Big{i}:\n" + "\n".join(f"    v{j} = {j}" for j in range(4000)) for i in range(8)},
    "tests/test_budget.py": "from pkg3.mod0 import Big0\n\n\ndef test_b():\n    assert Big0\n",
}


def test_the_global_budget_is_never_exceeded(tmp_path):
    repo = build_repo(tmp_path / "budget", BUDGET_FILES)
    sources, manifest = app.select_context(repo, "", "tests/test_budget.py::test_b", ())
    assert manifest["chars"] <= app.MAX_CONTEXT_CHARS
    assert sum(len(t) for _, t in sources) <= app.MAX_CONTEXT_CHARS
    assert len(sources) <= app.MAX_CONTEXT_FILES


def test_the_per_file_budget_is_enforced(tmp_path):
    repo = build_repo(tmp_path / "perfile", BUDGET_FILES)
    sources, _ = app.select_context(repo, "", "tests/test_budget.py::test_b", ())
    for rel, text in sources:
        assert len(text) <= app.MAX_FILE_CHARS + 200, f"{rel} exceeded the per-file budget"


def test_selection_is_deterministic_for_identical_inputs(tmp_path):
    repo = build_repo(tmp_path / "det", REEXPORT)
    a = app.select_context(repo, "", "tests/test_thing.py::test_render", ())
    b = app.select_context(repo, "", "tests/test_thing.py::test_render", ())
    assert a == b


def test_no_file_is_selected_twice(tmp_path):
    repo = build_repo(tmp_path / "dupe", REEXPORT)
    sources, manifest = app.select_context(repo, "", "tests/test_thing.py::test_render", ())
    files = [rel for rel, _ in sources]
    assert len(files) == len(set(files))
    assert len(manifest["files"]) == len(set(manifest["files"]))


# -------------------------------------------------------------- arm A parity

PARITY = {
    "src/pkg4/__init__.py": "",
    "src/pkg4/calc.py": "def total():\n    return 4\n",
    "tests/test_calc.py": "from pkg4.calc import total\n\n\ndef test_total():\n    assert total() == 5\n",
}


def events_of(repo: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted((repo / ".rift").rglob("ledger.jsonl")):
        out.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return out


@pytest.mark.parametrize("arm", ["A", "C"])
def test_every_arm_records_the_same_baseline_failure_evidence(tmp_path, run_cli, arm):
    """Arm A must not be shown less source than B and C for the same failure.

    Arm A skips diagnosis, and diagnosis was the only place a `failure_excerpt`
    was recorded — so `_first_failure_text` fell back to the signature, which
    names no file, and arm A's context selection had no traceback to work from.
    """
    repo = build_repo(tmp_path / f"parity-{arm}", PARITY)
    extra = ["--model-alone"] if arm == "A" else []
    run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        "tests/test_calc.py::test_total",
        "--allow-partial-sandbox",
        "--no-model",
        *extra,
    )
    excerpts = [
        e["payload"]["failure_excerpt"]
        for e in events_of(repo)
        if e["kind"] == "check_result" and e["payload"].get("failure_excerpt")
    ]
    assert excerpts, f"arm {arm} recorded no baseline failure excerpt"
    assert "test_calc.py" in excerpts[0], f"arm {arm}'s excerpt names no source file"
