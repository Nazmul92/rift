"""One schema-repair request for a malformed reply — and none for a truncated one.

The frozen design requires "allow one schema-repair request; abstain explicitly
if validation still fails". `llm.ModelResponseInvalid` has always documented it.
No caller implemented it: every operation appended `MODEL_RESPONSE_INVALID` and
returned `None`.

The distinction these tests exist to protect is between two failures that look
alike in the ledger and are not alike at all:

* a **completed** reply that will not parse — the model said something, and
  asking it to re-serialise that something is a bounded, meaningful request;
* a **truncated** reply, `finish_reason == "length"` with nothing visible — the
  allowance was consumed before an answer existed. The aborted preliminary run
  showed this at 4,000 output tokens and again at 8,000. Retrying under the same
  allowance buys the identical failure twice, at twice the price.

Every test uses a fake adapter. No provider is configured and no request leaves
the process.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import build_repo

import riftagent.app as app
import riftagent.llm as llm

FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/calc.py": "def total():\n    return 4\n",
    "tests/test_calc.py": "from pkg.calc import total\n\n\ndef test_total():\n    assert total() == 5\n",
}
TARGET = "tests/test_calc.py::test_total"

DIFF = "--- a/src/pkg/calc.py\n+++ b/src/pkg/calc.py\n@@ -1,2 +1,2 @@\n def total():\n-    return 4\n+    return 5\n"
GOOD = json.dumps({"diff": DIFF, "summary": "return 5"})


def reply(text: str, finish: str = "stop", out_tokens: int = 120) -> llm.ModelReply:
    return llm.ModelReply(
        text=text,
        usage=llm.ModelUsage(input_tokens=100, output_tokens=out_tokens),
        model_reported="fake-model",
        finish_reason=finish,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path / "repair", FILES)


def events_of(repo: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted((repo / ".rift").rglob("ledger.jsonl")):
        out.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return out


def kinds_of(repo: Path) -> list[str]:
    return [e["kind"] for e in events_of(repo)]


def change_repairs(repo: Path) -> list[dict]:
    """Repairs belonging to `propose_change`. Since DAR-021 the diagnosis
    operations have the same entitlement, so counting the bare event kind would
    silently include theirs."""
    return [
        e
        for e in events_of(repo)
        if e["kind"] == "model_repair_requested" and e["payload"]["operation"] == "propose_change"
    ]


def change_events(repo: Path, kind: str) -> list[dict]:
    """Only the events belonging to `propose_change` and its repair.

    Diagnosis makes its own optional requests first; they degrade to
    deterministic handles and are not what any of this is about.
    """
    return [e for e in events_of(repo) if e["kind"] == kind and "propose_change" in e["payload"].get("operation", "")]


@pytest.fixture
def fake_provider(monkeypatch):
    """Queue `propose_change` replies; record the requests actually issued.

    `fix` diagnoses before it proposes, and diagnosis may spend one bounded
    request on extra handles. Those are answered with an unusable object so the
    kernel falls back to deterministic discovery — they are not drawn from the
    queue and are not counted, or the counts below would be measuring the wrong
    operation.
    """
    calls: list[list[dict[str, str]]] = []
    queue: list[llm.ModelReply] = []

    def post_chat(config, messages, max_output_tokens, timeout_s=120.0, temperature=None):
        if messages[0]["content"] != llm._CHANGE_SYSTEM:
            # Truncated, so the diagnosis operations degrade deterministically
            # and — since DAR-021 gave them the repair too — do not spend one.
            return reply("", finish="length", out_tokens=4000)
        calls.append(messages)
        if not queue:
            raise AssertionError(f"an unexpected propose_change request was made (call {len(calls)})")
        return queue.pop(0)

    monkeypatch.setattr(llm, "post_chat", post_chat)
    monkeypatch.setattr(
        app.llm.ProviderConfig, "from_env", staticmethod(lambda env=None: llm.ProviderConfig("https://x/y", "k", "m"))
    )
    return calls, queue


def fix(run_cli, repo: Path, max_usd: str = "1.0"):
    return run_cli("--repo", str(repo), "--json", "fix", TARGET, "--allow-partial-sandbox", "--max-usd", max_usd)


# --------------------------------------------------------------- 1, 2, 3 and 4


def test_a_valid_first_response_makes_no_repair_request(repo, run_cli, fake_provider):
    """The path that costs nothing extra must stay free."""
    calls, queue = fake_provider
    queue.append(reply(GOOD))
    fix(run_cli, repo)
    assert len(calls) == 1, f"{len(calls)} requests were made for a reply that parsed"
    assert not change_repairs(repo)
    assert "changeset_registered" in kinds_of(repo)


def test_a_malformed_completed_response_triggers_exactly_one_repair(repo, run_cli, fake_provider):
    calls, queue = fake_provider
    queue.append(reply("Here is my fix: it changes the return value."))  # completed, no JSON
    queue.append(reply(GOOD))
    fix(run_cli, repo)

    kinds = kinds_of(repo)
    assert len(change_repairs(repo)) == 1, kinds
    assert len(calls) == 2, f"expected one request plus one repair, got {len(calls)}"

    # The repair asks for re-serialisation only — not a second attempt at the fix.
    repair_text = calls[1][1]["content"]
    assert "Re-send the SAME proposal" in repair_text
    assert "Do not change the fix" in repair_text
    assert "Here is my fix" in repair_text, "the repair did not carry the previous reply back"

    # And the repaired proposal continues into the ordinary ChangeSet flow.
    assert "changeset_registered" in kinds, kinds


def test_a_second_malformed_response_abstains(repo, run_cli, fake_provider):
    calls, queue = fake_provider
    queue.append(reply("still prose"))
    queue.append(reply("prose again"))
    fix(run_cli, repo)

    kinds = kinds_of(repo)
    assert len(change_repairs(repo)) == 1, "more than one repair was attempted"
    assert len(calls) == 2, f"expected exactly two requests, got {len(calls)}"
    assert "changeset_registered" not in kinds
    exhausted = [e for e in change_events(repo, "model_response_invalid") if e["payload"].get("repair_exhausted")]
    assert exhausted, "abstention after the repair was not recorded"


# ------------------------------------------------------------------------- 5


def test_output_exhaustion_does_not_trigger_a_repair(repo, run_cli, fake_provider):
    """The failure the aborted run hit, at 4,000 tokens and again at 8,000.

    Nothing visible was produced, so there is nothing to re-serialise and a
    repair would pay a second time for the same outcome.
    """
    calls, queue = fake_provider
    queue.append(reply("", finish="length", out_tokens=4000))
    fix(run_cli, repo)

    assert not change_repairs(repo), "a truncated reply was sent for repair"
    assert len(calls) == 1, f"a second request was made after truncation: {len(calls)}"
    invalid = change_events(repo, "model_response_invalid")
    assert invalid and invalid[0]["payload"]["output_exhausted"] is True, invalid
    assert invalid[0]["payload"]["finish_reason"] == "length"
    assert invalid[0]["payload"]["response_chars"] == 0


def test_a_truncated_reply_with_visible_text_is_still_exhaustion(repo, run_cli, fake_provider):
    """`finish_reason == "length"` means the answer was cut off, whether or not
    some of it escaped first. Half a proposal is not a proposal to repair."""
    calls, queue = fake_provider
    queue.append(reply('{"diff": "--- a/src/pkg/ca', finish="length", out_tokens=4000))
    fix(run_cli, repo)
    assert not change_repairs(repo)
    assert len(calls) == 1


def test_the_predicate_names_the_distinction():
    assert llm.output_exhausted(reply("", finish="length", out_tokens=4000)) is True
    assert llm.output_exhausted(reply("   ", finish="stop")) is True, "empty visible text is exhaustion"
    assert llm.output_exhausted(reply("prose", finish="stop")) is False
    assert llm.output_exhausted(reply(GOOD, finish="stop")) is False


# ------------------------------------------------------------------------- 6


def test_the_repair_request_is_reserved_and_settled(repo, run_cli, fake_provider):
    """A repair is a paid request. Reserving it before it is sent is what keeps
    the ceiling honest; spend outside the ledger is spend nobody authorized."""
    calls, queue = fake_provider
    queue.append(reply("prose"))
    queue.append(reply(GOOD))
    fix(run_cli, repo)

    reserved = [e for e in change_events(repo, "spend_reserved") if "repair" in e["payload"]["operation"]]
    settled = [e for e in change_events(repo, "spend_settled") if "repair" in e["payload"]["operation"]]
    assert len(reserved) == 1, [e["payload"]["operation"] for e in change_events(repo, "spend_reserved")]
    assert len(settled) == 1
    assert reserved[0]["payload"]["request_id"] == settled[0]["payload"]["request_id"]
    # And it is a distinct request from the one it repairs, so neither is
    # settled against the other's reservation.
    first = [e for e in change_events(repo, "spend_reserved") if "repair" not in e["payload"]["operation"]]
    assert first and first[0]["payload"]["request_id"] != reserved[0]["payload"]["request_id"]


def test_a_refused_repair_reservation_abstains_without_requesting(repo, run_cli, fake_provider):
    """If the remaining ceiling cannot cover the repair it is not made. Before
    the request is the only point at which refusing costs nothing."""
    calls, queue = fake_provider
    queue.append(reply("prose"))
    fix(run_cli, repo, max_usd="0.0000001")
    assert len(calls) <= 1
    assert "changeset_registered" not in kinds_of(repo)


# ------------------------------------------------------------------------- 7


def test_the_repair_path_sends_nothing_provider_specific(monkeypatch):
    """Adapter neutrality is an M1 acceptance property (M1-S03).

    Asserted on the wire request rather than on the source text, so a comment
    naming a vendor does not pass or fail it. The repair is ordinary chat
    messages: same body keys, same two headers, no `thinking`, no
    `reasoning_effort`, no vendor header.
    """
    seen: dict = {}

    class FakeResponse:
        def read(self, _n=None):
            return json.dumps(
                {
                    "choices": [{"message": {"content": GOOD}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                    "model": "fake-model",
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout=None):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["headers"] = {k.lower() for k in request.headers}
        return FakeResponse()

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)

    config = llm.ProviderConfig("https://example.invalid/v1/chat/completions", "k", "m")
    messages = llm.repair_prompt([{"role": "system", "content": "S"}, {"role": "user", "content": "U"}], "bad", "why")
    llm.post_chat(config, messages, max_output_tokens=4000)

    assert set(seen["body"]) == {"model", "messages", "max_tokens"}
    assert seen["headers"] == {"content-type", "authorization"}
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
    assert all(set(m) == {"role", "content"} for m in seen["body"]["messages"])


# --------------------------------------------------- every operation, not one

# `CLAUDE.md` grants the repair to each model operation. Until DAR-021 only
# `propose_change` had it: a diagnosis whose hypotheses were well reasoned and
# badly serialised was discarded in silence, so arms B and C would have scored
# worse for an adapter defect while `propose_change` got its promised retry.
#
# These drive the shared helper directly rather than through `fix`. The two
# diagnosis operations are conditional — `propose_handles` fires only on the
# representation-inadequate signal — so a CLI-level test would pass by never
# reaching them, which is the failure mode this whole project keeps finding.

HANDLES = json.dumps({"handles": [{"kind": "env", "arg": "TZ"}]})
ROLES = ["rA", "rB", "rT"]
HYPOTHESES = json.dumps(
    {
        "hypotheses": [
            {
                "hypothesis_id": f"h{n}",
                "roles": ROLES,
                "target_role": "rT",
                "latents": [{"name": "runs", "type": "counter", "max": 16, "inc_on": {"event": "run", "role": None}}],
                "condition": {"op": "ge", "var": "runs", "value": n + 1},
            }
            for n in range(3)
        ]
    }
)

OPERATIONS = {
    "propose_change": (lambda raw: llm.validate_change(raw), GOOD),
    "propose_handles": (lambda raw: llm.validate_handles(raw, []), HANDLES),
}


def harness(tmp_path: Path, monkeypatch, replies: list[llm.ModelReply]):
    """A real Flow and SpendLedger over a queue of replies."""
    from riftagent.records import Ledger, Pricing, SpendLedger

    calls: list[list[dict[str, str]]] = []

    def post_chat(config, messages, max_output_tokens, timeout_s=120.0, temperature=None):
        calls.append(messages)
        assert replies, f"unexpected request {len(calls)}"
        return replies.pop(0)

    monkeypatch.setattr(llm, "post_chat", post_chat)
    ledger = Ledger(tmp_path / "ledger.jsonl", "t1")
    flow = app.Flow(ledger, app.LiveRenderer(quiet=True), None, False)
    spend = SpendLedger(
        tmp_path / "spend.jsonl",
        scope="s",
        limit_usd=10.0,
        pricing=Pricing(input_per_mtok=1.0, output_per_mtok=1.0, provider="p", model="m"),
    )
    return flow, spend, calls


def kinds_in(flow) -> list[str]:
    """The accepted path appends nothing at all, so an absent ledger is a
    result and not an error."""
    if not flow.ledger.path.exists():
        return []
    return [json.loads(line)["kind"] for line in flow.ledger.path.read_text(encoding="utf-8").splitlines()]


def drive(tmp_path, monkeypatch, operation, replies):
    validate, _ = OPERATIONS[operation]
    flow, spend, calls = harness(tmp_path, monkeypatch, list(replies[1:]))
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    result = app._accept_or_repair(
        flow,
        spend,
        operation,
        "t1",
        llm.ProviderConfig("https://x/y", "k", "m"),
        messages,
        replies[0],
        4000,
        1,
        validate,
    )
    return result, flow, calls


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_valid_first_response_makes_no_repair_for_any_operation(tmp_path, monkeypatch, operation):
    _, good = OPERATIONS[operation]
    result, flow, calls = drive(tmp_path, monkeypatch, operation, [reply(good)])
    assert result is not None
    assert calls == [], f"{operation}: a request was made for a reply that parsed"
    assert "model_repair_requested" not in kinds_in(flow)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_malformed_response_gets_exactly_one_repair_for_any_operation(tmp_path, monkeypatch, operation):
    _, good = OPERATIONS[operation]
    result, flow, calls = drive(tmp_path, monkeypatch, operation, [reply("prose, no object"), reply(good)])
    assert result is not None, f"{operation}: the repaired response was not accepted"
    assert len(calls) == 1, f"{operation}: expected exactly one repair request"
    assert "Re-send the SAME proposal" in calls[0][1]["content"]
    assert kinds_in(flow).count("model_repair_requested") == 1


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_second_malformed_response_abstains_for_any_operation(tmp_path, monkeypatch, operation):
    result, flow, calls = drive(tmp_path, monkeypatch, operation, [reply("prose one"), reply("prose two")])
    assert result is None, f"{operation}: abstention did not happen"
    assert len(calls) == 1, f"{operation}: more than one repair was attempted"
    events = [json.loads(x) for x in flow.ledger.path.read_text(encoding="utf-8").splitlines()]
    assert [e for e in events if e["payload"].get("repair_exhausted")], operation


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_output_exhaustion_never_buys_a_repair_for_any_operation(tmp_path, monkeypatch, operation):
    result, flow, calls = drive(tmp_path, monkeypatch, operation, [reply("", finish="length", out_tokens=4000)])
    assert result is None
    assert calls == [], f"{operation}: a truncated reply was sent for repair"
    assert "model_repair_requested" not in kinds_in(flow)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_every_repair_is_reserved_and_settled_under_its_own_operation(tmp_path, monkeypatch, operation):
    _, good = OPERATIONS[operation]
    _, flow, _ = drive(tmp_path, monkeypatch, operation, [reply("prose"), reply(good)])
    events = [json.loads(x) for x in flow.ledger.path.read_text(encoding="utf-8").splitlines()]
    reserved = [e for e in events if e["kind"] == "spend_reserved"]
    settled = [e for e in events if e["kind"] == "spend_settled"]
    assert len(reserved) == 1 and len(settled) == 1, operation
    assert reserved[0]["payload"]["operation"] == f"{operation}_repair"
    assert reserved[0]["payload"]["request_id"] == settled[0]["payload"]["request_id"]


def test_the_hypotheses_operation_has_the_entitlement_too(tmp_path, monkeypatch):
    """Not parametrised because its validator needs the role list; the point is
    the same — arms B and C depend on this operation."""
    flow, spend, calls = harness(tmp_path, monkeypatch, [reply(HYPOTHESES)])
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    result = app._accept_or_repair(
        flow,
        spend,
        "propose_hypotheses",
        "t1",
        llm.ProviderConfig("https://x/y", "k", "m"),
        messages,
        reply("prose, no object"),
        4000,
        1,
        lambda raw: llm.validate_hypotheses(raw, ROLES),
    )
    assert result is not None and len(result) == 3
    assert len(calls) == 1
    assert kinds_in(flow).count("model_repair_requested") == 1
