"""RIFT debug loop.

    baseline -> discover handles -> populate ledger -> probe -> eliminate
             -> identify -> interventional gate

The LLM is not in this loop. Scoring, elimination and probe choice are
deterministic Python; a language model is needed only to propose handles the
generic discoverer missed and to write the eventual source patch. That is the
cost argument for this architecture: today's agents re-derive their entire
mental model on every iteration, which is where the tokens go.
"""

from __future__ import annotations

import os
import random
from typing import Any

from rift.population import Scored, score
from riftcode.contracts import (
    CENSORED,
    FAIL,
    IDENTIFIED,
    PASS,
    UNDERDETERMINED,
    UNEXPLAINED_BY_REPRESENTATION,
    BudgetExceeded,
    Diagnosis,
    Intervention,
)
from riftcode.observe import EvidenceBuilder, discover_interventions, refine_first, role_map
from riftcode.probes import CodeProbe, code_grammar, generate_probes, predict, select_probe
from riftcode.sandbox import Sandbox

IDENTITY_TARGET = "T"


def _binding(roles: list[str]) -> dict[str, str]:
    b = {r: r for r in roles if r != "rT"}
    b["rT"] = IDENTITY_TARGET
    return b


def detect_flakiness(eb: EvidenceBuilder) -> list[dict[str, Any]]:
    """Representation-failure signal, code flavour: an identical applied-set
    and repeat count produced incompatible outcomes. Same detector role as
    gridworld state aliasing — it says the current variable set is incomplete,
    without naming the missing variable."""
    seen: dict[tuple, set[str]] = {}
    for rec in eb.observed:
        key = (tuple(sorted(rec["applied"])), rec["repeats"])
        seen.setdefault(key, set()).add(rec["outcome"])
    return [
        {"applied": list(k[0]), "repeats": k[1], "outcomes": sorted(v)}
        for k, v in seen.items()
        if len(v) > 1
    ]


def _future_classes(
    live: list[Scored], probes: list[CodeProbe], eb: EvidenceBuilder
) -> list[Scored]:
    groups: dict[tuple, Scored] = {}
    for sc in sorted(live, key=lambda s: (s.j, s.dl)):
        key = (tuple(sc.predictions), tuple(predict(sc, p, eb) for p in probes))
        groups.setdefault(key, sc)
    return list(groups.values())


def _cause_of(h: dict[str, Any], mapping: dict[str, Intervention]) -> Intervention | None:
    for la in h.get("latents", []):
        if la["type"] == "bool":
            r = la["set_on"].get("role")
            if r in mapping:
                return mapping[r]
    return None


def localize(
    sandbox: Sandbox,
    target: str,
    *,
    policy: str = "disagreement",
    max_probes: int = 8,
    rng_seed: int = 0,
) -> tuple[Diagnosis, EvidenceBuilder, dict[str, Intervention]]:
    rng = random.Random(rng_seed)
    eb = EvidenceBuilder()
    notes: list[str] = []
    try:
        base = sandbox.run_target(target)
        eb.record((), 1, base.outcome, interventional=False)
        if base.outcome == PASS:
            return (
                Diagnosis(
                    IDENTIFIED,
                    None,
                    None,
                    1,
                    sandbox.budget.commands,
                    sandbox.budget.seconds,
                    notes=["target already passes on a clean episode"],
                ),
                eb,
                {},
            )
        collected = sandbox.collect()
        handles = discover_interventions(
            base.stdout + base.stderr,
            sandbox.list_paths(),
            collected,
            target,
            ambient_env=dict(os.environ),
        )
        mapping, roles = role_map(handles)
        notes.append(f"{len(handles)} candidate handles: {[h.label for h in handles]}")
        # A priori rule, not tuned on results: separating single-cause
        # hypotheses needs at least one probe per handle, plus a few more for
        # counters and two-cause combinations. Every probe is still charged.
        max_probes = max(max_probes, 2 * len(handles) + 4)
        binding = _binding(roles)
        pool = code_grammar(roles)
        probes = generate_probes(roles)
        scored = [score(h, binding, eb.evidence()) for h in pool]
        for _ in range(max_probes):
            live = [s for s in scored if s.status != "contradicted"]
            if not live:
                notes.append("all hypotheses contradicted: handle set is inadequate")
                break
            classes = _future_classes(live, probes, eb)
            if len(classes) == 1 and classes[0].n_preds >= 2:
                break
            probe = select_probe(policy, probes, classes, eb, rng)
            if probe.fresh:
                sandbox.fresh()
                eb.new_episode()
            ivs = tuple(mapping[r] for r in probe.applied)
            res = sandbox.run_target(target, ivs, probe.repeats)
            eb.record(probe.applied, probe.repeats, res.outcome)
            scored = [score(s.hypothesis, binding, eb.evidence()) for s in scored]
    except BudgetExceeded as e:
        notes.append(f"censored: {e}")
        live = [s for s in scored if s.status != "contradicted"] if "scored" in dir() else []
        return (
            Diagnosis(
                CENSORED,
                None,
                None,
                len(live),
                sandbox.budget.commands,
                sandbox.budget.seconds,
                notes=notes,
            ),
            eb,
            mapping if "mapping" in dir() else {},
        )
    flakes = detect_flakiness(eb)
    if flakes:
        notes.append(f"nondeterminism detected on identical inputs: {flakes}")
    live = [s for s in scored if s.status != "contradicted"]
    classes = _future_classes(live, probes, eb) if live else []
    best = min(live, key=lambda s: (s.j, s.dl)) if live else None
    cause = _cause_of(best.hypothesis, mapping) if best else None
    if best is not None and len(classes) == 1:
        # A surviving `const False` says only that nothing in the candidate
        # handle set changes the outcome. Elimination cannot reach a cause it
        # has no handle for, so this is a failure of the representation rather
        # than a positive identification, and it attributes nothing to the
        # repository.
        if best.hypothesis["condition"].get("const") is False:
            status = UNEXPLAINED_BY_REPRESENTATION
            notes.append(
                "no handle in the current action space changes the outcome, so "
                "this representation cannot explain the failure. A cause outside "
                "the action space (missing binary, dependency version, locale, "
                "parallelism) is indistinguishable from an unconditional failure "
                "at this boundary; the result attributes nothing to the "
                "repository and locates no cause."
            )
        else:
            status = IDENTIFIED
    elif not live:
        # Every candidate theory was contradicted, including "nothing helps".
        # That is the same representation failure by a different route, and it
        # is not the "several theories remain" meaning of underdetermined.
        status = UNEXPLAINED_BY_REPRESENTATION
    else:
        status = UNDERDETERMINED
    return (
        Diagnosis(
            status,
            cause,
            best.hypothesis if best else None,
            len(classes),
            sandbox.budget.commands,
            sandbox.budget.seconds,
            flaky=bool(flakes),
            notes=notes,
        ),
        eb,
        mapping,
    )


def bisect_cause(
    sandbox: Sandbox, target: str, cause: Intervention, collected: list[str], max_rounds: int = 6
) -> tuple[Intervention, int]:
    """Narrow a coarse `first:<dir>` cause to the specific test file responsible.

    Delta debugging falls out of the same discipline: halve the selection,
    keep the half that still makes the target pass. Each run is charged.
    """
    rounds = 0
    cur = cause
    while rounds < max_rounds:
        halves = refine_first(cur, collected, target)
        if not halves:
            break
        progressed = False
        for half in halves:
            sandbox.fresh()
            if sandbox.run_target(target, (half,)).outcome == PASS:
                files = half.arg.split(",")
                cur = (
                    Intervention("first", files[0])
                    if len(files) == 1
                    else Intervention("firstset", half.arg)
                )
                progressed = True
                break
        rounds += 1
        if not progressed:
            break
    return cur, rounds


def verify(sandbox: Sandbox, target: str, diagnosis: Diagnosis) -> dict[str, Any]:
    """Interventional gate. A claimed fix is accepted only if the failure
    reproduces on a clean episode, disappears when the cause is applied, and
    REAPPEARS when it is withdrawn. "It passes now" alone is consistent with a
    stale cache, a retry, or an unrelated edit — this is the check that
    separates a real fix from verification theatre."""
    if diagnosis.cause is None:
        return {"verdict": "not_applicable", "reason": "no environmental cause identified"}
    phases: dict[str, str] = {}
    try:
        sandbox.fresh()
        phases["baseline"] = sandbox.run_target(target).outcome
        sandbox.fresh()
        phases["with_fix"] = sandbox.run_target(target, (diagnosis.cause,)).outcome
        sandbox.fresh()
        phases["reverted"] = sandbox.run_target(target).outcome
    except BudgetExceeded as e:
        return {"verdict": "censored", "phases": phases, "reason": str(e)}
    ok = phases["baseline"] == FAIL and phases["with_fix"] == PASS and phases["reverted"] == FAIL
    return {"verdict": "verified" if ok else "rejected", "phases": phases}
