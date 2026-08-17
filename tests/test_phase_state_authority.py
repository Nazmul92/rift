"""Phase-state hashes as transition authority, proven on the real gate path.

The corrections these tests cover were implemented without any test that would
notice their absence — the same defect class as D6. Each test here fails if the
corresponding fix is reverted, and the first one demonstrates that directly by
restoring the former behaviour inside the test and asserting the failure.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from riftagent.app import main
from riftagent.records import Verdict

pytestmark = pytest.mark.slow


# The patched module writes a cache file on import. That file is untracked and
# is owned by nobody: it is not in the construction manifest and not in
# `touched_paths`. Under the former whole-tree comparison it survives into the
# withdrawal measurement and makes `withdrawn_tree != baseline_tree`, so the
# gate rejects a sound counterfactual because of a cache.
DEBRIS_REPO = {
    "src/app/__init__.py": "",
    "src/app/calc.py": "def add(a, b):\n    return a - b\n",
    "tests/test_calc.py": "from app.calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n",
    "tests/test_preserved.py": (
        "from app.calc import add\n\n\ndef test_add_is_callable():\n    assert callable(add)\n"
    ),
}

DEBRIS_FIX = """--- a/src/app/calc.py
+++ b/src/app/calc.py
@@ -1,2 +1,8 @@
+import pathlib
+
+# Warm a cache on import. Ordinary runtime debris: untracked, and owned by no
+# patch path.
+pathlib.Path("calc.cache").write_text("warmed", encoding="utf-8")
+
 def add(a, b):
-    return a - b
+    return a + b
"""

# For the reapplication transition the debris must come from the *withdrawal*
# run, so it is still present when `reapplied_state` is taken. The target test
# itself writes it, which happens in every phase including the unpatched ones.
REAPPLY_DEBRIS_REPO = {
    "src/app/__init__.py": "",
    "src/app/calc.py": "def add(a, b):\n    return a - b\n",
    "tests/test_calc.py": (
        "import pathlib\n\nfrom app.calc import add\n\n\n"
        "def test_add():\n"
        "    pathlib.Path('run.marker').write_text('ran', encoding='utf-8')\n"
        "    assert add(2, 2) == 4\n"
    ),
    "tests/test_preserved.py": (
        "from app.calc import add\n\n\ndef test_add_is_callable():\n    assert callable(add)\n"
    ),
}

PLAIN_FIX = """--- a/src/app/calc.py
+++ b/src/app/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""

# Adds a module and rewires existing implementation code to use it. Touches no
# test and no frozen judge artifact.
ADDING_REPO = {
    "src/app/__init__.py": "",
    "src/app/calc.py": "def add(a, b):\n    return a - b\n",
    "tests/test_calc.py": "from app.calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n",
    "tests/test_preserved.py": (
        "from app.calc import add\n\n\ndef test_add_is_callable():\n    assert callable(add)\n"
    ),
}

ADDING_FIX = """--- /dev/null
+++ b/src/app/arith.py
@@ -0,0 +1,2 @@
+def plus(a, b):
+    return a + b
--- a/src/app/calc.py
+++ b/src/app/calc.py
@@ -1,2 +1,5 @@
+from app.arith import plus
+
+
 def add(a, b):
-    return a - b
+    return plus(a, b)
"""


class _Fake(http.server.BaseHTTPRequestHandler):
    change_diff: str = DEBRIS_FIX
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))).decode())
        type(self).seen.append(body)
        system = body["messages"][0]["content"].lower()
        content = (
            '{"handles": []}'
            if "propose measurements" in system
            else json.dumps({"diff": type(self).change_diff, "summary": "decides nothing"})
        )
        payload = json.dumps(
            {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "model": "fake",
                "usage": {"prompt_tokens": 80, "completion_tokens": 30},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a: Any) -> None:
        return


@pytest.fixture
def provider(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Fake.seen = []
    _Fake.change_diff = DEBRIS_FIX
    monkeypatch.setenv("RIFT_LLM_URL", f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
    monkeypatch.setenv("RIFT_LLM_KEY", "sk-SENTINEL-fake-for-tests")
    monkeypatch.setenv("RIFT_LLM_MODEL", "fake")
    try:
        yield _Fake
    finally:
        server.shutdown()
        server.server_close()


def build_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8", newline="\n")
    return root


def run_fix(repo: Path, capsys) -> tuple[int, dict]:
    code = main(
        [
            "--repo",
            str(repo),
            "--json",
            "fix",
            "tests/test_calc.py::test_add",
            "--allow-partial-sandbox",
            "--preserve",
            "tests/test_preserved.py::test_add_is_callable",
            "--max-usd",
            "1.00",
            "--max-probes",
            "2",
        ]
    )
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def ledger_of(repo: Path) -> list[dict]:
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    return [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def passed_phases(events: list[dict]) -> set[str]:
    return {e["payload"]["phase"] for e in events if e["kind"] == "gate_phase_finished" and e["payload"]["passed"]}


# --------------------------------------------------------------------------
# runtime debris must not reject a sound counterfactual
# --------------------------------------------------------------------------


def test_runtime_debris_does_not_cause_a_false_tree_mismatch(tmp_path: Path, capsys, provider):
    repo = build_repo(tmp_path / "debris", DEBRIS_REPO)
    code, receipt = run_fix(repo, capsys)
    events = ledger_of(repo)

    assert passed_phases(events) >= {"baseline", "candidate", "withdrawal", "reapply", "preservation"}, receipt.get(
        "reason"
    )
    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code == 0

    # The debris really was produced — otherwise this fixture proves nothing.
    resets = [e for e in events if e["kind"] == "episode_reset"]
    assert any(e["payload"]["cleared"] > 0 for e in resets), "no phase ever cleared runtime debris"


def test_whole_tree_hashes_remain_diagnostic_only(tmp_path: Path, capsys, provider):
    """They are still recorded — they simply no longer decide anything."""
    repo = build_repo(tmp_path / "diag", DEBRIS_REPO)
    run_fix(repo, capsys)
    events = ledger_of(repo)

    finished = [e for e in events if e["kind"] == "gate_phase_finished" and e["payload"].get("artifacts")]
    with_tree = [e for e in finished if "tree_hash" in e["payload"]["artifacts"]]
    with_state = [e for e in finished if "state_hash" in e["payload"]["artifacts"]]
    assert with_tree, "the diagnostic whole-tree hash is no longer recorded"
    assert with_state, "the authoritative phase-state hash is not recorded"


def test_withdrawal_decision_receives_the_recorded_phase_state_hashes(tmp_path: Path, capsys, provider, monkeypatch):
    """Argument provenance, not forced rejection.

    The previous version returned `PhaseDecision(False)` unconditionally, which
    proved only that a forced rejection rejects. What matters is *which values
    the production call site supplies*, so this wraps the real decision, records
    its actual arguments, and checks them against the ledger.
    """
    from riftagent import kernel

    real = kernel.decide_withdrawal_state
    seen: dict[str, str] = {}

    def spy(withdrawn_state: str, baseline_state: str):
        seen["withdrawn"] = withdrawn_state
        seen["baseline"] = baseline_state
        return real(withdrawn_state, baseline_state)

    monkeypatch.setattr(kernel, "decide_withdrawal_state", spy)
    repo = build_repo(tmp_path / "prov-withdrawal", DEBRIS_REPO)
    _code, receipt = run_fix(repo, capsys)
    events = ledger_of(repo)
    artifacts = {
        e["payload"]["phase"]: e["payload"]["artifacts"]
        for e in events
        if e["kind"] == "gate_phase_finished" and e["payload"].get("artifacts")
    }

    assert seen, "the withdrawal decision was never invoked"
    assert seen["withdrawn"] == artifacts["withdrawal"]["state_hash"]
    assert seen["baseline"] == artifacts["baseline"]["state_hash"]

    # Phase-state operands match; the whole-tree operands do not, because the
    # candidate run left a cache behind. That difference is precisely what the
    # former authority would have rejected on.
    assert seen["withdrawn"] == seen["baseline"]
    assert artifacts["withdrawal"]["tree_hash"] != artifacts["baseline"]["tree_hash"], (
        "the fixture produced no runtime debris, so it cannot distinguish the two authorities"
    )
    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value


def test_reapply_decision_receives_the_recorded_phase_state_hashes(tmp_path: Path, capsys, provider, monkeypatch):
    """Same provenance check for the reapplication transition."""
    from riftagent import kernel

    real = kernel.decide_reapply
    seen: dict[str, str] = {}

    def spy(candidate_state_hash, reapplied_state_hash, frozen_hash, reloaded_hash):
        seen.setdefault("candidate", candidate_state_hash)
        seen["reapplied"] = reapplied_state_hash
        return real(candidate_state_hash, reapplied_state_hash, frozen_hash, reloaded_hash)

    monkeypatch.setattr(kernel, "decide_reapply", spy)
    provider.change_diff = PLAIN_FIX
    repo = build_repo(tmp_path / "prov-reapply", REAPPLY_DEBRIS_REPO)
    _code, receipt = run_fix(repo, capsys)
    events = ledger_of(repo)
    artifacts = {
        e["payload"]["phase"]: e["payload"]["artifacts"]
        for e in events
        if e["kind"] == "gate_phase_finished" and e["payload"].get("artifacts")
    }

    assert seen, "the reapplication decision was never invoked"
    assert seen["candidate"] == artifacts["candidate"]["state_hash"]
    assert seen["reapplied"] == artifacts["reapply"]["state_hash"]
    assert seen["candidate"] == seen["reapplied"]
    assert artifacts["reapply"]["tree_hash"] != artifacts["candidate"]["tree_hash"], (
        "no debris distinguished the two authorities in this fixture"
    )
    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value


# --------------------------------------------------------------------------
# a patch that adds a file
# --------------------------------------------------------------------------


def test_a_file_adding_repair_passes_end_to_end(tmp_path: Path, capsys, provider):
    """D1's consuming path. The added module must survive every reset, and
    disappear only through exact patch reversal."""
    provider.change_diff = ADDING_FIX
    repo = build_repo(tmp_path / "adding", ADDING_REPO)
    code, receipt = run_fix(repo, capsys)
    events = ledger_of(repo)

    assert passed_phases(events) >= {"baseline", "candidate", "withdrawal", "reapply", "preservation"}, receipt.get(
        "reason"
    )
    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code == 0

    changeset = next(e for e in events if e["kind"] == "changeset_registered")
    touched = set(changeset["payload"]["changeset"]["touched_paths"])
    assert touched == {"src/app/arith.py", "src/app/calc.py"}, touched

    # The added path is owned by the patch, so every reset preserved it.
    for reset in (e for e in events if e["kind"] == "episode_reset"):
        if reset["payload"]["patch_owned"]:
            assert "src/app/arith.py" in reset["payload"]["patch_owned"]

    # And it was genuinely *absent* after withdrawal, not merely unexamined.
    # Both state hashes now cover manifest ∪ touched_paths, so `arith.py` is
    # hashed as absent in each; equality is therefore evidence of removal.
    artifacts = {
        e["payload"]["phase"]: e["payload"]["artifacts"]
        for e in events
        if e["kind"] == "gate_phase_finished" and e["payload"].get("artifacts")
    }
    assert artifacts["withdrawal"]["state_hash"] == artifacts["baseline"]["state_hash"], (
        "withdrawal state does not match baseline, so the patch-added file may remain"
    )
    assert artifacts["reapply"]["state_hash"] == artifacts["candidate"]["state_hash"]
    assert artifacts["withdrawal"]["state_hash"] != artifacts["candidate"]["state_hash"], (
        "baseline and candidate states are identical, so the added file is outside the hashed "
        "universe and this assertion proves nothing"
    )


# --------------------------------------------------------------------------
# cleanup failure, on the real gate path
# --------------------------------------------------------------------------


def test_a_gate_phase_cleanup_failure_is_governed(tmp_path: Path, capsys, provider, monkeypatch):
    """A reset that cannot complete must stop the gate, not be swallowed.

    The previous version monkeypatched `app.reset_episode` itself, so it proved
    only that the *caller* handles a raised `SandboxError`. It never entered the
    real function, which meant restoring `except OSError: pass` inside it left
    the test green — the removal check could not see the code it was named for.

    This drives the production `reset_episode` and fails the filesystem
    operation underneath it: `Path.unlink` raises for the known debris file and
    delegates for everything else.
    """
    import riftagent.app as app

    repo = build_repo(tmp_path / "cleanup", DEBRIS_REPO)
    real_unlink = Path.unlink
    attempted: list[str] = []
    entered: list[str] = []

    real_reset = app.reset_episode

    def watched_reset(wt, patch_owned):
        entered.append("yes")
        return real_reset(wt, patch_owned)

    def refusing_unlink(self: Path, *a, **k):
        # `calc.cache` is written on import by the patched module — ordinary
        # runtime debris the reset must remove.
        if self.name == "calc.cache":
            attempted.append(self.name)
            raise OSError("device busy")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(app, "reset_episode", watched_reset)
    monkeypatch.setattr(Path, "unlink", refusing_unlink)
    code, receipt = run_fix(repo, capsys)
    events = ledger_of(repo)

    # the real function ran, and the intended removal was actually attempted
    assert entered, "the production reset_episode was never called"
    assert attempted, "the debris removal was never attempted, so no OSError could arise"

    # the OSError became a SandboxError and the gate stopped
    blocked = [e for e in events if e["kind"] == "infrastructure_blocked"]
    assert blocked, "cleanup failure did not stop the gate"
    reason = blocked[-1]["payload"]["reason"]
    assert "could not remove" in reason and "calc.cache" in reason, reason
    failing_phase = blocked[-1]["payload"].get("phase")
    assert failing_phase, "the failing phase was not recorded"

    # no successful reset for the failing phase; earlier ones may remain
    for reset in (e for e in events if e["kind"] == "episode_reset"):
        assert reset["payload"]["phase"] != failing_phase

    # no later gate phase completed
    order = [(i, e) for i, e in enumerate(events) if e["kind"] in ("infrastructure_blocked", "gate_phase_finished")]
    block_at = next(i for i, e in order if e["kind"] == "infrastructure_blocked")
    later = [e["payload"]["phase"] for i, e in order if i > block_at and e["kind"] == "gate_phase_finished"]
    assert not later, f"gate phases ran after the integrity stop: {later}"

    # a scoped receipt, a non-verified verdict, and no model request afterwards
    assert any(e["kind"] == "receipt_emitted" for e in events)
    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code != 0
    kinds = [e["kind"] for e in events]
    if "model_request_started" in kinds:
        assert kinds.index("infrastructure_blocked") > max(
            i for i, k in enumerate(kinds) if k == "model_request_started"
        )
