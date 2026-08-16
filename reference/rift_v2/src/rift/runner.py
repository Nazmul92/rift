"""RIFT v2 controller and registered experiments E0-E5.

Evaluator-side code in this module MAY touch private environments and oracle
metadata, but only AFTER candidates are produced, and oracle data never flows
into proposal inputs (enforced by tests + prompt leakage guard).
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rift.environments.generator import make_env
from rift.environments.private_runtime import PrivateEnv
from rift.environments.public_env import OpaqueSession
from rift.hypothesis import execute
from rift.metrics import bootstrap_ci, classification_report, paired_diff_ci, summary
from rift.perception import Tracker, barrier_candidates, path_to
from rift.population import Evidence, Scored, detect_failures, score
from rift.probes import generate_probes, select_probe
from rift.proposers import grammar_proposals
from rift.schema import (
    BudgetExceeded,
    PortableCausalSchema,
    SessionRunner,
    bind_and_transfer,
    compile_schema,
)

RESULTS = Path(__file__).resolve().parents[2] / "results"


# ---------------------------------------------------------------- controller


@dataclass
class IdentifyOutcome:
    selected: Scored | None
    actions: int
    censored: bool
    goal: bool
    n_probes: int
    failures: list[str] = field(default_factory=list)
    live_classes: int = 0
    expanded: bool = False
    expansion_trigger: str | None = None


def observation_only(h: dict[str, Any]) -> bool:
    """True if every latent is decodable from the current frame (disappeared
    booleans, t-based conditions). History-only latents disqualify."""
    for la in h.get("latents", []):
        if la["type"] in ("counter", "parity"):
            return False
        if la["type"] == "bool" and la["set_on"]["event"] not in ("disappeared",):
            return False
        if la.get("guard") is not None:
            return False
    return True


def rift_identify(
    session: OpaqueSession,
    seed: int,
    *,
    policy: str,
    budget: int = 420,
    max_probes: int = 10,
    hypothesis_filter: str = "generated",
    rng_seed: int = 0,
) -> tuple[IdentifyOutcome, SessionRunner, list[Scored], dict[str, str]]:
    rng = random.Random(rng_seed)
    runner = SessionRunner(session, seed, budget)
    tr = runner.trace
    try:
        runner.run(["S"])
    except BudgetExceeded:
        pass
    cands = barrier_candidates(tr)
    if not cands:
        return IdentifyOutcome(None, runner.used, True, runner.terminated, 0), runner, [], {}
    target_track = cands[0]
    visit = sorted(
        tid
        for tid, t in tr.tracks.items()
        if tid != tr.agent_track and len(t.cells) == 1 and tid != target_track
    )[:4]
    roles = [f"r{i}" for i in range(len(visit))] + ["rB"]
    binding = {f"r{i}": v for i, v in enumerate(visit)}
    binding["rB"] = target_track
    full_pool = grammar_proposals(roles, "rB")
    obs_pool = [h for h in full_pool if observation_only(h)]
    # generated mode starts observation-only and EXPANDS the hypothesis space
    # when a representation-failure signal fires (aliasing-triggered invention)
    pool = obs_pool if hypothesis_filter in ("observation_only", "generated") else full_pool
    expanded = False
    expansion_trigger: str | None = None
    ev = collect_evidence(runner)
    scored = [score(h, binding, ev) for h in pool]
    failures: list[str] = []
    n_probes = 0
    censored = False
    try:
        for _ in range(max_probes):
            live = [s for s in scored if s.status != "contradicted"]
            fs = detect_failures(runner.trace, ev, scored)
            failures += [f.trigger for f in fs]
            if fs and hypothesis_filter == "generated" and not expanded:
                expanded = True
                expansion_trigger = fs[0].trigger
                pool = full_pool
                scored = [score(h, binding, ev) for h in pool]
                live = [s2 for s2 in scored if s2.status != "contradicted"]
            if runner.terminated:
                runner.new_episode()
            probes = generate_probes(runner.trace, [target_track])
            if not probes:
                break
            # identification requires exhausted PREDICTIVE disagreement: one
            # equivalence class over past predictions AND predicted outcomes
            # for every candidate probe.
            t0 = max((s for s, _, _ in ev.events), default=0)
            live_capped = sorted(live, key=lambda s2: (s2.j, s2.dl))[:120]
            fut_reps = _future_class_reps(live_capped, probes, ev, t0)
            if len(fut_reps) == 1 and fut_reps[0].n_preds >= 3:
                break
            probe = select_probe(
                policy, probes, fut_reps, ev.events, ev.pushes, t0, rng, boundaries=ev.boundaries
            )
            n_probes += 1
            if probe.reset:
                runner.new_episode()
            ep = runner.episode_index
            start = runner.trace.steps
            acts = (
                probe.actions
                if not probe.reset
                else _build_probe_actions(runner.trace, probe, target_track)
            )
            runner.run(acts)
            for s, _tid, _o in runner.trace.pushes:
                if s > start:
                    runner.interventional.add((ep, s))
            ev = collect_evidence(runner)
            scored = [score(s.hypothesis, binding, ev) for s in scored]
    except BudgetExceeded:
        censored = True
    live = [s for s in scored if s.status != "contradicted"]
    sel = _select(live)
    goal = runner.goal_ever or (runner.terminated and runner.reward > 0)
    if sel is not None and not goal and not censored:
        goal = _execute_goal(runner, sel, target_track, visit)
    out = IdentifyOutcome(
        sel, runner.used, censored, goal, n_probes, failures, len(_class_reps(live))
    )
    out.expanded = expanded
    out.expansion_trigger = expansion_trigger
    return out, runner, scored, binding


def _build_probe_actions(tr, probe, target_track: str) -> list[str]:
    """Concretize a reset probe (wait / visits / push) against the current
    fresh-episode state."""
    acts: list[str] = ["S"] * probe.wait
    obs = tr.obs_log[-1]
    g = [list(r) for r in obs.grid]
    pos = tr.agent_pos
    g[pos[0]][pos[1]] = 0
    for tid in probe.visits:
        t = tr.tracks.get(tid)
        if t is None or not t.present:
            continue
        gg = tuple(tuple(r) for r in g)
        p = path_to(gg, pos, t.pos, stop_adjacent=False)
        if p is None:
            continue
        acts += p
        g[t.pos[0]][t.pos[1]] = 0
        pos = t.pos
    bt = tr.tracks.get(target_track)
    if bt is None:
        return acts
    gg = tuple(tuple(r) for r in g)
    p = path_to(gg, pos, bt.pos, stop_adjacent=True)
    if p is None:
        return acts
    acts += p
    for a in p:
        from rift.contracts import DELTAS

        dy, dx = DELTAS[a]
        pos = (pos[0] + dy, pos[1] + dx)
    dy, dx = bt.pos[0] - pos[0], bt.pos[1] - pos[1]
    push = "D" if dy == 1 else "U" if dy == -1 else "R" if dx == 1 else "L"
    acts += [push] * probe.n_pushes
    return acts


def collect_evidence(runner: SessionRunner) -> Evidence:
    """Merge all episodes' generic events/pushes with step offsets; pushes made
    during probe execution are marked interventional."""
    ev = Evidence()
    ev.boundaries = set()
    off = 0
    traces = list(runner.archive) + [runner.trace]
    for ep, tr in enumerate(traces):
        ev.boundaries.add(off)
        for e in tr.events:
            ev.events.append((e.step + off, e.kind, e.role))
        for s, tid, out in tr.pushes:
            ev.pushes.append((s + off, tid, out))
            if (ep, s) in runner.interventional:
                ev.interventional_steps.add(s + off)
        off += (tr.steps or 0) + 5
    ev.events.sort()
    ev.pushes.sort()
    return ev


def _future_class_reps(reps: list[Scored], probes: list, ev: Evidence, t0: int) -> list[Scored]:
    from rift.probes import predict_probe

    groups: dict[tuple, Scored] = {}
    for s in sorted(reps, key=lambda s: (s.j, s.dl)):
        fut = tuple(predict_probe(s, p, ev.events, ev.pushes, t0, ev.boundaries) for p in probes)
        groups.setdefault((tuple(s.predictions), fut), s)
    return list(groups.values())[:40]


def _class_reps(live: list[Scored]) -> list[Scored]:
    groups: dict[tuple[tuple[int, str], ...], Scored] = {}
    for s in sorted(live, key=lambda s: (s.j, s.dl)):
        groups.setdefault(tuple(s.predictions), s)
    return list(groups.values())


def _identified(reps: list[Scored], ev: Evidence) -> bool:
    if len(reps) == 1:
        return reps[0].n_preds >= 3
    return False


def _select(live: list[Scored]) -> Scored | None:
    if not live:
        return None
    return min(live, key=lambda s: (s.j, s.dl))


def _execute_goal(runner: SessionRunner, sel: Scored, target: str, visit: list[str]) -> bool:
    """Plan with the selected theory: find the cheapest probe it predicts will
    pass, run it, then walk to newly reachable cells."""
    from rift.probes import predict_probe
    from rift.schema import _finish_goal

    try:
        tr = runner.trace
        probes = generate_probes(tr, [target])
        t0 = max((e.step for e in tr.events), default=0)
        ev = Evidence()
        ev.extend_from_trace(tr, interventional=False)
        for p in sorted(probes, key=lambda p: len(p.actions)):
            if predict_probe(sel, p, ev.events, ev.pushes, t0) == "pass":
                runner.run(p.actions)
                break
        _finish_goal(runner)
    except BudgetExceeded:
        return False
    return runner.terminated and runner.reward > 0


# ------------------------------------------------------- oracle battery (eval)

BATTERY = (
    "push2",
    "visit0_push",
    "visit1_push",
    "seq01_push",
    "seq10_push",
    "wait15_push",
    "push5",
    "visit0x2_push",
)


def battery_run(
    family: str, seed: int, hypothesis: dict[str, Any], role_to_obj: dict[str, int]
) -> tuple[int, int, list[tuple[str, str]]]:
    """Evaluator-only: drive a FRESH private instance through scripted
    interventions (using oracle coordinates), record generic event streams via
    the public render, and compare hypothesis predictions to actual outcomes.
    Returns (correct, total, [(pred, actual)])."""
    correct = total = 0
    pairs: list[tuple[str, str]] = []
    for scen in BATTERY:
        env = make_env(family, seed)
        cfg = env._cfg  # evaluator-only access
        session = OpaqueSession(env)
        runner = SessionRunner(session, seed, budget=200)
        tr = runner.trace
        try:
            runner.run(["S"])
        except BudgetExceeded:
            continue
        objs = [i for i, o in enumerate(cfg.objects) if o.kind != "npc"]
        track_of_obj: dict[int, str] = {}
        for i in objs:
            for tid, t in tr.tracks.items():
                if t.present and cfg.objects[i].pos in t.cells and tid != tr.agent_track:
                    track_of_obj[i] = tid
        barrier_tid = next(
            (tid for tid, t in tr.tracks.items() if (cfg.barrier_row, cfg.wall_col) in t.cells),
            None,
        )
        if barrier_tid is None:
            continue
        plan: list[tuple[str, int]] = []
        if scen == "push2":
            plan = [("push", 2)]
        elif scen == "visit0_push" and len(objs) >= 1:
            plan = [("visit", objs[0]), ("push", 1)]
        elif scen == "visit1_push" and len(objs) >= 2:
            plan = [("visit", objs[1]), ("push", 1)]
        elif scen == "seq01_push" and len(objs) >= 2:
            plan = [("visit", objs[0]), ("visit", objs[1]), ("push", 1)]
        elif scen == "seq10_push" and len(objs) >= 2:
            plan = [("visit", objs[1]), ("visit", objs[0]), ("push", 1)]
        elif scen == "wait15_push":
            plan = [("wait", 15), ("push", 1)]
        elif scen == "push5":
            plan = [("push", 5)]
        elif scen == "visit0x2_push" and len(objs) >= 1:
            plan = [("visit", objs[0]), ("bounce", 0), ("visit", objs[0]), ("push", 1)]
        if not plan:
            continue
        ok = _drive(runner, cfg, plan)
        if not ok:
            continue
        # map learned roles to this episode's tracks
        b: dict[str, str] = {}
        valid = True
        for role, oi in role_to_obj.items():
            if oi == -1:
                b[role] = barrier_tid
            elif oi in track_of_obj:
                b[role] = track_of_obj[oi]
            else:
                valid = False
        if not valid:
            continue
        res = execute(hypothesis, b, [(e.step, e.kind, e.role) for e in tr.events], tr.pushes)
        pm = dict(res.predictions)
        for s, tid, out in tr.pushes:
            if tid != barrier_tid or s not in pm:
                continue
            total += 1
            pairs.append((pm[s], out))
            correct += pm[s] == out
    return correct, total, pairs


def _drive(runner: SessionRunner, cfg: Any, plan: list[tuple[str, int]]) -> bool:
    tr = runner.trace
    try:
        for op, arg in plan:
            obs = tr.obs_log[-1]
            g = [list(r) for r in obs.grid]
            ay, ax = tr.agent_pos
            g[ay][ax] = 0
            gg = tuple(tuple(r) for r in g)
            if op == "wait":
                runner.run(["S"] * arg)
            elif op == "bounce":
                runner.run(["S"])
            elif op == "visit":
                pos = cfg.objects[arg].pos
                p = path_to(gg, (ay, ax), pos, stop_adjacent=False)
                if p is None:
                    return False
                runner.run(p)
            elif op == "push":
                bpos = (cfg.barrier_row, cfg.wall_col)
                p = path_to(gg, (ay, ax), bpos, stop_adjacent=True)
                if p is None:
                    return False
                runner.run(p)
                pos2 = tr.agent_pos
                dy, dx = bpos[0] - pos2[0], bpos[1] - pos2[1]
                a = "D" if dy == 1 else "U" if dy == -1 else "R" if dx == 1 else "L"
                for _ in range(arg):
                    runner.run([a])
    except BudgetExceeded:
        return False
    return True


def role_obj_map(binding: dict[str, str], runner: SessionRunner, env: PrivateEnv) -> dict[str, int]:
    """Evaluator-only: map learned roles -> object indices (-1 = barrier)."""
    cfg = env._cfg
    tr = runner.trace
    out: dict[str, int] = {}
    for role, tid in binding.items():
        t = tr.tracks.get(tid)
        if t is None:
            continue
        if (cfg.barrier_row, cfg.wall_col) in t.cells:
            out[role] = -1
            continue
        first_cells = t.cells if t.present else t.cells
        for i, ob in enumerate(cfg.objects):
            # match by initial position: use first frame components
            if ob.pos in first_cells or _first_pos(tr, tid) == ob.pos:
                out[role] = i
                break
    return out


def _first_pos(tr: Any, tid: str) -> tuple[int, int] | None:
    t = tr.tracks.get(tid)
    return t.pos if t else None


def oracle_hypothesis(family: str, env: PrivateEnv) -> tuple[dict[str, Any], dict[str, int]]:
    """Evaluator-only upper bound. Never enters proposal/selection pipelines."""
    cfg = env._cfg
    fams = family.split("+")
    lat: list[dict[str, Any]] = []
    conds: list[dict[str, Any]] = []
    role_obj: dict[str, int] = {"rB": -1}
    ri = 0

    def role_for(kind: str, order: int = -1) -> str:
        nonlocal ri
        for i, ob in enumerate(cfg.objects):
            if (
                ob.kind == kind
                and (order < 0 or ob.order_index == order)
                and i not in role_obj.values()
            ):
                r = f"r{ri}"
                ri += 1
                role_obj[r] = i
                return r
        raise ValueError("no obj")

    for fam in fams:
        if fam == "possession":
            r = role_for("consumable")
            lat.append(
                {
                    "name": f"z{ri}",
                    "type": "bool",
                    "init": False,
                    "set_on": {"event": "overlap", "role": r},
                    "reset_on": None,
                }
            )
            conds.append({"var": f"z{ri}"})
        elif fam == "timed" or fam == "decoy_correlated" or fam == "other_actor":
            v = cfg.timed_t if fam != "other_actor" else cfg.npc_open_step
            conds.append({"op": "ge_t", "value": v})
        elif fam == "attempt_counter":
            lat.append(
                {
                    "name": "k0",
                    "type": "counter",
                    "max": 16,
                    "inc_on": {"event": "blocked", "role": "rB"},
                }
            )
            conds.append({"op": "ge", "var": "k0", "value": max(1, cfg.counter_n - 1)})
        elif fam == "switch_parity":
            r = role_for("persistent")
            lat.append(
                {"name": "p0", "type": "parity", "toggle_on": {"event": "overlap", "role": r}}
            )
            conds.append({"op": "parity", "var": "p0", "value": 1})
        elif fam == "ordered_sequence":
            r0, r1 = role_for("consumable", 0), role_for("consumable", 1)
            lat.append(
                {
                    "name": "za",
                    "type": "bool",
                    "init": False,
                    "set_on": {"event": "overlap", "role": r0},
                    "reset_on": None,
                }
            )
            lat.append(
                {
                    "name": "zb",
                    "type": "bool",
                    "init": False,
                    "set_on": {"event": "overlap", "role": r1},
                    "guard": {"var": "za"},
                    "reset_on": None,
                }
            )
            conds.append({"var": "zb"})
        elif fam == "multi_resource":
            r0, r1 = role_for("consumable"), role_for("consumable")
            lat.append(
                {
                    "name": "m0",
                    "type": "bool",
                    "init": False,
                    "set_on": {"event": "overlap", "role": r0},
                    "reset_on": None,
                }
            )
            lat.append(
                {
                    "name": "m1",
                    "type": "bool",
                    "init": False,
                    "set_on": {"event": "overlap", "role": r1},
                    "reset_on": None,
                }
            )
            conds.append({"op": "and", "args": [{"var": "m0"}, {"var": "m1"}]})
        elif fam == "phase_window":
            conds.append(
                {"op": "window", "period": cfg.phase_period, "lo": cfg.phase_lo, "hi": cfg.phase_hi}
            )
        elif fam == "ungated":
            conds.append({"const": True})
    cond = conds[0]
    for c in conds[1:]:
        cond = {"op": "and", "args": [cond, c]}
    roles = list(role_obj)
    h = {
        "hypothesis_id": "oracle",
        "roles": roles,
        "target_role": "rB",
        "latents": lat,
        "condition": cond,
    }
    return h, role_obj


# ---------------------------------------------------------------- experiments

E1_FAMILIES = ("possession", "timed", "attempt_counter", "switch_parity", "multi_resource")
E2_FAMILIES = E1_FAMILIES + (
    "ordered_sequence",
    "phase_window",
    "decoy_correlated",
    "other_actor",
    "ungated",
)


def run_e1(pairs_per_family: int = 10) -> dict[str, Any]:
    rows = []
    for fam in E1_FAMILIES:
        for s in range(pairs_per_family):
            seed = s * 3 + 1  # held-out instantiation partition
            per: dict[str, Any] = {"family": fam, "seed": seed}
            for pol in ("disagreement", "random", "goal_greedy"):
                env = make_env(fam, seed)
                out, runner, _, binding = rift_identify(
                    OpaqueSession(env), seed, policy=pol, rng_seed=seed
                )
                ok = False
                if out.selected is not None:
                    ro = role_obj_map(binding, runner, env)
                    c, t, _ = battery_run(fam, seed, out.selected.hypothesis, ro)
                    ok = t > 0 and c == t
                per[pol] = {
                    "identified_correct": ok,
                    "actions": out.actions,
                    "censored": out.censored,
                    "goal": out.goal,
                    "n_probes": out.n_probes,
                }
            rows.append(per)
    agg: dict[str, Any] = {"rows": rows}
    for pol in ("disagreement", "random", "goal_greedy"):
        acts = [r[pol]["actions"] for r in rows]
        agg[pol] = {
            "identification_success": sum(r[pol]["identified_correct"] for r in rows),
            "n": len(rows),
            "goal_success": sum(r[pol]["goal"] for r in rows),
            "censored": sum(r[pol]["censored"] for r in rows),
            "actions": summary(acts) | {"ci95": bootstrap_ci(acts)},
        }
    agg["paired_actions_disagreement_vs_random"] = paired_diff_ci(
        [float(r["disagreement"]["actions"]) for r in rows],
        [float(r["random"]["actions"]) for r in rows],
    )
    agg["paired_success_disagreement_vs_random"] = paired_diff_ci(
        [float(r["disagreement"]["identified_correct"]) for r in rows],
        [float(r["random"]["identified_correct"]) for r in rows],
    )
    return agg


def run_e2(seeds_per_family: int = 6) -> dict[str, Any]:
    ks = (1, 3, 5, 10)
    rows = []
    for fam in E2_FAMILIES:
        for s in range(seeds_per_family):
            seed = s * 3 + 1
            env = make_env(fam, seed)
            session = OpaqueSession(env)
            runner = SessionRunner(session, seed, budget=420)
            tr = runner.trace
            try:
                runner.run(["S"])
            except BudgetExceeded:
                continue
            cands = barrier_candidates(tr)
            if not cands:
                continue
            target = cands[0]
            visit = [
                tid
                for tid, t in tr.tracks.items()
                if tid != tr.agent_track and t.present and len(t.cells) == 1 and tid not in cands
            ][:3]
            roles = [f"r{i}" for i in range(len(visit))] + ["rB"]
            binding = {f"r{i}": v for i, v in enumerate(visit)} | {"rB": target}
            # initial passive trace: one push, one blocked observation
            probes = generate_probes(tr, [target], waits=(0,))
            p0 = next((p for p in probes if p.visits == () and p.n_pushes == 1), None)
            if p0:
                try:
                    runner.run(p0.actions)
                except BudgetExceeded:
                    pass
            ev = Evidence()
            ev.extend_from_trace(tr, interventional=False)
            pool = grammar_proposals(roles, "rB")
            scored = sorted((score(h, binding, ev) for h in pool), key=lambda s: (s.j, s.dl))
            fs = detect_failures(tr, ev, scored)
            ro = role_obj_map(binding, runner, env)
            row: dict[str, Any] = {
                "family": fam,
                "seed": seed,
                "n_valid_proposals": len(pool),
                "failure_triggers": [f.trigger for f in fs],
            }
            live = [s for s in scored if s.status != "contradicted"] or scored
            for k in ks:
                hit = False
                for cand in live[:k]:
                    c, t, _ = battery_run(fam, seed, cand.hypothesis, ro)
                    if t > 0 and c == t:
                        hit = True
                        break
                row[f"recall@{k}"] = hit
            # selection success after probing (full loop)
            env2 = make_env(fam, seed)
            out2, runner2, _, b2 = rift_identify(
                OpaqueSession(env2), seed, policy="disagreement", rng_seed=seed
            )
            ok = False
            if out2.selected is not None:
                ro2 = role_obj_map(b2, runner2, env2)
                c, t, _ = battery_run(fam, seed, out2.selected.hypothesis, ro2)
                ok = t > 0 and c == t
            row["selection_success_after_probing"] = ok
            row["dl_selected"] = out2.selected.dl if out2.selected else None
            rows.append(row)
    agg: dict[str, Any] = {"rows": rows, "provider": "grammar", "live_llm": "NOT_RUN_LIVE_LLM"}
    n = len(rows)
    for k in ks:
        agg[f"recall@{k}"] = {"num": sum(r[f"recall@{k}"] for r in rows), "den": n}
    agg["selection_success_after_probing"] = {
        "num": sum(r["selection_success_after_probing"] for r in rows),
        "den": n,
    }
    return agg


def run_e3(seeds_per_family: int = 5) -> dict[str, Any]:
    rows = []
    for fam in E2_FAMILIES:
        if fam == "ungated":
            continue
        for s in range(seeds_per_family):
            seed = s * 3 + 1
            row: dict[str, Any] = {"family": fam, "seed": seed}
            for mode in ("observation_only", "generated"):
                env = make_env(fam, seed)
                out, runner, _, b = rift_identify(
                    OpaqueSession(env),
                    seed,
                    policy="disagreement",
                    hypothesis_filter=mode,
                    rng_seed=seed,
                )
                pairs: list[tuple[str, str]] = []
                if out.selected is not None:
                    ro = role_obj_map(b, runner, env)
                    _, _, pairs = battery_run(fam, seed, out.selected.hypothesis, ro)
                row[mode] = pairs
            env = make_env(fam, seed)
            oh, ro = oracle_hypothesis(fam, env)
            _, _, opairs = battery_run(fam, seed, oh, ro)
            row["oracle_upper_bound"] = opairs
            rows.append(row)
    agg: dict[str, Any] = {"n_tasks": len(rows)}
    for mode in ("observation_only", "generated", "oracle_upper_bound"):
        tp = fp = tn = fn = 0
        for r in rows:
            for pred, actual in r[mode]:
                if actual == "pass":
                    tp += pred == "pass"
                    fn += pred != "pass"
                else:
                    tn += pred == "blocked"
                    fp += pred != "blocked"
        agg[mode] = classification_report(tp, fp, tn, fn)
    agg["rows_saved"] = True
    return agg, rows  # type: ignore[return-value]


TRANSFER_SUITES: dict[str, list[tuple[str, str | None]]] = {
    "positive": [("possession", "held_out")],
    "near_miss": [("multi_resource", None), ("ordered_sequence", None), ("switch_parity", None)],
    "negative": [
        ("ungated", None),
        ("timed", None),
        ("decoy_correlated", None),
        ("other_actor", None),
    ],
}


def run_e4(n_schemas: int = 6, per_suite_seeds: int = 5) -> dict[str, Any]:
    # 1) learn possession schemas on development seeds
    schemas: list[PortableCausalSchema] = []
    learn_actions = []
    for s in range(n_schemas):
        seed = s * 3  # development partition
        env = make_env("possession", seed)
        out, runner, _, b = rift_identify(
            OpaqueSession(env), seed, policy="disagreement", rng_seed=seed
        )
        if out.selected is None:
            continue
        ro = role_obj_map(b, runner, env)
        c, t, _ = battery_run("possession", seed, out.selected.hypothesis, ro)
        if t > 0 and c == t:
            schemas.append(compile_schema(out.selected, coverage=t))
            learn_actions.append(out.actions)
    if not schemas:
        return {"error": "no schema compiled"}
    pcs = schemas[0]
    suites: dict[str, Any] = {
        "n_schemas_compiled": len(schemas),
        "learning_actions": summary([float(x) for x in learn_actions]),
    }
    for suite, fams in TRANSFER_SUITES.items():
        rows = []
        for fam, _tag in fams:
            for s in range(per_suite_seeds):
                seed = s * 3 + 1  # held-out instantiations
                # RIFT schema transfer
                env = make_env(fam, seed)
                runner = SessionRunner(OpaqueSession(env), seed, budget=300)
                br = bind_and_transfer(pcs, runner, random.Random(seed))
                # full relearn baseline
                env2 = make_env(fam, seed)
                out2, _, _, _ = rift_identify(
                    OpaqueSession(env2), seed, policy="disagreement", rng_seed=seed
                )
                # literal replay baseline (record dev trajectory once)
                replay_ok, replay_acts = _replay_baseline(fam, seed)
                # appearance retrieval baseline
                app_ok = _appearance_baseline(fam, seed)
                # no-memory random baseline
                nm_ok = _random_baseline(fam, seed)
                rows.append(
                    {
                        "family": fam,
                        "seed": seed,
                        "decision": br.decision,
                        "goal": br.goal_reached,
                        "actions": br.actions_used,
                        "pred_acc": br.prediction_correct / br.prediction_total,
                        "relearn_goal": out2.goal,
                        "relearn_actions": out2.actions,
                        "replay_goal": replay_ok,
                        "replay_actions": replay_acts,
                        "appearance_goal": app_ok,
                        "random_goal": nm_ok,
                    }
                )
        n = len(rows)
        expected_bind = suite == "positive"
        suites[suite] = {
            "n": n,
            "true_binding_rate" if expected_bind else "false_binding_rate": sum(
                r["decision"] == "bound" for r in rows
            )
            / n,
            "underdetermined_rate": sum(r["decision"] == "underdetermined" for r in rows) / n,
            "rejected_rate": sum(r["decision"] == "rejected" for r in rows) / n,
            "goal_success": sum(r["goal"] for r in rows),
            "actions": summary([float(str(r["actions"])) for r in rows]),
            "pred_acc_mean": summary([float(str(r["pred_acc"])) for r in rows])["mean"],
            "relearn": {
                "goal": sum(r["relearn_goal"] for r in rows),
                "actions": summary([float(str(r["relearn_actions"])) for r in rows]),
            },
            "replay_goal": sum(r["replay_goal"] for r in rows),
            "appearance_goal": sum(r["appearance_goal"] for r in rows),
            "random_goal": sum(r["random_goal"] for r in rows),
            "rows": rows,
        }
    return suites


_REPLAY_CACHE: dict[str, list[str]] = {}


def _replay_baseline(fam: str, seed: int) -> tuple[bool, int]:
    """Replay the literal successful action sequence recorded on a dev
    possession instance."""
    if "acts" not in _REPLAY_CACHE:
        dev = make_env("possession", 0)
        out, runner, _, _ = rift_identify(OpaqueSession(dev), 0, policy="disagreement", rng_seed=0)
        # literal trajectory = the final episode's primitive actions
        _REPLAY_CACHE["acts"] = list(runner.trace.actions) if out.goal else []
    acts = _REPLAY_CACHE["acts"]
    env = make_env(fam, seed)
    session = OpaqueSession(env)
    session.reset(seed)
    used = 0
    reward = 0.0
    for a in acts[:300]:
        r = session.step(a)
        used += 1
        reward += r.reward
        if r.terminated:
            break
    return reward > 0, used


def _appearance_baseline(fam: str, seed: int) -> bool:
    """Bind roles by matching stored colours from the dev instance; colours are
    reshuffled per instance, so this tests appearance vs structure."""
    dev = make_env("possession", 0)
    session = OpaqueSession(dev)
    tr = Tracker()
    tr.observe_first(session.reset(0))
    dev_colors = sorted({t.color for t in tr.trace.tracks.values()})
    env = make_env(fam, seed)
    s2 = OpaqueSession(env)
    tr2 = Tracker()
    tr2.observe_first(s2.reset(seed))
    new_colors = sorted({t.color for t in tr2.trace.tracks.values()})
    # appearance match succeeds only if colour sets coincide (they won't,
    # instance palettes are shuffled); then it would replay coordinates.
    if dev_colors != new_colors:
        return False
    return _replay_baseline(fam, seed)[0]


def _random_baseline(fam: str, seed: int, budget: int = 300) -> bool:
    env = make_env(fam, seed)
    session = OpaqueSession(env)
    session.reset(seed)
    rng = random.Random(seed)
    reward = 0.0
    for _ in range(budget):
        r = session.step(rng.choice(("L", "R", "U", "D", "S")))
        reward += r.reward
        if r.terminated:
            break
    return reward > 0


def run_e5(n_rounds: int = 10) -> dict[str, Any]:
    sequence = [
        "possession",
        "timed",
        "possession",
        "switch_parity",
        "possession",
        "multi_resource",
        "possession",
        "ungated",
        "possession",
        "possession",
    ][:n_rounds]
    library: list[PortableCausalSchema] = []
    rows = []
    for i, fam in enumerate(sequence):
        seed = i * 3 + 1
        used_schema = False
        actions = 0
        goal = False
        decision = None
        for pcs in library:
            env = make_env(fam, seed)
            runner = SessionRunner(OpaqueSession(env), seed, budget=300)
            br = bind_and_transfer(pcs, runner, random.Random(seed))
            actions += br.actions_used
            decision = br.decision
            if br.decision == "bound" and br.goal_reached:
                used_schema, goal = True, True
                break
        if not goal:
            env = make_env(fam, seed)
            out, runner, _, b = rift_identify(
                OpaqueSession(env), seed, policy="disagreement", rng_seed=seed
            )
            actions += out.actions
            goal = out.goal
            if out.selected is not None:
                ro = role_obj_map(b, runner, env)
                c, t, _ = battery_run(fam, seed, out.selected.hypothesis, ro)
                if (
                    t > 0
                    and c == t
                    and not any(
                        s.latent_program == compile_schema(out.selected, t).latent_program
                        for s in library
                    )
                ):
                    library.append(compile_schema(out.selected, t))
        rows.append(
            {
                "round": i,
                "family": fam,
                "actions": actions,
                "goal": goal,
                "via_schema": used_schema,
                "binding_decision": decision,
                "library_size": len(library),
            }
        )
    poss = [r for r in rows if r["family"] == "possession"]
    return {
        "rows": rows,
        "possession_actions_by_round": [(int(str(r["round"])), r["actions"]) for r in poss],
        "schema_reuse_count": sum(r["via_schema"] for r in rows),
        "final_library_size": len(library),
    }


# ---------------------------------------------------------------- main


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    t0 = time.time()
    report: dict[str, Any] = {
        "config": {
            "lambda": {"intervention": 1.0, "dl": 0.002, "planning": 0.0},
            "budget_actions": 420,
            "max_probes": 10,
            "partitions": "dev: seed%3==0, held-out instantiation: seed%3==1",
        }
    }
    print("E1 ...", flush=True)
    report["E1"] = run_e1()
    print(f"E1 done {time.time() - t0:.0f}s", flush=True)
    print("E2 ...", flush=True)
    report["E2"] = run_e2()
    print(f"E2 done {time.time() - t0:.0f}s", flush=True)
    print("E3 ...", flush=True)
    e3, e3rows = run_e3()  # type: ignore[misc]
    report["E3"] = e3
    print(f"E3 done {time.time() - t0:.0f}s", flush=True)
    print("E4 ...", flush=True)
    report["E4"] = run_e4()
    print(f"E4 done {time.time() - t0:.0f}s", flush=True)
    print("E5 ...", flush=True)
    report["E5"] = run_e5()
    report["wall_clock_s"] = time.time() - t0
    (RESULTS / "aggregate.json").write_text(json.dumps(report, indent=2, default=str))
    with (RESULTS / "e3_rows.jsonl").open("w") as f:
        for r in e3rows:
            f.write(json.dumps(r, default=str) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "E4"}, indent=2, default=str)[:4000])


if __name__ == "__main__":
    main()
