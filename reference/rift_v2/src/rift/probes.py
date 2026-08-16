"""Probe generation and selection.

Candidate probes are macro action-sequences generated ONLY from public
observations: visit each visible component, wait k, push each barrier
candidate n times, and compositions. Every ablation policy receives the exact
same candidate list; every primitive action is charged against the budget.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from rift.contracts import PrimitiveAction
from rift.hypothesis import execute
from rift.perception import TrackedTrace, path_to
from rift.population import Scored


@dataclass(frozen=True)
class Probe:
    name: str
    actions: tuple[PrimitiveAction, ...]
    visits: tuple[str, ...]  # tracks visited (overlap attempts), in order
    pushes_target: str | None
    n_pushes: int
    reset: bool = False  # start a fresh episode first (reset intervention)
    wait: int = 0
    est_len: int = 0  # estimated primitive-action cost (for reset probes)


def _nav_grid(trace: TrackedTrace) -> tuple[tuple[int, ...], ...]:
    obs = trace.obs_log[-1]
    g = [list(r) for r in obs.grid]
    ay, ax = trace.agent_pos
    g[ay][ax] = 0
    return tuple(tuple(r) for r in g)


def generate_probes(
    trace: TrackedTrace, barrier_cands: list[str], waits: tuple[int, ...] = (0, 6, 12)
) -> list[Probe]:
    grid = _nav_grid(trace)
    start = trace.agent_pos
    visitable: dict[str, list[PrimitiveAction]] = {}
    for tid, t in trace.tracks.items():
        if tid == trace.agent_track or not t.present or len(t.cells) != 1:
            continue
        if tid in barrier_cands:
            continue
        p = path_to(grid, start, t.pos, stop_adjacent=False)
        if p is not None:
            visitable[tid] = p
    probes: list[Probe] = []
    for b in barrier_cands[:2]:
        bt = trace.tracks[b]
        goto_b = path_to(grid, start, bt.pos, stop_adjacent=True)
        if goto_b is None:
            continue
        push_dir = _push_dir(trace, b, goto_b)
        for w in waits:
            for n in (1, 3):
                acts = tuple(["S"] * w + goto_b + [push_dir] * n)
                probes.append(
                    Probe(f"wait{w}_push{n}_{b}", acts, (), b, n, wait=w, est_len=len(acts))
                )
                # reset intervention: fresh episode, wait, push without visits
                probes.append(
                    Probe(
                        f"reset_wait{w}_push{n}_{b}",
                        (),
                        (),
                        b,
                        n,
                        reset=True,
                        wait=w,
                        est_len=len(acts) + 4,
                    )
                )
        # visit one component then push
        for tid, p in visitable.items():
            t2 = trace.tracks[tid]
            g2 = [list(r) for r in grid]
            g2[t2.pos[0]][t2.pos[1]] = 0
            back = path_to(tuple(tuple(r) for r in g2), t2.pos, bt.pos, stop_adjacent=True)
            if back is None:
                continue
            probes.append(
                Probe(
                    f"visit_{tid}_push_{b}",
                    tuple(p + back + [push_dir]),
                    (tid,),
                    b,
                    1,
                    est_len=len(p) + len(back) + 1,
                )
            )
            probes.append(
                Probe(
                    f"reset_visit_{tid}_push_{b}",
                    (),
                    (tid,),
                    b,
                    1,
                    reset=True,
                    est_len=len(p) + len(back) + 5,
                )
            )
        # visit two components (both orders) then push — sequence coverage
        vk = list(visitable)
        for i in range(len(vk)):
            for j in range(len(vk)):
                if i == j:
                    continue
                a_, b_ = vk[i], vk[j]
                ta, tb2 = trace.tracks[a_], trace.tracks[b_]
                g2 = [list(r) for r in grid]
                g2[ta.pos[0]][ta.pos[1]] = 0
                g2[tb2.pos[0]][tb2.pos[1]] = 0
                gg = tuple(tuple(r) for r in g2)
                p1 = visitable[a_]
                p2 = path_to(gg, ta.pos, tb2.pos, stop_adjacent=False)
                p3 = path_to(gg, tb2.pos, bt.pos, stop_adjacent=True)
                if p2 is None or p3 is None:
                    continue
                probes.append(
                    Probe(
                        f"seq_{a_}_{b_}_push_{b}",
                        tuple(p1 + p2 + p3 + [push_dir]),
                        (a_, b_),
                        b,
                        1,
                        est_len=len(p1) + len(p2) + len(p3) + 1,
                    )
                )
                probes.append(
                    Probe(
                        f"reset_seq_{a_}_{b_}_push_{b}",
                        (),
                        (a_, b_),
                        b,
                        1,
                        reset=True,
                        est_len=len(p1) + len(p2) + len(p3) + 5,
                    )
                )
    return probes


def _push_dir(
    trace: TrackedTrace, tid: str, path_adjacent: list[PrimitiveAction]
) -> PrimitiveAction:
    # after walking adjacent, push toward the component
    t = trace.tracks[tid]
    grid = _nav_grid(trace)
    pos = trace.agent_pos
    for a in path_adjacent:
        from rift.contracts import DELTAS

        dy, dx = DELTAS[a]
        pos = (pos[0] + dy, pos[1] + dx)
    dy, dx = t.pos[0] - pos[0], t.pos[1] - pos[1]
    _ = grid
    if dy == 1:
        return "D"
    if dy == -1:
        return "U"
    if dx == 1:
        return "R"
    return "L"


# ---------------- prediction of probe outcomes per theory ----------------


def predict_probe(
    sc: Scored,
    probe: Probe,
    ev_events: list[tuple[int, str, str | None]],
    ev_pushes: list[tuple[int, str, str]],
    t0: int,
    boundaries: set[int] | frozenset[int] = frozenset({0}),
) -> str | None:
    """Simulate the theory's latent program over prior evidence PLUS the events
    the probe would generate (assuming visited components overlap), and read
    the predicted outcome of the probe's final push."""
    if probe.pushes_target is None:
        return None
    sim_events = list(ev_events)
    sim_pushes = list(ev_pushes)
    bset = set(boundaries) or {0}
    if probe.reset:
        bset = bset | {t0 + 1}
    length = probe.est_len or len(probe.actions)
    # visits generate overlap+disappeared events in order at spaced times
    vt = t0 + 1
    for tid in probe.visits:
        vt += max(2, length // (len(probe.visits) + 1))
        sim_events.append((vt, "overlap", tid))
        sim_events.append((vt, "disappeared", tid))
    push_t = t0 + max(length, 2)
    for n in range(probe.n_pushes):
        sim_pushes.append((push_t - (probe.n_pushes - 1 - n), probe.pushes_target, "?"))
    res = execute(sc.hypothesis, sc.binding, sorted(sim_events), sorted(sim_pushes), bset)
    preds = [p for st, p in res.predictions if st > t0]
    return preds[-1] if preds else None


def js_divergence(dists: list[dict[str, float]]) -> float:
    if len(dists) < 2:
        return 0.0
    keys = {k for d in dists for k in d}
    m = {k: sum(d.get(k, 0.0) for d in dists) / len(dists) for k in keys}

    def kl(p: dict[str, float], q: dict[str, float]) -> float:
        s = 0.0
        for k, pv in p.items():
            if pv > 0 and q.get(k, 0) > 0:
                s += pv * math.log(pv / q[k])
        return s

    return sum(kl(d, m) for d in dists) / len(dists)


def select_probe(
    policy: str,
    probes: list[Probe],
    live: list[Scored],
    ev_events: list[tuple[int, str, str | None]],
    ev_pushes: list[tuple[int, str, str]],
    t0: int,
    rng: random.Random,
    boundaries: set[int] | frozenset[int] = frozenset({0}),
) -> Probe:
    if not probes:
        raise ValueError("no probes")
    if policy == "random":
        return rng.choice(probes)
    if policy == "goal_greedy":
        # cheapest probe that any live theory predicts will pass
        best = None
        for p in sorted(probes, key=lambda x: x.est_len or len(x.actions)):
            for sc in live:
                if predict_probe(sc, p, ev_events, ev_pushes, t0, boundaries) == "pass":
                    return p
            if best is None:
                best = p
        return best or probes[0]
    if policy == "disagreement":
        best, best_score = probes[0], (-1.0, 0)
        for p in probes:
            raw = [predict_probe(sc, p, ev_events, ev_pushes, t0, boundaries) for sc in live]
            preds: list[str] = [x for x in raw if x is not None]
            if not preds:
                continue
            dists: list[dict[str, float]] = [{x: 1.0} for x in preds]
            d = js_divergence(dists)
            s = (d, -(p.est_len or len(p.actions)))
            if s > best_score:
                best, best_score = p, s
        if best_score[0] <= 0.0:
            return rng.choice(probes)
        return best
    raise ValueError(policy)
