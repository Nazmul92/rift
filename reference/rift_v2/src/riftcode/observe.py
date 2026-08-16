"""Perception for the code domain.

Two jobs, both generic:

1. `discover_interventions` — enumerate the handles the agent could pull,
   derived from observable signals only: identifier-shaped tokens in the
   failure output, state-looking paths in the working tree, and other
   collected test ids. This is the code-domain analogue of anonymous
   component extraction, and (as in the gridworld) it is the step most likely
   to be the real bottleneck: a cause with no corresponding handle here can
   never be hypothesised downstream.

2. `EvidenceBuilder` — turn executed probes into the generic event/push
   stream the shared IR consumes, with one boundary per sandbox episode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rift.population import Evidence
from riftcode.contracts import Intervention

IDENT = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")
NOISE = {
    "ERROR",
    "ERRORS",
    "FAILED",
    "FAILURES",
    "WARNING",
    "WARNINGS",
    "TRUE",
    "FALSE",
    "NONE",
    "PASSED",
    "SKIPPED",
    "ASSERT",
    "PYTEST",
    "PYTHONPATH",
    "TRACEBACK",
    "SHORT",
    "TEST",
    "SUMMARY",
    "INFO",
}
STATE_HINT = ("cache", "tmp", "state", "lock", "build", "artifact", ".mark")
# Variables that are part of any normal shell; their presence is not a signal.
STANDARD_ENV = {
    "PATH",
    "HOME",
    "PWD",
    "SHELL",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TERM",
    "HOSTNAME",
    "SHLVL",
    "TMPDIR",
    "PYTHONPATH",
    "PYTHONHASHSEED",
    "VIRTUAL_ENV",
    "OLDPWD",
    "_",
    "LS_COLORS",
    "MAIL",
    "EDITOR",
    "PAGER",
}


def discover_interventions(
    failure_text: str,
    paths: list[str],
    collected: list[str],
    target: str,
    cap: int = 8,
    ambient_env: dict[str, str] | None = None,
    quotas: tuple[int, int, int, int] = (2, 2, 2, 4),
) -> list[Intervention]:
    """Enumerate pullable handles from observable signals only.

    Four generic sources, none of them fault-specific:
      * identifier-shaped tokens in the failure output      -> env
      * non-standard variables in the ambient environment   -> unsetenv
      * state-looking DIRECTORIES in the tree               -> clear
      * other collected tests, coarsest granularity first   -> first

    Sources get quotas rather than first-come order: on a real repository one
    source (typically cache directories) otherwise floods the budget and
    crowds out the others. Coarse `first` handles precede fine ones because a
    real suite has thousands of tests, so the handle set is a hierarchy to be
    bisected, not an enumeration.
    """
    env_c: list[Intervention] = []
    unset_c: list[Intervention] = []
    clear_c: list[Intervention] = []
    for name in IDENT.findall(failure_text):
        if name not in NOISE and all(i.arg != name for i in env_c):
            env_c.append(Intervention("env", name))
    for name in sorted(ambient_env or {}):
        if name not in STANDARD_ENV and IDENT.fullmatch(name):
            unset_c.append(Intervention("unsetenv", name))
    seen_dirs: set[str] = set()
    for p in paths:
        if not p.endswith("/"):
            continue
        name = p.rstrip("/").split("/")[-1]
        if name in seen_dirs:
            continue
        if any(h in name.lower() for h in STATE_HINT):
            seen_dirs.add(name)
            clear_c.append(Intervention("clear", name))
    first_c = _first_handles(collected, target)
    out: list[Intervention] = []
    seen: set[str] = set()
    for pool, q in zip((env_c, unset_c, clear_c, first_c), quotas):
        for iv in pool[:q]:
            if iv.label not in seen and len(out) < cap:
                seen.add(iv.label)
                out.append(iv)
    return out


def _first_handles(collected: list[str], target: str, per_level: int = 3) -> list[Intervention]:
    """Coarse-to-fine `run this before the target` handles."""
    others = [n for n in collected if n != target and "::" in n]
    files = sorted({n.split("::")[0] for n in others})
    tgt_file = target.split("::")[0]
    dirs = sorted({f.rsplit("/", 1)[0] + "/" for f in files if "/" in f})
    out: list[Intervention] = []
    for d in dirs[:per_level]:
        out.append(Intervention("first", d))
    for f in files[:per_level]:
        if f != tgt_file:
            out.append(Intervention("first", f))
    return out


def refine_first(cause: Intervention, collected: list[str], target: str) -> list[Intervention]:
    """Split a coarse `first` handle into halves for bisection."""
    if cause.kind == "firstset":
        inside = sorted(set(cause.arg.split(",")))
    elif cause.kind == "first" and "::" not in cause.arg and cause.arg.endswith("/"):
        inside = sorted({n.split("::")[0] for n in collected if n.startswith(cause.arg)})
    else:
        return []
    inside = [f for f in inside if f != target.split("::")[0]]
    if len(inside) < 2:
        return []
    mid = len(inside) // 2
    return [
        Intervention("firstset", ",".join(inside[:mid])),
        Intervention("firstset", ",".join(inside[mid:])),
    ]


def role_map(interventions: list[Intervention]) -> tuple[dict[str, Intervention], list[str]]:
    """Anonymous role ids: the hypothesis language never sees the handle's
    name, only r0..rN, so a theory cannot smuggle in semantics from the label."""
    mapping = {f"r{i}": iv for i, iv in enumerate(interventions)}
    return mapping, [*mapping.keys(), "rT"]


@dataclass
class EvidenceBuilder:
    """Accumulates generic events across episodes."""

    step: int = 0
    events: list[tuple[int, str, str | None]] = field(default_factory=list)
    pushes: list[tuple[int, str, str]] = field(default_factory=list)
    boundaries: set[int] = field(default_factory=lambda: {0})
    interventional: set[int] = field(default_factory=set)
    observed: list[dict] = field(default_factory=list)

    def new_episode(self) -> None:
        self.step += 2
        self.boundaries.add(self.step)

    def record(
        self,
        applied_roles: tuple[str, ...],
        repeats: int,
        outcome: str,
        interventional: bool = True,
    ) -> None:
        for r in applied_roles:
            self.step += 1
            self.events.append((self.step, "applied", r))
        for i in range(repeats):
            self.step += 1
            self.events.append((self.step, "run", None))
            if i == repeats - 1:
                self.pushes.append((self.step, "T", outcome))
                if interventional:
                    self.interventional.add(self.step)
        self.observed.append(
            {"applied": list(applied_roles), "repeats": repeats, "outcome": outcome}
        )

    def evidence(self) -> Evidence:
        ev = Evidence()
        ev.events = sorted(self.events)
        ev.pushes = sorted(self.pushes)
        ev.boundaries = set(self.boundaries)
        ev.interventional_steps = set(self.interventional)
        return ev
