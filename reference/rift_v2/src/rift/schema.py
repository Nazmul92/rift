"""Portable Causal Schema compilation and observation-only role binding.

A schema is compiled FROM the surviving learned theory. It stores role slots,
grounding constraints, latent program, identification probes and uncertainty —
never coordinates, colours, concrete track ids, or literal trajectories.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from rift.contracts import PrimitiveAction
from rift.perception import TrackedTrace, Tracker, barrier_candidates, path_to
from rift.population import Evidence, Scored, score
from rift.probes import Probe


@dataclass
class PortableCausalSchema:
    schema_id: str
    roles: list[str]
    role_constraints: dict[str, str]  # role -> generic grounding constraint
    latent_program: dict[str, Any]  # the theory IR (role-indexed, no track ids)
    identification: str  # description of the probe strategy
    initiation: str
    termination: str
    counterexamples: list[str] = field(default_factory=list)
    validated_on: list[str] = field(default_factory=list)
    coverage: int = 0
    uncertainty: float = 1.0


def compile_schema(sc: Scored, coverage: int) -> PortableCausalSchema:
    h = sc.hypothesis
    constraints = {}
    for r in h["roles"]:
        if r == h["target_role"]:
            constraints[r] = "blocking component whose removal expands reachability"
        else:
            constraints[r] = "reachable non-blocking component"
    return PortableCausalSchema(
        schema_id=f"pcs_{h['hypothesis_id']}",
        roles=list(h["roles"]),
        role_constraints=constraints,
        latent_program={k: h[k] for k in ("roles", "target_role", "latents", "condition")},
        identification="push target without latent precondition, then satisfy "
        "candidate latent update events and push again",
        initiation="target bound and unresolved passage needed",
        termination="target passed or binding rejected",
        coverage=coverage,
        uncertainty=max(0.0, 1.0 - coverage / 10.0),
    )


@dataclass
class BindingResult:
    decision: str  # bound | rejected | underdetermined
    binding: dict[str, str] | None
    actions_used: int
    goal_reached: bool
    prediction_correct: int
    prediction_total: int


class BudgetExceeded(RuntimeError):
    pass


class SessionRunner:
    """Utility to run action sequences against a public session with a hard
    budget; every primitive action (nav, wait, failed move) is charged."""

    def __init__(self, session: Any, seed: int, budget: int):
        self.session = session
        self.seed = seed
        self.tracker = Tracker()
        obs = session.reset(seed)
        self.tracker.observe_first(obs)
        self.budget = budget
        self.used = 0
        self.reward = 0.0
        self.terminated = False
        self.goal_ever = False
        self.archive: list[TrackedTrace] = []
        # interventional push markers: (episode_index, step) pairs
        self.interventional: set[tuple[int, int]] = set()
        self._calibrate()

    @property
    def episode_index(self) -> int:
        return len(self.archive)

    def new_episode(self) -> None:
        """Reset the SAME instance (same seed) for another evidence-gathering
        episode. Calibration actions are charged to the budget."""
        self.goal_ever = self.goal_ever or (self.terminated and self.reward > 0)
        self.archive.append(self.tracker.trace)
        self.tracker = Tracker()
        obs = self.session.reset(self.seed)
        self.tracker.observe_first(obs)
        self.terminated = False
        self.reward = 0.0
        self._calibrate()

    def _calibrate(self) -> None:
        for a in ("R", "L", "D", "U"):
            if self.tracker.trace.agent_track:
                break
            before = self.tracker.trace.obs_log[-1]
            res = self._raw_step(a)
            self.tracker.identify_self(before, a, res.observation)
            self.tracker.update(a, res.observation)

    def _raw_step(self, a: PrimitiveAction) -> Any:
        if self.used >= self.budget:
            raise BudgetExceeded()
        self.used += 1
        res = self.session.step(a)
        self.reward += res.reward
        self.terminated = self.terminated or res.terminated
        return res

    def run(self, actions: tuple[PrimitiveAction, ...] | list[PrimitiveAction]) -> None:
        for a in actions:
            if self.terminated:
                return
            res = self._raw_step(a)
            self.tracker.update(a, res.observation)

    @property
    def trace(self) -> TrackedTrace:
        return self.tracker.trace


def bind_and_transfer(
    pcs: PortableCausalSchema, runner: SessionRunner, rng: random.Random, min_support: int = 2
) -> BindingResult:
    """Observation-only binding via reset interventions.

    Probe A: fresh episode, bare push (no latent satisfaction).
    Probe B (per candidate binding): fresh episode, visit the binding's object
    roles, push. Each candidate binding is scored on the accumulated evidence;
    bound requires an uncontradicted binding with interventional support,
    rejected requires every candidate binding contradicted, else
    underdetermined. The transferred policy executes only after support.
    """
    tr = runner.trace
    try:
        runner.run(["S"])
    except BudgetExceeded:
        return BindingResult("underdetermined", None, runner.used, runner.goal_ever, 0, 1)
    cands = barrier_candidates(tr)
    h = pcs.latent_program
    obj_roles = [r for r in h["roles"] if r != h["target_role"]]
    if not cands:
        return BindingResult("underdetermined", None, runner.used, runner.goal_ever, 0, 1)
    target_track = cands[0]
    visitable = sorted(
        tid
        for tid, t in tr.tracks.items()
        if tid != tr.agent_track and len(t.cells) == 1 and tid != target_track
    )
    import itertools

    bindings: list[dict[str, str]] = []
    if obj_roles:
        for perm in itertools.permutations(visitable, min(len(obj_roles), len(visitable))):
            if len(perm) < len(obj_roles):
                break
            b = {h["target_role"]: target_track}
            b.update(dict(zip(obj_roles, perm)))
            bindings.append(b)
    else:
        bindings = [{h["target_role"]: target_track}]
    if not bindings:
        return BindingResult("underdetermined", None, runner.used, runner.goal_ever, 0, 1)

    def probe_reset(visits: tuple[str, ...]) -> None:
        from rift.probes import Probe
        from rift.runner import _build_probe_actions

        runner.new_episode()
        pr = Probe("bind_probe", (), visits, target_track, 1, reset=True)
        acts = _build_probe_actions(runner.trace, pr, target_track)
        ep = runner.episode_index
        start = runner.trace.steps
        runner.run(acts)
        for s2, _t, _o in runner.trace.pushes:
            if s2 > start:
                runner.interventional.add((ep, s2))

    def evidence() -> Evidence:
        from rift.runner import collect_evidence

        return collect_evidence(runner)

    decision = "underdetermined"
    chosen: dict[str, str] | None = None
    correct = total = 0
    try:
        probe_reset(())
        ev = evidence()
        scored = [(b, score(dict(h, hypothesis_id="bind"), b, ev)) for b in bindings]
        alive = [(b, sc) for b, sc in scored if sc.status != "contradicted"]
        if not alive:
            decision = "rejected"
        else:
            # probe candidate bindings, distinct visit-sets only, up to 3
            seen_visits: set[tuple[str, ...]] = set()
            for b, _sc in alive:
                visits = tuple(b[r] for r in obj_roles)
                if visits in seen_visits:
                    continue
                seen_visits.add(visits)
                if len(seen_visits) > 3:
                    break
                probe_reset(visits)
                ev = evidence()
                scored = [(b2, score(dict(h, hypothesis_id="bind"), b2, ev)) for b2 in bindings]
                alive = [(b2, sc) for b2, sc in scored if sc.status != "contradicted"]
                if not alive:
                    decision = "rejected"
                    break
                best_b, best_sc = min(alive, key=lambda x: (x[1].j, x[1].dl))
                interv = len(
                    ev.interventional_steps
                    & {s2 for s2, tid, _ in ev.pushes if tid == target_track}
                )
                if (
                    best_sc.n_preds >= min_support
                    and interv >= min_support
                    and runner.trace.pushes
                    and runner.trace.pushes[-1][2] == "pass"
                ):
                    decision = "bound"
                    chosen = best_b
                    break
        if decision == "bound":
            _finish_goal(runner)
    except BudgetExceeded:
        pass
    # schema prediction accuracy on all target pushes, best surviving binding
    ev = evidence()
    pool = [chosen] if chosen else bindings[:1]
    if pool and pool[0] is not None:
        sc = score(dict(h, hypothesis_id="bind"), pool[0], ev)
        pm = dict(sc.predictions)
        for s2, tid, o in ev.pushes:
            if tid == pool[0][h["target_role"]] and s2 in pm:
                total += 1
                correct += pm[s2] == o
    return BindingResult(
        decision,
        chosen,
        runner.used,
        runner.goal_ever or (runner.terminated and runner.reward > 0),
        correct,
        max(total, 1),
    )


def _schema_requires_latent(h: dict[str, Any]) -> bool:
    c = h["condition"]
    return c.get("const") is not True


def _matching_probe(probes: list[Probe], visits: tuple[str, ...]) -> Probe | None:
    for p in probes:
        if p.visits == visits and p.n_pushes == 1:
            return p
    for p in probes:
        if set(p.visits) == set(visits) and p.n_pushes == 1:
            return p
    return None


def _goalish(tr: TrackedTrace) -> tuple[int, int] | None:
    return None


def _finish_goal(runner: SessionRunner) -> None:
    """After passage, walk to any newly reachable single-cell component
    (reward discovered on contact terminates the episode)."""
    tr = runner.trace
    try:
        for _ in range(3):
            if runner.terminated:
                return
            obs = tr.obs_log[-1]
            g = [list(r) for r in obs.grid]
            ay, ax = tr.agent_pos
            g[ay][ax] = 0
            gg = tuple(tuple(r) for r in g)
            targets = [
                t
                for t in tr.tracks.values()
                if t.tid != tr.agent_track and t.present and len(t.cells) == 1
            ]
            done = False
            for t in sorted(targets, key=lambda t: abs(t.pos[0] - ay) + abs(t.pos[1] - ax)):
                p = path_to(gg, (ay, ax), t.pos, stop_adjacent=False)
                if p:
                    runner.run(p)
                    done = True
                    break
            if not done:
                return
    except BudgetExceeded:
        return
