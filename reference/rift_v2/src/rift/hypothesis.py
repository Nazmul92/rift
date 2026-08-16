"""Executable hypothesis IR and runtime.

Hypotheses are JSON-shaped dicts, validated before execution, executed
deterministically over generic event traces, side-effect free, depth- and
operation-bounded. No arbitrary Python ever executes.

IR shape
--------
{
  "hypothesis_id": str,
  "roles": ["r0", ...],                    # anonymous role slots
  "target_role": "rB",                     # the pushed component this predicts
  "latents": [
     {"name":"z0","type":"bool","init":false,
      "set_on":{"event":"overlap","role":"r0"},
      "guard": PRED | null,                # optional: set only if PRED holds
      "reset_on": EVENT | null},
     {"name":"k0","type":"counter","max":8,"inc_on":{"event":"blocked","role":"rB"}},
     {"name":"p0","type":"parity","toggle_on":{"event":"overlap","role":"r0"}}
  ],
  "condition": PRED                        # push passes iff PRED
}

PRED := {"var": name}
      | {"op":"ge","var":counter,"value":int}
      | {"op":"parity","var":parity,"value":0|1}
      | {"op":"window","period":int,"lo":int,"hi":int}     # over step index t
      | {"op":"ge_t","value":int}                          # t >= value
      | {"op":"and"|"or","args":[PRED,PRED]} | {"op":"not","arg":PRED}
      | {"const": true|false}

EVENT := {"event": kind, "role": role|null}  kind in EVENT_KINDS
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Domain vocabularies register their own event kinds; the IR itself is
# domain-agnostic (gridworld: overlap/disappeared/...; code: applied/run/...).
EVENT_KINDS: set[str] = {
    "overlap",
    "disappeared",
    "adjacent_enter",
    "blocked",
    "comp_moved",
    "tick",
}


def register_event_kinds(*kinds: str) -> None:
    EVENT_KINDS.update(kinds)


MAX_DEPTH = 4
MAX_OPS = 32
MAX_LATENTS = 4
MAX_COUNTER = 16


class IRValidationError(ValueError):
    pass


def _pred_ops(p: Any, depth: int = 0) -> int:
    if depth > MAX_DEPTH:
        raise IRValidationError("depth")
    if not isinstance(p, dict):
        raise IRValidationError("pred type")
    if "const" in p:
        if not isinstance(p["const"], bool):
            raise IRValidationError("const")
        return 1
    if "var" in p and "op" not in p:
        return 1
    op = p.get("op")
    if op == "ge":
        if not isinstance(p.get("value"), int) or not isinstance(p.get("var"), str):
            raise IRValidationError("ge")
        return 1
    if op == "parity":
        if p.get("value") not in (0, 1):
            raise IRValidationError("parity")
        return 1
    if op == "window":
        if not all(isinstance(p.get(k), int) for k in ("period", "lo", "hi")):
            raise IRValidationError("window")
        if not (1 <= p["period"] <= 32 and 0 <= p["lo"] < p["hi"] <= p["period"]):
            raise IRValidationError("window range")
        return 1
    if op == "ge_t":
        if not isinstance(p.get("value"), int) or not (0 <= p["value"] <= 200):
            raise IRValidationError("ge_t")
        return 1
    if op in ("and", "or"):
        args = p.get("args")
        if not isinstance(args, list) or len(args) != 2:
            raise IRValidationError("args")
        return 1 + sum(_pred_ops(a, depth + 1) for a in args)
    if op == "not":
        return 1 + _pred_ops(p.get("arg"), depth + 1)
    raise IRValidationError(f"op {op}")


def _event_ok(e: Any, roles: list[str]) -> None:
    if not isinstance(e, dict) or e.get("event") not in EVENT_KINDS:
        raise IRValidationError("event")
    r = e.get("role")
    if r is not None and r not in roles:
        raise IRValidationError("event role")


def validate(h: dict[str, Any]) -> None:
    if not isinstance(h.get("hypothesis_id"), str):
        raise IRValidationError("id")
    roles = h.get("roles")
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        raise IRValidationError("roles")
    if h.get("target_role") not in roles:
        raise IRValidationError("target_role")
    lats = h.get("latents", [])
    if not isinstance(lats, list) or len(lats) > MAX_LATENTS:
        raise IRValidationError("latents")
    names = set()
    ops = 0
    for la in lats:
        n, ty = la.get("name"), la.get("type")
        if not isinstance(n, str) or n in names:
            raise IRValidationError("latent name")
        names.add(n)
        if ty == "bool":
            _event_ok(la.get("set_on"), roles)
            if la.get("reset_on") is not None:
                _event_ok(la["reset_on"], roles)
            if la.get("guard") is not None:
                ops += _pred_ops(la["guard"])
        elif ty == "counter":
            _event_ok(la.get("inc_on"), roles)
            if not isinstance(la.get("max"), int) or not (1 <= la["max"] <= MAX_COUNTER):
                raise IRValidationError("counter max")
        elif ty == "parity":
            _event_ok(la.get("toggle_on"), roles)
        else:
            raise IRValidationError("latent type")
        ops += 1
    ops += _pred_ops(h.get("condition"))
    if ops > MAX_OPS:
        raise IRValidationError("ops")


def description_length(h: dict[str, Any]) -> int:
    """Deterministic complexity: canonical JSON node count."""

    def nodes(x: Any) -> int:
        if isinstance(x, dict):
            return 1 + sum(nodes(v) for v in x.values())
        if isinstance(x, list):
            return 1 + sum(nodes(v) for v in x)
        return 1

    return nodes({k: h[k] for k in ("roles", "latents", "condition") if k in h})


# ---------------- execution ----------------


@dataclass
class ExecResult:
    predictions: list[tuple[int, str]]  # (push step, "pass"/"blocked")


def _eval_pred(p: dict[str, Any], state: dict[str, int], t: int) -> bool:
    if "const" in p:
        return bool(p["const"])
    if "var" in p and "op" not in p:
        return bool(state.get(p["var"], 0))
    op = p["op"]
    if op == "ge":
        return state.get(p["var"], 0) >= p["value"]
    if op == "parity":
        return state.get(p["var"], 0) % 2 == p["value"]
    if op == "window":
        return p["lo"] <= (t % p["period"]) < p["hi"]
    if op == "ge_t":
        return t >= p["value"]
    if op == "and":
        return _eval_pred(p["args"][0], state, t) and _eval_pred(p["args"][1], state, t)
    if op == "or":
        return _eval_pred(p["args"][0], state, t) or _eval_pred(p["args"][1], state, t)
    if op == "not":
        return not _eval_pred(p["arg"], state, t)
    raise IRValidationError(op)


def _init_state(h: dict[str, Any]) -> dict[str, int]:
    return {
        la["name"]: 1 if (la["type"] == "bool" and la.get("init")) else 0
        for la in h.get("latents", [])
    }


def execute(
    h: dict[str, Any],
    binding: dict[str, str],
    events: list[tuple[int, str, str | None]],
    pushes: list[tuple[int, str, str]],
    boundaries: frozenset[int] | set[int] = frozenset({0}),
) -> ExecResult:
    """Run the latent program over the event stream and predict every push
    into the track bound to target_role. Deterministic, side-effect free.
    `boundaries` are episode-start offsets: latent state resets there, and
    time-based predicates use the local (per-episode) step index."""
    inv = {v: k for k, v in binding.items()}  # track -> role
    state: dict[str, int] = _init_state(h)
    bnds = sorted(boundaries or {0})
    seg_i = 0
    target_track = binding[h["target_role"]]
    preds: list[tuple[int, str]] = []
    push_at: dict[int, list[str]] = {}
    for st, tid, _out in pushes:
        push_at.setdefault(st, []).append(tid)
    ei = 0
    all_steps = sorted({s for s, _, _ in events} | set(push_at))
    for t in all_steps:
        while seg_i + 1 < len(bnds) and t >= bnds[seg_i + 1]:
            seg_i += 1
            state = _init_state(h)
        local_t = t - bnds[seg_i]
        # predictions are made against state BEFORE this step's events resolve,
        # except blocked/overlap events caused by the push itself, which are the
        # outcome, not the cause. We evaluate condition at push time using state
        # accumulated strictly before step t, plus same-step non-push events.
        pre_events = []
        while ei < len(events) and events[ei][0] <= t:
            pre_events.append(events[ei])
            ei += 1
        # split same-step: apply events from steps < t first
        for s, kind, role in pre_events:
            if s < t:
                _apply(h, state, inv, kind, role)
        if t in push_at:
            for tid in push_at[t]:
                if tid == target_track:
                    ok = _eval_pred(h["condition"], state, local_t)
                    preds.append((t, "pass" if ok else "blocked"))
        for s, kind, role in pre_events:
            if s == t:
                _apply(h, state, inv, kind, role)
    return ExecResult(preds)


def _apply(
    h: dict[str, Any], state: dict[str, int], inv: dict[str, str], kind: str, role: str | None
) -> None:
    r = inv.get(role) if role else None
    for la in h.get("latents", []):
        ty = la["type"]
        if ty == "bool":
            e = la["set_on"]
            if e["event"] == kind and (e.get("role") is None or e.get("role") == r):
                g = la.get("guard")
                if g is None or _eval_pred(g, state, 0):
                    state[la["name"]] = 1
            rs = la.get("reset_on")
            if rs and rs["event"] == kind and (rs.get("role") is None or rs.get("role") == r):
                state[la["name"]] = 0
        elif ty == "counter":
            e = la["inc_on"]
            if e["event"] == kind and (e.get("role") is None or e.get("role") == r):
                state[la["name"]] = min(la["max"], state.get(la["name"], 0) + 1)
        elif ty == "parity":
            e = la["toggle_on"]
            if e["event"] == kind and (e.get("role") is None or e.get("role") == r):
                state[la["name"]] = state.get(la["name"], 0) + 1


def canonical(h: dict[str, Any]) -> str:
    return json.dumps({k: h[k] for k in sorted(h) if k != "hypothesis_id"}, sort_keys=True)


def behaviourally_equivalent(
    preds_a: list[tuple[int, str]], preds_b: list[tuple[int, str]]
) -> bool:
    return preds_a == preds_b
