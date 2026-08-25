"""A deterministic OpenAI-compatible provider on loopback. No network, no spend.

The dry run has to exercise the **same** `run` path the paid benchmark will use,
which means the real provider adapter, the real reserve/settle ledger and the
real schema-repair policy. Monkeypatching `llm.post_chat` cannot do that: the
arms run as subprocesses. So this serves canned completions over
`127.0.0.1`, and `RIFT_LLM_URL` points at it.

Nothing leaves the machine and nothing is billed. The responses are scripted per
case so the run can be driven through outcomes that matter — a correct fix, a
target-passing patch that breaks preserved behaviour, an unapplicable patch,
malformed JSON that must consume the one authorised schema repair, and a refusal
that must abstain rather than invent a candidate.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UNAPPLICABLE_DIFF = "--- a/nope.py\n+++ b/nope.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"

CORRECT = "correct"
BREAKS_PRESERVATION = "breaks_preservation"
UNAPPLICABLE = "unapplicable"
MALFORMED_THEN_VALID = "malformed_then_valid"
NO_CANDIDATE = "no_candidate"


class Script:
    """Answers chosen from the request itself, not from a shared queue.

    A flat list consumed in order looked adequate and was not: arms make
    different numbers of calls — arm A proposes once, arms B and C ask for
    handles and hypotheses first — so after the first case every later arm
    received a reply meant for something else, and the run reported
    `unverifiable` almost everywhere.

    Dispatching on the prompt makes the fake deterministic and order-independent:
    a handles request always gets handles, a change request always gets the
    candidate scripted for *that case*, identified by the target node id the
    prompt carries.
    """

    def __init__(self, per_case: dict[str, list[str]], model: str = "claude-sonnet-4-6") -> None:
        self.per_case = {k: list(v) for k, v in per_case.items()}
        self.model = model
        self.requests: list[dict] = []
        self.change_requests: list[str] = []

    def _kind(self, system: str) -> str:
        if "unified diff" in system or "propose_change" in system or "diff" in system.lower():
            return "change"
        if "handle" in system.lower():
            return "handles"
        return "hypotheses"

    def next_reply(self, body: dict) -> tuple[str, str]:
        self.requests.append(body)
        messages = body.get("messages") or [{}]
        system = str(messages[0].get("content", ""))
        user = " ".join(str(m.get("content", "")) for m in messages[1:])
        kind = self._kind(system)

        if kind == "handles":
            return json.dumps({"handles": [{"kind": "env", "arg": "TZ"}]}), self.model
        if kind == "hypotheses":
            return json.dumps({"hypotheses": []}), self.model

        for target, replies in self.per_case.items():
            if target in user:
                self.change_requests.append(target)
                # Each arm gets the same scripted candidate for its case; a
                # malformed-then-valid script is consumed in order *within* the
                # case so the one authorised repair is exercised exactly once.
                return (replies.pop(0) if len(replies) > 1 else replies[0]), self.model
        return json.dumps({"diff": "", "summary": "no script matched this request"}), self.model


def _handler(script: Script) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            text, model = script.next_reply(body)
            payload = json.dumps(
                {
                    "id": "fake",
                    "object": "chat.completion",
                    "model": model,
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            return

    return Handler


class FakeProvider:
    """Context manager yielding the base URL to point `RIFT_LLM_URL` at."""

    def __init__(self, script: Script) -> None:
        self.script = script
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> str:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self.script))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1/chat/completions"

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def proposal(diff: str, summary: str = "fake candidate") -> str:
    return json.dumps({"diff": diff, "summary": summary})


def scripted(kind: str, correct_diff: str, wrong_diff: str) -> list[str]:
    """The replies for one case, in the order that case will consume them."""
    table: dict[str, list[str]] = {
        CORRECT: [proposal(correct_diff, "apply the historical fix")],
        BREAKS_PRESERVATION: [proposal(wrong_diff, "make the target pass")],
        UNAPPLICABLE: [proposal(UNAPPLICABLE_DIFF, "will not apply")],
        # One malformed reply then a valid one: the frozen policy allows exactly
        # one schema repair, and this proves the runner neither adds retries nor
        # loses the recovered candidate.
        MALFORMED_THEN_VALID: ["{not json at all", proposal(correct_diff, "recovered after repair")],
        NO_CANDIDATE: [json.dumps({"summary": "I cannot determine a fix"})],
    }
    return table[kind]


Reply = Callable[[dict], str]
