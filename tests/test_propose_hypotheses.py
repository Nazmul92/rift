"""`propose_hypotheses` at the governed ambiguity point.

The operation had a validator and no call site. These tests drive the real
`why` and `fix` command flows against a fake provider and assert on the ledger,
because a call site alone is not enforcement: what matters is *where* the
request is made, that it is reserved and settled through the spend ledger, that
the returned theories are scored by the kernel against the evidence already
recorded, and that every failure mode leaves the deterministic diagnosis exactly
as it was.

Each test states which production behaviour it would stop passing without.
"""

from __future__ import annotations

import ast
import http.server
import json
import re
import threading
from pathlib import Path
from typing import Any

import pytest

from riftagent.records import Verdict
from tests.conftest import ORDER_TARGET, SIMPLE_TARGET, build_repo

# An unconditional wrong constant. No handle in the action space changes the
# outcome, so the enumerated grammar stalls with a cause it cannot locate --
# which is exactly the ambiguity point the model request exists for.
UNRESOLVED_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/calc.py": "def total():\n    return 10\n",
    "src/pkg/util.py": "def double(x):\n    return x * 2\n",
    "tests/test_calc.py": "from pkg.calc import total\n\n\ndef test_total():\n    assert total() == 11\n",
    "tests/test_other.py": "from pkg.util import double\n\n\ndef test_double():\n    assert double(3) == 6\n",
}


def _hypotheses_for(roles: list[str]) -> dict[str, Any]:
    """Three theories in the closed IR, built from the roles the prompt carried.

    `m_always_passes` is unconditional PASS and is therefore contradicted by the
    baseline failure alone, whatever else the run observed. That is what makes
    the "the kernel scored it" assertion deterministic rather than incidental.
    """
    first = roles[0]
    return {
        "hypotheses": [
            {
                "hypothesis_id": "m_counts_runs",
                "roles": roles,
                "target_role": "rT",
                "latents": [{"name": "runs", "type": "counter", "max": 16, "inc_on": {"event": "run", "role": None}}],
                "condition": {"op": "ge", "var": "runs", "value": 2},
            },
            {
                "hypothesis_id": "m_needs_role",
                "roles": roles,
                "target_role": "rT",
                "latents": [
                    {
                        "name": "seen",
                        "type": "bool",
                        "init": False,
                        "set_on": {"event": "applied", "role": first},
                        "reset_on": None,
                    }
                ],
                "condition": {"op": "not", "arg": {"var": "seen"}},
            },
            {
                "hypothesis_id": "m_always_passes",
                "roles": roles,
                "target_role": "rT",
                "latents": [],
                "condition": {"const": True},
            },
        ]
    }


class _Fake(http.server.BaseHTTPRequestHandler):
    """One handler for every operation, so a test can only distinguish them the
    way the runtime does: by the prompt actually sent."""

    mode: str = "valid"
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))).decode())
        type(self).seen.append(body)
        system = body["messages"][0]["content"].lower()
        user = body["messages"][1]["content"]
        if "propose measurements" in system:
            content = '{"handles": []}'
        elif "closed intermediate representation" in system:
            if type(self).mode == "interrupted":
                # Served, possibly billed, and no usable response comes back.
                self.close_connection = True
                return
            content = json.dumps(self._hypotheses_reply(user))
        else:
            content = json.dumps({"diff": "", "summary": "no patch is proposed by these tests"})
        payload = json.dumps(
            {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "model": "fake",
                "usage": {"prompt_tokens": 120, "completion_tokens": 60},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _hypotheses_reply(self, user: str) -> dict[str, Any]:
        mode = type(self).mode
        if mode == "empty":
            return {"hypotheses": []}
        if mode == "invalid":
            # Confidence is refused as an input at every level.
            reply = _hypotheses_for(roles_in(user))
            reply["hypotheses"][0]["confidence"] = 0.97
            return reply
        return _hypotheses_for(roles_in(user))

    def log_message(self, *a: Any) -> None:
        return


def roles_in(user_message: str) -> list[str]:
    """Recover the role list from the prompt.

    The fake cannot answer validly without it: `validate_hypotheses` refuses any
    hypothesis whose `roles` differ from the discovered set. So a valid response
    is itself evidence that the request carried the roles.
    """
    match = re.search(r"Roles \(use exactly this list, in this order\): (\[[^\]]*\])", user_message)
    assert match, f"the propose_hypotheses prompt carried no role list:\n{user_message[:400]}"
    roles = ast.literal_eval(match.group(1))
    assert isinstance(roles, list) and roles[-1] == "rT"
    return [str(r) for r in roles]


@pytest.fixture
def provider(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Fake.seen = []
    _Fake.mode = "valid"
    monkeypatch.setenv("RIFT_LLM_URL", f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
    monkeypatch.setenv("RIFT_LLM_KEY", "sk-SENTINEL-fake-for-tests")
    monkeypatch.setenv("RIFT_LLM_MODEL", "fake")
    try:
        yield _Fake
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def unresolved_repo(tmp_path: Path) -> Path:
    return build_repo(tmp_path / "unresolved", UNRESOLVED_FILES)


def run_why(repo: Path, capsys, extra: list[str] | None = None) -> tuple[int, dict]:
    from riftagent.app import main

    code = main(
        [
            "--repo",
            str(repo),
            "--json",
            "why",
            SIMPLE_TARGET,
            "--allow-partial-sandbox",
            "--max-usd",
            "1.00",
            # The enumerated space needs about ten experiments to stall on this
            # fixture. A lower bound would end the loop by probe exhaustion
            # instead, which is a different stop and deliberately not the point
            # at which a request is made.
            "--max-probes",
            "16",
            "--max-commands",
            "400",
            *(extra or []),
        ]
    )
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def ledger_of(repo: Path) -> list[dict]:
    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    return [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]


def ops(events: list[dict], kind: str) -> list[dict]:
    return [e["payload"] for e in events if e["kind"] == kind]


def hypothesis_requests(events: list[dict]) -> list[dict]:
    return [p for p in ops(events, "model_request_started") if p.get("operation") == "propose_hypotheses"]


# ---------------------------------------------------------------- valid


def test_valid_hypotheses_are_requested_at_the_ambiguity_point_and_scored(unresolved_repo, capsys, provider):
    """Without the call site there is no request; without the merge and rescore
    the returned ids never reach `kernel.score`, so `contradicted` cannot name
    one."""
    code, receipt = run_why(unresolved_repo, capsys)
    events = ledger_of(unresolved_repo)

    # 1. the request was made, once, on the deterministic path's own terms
    started = hypothesis_requests(events)
    assert len(started) == 1, "propose_hypotheses was not requested exactly once at the ambiguity point"

    # 2. it was reserved before the request and settled after it, by request id
    reserved = [p for p in ops(events, "spend_reserved") if p["operation"] == "propose_hypotheses"]
    settled = [p for p in ops(events, "spend_settled") if p["operation"] == "propose_hypotheses"]
    assert len(reserved) == 1 and len(settled) == 1
    assert reserved[0]["request_id"] == settled[0]["request_id"]
    assert reserved[0]["spend_event_id"] and settled[0]["spend_event_id"]

    # 3. the reservation preceded the network call, and the settlement followed
    order = [e["kind"] for e in events if e["payload"].get("operation") == "propose_hypotheses"]
    assert order.index("spend_reserved") < order.index("model_request_started") < order.index("spend_settled")

    # 4. the theory space grew by exactly the ids returned
    proposals = ops(events, "hypotheses_proposed")
    assert len(proposals) == 2, "the widened theory space was not recorded as its own event"
    enumerated, widened = proposals
    assert widened["model_proposed"] == ["m_counts_runs", "m_needs_role", "m_always_passes"]
    assert widened["count"] == enumerated["count"] + 3
    assert "no remaining probe could separate" in widened["origin"]

    # 5. the kernel scored them against the evidence already recorded. An
    #    unconditional-pass theory cannot survive an observed failure, and the
    #    only way its id appears here is by going through `kernel.score`.
    diagnosis = ops(events, "diagnosis_emitted")[-1]["diagnosis"]
    assert "m_always_passes" in diagnosis["contradicted"]

    # 6. and the run still ends in an honest unresolved verdict
    assert receipt["verdict"] in {
        Verdict.REPRESENTATION_INADEQUATE.value,
        Verdict.UNDERDETERMINED.value,
    }, receipt["verdict"]
    assert receipt["spend"]["requests"] >= 1
    assert code != 0


def test_the_request_carries_the_roles_and_the_recorded_observations(unresolved_repo, capsys, provider):
    """Argument provenance: the prompt's contents are asserted from the body the
    provider actually received, not from the builder in isolation."""
    run_why(unresolved_repo, capsys)
    body = next(b for b in provider.seen if "closed intermediate representation" in b["messages"][0]["content"].lower())
    user = body["messages"][1]["content"]

    events = ledger_of(unresolved_repo)
    roles = ops(events, "hypotheses_proposed")[0]["roles"]
    assert roles_in(user) == roles, "the prompt's role list is not the discovered role set"

    # every handle behind a role is named, and every observation already made is
    # in the trace the model was shown
    handles = ops(events, "handles_discovered")[0]["handles"]
    for h in handles:
        assert f"{h['kind']}:{h['arg']}" in user, f"role for {h['kind']}:{h['arg']} was not disclosed"
    observed = [p["observation"] for p in ops(events, "probe_selected")]
    assert user.count("→ target") >= len(observed), "the recorded observations were not shown"


def test_fix_reaches_the_same_shared_diagnosis_flow(unresolved_repo, capsys, provider):
    """`fix` and `why` share one diagnosis. A second, `fix`-only wiring would
    pass every `why` test above and still be a second flow."""
    from riftagent.app import main

    main(
        [
            "--repo",
            str(unresolved_repo),
            "--json",
            "fix",
            SIMPLE_TARGET,
            "--allow-partial-sandbox",
            "--max-usd",
            "1.00",
            "--max-probes",
            "16",
            "--max-commands",
            "400",
        ]
    )
    capsys.readouterr()
    assert len(hypothesis_requests(ledger_of(unresolved_repo))) == 1


# ---------------------------------------------------------------- empty


def test_an_empty_hypothesis_list_adds_nothing_and_costs_the_diagnosis_nothing(unresolved_repo, capsys, provider):
    """Zero is below the bounded minimum, so it is refused as invalid rather
    than treated as a proposal. Either way the deterministic diagnosis stands."""
    provider.mode = "empty"
    code, receipt = run_why(unresolved_repo, capsys)
    events = ledger_of(unresolved_repo)

    assert len(hypothesis_requests(events)) == 1
    invalid = [p for p in ops(events, "model_response_invalid") if p["operation"] == "propose_hypotheses"]
    assert len(invalid) == 1 and invalid[0]["effect"] == "enumerated theories only"
    assert len(ops(events, "hypotheses_proposed")) == 1, "an empty reply widened the theory space"
    # charged, because the request was served
    assert [p for p in ops(events, "spend_settled") if p["operation"] == "propose_hypotheses"]
    assert receipt["verdict"] in {Verdict.REPRESENTATION_INADEQUATE.value, Verdict.UNDERDETERMINED.value}
    assert code != 0


# ---------------------------------------------------------------- invalid


def test_an_invalid_response_is_refused_and_the_diagnosis_is_unchanged(unresolved_repo, capsys, provider):
    provider.mode = "invalid"
    code, receipt = run_why(unresolved_repo, capsys)
    events = ledger_of(unresolved_repo)

    invalid = [p for p in ops(events, "model_response_invalid") if p["operation"] == "propose_hypotheses"]
    assert len(invalid) == 1
    assert "confidence" in invalid[0]["reason"]
    assert len(ops(events, "hypotheses_proposed")) == 1
    diagnosis = ops(events, "diagnosis_emitted")[-1]["diagnosis"]
    assert not any(h.startswith("m_") for h in diagnosis["contradicted"])
    assert receipt["verdict"] in {Verdict.REPRESENTATION_INADEQUATE.value, Verdict.UNDERDETERMINED.value}
    assert code != 0


# ---------------------------------------------------------------- refused


def test_a_refused_budget_stops_the_request_before_it_is_sent(unresolved_repo, capsys, provider):
    """Refusal before the request is the only point at which refusing is free,
    so the assertion is that nothing was sent -- not merely that nothing was
    used."""
    code, receipt = run_why(unresolved_repo, capsys, ["--max-usd", "0.0000001"])
    events = ledger_of(unresolved_repo)

    refused = [p for p in ops(events, "spend_refused") if p["operation"] == "propose_hypotheses"]
    assert len(refused) == 1, "the hypotheses request was not refused by the budget"
    assert not hypothesis_requests(events), "a refused request was sent anyway"
    assert not any(
        "closed intermediate representation" in b["messages"][0]["content"].lower() for b in provider.seen
    ), "the provider received a request the budget had refused"
    assert len(ops(events, "hypotheses_proposed")) == 1
    assert receipt["verdict"] in {Verdict.REPRESENTATION_INADEQUATE.value, Verdict.UNDERDETERMINED.value}
    assert code != 0


# ---------------------------------------------------------------- interrupted


def test_an_interrupted_request_is_charged_in_full_and_recorded(unresolved_repo, capsys, provider):
    """A request that was served but returned nothing usable may still have been
    billed. Releasing the reservation would make an unanswered request free."""
    provider.mode = "interrupted"
    code, receipt = run_why(unresolved_repo, capsys)
    events = ledger_of(unresolved_repo)

    assert len(hypothesis_requests(events)) == 1
    settled = [p for p in ops(events, "spend_settled") if p["operation"] == "propose_hypotheses"]
    assert len(settled) == 1, "an interrupted request left its reservation unsettled"
    unavailable = [p for p in ops(events, "model_unavailable") if p["operation"] == "propose_hypotheses"]
    assert len(unavailable) == 1 and unavailable[0]["effect"] == "enumerated theories only"
    assert len(ops(events, "hypotheses_proposed")) == 1
    assert receipt["verdict"] in {Verdict.REPRESENTATION_INADEQUATE.value, Verdict.UNDERDETERMINED.value}
    assert code != 0


# ---------------------------------------------------------------- no downgrade


def test_a_supported_diagnosis_is_never_put_at_risk_by_a_model_request(order_repo, capsys, provider):
    """The guard, stated as the behaviour it protects: where the evidence already
    supports a cause, widening the theory space could only split one behavioural
    class into two and turn `diagnosis_supported` into `underdetermined`."""
    from riftagent.app import main

    code = main(
        [
            "--repo",
            str(order_repo),
            "--json",
            "why",
            ORDER_TARGET,
            "--allow-partial-sandbox",
            "--max-usd",
            "1.00",
        ]
    )
    receipt = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    events = ledger_of(order_repo)

    assert receipt["verdict"] == Verdict.DIAGNOSIS_SUPPORTED.value, receipt.get("reason")
    assert not hypothesis_requests(events), "a supported diagnosis was put at risk by a model request"
    assert not any("closed intermediate representation" in b["messages"][0]["content"].lower() for b in provider.seen)
    notes = ops(events, "diagnosis_emitted")[-1]["diagnosis"]["notes"]
    assert any("already supported a cause" in n for n in notes), notes
    assert code == 0
