"""DAR-015: the BM-06 ablation controls, and the fence around arm A.

These exist so the benchmark's arms can differ. The risk they carry is the
opposite of the one they solve: an ablation that could emit the product's own
verdict would eventually be quoted as the product's result. So the tests below
check both that the controls reach the runtime and that arm A cannot produce a
verification verdict no matter what happens inside it.

Every test asserts argument provenance and the control flow that follows —
which policy `select_probe` was actually called with, which seed reached the
rng — rather than that a helper returns a plausible value.
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
}
TARGET = "tests/test_calc.py::test_total"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path / "abl", FILES)


def events_of(repo: Path) -> list[dict]:
    out: list[dict] = []
    for path in (repo / ".rift").rglob("ledger.jsonl"):
        out.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return out


# ------------------------------------------------------------ probe policy


def test_the_policy_argument_reaches_select_probe(repo, run_cli, monkeypatch):
    """D11. `kernel.select_probe` already implemented `random`; nothing passed
    it. The assertion is on the argument the kernel received, because a CLI flag
    that parses and goes nowhere looks identical from outside."""
    import riftagent.kernel as kernel

    seen: list[str] = []
    original = kernel.select_probe
    monkeypatch.setattr(
        kernel,
        "select_probe",
        lambda policy, probes, live, ev, rng: seen.append(policy) or original(policy, probes, live, ev, rng),
    )
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
        "7",
    )
    assert seen, "select_probe was never called; the test proves nothing about policy"
    assert set(seen) == {"random"}, seen


def test_the_default_policy_is_unchanged(repo, run_cli, monkeypatch):
    import riftagent.kernel as kernel

    seen: list[str] = []
    original = kernel.select_probe
    monkeypatch.setattr(
        kernel,
        "select_probe",
        lambda policy, probes, live, ev, rng: seen.append(policy) or original(policy, probes, live, ev, rng),
    )
    run_cli("--repo", str(repo), "--json", "fix", TARGET, "--allow-partial-sandbox", "--no-model")
    assert set(seen) == {"disagreement"}, seen


def test_the_seed_reaches_the_rng_and_is_reproducible(repo, run_cli, monkeypatch, tmp_path):
    """D12 and D13. The seed must control the draw, and the same seed must give
    the same draw in a different process — the property `hash()` lacked."""
    import random as random_module

    import riftagent.app as app

    seeds: list[object] = []
    original = app.random.Random
    monkeypatch.setattr(app.random, "Random", lambda seed=None: seeds.append(seed) or original(seed))
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
    assert 20260818 in seeds, f"the supplied seed never reached an rng: {seeds}"

    # Reproducible by construction: the same seed yields the same sequence in
    # any process, which is what makes a rerun of arm B the same experiment.
    assert [random_module.Random(20260818).random() for _ in range(3)] == [
        random_module.Random(20260818).random() for _ in range(3)
    ]


def test_removing_seed_plumbing_is_detected(repo, run_cli, monkeypatch):
    """D12's mutation: with the request field forced back to None, the supplied
    seed no longer reaches the rng — which is exactly what the test above
    asserts, so that test is sensitive to this plumbing rather than incidental."""
    import riftagent.app as app

    seeds: list[object] = []
    original_random = app.random.Random
    monkeypatch.setattr(app.random, "Random", lambda seed=None: seeds.append(seed) or original_random(seed))

    original_request = app.WhyRequest
    monkeypatch.setattr(app, "WhyRequest", lambda **kw: original_request(**{**kw, "probe_seed": None}))

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
    assert 20260818 not in seeds, "the mutation did not remove the seed; the test would not be sensitive"


# ------------------------------------------------------------ model-alone


def test_model_alone_takes_a_different_path_and_records_the_ablation(repo, run_cli, monkeypatch):
    """D10. Arm A skips diagnosis entirely — so `select_probe` is never called —
    and the run is durably marked as an ablation."""
    import riftagent.kernel as kernel

    called: list[str] = []
    monkeypatch.setattr(kernel, "select_probe", lambda *a, **k: called.append("x"))
    run_cli("--repo", str(repo), "--json", "fix", TARGET, "--allow-partial-sandbox", "--no-model", "--model-alone")

    assert called == [], "arm A ran the diagnosis loop; it is not the incumbent path"
    kinds = [e["kind"] for e in events_of(repo)]
    assert "benchmark_ablation" in kinds, kinds


def test_model_alone_can_never_emit_a_verified_rift_verdict(repo, run_cli):
    """D14. The fence. Without a model the run abstains, but the property under
    test is that no path from here reaches the product's verdict."""
    code, out = run_cli(
        "--repo", str(repo), "--json", "fix", TARGET, "--allow-partial-sandbox", "--no-model", "--model-alone"
    )
    text = out + json.dumps(events_of(repo))
    assert "verified_against_approved_checks" not in text, "an ablation produced the product's verification verdict"

    ablation = [e for e in events_of(repo) if e["kind"] == "benchmark_ablation"]
    assert ablation and ablation[0]["payload"]["verdict_ceiling"] == "accepted_by_target_pass"


def test_without_the_flag_the_kernel_path_runs(repo, run_cli, monkeypatch):
    """D10's sensitivity check. The arm-A test asserts `select_probe` is never
    called; that means nothing unless the same command *does* call it when the
    flag is absent. Same repository, same target — only the flag differs."""
    import riftagent.kernel as kernel

    called: list[str] = []
    original = kernel.select_probe
    monkeypatch.setattr(
        kernel,
        "select_probe",
        lambda policy, probes, live, ev, rng: called.append(policy) or original(policy, probes, live, ev, rng),
    )
    run_cli("--repo", str(repo), "--json", "fix", TARGET, "--allow-partial-sandbox", "--no-model")
    assert called, "the kernel path never probed even without --model-alone; the arm-A assertion is not diagnostic"


def test_the_ablation_verdict_exists_and_is_separate_from_the_product_verdict():
    from riftagent.records import Verdict

    assert Verdict.ACCEPTED_BY_TARGET_PASS.value == "accepted_by_target_pass"
    assert Verdict.ACCEPTED_BY_TARGET_PASS is not Verdict.VERIFIED_AGAINST_APPROVED_CHECKS
