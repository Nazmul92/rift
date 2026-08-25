"""Every arm must consume the declared task identity, not merely receive it.

The driver tests proved `--precondition` and `--expect-signature` reach the argv
of arms A, B and C. That is a different claim from the product consuming them,
and the two came apart: `freeze_declared_reproducer` was called only inside the
`--model-alone` branch, so arm A enforced the manifest's failure identity while
arms B and C parsed the same flags and derived their own reproducer from
whatever they happened to observe. Three arms would have solved three subtly
different tasks — the one thing a comparison cannot survive.

Required-but-unconsumed evidence, one boundary deeper than the last occurrence.

These tests drive the real CLI. `--no-model` is not used where the point is to
show that *no model request happens*, because a run that could not call a model
proves nothing about whether it would have.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import build_repo

FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/calc.py": "def total():\n    return 4\n",
    "tests/test_calc.py": "from pkg.calc import total\n\n\ndef test_total():\n    assert total() == 5\n",
    "tests/test_keep.py": "def test_keep():\n    assert True\n",
}
TARGET = "tests/test_calc.py::test_total"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path / "declared", FILES)


def events_of(repo: Path) -> list[dict]:
    out: list[dict] = []
    for path in (repo / ".rift").rglob("ledger.jsonl"):
        out.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return out


def kinds_of(repo: Path) -> list[str]:
    return [e["kind"] for e in events_of(repo)]


def test_the_normal_fix_path_freezes_the_declared_reproducer(repo, run_cli):
    """Arms B and C. Previously this event appeared only under --model-alone."""
    run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--no-model",
        "--expect-signature",
        "Failure",
    )
    assert "reproducer_frozen" in kinds_of(repo), kinds_of(repo)


def test_arm_b_freezes_it_too(repo, run_cli):
    """The random-policy arm takes the same path and must reach the same
    declaration; a policy flag changes selection, not task identity."""
    run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--no-model",
        "--expect-signature",
        "AssertionError",
        "--probe-policy",
        "random",
        "--probe-seed",
        "7",
    )
    assert "reproducer_frozen" in kinds_of(repo), kinds_of(repo)


def test_a_mismatched_signature_stops_before_any_model_request(repo, run_cli, monkeypatch):
    """The property that matters for spend.

    The model adapter is replaced by one that fails the test if called. A run
    whose declared identity does not match what the repository shows must stop
    before proposing, because a proposal bought against a different failure is
    a patch for a different problem.
    """
    import riftagent.app as app

    def refuse(*a, **k):
        raise AssertionError("a model request was made despite a declared-signature mismatch")

    monkeypatch.setattr(app, "_request_change", refuse)
    monkeypatch.setattr(app, "_request_handles", refuse, raising=False)
    monkeypatch.setattr(app, "_request_hypotheses", refuse, raising=False)

    code, out = run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--expect-signature",
        "ImportError: no such module",
    )
    events = events_of(repo)
    failed = [e for e in events if e["kind"] == "reproduction_failed"]
    assert failed, [e["kind"] for e in events]
    assert "signature" in failed[0]["payload"]["reason"], failed[0]
    assert failed[0]["payload"]["expected"].startswith("ImportError")
    # Nothing was proposed, and no gate phase completed successfully.
    assert "changeset_registered" not in kinds_of(repo)
    assert not [e for e in events if e["kind"] == "gate_phase_finished" and e["payload"].get("passed")]


def test_a_compatible_declaration_proceeds(repo, run_cli):
    """The enforcement must not block a case that does reproduce as declared:
    a check that rejects everything is not a check.

    The declared type here is `Failure`, not `AssertionError`, and that is the
    point: pytest's assertion rewriting turns a bare `assert` into a `Failure`,
    which is exactly why three manifest signatures had to be re-encoded. This
    fixture reproduces the phenomenon in miniature.
    """
    run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--no-model",
        "--expect-signature",
        "Failure",
    )
    kinds = kinds_of(repo)
    assert "reproducer_frozen" in kinds
    assert not [e for e in events_of(repo) if e["kind"] == "reproduction_failed"], kinds


def test_a_declared_task_that_does_not_fail_stops_before_proposing(repo, run_cli, monkeypatch):
    """A target that already passes cannot be repaired against the declaration,
    and must not buy a proposal either."""
    import riftagent.app as app

    monkeypatch.setattr(
        app, "_request_change", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model request made"))
    )
    fixed = build_repo(Path(str(repo)) / ".." / "passing", {**FILES, "src/pkg/calc.py": "def total():\n    return 5\n"})
    code, out = run_cli(
        "--repo",
        str(fixed),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--expect-signature",
        "Failure",
    )
    failed = [e for e in events_of(fixed) if e["kind"] == "reproduction_failed"]
    assert failed, kinds_of(fixed)
    assert "does not reproduce" in failed[0]["payload"]["reason"]


def test_removing_the_normal_path_freeze_makes_these_tests_red(repo, run_cli, monkeypatch):
    """The mutation. With the declaration withheld from the non-ablation path,
    B and C stop consuming it and the enforcement above cannot fire."""
    import riftagent.app as app

    original = app.freeze_declared_reproducer
    monkeypatch.setattr(
        app,
        "freeze_declared_reproducer",
        lambda flow, args, root, checkset, probe, budgets: (
            checkset
            if not getattr(args, "model_alone", False)
            else original(flow, args, root, checkset, probe, budgets)
        ),
    )
    run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--no-model",
        "--expect-signature",
        "ImportError: no such module",
    )
    kinds = kinds_of(repo)
    assert "reproducer_frozen" not in kinds, "the mutation did not remove the freeze; the tests are not sensitive"
    assert "reproduction_failed" not in kinds, (
        "enforcement survived the mutation, so it is not the freeze that drives it"
    )


def test_the_manifest_signatures_are_canonical():
    """Every frozen signature must parse as `ExceptionType: message`.

    Three were raw pytest `E ` lines. Where assertion rewriting emits a bare
    `assert ...`, RIFT records the type as `Failure`, so the declared type never
    matched and all three arms would have stopped at baseline.
    """
    manifest_path = Path(__file__).resolve().parent.parent / "benchmark" / "bm06" / "manifest-preliminary.json"
    if not manifest_path.is_file():
        pytest.skip("the preliminary manifest is not present")
    from riftagent.app import parse_signature

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for case in manifest["cases"]:
        signature = parse_signature(case["signature"])
        assert signature.exception_type, f"{case['case_id']} has no exception type: {case['signature']!r}"
        assert " " not in signature.exception_type, (
            f"{case['case_id']} signature is a raw pytest line, not a canonical signature: {case['signature']!r}"
        )
