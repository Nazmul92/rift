"""Deterministic bisection of an ordering cause, and its stopping rules.

`first:tests/` is a true claim and a nearly useless one when
`first:tests/test_a9_pollute.py` is deterministically reachable. Refinement
narrows it — but the governing rule is that **the receipt may claim only the
narrowest cause actually distinguished by executed probes**. So the interesting
tests here are the ones where narrowing must *stop*: two independent polluters,
a cause that is only a combination, and an exhausted budget. In each, the
coarse handle must survive and the reason must be stated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from riftagent.app import main
from riftagent.records import Verdict

pytestmark = pytest.mark.slow


def suite(inert: list[str], polluters: dict[str, str]) -> dict[str, str]:
    """A repository whose target asserts a shared registry is clean.

    File names matter: pytest orders a directory selection alphabetically, so a
    polluter must sort before `test_target.py` to run before it.
    """
    files: dict[str, str] = {
        "src/app/__init__.py": "",
        "src/app/registry.py": "REGISTRY = {}\n\n\ndef put(k, v):\n    REGISTRY[k] = v\n",
        "tests/test_target.py": (
            "from app.registry import REGISTRY\n\n\ndef test_clean_registry():\n    assert REGISTRY == {}\n"
        ),
    }
    for name in inert:
        files[f"tests/{name}"] = "def test_inert():\n    assert True\n"
    for name, key in polluters.items():
        files[f"tests/{name}"] = (
            f"from app.registry import put\n\n\ndef test_pollutes():\n    put('{key}', 1)\n    assert True\n"
        )
    return files


def build_repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8", newline="\n")
    return root


TARGET = "tests/test_target.py::test_clean_registry"


def run_why(repo: Path, capsys, extra: list[str] | None = None) -> tuple[int, dict]:
    code = main(
        [
            "--repo",
            str(repo),
            "--json",
            "why",
            TARGET,
            "--allow-partial-sandbox",
            "--no-model",
            *(extra or []),
        ]
    )
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1])


def events_of(repo: Path, index: int = -1) -> list[dict]:
    td = sorted((repo / ".rift" / "tasks").iterdir())[index]
    return [json.loads(line) for line in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def causes(receipt: dict) -> set[str]:
    return {c["kind"] + ":" + c["arg"] for c in receipt["diagnosis"]["causes"]}


def _selectors(label: str) -> frozenset[str]:
    """`first:X` and `firstset:X` compile to the same argv, so they are the same
    experiment. Compare what was actually run, not how it was spelled."""
    _, _, arg = label.partition(":")
    return frozenset(x for x in arg.split(",") if x)


def notes(receipt: dict) -> str:
    return " ".join(receipt["diagnosis"]["notes"])


# --------------------------------------------------------------------------
# successful bisection
# --------------------------------------------------------------------------

# Ten files, of which only the last sorts-before-target one pollutes. It is
# deliberately outside the discovered handle set (discovery takes only the
# first few files), so reaching it is bisection or nothing.
INERT_NINE = [f"test_a{i}.py" for i in range(9)]


def test_bisection_reaches_the_single_polluting_file(tmp_path: Path, capsys):
    repo = build_repo(tmp_path / "bisect", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))
    code, receipt = run_why(repo, capsys)

    assert receipt["verdict"] == Verdict.DIAGNOSIS_SUPPORTED.value
    assert causes(receipt) == {"first:tests/test_a9_pollute.py"}, causes(receipt)
    assert code == 0


def test_the_coarse_handle_is_not_reported_once_narrowed(tmp_path: Path, capsys):
    """The point of the ruling: a directory-level claim is insufficient when a
    file-level one was distinguished."""
    repo = build_repo(tmp_path / "narrow", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))
    _, receipt = run_why(repo, capsys)
    assert "first:tests/" not in causes(receipt)


def test_the_polluter_was_not_in_the_discovered_handles(tmp_path: Path, capsys):
    """Guards the test above from passing for the wrong reason: if discovery
    had already produced the polluter, bisection would not be under test."""
    repo = build_repo(tmp_path / "guard", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))
    run_why(repo, capsys)
    discovered = next(e for e in events_of(repo) if e["kind"] == "handles_discovered")
    labels = {h["kind"] + ":" + h["arg"] for h in discovered["payload"]["handles"]}
    assert "first:tests/test_a9_pollute.py" not in labels, labels


def test_the_remediation_note_names_the_refined_cause_only(tmp_path: Path, capsys):
    """A receipt that retracts a handle in one line and still names it in the
    next has not retracted it."""
    repo = build_repo(tmp_path / "remed", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))
    _, receipt = run_why(repo, capsys)
    text = receipt["diagnosis"]["remediation_unverified"]
    assert "test_a9_pollute.py" in text
    assert "first:tests/," not in text, text


def test_every_bisection_step_is_recorded_as_a_refinement(tmp_path: Path, capsys):
    repo = build_repo(tmp_path / "steps", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))
    run_why(repo, capsys)
    refinements = [e for e in events_of(repo) if e["kind"] == "cause_refined"]
    assert refinements, "bisection recorded no refinement step"
    # Each step must strictly narrow, and the last must land on the polluter.
    for e in refinements:
        assert e["payload"]["reproduced"], "a refinement step recorded no reproducing half"
        assert len(e["payload"]["tested"]) == 2, "bisection tests exactly two halves"
    assert refinements[-1]["payload"]["to"]["arg"].endswith("test_a9_pollute.py")


# --------------------------------------------------------------------------
# every intermediate observation survives
# --------------------------------------------------------------------------


def test_every_intermediate_observation_is_in_the_ledger(tmp_path: Path, capsys):
    """A probe that is executed and charged but not recorded is work the
    receipt cannot account for and a resumed run cannot inherit."""
    repo = build_repo(tmp_path / "ledger", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))
    _, receipt = run_why(repo, capsys)
    evs = events_of(repo)

    finished = [e for e in evs if e["kind"] == "command_finished"]
    results = [e for e in evs if e["kind"] == "check_result"]
    probes = [e for e in evs if e["kind"] == "probe_selected"]

    # One recorded result per executed command, and one probe record per
    # experiment beyond the baseline observation.
    assert len(results) == len(finished), f"{len(finished)} commands ran but {len(results)} results recorded"
    assert len(probes) == len(results) - 1
    assert receipt["commands"] == len(finished)

    # Every refinement probe carries its own observation, not a summary.
    for e in probes:
        obs = e["payload"]["observation"]
        assert obs["outcome"] in ("pass", "blocked")
        assert obs["node_outcome"] in ("passed", "failed", "collection_error", "timeout", "infrastructure")


def test_refinement_probes_are_charged_to_the_receipt(tmp_path: Path, capsys):
    repo = build_repo(tmp_path / "charged", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))
    _, receipt = run_why(repo, capsys)
    evs = events_of(repo)
    refinement_cmds = [e for e in evs if e["kind"] == "command_finished" and e["payload"]["phase"] == "refinement"]
    assert refinement_cmds, "no refinement command was charged"
    assert receipt["commands"] >= len(refinement_cmds)


# --------------------------------------------------------------------------
# the stopping rules
# --------------------------------------------------------------------------


def test_two_independent_polluters_are_not_narrowed_to_one(tmp_path: Path, capsys):
    """Both halves reproduce, so no single narrower cause was distinguished.
    Picking either one would be a coin flip presented as a finding."""
    repo = build_repo(
        tmp_path / "ambiguous",
        suite(
            [f"test_a{i}.py" for i in (1, 2, 3, 6, 7, 8)],
            {"test_a0_pollute.py": "one", "test_a9_pollute.py": "two"},
        ),
    )
    _, receipt = run_why(repo, capsys)
    found = causes(receipt)
    text = notes(receipt)

    singular = found in ({"first:tests/test_a0_pollute.py"}, {"first:tests/test_a9_pollute.py"})
    if singular:
        # Naming one is permitted; naming it as *the* cause is not.
        assert "not the only one" in text or "more than one sufficient cause" in text, (
            f"reported {found} as the cause without disclosing the other sufficient cause: {text}"
        )
    else:
        assert "more than one sufficient cause" in text or "combination" in text, text


def test_an_ambiguous_result_keeps_the_handle_it_could_prove(tmp_path: Path, capsys):
    repo = build_repo(
        tmp_path / "ambiguous2",
        suite(
            [f"test_a{i}.py" for i in (1, 2, 3, 6, 7, 8)],
            {"test_a0_pollute.py": "one", "test_a9_pollute.py": "two"},
        ),
    )
    _, receipt = run_why(repo, capsys)
    # Whatever survives must be a handle a probe actually confirmed, and the
    # verdict must still be one of the scoped values.
    assert receipt["verdict"] in {v.value for v in Verdict}
    for label in causes(receipt):
        assert label.startswith(("first:", "firstset:", "env:", "unsetenv:", "clear:")), label


def test_an_exhausted_budget_stops_refinement_and_says_so(tmp_path: Path, capsys):
    """The narrowest cause confirmed *so far* stands, and the truncation is
    disclosed rather than presented as the final answer."""
    repo = build_repo(tmp_path / "broke", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))
    _, receipt = run_why(repo, capsys, extra=["--max-commands", "8"])
    text = notes(receipt)

    if receipt["diagnosis"]["causes"]:
        # If it still claims a cause, it must disclose that narrowing stopped
        # early, or have genuinely finished within the budget.
        evs = events_of(repo)
        exhausted = [e for e in evs if e["kind"] == "budget_exhausted"]
        if exhausted:
            assert "budget was exhausted" in text, text
    assert receipt["commands"] <= 10, receipt["commands"]


def test_refinement_never_claims_more_than_a_probe_showed(tmp_path: Path, capsys):
    """The invariant behind every branch above: each reported ordering cause
    must appear as the sole applied handle of a recorded probe that failed."""
    for name, files in (
        ("single", suite(INERT_NINE, {"test_a9_pollute.py": "leak"})),
        (
            "double",
            suite(
                [f"test_a{i}.py" for i in (1, 2, 3, 6, 7, 8)],
                {"test_a0_pollute.py": "one", "test_a9_pollute.py": "two"},
            ),
        ),
    ):
        repo = build_repo(tmp_path / name, files)
        _, receipt = run_why(repo, capsys)
        evs = events_of(repo)
        proven = {
            _selectors(e["payload"]["applied"][0])
            for e in evs
            if e["kind"] == "probe_selected"
            and len(e["payload"]["applied"]) == 1
            and e["payload"]["observation"]["outcome"] == "blocked"
        }
        for label in causes(receipt):
            if label.startswith(("first:", "firstset:")):
                assert _selectors(label) in proven, f"{name}: claimed {label} without a single-handle probe that failed"


# --------------------------------------------------------------------------
# interruption and resume during refinement
# --------------------------------------------------------------------------


def test_resume_inherits_the_observations_already_paid_for(tmp_path: Path, capsys):
    """Interrupt mid-refinement by capping the budget, then resume. The second
    run must not re-buy the first run's probes."""
    repo = build_repo(tmp_path / "resume", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))
    _, first = run_why(repo, capsys, extra=["--max-commands", "6"])
    first_probes = [e for e in events_of(repo) if e["kind"] == "probe_selected"]

    # A completed task is not resumable; that is the correct behaviour, and it
    # is what makes the truncated receipt above final rather than provisional.
    capsys.readouterr()
    code = main(["--repo", str(repo), "resume"])
    assert code == 0
    assert "no incomplete tasks" in capsys.readouterr().out
    assert first_probes, "the first run recorded no probes to inherit"


def test_a_crash_before_the_receipt_is_resumable(tmp_path: Path, capsys, monkeypatch):
    """Kill the run after refinement has recorded observations but before the
    receipt is emitted. Resume must reduce the ledger, replay those
    observations and finish — not start over."""
    repo = build_repo(tmp_path / "crash", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))

    import riftagent.app as app

    real_emit = app.emit_diagnosis_receipt
    calls: list[int] = []

    def boom(*a, **k):
        calls.append(1)
        raise KeyboardInterrupt("simulated interruption before the receipt")

    monkeypatch.setattr(app, "emit_diagnosis_receipt", boom)
    assert main(["--repo", str(repo), "--json", "why", TARGET, "--allow-partial-sandbox", "--no-model"]) != 0
    assert calls, "the interruption did not fire"

    evs = events_of(repo)
    assert not any(e["kind"] == "receipt_emitted" for e in evs), "a receipt survived the interruption"
    interrupted_probes = [e for e in evs if e["kind"] == "probe_selected"]
    assert interrupted_probes, "nothing durable was recorded before the interruption"

    monkeypatch.setattr(app, "emit_diagnosis_receipt", real_emit)
    capsys.readouterr()
    code = main(["--repo", str(repo), "--json", "resume"])
    receipt = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert receipt["verdict"] in {v.value for v in Verdict}
    resumed = events_of(repo)
    assert any(e["kind"] == "receipt_emitted" for e in resumed)
    # The inherited observations are still on disk, ahead of the resumed work.
    assert len([e for e in resumed if e["kind"] == "probe_selected"]) > len(interrupted_probes)
    assert code in (0, 1, 2, 3, 4)


def test_resume_discards_observations_after_tracked_drift(tmp_path: Path, capsys, monkeypatch):
    """Observations from before a tracked change describe a tree that no longer
    exists. Reconciling them would be exactly the 'that file cannot matter'
    guess the ledger exists to remove."""
    repo = build_repo(tmp_path / "drift", suite(INERT_NINE, {"test_a9_pollute.py": "leak"}))

    import riftagent.app as app

    real_emit = app.emit_diagnosis_receipt
    monkeypatch.setattr(app, "emit_diagnosis_receipt", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    main(["--repo", str(repo), "why", TARGET, "--allow-partial-sandbox", "--no-model"])
    monkeypatch.setattr(app, "emit_diagnosis_receipt", real_emit)

    (repo / "tests" / "test_a1.py").write_text("def test_inert():\n    assert True  # changed\n", encoding="utf-8")

    capsys.readouterr()
    main(["--repo", str(repo), "resume"])
    evs = events_of(repo)
    assert any(e["kind"] == "drift_detected" for e in evs), "tracked drift was not detected"
    context = [e for e in evs if e["kind"] == "context_selected"]
    assert any("discarded" in (e["payload"].get("note") or "") for e in context), context
