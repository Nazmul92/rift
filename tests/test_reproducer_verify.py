"""DAR-015: `verify` can be given the experiment that reproduces the failure.

The gap this closes had surfaced four times — the prototype's C4 abstention, the
stage-2 confirmation criterion, the BM-06 driver's bare-target call, and finally
`verify` itself, which had no parameter a reproducer could arrive through. Each
earlier occurrence was fixed where it appeared; the product gap underneath was
what kept producing them.

The fixture below is the shape that exposes it: a polluter test leaves state
behind, and the target passes when run alone and fails when run after it. A gate
that can only run the bare target measures the wrong thing and reports a passing
baseline, so no patch for the failure can ever be verified.

Every test drives the real CLI. No model is configured and none is called —
`verify` makes no model request at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import build_repo, make_diff

# A leaked global registry. `summary()` reads it, so the target's outcome
# depends on whether the polluter ran first.
POLLUTE_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/registry.py": (
        "REGISTRY = {}\n\n\ndef add(name):\n    REGISTRY[name] = True\n\n\ndef names():\n    return sorted(REGISTRY)\n"
    ),
    "src/pkg/report.py": "from pkg.registry import names\n\n\ndef summary():\n    return ','.join(names())\n",
    "tests/test_pollute.py": "from pkg.registry import add\n\n\ndef test_pollute():\n    add('leaked')\n",
    "tests/test_clean.py": "from pkg.report import summary\n\n\ndef test_clean():\n    assert summary() == ''\n",
    "tests/test_keep.py": "def test_keep():\n    assert True\n",
}

TARGET = "tests/test_clean.py::test_clean"
POLLUTER = "tests/test_pollute.py::test_pollute"
PRESERVE = "tests/test_keep.py::test_keep"


@pytest.fixture
def pollute_repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path / "pollute", POLLUTE_FILES)


@pytest.fixture
def state_fix_diff(pollute_repo: Path) -> str:
    """Implementation-only: `summary()` stops reading process-global state.

    Touches no test file, so it is a patch the frozen judge permits.
    """
    return make_diff(
        pollute_repo,
        {"src/pkg/report.py": "def summary(registry=None):\n    return ','.join(sorted(registry or {}))\n"},
    )


def receipt_of(out: str) -> dict:
    for line in reversed(out.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


# ------------------------------------------------------------------ 1. the gap


def baseline_passed(repo: Path) -> bool:
    """Did the baseline phase reproduce the failure? Read from the ledger."""
    for path in (repo / ".rift").rglob("ledger.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event["kind"] == "gate_phase_finished" and event["payload"].get("phase") == "baseline":
                return bool(event["payload"].get("passed"))
    return False


def test_the_bare_target_passes_while_the_precondition_reproduces_the_failure(tmp_path, run_cli, write_diff):
    """D1. The asymmetry the whole feature exists for, asserted through the CLI.

    Same patch, same target, same repository — the only difference is whether
    the polluter is declared. Without it the baseline does not reproduce, so no
    patch for this failure could ever be verified.
    """
    # Semantically inert: a comment. It cannot repair anything, so the only
    # thing that varies between the two runs below is the declared precondition.
    inert = {"src/pkg/report.py": "# a comment only\n" + POLLUTE_FILES["src/pkg/report.py"]}

    bare_repo = build_repo(tmp_path / "bare", POLLUTE_FILES)
    run_cli(
        "--repo",
        str(bare_repo),
        "--json",
        "verify",
        str(write_diff(make_diff(bare_repo, inert))),
        TARGET,
        "--allow-partial-sandbox",
    )
    assert not baseline_passed(bare_repo), "the bare target failed alone; the fixture is not the shape under test"

    with_pre = build_repo(tmp_path / "with_pre", POLLUTE_FILES)
    run_cli(
        "--repo",
        str(with_pre),
        "--json",
        "verify",
        str(write_diff(make_diff(with_pre, inert))),
        TARGET,
        "--allow-partial-sandbox",
        "--precondition",
        POLLUTER,
    )
    assert baseline_passed(with_pre), "the declared precondition did not reproduce the failure"


# ------------------------------------------------------------------ 2-3. the gate


def test_verify_drives_the_reproducer_through_every_phase(pollute_repo, run_cli, write_diff, state_fix_diff):
    """D2 and D3 together: the frozen experiment runs in baseline, candidate,
    withdrawal and reapplication, and an implementation-only patch verifies."""
    code, out = run_cli(
        "--repo",
        str(pollute_repo),
        "--json",
        "verify",
        str(write_diff(state_fix_diff)),
        TARGET,
        "--allow-partial-sandbox",
        "--precondition",
        POLLUTER,
        "--preserve",
        PRESERVE,
    )
    receipt = receipt_of(out)
    assert receipt.get("verdict") == "verified_against_approved_checks", receipt

    events = [json.loads(x) for x in (pollute_repo / ".rift").rglob("ledger.jsonl") for x in x.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert "reproducer_frozen" in kinds, "the reproducer was never frozen"
    phases = [e["payload"].get("phase") for e in events if e["kind"] == "gate_phase_finished"]
    for phase in ("baseline", "candidate", "withdrawal", "reapply"):
        assert phase in phases, f"{phase} did not run: {phases}"
    # Every phase ran the same declared experiment, not the bare target.
    runs = [e for e in events if e["kind"] == "command_started" and "→" in str(e["payload"].get("display", ""))]
    assert len({e["payload"]["phase"] for e in runs}) >= 3, "the reproducer was not used in every phase"


# ------------------------------------------------------------------ 4. judge artifacts


@pytest.mark.parametrize("edited", ["tests/test_pollute.py", "tests/test_clean.py"])
def test_a_patch_touching_a_judge_artifact_is_rejected(pollute_repo, run_cli, write_diff, edited):
    """D4. The polluter is part of the judge once it is declared: a patch that
    edits it weakens the experiment while leaving the contract record
    byte-identical."""
    # A *behaviour-preserving* edit: one added comment. Two earlier versions of
    # this test passed for the wrong reason — one produced an empty diff, the
    # other neutered the polluter so the baseline stopped reproducing and the
    # run failed for that reason instead of for the protection. Keeping the
    # experiment intact leaves rejection as the only thing under test.
    diff = make_diff(pollute_repo, {edited: "# touched\n" + POLLUTE_FILES[edited]})
    assert diff.strip(), "the fixture edit produced no diff; the test would prove nothing"
    code, out = run_cli(
        "--repo",
        str(pollute_repo),
        "--json",
        "verify",
        str(write_diff(diff)),
        TARGET,
        "--allow-partial-sandbox",
        "--precondition",
        POLLUTER,
    )
    assert receipt_of(out).get("verdict") != "verified_against_approved_checks"
    # The property is rejection *before execution*, not merely a non-verified
    # outcome: an unprotected judge artifact would let the patch run and fail
    # for some other reason, which looks the same from the verdict alone.
    events = [
        json.loads(line)
        for path in (pollute_repo / ".rift").rglob("ledger.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    rejected = [e for e in events if e["kind"] == "changeset_rejected"]
    assert rejected, f"the patch was not rejected before execution: {[e['kind'] for e in events]}"
    assert "protected" in json.dumps(rejected[0]["payload"]).lower(), rejected[0]


# ------------------------------------------------------------------ 5-6. wiring


def test_removing_precondition_plumbing_makes_the_gate_test_red(
    pollute_repo, run_cli, write_diff, state_fix_diff, monkeypatch
):
    """D5. With the contract not built, the gate runs the bare target — which
    passes at baseline, so the patch can no longer verify."""
    import riftagent.app as app

    monkeypatch.setattr(app, "verify_reproducer", lambda *a, **k: (_ for _ in ()).throw(AssertionError("x")))
    with pytest.raises(AssertionError):
        run_cli(
            "--repo",
            str(pollute_repo),
            "--json",
            "verify",
            str(write_diff(state_fix_diff)),
            TARGET,
            "--allow-partial-sandbox",
            "--precondition",
            POLLUTER,
        )


def test_removing_signature_plumbing_makes_the_expectation_test_red():
    """D6. `signature_compatible` is what an --expect-signature mismatch runs
    through; a version that always agrees accepts a different failure as this
    failure."""
    from riftagent.app import signature_compatible
    from riftagent.records import Signature

    expected = Signature("AssertionError", "registry is dirty")
    assert signature_compatible(expected, Signature("AssertionError", "registry is dirty here"))
    assert not signature_compatible(expected, Signature("ImportError", "registry is dirty"))
    assert not signature_compatible(expected, Signature("AssertionError", "something else"))
    assert not signature_compatible(expected, None)
    # A bare type matches any message of that type, and an empty expectation
    # means "freeze what is observed", never "anything will do".
    assert signature_compatible(Signature("AssertionError", ""), Signature("AssertionError", "whatever"))
    assert not signature_compatible(Signature("AssertionError", ""), Signature("ValueError", "whatever"))


def test_an_incompatible_baseline_signature_stops_the_gate(pollute_repo, run_cli, write_diff, state_fix_diff):
    """The declared failure is not the observed one, so nothing after the
    baseline would be measuring the declared experiment."""
    code, out = run_cli(
        "--repo",
        str(pollute_repo),
        "--json",
        "verify",
        str(write_diff(state_fix_diff)),
        TARGET,
        "--allow-partial-sandbox",
        "--precondition",
        POLLUTER,
        "--expect-signature",
        "ImportError: no such module",
    )
    receipt = receipt_of(out)
    assert receipt.get("verdict") != "verified_against_approved_checks", receipt
    events = [json.loads(x) for x in (pollute_repo / ".rift").rglob("ledger.jsonl") for x in x.read_text().splitlines()]
    assert any(e["kind"] == "reproduction_failed" for e in events), [e["kind"] for e in events]


def test_a_compatible_expected_signature_verifies(pollute_repo, run_cli, write_diff, state_fix_diff):
    code, out = run_cli(
        "--repo",
        str(pollute_repo),
        "--json",
        "verify",
        str(write_diff(state_fix_diff)),
        TARGET,
        "--allow-partial-sandbox",
        "--precondition",
        POLLUTER,
        "--expect-signature",
        "AssertionError",
    )
    assert receipt_of(out).get("verdict") == "verified_against_approved_checks"
