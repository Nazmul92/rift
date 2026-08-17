"""`rift fix` end to end, and the spend reservation that gates every request.

Two properties dominate this file.

**`fix` adds no second acceptance path.** It produces a candidate and hands it
to `run_gate` — the same function `verify` calls. A verb that verified its own
output would be the model-satisfies-its-own-judge failure wearing a different
name, so the tests check that the gate ran, that the model's summary changed
nothing, and that a patch touching the frozen judge never executes.

**Worst case is reserved before the request, not estimated after it.** Input is
not free, an unanswered request is not free, and absent usage retains the full
reservation. The provider here is a local `http.server`; no credential and no
network are involved.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from riftagent.app import main, select_context, token_ceiling
from riftagent.records import (
    BudgetRefused,
    ModelUsage,
    Pricing,
    SpendLedger,
    Verdict,
    reserve_cost,
    spend_for_task,
)

pytestmark = pytest.mark.slow

PRICING = Pricing(input_per_mtok=1.0, output_per_mtok=5.0, provider="test", model="test-model")


# --------------------------------------------------------------------------
# the reservation arithmetic
# --------------------------------------------------------------------------


def test_input_tokens_are_not_free():
    """The defect this rule exists to prevent: reserving only the output."""
    both = reserve_cost(PRICING, input_token_ceiling=1_000_000, max_output_tokens=1_000_000)
    assert both == pytest.approx(6.0)
    assert reserve_cost(PRICING, 1_000_000, 0) == pytest.approx(1.0)


def ledger(tmp_path, limit=2.00, scope="s1"):
    return SpendLedger(tmp_path / ".rift" / "spend.jsonl", scope=scope, limit_usd=limit, pricing=PRICING)


def test_a_request_over_the_remaining_authorization_is_refused(tmp_path: Path):
    led = ledger(tmp_path, limit=0.10)
    with pytest.raises(BudgetRefused):
        led.reserve("r1", "t1", 1, 1_000_000, 1_000_000)
    assert led.remaining_usd() == pytest.approx(0.10), "a refused request must reserve nothing"
    assert any(e["kind"] == "refused" for e in led.events()), "the refusal was not recorded"


def test_reported_usage_is_charged_and_the_rest_released(tmp_path: Path):
    led = ledger(tmp_path)
    _, reserved = led.reserve("r1", "t1", 1, 100_000, 4_000)
    assert reserved == pytest.approx(0.12)
    record = led.settle("r1", "t1", 1, ModelUsage(input_tokens=10_000, output_tokens=500))

    assert record["charged_usd"] == pytest.approx(0.0125)
    assert record["released_usd"] == pytest.approx(0.1075)
    assert record["usage_source"] == "provider_reported"
    assert led.remaining_usd() == pytest.approx(2.00 - 0.0125)


@pytest.mark.parametrize(
    "usage", [ModelUsage(), ModelUsage(input_tokens=5, output_tokens=None), ModelUsage(output_tokens=5)]
)
def test_absent_usage_retains_the_full_reservation(tmp_path: Path, usage: ModelUsage):
    """Never substitute an estimate. An under-estimate is how a cap is exceeded
    while appearing to hold."""
    led = ledger(tmp_path)
    _, reserved = led.reserve("r1", "t1", 1, 100_000, 4_000)
    record = led.settle("r1", "t1", 1, usage)

    assert record["charged_usd"] == pytest.approx(reserved)
    assert record["released_usd"] == 0.0
    assert record["usage_source"] == "unknown_full_reservation_retained"


def test_the_cumulative_cap_holds_across_many_requests(tmp_path: Path):
    led = ledger(tmp_path, limit=0.05)
    sent = 0
    for i in range(100):
        try:
            led.reserve(f"r{i}", "t1", 1, 10_000, 1_000)
        except BudgetRefused:
            break
        led.settle(f"r{i}", "t1", 1, ModelUsage(input_tokens=10_000, output_tokens=1_000))
        sent += 1
    assert led.committed_usd() <= 0.05 + 1e-9
    assert sent > 0


def test_an_unsettled_reservation_still_counts_against_the_limit(tmp_path: Path):
    """A crash after reservation must leave the reservation consumed. Charging
    only on settlement would record an unanswered request as free, and a crash
    loop would spend without limit while the ledger showed zero."""
    led = ledger(tmp_path, limit=0.25)
    _, reserved = led.reserve("r1", "t1", 1, 100_000, 4_000)
    assert led.committed_usd() == pytest.approx(reserved)
    assert led.remaining_usd() == pytest.approx(0.25 - reserved)


def test_settlement_is_idempotent(tmp_path: Path):
    """A resume must not charge the same request twice."""
    led = ledger(tmp_path)
    led.reserve("r1", "t1", 1, 100_000, 4_000)
    first = led.settle("r1", "t1", 1, ModelUsage(input_tokens=1_000, output_tokens=100))
    again = led.settle("r1", "t1", 1, ModelUsage(input_tokens=999_999, output_tokens=999_999))
    assert again["event_id"] == first["event_id"]
    assert led.committed_usd() == pytest.approx(first["charged_usd"])


def test_scope_isolates_authorizations(tmp_path: Path):
    path = tmp_path / ".rift" / "spend.jsonl"
    a = SpendLedger(path, scope="run-A", limit_usd=0.05, pricing=PRICING)
    b = SpendLedger(path, scope="run-B", limit_usd=0.05, pricing=PRICING)
    a.reserve("r1", "t1", 1, 10_000, 1_000)
    a.settle("r1", "t1", 1, ModelUsage(input_tokens=10_000, output_tokens=1_000))
    assert b.committed_usd() == 0.0, "one scope consumed another's authorization"
    assert a.committed_usd() > 0.0


def test_a_torn_final_line_is_dropped_not_fatal(tmp_path: Path):
    led = ledger(tmp_path)
    led.reserve("r1", "t1", 1, 1_000, 100)
    with open(led.path, "a", encoding="utf-8") as fh:
        fh.write('{"kind": "reserved", "scope": "s1", "req')
    assert len(led.events()) == 1


def test_task_spend_is_derived_by_joining_references(tmp_path: Path):
    led = ledger(tmp_path)
    led.reserve("r1", "t1", 1, 100_000, 4_000)
    led.settle("r1", "t1", 1, ModelUsage(input_tokens=1_000, output_tokens=100))
    led.reserve("r2", "t2", 1, 100_000, 4_000)
    led.settle("r2", "t2", 1, ModelUsage(input_tokens=2_000, output_tokens=200))

    one = spend_for_task(led.path, "s1", "t1")
    assert one["requests"] == 1
    assert one["charged_usd"] == pytest.approx(PRICING.cost(1_000, 100))
    assert one["unknown_usage"] == 0


def test_the_ceiling_does_not_depend_on_a_provider_tokenizer():
    """A pessimistic character bound, deliberately. It must never under-count a
    prompt, whatever the provider's tokenizer would say."""
    messages = [{"role": "user", "content": "x" * 30_000}]
    ceiling = token_ceiling(messages)
    assert ceiling >= 30_000 / 4, "the bound must exceed any plausible token count"
    assert ceiling > len(messages[0]["content"]) / 4


# --------------------------------------------------------------------------
# bounded context selection
# --------------------------------------------------------------------------

BROKEN = {
    "src/app/__init__.py": "",
    "src/app/calc.py": "def add(a, b):\n    return a - b\n",
    "tests/test_calc.py": "from app.calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n",
    "tests/test_other.py": "def test_other():\n    assert True\n",
}


def build_repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8", newline="\n")
    return root


def test_context_selection_is_by_citation_and_is_bounded(tmp_path: Path):
    repo = build_repo(tmp_path / "ctx", BROKEN)
    failure = 'File "src/app/calc.py", line 2, in add\n    return a - b\nAssertionError'
    chosen, manifest = select_context(repo, failure, "tests/test_calc.py::test_add", ())

    names = [rel for rel, _ in chosen]
    assert "src/app/calc.py" in names, names
    assert "tests/test_other.py" not in names, "an uncited file was included"
    assert manifest["chars"] <= manifest["cap_chars"]
    assert len(names) <= manifest["cap_files"]
    assert "embedding" in manifest["selection"] and "no embedding" in manifest["selection"]


def test_protected_and_rift_paths_never_enter_context(tmp_path: Path):
    """The model cannot be asked to edit the judge, so it is not shown it."""
    files = dict(BROKEN)
    files["conftest.py"] = "# judge\n"
    files[".rift/tasks/x/ledger.jsonl"] = '{"secret": "sk-SENTINEL"}\n'
    repo = build_repo(tmp_path / "prot", files)
    failure = (
        'File "conftest.py", line 1, in <module>\n'
        'File ".rift/tasks/x/ledger.jsonl", line 1\n'
        'File "src/app/calc.py", line 2, in add\n'
    )
    chosen, manifest = select_context(repo, failure, "tests/test_calc.py::test_add", ("conftest.py",))

    body = "\n".join(text for _, text in chosen)
    assert "sk-SENTINEL" not in body
    assert all(not rel.startswith(".rift") for rel, _ in chosen)
    assert "conftest.py" not in [rel for rel, _ in chosen]
    assert any("protected or excluded" in s for s in manifest["skipped"]), manifest["skipped"]


def test_a_file_outside_the_repository_is_refused(tmp_path: Path):
    repo = build_repo(tmp_path / "esc", BROKEN)
    outside = tmp_path / "secrets.py"
    outside.write_text("KEY = 'sk-SENTINEL'\n", encoding="utf-8")
    failure = 'File "../secrets.py", line 1\nFile "src/app/calc.py", line 2, in add\n'
    chosen, _ = select_context(repo, failure, "tests/test_calc.py::test_add", ())
    assert "sk-SENTINEL" not in "\n".join(t for _, t in chosen)


# --------------------------------------------------------------------------
# `fix` against a fake provider
# --------------------------------------------------------------------------

GOOD_DIFF = """--- a/src/app/calc.py
+++ b/src/app/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""

JUDGE_DIFF = """--- a/tests/test_calc.py
+++ b/tests/test_calc.py
@@ -1,5 +1,5 @@
 from app.calc import add


 def test_add():
-    assert add(2, 2) == 4
+    assert True
"""

INERT_DIFF = """--- a/src/app/calc.py
+++ b/src/app/calc.py
@@ -1,2 +1,3 @@
+# a comment that changes nothing
 def add(a, b):
     return a - b
"""


class _Provider(http.server.BaseHTTPRequestHandler):
    reply: dict[str, Any] = {}
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("content-length", "0"))).decode("utf-8")
        body = json.loads(raw)
        type(self).seen.append(body)
        # `fix` is diagnosis-first, so it issues a bounded `propose_handles`
        # request before `propose_change`. The fake answers whichever it was
        # asked, rather than pretending the loop makes only one call.
        system = body["messages"][0]["content"]
        if "propose measurements" in system.lower():
            answer: dict = {
                "choices": [{"message": {"content": '{"handles": []}'}, "finish_reason": "stop"}],
                "model": "test-model",
                "usage": {"prompt_tokens": 40, "completion_tokens": 5},
            }
        else:
            answer = type(self).reply
        payload = json.dumps(answer).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a: Any) -> None:
        return


def completion(diff: str, usage: dict | None = None) -> dict:
    body: dict[str, Any] = {
        "choices": [
            {
                "message": {"content": json.dumps({"diff": diff, "summary": "a summary decides nothing"})},
                "finish_reason": "stop",
            }
        ],
        "model": "test-model",
    }
    if usage is not None:
        body["usage"] = usage
    return body


@pytest.fixture
def provider(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Provider.seen = []
    monkeypatch.setenv("RIFT_LLM_URL", f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
    monkeypatch.setenv("RIFT_LLM_KEY", "sk-SENTINEL-fake")
    monkeypatch.setenv("RIFT_LLM_MODEL", "test-model")
    try:
        yield _Provider
    finally:
        server.shutdown()
        server.server_close()


def run_fix(repo: Path, capsys, extra: list[str] | None = None) -> tuple[int, dict]:
    code = main(
        [
            "--repo",
            str(repo),
            "--json",
            "fix",
            "tests/test_calc.py::test_add",
            "--allow-partial-sandbox",
            "--max-probes",
            "2",
            *(extra or []),
        ]
    )
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def spend_events(repo: Path) -> list[dict]:
    """The authoritative spend ledger. Task ledgers hold only references."""
    path = repo / ".rift" / "spend.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def ledger_of(repo: Path) -> list[dict]:
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    return [json.loads(line) for line in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def test_a_correct_patch_passes_the_same_gate_verify_uses(tmp_path: Path, capsys, provider):
    provider.reply = completion(GOOD_DIFF, {"prompt_tokens": 900, "completion_tokens": 60})
    repo = build_repo(tmp_path / "good", BROKEN)
    code, receipt = run_fix(repo, capsys)

    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value, receipt["reason"]
    assert code == 0
    phases = {
        e["payload"]["phase"] for e in ledger_of(repo) if e["kind"] == "gate_phase_finished" and e["payload"]["passed"]
    }
    assert {"baseline", "candidate", "withdrawal", "reapply"} <= phases, phases


def test_a_patch_touching_the_frozen_judge_never_runs(tmp_path: Path, capsys, provider):
    """Structural rejection before execution. The judge is not negotiable."""
    provider.reply = completion(JUDGE_DIFF, {"prompt_tokens": 900, "completion_tokens": 60})
    repo = build_repo(tmp_path / "judge", BROKEN)
    code, receipt = run_fix(repo, capsys)

    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code != 0
    evs = ledger_of(repo)
    assert any(e["kind"] == "changeset_rejected" for e in evs)
    assert not any(e["kind"] == "changeset_registered" for e in evs), "a judge-touching patch was registered"


def test_an_inert_patch_is_rejected_by_the_counterfactual(tmp_path: Path, capsys, provider):
    """It applies cleanly and changes nothing. Only the gate can tell."""
    provider.reply = completion(INERT_DIFF, {"prompt_tokens": 900, "completion_tokens": 60})
    repo = build_repo(tmp_path / "inert", BROKEN)
    code, receipt = run_fix(repo, capsys)

    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code != 0
    assert receipt["rejected_phase"] == "candidate", receipt


def test_the_model_summary_is_never_evidence(tmp_path: Path, capsys, provider):
    provider.reply = completion(INERT_DIFF, {"prompt_tokens": 10, "completion_tokens": 10})
    repo = build_repo(tmp_path / "summary", BROKEN)
    _, receipt = run_fix(repo, capsys)
    assert "a summary decides nothing" not in json.dumps(receipt).replace("summary_not_evidence", "")


def test_no_credential_reaches_the_ledger_or_the_repository(tmp_path: Path, capsys, provider):
    provider.reply = completion(GOOD_DIFF, {"prompt_tokens": 10, "completion_tokens": 10})
    repo = build_repo(tmp_path / "cred", BROKEN)
    run_fix(repo, capsys)
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    for artifact in td.rglob("*"):
        if artifact.is_file():
            assert "sk-SENTINEL" not in artifact.read_text(encoding="utf-8", errors="replace"), artifact


# --------------------------------------------------------------------------
# spend, recorded
# --------------------------------------------------------------------------


def test_reservation_and_settlement_are_both_durable(tmp_path: Path, capsys, provider):
    provider.reply = completion(GOOD_DIFF, {"prompt_tokens": 900, "completion_tokens": 60})
    repo = build_repo(tmp_path / "spend", BROKEN)
    _, receipt = run_fix(repo, capsys, extra=["--max-usd", "1.00", "--price-input", "1.0", "--price-output", "5.0"])

    refs = [e for e in ledger_of(repo) if e["kind"] in ("spend_reserved", "spend_settled")]
    assert len(refs) >= 2
    for ref in refs:
        assert "spend_event_id" in ref["payload"], "the task ledger must carry a reference"
        for copied in ("charged_usd", "reserved_usd", "released_usd"):
            assert copied not in ref["payload"], f"{copied} was copied into the task ledger"

    events = spend_events(repo)
    r = [e for e in events if e["kind"] == "reserved"][-1]
    t = [e for e in events if e["kind"] == "settled"][-1]
    assert r["input_token_ceiling"] > 0, "input was treated as free"
    assert r["reserved_usd"] > 0
    assert t["usage_source"] == "provider_reported"
    assert t["charged_usd"] == pytest.approx((900 * 1.0 + 60 * 5.0) / 1e6)
    assert t["released_usd"] == pytest.approx(r["reserved_usd"] - t["charged_usd"])
    for key in ("prompt", "messages", "content", "diff"):
        assert key not in r and key not in t, f"{key} leaked into a spend event"

    # The receipt derives its figure by joining, never by copying, and it sums
    # every settled request rather than reporting the last one.
    settled = [e for e in events if e["kind"] == "settled"]
    all_charged = sum(e["charged_usd"] for e in settled)
    assert receipt["spend"]["charged_usd"] == pytest.approx(all_charged)
    assert receipt["spend"]["requests"] == len(settled)
    # This fixture locates its cause, so `propose_handles` is never triggered
    # and `propose_change` is the only charged request. The multi-request half
    # of the sum — that it is not merely the last request's figure — is asserted
    # where two requests genuinely occur, in
    # `test_acceptance_gaps.py::test_f06_all_contradicted_triggers_one_bounded_handles_request`.
    assert len(settled) == 1, [e.get("request_id") for e in settled]
    assert receipt["spend"]["authoritative"] == ".rift/spend.jsonl"


def test_missing_provider_usage_retains_the_whole_reservation(tmp_path: Path, capsys, provider):
    provider.reply = completion(GOOD_DIFF, usage=None)
    repo = build_repo(tmp_path / "nousage", BROKEN)
    _, receipt = run_fix(repo, capsys, extra=["--max-usd", "1.00"])

    settled = [e for e in spend_events(repo) if e["kind"] == "settled"][-1]
    assert settled["usage_source"] == "unknown_full_reservation_retained"
    assert settled["released_usd"] == 0.0
    assert settled["charged_usd"] == pytest.approx(settled["reserved_usd"])
    assert receipt["spend"]["unknown_usage"] == 1


def test_an_unaffordable_request_is_refused_before_it_is_sent(tmp_path: Path, capsys, provider):
    provider.reply = completion(GOOD_DIFF, {"prompt_tokens": 10, "completion_tokens": 10})
    _Provider.seen = []
    repo = build_repo(tmp_path / "broke", BROKEN)
    code, receipt = run_fix(repo, capsys, extra=["--max-usd", "0.0000001"])

    assert not _Provider.seen, "a request was sent despite an insufficient authorization"
    assert any(e["kind"] == "spend_refused" for e in ledger_of(repo))
    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code != 0


def test_no_model_configured_is_an_explicit_abstention(tmp_path: Path, capsys, monkeypatch):
    for var in ("RIFT_LLM_URL", "RIFT_LLM_KEY", "RIFT_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    repo = build_repo(tmp_path / "nomodel", BROKEN)
    code, receipt = run_fix(repo, capsys)

    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert code != 0
    assert any(e["kind"] == "model_unavailable" for e in ledger_of(repo))
    assert not any(e["kind"] == "changeset_registered" for e in ledger_of(repo))


def test_a_rejected_attempt_stays_in_the_ledger_and_is_charged(tmp_path: Path, capsys, provider):
    provider.reply = completion(JUDGE_DIFF, {"prompt_tokens": 500, "completion_tokens": 40})
    repo = build_repo(tmp_path / "charged", BROKEN)
    run_fix(repo, capsys, extra=["--max-usd", "1.00", "--max-attempts", "2"])

    rejected = [e for e in ledger_of(repo) if e["kind"] == "changeset_rejected"]
    change_settlements = [
        e for e in spend_events(repo) if e["kind"] == "settled" and e["attempt"] >= 1 and e["charged_usd"] > 0
    ]
    assert len(rejected) >= 1
    assert len(change_settlements) >= len(rejected), "a rejected attempt was not charged"


# --------------------------------------------------------------------------
# a bad patch is a bad patch, not a broken machine
# --------------------------------------------------------------------------

NONAPPLYING_DIFF = """--- a/src/app/calc.py
+++ b/src/app/calc.py
@@ -1,2 +1,2 @@
 def subtract(a, b):
-    return a * b
+    return a / b
"""


def test_a_patch_that_does_not_apply_is_rejected_not_infrastructure(tmp_path: Path, capsys, provider):
    """Found by live calibration: a model diff whose context does not match was
    recorded as `infrastructure_blocked`. That blames the repository for the
    proposal's defect, and in a benchmark it drops the attempt out of the
    denominator instead of counting it as the rejection it is."""
    provider.reply = completion(NONAPPLYING_DIFF, {"prompt_tokens": 200, "completion_tokens": 50})
    repo = build_repo(tmp_path / "noapply", BROKEN)
    code, receipt = run_fix(repo, capsys)

    assert receipt["verdict"] != Verdict.INFRASTRUCTURE_BLOCKED.value, receipt["reason"]
    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert receipt["rejected_phase"] == "candidate", receipt
    assert code != 0

    evs = ledger_of(repo)
    rejected = [e for e in evs if e["kind"] == "changeset_rejected"]
    assert rejected, "no rejection was recorded"
    assert "does not apply" in rejected[-1]["payload"]["reason"]
    assert not any(e["kind"] == "infrastructure_blocked" for e in evs)


def test_the_attempt_is_still_charged_when_the_patch_will_not_apply(tmp_path: Path, capsys, provider):
    """Reclassifying the outcome must not make the request free."""
    provider.reply = completion(NONAPPLYING_DIFF, {"prompt_tokens": 200, "completion_tokens": 50})
    repo = build_repo(tmp_path / "noapply2", BROKEN)
    run_fix(repo, capsys, extra=["--max-usd", "1.00"])
    settled = [e for e in spend_events(repo) if e["kind"] == "settled"]
    assert settled and settled[-1]["charged_usd"] > 0


# --------------------------------------------------------------------------
# diff canonicalisation
# --------------------------------------------------------------------------


def test_a_diff_missing_its_final_newline_still_applies(tmp_path: Path, capsys, provider):
    """Found by live calibration: four of five model diffs were rejected as
    `corrupt patch` for one missing byte. `git apply` requires the last line to
    end with a newline, and the model's JSON string did not."""
    provider.reply = completion(GOOD_DIFF.rstrip("\n"), {"prompt_tokens": 200, "completion_tokens": 50})
    repo = build_repo(tmp_path / "nonl", BROKEN)
    code, receipt = run_fix(repo, capsys)

    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value, receipt["reason"]
    assert code == 0


def test_canonicalisation_happens_before_the_patch_is_stored(tmp_path: Path, capsys, provider):
    """The recorded bytes must be the bytes that are applied, or the reapply
    phase would compare a normalised patch against an unnormalised record."""
    provider.reply = completion(GOOD_DIFF.rstrip("\n"), {"prompt_tokens": 200, "completion_tokens": 50})
    repo = build_repo(tmp_path / "stored", BROKEN)
    run_fix(repo, capsys)

    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    stored = (td / "change-set.diff").read_bytes()
    assert stored.endswith(b"\n"), "the stored patch was not canonicalised"

    registered = [e for e in ledger_of(repo) if e["kind"] == "changeset_registered"][-1]
    from riftagent.records import content_hash

    assert registered["payload"]["changeset"]["patch_hash"] == content_hash(stored)


def test_crlf_line_endings_are_normalised(tmp_path: Path, capsys, provider):
    provider.reply = completion(GOOD_DIFF.replace("\n", "\r\n"), {"prompt_tokens": 200, "completion_tokens": 50})
    repo = build_repo(tmp_path / "crlf", BROKEN)
    code, receipt = run_fix(repo, capsys)
    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value, receipt["reason"]
    assert code == 0


def test_canonicalisation_does_not_rescue_a_genuinely_bad_patch(tmp_path: Path, capsys, provider):
    """The normalisation must be exactly one terminator, not leniency."""
    provider.reply = completion(NONAPPLYING_DIFF, {"prompt_tokens": 200, "completion_tokens": 50})
    repo = build_repo(tmp_path / "stillbad", BROKEN)
    _, receipt = run_fix(repo, capsys)
    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert receipt["rejected_phase"] == "candidate"


# --------------------------------------------------------------------------
# the model must actually be shown the source it is asked to change
# --------------------------------------------------------------------------


PYTEST_TRACEBACK = """
tests/test_calc.py:5: in test_add
    assert add(2, 2) == 4
src/app/calc.py:2: in add
    return a - b
E   AssertionError: assert 0 == 4
"""


def test_pytest_style_frames_are_recognised(tmp_path: Path):
    """Found by live calibration: the pattern matched only stdlib `File "..."`
    frames, but pytest prints `path.py:12: in fn`. Nothing matched, so no source
    was ever selected and the model was asked to patch code it had not seen —
    it duly invented both the path and the original line."""
    repo = build_repo(tmp_path / "frames", BROKEN)
    chosen, _ = select_context(repo, PYTEST_TRACEBACK, "tests/test_calc.py::test_add", ())
    names = [rel for rel, _ in chosen]
    assert "src/app/calc.py" in names, names


def test_the_implementation_file_reaches_the_prompt(tmp_path: Path, capsys, provider):
    """End to end: the request body must contain the source under repair."""
    provider.reply = completion(GOOD_DIFF, {"prompt_tokens": 200, "completion_tokens": 50})
    _Provider.seen = []
    repo = build_repo(tmp_path / "shown", BROKEN)
    run_fix(repo, capsys)

    assert _Provider.seen, "no request was sent"
    body = json.dumps(_Provider.seen[-1])  # the propose_change request
    assert "src/app/calc.py" in body, "the implementation path was never shown to the model"
    assert "return a - b" in body, "the failing source line was never shown to the model"


def test_the_failure_output_is_durable(tmp_path: Path, capsys, provider):
    """What the model is shown must be what was recorded, not a value carried
    in a variable across the run."""
    provider.reply = completion(GOOD_DIFF, {"prompt_tokens": 200, "completion_tokens": 50})
    repo = build_repo(tmp_path / "durable", BROKEN)
    run_fix(repo, capsys)

    excerpts = [
        e["payload"]["failure_excerpt"]
        for e in ledger_of(repo)
        if e["kind"] == "check_result" and e["payload"].get("failure_excerpt")
    ]
    assert excerpts, "no failure output was made durable"
    assert "calc.py" in excerpts[0]
    assert len(excerpts[0]) <= 6000, "the excerpt is unbounded"


# --------------------------------------------------------------------------
# diff strip levels
# --------------------------------------------------------------------------

# What `diff -u` and many generators emit: bare repository-relative paths with
# no `a/`/`b/` prefixes. `git apply` defaults to -p1 and strips a component.
NO_PREFIX_DIFF = """--- src/app/calc.py
+++ src/app/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


def test_a_prefixless_diff_is_accepted(tmp_path: Path, capsys, provider):
    """Found by live calibration: a semantically correct fix was rejected
    because it used bare paths instead of the `a/`/`b/` convention."""
    provider.reply = completion(NO_PREFIX_DIFF, {"prompt_tokens": 200, "completion_tokens": 50})
    repo = build_repo(tmp_path / "p0", BROKEN)
    code, receipt = run_fix(repo, capsys)

    assert receipt["verdict"] == Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value, receipt["reason"]
    assert code == 0


def test_the_strip_level_fallback_does_not_accept_a_bad_patch(tmp_path: Path, capsys, provider):
    """Trying a second strip level must not become leniency: `--check` still
    has to pass at whichever level is used."""
    provider.reply = completion(NONAPPLYING_DIFF, {"prompt_tokens": 200, "completion_tokens": 50})
    repo = build_repo(tmp_path / "p0bad", BROKEN)
    _, receipt = run_fix(repo, capsys)
    assert receipt["verdict"] != Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value
    assert receipt["rejected_phase"] == "candidate"


def test_withdrawal_uses_the_same_strip_level_as_the_apply(tmp_path: Path, capsys, provider):
    """The counterfactual is only valid if the reverse removes exactly what the
    forward added."""
    provider.reply = completion(NO_PREFIX_DIFF, {"prompt_tokens": 200, "completion_tokens": 50})
    repo = build_repo(tmp_path / "p0rev", BROKEN)
    _, receipt = run_fix(repo, capsys)
    phases = {
        e["payload"]["phase"] for e in ledger_of(repo) if e["kind"] == "gate_phase_finished" and e["payload"]["passed"]
    }
    assert {"withdrawal", "reapply"} <= phases, phases
