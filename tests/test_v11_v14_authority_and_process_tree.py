"""V-11 … V-14: isolation authority and whole-tree process control.

The authority tests force the partial tier rather than waiting for a platform
that happens to lack bubblewrap, so they assert the same rule everywhere.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from riftagent.records import EventKind, IsolationLevel, Verdict, read_events
from riftagent.sandbox import IS_WINDOWS, build_env, probe_isolation, run_argv
from tests.conftest import SIMPLE_TARGET
from tests.test_gate_end_to_end import events_of, latest_task, receipt_of

# ---------------------------------------------------------------- V-11, V-12


def test_v11_yes_cannot_authorise_partial_isolation(simple_repo, correct_diff, write_diff, run_cli, partial_probe):
    code, _ = run_cli("--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--yes")
    receipt = receipt_of(simple_repo)
    assert code == 3
    assert receipt["verdict"] == Verdict.INFRASTRUCTURE_BLOCKED.value
    assert "--allow-partial-sandbox" in receipt["reason"]
    kinds = [e.kind for e in events_of(simple_repo)]
    assert EventKind.COMMAND_STARTED not in kinds, "repository code ran without isolation authority"
    assert receipt["authorities"]["partial_sandbox"] == "none"


def test_v12_partial_execution_requires_the_explicit_flag(
    simple_repo, correct_diff, write_diff, run_cli, partial_probe
):
    code, _ = run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    receipt = receipt_of(simple_repo)
    assert code == 0
    assert receipt["sandbox"] == IsolationLevel.PARTIAL.value
    assert receipt["authorities"]["partial_sandbox"] == "--allow-partial-sandbox"
    assert receipt["authorities"]["spec_approval"] == "not_applicable"


def test_v12_authorities_are_recorded_separately(simple_repo, correct_diff, write_diff, run_cli, partial_probe):
    run_cli(
        "--repo",
        str(simple_repo),
        "verify",
        str(write_diff(correct_diff)),
        SIMPLE_TARGET,
        "--yes",
        "--allow-partial-sandbox",
    )
    receipt = receipt_of(simple_repo)
    assert receipt["authorities"] == {"spec_approval": "not_applicable", "partial_sandbox": "--allow-partial-sandbox"}


def test_receipt_states_the_isolation_level_actually_used(simple_repo, correct_diff, write_diff, run_cli):
    run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    receipt = receipt_of(simple_repo)
    probe = probe_isolation()
    assert receipt["sandbox"] == probe.level.value
    assert receipt["sandbox_detail"] == probe.detail
    if probe.level is IsolationLevel.PARTIAL:
        assert any("partial sandbox" in n for n in receipt["remaining_uncertainty"])


def test_require_full_sandbox_blocks_rather_than_downgrading(
    simple_repo, correct_diff, write_diff, run_cli, partial_probe
):
    code, _ = run_cli(
        "--repo",
        str(simple_repo),
        "verify",
        str(write_diff(correct_diff)),
        SIMPLE_TARGET,
        "--require-full-sandbox",
        "--allow-partial-sandbox",
    )
    assert code == 3
    assert receipt_of(simple_repo)["verdict"] == Verdict.INFRASTRUCTURE_BLOCKED.value


def test_blocked_isolation_still_produces_a_replayable_receipt(
    simple_repo, correct_diff, write_diff, run_cli, partial_probe
):
    run_cli("--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET)
    td = latest_task(simple_repo)
    assert (td / "receipt.json").is_file() and (td / "transcript.txt").is_file()
    events, _ = read_events(td / "ledger.jsonl")
    assert events[-1].kind is EventKind.RECEIPT_EMITTED


# ---------------------------------------------------------------- V-13, V-14

GRANDCHILD = """
import subprocess, sys, time, os
marker = sys.argv[1]
code = (
    "import time, sys\\n"
    "open(sys.argv[1] + '.started', 'w').write('1')\\n"
    "time.sleep(12)\\n"
    "open(sys.argv[1] + '.survived', 'w').write('1')\\n"
)
subprocess.Popen([sys.executable, "-c", code, marker])
time.sleep(60)
"""


@pytest.mark.slow
def test_v13_timeout_terminates_the_whole_process_tree(tmp_path: Path):
    """A grandchild that outlives the timeout is the failure this guards.

    The child spawns a grandchild and both sleep past the deadline. If only the
    direct child were killed, `.survived` would appear.
    """
    probe = probe_isolation()
    marker = tmp_path / "gc"
    script = tmp_path / "spawner.py"
    script.write_text(GRANDCHILD, encoding="utf-8")
    env = build_env(tmp_path, tmp_path)
    started = time.time()
    result = run_argv([sys.executable, str(script), str(marker)], tmp_path, env, 4.0, probe)
    assert result.timed_out is True
    assert time.time() - started < 30

    deadline = time.time() + 20
    while time.time() < deadline and not marker.with_suffix(".started").exists():
        time.sleep(0.2)
    assert marker.with_suffix(".started").exists(), "the grandchild never started; the test proves nothing"
    time.sleep(14)
    assert not marker.with_suffix(".survived").exists(), "a grandchild outlived the timeout"


@pytest.mark.skipif(not IS_WINDOWS, reason="native Windows Job Object path")
def test_v14_windows_uses_a_tested_whole_tree_mechanism():
    probe = probe_isolation()
    assert probe.tree_kill is True, "Windows without reliable tree termination must block, not proceed"
    assert "Job Object" in probe.detail


@pytest.mark.skipif(IS_WINDOWS, reason="posix process-group path")
def test_v13_posix_uses_a_process_group():
    probe = probe_isolation()
    assert probe.tree_kill is True


def test_execution_is_refused_when_the_tree_cannot_be_controlled(
    simple_repo, correct_diff, write_diff, run_cli, monkeypatch
):
    """The V-14 fallback: if descendants cannot be terminated, block."""
    from riftagent.sandbox import IsolationProbe

    monkeypatch.setattr(
        "riftagent.app.probe_isolation",
        lambda: IsolationProbe(IsolationLevel.PARTIAL, "no tree control", tree_kill=False),
    )
    code, _ = run_cli(
        "--repo", str(simple_repo), "verify", str(write_diff(correct_diff)), SIMPLE_TARGET, "--allow-partial-sandbox"
    )
    assert code == 3
    receipt = receipt_of(simple_repo)
    assert receipt["verdict"] == Verdict.INFRASTRUCTURE_BLOCKED.value
    assert "process tree" in receipt["reason"]
    assert EventKind.COMMAND_STARTED not in [e.kind for e in events_of(simple_repo)]


def test_child_environment_is_built_by_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFT_LLM_KEY", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = build_env(tmp_path, tmp_path)
    assert "secret" not in json.dumps(env)
    assert "PATH" in env
    assert env["HOME"] == str(tmp_path)
