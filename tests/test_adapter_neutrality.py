"""Ruling 3: regression tests for two corrected honesty defects.

An earlier M1 checkpoint claimed that the adapter preserved provider neutrality
and that provider errors were summarised without echoing the body. The shipped
code supported neither claim: it sent `x-api-key` and `anthropic-version` to
whatever URL was configured, and it forwarded the first 200 characters of the
provider's `message` field — which is exactly where a provider quotes the
offending request back. These tests pin the corrected behaviour so the claims
cannot silently become false again.

The provider is a real HTTP server on loopback. Nothing here contacts a network
host and no credential is required.
"""

from __future__ import annotations

import http.server
import json
import threading
import traceback
from pathlib import Path
from typing import Any

import pytest

from riftagent.llm import ModelUnavailable, ProviderConfig, post_chat
from riftagent.records import EventKind, Ledger, read_events

# Distinct, unmistakable strings. If any of these reaches an exception, a
# ledger or a receipt, the test names the exact leak path.
SECRET = "sk-SENTINEL-KEY-must-never-appear"
PROMPT = "SENTINELPROMPT-do-not-echo-me"
SOURCE = "def sentinel_source_function():  # SENTINELSOURCE"


class _Handler(http.server.BaseHTTPRequestHandler):
    """Replies with a 400 shaped like a real provider error whose `message`
    quotes the submitted request — the realistic case, not a contrived one."""

    captured: dict[str, Any] = {}

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        type(self).captured = {
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": raw,
        }
        payload = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": (
                        f"Your request could not be processed. Submitted content: {PROMPT} "
                        f"{SOURCE} (authenticated with {SECRET})"
                    ),
                    "param": SOURCE,
                }
            }
        ).encode("utf-8")
        self.send_response(400)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a: Any) -> None:  # pragma: no cover - silence stderr
        return


@pytest.fixture
def echoing_provider():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------
# defect 1 — provider neutrality
# --------------------------------------------------------------------------


def test_an_openai_shaped_https_endpoint_is_accepted():
    cfg = ProviderConfig.from_env(
        {
            "RIFT_LLM_URL": "https://api.openai.com/v1/chat/completions",
            "RIFT_LLM_KEY": SECRET,
            "RIFT_LLM_MODEL": "gpt-4o-mini",
        }
    )
    assert cfg.url == "https://api.openai.com/v1/chat/completions"


def test_the_anthropic_compatibility_endpoint_is_accepted():
    cfg = ProviderConfig.from_env(
        {
            "RIFT_LLM_URL": "https://api.anthropic.com/v1/chat/completions",
            "RIFT_LLM_KEY": SECRET,
            "RIFT_LLM_MODEL": "claude-haiku-4-5-20251001",
        }
    )
    assert cfg.model == "claude-haiku-4-5-20251001"


def test_no_provider_specific_header_is_sent(echoing_provider: str):
    """The corrected claim: the adapter speaks only the OpenAI-compatible
    contract, whatever host is configured. A vendor header aimed at an
    arbitrary URL is both a neutrality failure and a key sent into an
    authentication scheme the operator did not choose."""
    cfg = ProviderConfig(url=echoing_provider, key=SECRET, model="m")
    with pytest.raises(ModelUnavailable):
        post_chat(cfg, [{"role": "user", "content": "hi"}], max_output_tokens=16)

    sent = _Handler.captured["headers"]
    assert sent["authorization"] == f"Bearer {SECRET}"
    for vendor in ("x-api-key", "anthropic-version", "openai-organization", "api-key"):
        assert vendor not in sent, f"provider-specific header {vendor} was sent"


def test_the_adapter_source_contains_no_vendor_header():
    """A structural check as well as a behavioural one: the strings are gone,
    not merely unreachable on this path."""
    import riftagent.llm

    text = Path(riftagent.llm.__file__).read_text(encoding="utf-8")
    body = text.split("def test", 1)[0]
    for vendor in ("x-api-key", "anthropic-version"):
        assert vendor not in body.lower(), f"llm.py still mentions {vendor}"


def test_loopback_plaintext_http_is_accepted():
    """Deterministic fake providers need a real socket; on loopback there is no
    network to observe."""
    for host in ("127.0.0.1:8080", "localhost:9999"):
        cfg = ProviderConfig.from_env(
            {
                "RIFT_LLM_URL": f"http://{host}/v1/chat/completions",
                "RIFT_LLM_KEY": SECRET,
                "RIFT_LLM_MODEL": "fake",
            }
        )
        assert cfg.url.startswith("http://")


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1/chat/completions",
        "http://10.0.0.5/v1/chat/completions",
        "http://evil.example.com/v1/chat/completions",
        "ftp://host/v1/chat/completions",
    ],
)
def test_non_loopback_plaintext_is_rejected(url: str):
    """The key would cross the wire in clear text."""
    with pytest.raises(ModelUnavailable):
        ProviderConfig.from_env({"RIFT_LLM_URL": url, "RIFT_LLM_KEY": SECRET, "RIFT_LLM_MODEL": "m"})


def test_a_base_url_is_still_rejected():
    with pytest.raises(ModelUnavailable, match="complete POST endpoint"):
        ProviderConfig.from_env(
            {
                "RIFT_LLM_URL": "https://api.anthropic.com/v1",
                "RIFT_LLM_KEY": SECRET,
                "RIFT_LLM_MODEL": "m",
            }
        )


# --------------------------------------------------------------------------
# defect 2 — the provider error body is never echoed
# --------------------------------------------------------------------------


def test_a_provider_error_reaches_no_exception_text(echoing_provider: str):
    cfg = ProviderConfig(url=echoing_provider, key=SECRET, model="m")
    with pytest.raises(ModelUnavailable) as caught:
        post_chat(
            cfg,
            [{"role": "user", "content": f"{PROMPT}\n{SOURCE}"}],
            max_output_tokens=16,
        )

    # The whole traceback, not just the message: a chained `from exc` would put
    # the original body in the __cause__ frame where a crash dump would find it.
    rendered = "".join(traceback.format_exception(caught.value))
    for sentinel in (PROMPT, SOURCE, SECRET):
        assert sentinel not in rendered, f"{sentinel!r} leaked into the traceback"

    # What survives is a classification, and the status code the caller already
    # has. That is enough to act on and carries nothing from the request.
    assert "400" in str(caught.value)
    assert "invalid_request_error" in str(caught.value)


def test_a_provider_error_reaches_no_ledger_or_receipt(echoing_provider: str, tmp_path: Path):
    """Recorded the way the application records an unavailable provider."""
    cfg = ProviderConfig(url=echoing_provider, key=SECRET, model="m")
    ledger = Ledger(tmp_path / "ledger.jsonl", "verify-0a1b2c3d-0000")
    try:
        post_chat(cfg, [{"role": "user", "content": f"{PROMPT}\n{SOURCE}"}], max_output_tokens=16)
    except ModelUnavailable as exc:
        ledger.append(
            EventKind.MODEL_UNAVAILABLE,
            {"reason": str(exc), "operation": "propose_hypotheses"},
        )

    raw = (tmp_path / "ledger.jsonl").read_bytes()
    for sentinel in (PROMPT, SOURCE, SECRET):
        assert sentinel.encode() not in raw, f"{sentinel!r} leaked into the ledger"

    # And the same through replay, which is what a receipt is projected from.
    events, truncated = read_events(tmp_path / "ledger.jsonl")
    assert not truncated
    assert len(events) == 1
    projected = json.dumps([e.to_dict() for e in events])
    for sentinel in (PROMPT, SOURCE, SECRET):
        assert sentinel not in projected


@pytest.mark.parametrize(
    "kind",
    [
        "not a dict",
        123,
        None,
        {"nested": "object"},
        "type_with_spaces and prose that quotes " + PROMPT,
        PROMPT,
        "A" * 500,
    ],
)
def test_only_a_token_shaped_error_type_is_repeated(kind: Any, tmp_path: Path):
    """The `type` field is repeated only when it looks like a vendor error
    token. Anything else — including a provider that puts prose there — is
    dropped rather than trusted."""
    import urllib.error

    from riftagent.llm import _safe_error_detail

    body = json.dumps({"error": {"type": kind, "message": PROMPT}}).encode()
    exc = urllib.error.HTTPError("http://127.0.0.1/v1/chat/completions", 400, "Bad Request", {}, None)
    exc.read = lambda n=None, _b=body: _b  # type: ignore[method-assign]
    detail = _safe_error_detail(exc)
    assert PROMPT not in detail
    assert detail in ("provider error", kind if isinstance(kind, str) else "provider error")


def test_a_key_never_appears_in_a_config_repr():
    cfg = ProviderConfig(url="https://h/v1/chat/completions", key=SECRET, model="m")
    assert SECRET not in repr(cfg)
    assert SECRET not in f"{cfg!r}"
    assert "<redacted>" in repr(cfg)


# --------------------------------------------------------------------------
# DAR-014 — the outbound payload asserts no sampling preference by default
# --------------------------------------------------------------------------


def _sent_body(url: str, **kwargs) -> dict:
    """Drive one real request and return the JSON the provider actually received.

    Asserted against the captured body rather than the call arguments: the
    question is what left the machine, and a default that is correct in the
    signature but serialised anyway would pass an argument-level check.
    """
    cfg = ProviderConfig.from_env({"RIFT_LLM_URL": url, "RIFT_LLM_KEY": SECRET, "RIFT_LLM_MODEL": "m"})
    with pytest.raises(ModelUnavailable):
        post_chat(cfg, [{"role": "user", "content": "hello"}], max_output_tokens=16, **kwargs)
    return json.loads(_Handler.captured["body"])


def test_no_temperature_is_sent_by_default(echoing_provider):
    """The field is omitted entirely, not sent as null.

    Several current models reject a non-default sampling parameter with a 400,
    and 0.0 is not the default anywhere — the adapter was asserting a preference
    no caller had expressed, and failing against otherwise-compatible providers
    for it. `null` would be no better than 0.0: it is still the key being present.
    """
    body = _sent_body(echoing_provider)
    assert "temperature" not in body, body
    assert "top_p" not in body and "top_k" not in body, body
    # The control: the request is otherwise complete, so the absence above is
    # about temperature rather than about a request that failed to build.
    assert body["model"] == "m"
    assert body["max_tokens"] == 16
    assert body["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.parametrize("value", [0.0, 0.2, 1.0])
def test_an_explicit_temperature_is_still_serialised(echoing_provider, value: float):
    """The positive control, and the half that keeps the adapter neutral.

    Providers that accept a sampling parameter must still be able to receive
    one. Removing the capability rather than the default would trade one
    provider-specific failure for another.
    """
    body = _sent_body(echoing_provider, temperature=value)
    assert body["temperature"] == value, body


def test_the_default_is_none_in_the_signature(echoing_provider):
    """Pins the default at the signature too, so a later edit that reintroduces
    a numeric default fails here as well as in the payload test above."""
    import inspect

    default = inspect.signature(post_chat).parameters["temperature"].default
    assert default is None, f"post_chat defaults temperature to {default!r}"
