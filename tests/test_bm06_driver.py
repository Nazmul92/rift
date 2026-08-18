"""The BM-06 driver's consuming path, not just its arithmetic.

The previous version of this file tested `report()` and a dry run that
substituted the CLI with a function raising on call. Every test passed while the
experiment underneath was ceremonial: all three arms issued one command, arm B's
seed selected nothing and changed per process, arm A's patch was never captured,
shadow evaluation always received `None`, and acceptance came from a process
return code. Helper tests over an experiment that does not run prove the helpers.

So the tests below execute the driver's live orchestration against a fake CLI and
fail on the specific ways it was wrong. Each arm-distinction test is paired with
a removal mutation showing it goes red when the property is taken away — a test
that cannot fail is a claim, not evidence.

No provider is configured and no request is made: `rift` is substituted
throughout, and one test asserts that a manifest failure makes zero calls.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parent.parent / "benchmark" / "bm06" / "driver.py"


def load_driver():
    spec = importlib.util.spec_from_file_location("bm06_driver", DRIVER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bm06_driver"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def fake_proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout, stderr="")


def manifest_with(tmp_path: Path, **overrides) -> dict:
    worktree = tmp_path / "repo"
    (worktree / ".rift").mkdir(parents=True, exist_ok=True)
    base = {
        "manifest_hash": "deadbeef",
        "arms": {"A": {}, "B": {"seed": 20260818}, "C": {}},
        "budget": {"scope": "bm06", "max_usd": 10.5},
        "model": {
            "id": "claude-sonnet-5",
            "price_input_per_mtok": 2.0,
            "price_output_per_mtok": 10.0,
            "max_output_tokens": 4000,
            "max_probes": 16,
            "max_attempts": 1,
            "max_commands": 400,
            "timeout_s": 600.0,
        },
        "cases": [
            {
                "case_id": "c1",
                "repo": "demo",
                "worktree": str(worktree),
                "target": "tests/test_x.py::test_y",
                "label": "gateable",
                "cause_class": "genuine_source_bug",
                "status": "OK",
                "signature": "AssertionError: boom",
                "preserve": ["tests/test_x.py::test_keep"],
            }
        ],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------- validation


def test_an_invalid_manifest_makes_zero_requests(tmp_path: Path, monkeypatch):
    """The fail-closed property. If validation can be reached *after* a request,
    the ceiling protects nothing."""
    d = load_driver()
    calls: list[list[str]] = []
    monkeypatch.setattr(d, "rift", lambda args, cwd, timeout=3600.0: calls.append(args) or fake_proc())

    manifest = manifest_with(tmp_path, budget={})  # no ceiling
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["driver", "--manifest", str(path), "--out", str(tmp_path / "r.json")])

    assert d.main() == 2, "an invalid manifest must not be run"
    assert calls == [], f"a request was made despite invalid manifest: {calls}"


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda m: m.update(arms={}), "arms is empty"),
        (lambda m: m.update(budget={}), "budget is empty"),
        (lambda m: m["cases"][0].update(preserve=[]), "preservation checks are empty"),
        (lambda m: m["cases"][0].update(signature=None), "no expected signature"),
        (lambda m: m["cases"][0].update(target=None), "no target"),
        (lambda m: m["cases"][0].update(status="GROUND_TRUTH_DISPUTED"), "DISPUTED"),
        (lambda m: m["cases"][0].update(worktree=""), "no worktree"),
        (lambda m: m["cases"][0].update(ordering_precondition="full suite"), "no exact reproducer"),
    ],
)
def test_validation_rejects_each_missing_property(tmp_path: Path, mutate, expected):
    d = load_driver()
    manifest = manifest_with(tmp_path)
    mutate(manifest)
    failures = d.validate_manifest(manifest, tmp_path)
    assert any(expected in f for f in failures), f"expected {expected!r} in {failures}"


def test_a_complete_manifest_validates(tmp_path: Path):
    d = load_driver()
    assert d.validate_manifest(manifest_with(tmp_path), tmp_path) == []


# --------------------------------------------------------------- arms differ


def test_the_three_arms_are_three_different_experiments(tmp_path: Path):
    """The defect that made the whole driver ceremonial: A, B and C all issued
    the same `rift fix` command."""
    d = load_driver()
    manifest = manifest_with(tmp_path)
    case = manifest["cases"][0]
    keys = {arm: d.orchestration_key(arm, case, manifest, "bm06") for arm in d.ARMS}
    assert len(set(keys.values())) == 3, f"arms collapsed to the same orchestration: {keys}"
    assert "--model-alone" in keys["A"], "arm A is not the model-alone path"
    assert "--probe-policy random" in keys["B"], "arm B does not select randomly"
    assert "--probe-policy" not in keys["C"], "arm C must use the shipped default policy"


def test_removing_the_arm_distinction_makes_the_previous_test_red(tmp_path: Path, monkeypatch):
    """The mutation. With per-arm arguments removed, arms collapse — and the
    assertion above is what notices."""
    d = load_driver()
    manifest = manifest_with(tmp_path)
    case = manifest["cases"][0]

    original = d.arm_argv
    monkeypatch.setattr(d, "arm_argv", lambda arm, c, m, s: original("C", c, m, s))
    keys = {arm: d.orchestration_key(arm, case, manifest, "bm06") for arm in d.ARMS}
    assert len(set(keys.values())) == 1, "mutation did not collapse the arms; the test would not be sensitive"


def test_arm_b_seed_is_stable_across_processes(tmp_path: Path):
    """`hash()` is randomised per process, so the old seed made every rerun of B
    a different experiment. The value is pinned here, not merely compared to
    itself."""
    d = load_driver()
    manifest = manifest_with(tmp_path)
    case = manifest["cases"][0]

    seed = d.probe_seed(manifest, case)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.util,sys,json;"
            f"spec=importlib.util.spec_from_file_location('d',{str(DRIVER)!r});"
            "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
            f"print(m.probe_seed(json.loads({json.dumps(json.dumps(manifest))}),"
            f"json.loads({json.dumps(json.dumps(case))})))",
        ],
        capture_output=True,
        text=True,
        env={"PYTHONHASHSEED": "1", "PATH": "/usr/bin:/bin", "SYSTEMROOT": "C:\\\\Windows"},
    )
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.strip()) == seed, "the seed changed in another process with a different hash seed"


def test_arm_b_seed_differs_per_case(tmp_path: Path):
    d = load_driver()
    manifest = manifest_with(tmp_path)
    a = dict(manifest["cases"][0], case_id="alpha")
    b = dict(manifest["cases"][0], case_id="beta")
    assert d.probe_seed(manifest, a) != d.probe_seed(manifest, b)


def test_an_unsupported_arm_is_refused_not_substituted(tmp_path: Path, monkeypatch):
    """The failure this file exists to prevent repeating: an arm the CLI cannot
    express must be named as unavailable, never quietly given another arm's
    command."""
    d = load_driver()
    issued: list[list[str]] = []

    def fake_rift(args, cwd, timeout=3600.0):
        if args[:2] == ["fix", "--help"]:
            return fake_proc(stdout="usage: rift fix [--max-probes N]")  # neither flag offered
        issued.append(args)
        return fake_proc(stdout=json.dumps({"verdict": "abstained"}))

    monkeypatch.setattr(d, "rift", fake_rift)
    manifest = manifest_with(tmp_path)
    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    out = tmp_path / "r.json"
    monkeypatch.setattr(sys, "argv", ["driver", "--manifest", str(path), "--out", str(out)])

    assert d.main() == 0
    records = json.loads(out.read_text(encoding="utf-8"))["records"]
    unavailable = {r["arm"] for r in records if r.get("arm_unavailable")}
    assert unavailable == {"A", "B"}, f"unsupported arms were not refused: {unavailable}"
    assert all("--model-alone" not in " ".join(a) for a in issued), "arm A ran despite being unsupported"


# --------------------------------------------------------------- evidence


def test_acceptance_comes_from_the_verdict_not_the_return_code(tmp_path: Path):
    d = load_driver()
    rec = d.record(
        manifest_with(tmp_path)["cases"][0],
        "C",
        fake_proc(stdout=json.dumps({"verdict": "abstained_model_unavailable"}), returncode=0),
        {"accepted": False},
    )
    assert rec["verdict"] == "abstained_model_unavailable"
    assert rec["accepted"] is False, "a zero exit code was treated as acceptance"


def test_shadow_evaluation_uses_arm_a_patch_bytes(tmp_path: Path, monkeypatch):
    """`None` is valid only when arm A produced no patch. Passing `None` while a
    patch exists evaluates nothing and reports it as a result."""
    d = load_driver()
    patch = tmp_path / "a.diff"
    patch.write_text("--- a\n+++ b\n", encoding="utf-8")
    seen: list[str] = []

    def fake_rift(args, cwd, timeout=3600.0):
        seen.extend(args)
        return fake_proc(stdout=json.dumps({"verdict": "verified_against_approved_checks"}))

    monkeypatch.setattr(d, "rift", fake_rift)
    case = manifest_with(tmp_path)["cases"][0]
    out = d.evaluate_under_gate(case, patch, tmp_path)
    assert out["evaluated"] is True
    assert str(patch) in seen, "the gate was not given arm A's patch bytes"
    assert out["verdict"] == "verified_against_approved_checks"


def test_no_patch_means_not_evaluated_rather_than_a_verdict(tmp_path: Path):
    d = load_driver()
    out = d.evaluate_under_gate(manifest_with(tmp_path)["cases"][0], None, tmp_path)
    assert out["evaluated"] is False and "no patch" in out["reason"]


def test_the_captured_patch_is_the_durable_bytes(tmp_path: Path):
    d = load_driver()
    repo = tmp_path / "repo"
    td = repo / ".rift" / "tasks" / "t1"
    td.mkdir(parents=True)
    (td / "change-set.diff").write_bytes(b"--- a\n+++ b\n@@\n-x\n+y\n")
    got = d.capture_patch(repo, {"task_id": "t1"}, tmp_path / "patches", "A", "c1")
    assert got is not None and got.read_bytes() == b"--- a\n+++ b\n@@\n-x\n+y\n"


def test_an_empty_changeset_is_not_a_patch(tmp_path: Path):
    d = load_driver()
    repo = tmp_path / "repo"
    td = repo / ".rift" / "tasks" / "t1"
    td.mkdir(parents=True)
    (td / "change-set.diff").write_text("   \n", encoding="utf-8")
    assert d.capture_patch(repo, {"task_id": "t1"}, tmp_path / "p", "A", "c1") is None


def test_spend_is_read_from_the_ledger_by_reference(tmp_path: Path):
    """Not copied into the row. A copied total can drift from the ledger and
    still look authoritative."""
    d = load_driver()
    repo = tmp_path / "repo"
    (repo / ".rift").mkdir(parents=True)
    ledger = repo / ".rift" / "spend.jsonl"
    ledger.write_text(
        json.dumps({"scope": "bm06", "charged_usd": 0.25})
        + "\n"
        + json.dumps({"scope": "bm06", "charged_usd": 0.5})
        + "\n",
        encoding="utf-8",
    )
    ref = d.spend_event_ids(repo, "bm06", since=0)
    assert ref["event_ids"] == [0, 1]
    assert d.spend_from_ledger(ref) == pytest.approx(0.75)


def test_an_unreadable_ledger_reports_unmeasured_not_free(tmp_path: Path):
    d = load_driver()
    ref = d.spend_event_ids(tmp_path / "nope", "bm06", since=0)
    assert ref["present"] is False
    assert d.spend_from_ledger(ref) is None, "a missing ledger was reported as zero spend"


def test_ground_truth_correctness_is_set_on_a_live_run(tmp_path: Path, monkeypatch):
    """It was never computed outside the dry run, so every live case would have
    scored as incorrect regardless of what the arm did."""
    d = load_driver()

    def fake_rift(args, cwd, timeout=3600.0):
        if args[:2] == ["fix", "--help"]:
            return fake_proc(stdout="--model-alone --probe-policy")
        if "verify" in args:
            return fake_proc(stdout=json.dumps({"verdict": "verified_against_approved_checks"}))
        return fake_proc(stdout=json.dumps({"verdict": "verified_against_approved_checks", "task_id": "t1"}))

    monkeypatch.setattr(d, "rift", fake_rift)
    manifest = manifest_with(tmp_path)
    repo = Path(manifest["cases"][0]["worktree"])
    td = repo / ".rift" / "tasks" / "t1"
    td.mkdir(parents=True, exist_ok=True)
    (td / "change-set.diff").write_text("--- a\n+++ b\n", encoding="utf-8")

    path = tmp_path / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    out = tmp_path / "r.json"
    monkeypatch.setattr(
        sys, "argv", ["driver", "--manifest", str(path), "--out", str(out), "--patches", str(tmp_path / "p")]
    )
    assert d.main() == 0

    records = json.loads(out.read_text(encoding="utf-8"))["records"]
    assert len(records) == 3, "one record per arm"
    assert all("ground_truth_correct" in r for r in records), "ground truth was never computed"
    assert all(r["ground_truth_correct"] is True for r in records)
    assert {r["orchestration"] for r in records}.__len__() == 3, "arms shared an orchestration on a live run"
    arm_a = next(r for r in records if r["arm"] == "A")
    assert arm_a["shadow"]["evaluated"] is True, "arm A produced a patch but shadow evaluation received nothing"


# --------------------------------------------------------------- report


def row(arm: str, **kw) -> dict:
    base = {
        "case_id": kw.pop("case_id", "c1"),
        "arm": arm,
        "label": "gateable",
        "cause_class": "genuine_source_bug",
        "status": "OK",
        "accepted": False,
        "ground_truth_correct": False,
        "failed_phase": None,
        "support": None,
        "gate": None,
    }
    base.update(kw)
    return base


def test_an_abstention_stays_in_the_denominator():
    d = load_driver()
    out = d.report(
        [
            row("C", case_id="a", accepted=True, ground_truth_correct=True),
            row("C", case_id="b"),
            row("C", case_id="c"),
        ],
        {},
    )["per_arm"]["C"]
    assert out["gateable_attempted"] == 3
    assert out["verified_fix_yield"] == pytest.approx(1 / 3)


def test_gate_not_applicable_never_earns_verified_fix_credit():
    d = load_driver()
    out = d.report(
        [
            row(
                "C", case_id="obs", label="observationally_diagnosable", gate="not_applicable", support="observational"
            ),
            row("C", case_id="g", accepted=True, ground_truth_correct=True),
        ],
        {},
    )["per_arm"]["C"]
    assert out["gateable_attempted"] == 1
    assert out["verified_fix_yield"] == pytest.approx(1.0)
    assert out["observational_diagnosis_yield"] == pytest.approx(1.0)


def test_zero_correct_fixes_is_undefined_not_zero():
    d = load_driver()
    out = d.report([row("A", accepted=True)], {})["per_arm"]["A"]
    assert out["cost_per_correct_fix"] is None
    assert out["false_fix_acceptance"] == pytest.approx(1.0)


def test_invalid_and_disputed_labels_are_excluded_and_disclosed():
    d = load_driver()
    out = d.report(
        [
            row("C", case_id="ok", accepted=True, ground_truth_correct=True),
            row("C", case_id="bad", status="GROUND_TRUTH_INVALID"),
            row("C", case_id="dis", status="GROUND_TRUTH_DISPUTED"),
        ],
        {},
    )
    assert out["per_arm"]["C"]["gateable_attempted"] == 1
    assert {e["case_id"] for e in out["excluded"]} == {"bad", "dis"}


def test_failed_phase_survives_into_the_report():
    d = load_driver()
    out = d.report(
        [row("C", case_id="p1", failed_phase="candidate"), row("C", case_id="p2", failed_phase="withdrawal")], {}
    )
    assert out["per_arm"]["C"]["failed_phases"] == ["candidate", "withdrawal"]


def test_a_refused_arm_is_disclosed_in_the_report():
    d = load_driver()
    out = d.report([row("A", arm_unavailable="--model-alone"), row("C", accepted=True, ground_truth_correct=True)], {})
    assert out["arms_refused"] == ["A"]


# --------------------------------------------------------------- DAR-015: reproducer


def test_ground_truth_and_shadow_receive_the_exact_reproducer(tmp_path: Path, monkeypatch):
    """D7. A bare-target evaluation reports a passing baseline for an
    order-dependent case and scores it as already fixed — the defect that has
    now surfaced four times."""
    d = load_driver()
    issued: list[list[str]] = []

    def fake_rift(args, cwd, timeout=3600.0):
        if args[:2] == ["fix", "--help"]:
            return fake_proc(stdout="--precondition --expect-signature")
        issued.append(args)
        return fake_proc(stdout=json.dumps({"verdict": "verified_against_approved_checks"}))

    monkeypatch.setattr(d, "rift", fake_rift)
    case = dict(
        manifest_with(tmp_path)["cases"][0],
        reproducer=["tests/test_pollute.py::test_pollute"],
        signature="AssertionError: registry is dirty",
    )
    patch = tmp_path / "p.diff"
    patch.write_text("--- a\n+++ b\n", encoding="utf-8")

    out = d.evaluate_under_gate(case, patch, tmp_path)
    assert out["evaluated"] is True
    flat = " ".join(issued[0])
    assert "--precondition tests/test_pollute.py::test_pollute" in flat, flat
    assert "--expect-signature AssertionError: registry is dirty" in flat, flat


def test_a_missing_reproducer_capability_refuses_rather_than_falling_back(tmp_path: Path, monkeypatch):
    """D8. The installed CLI cannot express the experiment, so the case is not
    evaluated at all. Falling back to the bare target would produce a number
    that looks like a result and measures something else."""
    d = load_driver()
    issued: list[list[str]] = []

    def fake_rift(args, cwd, timeout=3600.0):
        if args[:2] == ["fix", "--help"]:
            return fake_proc(stdout="usage: rift fix [--max-probes N]")  # no --precondition
        issued.append(args)
        return fake_proc(stdout=json.dumps({"verdict": "verified_against_approved_checks"}))

    monkeypatch.setattr(d, "rift", fake_rift)
    case = dict(manifest_with(tmp_path)["cases"][0], reproducer=["tests/test_pollute.py::test_pollute"])
    patch = tmp_path / "p.diff"
    patch.write_text("--- a\n+++ b\n", encoding="utf-8")

    out = d.evaluate_under_gate(case, patch, tmp_path)
    assert out == {"evaluated": False, "reason": "NOT_RUN_REPRODUCER_UNSUPPORTED"}
    assert issued == [], "a verify ran despite the capability being absent"


def test_validation_requires_complete_arm_definitions_and_a_frozen_seed(tmp_path: Path):
    """C2-C4: checked before any CLI probing, so an incomplete manifest cannot
    reach a provider."""
    d = load_driver()
    manifest = manifest_with(tmp_path)
    del manifest["arms"]["B"]
    assert any("arms.B is not defined" in f for f in d.validate_manifest(manifest, tmp_path))

    manifest = manifest_with(tmp_path)
    manifest["arms"]["B"] = {}
    assert any("arms.B.seed is missing" in f for f in d.validate_manifest(manifest, tmp_path))

    manifest = manifest_with(tmp_path)
    del manifest["model"]["max_probes"]
    assert any("model.max_probes is missing" in f for f in d.validate_manifest(manifest, tmp_path))


def test_the_patch_is_captured_once(tmp_path: Path):
    """The duplicated write is gone; the bytes are the durable bytes."""
    d = load_driver()
    repo = tmp_path / "repo"
    td = repo / ".rift" / "tasks" / "t1"
    td.mkdir(parents=True)
    (td / "change-set.diff").write_bytes(b"--- a\n+++ b\n")
    got = d.capture_patch(repo, {"task_id": "t1"}, tmp_path / "p", "A", "c1")
    assert got is not None and got.read_bytes() == (td / "change-set.diff").read_bytes()


def test_arm_a_receives_the_frozen_reproducer(tmp_path: Path):
    """Arm A must reproduce the same task as B and C. Without the reproducer its
    baseline runs bare, an order-dependent target passes, and arm A reports
    nothing to repair — which is not a weaker arm, it is a different task."""
    d = load_driver()
    manifest = manifest_with(tmp_path)
    case = dict(
        manifest["cases"][0],
        reproducer=["tests/test_pollute.py::test_pollute"],
        signature="AssertionError: registry is dirty",
    )
    argv = " ".join(d.arm_argv("A", case, manifest, "bm06"))
    assert "--model-alone" in argv
    assert "--precondition tests/test_pollute.py::test_pollute" in argv, argv
    assert "--expect-signature AssertionError: registry is dirty" in argv, argv
    # C keeps the shipped path and gets the same experiment through its own gate.
    assert "--model-alone" not in " ".join(d.arm_argv("C", case, manifest, "bm06"))
