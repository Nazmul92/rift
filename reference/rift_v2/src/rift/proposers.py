"""Hypothesis proposers.

Grammar: bounded enumeration over the IR grammar (open-vocabulary in the sense
that no candidate list encodes environment answers; the space is the grammar).

LLM: live provider requiring ANTHROPIC_API_KEY. Receives ONLY public trace
data + IR schema. One schema-repair retry max. NO fallback to grammar.

Fake: deterministic fixtures for tests only.
"""

from __future__ import annotations

import itertools
import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from rift.hypothesis import IRValidationError, canonical, description_length, validate


@dataclass
class Proposal:
    hypothesis: dict[str, Any] | None
    provider: str
    valid: bool
    failure: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------- grammar search ----------------


def _bool_lat(name: str, ev: str, role: str, guard: dict[str, Any] | None = None) -> dict:
    d: dict[str, Any] = {
        "name": name,
        "type": "bool",
        "init": False,
        "set_on": {"event": ev, "role": role},
        "reset_on": None,
    }
    if guard is not None:
        d["guard"] = guard
    return d


def grammar_proposals(
    roles: list[str],
    target: str,
    *,
    budget: int = 4000,
    t_values: tuple[int, ...] = (4, 6, 8, 10, 12, 14, 16),
    k_values: tuple[int, ...] = (1, 2, 3, 4),
    periods: tuple[int, ...] = (6, 7, 8, 9),
) -> list[dict[str, Any]]:
    """Enumerate candidate hypotheses under a fixed budget. Object roles are the
    non-target roles; the grammar covers single-latent conditions, temporal
    conditions, and 2-way conjunctions (composition coverage)."""
    obj_roles = [r for r in roles if r != target]
    atoms: list[tuple[list[dict], dict]] = []  # (latents, condition)
    atoms.append(([], {"const": True}))
    atoms.append(([], {"const": False}))
    for tv in t_values:
        atoms.append(([], {"op": "ge_t", "value": tv}))
    for p in periods:
        for hi in (2, 3, 4):
            if hi < p:
                atoms.append(([], {"op": "window", "period": p, "lo": 0, "hi": hi}))
    i = 0
    for r in obj_roles:
        for ev in ("overlap", "disappeared", "adjacent_enter"):
            atoms.append(([_bool_lat(f"z{i}", ev, r)], {"var": f"z{i}"}))
            i += 1
        atoms.append(
            (
                [{"name": f"p{i}", "type": "parity", "toggle_on": {"event": "overlap", "role": r}}],
                {"op": "parity", "var": f"p{i}", "value": 1},
            )
        )
        i += 1
    for kv in k_values:
        atoms.append(
            (
                [
                    {
                        "name": f"k{i}",
                        "type": "counter",
                        "max": 16,
                        "inc_on": {"event": "blocked", "role": target},
                    }
                ],
                {"op": "ge", "var": f"k{i}", "value": kv},
            )
        )
        i += 1
    # ordered pairs: z_b set on overlap(r_b) guarded by z_a
    for ra, rb in itertools.permutations(obj_roles, 2):
        za, zb = f"z{i}", f"z{i + 1}"
        lats = [_bool_lat(za, "overlap", ra), _bool_lat(zb, "overlap", rb, guard={"var": za})]
        atoms.append((lats, {"var": zb}))
        i += 2
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def emit(lats: list[dict], cond: dict) -> None:
        if len(out) >= budget or len(lats) > 4:
            return
        h = {
            "hypothesis_id": f"g{len(out)}",
            "roles": roles,
            "target_role": target,
            "latents": lats,
            "condition": cond,
        }
        try:
            validate(h)
        except IRValidationError:
            return
        c = canonical(h)
        if c not in seen:
            seen.add(c)
            out.append(h)

    for lats, cond in atoms:
        emit(lats, cond)
    # conjunctions of two atoms (skip consts)
    real = [(la, c) for la, c in atoms if "const" not in c]
    for (l1, c1), (l2, c2) in itertools.combinations(real, 2):
        n1 = {x["name"] for x in l1}
        if any(x["name"] in n1 for x in l2):
            continue
        emit(l1 + l2, {"op": "and", "args": [c1, c2]})
        if len(out) >= budget:
            break
    return out


# ---------------- live LLM ----------------


class LiveLLMUnavailable(RuntimeError):
    pass


IR_SCHEMA_DOC = """You will receive a public interaction trace from an unknown
gridworld: rendered observations were reduced to anonymous components c0..cN,
generic events (overlap/disappeared/adjacent_enter/blocked/comp_moved/tick),
and push outcomes (pass/blocked) against a target component. Propose the top-k
DIVERSE executable hypotheses, as a JSON list, each following exactly this IR:
{"hypothesis_id": str, "roles": [...], "target_role": str,
 "latents": [ {"name","type":"bool","init",false,"set_on":EVENT,"reset_on":null,"guard":PRED?}
            | {"name","type":"counter","max":int,"inc_on":EVENT}
            | {"name","type":"parity","toggle_on":EVENT} ],
 "condition": PRED}
PRED := {"var":n} | {"op":"ge","var":n,"value":int} | {"op":"parity","var":n,"value":0|1}
      | {"op":"window","period":int,"lo":int,"hi":int} | {"op":"ge_t","value":int}
      | {"op":"and"|"or","args":[P,P]} | {"op":"not","arg":P} | {"const":bool}
EVENT := {"event":kind,"role":r|null}. Output ONLY the JSON list."""


def llm_proposals(
    trace_summary: dict[str, Any],
    roles: list[str],
    target: str,
    k: int,
    model: str = "claude-sonnet-4-6",
) -> list[Proposal]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LiveLLMUnavailable("NOT_RUN_LIVE_LLM: no credentials")
    banned = ("family", "token", "door", "key", "switch", "gated", "oracle", "barrier")
    ser = json.dumps(trace_summary)
    for b in banned:
        if b in ser:
            raise RuntimeError(f"leakage guard: banned term '{b}' in prompt payload")
    prompt = (
        f"{IR_SCHEMA_DOC}\n\nRoles available: {roles}; target_role: {target}.\n"
        f"Public trace:\n{ser}\n\nReturn a JSON list of {k} hypotheses."
    )
    out: list[Proposal] = []
    body = {"model": model, "max_tokens": 3000, "messages": [{"role": "user", "content": prompt}]}
    t0 = time.time()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    text = "".join(b.get("text", "") for b in data.get("content", []))
    latency = time.time() - t0
    repaired = False
    for attempt in range(2):
        try:
            arr = json.loads(text.strip().strip("`").removeprefix("json"))
            break
        except json.JSONDecodeError:
            if attempt == 1:
                return [
                    Proposal(
                        None,
                        "llm",
                        False,
                        "parse_failure",
                        {"latency": latency, "repaired": repaired},
                    )
                ]
            repaired = True
            # single allowed schema-repair request
            msgs = body["messages"]
            assert isinstance(msgs, list)
            msgs.append({"role": "assistant", "content": text})
            msgs.append(
                {"role": "user", "content": "Malformed JSON. Reply with ONLY the JSON list."}
            )
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(body).encode(),
                headers={
                    "content-type": "application/json",
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            text = "".join(b.get("text", "") for b in data.get("content", []))
    usage = data.get("usage", {})
    for h in arr if isinstance(arr, list) else []:
        try:
            validate(h)
            out.append(
                Proposal(
                    h,
                    "llm",
                    True,
                    meta={
                        "model": model,
                        "latency": latency,
                        "tokens": usage,
                        "repaired": repaired,
                        "dl": description_length(h),
                    },
                )
            )
        except (IRValidationError, TypeError, KeyError) as e:
            out.append(
                Proposal(
                    None, "llm", False, f"invalid:{e}", meta={"model": model, "repaired": repaired}
                )
            )
    return out


# ---------------- fake provider (fixtures only) ----------------


def fake_proposals(fixture: list[dict[str, Any]]) -> list[Proposal]:
    out = []
    for h in fixture:
        try:
            validate(h)
            out.append(Proposal(h, "fake", True, meta={"fixture": True}))
        except IRValidationError as e:
            out.append(Proposal(None, "fake", False, str(e), meta={"fixture": True}))
    return out
