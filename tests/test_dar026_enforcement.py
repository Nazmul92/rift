"""The invariant lives inside the execution method, and the model is bound.

Two defects that survived every previous provenance pass because both looked
closed from the outside.

* `Bound` centralised the *plumbing* and left the invariant to the caller.
  `Bound.check` existed and had to be remembered — remembered for the arms,
  forgotten for ground-truth scoring, which pre-checked and then called `rift`
  directly with no check afterwards. A convention already broken once is not an
  invariant.
* The manifest declared `claude-sonnet-4-6` and carried that model's prices and
  output caps. The model that actually runs comes from `RIFT_LLM_MODEL`, and
  nothing compared them — so a run could be reserved, charged and reported
  entirely under the manifest's identity while a different model did the work.

No provider call is made anywhere in this file.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark" / "bm06"))

import driver as d  # noqa: E402


def runtime_tree(tmp_path: Path, body: str = "VERSION = 1\n") -> Path:
    root = tmp_path / "rt"
    pkg = root / "src" / "riftagent"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(body, encoding="utf-8")
    return root


def bound_for(tmp_path: Path) -> tuple[d.Bound, Path]:
    root = runtime_tree(tmp_path)
    return d.Bound(root, d.runtime_hash(root)[0]), root


def fake_proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout, stderr="")


# ------------------------------------------------- the invariant is structural


def test_an_unchanged_runtime_executes(tmp_path: Path, monkeypatch):
    bound, _ = bound_for(tmp_path)
    monkeypatch.setattr(d, "_rift", lambda *a, **k: fake_proc("ok"))
    assert bound.run(["fix", "--help"], cwd=tmp_path, label="probe").stdout == "ok"


def test_drift_before_an_invocation_means_the_subprocess_never_starts(tmp_path: Path, monkeypatch):
    """Refusing after a request has been paid for costs the money anyway."""
    bound, root = bound_for(tmp_path)
    started: list[list[str]] = []
    monkeypatch.setattr(d, "_rift", lambda args, **k: started.append(args) or fake_proc())

    (root / "src" / "riftagent" / "__init__.py").write_text("VERSION = 2\n", encoding="utf-8")
    with pytest.raises(d.RuntimeDrift, match="before"):
        bound.run(["fix", "x"], cwd=tmp_path, label="an arm")
    assert started == [], "the subprocess ran despite a drifted runtime"


def test_drift_during_an_invocation_is_detected_afterwards(tmp_path: Path, monkeypatch):
    """The window between "checked" and "ran" is the one that matters, and it
    cannot be closed beforehand."""
    bound, root = bound_for(tmp_path)

    def mutating(args, cwd, timeout=3600.0, env=None):
        (root / "src" / "riftagent" / "__init__.py").write_text("VERSION = 3\n", encoding="utf-8")
        return fake_proc("finished")

    monkeypatch.setattr(d, "_rift", mutating)
    with pytest.raises(d.RuntimeDrift, match="during"):
        bound.run(["fix", "x"], cwd=tmp_path, label="an arm")


def test_both_checks_use_the_invocation_directory(tmp_path: Path, monkeypatch):
    """Checking from one directory and running in another asks a different
    question than the one that matters — the cwd is on `sys.path`."""
    bound, _ = bound_for(tmp_path)
    asked: list[Path] = []
    monkeypatch.setattr(d, "assert_runtime", lambda root, frozen, env, when, cwd=None: asked.append(cwd))
    monkeypatch.setattr(d, "_rift", lambda *a, **k: fake_proc())

    worktree = tmp_path / "case"
    worktree.mkdir()
    bound.run(["fix", "x"], cwd=worktree, label="an arm")
    assert asked == [worktree, worktree], asked


def test_a_case_local_riftagent_is_rejected_from_the_real_cwd(tmp_path: Path):
    """A case worktree that shadows the package must be caught, and only asking
    from that directory catches it."""
    bound, root = bound_for(tmp_path)
    worktree = tmp_path / "case"
    (worktree / "riftagent").mkdir(parents=True)
    (worktree / "riftagent" / "__init__.py").write_text("VERSION = 'shadow'\n", encoding="utf-8")

    bound.check(tmp_path, "from elsewhere")  # clean directory: fine
    with pytest.raises(d.RuntimeDrift, match="resolves to"):
        bound.run(["fix", "x"], cwd=worktree, label="an arm")


# ------------------------------------- no scoring path bypasses the wrapper


def test_ground_truth_evaluation_gets_the_same_protection_as_an_arm(tmp_path: Path, monkeypatch):
    bound, _ = bound_for(tmp_path)
    checks: list[str] = []
    monkeypatch.setattr(d, "assert_runtime", lambda root, frozen, env, when, cwd=None: checks.append(when))
    monkeypatch.setattr(d, "_rift", lambda *a, **k: fake_proc("--precondition --expect-signature"))

    patch = tmp_path / "p.diff"
    patch.write_text("--- a\n+++ b\n", encoding="utf-8")
    case = {"case_id": "c1", "worktree": str(tmp_path), "target": "t.py::t", "signature": "E: m"}
    d.evaluate_under_gate(case, patch, tmp_path, bound)

    assert any(w.startswith("before ground-truth") for w in checks), checks
    assert any(w.startswith("during ground-truth") for w in checks), "no post-execution check on scoring"


def test_shadow_evaluation_gets_the_same_protection(tmp_path: Path, monkeypatch):
    """Arm A's shadow is the same function, so it inherits the same binding —
    asserted rather than assumed, since that was the previous defect."""
    bound, _ = bound_for(tmp_path)
    monkeypatch.setattr(d, "_rift", lambda *a, **k: fake_proc("--expect-signature"))
    drifted: list[str] = []

    real = d.assert_runtime

    def watch(root, frozen, env, when, cwd=None):
        drifted.append(when)
        return real(root, frozen, env, when, cwd)

    monkeypatch.setattr(d, "assert_runtime", watch)
    patch = tmp_path / "p.diff"
    patch.write_text("--- a\n+++ b\n", encoding="utf-8")
    case = {"case_id": "shadowed", "worktree": str(tmp_path), "target": "t.py::t", "signature": "E: m"}
    d.evaluate_under_gate(case, patch, tmp_path, bound)
    assert len([w for w in drifted if "shadowed" in w]) >= 2, drifted


def test_no_scoring_path_calls_the_raw_helper(tmp_path: Path):
    """Asserted on the driver's own source: the orchestration and scoring
    functions must reach the CLI only through `Bound`."""
    import ast

    tree = ast.parse(Path(d.__file__).read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name in {"cli_supports", "run"}:  # the documented unbound probe, and Bound.run itself
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_rift":
                offenders.append(node.name)
    assert not offenders, f"these bypass Bound.run: {sorted(set(offenders))}"


# ------------------------------------------------------------- model binding

MANIFEST = {"model": {"id": "claude-sonnet-4-6"}}


def test_a_matching_model_is_allowed():
    assert d.model_binding_failures(MANIFEST, {"RIFT_LLM_MODEL": "claude-sonnet-4-6"}) == []


def test_a_different_model_aborts_before_execution():
    failures = d.model_binding_failures(MANIFEST, {"RIFT_LLM_MODEL": "claude-sonnet-5"})
    assert failures and "is not the manifest model" in failures[0]
    assert "priced as" in failures[0], "the failure does not say what would have gone wrong"


@pytest.mark.parametrize("env", [{}, {"RIFT_LLM_MODEL": ""}, {"RIFT_LLM_MODEL": "   "}])
def test_a_missing_or_empty_model_aborts(env: dict):
    failures = d.model_binding_failures(MANIFEST, env)
    assert failures and "unset or empty" in failures[0]


def test_a_manifest_without_a_model_aborts():
    assert d.model_binding_failures({}, {"RIFT_LLM_MODEL": "claude-sonnet-4-6"})


def test_the_binding_is_checked_during_manifest_validation(tmp_path: Path, monkeypatch):
    """Pre-spend, with the whole manifest, not per-arm: pricing one model while
    executing another is not something a later check can undo."""
    monkeypatch.delenv("RIFT_LLM_MODEL", raising=False)
    manifest = {
        "arms": {a: {"description": "x", **({"seed": 1} if a == "B" else {})} for a in ("A", "B", "C")},
        "budget": {"scope": "s", "max_usd": 1.0},
        "model": {
            "id": "claude-sonnet-4-6",
            "max_probes": 1,
            "max_attempts": 1,
            "max_commands": 1,
            "max_output_tokens": 1,
        },
        "cases": [],
    }
    failures = d.validate_manifest(manifest, tmp_path, None)
    assert any("RIFT_LLM_MODEL" in f for f in failures)


def test_pricing_cannot_describe_one_model_while_another_executes(tmp_path: Path, monkeypatch):
    """The concrete consequence: the manifest's prices and the executing model
    must belong to the same model, or the run does not start."""
    monkeypatch.setenv("RIFT_LLM_MODEL", "claude-sonnet-5")
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "benchmark" / "bm06" / "manifest-preliminary.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["model"]["price_input_per_mtok"] == 3.0, "the frozen price is not Sonnet 4.6's"
    failures = d.model_binding_failures(manifest)
    assert failures, "a Sonnet 5 environment ran against Sonnet 4.6 pricing without complaint"


# --------------------------------------- provider identity: see DAR-027

# Four tests stood here and asserted that `pricing.model` in the spend ledger
# was the provider's reported identity. It is not — it is
# `os.environ.get("RIFT_LLM_MODEL")` written back out by the runtime, so those
# tests confirmed that our own configuration agreed with itself, and would have
# passed while a provider served a different model entirely.
#
# They are superseded rather than deleted: the property they were meant to hold
# is now held properly in `test_dar027_provider_identity.py`, which reads
# `model_reported` from `MODEL_RESPONSE_RECEIVED` in the arm's own task ledger
# and includes the case those four could never have caught — priced as 4.6,
# served by 5.


def test_the_priced_identity_is_not_treated_as_provider_evidence(tmp_path: Path):
    """What remains true here: the spend ledger's model is the configured one,
    and it is named for that now."""
    spend = tmp_path / ".rift" / "spend.jsonl"
    spend.parent.mkdir(parents=True)
    spend.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"scope": "s", "kind": "settled", "pricing": {"model": "claude-sonnet-4-6"}},
                {"scope": "other", "kind": "settled", "pricing": {"model": "claude-3-haiku"}},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert d.priced_models(tmp_path, "s") == ["claude-sonnet-4-6"]
    assert d.priced_models(tmp_path, "missing-scope") == []
    assert not hasattr(d, "reported_models"), "the misleading name is still callable"
    assert not hasattr(d, "reported_model_failure"), "the misleading helper is still callable"


def test_both_model_identities_are_stamped(monkeypatch, tmp_path: Path, capsys):
    """`manifest_model` and `configured_model` both travel in the result, and
    are equal for an authorized run. Recording only the manifest's is what let
    the two diverge unnoticed."""
    monkeypatch.setenv("RIFT_LLM_MODEL", "claude-sonnet-4-6")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {"cases": [], "arms": {}, "budget": {"scope": "s", "max_usd": 1.0}, "model": {"id": "claude-sonnet-4-6"}}
        ),
        encoding="utf-8",
    )
    runtime_root = runtime_tree(tmp_path)
    out = tmp_path / "r.json"

    monkeypatch.setattr(d, "validate_manifest", lambda *a, **k: [])
    monkeypatch.setattr(d, "assert_runtime", lambda *a, **k: None)
    monkeypatch.setattr(d, "_rift", lambda *a, **k: fake_proc(""))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "driver",
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--work",
            str(tmp_path),
            "--runtime-root",
            str(runtime_root),
            "--repos",
            str(tmp_path),
        ],
    )
    assert d.main() == 0

    stamped = json.loads(out.read_text(encoding="utf-8"))
    assert stamped["manifest_model"] == "claude-sonnet-4-6"
    assert stamped["configured_model"] == "claude-sonnet-4-6"
    assert stamped["manifest_model"] == stamped["configured_model"]
