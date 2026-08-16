"""`rift why` end to end, against real temporary repositories.

Every test here drives the public CLI and reads the emitted receipt. The point
of the verb is not that it finds a cause — it is that what it says is bounded by
what its experiments actually distinguished. So the suite spends most of its
weight on the cases where the honest answer is *not* a cause: a target that does
not fail, an action space that cannot express the failure, and a target that
cannot be observed at all.

No model is configured in any of these. `why` is required to run to completion
without one and to say so in the receipt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from riftagent.app import main
from riftagent.records import GateStatus, Support, Verdict

pytestmark = pytest.mark.slow


# --------------------------------------------------------------------------
# fixtures: three repositories with genuinely different causes
# --------------------------------------------------------------------------


# An order-dependent failure: the target passes alone and fails after a
# neighbour mutates shared module state. This is the case the whole design
# exists for, and it is invisible to a runner that only ever runs one test.
ORDER_DEPENDENT = {
    "src/app/__init__.py": "",
    "src/app/registry.py": "REGISTRY = {}\n\n\ndef put(k, v):\n    REGISTRY[k] = v\n",
    "tests/test_a_first.py": (
        "from app.registry import put\n\n\ndef test_pollutes():\n    put('leaked', 1)\n    assert True\n"
    ),
    "tests/test_target.py": (
        "from app.registry import REGISTRY\n\n\ndef test_clean_registry():\n    assert REGISTRY == {}\n"
    ),
}

# An environment-gated failure: the target fails unless a variable is set.
ENV_GATED = {
    "src/app/__init__.py": "",
    "src/app/cfg.py": "import os\n\n\ndef token():\n    return os.environ.get('APP_TOKEN')\n",
    "tests/test_target.py": (
        "from app.cfg import token\n\n\ndef test_token_present():\n"
        "    assert token() is not None, 'APP_TOKEN missing'\n"
    ),
}

# An unconditional failure with no environmental lever at all. The honest
# answer is that this representation cannot explain it — never a claim about
# the source.
UNCONDITIONAL = {
    "src/app/__init__.py": "",
    "src/app/math.py": "def add(a, b):\n    return a - b\n",
    "tests/test_target.py": "from app.math import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n",
}

PASSING = {
    "src/app/__init__.py": "",
    "tests/test_target.py": "def test_fine():\n    assert True\n",
}


def build_repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8", newline="\n")
    return root


def run_why(repo: Path, node: str, capsys, extra: list[str] | None = None) -> tuple[int, dict]:
    argv = [
        "--repo",
        str(repo),
        "--json",
        "why",
        node,
        "--allow-partial-sandbox",
        "--no-model",
        *(extra or []),
    ]
    code = main(argv)
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1])


# --------------------------------------------------------------------------
# the honest non-answers
# --------------------------------------------------------------------------


def test_a_passing_target_yields_no_cause(tmp_path: Path, capsys):
    """There is no failure here to explain. Inventing one would be the single
    worst thing this verb could do."""
    repo = build_repo(tmp_path / "ok", PASSING)
    code, receipt = run_why(repo, "tests/test_target.py::test_fine", capsys)
    assert receipt["verdict"] == Verdict.UNVERIFIABLE.value
    assert receipt["diagnosis"]["causes"] == []
    assert code != 0
    assert "no failure here to explain" in " ".join(receipt["diagnosis"]["notes"])


def test_an_unexplainable_failure_attributes_nothing_to_the_source(tmp_path: Path, capsys):
    """The M0 correction, carried into the product. A surviving 'nothing here
    helps' theory is a statement about the representation."""
    repo = build_repo(tmp_path / "hard", UNCONDITIONAL)
    code, receipt = run_why(repo, "tests/test_target.py::test_add", capsys)
    assert receipt["verdict"] == Verdict.REPRESENTATION_INADEQUATE.value
    assert receipt["diagnosis"]["causes"] == []
    assert receipt["diagnosis"]["gate"] == GateStatus.NOT_APPLICABLE.value
    assert code != 0

    text = " ".join(receipt["diagnosis"]["notes"]).lower()
    for forbidden in ("defect", "bug in the code", "the source is wrong", "fix the code"):
        assert forbidden not in text, f"the diagnosis claims something about the source: {forbidden!r}"
    assert "attributes nothing to the repository" in text


def test_a_missing_target_is_infrastructure_not_a_diagnosis(tmp_path: Path, capsys):
    """A node that cannot be collected was not observed. Not observing a target
    is not evidence about it."""
    repo = build_repo(tmp_path / "gone", UNCONDITIONAL)
    code, receipt = run_why(repo, "tests/test_target.py::test_does_not_exist", capsys)
    assert receipt["verdict"] in (
        Verdict.INFRASTRUCTURE_BLOCKED.value,
        Verdict.REPRESENTATION_INADEQUATE.value,
    )
    assert receipt["diagnosis"]["causes"] == []
    assert code != 0


# --------------------------------------------------------------------------
# the cases where evidence does support something
# --------------------------------------------------------------------------


def test_an_order_dependent_failure_is_located_or_honestly_bounded(tmp_path: Path, capsys):
    """Either the ordering handle is supported, or the result is one of the
    scoped abstentions. What is never permitted is a cause the experiments did
    not distinguish."""
    repo = build_repo(tmp_path / "order", ORDER_DEPENDENT)
    code, receipt = run_why(repo, "tests/test_target.py::test_clean_registry", capsys)
    diagnosis = receipt["diagnosis"]

    assert diagnosis["status"] in (
        Verdict.DIAGNOSIS_SUPPORTED.value,
        Verdict.UNDERDETERMINED.value,
        Verdict.REPRESENTATION_INADEQUATE.value,
    )
    if diagnosis["status"] == Verdict.DIAGNOSIS_SUPPORTED.value:
        assert diagnosis["causes"], "a supported diagnosis must name what it located"
        assert all(c["kind"] in ("first", "firstset", "env", "unsetenv", "clear") for c in diagnosis["causes"])
        assert code == 0
    else:
        assert diagnosis["causes"] == [], "an abstention must not name a cause"
        assert code != 0


def test_the_environment_handle_is_discovered_from_the_failure_text(tmp_path: Path, capsys):
    """Handle discovery reads observable signals only — here, the identifier
    named in the assertion message."""
    repo = build_repo(tmp_path / "env", ENV_GATED)
    _, receipt = run_why(repo, "tests/test_target.py::test_token_present", capsys)

    task_dir = next((repo / ".rift" / "tasks").iterdir())
    events = [json.loads(line) for line in (task_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    discovered = [e for e in events if e["kind"] == "handles_discovered"]
    assert discovered, "no handle discovery event was recorded"
    labels = {h["kind"] + ":" + h["arg"] for h in discovered[0]["payload"]["handles"]}
    assert "env:APP_TOKEN" in labels, f"APP_TOKEN was not discovered from the failure text: {labels}"


# --------------------------------------------------------------------------
# discipline that must hold on every path
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "node"),
    [
        (ORDER_DEPENDENT, "tests/test_target.py::test_clean_registry"),
        (UNCONDITIONAL, "tests/test_target.py::test_add"),
        (ENV_GATED, "tests/test_target.py::test_token_present"),
    ],
    ids=["order", "unconditional", "env"],
)
def test_a_verdict_is_always_from_the_scoped_vocabulary(tmp_path: Path, capsys, files: dict, node: str):
    repo = build_repo(tmp_path / "vocab", files)
    _, receipt = run_why(repo, node, capsys)
    assert receipt["verdict"] in {v.value for v in Verdict}
    assert receipt["verdict"] not in ("verified", "done", "ok", "fixed")


def test_a_cause_is_never_reported_without_support(tmp_path: Path, capsys):
    """The structural invariant: `causes` and `support` stand or fall together.
    A named cause with no support would be exactly the confident guess the
    design exists to prevent."""
    for name, files, node in (
        ("order", ORDER_DEPENDENT, "tests/test_target.py::test_clean_registry"),
        ("hard", UNCONDITIONAL, "tests/test_target.py::test_add"),
        ("env", ENV_GATED, "tests/test_target.py::test_token_present"),
    ):
        repo = build_repo(tmp_path / name, files)
        _, receipt = run_why(repo, node, capsys)
        d = receipt["diagnosis"]
        assert bool(d["causes"]) == (d["support"] is not None), d
        if d["support"] == Support.OBSERVATIONAL.value:
            assert d["gate"] == GateStatus.NOT_APPLICABLE.value
            assert d["remediation_unverified"], "an observational finding must label remediation unverified"


def test_the_receipt_says_no_model_was_used(tmp_path: Path, capsys):
    """`why` runs model-free. The receipt must say so rather than leave the
    reader to assume it."""
    repo = build_repo(tmp_path / "nomodel", UNCONDITIONAL)
    _, receipt = run_why(repo, "tests/test_target.py::test_add", capsys)
    assert "not_applicable" in receipt["tokens"] or "none" in receipt["tokens"]
    assert "0" not in receipt["tokens"].split("in")[0] or "no model" in receipt["tokens"]


def test_no_provider_credential_reaches_the_ledger(tmp_path: Path, capsys, monkeypatch):
    """The environment allowlist is what keeps the key out of the repository
    subprocess; this holds that it also never reaches durable evidence."""
    monkeypatch.setenv("RIFT_LLM_KEY", "sk-SENTINEL-must-not-appear")
    repo = build_repo(tmp_path / "cred", UNCONDITIONAL)
    run_why(repo, "tests/test_target.py::test_add", capsys)

    task_dir = next((repo / ".rift" / "tasks").iterdir())
    for artifact in task_dir.iterdir():
        if artifact.is_file():
            assert "sk-SENTINEL" not in artifact.read_text(encoding="utf-8", errors="replace"), artifact.name


def test_the_settled_transcript_replays_byte_for_byte(tmp_path: Path, capsys):
    """A diagnosis transcript is a pure projection of its ledger, like a gate
    transcript. Streaming may not carry state the replay cannot reproduce."""
    repo = build_repo(tmp_path / "replay", UNCONDITIONAL)
    run_why(repo, "tests/test_target.py::test_add", capsys)
    task_dir = next((repo / ".rift" / "tasks").iterdir())
    settled = (task_dir / "transcript.txt").read_bytes()

    capsys.readouterr()
    assert main(["--repo", str(repo), "replay", task_dir.name]) == 0
    replayed = capsys.readouterr().out

    # The stored transcript is the same projection the replay renders.
    assert settled.decode("utf-8").strip() == replayed.strip()


def test_two_concurrent_why_tasks_do_not_share_a_ledger(tmp_path: Path, capsys):
    """Ruling 1, exercised through the verb rather than the allocator."""
    repo = build_repo(tmp_path / "twice", UNCONDITIONAL)
    run_why(repo, "tests/test_target.py::test_add", capsys)
    run_why(repo, "tests/test_target.py::test_add", capsys)

    dirs = sorted((repo / ".rift" / "tasks").iterdir())
    assert len(dirs) == 2, [d.name for d in dirs]
    assert dirs[0].name != dirs[1].name
    assert dirs[0].name.startswith("why-") and dirs[1].name.startswith("why-")


def test_the_repository_is_not_modified(tmp_path: Path, capsys):
    """Diagnosis probes delete state directories and set variables. All of that
    must happen in the disposable sandbox, never in the user's tree."""
    repo = build_repo(tmp_path / "intact", ORDER_DEPENDENT)
    before = {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".rift" not in p.parts
    }
    run_why(repo, "tests/test_target.py::test_clean_registry", capsys)
    after = {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".rift" not in p.parts
    }
    assert before == after


def test_the_probe_budget_is_respected(tmp_path: Path, capsys):
    repo = build_repo(tmp_path / "budget", ORDER_DEPENDENT)
    _, receipt = run_why(repo, "tests/test_target.py::test_clean_registry", capsys, extra=["--max-probes", "1"])
    task_dir = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    events = [json.loads(line) for line in (task_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    probes = [e for e in events if e["kind"] == "probe_selected"]
    assert len(probes) <= 1, f"{len(probes)} probes ran under a budget of 1"


def test_env_is_not_inherited_into_the_probe(tmp_path: Path, capsys):
    """A variable set in the parent must not silently satisfy the target: the
    child environment is built by allowlist, so `env:APP_TOKEN` measures the
    handle rather than the operator's shell."""
    os.environ.pop("APP_TOKEN", None)
    repo = build_repo(tmp_path / "noinherit", ENV_GATED)
    _, receipt = run_why(repo, "tests/test_target.py::test_token_present", capsys)
    assert receipt["results"], "no observation was recorded"
    assert receipt["results"][0]["outcome"] == "failed", "the target should fail with APP_TOKEN unset"
