"""Cited lines outrank imports; used dependencies are followed; runtimes and
baseline trees are bound to what actually executes.

Four defects, each found by an audit that had itself been corrected first.

* **dateutil.** The required region is `EPOCH = ...` at *module level* in
  `tz/tz.py`. `enclosing_unit` correctly returns None there, and the previous
  implementation then contributed nothing for the cited line — so imported class
  definitions filled the entire per-file budget and the one region the traceback
  named was the one region not sent. Selected ranges began at line 41; the fix
  needed 34-40.
* **icalendar.** `timezone/__init__.py` was selected and contains
  `from .tzp import TZP` followed by `tzp = TZP()`. The required region is in
  `tzp.py`, which nothing followed: resolution chased the test's own symbol into
  `tzid.py` and stopped.
* **Runtime binding.** Hashing bytes at startup says nothing about which
  `riftagent` a subprocess imports. An installed copy earlier on `sys.path`
  would run while the frozen hash described source that never executed.
* **Baseline binding.** A case is not merely its parent commit: several lay the
  fix commit's test half over the parent's source, so `git rev-parse HEAD` is
  true and insufficient.

Nothing here makes a model call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import build_repo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark" / "bm06"))

import driver as d  # noqa: E402

import riftagent.app as app  # noqa: E402

# ------------------------------------------------------- cited-line priority

BIG_CLASS = "\n".join(f"    def pad_{i}(self):\n        return {i}\n" for i in range(900))
MODULE_LEVEL = f'''"""A module where the failure is not inside any definition."""

import datetime

ZERO = datetime.timedelta(0)
EPOCH = datetime.datetime.utcfromtimestamp(0)
EPOCHORDINAL = EPOCH.toordinal()


class Imported:
{BIG_CLASS}
'''


def test_a_cited_module_level_line_is_not_displaced_by_imported_definitions():
    """The dateutil defect, by its own mechanism.

    The cited line is at module level and the imported class is large enough to
    consume the whole per-file budget. The cited region must survive."""
    assert len(MODULE_LEVEL) > app.MAX_FILE_CHARS
    cited = next(i for i, ln in enumerate(MODULE_LEVEL.splitlines(), 1) if ln.startswith("EPOCH ="))

    text, ranges, why = app.excerpt(MODULE_LEVEL, cited=[cited], wanted={"Imported"}, budget=app.MAX_FILE_CHARS)

    assert "EPOCH = datetime.datetime.utcfromtimestamp(0)" in text, "the cited module-level region was discarded"
    assert any(lo <= cited <= hi for lo, hi in ranges), ranges
    assert "module level" in why


def test_a_cited_line_inside_a_definition_still_selects_the_whole_definition():
    """The module-level rule must not cost the enclosing-definition rule.

    The class here fits the per-file budget; one that does not still falls back
    to a bounded window, which `test_a_definition_larger_than_the_budget_...`
    covers."""
    pad = "\n".join("# filler " + "x" * 60 for _ in range(400))
    source = (
        pad
        + "\n\nclass Enclosing:\n    def first(self):\n        return 1\n\n"
        + "    def target(self):\n        return self.first() + 1\n\n"
        + "    def last(self):\n        return 3\n\n"
        + pad
    )
    assert len(source) > app.MAX_FILE_CHARS
    cited = next(i for i, ln in enumerate(source.splitlines(), 1) if "return self.first() + 1" in ln)

    text, _, why = app.excerpt(source, cited=[cited], wanted=set(), budget=app.MAX_FILE_CHARS)
    assert "class Enclosing:" in text
    assert "def first" in text and "def last" in text, "the enclosing definition was cut short"
    assert "encloses cited line" in why


def test_cited_regions_outrank_imported_definitions_under_pressure():
    """Both compete for one budget; the cited one must win."""
    cited = next(i for i, ln in enumerate(MODULE_LEVEL.splitlines(), 1) if ln.startswith("EPOCH ="))
    text, _, _ = app.excerpt(MODULE_LEVEL, cited=[cited], wanted={"Imported"}, budget=1_200)
    assert "EPOCH =" in text
    assert "def pad_400" not in text, "a lower-priority definition displaced the cited region"


def test_selected_ranges_are_emitted_in_file_order():
    """Priority decides what is kept, not what order a reader sees it in."""
    cited = next(i for i, ln in enumerate(MODULE_LEVEL.splitlines(), 1) if ln.startswith("EPOCH ="))
    _, ranges, _ = app.excerpt(MODULE_LEVEL, cited=[cited], wanted={"Imported"}, budget=app.MAX_FILE_CHARS)
    assert ranges == sorted(ranges)


# ------------------------------------------------- used-dependency traversal

PACKAGE = {
    "src/pkg/__init__.py": "",
    "src/pkg/zone/__init__.py": (
        "from .zid import from_dt\nfrom .zp import ZP\n\nzp = ZP()\n\n\ndef use_a():\n    zp.use_a()\n"
    ),
    "src/pkg/zone/zid.py": "def from_dt(dt):\n    return getattr(dt, 'tzname', lambda: None)()\n",
    "src/pkg/zone/zp.py": (
        "class ZP:\n"
        "    def __init__(self):\n        self.impl = 'a'\n\n"
        "    def use_a(self):\n        self.impl = 'a'\n\n"
        "    def timezone(self, tzid):\n        return tzid\n"
    ),
    "src/pkg/unused.py": "class NeverReferenced:\n    pass\n",
    "tests/test_zone.py": "from pkg.zone import from_dt\n\n\ndef test_dt():\n    assert from_dt(object()) is None\n",
}


def test_a_used_local_import_is_followed(tmp_path: Path):
    """`zone/__init__.py` imports ZP and constructs it, so `zp.py` is a
    dependency the selected code demonstrably relies on."""
    repo = build_repo(tmp_path / "pkg", PACKAGE)
    assert "src/pkg/zone/zp.py" in app.used_dependencies(repo, "src/pkg/zone/__init__.py")


def test_the_used_dependency_reaches_the_prompt(tmp_path: Path):
    """The icalendar regression, generically: __init__ uses ZP -> zp.py
    selected -> the method the fix would change is visible."""
    repo = build_repo(tmp_path / "pkg2", PACKAGE)
    sources, manifest = app.select_context(repo, "", "tests/test_zone.py::test_dt", ())
    files = [rel for rel, _ in sources]
    assert "src/pkg/zone/zp.py" in files, files
    assert "def timezone" in dict(sources)["src/pkg/zone/zp.py"]
    assert "src/pkg/zone/zp.py" in manifest["stages"]["used_dependencies"]


def test_an_unreferenced_import_is_not_followed(tmp_path: Path):
    """Imported but never used is not a dependency the code relies on. Without
    this the traversal is an import crawl."""
    files = dict(PACKAGE)
    files["src/pkg/zone/__init__.py"] = "from .zid import from_dt\nfrom ..unused import NeverReferenced\n"
    repo = build_repo(tmp_path / "pkg3", files)
    assert "src/pkg/unused.py" not in app.used_dependencies(repo, "src/pkg/zone/__init__.py")


def test_only_local_imports_are_followed(tmp_path: Path):
    files = dict(PACKAGE)
    files["src/pkg/zone/__init__.py"] = "import json\nimport datetime\n\nx = json.dumps({})\ny = datetime.date\n"
    repo = build_repo(tmp_path / "pkg4", files)
    assert app.used_dependencies(repo, "src/pkg/zone/__init__.py") == []


def test_traversal_is_deterministic_and_deduplicated(tmp_path: Path):
    repo = build_repo(tmp_path / "pkg5", PACKAGE)
    first = app.select_context(repo, "", "tests/test_zone.py::test_dt", ())
    assert first == app.select_context(repo, "", "tests/test_zone.py::test_dt", ())
    files = first[1]["files"]
    assert len(files) == len(set(files))


def test_traversal_respects_the_budgets(tmp_path: Path):
    repo = build_repo(tmp_path / "pkg6", PACKAGE)
    _sources, manifest = app.select_context(repo, "", "tests/test_zone.py::test_dt", ())
    assert manifest["chars"] <= app.MAX_CONTEXT_CHARS
    assert len(manifest["files"]) <= app.MAX_CONTEXT_FILES


# ------------------------------------------------------- baseline tree hash


def tree(tmp_path: Path, name: str = "wt") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_mod.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    return root


def test_a_source_change_changes_the_baseline_identity(tmp_path: Path):
    root = tree(tmp_path)
    before = d.baseline_tree_hash(root)
    (root / "src" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    assert d.baseline_tree_hash(root) != before


def test_a_frozen_test_change_changes_the_baseline_identity(tmp_path: Path):
    """The judge is part of the tree that executes. A case carrying the fix
    commit's test half is exactly why the commit id is not enough."""
    root = tree(tmp_path)
    before = d.baseline_tree_hash(root)
    (root / "tests" / "test_mod.py").write_text("def test_x():\n    assert False\n", encoding="utf-8")
    assert d.baseline_tree_hash(root) != before


def test_an_untracked_execution_relevant_file_changes_the_identity(tmp_path: Path):
    """A stray module on the import path changes what runs, tracked or not."""
    root = tree(tmp_path)
    before = d.baseline_tree_hash(root)
    (root / "conftest.py").write_text("import sys\n", encoding="utf-8")
    assert d.baseline_tree_hash(root) != before


@pytest.mark.parametrize(
    "rel",
    [
        "__pycache__/mod.cpython-312.pyc",
        ".pytest_cache/CACHEDIR.TAG",
        ".rift/tasks/t/ledger.jsonl",
        "src/mod.pyc",
        ".mypy_cache/x.json",
    ],
)
def test_transient_state_does_not_change_the_identity(tmp_path: Path, rel: str):
    root = tree(tmp_path)
    before = d.baseline_tree_hash(root)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("transient\n", encoding="utf-8")
    assert d.baseline_tree_hash(root) == before, f"{rel} altered the baseline identity"


def test_a_reset_restores_the_exact_baseline_identity(tmp_path: Path):
    root = tmp_path / "git-wt"
    root.mkdir()

    def g(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout.strip()

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (root / "mod.py").write_text("x = 1\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "base")
    frozen = d.baseline_tree_hash(root)

    (root / "mod.py").write_text("x = 999\n", encoding="utf-8")
    (root / "stray.py").write_text("y = 1\n", encoding="utf-8")
    assert d.baseline_tree_hash(root) != frozen

    g("checkout", "--", ".")
    g("clean", "-qfd")
    assert d.baseline_tree_hash(root) == frozen


def test_the_exclusion_set_is_documented_and_narrow():
    """Excluding a file because it is inconvenient is how a tree hash stops
    meaning "the tree that ran"."""
    assert ".git" in d.BASELINE_EXCLUDE_DIRS and ".rift" in d.BASELINE_EXCLUDE_DIRS
    assert "__pycache__" in d.BASELINE_EXCLUDE_DIRS
    assert ".pyc" in d.BASELINE_EXCLUDE_SUFFIXES
    # Nothing execution-relevant is excluded.
    for name in ("src", "tests", "conftest.py", "setup.cfg", "pyproject.toml"):
        assert name not in d.BASELINE_EXCLUDE_DIRS


# --------------------------------------------------- runtime execution binding


def runtime_tree(tmp_path: Path, body: str = "VERSION = 1\n") -> Path:
    root = tmp_path / "rt"
    pkg = root / "src" / "riftagent"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(body, encoding="utf-8")
    return root


def test_an_unchanged_runtime_that_resolves_correctly_passes(tmp_path: Path):
    root = runtime_tree(tmp_path)
    frozen, _ = d.runtime_hash(root)
    env = d.runtime_env(root)
    d.assert_runtime(root, frozen, env, "in a test")  # must not raise


def test_a_mutated_runtime_is_refused_before_execution(tmp_path: Path):
    root = runtime_tree(tmp_path)
    frozen, _ = d.runtime_hash(root)
    (root / "src" / "riftagent" / "__init__.py").write_text("VERSION = 2\n", encoding="utf-8")
    with pytest.raises(d.RuntimeDrift, match="governed runtime changed"):
        d.assert_runtime(root, frozen, d.runtime_env(root), "before an arm")


def test_a_different_installed_runtime_resolving_first_is_refused(tmp_path: Path):
    """Bytes on disk being correct says nothing about which copy gets imported."""
    root = runtime_tree(tmp_path)
    frozen, _ = d.runtime_hash(root)

    other = tmp_path / "site" / "riftagent"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("VERSION = 'impostor'\n", encoding="utf-8")

    env = d.runtime_env(root)
    # The impostor is ahead of the intended tree on the path.
    env["PYTHONPATH"] = str(tmp_path / "site") + os.pathsep + env["PYTHONPATH"]
    with pytest.raises(d.RuntimeDrift, match="resolves to"):
        d.assert_runtime(root, frozen, env, "before an arm")


def test_the_pinned_environment_selects_the_intended_runtime(tmp_path: Path):
    root = runtime_tree(tmp_path)
    landed = d.resolves_to(root, d.runtime_env(root))
    assert landed, "riftagent did not import at all"
    assert Path(landed).resolve().is_relative_to((root / "src" / "riftagent").resolve())


def test_drift_during_an_arm_is_detected_afterwards(tmp_path: Path):
    """The window between "checked" and "ran" is the one that matters."""
    root = runtime_tree(tmp_path)
    frozen, _ = d.runtime_hash(root)
    env = d.runtime_env(root)
    d.assert_runtime(root, frozen, env, "before")

    # ... the arm runs, and something edits the runtime underneath it ...
    (root / "src" / "riftagent" / "__init__.py").write_text("VERSION = 3\n", encoding="utf-8")

    with pytest.raises(d.RuntimeDrift):
        d.assert_runtime(root, frozen, env, "during")


# ------------------------------------- frozen baselines and the bound wrapper

# The last provenance gap: an expected tree measured at startup answers a
# different question than one frozen at curation. A tree that drifted before the
# run began would be frozen in its drifted state and every later check would
# agree with it.


def test_every_valid_case_carries_a_frozen_baseline_tree_hash():
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "benchmark" / "bm06" / "manifest-preliminary.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(manifest["cases"]) == 8
    for case in manifest["cases"]:
        digest = case.get("baseline_tree_hash")
        assert digest and len(digest) == 64, case["case_id"]
    # Eight distinct trees; a repeated hash would mean two cases share a tree.
    assert len({c["baseline_tree_hash"] for c in manifest["cases"]}) == 8
    assert manifest["baseline_identity"]["excluded_dirs"]


def test_validation_rejects_a_case_with_no_frozen_baseline(tmp_path: Path):
    case = {
        "case_id": "c1",
        "repo": "demo",
        "parent": "a" * 40,
        "commit": "b" * 40,
        "target": "t.py::t",
        "signature": "E: m",
        "preserve": ["t.py::p"],
        "worktree": str(tmp_path),
        "cause_class": "genuine_source_bug",
        "label": "l",
    }
    manifest = {
        "arms": {a: {"description": "x", **({"seed": 1} if a == "B" else {})} for a in ("A", "B", "C")},
        "budget": {"scope": "s", "max_usd": 1.0},
        "model": {"id": "m", "max_probes": 1, "max_attempts": 1, "max_commands": 1, "max_output_tokens": 1},
        "cases": [case],
    }
    failures = d.validate_manifest(manifest, tmp_path, None)
    assert any("no baseline_tree_hash" in f for f in failures)


def test_validation_rejects_a_drifted_baseline(tmp_path: Path):
    """All eight are proven before the first request. A corpus that fails on
    case six has already been paid for through case five."""
    root = tree(tmp_path, "wt")
    frozen = d.baseline_tree_hash(root)
    (root / "src" / "mod.py").write_text("x = 999\n", encoding="utf-8")

    case = {
        "case_id": "c1",
        "repo": "demo",
        "parent": "a" * 40,
        "commit": "b" * 40,
        "target": "t.py::t",
        "signature": "E: m",
        "preserve": ["t.py::p"],
        "worktree": str(root),
        "cause_class": "genuine_source_bug",
        "label": "l",
        "baseline_tree_hash": frozen,
    }
    manifest = {
        "arms": {a: {"description": "x", **({"seed": 1} if a == "B" else {})} for a in ("A", "B", "C")},
        "budget": {"scope": "s", "max_usd": 1.0},
        "model": {"id": "m", "max_probes": 1, "max_attempts": 1, "max_commands": 1, "max_output_tokens": 1},
        "cases": [case],
    }
    failures = d.validate_manifest(manifest, tmp_path, None)
    assert any("does not match the frozen" in f for f in failures)


def test_the_bound_wrapper_carries_one_environment(tmp_path: Path):
    root = runtime_tree(tmp_path)
    frozen, _ = d.runtime_hash(root)
    bound = d.Bound(root, frozen)
    assert bound.env["PYTHONPATH"].startswith(str((root / "src").resolve()))
    bound.check(tmp_path, "in a test")  # must not raise


def test_the_bound_wrapper_refuses_a_drifted_runtime(tmp_path: Path):
    root = runtime_tree(tmp_path)
    bound = d.Bound(root, d.runtime_hash(root)[0])
    (root / "src" / "riftagent" / "__init__.py").write_text("VERSION = 9\n", encoding="utf-8")
    with pytest.raises(d.RuntimeDrift):
        bound.check(tmp_path, "in a test")


def test_resolution_is_asked_from_the_invocation_directory(tmp_path: Path):
    """Python puts the working directory on `sys.path`, so a probe run from one
    place and an arm run from another answer different questions — and case
    worktrees are repositories that may contain something importable."""
    root = runtime_tree(tmp_path)
    frozen, _ = d.runtime_hash(root)

    # A worktree that shadows the package from its own directory.
    worktree = tmp_path / "case"
    (worktree / "riftagent").mkdir(parents=True)
    (worktree / "riftagent" / "__init__.py").write_text("VERSION = 'shadow'\n", encoding="utf-8")

    from_elsewhere = d.resolves_to(root, d.runtime_env(root), tmp_path)
    from_worktree = d.resolves_to(root, d.runtime_env(root), worktree)
    assert Path(from_elsewhere).is_relative_to((root / "src" / "riftagent").resolve())
    assert from_worktree != from_elsewhere, "the cwd made no difference; the probe is not asking where it runs"

    with pytest.raises(d.RuntimeDrift, match="resolves to"):
        d.assert_runtime(root, frozen, d.runtime_env(root), "before an arm", worktree)


def test_ground_truth_evaluation_runs_under_the_bound_runtime(tmp_path: Path, monkeypatch):
    """`rift` grew an `env` argument and `evaluate_under_gate` was not updated,
    so the arms ran against the frozen runtime while the evaluation that scores
    them ran against whatever resolved first."""
    root = runtime_tree(tmp_path)
    bound = d.Bound(root, d.runtime_hash(root)[0])
    monkeypatch.setattr(d, "assert_runtime", lambda *a, **k: None)

    seen: list[dict | None] = []

    def fake_rift(args, cwd, timeout=3600.0, env=None):
        seen.append(env)
        return subprocess.CompletedProcess(
            args=["fake"], returncode=0, stdout="--precondition --expect-signature", stderr=""
        )

    monkeypatch.setattr(d, "_rift", fake_rift)
    patch = tmp_path / "p.diff"
    patch.write_text("--- a\n+++ b\n", encoding="utf-8")
    case = {"case_id": "c1", "worktree": str(tmp_path), "target": "t.py::t", "signature": "E: m"}
    d.evaluate_under_gate(case, patch, tmp_path, bound)

    assert seen, "no invocation was made"
    assert all(e is not None and "PYTHONPATH" in e for e in seen), "an evaluation ran unbound"


# ------------------ arm A records an unappliable patch instead of crashing


def test_arm_a_records_an_unappliable_patch_rather_than_crashing(tmp_path, run_cli, monkeypatch):
    """A model patch that will not apply is an ordinary outcome, not a harness
    error: the arm proposed something the tree rejects, which is a failure to
    repair and must be recorded as one.

    This path was missing. `wt.apply_patch` raised, the process died with a
    traceback, and the arm emitted no receipt at all — so the benchmark could
    only report that something had gone wrong somewhere, and the *known* case
    of a model emitting a corrupt diff had no verdict to score.
    """
    import riftagent.llm as llm

    files = {
        "src/pkg9/__init__.py": "",
        "src/pkg9/calc.py": "def total():\n    return 4\n",
        "tests/test_calc.py": "from pkg9.calc import total\n\n\ndef test_total():\n    assert total() == 5\n",
    }
    repo = build_repo(tmp_path / "corrupt", files)

    # A diff with a hunk header that describes lines the file does not have.
    corrupt = (
        "--- a/src/pkg9/calc.py\n+++ b/src/pkg9/calc.py\n"
        "@@ -40,7 +40,7 @@ def elsewhere():\n     context\n-    gone\n+    new\n     more\n"
    )
    reply = llm.ModelReply(
        text=json.dumps({"diff": corrupt, "summary": "will not apply"}),
        usage=llm.ModelUsage(input_tokens=10, output_tokens=10),
        model_reported="fake-model",
        finish_reason="stop",
    )
    monkeypatch.setattr(llm, "post_chat", lambda *a, **k: reply)
    monkeypatch.setattr(
        app.llm.ProviderConfig, "from_env", staticmethod(lambda env=None: llm.ProviderConfig("https://x/y", "k", "m"))
    )

    run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        "tests/test_calc.py::test_total",
        "--allow-partial-sandbox",
        "--model-alone",
        "--max-usd",
        "1.0",
    )

    events = []
    for path in sorted((repo / ".rift").rglob("ledger.jsonl")):
        events.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    kinds = [e["kind"] for e in events]

    assert "receipt_emitted" in kinds, "the arm crashed instead of emitting a verdict"
    phase = [e["payload"] for e in events if e["kind"] == "gate_phase_finished"]
    assert phase and phase[-1]["passed"] is False
    assert "does not apply" in phase[-1]["reason"], phase[-1]["reason"]
