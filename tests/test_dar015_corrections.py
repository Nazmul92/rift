"""The four DAR-015 consuming-path defects, and the properties that replace them.

Each defect had a test that passed while the property was absent, which is why
they survived review. The pattern in every case was the same: the test asserted
something true for a reason other than the one it named — an absent verdict in a
run that abstained, a seed observed only when supplied, a contract frozen but
never enforced. The tests here assert the property in the situation where it
could actually be violated.

No provider is configured and no request is made.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import build_repo, make_diff

# The order-dependent shape: the target passes alone and fails after the
# polluter. Arm A must still be given this experiment, or it sees a passing
# target and reports nothing to repair.
FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/registry.py": "REGISTRY = {}\n\n\ndef add(name):\n    REGISTRY[name] = True\n",
    "src/pkg/report.py": "from pkg.registry import REGISTRY\n\n\ndef summary():\n    return sorted(REGISTRY)\n",
    "tests/test_pollute.py": "from pkg.registry import add\n\n\ndef test_pollute():\n    add('leaked')\n",
    "tests/test_clean.py": "from pkg.report import summary\n\n\ndef test_clean():\n    assert summary() == []\n",
    "tests/test_keep.py": "def test_keep():\n    assert True\n",
    "suite/__init__.py": "",
    "suite/test_dir_pollute.py": "from pkg.registry import add\n\n\ndef test_dir_pollute():\n    add('dir')\n",
}
TARGET = "tests/test_clean.py::test_clean"
POLLUTER = "tests/test_pollute.py::test_pollute"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path / "order", FILES)


def events_of(repo: Path) -> list[dict]:
    out: list[dict] = []
    for path in (repo / ".rift").rglob("ledger.jsonl"):
        out.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return out


def receipt_of(out: str) -> dict:
    for line in reversed(out.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


# ------------------------------------------------------- 1. arm A gets the task


def test_arm_a_reproduces_the_frozen_task_instead_of_the_bare_target(repo, run_cli, monkeypatch):
    """The fifth occurrence of the isolated-baseline defect.

    Arm A established its baseline on the bare target. For this shape the target
    passes there, so arm A reported "nothing to repair" and never proposed —
    making it incomparable with B and C, which do receive the experiment.
    """
    proposed: list[str] = []
    monkeypatch.setattr("riftagent.app._request_change", lambda *a, **k: proposed.append("asked") or None)
    run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--model-alone",
        "--precondition",
        POLLUTER,
        "--no-model",
    )
    events = events_of(repo)
    assert any(e["kind"] == "reproducer_frozen" for e in events), "arm A froze no experiment"
    # The baseline reproduced, so arm A had a failure to work from and asked for
    # a patch. Without the reproducer it stops before this point.
    assert proposed == ["asked"], [e["kind"] for e in events]


def test_removing_arm_a_reproducer_plumbing_makes_that_test_red(repo, run_cli, monkeypatch):
    """The mutation: with the frozen contract withheld from arm A, the baseline
    runs bare, passes, and no proposal is ever requested."""
    import riftagent.app as app

    proposed: list[str] = []
    monkeypatch.setattr(app, "_request_change", lambda *a, **k: proposed.append("asked") or None)
    monkeypatch.setattr(app, "proj_reproducer", lambda flow: None)
    run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--model-alone",
        "--precondition",
        POLLUTER,
        "--no-model",
    )
    assert proposed == [], "the mutation did not remove the reproducer; the test above is not sensitive"


# ------------------------------------------- 2. the ablation verdict is a verdict


def test_a_successful_arm_a_run_carries_the_ablation_verdict_everywhere(repo, run_cli, monkeypatch):
    """The receipt, the replayed receipt and the ledger-derived receipt must all
    agree. Previously the value lived only in a gate event's artifacts dict and
    no receipt carried it at all."""
    import riftagent.app as app
    from riftagent.records import Verdict

    fix = make_diff(repo, {"src/pkg/report.py": "def summary():\n    return []\n"})
    monkeypatch.setattr(app, "_request_change", lambda *a, **k: (fix, "arm A patch"))

    code, out = run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--model-alone",
        "--precondition",
        POLLUTER,
    )
    receipt = receipt_of(out)
    assert receipt.get("verdict") == Verdict.ACCEPTED_BY_TARGET_PASS.value, receipt
    assert receipt.get("benchmark_ablation") == "model_alone", receipt
    assert receipt.get("product_verification_eligible") is False, receipt

    # Derived again from the ledger alone: same verdict, no live state involved.
    from riftagent.kernel import derive_verdict
    from riftagent.records import read_events, reduce

    for path in (repo / ".rift").rglob("ledger.jsonl"):
        events, truncated = read_events(path)
        proj = reduce(events, truncated)
        if proj.ablation:
            assert derive_verdict(proj).verdict is Verdict.ACCEPTED_BY_TARGET_PASS
            break
    else:
        pytest.fail("no ledger carried the ablation state")


def test_removing_the_ablation_reduction_makes_that_test_red(repo, run_cli, monkeypatch):
    """The mutation: with the event no longer reduced, the kernel falls back to
    its ordinary derivation and the ablation verdict disappears."""
    from riftagent.kernel import derive_verdict
    from riftagent.records import TaskProjection, Verdict

    proj = TaskProjection(ablation="")
    assert derive_verdict(proj).verdict is not Verdict.ACCEPTED_BY_TARGET_PASS
    assert derive_verdict(TaskProjection(ablation="model_alone")).verdict is not (
        Verdict.VERIFIED_AGAINST_APPROVED_CHECKS
    )


def test_the_verified_verdict_is_unreachable_from_the_ablation(repo):
    """The ceiling, asserted on the derivation rather than on one run's output."""
    from riftagent.kernel import derive_verdict
    from riftagent.records import GatePhase, TaskProjection, Verdict

    # Even with every gate phase somehow recorded complete, the ablation cannot
    # produce the product's verdict.
    proj = TaskProjection(
        ablation="model_alone",
        completed_phases={
            GatePhase.BASELINE,
            GatePhase.CANDIDATE,
            GatePhase.WITHDRAWAL,
            GatePhase.REAPPLY,
            GatePhase.PRESERVATION,
        },
    )
    assert derive_verdict(proj).verdict is Verdict.ACCEPTED_BY_TARGET_PASS


# ------------------------------------------------------- 3. seed validity


@pytest.mark.parametrize(
    "extra, expected",
    [
        (["--probe-policy", "random"], "requires --probe-seed"),
        (["--probe-seed", "5"], "has no effect"),
    ],
)
def test_a_meaningless_policy_and_seed_pair_is_a_usage_error(repo, run_cli, extra, expected):
    """Rejected before the sandbox, so a run that cannot describe itself never
    executes a probe or reaches a provider."""
    code, out = run_cli("--repo", str(repo), "--json", "fix", TARGET, "--allow-partial-sandbox", *extra)
    assert code != 0
    assert expected in run_cli.err, run_cli.err
    assert not (repo / ".rift" / "tasks").exists(), "a task directory was created for a rejected run"
    assert events_of(repo) == [], "events were recorded for a run that should not have started"


def test_a_valid_random_policy_is_recorded_durably(repo, run_cli):
    run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--no-model",
        "--probe-policy",
        "random",
        "--probe-seed",
        "20260818",
    )
    frozen = [e for e in events_of(repo) if e["kind"] == "probe_policy_frozen"]
    assert frozen, [e["kind"] for e in events_of(repo)]
    assert frozen[0]["payload"] == {"policy": "random", "seed": 20260818}


def test_resume_reuses_the_recorded_policy_rather_than_the_command_line(repo, run_cli):
    """The reduced value is what the diagnosis loop reads, so a second
    invocation cannot silently change the experiment."""
    from riftagent.records import read_events, reduce

    run_cli(
        "--repo",
        str(repo),
        "--json",
        "fix",
        TARGET,
        "--allow-partial-sandbox",
        "--no-model",
        "--probe-policy",
        "random",
        "--probe-seed",
        "99",
    )
    for path in (repo / ".rift").rglob("ledger.jsonl"):
        events, truncated = read_events(path)
        proj = reduce(events, truncated)
        if proj.probe_seed is not None:
            assert (proj.probe_policy, proj.probe_seed) == ("random", 99)
            break
    else:
        pytest.fail("no ledger carried the frozen probe policy")


# --------------------------------- 4. every frozen reproducer is enforced


def test_a_signature_only_reproducer_is_validated_after_execution(repo, run_cli, write_diff, monkeypatch):
    """`run_episode` returned early for a contract with no preconditions, so the
    after-execution integrity check never ran and the contract was frozen but
    unenforced."""
    import riftagent.app as app

    seen: list[str] = []
    original = app._validate_reproducer

    def spy(flow, wt, phase, reproducer, source_digest, expected_tree, when, patch_paths=frozenset()):
        seen.append(when)
        return original(flow, wt, phase, reproducer, source_digest, expected_tree, when, patch_paths)

    monkeypatch.setattr(app, "_validate_reproducer", spy)
    diff = make_diff(repo, {"src/pkg/report.py": "def summary():\n    return []\n"})
    run_cli(
        "--repo",
        str(repo),
        "--json",
        "verify",
        str(write_diff(diff)),
        TARGET,
        "--allow-partial-sandbox",
        "--expect-signature",
        "AssertionError",
    )
    assert "after" in seen, f"the after-execution check never ran: {seen}"
    assert seen.count("after") >= 1 and "before" in seen


def test_a_directory_precondition_resolves_to_its_test_files(repo, run_cli, write_diff):
    """A directory selector must freeze the files it actually runs. Hashing the
    directory label records `<absent>` and calls it protected evidence."""
    diff = make_diff(repo, {"src/pkg/report.py": "def summary():\n    return []\n"})
    run_cli(
        "--repo",
        str(repo),
        "--json",
        "verify",
        str(write_diff(diff)),
        TARGET,
        "--allow-partial-sandbox",
        "--precondition",
        "suite/",
    )
    frozen = [e for e in events_of(repo) if e["kind"] == "reproducer_frozen"]
    assert frozen, [e["kind"] for e in events_of(repo)]
    protected = frozen[0]["payload"]["protected_added"]
    assert "suite/test_dir_pollute.py" in protected, protected
    assert "suite/" not in protected and "suite" not in protected, protected


def test_an_unresolvable_precondition_is_refused(repo, run_cli, write_diff):
    """Refused rather than frozen as an absent file."""
    diff = make_diff(repo, {"src/pkg/report.py": "def summary():\n    return []\n"})
    code, out = run_cli(
        "--repo",
        str(repo),
        "--json",
        "verify",
        str(write_diff(diff)),
        TARGET,
        "--allow-partial-sandbox",
        "--precondition",
        "tests/does_not_exist.py",
    )
    assert receipt_of(out).get("verdict") != "verified_against_approved_checks"
    assert any(e["kind"] == "reproduction_failed" for e in events_of(repo))
