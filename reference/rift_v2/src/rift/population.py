"""Theory population, scoring, and representation-failure detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rift.hypothesis import description_length, execute
from rift.perception import TrackedTrace

# Declared in configs/benchmark.yaml BEFORE experiments; mirrored here.
LAMBDA_INTERVENTION = 1.0
LAMBDA_DL = 0.002
LAMBDA_PLANNING = 0.0  # declared 0: see DESIGN_LIMITS.md


@dataclass
class Evidence:
    """(step, target_track, outcome, interventional?) plus event log context."""

    events: list[tuple[int, str, str | None]] = field(default_factory=list)
    pushes: list[tuple[int, str, str]] = field(default_factory=list)
    interventional_steps: set[int] = field(default_factory=set)
    boundaries: set[int] = field(default_factory=lambda: {0})

    def extend_from_trace(self, tr: TrackedTrace, interventional: bool) -> None:
        off = (max((s for s, _, _ in self.events), default=0) + 5) if self.events else 0
        for e in tr.events:
            self.events.append((e.step + off, e.kind, e.role))
        for s, tid, out in tr.pushes:
            self.pushes.append((s + off, tid, out))
            if interventional:
                self.interventional_steps.add(s + off)
        self.events.sort()
        self.pushes.sort()


@dataclass
class Scored:
    hypothesis: dict[str, Any]
    binding: dict[str, str]
    status: str  # supported | contradicted | underdetermined
    j: float
    pred_loss: float
    interv_loss: float
    dl: int
    n_preds: int
    predictions: list[tuple[int, str]]


def score(h: dict[str, Any], binding: dict[str, str], ev: Evidence) -> Scored:
    res = execute(h, binding, ev.events, ev.pushes, ev.boundaries)
    pred_map = dict(res.predictions)
    errs = interr = npr = ninter = 0
    for s, tid, out in ev.pushes:
        if tid != binding[h["target_role"]]:
            continue
        p = pred_map.get(s)
        if p is None:
            continue
        npr += 1
        wrong = p != out
        errs += wrong
        if s in ev.interventional_steps:
            ninter += 1
            interr += wrong
    pred_loss = errs / npr if npr else 1.0
    interv_loss = interr / ninter if ninter else 0.0
    dl = description_length(h)
    j = pred_loss + LAMBDA_INTERVENTION * interv_loss + LAMBDA_DL * dl
    status = "underdetermined"
    if npr >= 1 and errs > 0:
        status = "contradicted"
    elif ninter >= 2 and interr == 0 and npr >= 3:
        status = "supported"
    return Scored(h, binding, status, j, pred_loss, interv_loss, dl, npr, res.predictions)


def equivalence_classes(scored: list[Scored]) -> list[list[Scored]]:
    groups: dict[tuple[tuple[int, str], ...], list[Scored]] = {}
    for s in scored:
        groups.setdefault(tuple(s.predictions), []).append(s)
    return list(groups.values())


@dataclass
class FailureSignal:
    trigger: str
    detail: dict[str, Any]


def detect_failures(trace: TrackedTrace, ev: Evidence, scored: list[Scored]) -> list[FailureSignal]:
    out: list[FailureSignal] = []
    # 1. observational aliasing: same auto-derived encoded state + same target
    #    push -> incompatible outcomes.  Signature = the set of currently
    #    absent tracks, replayed from generic events per episode segment.
    #    (auto-derived; not a hand-picked variable)
    bnds = sorted(ev.boundaries or {0})
    absent: set[str] = set()
    seg_i = 0
    sig_out: dict[tuple[object, ...], set[str]] = {}
    ei = 0
    evs = sorted(ev.events)
    for s, tid, o in sorted(ev.pushes):
        while seg_i + 1 < len(bnds) and s >= bnds[seg_i + 1]:
            seg_i += 1
            absent = set()
        while ei < len(evs) and evs[ei][0] < s:
            st_, kind, role = evs[ei]
            if kind == "disappeared" and role:
                absent.add(role)
            ei += 1
        key = (tid, seg_i if False else 0, frozenset(absent))
        sig_out.setdefault(key, set()).add(o)
    alias = [k for k, v in sig_out.items() if len(v) > 1]
    if alias:
        out.append(FailureSignal("state_aliasing", {"n_aliased_signatures": len(alias)}))
    # 2. all current theories contradicted
    live = [s for s in scored if s.status != "contradicted"]
    if scored and not live:
        out.append(FailureSignal("all_theories_contradicted", {"n": len(scored)}))
    # 3. consensus-but-wrong: every surviving theory made the same wrong pred
    wrongs = 0
    for s, tid, o in ev.pushes:
        preds = {dict(sc.predictions).get(s) for sc in live if dict(sc.predictions).get(s)}
        if preds and len(preds) == 1 and next(iter(preds)) != o:
            wrongs += 1
    if wrongs >= 2:
        out.append(FailureSignal("consensus_wrong", {"n_pushes": wrongs}))
    return out
