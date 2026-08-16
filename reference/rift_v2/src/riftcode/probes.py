"""Hypothesis grammar and probe economics for the code domain.

Grammar atoms (all constructed at runtime over anonymous roles):
    const False        -> no handle in the action space changes the outcome
                          (a representation failure; it does not locate a cause)
    const True         -> target is not actually gated (already green)
    var z_r            -> passes iff intervention r has been applied
    ge k, n            -> passes on the n-th run within an episode (retry-masked)
    and(a, b)          -> two-cause conjunction
Probes are (fresh?, applied roles, repeats) triples. Selection maximises
Jensen-Shannon divergence of predicted outcomes per unit estimated cost.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from typing import Any

from rift.hypothesis import execute
from rift.population import Scored
from rift.probes import js_divergence
from riftcode.observe import EvidenceBuilder


def code_grammar(roles: list[str], max_conj: int = 2) -> list[dict[str, Any]]:
    obj_roles = [r for r in roles if r != "rT"]
    atoms: list[tuple[list[dict], dict]] = [([], {"const": False}), ([], {"const": True})]
    for i, r in enumerate(obj_roles):
        atoms.append(
            (
                [
                    {
                        "name": f"z{i}",
                        "type": "bool",
                        "init": False,
                        "set_on": {"event": "applied", "role": r},
                        "reset_on": None,
                    }
                ],
                {"var": f"z{i}"},
            )
        )
    for n in (2, 3, 4):
        atoms.append(
            (
                [
                    {
                        "name": f"k{n}",
                        "type": "counter",
                        "max": 16,
                        "inc_on": {"event": "run", "role": None},
                    }
                ],
                {"op": "ge", "var": f"k{n}", "value": n},
            )
        )
    out: list[dict[str, Any]] = []
    for lats, cond in atoms:
        out.append(_h(len(out), roles, lats, cond))
    real = [(la, c) for la, c in atoms if "const" not in c]
    if max_conj >= 2:
        for (l1, c1), (l2, c2) in itertools.combinations(real, 2):
            if {x["name"] for x in l1} & {x["name"] for x in l2}:
                continue
            # conjunction: both causes required (stacked causes)
            out.append(_h(len(out), roles, l1 + l2, {"op": "and", "args": [c1, c2]}))
            # disjunction: either suffices. Required because discovered handles
            # form a hierarchy (a directory handle subsumes a file handle), so
            # two different handles can each be sufficient.
            out.append(_h(len(out), roles, l1 + l2, {"op": "or", "args": [c1, c2]}))
    return out


def _h(i: int, roles: list[str], lats: list[dict], cond: dict) -> dict[str, Any]:
    return {
        "hypothesis_id": f"c{i}",
        "roles": roles,
        "target_role": "rT",
        "latents": lats,
        "condition": cond,
    }


@dataclass(frozen=True)
class CodeProbe:
    fresh: bool
    applied: tuple[str, ...]
    repeats: int

    @property
    def name(self) -> str:
        f = "fresh" if self.fresh else "same"
        return f"{f}[{'+'.join(self.applied) or 'none'}]x{self.repeats}"

    @property
    def est_cost(self) -> float:
        return self.repeats + (0.5 if self.fresh else 0.0) + 0.2 * len(self.applied)


def generate_probes(roles: list[str], max_pairs: int = 3) -> list[CodeProbe]:
    obj = [r for r in roles if r != "rT"]
    probes = [
        CodeProbe(True, (), 1),
        CodeProbe(True, (), 3),
        CodeProbe(False, (), 1),
    ]
    for r in obj:
        probes.append(CodeProbe(True, (r,), 1))
    for a, b in list(itertools.combinations(obj, 2))[:max_pairs]:
        probes.append(CodeProbe(True, (a, b), 1))
    return probes


def predict(sc: Scored, probe: CodeProbe, eb: EvidenceBuilder) -> str | None:
    """Simulate the probe against the theory's latent program."""
    sim = EvidenceBuilder(
        step=eb.step,
        events=list(eb.events),
        pushes=list(eb.pushes),
        boundaries=set(eb.boundaries),
        interventional=set(eb.interventional),
    )
    if probe.fresh:
        sim.new_episode()
    sim.record(probe.applied, probe.repeats, "?")
    ev = sim.evidence()
    res = execute(sc.hypothesis, sc.binding, ev.events, ev.pushes, ev.boundaries)
    later = [o for s, o in res.predictions if s > eb.step]
    return later[-1] if later else None


def select_probe(
    policy: str,
    probes: list[CodeProbe],
    live: list[Scored],
    eb: EvidenceBuilder,
    rng: random.Random,
) -> CodeProbe:
    """All policies receive the identical candidate probe set."""
    if policy == "random":
        return rng.choice(probes)
    if policy == "cheapest":
        return min(probes, key=lambda p: p.est_cost)
    if policy != "disagreement":
        raise ValueError(policy)
    best, best_key = probes[0], (-1.0, 0.0)
    for p in probes:
        preds = [x for x in (predict(sc, p, eb) for sc in live) if x is not None]
        if not preds:
            continue
        d = js_divergence([{x: 1.0} for x in preds])
        key = (d / p.est_cost, -p.est_cost)
        if key > best_key:
            best, best_key = p, key
    if best_key[0] <= 0.0:
        return rng.choice(probes)
    return best
