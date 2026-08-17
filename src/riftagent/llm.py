"""Provider adapter and the four typed model operations (M1 ships three).

This module is the only place an HTTP request to a model is constructed. It is
deliberately the *narrowest* module in the runtime:

* it imports no kernel, no sandbox, no checks — enforced by an AST test;
* it executes no repository command and applies no patch;
* it returns validated, immutable data, never prose that is treated as evidence;
* it discards model confidence entirely. A model that says it is certain and a
  model that says it is guessing produce identical kernel input.

The provider surface is plain OpenAI-compatible chat completions over HTTP with
no SDK. `RIFT_LLM_URL` is the **complete POST endpoint** — the adapter posts to
exactly that string and never appends a path to it. That is checked rather than
assumed, because a base-URL/endpoint mix-up is the single most likely
misconfiguration and it fails as an opaque 404 at benchmark time.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from riftagent.records import (
    Handle,
    IRValidationError,
    ModelUsage,
    Primitive,
    ValidationError,
    canonical_diff,
    require,
    strict_fields,
    validate_hypothesis,
)

ENV_URL = "RIFT_LLM_URL"
ENV_KEY = "RIFT_LLM_KEY"
ENV_MODEL = "RIFT_LLM_MODEL"

# Bounded by construction. A response larger than this is refused before it is
# parsed: an unbounded body is a denial-of-service surface, not a proposal.
MAX_RESPONSE_BYTES = 1_000_000
MAX_HYPOTHESES = 6
MIN_HYPOTHESES = 3
MAX_HANDLES = 8
# Bounded context. The tail of a traceback carries the signal; the head is
# usually setup noise, and an unbounded prompt is an unbounded bill.
FAILURE_TEXT_CHARS = 6000
# The trace shown to `propose_hypotheses`. Bounded for the same reason, and the
# tail is the part the remaining ambiguity is about.
MAX_OBSERVATIONS = 40


class ModelUnavailable(RuntimeError):
    """No usable provider configuration, or the provider could not be reached.

    Never silently substituted with fixture or template output: the caller
    converts this into an explicit `model_unavailable` abstention.
    """


class ModelResponseInvalid(ValueError):
    """The model returned something that is not a valid proposal.

    Carries no authority. One repair attempt is permitted, then the caller
    abstains.
    """


# A key must never cross the network in clear text. The single exception is a
# loopback address, where there is no network to observe: the deterministic
# fake-provider tests need a real socket, and forcing them onto TLS would mean
# shipping a certificate in the test tree.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _transport_ok(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return True
    if parts.scheme != "http":
        return False
    return (parts.hostname or "") in LOOPBACK_HOSTS


@dataclass(frozen=True)
class ProviderConfig:
    """Resolved provider settings. The key is held here and never rendered.

    `__repr__` is overridden because a dataclass repr would otherwise print the
    key into any traceback, log line, or debugger frame that touches it.
    """

    url: str
    key: str
    model: str

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"ProviderConfig(url={self.url!r}, model={self.model!r}, key=<redacted>)"

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> ProviderConfig:
        src = env if env is not None else os.environ
        url = (src.get(ENV_URL) or "").strip()
        key = (src.get(ENV_KEY) or "").strip()
        model = (src.get(ENV_MODEL) or "").strip()
        missing = [n for n, v in ((ENV_URL, url), (ENV_KEY, key), (ENV_MODEL, model)) if not v]
        if missing:
            raise ModelUnavailable(f"provider not configured: {', '.join(missing)} unset or empty")
        if not _transport_ok(url):
            raise ModelUnavailable(f"{ENV_URL} must use https, or plain http only on loopback (got {url!r})")
        # A complete endpoint, not a base URL. Catching this here turns a
        # confusing 404 mid-benchmark into a clear configuration error before
        # any money is spent.
        if not url.rstrip("/").endswith("/chat/completions"):
            raise ModelUnavailable(
                f"{ENV_URL} must be the complete POST endpoint ending in /chat/completions, "
                f"not a base URL (got {url!r})"
            )
        return ProviderConfig(url=url, key=key, model=model)


@dataclass(frozen=True)
class ModelReply:
    """One provider response, already stripped of authority."""

    text: str
    usage: ModelUsage
    model_reported: str
    finish_reason: str

    def redacted(self) -> dict[str, Any]:
        """What may be written to the ledger.

        The prompt is not recorded and the key never existed in this object;
        what is durable is the usage, the model the provider says served the
        request, and why it stopped.
        """
        return {
            "usage": self.usage.to_dict(),
            "model_reported": self.model_reported,
            "finish_reason": self.finish_reason,
            "response_chars": len(self.text),
        }


def post_chat(
    config: ProviderConfig,
    messages: list[dict[str, str]],
    max_output_tokens: int,
    timeout_s: float = 120.0,
    temperature: float | None = 0.0,
) -> ModelReply:
    """One bounded chat-completions request. No streaming, no tool calling.

    The provider is given no tools, no functions, and no ability to request
    execution. Its entire surface is text in, text out.
    """
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "max_tokens": int(max_output_tokens),
    }
    if temperature is not None:
        body["temperature"] = temperature
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - https enforced in from_env
        config.url,
        data=payload,
        method="POST",
        # Exactly the two headers the OpenAI-compatible chat-completions
        # contract defines. No provider-specific header is sent, to any host:
        # a vendor header aimed at whatever URL happens to be configured is
        # both a leak of the key into an unintended authentication scheme and
        # the end of provider neutrality. Anthropic's compatibility endpoint
        # accepts bearer auth like every other OpenAI-compatible endpoint.
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {config.key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = _safe_error_detail(exc)
        raise ModelUnavailable(f"provider returned HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ModelUnavailable(f"provider unreachable: {type(exc).__name__}") from None

    if len(raw) > MAX_RESPONSE_BYTES:
        raise ModelResponseInvalid("provider response exceeded the size bound")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelResponseInvalid(f"provider response was not JSON: {exc}") from None
    return _parse_reply(data)


# A provider error `type` is a machine token from a small vendor vocabulary
# (`invalid_request_error`, `authentication_error`, ...). A provider error
# `message` is free text that routinely quotes the submitted request back —
# which here means prompt text, and therefore repository source. Only a value
# matching this shape is repeated, and nothing else from the body ever is.
_ERROR_TOKEN = re.compile(r"\A[a-z][a-z0-9_.-]{0,39}\Z")


def _safe_error_detail(exc: urllib.error.HTTPError) -> str:
    """Classify a provider error without repeating any part of the body.

    Truncating the provider's `message` was not sufficient: the leading 200
    characters of a 400 are exactly where a provider quotes the offending
    input. So the message is not read at all. What survives is the vendor's
    error *type* when it is a recognisable token, and otherwise nothing — the
    status code the caller already has is the diagnostic.
    """
    try:
        body = json.loads(exc.read(8192).decode("utf-8", "replace"))
    except Exception:
        return "unreadable error body"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        kind = err.get("type")
        if isinstance(kind, str) and _ERROR_TOKEN.match(kind):
            return kind
    return "provider error"


def _parse_reply(data: Any) -> ModelReply:
    if not isinstance(data, dict):
        raise ModelResponseInvalid("provider response was not an object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelResponseInvalid("provider response contained no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ModelResponseInvalid("provider choice was not an object")
    message = first.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ModelResponseInvalid("provider choice contained no text content")
    usage_raw = data.get("usage")
    usage = ModelUsage()
    if isinstance(usage_raw, dict):
        # Provider-reported only. A missing field stays None and renders as
        # `unknown`; it is never estimated from the text.
        inp = usage_raw.get("prompt_tokens")
        out = usage_raw.get("completion_tokens")
        usage = ModelUsage(
            input_tokens=int(inp) if isinstance(inp, int) else None,
            output_tokens=int(out) if isinstance(out, int) else None,
        )
    return ModelReply(
        text=message["content"],
        usage=usage,
        model_reported=str(data.get("model", "")),
        finish_reason=str(first.get("finish_reason", "")),
    )


# --------------------------------------------------------------------------
# JSON extraction
# --------------------------------------------------------------------------


def extract_json(text: str) -> dict[str, Any]:
    """Pull one JSON object out of a model reply.

    The surrounding prose is discarded, never executed and never interpreted.
    Only a top-level object is accepted; a bare array or scalar is not a
    proposal shape this runtime understands.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        body = stripped.split("```")
        for chunk in body:
            chunk = chunk.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("{"):
                stripped = chunk
                break
    start = stripped.find("{")
    if start < 0:
        raise ModelResponseInvalid("no JSON object found in the response")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(stripped[start:], start=start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(stripped[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise ModelResponseInvalid(f"malformed JSON object: {exc}") from None
                if not isinstance(parsed, dict):
                    raise ModelResponseInvalid("top-level JSON value was not an object")
                return parsed
    raise ModelResponseInvalid("unterminated JSON object in the response")


# --------------------------------------------------------------------------
# prompts
#
# Every prompt states the closed vocabulary and demands one JSON object. The
# instructions are not a security boundary — the validators are. A prompt that
# is ignored costs one abstention; it can never cost an unvalidated value.
# --------------------------------------------------------------------------


_HANDLES_SYSTEM = (
    "You propose measurements, not fixes and not commands. "
    "Reply with exactly one JSON object and no prose. "
    'Shape: {"handles": [{"kind": "<primitive>", "arg": "<name-or-path>"}]}. '
    "Permitted kinds and nothing else: "
    "env (an environment variable whose being set may matter), "
    "unsetenv (a variable whose absence may matter), "
    "clear (a state directory name to delete), "
    "first (a test file or directory to run before the target), "
    "file_assert (a path whose existence is asserted), "
    "dep_assert (an importable module name whose presence is asserted). "
    "arg must be a bare name or a repository-relative path: no shell characters, "
    "no absolute path, no '..'. Do not include a confidence, certainty, "
    "probability, score or likelihood field; a response containing one is discarded. "
    "At most 8 handles. Propose only handles that are not already listed."
)


def handles_prompt(failure_text: str, node_id: str, existing: list[Handle]) -> list[dict[str, str]]:
    """Build the `propose_handles` request.

    The failure text is truncated rather than sent whole: the useful signal is
    the traceback tail, and an unbounded body is an unbounded bill.
    """
    already = ", ".join(h.label for h in existing) or "(none)"
    tail = failure_text[-FAILURE_TEXT_CHARS:]
    return [
        {"role": "system", "content": _HANDLES_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Failing test: {node_id}\n"
                f"Handles already discovered: {already}\n\n"
                f"Observed failure output (truncated to the last {FAILURE_TEXT_CHARS} characters):\n"
                f"{tail}"
            ),
        },
    ]


_HYPOTHESES_SYSTEM = (
    "You propose theories in a closed intermediate representation, not prose and not fixes. "
    "Reply with exactly one JSON object and no prose. "
    'Shape: {"hypotheses": [{"hypothesis_id": "<unique string>", "roles": [<the exact role list '
    'given>], "target_role": "rT", "latents": [...], "condition": {...}}]}. '
    "A latent is either "
    '{"name": "<id>", "type": "bool", "init": false, "set_on": {"event": "applied", "role": "<role>"}, '
    '"reset_on": null} or '
    '{"name": "<id>", "type": "counter", "max": <1-16>, "inc_on": {"event": "run", "role": null}}. '
    'An event is {"event": "applied"|"run", "role": "<role>"|null}. '
    'A condition is one of {"const": true|false}, {"var": "<latent name>"}, '
    '{"op": "ge", "var": "<counter name>", "value": <int>}, {"op": "not", "arg": <condition>}, '
    '{"op": "and", "args": [<condition>, <condition>]}, {"op": "or", "args": [<condition>, <condition>]}. '
    "The condition predicts the target run's outcome: true means it passes, false means it fails. "
    "Latent state resets at every episode boundary. No other field is accepted anywhere at any "
    "depth, and no other operator exists. "
    f"Return {MIN_HYPOTHESES}-{MAX_HYPOTHESES} hypotheses, each with a distinct hypothesis_id. "
    "Do not include a confidence, certainty, probability, score or likelihood field; a response "
    "containing one is discarded. "
    "Your theories decide nothing: each one is scored against the observations already recorded "
    "and against further experiments this runtime chooses to run. A theory that mispredicts one "
    "observed outcome is eliminated."
)


def hypotheses_prompt(
    node_id: str,
    failure_text: str,
    roles: list[str],
    handles: list[Handle],
    observed: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build the `propose_hypotheses` request.

    The role→handle mapping is disclosed. The kernel's role anonymity exists so
    that *scoring* cannot read a label — `execute` and `score` see r0..rN and
    never a name — and that invariant is untouched by telling the proposer what
    each role varies. Withholding it would ask for theories about symbols with
    no referent.

    The recorded observations are included because a theory is only worth
    proposing against evidence that already exists; every one of them is
    replayed against the proposal by `kernel.score` regardless of what the
    prompt said.
    """
    # `zip` pairs r0..rN with the handles they were built from and drops the
    # target role, which has no handle behind it.
    varies = "\n".join(f"  {role} = {h.kind}:{h.arg}" for role, h in zip(roles, handles, strict=False))
    trace = "\n".join(
        f"  applied [{'+'.join(o.get('applied') or ()) or 'nothing'}] "
        f"x{o.get('repeats', 1)} → target {o.get('outcome', '?')}"
        for o in observed[-MAX_OBSERVATIONS:]
    )
    return [
        {"role": "system", "content": _HYPOTHESES_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Failing test: {node_id}\n"
                f"Roles (use exactly this list, in this order): {roles}\n"
                f"What each role varies:\n{varies or '  (none)'}\n\n"
                f"Experiments already run and their observed outcomes:\n{trace or '  (none)'}\n\n"
                "The enumerated theory space could not be narrowed to one behavioural class by any "
                "remaining experiment. Propose theories it does not already contain.\n\n"
                f"Observed failure output (truncated to the last {FAILURE_TEXT_CHARS} characters):\n"
                f"{failure_text[-FAILURE_TEXT_CHARS:]}"
            ),
        },
    ]


_CHANGE_SYSTEM = (
    "You propose one source change. Reply with exactly one JSON object and no prose. "
    'Shape: {"diff": "<unified diff>", "summary": "<one sentence>"}. '
    "The diff must apply with `git apply` against the repository as given, use "
    "repository-relative paths, and touch implementation files only. "
    "Do not modify tests, conftest.py, pytest.ini, pyproject.toml, setup.cfg, tox.ini or "
    "anything under .git or .rift: a patch touching the checks is structurally rejected "
    "before it is run, and the attempt is charged. "
    "Do not include a confidence, certainty, probability, score or likelihood field; a "
    "response containing one is discarded. "
    "Your summary decides nothing. The patch is accepted only if the test fails without it, "
    "passes with it, fails again with the identical original error when it is withdrawn, and "
    "breaks no other declared test."
)


def change_prompt(
    node_id: str,
    failure_text: str,
    cause_labels: list[str],
    sources: list[tuple[str, str]],
) -> list[dict[str, str]]:
    """Build the `propose_change` request from bounded, selected context."""
    causes = ", ".join(cause_labels) or "(none located)"
    body = "\n\n".join(f"--- {path} ---\n{text}" for path, text in sources)
    return [
        {"role": "system", "content": _CHANGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Failing test: {node_id}\n"
                f"Diagnosed cause(s): {causes}\n\n"
                f"Observed failure output (truncated):\n{failure_text[-FAILURE_TEXT_CHARS:]}\n\n"
                f"Repository source under consideration:\n{body}"
            ),
        },
    ]


# --------------------------------------------------------------------------
# operation validators
# --------------------------------------------------------------------------


def validate_hypotheses(raw: dict[str, Any], roles: list[str]) -> list[dict[str, Any]]:
    """`propose_hypotheses` → 3-6 hypotheses in the executable IR.

    Model confidence is *rejected as an input*, not merely ignored: a response
    carrying a confidence field is refused, so no downstream code can start
    reading it by accident.
    """
    strict_fields(raw, ("hypotheses",), "propose_hypotheses")
    items = raw.get("hypotheses")
    if not isinstance(items, list):
        raise ValidationError("hypotheses must be a list")
    require(
        MIN_HYPOTHESES <= len(items) <= MAX_HYPOTHESES,
        f"expected {MIN_HYPOTHESES}-{MAX_HYPOTHESES} hypotheses, got {len(items)}",
    )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        require(isinstance(item, dict), "each hypothesis must be an object")
        for banned in ("confidence", "certainty", "probability", "score", "likelihood"):
            require(
                banned not in item,
                f"hypothesis carries {banned!r}; model confidence is not an accepted input",
            )
        strict_fields(item, ("hypothesis_id", "roles", "target_role", "latents", "condition"), "hypothesis")
        # The proposal must speak the anonymous role language, and only about
        # roles that actually exist in this task.
        require(item.get("roles") == roles, "hypothesis roles must match the discovered role set")
        try:
            validate_hypothesis(item)
        except IRValidationError as exc:
            raise ValidationError(f"hypothesis is not expressible in the IR: {exc}") from None
        hid = str(item["hypothesis_id"])
        require(hid not in seen, f"duplicate hypothesis_id {hid!r}")
        seen.add(hid)
        out.append(item)
    return out


def validate_handles(raw: dict[str, Any], existing: list[Handle]) -> list[Handle]:
    """`propose_handles` → compositions of approved primitives only.

    A novel executable primitive, a command, a script, or an absolute path is
    refused. The model may request a *measurement*; the kernel decides whether
    that measurement can be performed at all.
    """
    strict_fields(raw, ("handles",), "propose_handles")
    items = raw.get("handles")
    if not isinstance(items, list):
        raise ValidationError("handles must be a list")
    # Zero is a legitimate answer: the prompt asks only for handles *not already
    # discovered*, and there often are none. Requiring at least one would
    # pressure the model into inventing a measurement to satisfy the schema —
    # the opposite of what a bounded proposal interface is for.
    require(len(items) <= MAX_HANDLES, f"expected at most {MAX_HANDLES} handles, got {len(items)}")
    known = {h.label for h in existing}
    out: list[Handle] = []
    for item in items:
        require(isinstance(item, dict), "each handle must be an object")
        for banned in ("command", "argv", "script", "code", "shell", "run", "exec"):
            require(banned not in item, f"handle carries {banned!r}; only data handles are accepted")
        handle = Handle.from_dict(item)
        require(
            handle.kind in tuple(Primitive),
            f"{handle.kind!r} is not an approved primitive",
        )
        require(handle.label not in known, f"handle {handle.label} was already discovered")
        require(all(handle.label != h.label for h in out), f"duplicate handle {handle.label}")
        out.append(handle)
    return out


def validate_change(raw: dict[str, Any]) -> tuple[str, str]:
    """`propose_change` → one canonical unified diff plus a non-authoritative summary.

    The summary is returned so it can be shown to a human, and is never
    consulted by the gate. Acceptance is decided by the counterfactual and the
    frozen judge; a persuasive summary attached to a patch that fails the gate
    changes nothing.
    """
    strict_fields(raw, ("diff", "summary"), "propose_change")
    diff = raw.get("diff")
    if not isinstance(diff, str) or diff.strip() == "":
        raise ValidationError("diff must be a non-empty string")
    summary = raw.get("summary", "")
    if not isinstance(summary, str):
        raise ValidationError("summary must be a string")
    require(len(summary) <= 2000, "summary is too long")
    for banned in ("confidence", "certainty"):
        require(banned not in raw, f"response carries {banned!r}; model confidence is not accepted")
    return canonical_diff(diff), summary[:2000]


__all__ = [
    "ENV_KEY",
    "ENV_MODEL",
    "ENV_URL",
    "MAX_RESPONSE_BYTES",
    "ModelReply",
    "ModelResponseInvalid",
    "ModelUnavailable",
    "ProviderConfig",
    "change_prompt",
    "extract_json",
    "handles_prompt",
    "hypotheses_prompt",
    "post_chat",
    "validate_change",
    "validate_handles",
    "validate_hypotheses",
]
