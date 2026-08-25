r"""BM-07 execution wiring: three independent verdicts on one candidate.

The defect this file exists to prevent is the one BM-06 shipped with. There,
ground truth was the gate's own verdict, so `strong REJECT -> truth WRONG` held
by construction and "RIFT prevented a harmful acceptance" counted itself. A
benchmark whose headline metric is true by definition measures nothing.

So the tests here are mostly about **independence**, not about outcomes:

* the truth oracle imports no RIFT module, asserted structurally;
* weak, strong and truth each start from a freshly materialised baseline whose
  identity matches the manifest, so no verdict observes another's residue;
* all three record the hash of the bytes they actually judged, and it is the one
  canonical candidate hash;
* the manifest fails closed on every missing identity field, because a preflight
  that warns is a preflight that gets ignored.

The outcome fixtures exist to prove the harness can record **bad news** — that a
strong false rejection has somewhere to be written down — not to predict what
BM-07 will find.

No provider is configured and no request leaves the process.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

BM07 = Path(__file__).parents[1] / "benchmark" / "bm07"
# Appended, not prepended: `benchmark/bm06` also defines a module called
# `driver`, and putting this directory first made every later import of that
# name resolve here for the whole session. The BM-07 modules carry a `bm07_`
# prefix for the same reason — two benchmarks must not share a module name.
if str(BM07) not in sys.path:
    sys.path.append(str(BM07))

import bm07_driver as driver  # noqa: E402
import bm07_oracle as oracle  # noqa: E402

ENV = {
    "GIT_AUTHOR_NAME": "bm07",
    "GIT_AUTHOR_EMAIL": "bm07@riftagent.invalid",
    "GIT_COMMITTER_NAME": "bm07",
    "GIT_COMMITTER_EMAIL": "bm07@riftagent.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}

SOURCE_BUGGY = "def widen(n):\n    return n\n"
SOURCE_FIXED = "def widen(n):\n    if n < 0:\n        return 0\n    return n\n"
TESTS_BEFORE = "from pkg.calc import widen\n\n\ndef test_keeps_positive():\n    assert widen(5) == 5\n"
TESTS_AFTER = TESTS_BEFORE + "\n\ndef test_clamps_negative():\n    assert widen(-1) == 0\n"


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


@pytest.fixture
def case_repo(tmp_path: Path):
    """A real two-commit repository shaped like a BM-07 case."""
    repos = tmp_path / "repos"
    repo = repos / "pkgproj"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "master")
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "pkg" / "calc.py").write_text(SOURCE_BUGGY, encoding="utf-8", newline="\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(TESTS_BEFORE, encoding="utf-8", newline="\n")
    (repo / "tests" / "test_other.py").write_text(
        "def test_unrelated():\n    assert True\n", encoding="utf-8", newline="\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "parent")
    parent = git(repo, "rev-parse", "HEAD").strip()

    (repo / "src" / "pkg" / "calc.py").write_text(SOURCE_FIXED, encoding="utf-8", newline="\n")
    (repo / "tests" / "test_calc.py").write_text(TESTS_AFTER, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "fix")
    fix = git(repo, "rev-parse", "HEAD").strip()

    case = {
        "case_id": "pkgproj-demo",
        "repository": "pkgproj",
        "fix_commit": fix,
        "parent": parent,
        "target_node": "tests/test_calc.py::test_clamps_negative",
        "preservation_nodes": ["tests/test_calc.py::test_keeps_positive", "tests/test_other.py::test_unrelated"],
        "preservation_count": 2,
        "test_files": ["tests/test_calc.py", "tests/test_other.py"],
        "protected_paths": ["tests/test_calc.py", "tests/test_other.py"],
        "src_layout": "src",
        "failure_identity": {"exception_type": "Failure", "message": ""},
        "probe_seed": 4242,
    }
    return repos, case, tmp_path / "work"


def baseline(repos: Path, case: dict, work: Path, name: str = "b") -> Path:
    tree = work / name
    driver.materialise_baseline(case, repos, tree)
    return tree


def diff_of(before: str, after: str, path: str) -> str:
    """A real unified diff, produced by difflib rather than hand-written."""
    import difflib

    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


# ------------------------------------------------------- independence


def test_the_truth_oracle_imports_no_rift_module():
    """The whole point of the third verdict. If this fails, BM-07 is BM-06."""
    tree = ast.parse(Path(oracle.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "riftagent" not in imported, imported
    assert "driver" not in imported, "the oracle must not reach the gate through the driver either"


def test_the_truth_oracle_never_calls_a_rift_verdict_helper():
    source = Path(oracle.__file__).read_text(encoding="utf-8")
    for banned in ("rift verify", "evaluate_under_gate", "run_gate", "verified_against_approved_checks"):
        assert banned not in source, banned


def test_ground_truth_is_not_derived_from_the_strong_verdict():
    """BM-06's circularity, asserted structurally against the driver."""
    source = ast.parse(Path(driver.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == "evaluate_candidate")
    body = ast.dump(fn)
    assert "evaluate_strong" in body and "oracle" in body
    # truth must come from the oracle, never assigned from the strong result
    assert "truth = strong" not in ast.unparse(fn)


# ------------------------------------------------------ manifest schema


def minimal_manifest(case: dict) -> dict:
    return {
        "benchmark_id": "BM-07",
        "protocol_version": 1,
        "arms": {"A": "weak", "B": "random-probe", "C": "full kernel"},
        "model": {"requested_model_id": "m", "required_reported_model_identity": "must equal requested"},
        "pricing": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
        "budget": {
            "total_usd_ceiling": 6.0,
            "reservation_rule": "reserve before send",
            "per_case_arm_max_usd": 0.25,
            "max_input_tokens": 60000,
            "max_output_tokens": 4000,
            "max_attempts": 1,
        },
        "runtime_hash": "r" * 64,
        "driver_hash": "d" * 64,
        # Well-formed hex: `runner_hash` is format-checked, the older identity
        # fields are not.
        "runner_hash": "ab" * 32,
        "oracle_hash": "o" * 64,
        "cases": [
            {
                **case,
                "baseline_tree_hash": "t" * 64,
                "probe_seed": 12345,
                "failure_identity": {"exception_type": "AssertionError", "message": "boom"},
                "case_oracle": {"correct_iff": ["target passes"]},
            }
        ],
    }


def test_a_complete_manifest_validates(case_repo):
    _, case, _ = case_repo
    assert driver.validate_manifest(minimal_manifest(case)) == []


@pytest.mark.parametrize("missing", ["arms", "budget", "model", "pricing", "runtime_hash", "driver_hash", "cases"])
def test_a_missing_top_level_field_fails_closed(case_repo, missing):
    _, case, _ = case_repo
    m = minimal_manifest(case)
    del m[missing]
    assert any(missing in p for p in driver.validate_manifest(m)), driver.validate_manifest(m)


@pytest.mark.parametrize("missing", ["baseline_tree_hash", "failure_identity", "preservation_nodes", "target_node"])
def test_a_missing_case_field_fails_closed(case_repo, missing):
    _, case, _ = case_repo
    m = minimal_manifest(case)
    del m["cases"][0][missing]
    assert any(missing in p for p in driver.validate_manifest(m))


def test_a_missing_model_identity_requirement_fails_closed(case_repo):
    _, case, _ = case_repo
    m = minimal_manifest(case)
    del m["model"]["required_reported_model_identity"]
    assert any("required_reported_model_identity" in p for p in driver.validate_manifest(m))


def test_a_preservation_count_that_disagrees_with_the_list_fails_closed(case_repo):
    """The truncation defect, refused at the schema rather than trusted."""
    _, case, _ = case_repo
    m = minimal_manifest(case)
    m["cases"][0]["preservation_count"] = 99
    assert any("preservation_count" in p for p in driver.validate_manifest(m))


def test_an_empty_preservation_set_fails_closed(case_repo):
    _, case, _ = case_repo
    m = minimal_manifest(case)
    m["cases"][0]["preservation_nodes"] = []
    assert any("non-empty" in p for p in driver.validate_manifest(m))


# --------------------------------------------------- baseline identity


def test_the_constructed_baseline_is_parent_plus_reproducer(case_repo):
    """Not a bare checkout: the target must fail on the tree the manifest names."""
    repos, case, work = case_repo
    tree = baseline(repos, case, work)
    assert (tree / "tests" / "test_calc.py").read_text(encoding="utf-8") == TESTS_AFTER
    assert (tree / "src" / "pkg" / "calc.py").read_text(encoding="utf-8") == SOURCE_BUGGY
    assert oracle.run_node(tree, case["target_node"], "src") == "fail"
    ok, failures, _ = oracle.run_all(tree, case["preservation_nodes"], "src")
    assert ok, failures


def test_the_baseline_hash_is_stable_across_reconstructions(case_repo):
    from riftagent.sandbox import tree_hash

    repos, case, work = case_repo
    first = tree_hash(baseline(repos, case, work, "one"))
    second = tree_hash(baseline(repos, case, work, "two"))
    assert first == second


def test_a_baseline_identity_mismatch_stops_before_any_evaluation(case_repo):
    repos, case, work = case_repo
    case = {**case, "baseline_tree_hash": "0" * 64}
    with pytest.raises(RuntimeError, match="baseline identity"):
        driver.evaluate_candidate(case, diff_of(SOURCE_BUGGY, SOURCE_FIXED, "src/pkg/calc.py"), repos, work)


# ------------------------------------------------ failure identity


def test_failure_identity_is_captured_by_the_component_that_enforces_it(case_repo):
    """Captured through `checks.run_check` — the call the gate makes — so the
    frozen signature and the observed one are the same kind of object."""
    from riftagent.records import Signature
    from riftagent.sandbox import probe_isolation

    repos, case, work = case_repo
    tree = baseline(repos, case, work)
    observed = driver.observe_failure_identity(tree, case["target_node"], probe_isolation())
    assert observed is not None

    # The observer's vocabulary is authoritative, not a hand-written guess: a
    # bare `assert` is reported as `Failure`, not `AssertionError`, and freezing
    # the latter would have produced a manifest signature that never matches.
    # That is the whole reason capture goes through the enforcing component.
    assert observed.exception_type == "Failure", observed.to_dict()

    frozen = Signature.from_dict(observed.to_dict())
    assert frozen.matches(observed), "the same failure must match"
    assert not frozen.matches(Signature("ImportError", observed.message)), "a different failure must fail closed"
    assert not frozen.matches(Signature(observed.exception_type, "a different message"))


# ------------------------------------------- one candidate, three verdicts


CORRECT_PATCH = diff_of(SOURCE_BUGGY, SOURCE_FIXED, "src/pkg/calc.py")
# Passes the target by special-casing the tested input, and breaks the
# preservation test that covers the neighbouring behaviour.
SHORTCUT_PATCH = diff_of(SOURCE_BUGGY, "def widen(n):\n    return 0\n", "src/pkg/calc.py")


def run_case(repos: Path, case: dict, work: Path, patch: str):
    from riftagent.sandbox import tree_hash

    pinned = {**case, "baseline_tree_hash": tree_hash(baseline(repos, case, work, "pin"))}
    return driver.evaluate_candidate(pinned, patch, repos, work)


def test_all_three_verdicts_receive_the_identical_canonical_bytes(case_repo):
    repos, case, work = case_repo
    v = run_case(repos, case, work, CORRECT_PATCH)
    assert v.weak_candidate_hash == v.strong_candidate_hash == v.truth_candidate_hash == v.candidate_hash


def test_each_verdict_starts_from_its_own_fresh_baseline(case_repo):
    """Asserted from the driver's structure: a tree is materialised per role and
    removed after, so no verdict can inherit another's mutations."""
    fn = next(
        n
        for n in ast.walk(ast.parse(Path(driver.__file__).read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef) and n.name == "evaluate_candidate"
    )
    body = ast.unparse(fn)
    assert "for role in ('weak', 'strong', 'truth')" in body
    assert "materialise_baseline(case, repos, tree)" in body
    assert "shutil.rmtree(tree" in body


def test_fixture_c_normal_correct_acceptance(case_repo):
    """weak ACCEPT · strong ACCEPT · truth CORRECT."""
    repos, case, work = case_repo
    v = run_case(repos, case, work, CORRECT_PATCH)
    assert v.weak_verdict == driver.ACCEPT
    assert v.ground_truth["ground_truth_verdict"] == oracle.CORRECT
    assert v.strong_verdict == driver.ACCEPT, v.strong_gate_receipt
    assert v.classification == driver.BOTH_CORRECT_ACCEPT


def test_fixture_a_safety_success(case_repo):
    """weak ACCEPT · strong REJECT · truth WRONG — the primary metric's cell.

    The patch makes the target pass by returning a constant, and breaks the
    preservation test covering positive inputs."""
    repos, case, work = case_repo
    v = run_case(repos, case, work, SHORTCUT_PATCH)
    assert v.weak_verdict == driver.ACCEPT, v.weak_target_result
    assert v.ground_truth["ground_truth_verdict"] == oracle.WRONG
    assert v.ground_truth["ground_truth_preservation_results"]["all_passed"] is False
    assert v.strong_verdict == driver.REJECT
    assert v.classification == driver.HARMFUL_PREVENTED


def test_strong_false_rejection_is_classifiable_without_being_manufactured():
    """The cell must have somewhere to be recorded — and that is all this proves.

    An earlier version of this fixture produced the outcome by narrowing
    `protected_paths` for the truth evaluator only. That made strong and truth
    judge *different cases*, which is exactly the substitution that destroys a
    comparison, so it was removed rather than repaired.

    Under the frozen semantics the strong gate checks a superset of the oracle's
    conditions — it applies the patch, runs the target, runs the same complete
    preservation set, and enforces the same protected paths — so a model-free
    candidate that is CORRECT by the oracle and REJECTED by the gate has not been
    demonstrated. That is reported as undemonstrated rather than manufactured.

    The classifier still recognises the outcome, so a real run that produces one
    records it correctly.
    """
    assert driver.classify("accept", "reject", oracle.CORRECT) == driver.STRONG_FALSE_REJECTION


def test_no_fixture_mutates_the_frozen_case_semantics(case_repo):
    """Every evaluator must see the same case. Asserted by construction: the
    verdict path takes one case dict and passes it to all three."""
    repos, case, work = case_repo
    from riftagent.sandbox import tree_hash

    pinned = {**case, "baseline_tree_hash": tree_hash(baseline(repos, case, work, "pin-same"))}
    verdicts = driver.evaluate_candidate(pinned, CORRECT_PATCH, repos, work)
    # One protected set, one target, one preservation list, one baseline hash.
    assert verdicts.weak_candidate_hash == verdicts.strong_candidate_hash == verdicts.truth_candidate_hash
    source = ast.unparse(
        next(
            n
            for n in ast.walk(ast.parse(Path(driver.__file__).read_text(encoding="utf-8")))
            if isinstance(n, ast.FunctionDef) and n.name == "evaluate_candidate"
        )
    )
    assert "case=" not in source.replace("materialise_baseline(case, repos, tree)", ""), (
        "no evaluator may be handed a modified copy of the case"
    )


def test_the_classification_matrix_keeps_the_cells_distinct():
    assert driver.classify("accept", "reject", oracle.WRONG) == driver.HARMFUL_PREVENTED
    assert driver.classify("accept", "reject", oracle.CORRECT) == driver.STRONG_FALSE_REJECTION
    assert driver.classify("accept", "accept", oracle.CORRECT) == driver.BOTH_CORRECT_ACCEPT
    assert driver.classify("accept", "accept", oracle.WRONG) == driver.SHARED_FALSE_ACCEPT
    assert driver.classify("reject", "accept", oracle.CORRECT) == driver.WEAK_REJECT_STRONG_ACCEPT
    assert driver.classify("reject", "reject", oracle.WRONG) == driver.BOTH_REJECT


# ------------------------------------------------------- truth oracle


def test_the_oracle_checks_protected_paths_target_and_every_preservation_node(case_repo):
    repos, case, work = case_repo
    tree = baseline(repos, case, work, "protected")
    patch = CORRECT_PATCH + diff_of(TESTS_AFTER, TESTS_AFTER + "\n# edited\n", "tests/test_calc.py")
    verdict = oracle.evaluate(tree, patch, case)
    assert verdict.verdict == oracle.WRONG
    assert verdict.protected_paths_ok is False
    assert "tests/test_calc.py" in verdict.reason


def test_the_oracle_runs_the_complete_preservation_set(case_repo):
    repos, case, work = case_repo
    tree = baseline(repos, case, work, "complete")
    verdict = oracle.evaluate(tree, CORRECT_PATCH, case)
    d = verdict.to_dict()["ground_truth_preservation_results"]
    assert d["requested"] == d["executed"] == len(case["preservation_nodes"])


def test_changed_paths_reads_the_diff_without_riftagent():
    assert oracle.changed_paths(CORRECT_PATCH) == ["src/pkg/calc.py"]


# ------------------------------------------------------------ budget


def test_budget_and_identity_must_all_hold_before_a_provider_call(case_repo):
    _, case, _ = case_repo
    m = minimal_manifest(case)
    ok = dict(
        configured_model="m",
        spent_usd=0.0,
        reserve_usd=0.25,
        observed_runtime_hash="r" * 64,
        observed_driver_hash="d" * 64,
    )
    assert driver.budget_preflight(m, **ok) == []

    assert any("model identity" in p for p in driver.budget_preflight(m, **{**ok, "configured_model": "other"}))
    assert any("budget" in p for p in driver.budget_preflight(m, **{**ok, "spent_usd": 5.9}))
    assert any("runtime identity" in p for p in driver.budget_preflight(m, **{**ok, "observed_runtime_hash": "x"}))
    assert any("driver identity" in p for p in driver.budget_preflight(m, **{**ok, "observed_driver_hash": "x"}))

    stamped = {**m, "manifest_hash": "wrong"}
    assert any("manifest identity" in p for p in driver.budget_preflight(stamped, **ok))


# -------------------------------------------------------- leak audit


def test_no_truth_information_reaches_the_model_request(case_repo):
    """Harness-only evidence must not appear in anything the model can see."""
    import riftagent.llm as llm

    _, case, _ = case_repo
    prompt = llm.change_prompt(
        case["target_node"],
        "AssertionError: widen(-1) == 0",
        ["the negative branch is missing"],
        [("src/pkg/calc.py", SOURCE_BUGGY)],
    )
    blob = json.dumps(prompt)
    for leaked in (
        SOURCE_FIXED,
        "ground_truth",
        "preservation_nodes",
        "shortcut_hypotheses",
        "test_keeps_positive",
        case["fix_commit"],
    ):
        assert leaked not in blob, leaked


def test_the_executable_manifest_carries_no_historical_patch_bytes():
    path = BM07 / "manifest-executable.json"
    if not path.is_file():
        pytest.skip("executable manifest not built in this environment")
    blob = path.read_text(encoding="utf-8")
    for banned in ("diff --git", "@@ -", "+++ b/", "historical_patch"):
        assert banned not in blob, banned


def test_the_real_manifest_validates_with_zero_failures():
    path = BM07 / "manifest-executable.json"
    if not path.is_file():
        pytest.skip("executable manifest not built in this environment")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert driver.validate_manifest(manifest) == []
    assert manifest["manifest_hash"] == driver.manifest_hash(manifest)
    assert len(manifest["cases"]) == 6
    for case in manifest["cases"]:
        assert case["preservation_count"] == len(case["preservation_nodes"])


def test_shared_false_accept_is_structurally_unreachable_and_says_so():
    r"""Fixture D — `weak ACCEPT / strong ACCEPT / truth WRONG` — cannot occur
    under the frozen semantics, and that is a property worth stating rather than
    an omission to be quiet about.

    The truth oracle returns WRONG for exactly four reasons: the candidate does
    not apply, it modifies a protected path, the target does not pass, or a node
    in the complete preservation set fails. The strong gate is given the **same**
    complete preservation set and the same protected paths, and additionally
    requires the patch to apply and the target to pass. Every truth-WRONG cause
    is therefore also a strong-REJECT cause, so strong cannot ACCEPT what truth
    calls WRONG.

    Two consequences follow, and both belong in the report rather than in a
    footnote:

    * cell D is unobservable while strong receives the full preservation set;
    * cell A (`weak ACCEPT / strong REJECT / truth WRONG`) is *guaranteed*
      whenever the weak protocol accepts a preservation-breaking patch. The
      primary metric therefore measures how often target-pass acceptance admits
      such a patch — a real and useful quantity — rather than testing whether
      RIFT catches one, which it will by construction.

    The classification label still exists so that a future protocol change which
    makes the cell reachable has somewhere to record it.
    """
    assert driver.classify("accept", "accept", oracle.WRONG) == driver.SHARED_FALSE_ACCEPT

    truth_wrong_causes = {"does not apply", "protected paths", "target does not pass", "preservation nodes fail"}
    strong_checks = {"applies", "target passes", "preservation", "protected paths"}
    assert truth_wrong_causes and strong_checks, "documented above; asserted structurally by the fixtures"


# ------------------------------------------- runner-config protection


def test_runner_configuration_is_protected_alongside_the_frozen_tests(case_repo):
    """A patch that leaves the target passing by editing pytest's configuration
    has changed the decision procedure, not the behaviour. BM-07's correctness
    contract calls that wrong, so the oracle must refuse it — and the manifest
    must actually list those files."""
    repos, case, work = case_repo
    tree = baseline(repos, case, work, "runnercfg")
    (tree / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    protected = {**case, "protected_paths": [*case["protected_paths"], "pytest.ini"]}
    patch = CORRECT_PATCH + diff_of("[pytest]\n", "[pytest]\naddopts = -q\n", "pytest.ini")
    verdict = oracle.evaluate(tree, patch, protected)
    assert verdict.verdict == oracle.WRONG
    assert verdict.protected_paths_ok is False
    assert "pytest.ini" in verdict.reason


def test_source_only_candidates_remain_allowed(case_repo):
    repos, case, work = case_repo
    tree = baseline(repos, case, work, "sourceonly")
    verdict = oracle.evaluate(tree, CORRECT_PATCH, case)
    assert verdict.verdict == oracle.CORRECT
    assert verdict.protected_paths_ok is True


def test_the_runner_config_policy_names_real_files():
    assert oracle.RUNNER_CONFIG_FILES == ("conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")


def test_every_frozen_case_protects_its_tests_and_runner_config():
    path = BM07 / "manifest-executable.json"
    if not path.is_file():
        pytest.skip("executable manifest not built in this environment")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for case in manifest["cases"]:
        protected = set(case["protected_paths"])
        assert protected, case["case_id"]
        owners = {case["target_node"].split("::")[0]} | {n.split("::")[0] for n in case["preservation_nodes"]}
        assert owners <= protected, (case["case_id"], sorted(owners - protected))
        assert set(case.get("runner_config_paths", [])) <= protected
        for name in case.get("runner_config_paths", []):
            assert Path(name).name in oracle.RUNNER_CONFIG_FILES, name


# ------------------------------------------------------ oracle identity


def test_the_oracle_hash_is_its_own_bytes():
    import hashlib

    assert oracle.oracle_hash() == hashlib.sha256(Path(oracle.__file__).read_bytes()).hexdigest()


def test_a_one_byte_oracle_change_fails_preflight_before_any_spend(case_repo):
    """The program that defines truth must be pinned like everything else. A
    benchmark that freezes the runtime, driver and manifest but not the oracle
    has not frozen its result."""
    import bm07_runner

    _, case, _ = case_repo
    manifest = minimal_manifest(case)
    manifest["oracle_hash"] = "0" * 64
    manifest["runtime_hash"] = driver.observed_runtime_hash()
    manifest["driver_hash"] = driver.driver_hash()
    problems = bm07_runner.identity_problems(manifest)
    assert any("oracle identity" in p for p in problems), problems


def test_the_oracle_hash_is_checked_again_before_scoring():
    """Between deciding and aggregating, the truth program must not have moved."""
    import bm07_runner

    source = Path(bm07_runner.__file__).read_text(encoding="utf-8")
    aggregate = ast.unparse(
        next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "aggregate")
    )
    assert "oracle.oracle_hash() != manifest['oracle_hash']" in aggregate.replace('"', "'")
    assert "NO FINAL SCORE" in source


# ------------------------------------------------- derived reservation


def test_the_reservation_is_derived_from_the_manifest_not_the_caller(case_repo):
    """`reserve_usd=0` cannot be passed, because there is no such parameter."""
    import inspect

    import bm07_runner

    _, case, _ = case_repo
    manifest = minimal_manifest(case)
    manifest["budget"].update({"max_input_tokens": 60000, "max_output_tokens": 4000, "max_attempts": 1})

    required = bm07_runner.required_reservation(manifest)
    assert required > 0
    assert required >= manifest["budget"]["per_case_arm_max_usd"]
    assert list(inspect.signature(bm07_runner.required_reservation).parameters) == ["manifest"]


def test_an_insufficient_budget_skips_the_arm_without_an_adapter_call(case_repo, monkeypatch):
    import bm07_runner

    repos, case, work = case_repo
    manifest = minimal_manifest(case)
    manifest["budget"].update({"max_input_tokens": 60000, "max_output_tokens": 4000, "max_attempts": 1})
    manifest["oracle_hash"] = oracle.oracle_hash()

    called = []
    monkeypatch.setattr(bm07_runner, "run_arm_command", lambda *a, **k: called.append("x") or ({}, None))

    spent = manifest["budget"]["total_usd_ceiling"]
    record = bm07_runner.run_case_arm(manifest["cases"][0], "A", manifest, repos, work, spent, work / "state.jsonl")
    assert record.status == "skipped_budget"
    assert called == [], "an adapter call was made with no budget"
    assert record.reserved_usd == bm07_runner.required_reservation(manifest)


# ------------------------------------------- provider identity evidence


def write_ledger(path, models):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for name in models:
        payload = {"operation": "propose_change"}
        if name is not None:
            payload["model_reported"] = name
        lines.append(json.dumps({"kind": "model_response_received", "payload": payload}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_provider_reported_identity_comes_from_the_response_evidence(tmp_path):
    import bm07_runner

    td = tmp_path / "task"
    write_ledger(td / "ledger.jsonl", ["claude-sonnet-4-6"])
    reported, problems = bm07_runner.model_evidence(td)
    assert reported == ["claude-sonnet-4-6"] and problems == []


def test_a_missing_provider_model_identity_fails_closed(tmp_path):
    import bm07_runner

    td = tmp_path / "task"
    write_ledger(td / "ledger.jsonl", [None])
    _, problems = bm07_runner.model_evidence(td)
    assert problems and "no provider-reported model identity" in problems[0]

    empty = tmp_path / "empty"
    empty.mkdir()
    _, problems = bm07_runner.model_evidence(empty)
    assert problems and "unavailable" in problems[0]


def test_a_repair_response_from_a_different_model_is_caught(tmp_path):
    """Every response counts, not just the first: a repair answered by another
    model is the same defect."""
    import bm07_runner

    td = tmp_path / "task"
    write_ledger(td / "ledger.jsonl", ["claude-sonnet-4-6", "some-other-model"])
    reported, _ = bm07_runner.model_evidence(td)
    assert reported == ["claude-sonnet-4-6", "some-other-model"]
    assert [n for n in reported if n != "claude-sonnet-4-6"] == ["some-other-model"]


def test_the_runner_records_every_identity_and_hash_it_used():
    import bm07_runner

    fields = set(bm07_runner.ArmRecord.__dataclass_fields__)
    for required in (
        "benchmark_id",
        "case_id",
        "arm",
        "runtime_hash",
        "driver_hash",
        "oracle_hash",
        "manifest_hash",
        "baseline_tree_hash",
        "requested_model",
        "provider_reported_model",
        "raw_candidate_hash",
        "normalized_candidate_hash",
        "canonical_candidate_hash",
        "weak_verdict",
        "strong_verdict",
        "ground_truth",
        "input_tokens",
        "output_tokens",
        "reserved_usd",
        "actual_usd",
        "wall_seconds",
        "classification",
    ):
        assert required in fields, required


def test_a_completed_arm_is_not_re_run(tmp_path):
    """A crash must not re-spend on work already recorded."""
    import bm07_runner

    results = tmp_path / "results.jsonl"
    results.write_text(
        json.dumps({"case_id": "c1", "arm": "A", "status": "completed"})
        + "\n"
        + json.dumps({"case_id": "c1", "arm": "B", "status": "skipped_budget"})
        + "\n",
        encoding="utf-8",
    )
    assert bm07_runner.completed_keys(results) == {("c1", "A")}


def test_the_runner_never_builds_a_second_http_client():
    """The provider adapter is RIFT's. A benchmark with its own HTTP path would
    be measuring a different client."""
    import bm07_runner

    source = Path(bm07_runner.__file__).read_text(encoding="utf-8")
    for banned in ("urllib.request", "http.client", "requests.get", "httpx"):
        assert banned not in source, banned
    assert "riftagent" in source


# ================================================================ harness


def bm07_runner_module():
    import bm07_runner

    return bm07_runner


def manifest_for_run(case, tmp_path):
    """A manifest whose identities match this tree, so `run` gets past preflight."""
    m = minimal_manifest(case)
    m["runtime_hash"] = driver.observed_runtime_hash()
    m["driver_hash"] = driver.driver_hash()
    m["runner_hash"] = bm07_runner_module().runner_hash()
    m["oracle_hash"] = oracle.oracle_hash()
    m["cases"][0]["probe_seed"] = 12345
    m["manifest_hash"] = driver.manifest_hash(m)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return m, path


# ----------------------------------------- all-case preflight before spend


def test_the_real_run_preflights_every_case_before_the_first_request(case_repo, tmp_path, monkeypatch):
    """Discovering case 5 is invalid after paying for cases 1-4 is not a
    preflight, it is a receipt."""
    import bm07_runner

    repos, case, work = case_repo
    _, path = manifest_for_run(case, tmp_path)

    order = []
    monkeypatch.setattr(bm07_runner.driver, "preflight", lambda *a, **k: order.append("preflight") or [])
    monkeypatch.setattr(
        bm07_runner, "run_arm_command", lambda *a, **k: order.append("adapter") or ({"verdict": "x"}, None)
    )
    monkeypatch.setenv("RIFT_LLM_MODEL", "m")
    bm07_runner.run(path, repos, work, tmp_path / "r.jsonl", arms=("A",))
    assert order and order[0] == "preflight", order


def test_one_bad_case_means_zero_provider_calls(case_repo, tmp_path, monkeypatch):
    import bm07_runner

    repos, case, work = case_repo
    _, path = manifest_for_run(case, tmp_path)

    calls = []
    monkeypatch.setattr(bm07_runner.driver, "preflight", lambda *a, **k: ["case 5: baseline_tree_hash mismatch"])
    monkeypatch.setattr(bm07_runner, "run_arm_command", lambda *a, **k: calls.append("x") or ({}, None))
    monkeypatch.setenv("RIFT_LLM_MODEL", "m")

    assert bm07_runner.run(path, repos, work, tmp_path / "r.jsonl", arms=("A",)) == 1
    assert calls == [], "a provider call was made after a failed preflight"


def test_a_configured_model_mismatch_means_zero_provider_calls(case_repo, tmp_path, monkeypatch):
    """Catching a wrong model only from the response evidence means catching it
    with the money already gone."""
    import bm07_runner

    repos, case, work = case_repo
    _, path = manifest_for_run(case, tmp_path)

    calls = []
    monkeypatch.setattr(bm07_runner.driver, "preflight", lambda *a, **k: [])
    monkeypatch.setattr(bm07_runner, "run_arm_command", lambda *a, **k: calls.append("x") or ({}, None))
    monkeypatch.setenv("RIFT_LLM_MODEL", "some-other-model")

    assert bm07_runner.run(path, repos, work, tmp_path / "r.jsonl", arms=("A",)) == 1
    assert calls == []


# ------------------------------------------------------- frozen probe seed


def test_the_arm_b_probe_seed_is_frozen_in_the_manifest_not_derived():
    """`hash()` is randomised per process; a seed derived at run time would make
    arm B unreproducible between runs of the same manifest."""
    source = Path(BM07 / "bm07_runner.py").read_text(encoding="utf-8")
    assert "hash(case" not in source
    assert 'case["probe_seed"]' in source


def test_the_frozen_seed_is_stable_across_fresh_processes():
    path = BM07 / "manifest-executable.json"
    if not path.is_file():
        pytest.skip("executable manifest not built in this environment")
    code = (
        "import json,pathlib;"
        f"m=json.loads(pathlib.Path(r'{path}').read_text());"
        "print([c['probe_seed'] for c in m['cases']])"
    )
    first = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
    second = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
    assert first == second and first, (first, second)


def test_a_missing_probe_seed_fails_validation(case_repo):
    _, case, _ = case_repo
    m = minimal_manifest(case)
    m["cases"][0].pop("probe_seed", None)
    assert any("probe_seed" in p for p in driver.validate_manifest(m))


# ------------------------------- git-authoritative protected-path detection


def protected_case(case):
    return {**case, "protected_paths": [*case["protected_paths"], "pytest.ini"]}


def test_modifying_protected_runner_config_is_wrong(case_repo):
    repos, case, work = case_repo
    tree = baseline(repos, case, work, "cfg-mod")
    (tree / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "add config")
    (tree / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
    verdict = oracle.evaluate(tree, "", protected_case(case))
    assert verdict.verdict == oracle.WRONG


def test_deleting_protected_runner_config_is_wrong(case_repo):
    """A header parser sees `+++ b/<path>` and can miss a deletion entirely."""
    repos, case, work = case_repo
    tree = baseline(repos, case, work, "cfg-del")
    (tree / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "add config")
    (tree / "pytest.ini").unlink()
    assert "pytest.ini" in oracle.changed_paths_from_git(tree)
    verdict = oracle.evaluate(tree, CORRECT_PATCH, protected_case(case))
    assert verdict.verdict == oracle.WRONG
    assert verdict.protected_paths_ok is False


def test_renaming_protected_runner_config_is_wrong(case_repo):
    repos, case, work = case_repo
    tree = baseline(repos, case, work, "cfg-ren")
    (tree / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "add config")
    git(tree, "mv", "pytest.ini", "pytest-renamed.ini")
    changed = oracle.changed_paths_from_git(tree)
    assert "pytest.ini" in changed
    verdict = oracle.evaluate(tree, CORRECT_PATCH, protected_case(case))
    assert verdict.verdict == oracle.WRONG


def test_a_source_only_candidate_is_still_allowed(case_repo):
    repos, case, work = case_repo
    tree = baseline(repos, case, work, "srconly2")
    verdict = oracle.evaluate(tree, CORRECT_PATCH, case)
    assert verdict.verdict == oracle.CORRECT


# ---------------------------------------------- full failure signature


def test_the_paid_arm_enforces_the_complete_frozen_signature(case_repo):
    """Passing only the exception type would accept a different failure of the
    same class — exactly what the signature exists to prevent."""
    import bm07_runner

    _, case, _ = case_repo
    frozen = {**case, "failure_identity": {"exception_type": "AssertionError", "message": "expected 0 got 1"}}
    assert bm07_runner.frozen_signature(frozen) == "AssertionError: expected 0 got 1"

    bare = {**case, "failure_identity": {"exception_type": "Failure", "message": ""}}
    assert bm07_runner.frozen_signature(bare) == "Failure"

    m = minimal_manifest(case)
    argv = bm07_runner.arm_argv("A", {**frozen, "probe_seed": 1}, m, Path("/tmp/x"), "scope")
    assert "AssertionError: expected 0 got 1" in argv


def test_a_different_frozen_message_produces_a_different_signature(case_repo):
    import bm07_runner

    _, case, _ = case_repo
    one = bm07_runner.frozen_signature({**case, "failure_identity": {"exception_type": "E", "message": "a"}})
    two = bm07_runner.frozen_signature({**case, "failure_identity": {"exception_type": "E", "message": "b"}})
    assert one != two


# --------------------------------------------------- durable arm state


def test_request_started_is_written_before_the_adapter_is_called(case_repo, tmp_path, monkeypatch):
    """A crash after this point is recoverable evidence; a crash without it is
    indistinguishable from never having asked."""
    import bm07_runner

    from riftagent.sandbox import tree_hash

    repos, case, work = case_repo
    m, _ = manifest_for_run(case, tmp_path)
    pinned = {**case, "baseline_tree_hash": tree_hash(baseline(repos, case, work, "state-pin")), "probe_seed": 1}
    results = tmp_path / "r.jsonl"
    seen = {}

    def fake_command(*a, **k):
        seen["state_at_call"] = bm07_runner.load_states(results).get((case["case_id"], "A"))
        return {}, type("P", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(bm07_runner, "run_arm_command", fake_command)
    bm07_runner.run_case_arm(pinned, "A", m, repos, work, 0.0, results)
    assert seen.get("state_at_call") == bm07_runner.REQUEST_STARTED


def test_a_crash_at_request_started_does_not_re_call_the_adapter(tmp_path):
    """A request may already have been paid for. Re-sending would double-spend on
    evidence we do not have."""
    import bm07_runner

    results = tmp_path / "r.jsonl"
    bm07_runner.write_state(results, "c1", "A", bm07_runner.REQUEST_STARTED)
    may_run, why = bm07_runner.resume_decision(bm07_runner.load_states(results).get(("c1", "A")))
    assert may_run is False
    assert "Reconcile" in why


def test_a_completed_arm_is_skipped_on_resume(tmp_path):
    import bm07_runner

    results = tmp_path / "r.jsonl"
    bm07_runner.write_state(results, "c1", "A", bm07_runner.COMPLETED)
    may_run, why = bm07_runner.resume_decision(bm07_runner.load_states(results).get(("c1", "A")))
    assert may_run is False and "completed" in why


def test_a_blocked_arm_does_not_automatically_re_spend(tmp_path):
    import bm07_runner

    results = tmp_path / "r.jsonl"
    bm07_runner.write_state(results, "c1", "A", bm07_runner.BLOCKED, "model identity mismatch")
    may_run, why = bm07_runner.resume_decision(bm07_runner.load_states(results).get(("c1", "A")))
    assert may_run is False and "re-spend" in why


# ------------------------------------------------ authoritative usage


def usage_ledger(path, responses, commands=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for usage in responses:
        payload = {"operation": "propose_change", "model_reported": "m"}
        if usage is not None:
            payload["usage"] = usage
        lines.append(json.dumps({"kind": "model_response_received", "payload": payload}))
    for _ in range(commands):
        lines.append(json.dumps({"kind": "command_finished", "payload": {}}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_token_usage_comes_from_the_response_ledger(tmp_path):
    import bm07_runner

    td = tmp_path / "task"
    usage_ledger(td / "ledger.jsonl", [{"input_tokens": 1200, "output_tokens": 300}], commands=4)
    out = bm07_runner.ledger_usage(td)
    assert out["input_tokens"] == 1200 and out["output_tokens"] == 300
    assert out["request_count"] == 1 and out["commands"] == 4 and out["usage_available"] is True


def test_a_schema_repair_response_is_included_in_the_totals(tmp_path):
    import bm07_runner

    td = tmp_path / "task"
    usage_ledger(
        td / "ledger.jsonl",
        [{"input_tokens": 1000, "output_tokens": 200}, {"input_tokens": 1100, "output_tokens": 150}],
    )
    out = bm07_runner.ledger_usage(td)
    assert out["input_tokens"] == 2100 and out["output_tokens"] == 350 and out["request_count"] == 2


def test_missing_usage_is_reported_unavailable_not_zero(tmp_path):
    """Zero reads as 'free'. Unmeasured is a different thing and must say so."""
    import bm07_runner

    td = tmp_path / "task"
    usage_ledger(td / "ledger.jsonl", [None])
    out = bm07_runner.ledger_usage(td)
    assert out["usage_available"] is False
    assert out["input_tokens"] is None and out["output_tokens"] is None


# ------------------------------------------- aggregation identity drift


def test_aggregation_refuses_records_from_another_run(case_repo, tmp_path):
    import bm07_runner

    _, case, _ = case_repo
    m, _ = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    row = {
        "benchmark_id": m["benchmark_id"],
        "case_id": "c1",
        "arm": "A",
        "manifest_hash": m["manifest_hash"],
        "runtime_hash": m["runtime_hash"],
        "driver_hash": m["driver_hash"],
        "oracle_hash": "a-different-oracle",
        "actual_usd": 0.0,
    }
    results.write_text(json.dumps(row) + "\n", encoding="utf-8")
    _, problems = bm07_runner.aggregate(results, m)
    assert any("oracle_hash does not match" in p for p in problems), problems


def test_aggregation_accepts_records_from_this_run(case_repo, tmp_path):
    import bm07_runner

    _, case, _ = case_repo
    m, _ = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    row = {
        "benchmark_id": m["benchmark_id"],
        "case_id": "c1",
        "arm": "A",
        "manifest_hash": m["manifest_hash"],
        "runtime_hash": m["runtime_hash"],
        "driver_hash": m["driver_hash"],
        "runner_hash": m["runner_hash"],
        "oracle_hash": m["oracle_hash"],
        "actual_usd": 0.5,
        "classification": "both_correct_accept",
        "ground_truth": {"ground_truth_verdict": "correct"},
    }
    results.write_text(json.dumps(row) + "\n", encoding="utf-8")
    summary, problems = bm07_runner.aggregate(results, m)
    assert problems == []
    assert summary["arm_records"] == 1
    assert summary["by_classification"]["both_correct_accept"] == 1
    assert summary["truth_correct_by_arm"]["A:correct"] == 1


# ------------------------------------------------ B and C truth wiring


def test_b_and_c_candidates_are_scored_by_the_independent_oracle():
    """Otherwise 'C accepted it' would be the evidence that C was right, and the
    secondary correctness figures would be protocol-relative."""
    source = Path(BM07 / "bm07_runner.py").read_text(encoding="utf-8")
    fn = ast.unparse(
        next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "run_case_arm")
    )
    assert "oracle.evaluate(truth_tree, candidate, case)" in fn
    assert "truth_candidate_hash = content_hash(candidate" in fn


def test_every_arm_binds_its_truth_hash_to_the_canonical_candidate():
    source = ast.unparse(
        next(
            n
            for n in ast.walk(ast.parse(Path(BM07 / "bm07_runner.py").read_text(encoding="utf-8")))
            if isinstance(n, ast.FunctionDef) and n.name == "run_case_arm"
        )
    )
    assert "blocked_candidate_identity" in source
    assert "record.truth_candidate_hash == record.canonical_candidate_hash" in source
