"""PRIVATE environment runtime.

Agent modules must never import this module (enforced by tests/test_isolation).
Only the public wrapper in public_env.py and the evaluator may touch it.

Engine: rectangular grid, outer walls, one interior wall column with a single
barrier cell separating the agent region from the goal region. Objects sit in
the agent region. The barrier admits passage iff the family's hidden condition
holds. The barrier is ALWAYS rendered identically whether locked or unlocked,
so openness is never directly observable.

Rendered colour codes (colours are shuffled per instance so no code is
semantically stable across instances, except EMPTY=0):
    0 empty | walls, agent, barrier, goal, objects: instance-shuffled ints 1..12
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from rift.contracts import ACTIONS, DELTAS, PrimitiveAction, PublicObservation, StepResult

FAMILIES = (
    "possession",
    "timed",
    "attempt_counter",
    "switch_parity",
    "ordered_sequence",
    "multi_resource",
    "phase_window",
    "other_actor",
    "ungated",
    "decoy_correlated",
)

# held-out causal compositions (conjunctions never used in development)
COMPOSITIONS = (
    "possession+switch_parity",
    "multi_resource+timed",
)


@dataclass
class _Obj:
    pos: tuple[int, int]
    color: int
    kind: str  # "consumable" | "persistent" | "decoy_consumable" | "npc"
    order_index: int = -1
    despawn_at: int = -1
    patrol: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class PrivateConfig:
    family: str
    h: int
    w: int
    wall_col: int
    barrier_row: int
    agent_start: tuple[int, int]
    goal: tuple[int, int]
    objects: list[_Obj]
    colors: dict[str, int]  # wall/agent/barrier/goal -> colour code
    timed_t: int = 0
    counter_n: int = 0
    phase_period: int = 0
    phase_lo: int = 0
    phase_hi: int = 0
    npc_open_step: int = -1


class PrivateEnv:
    """Concrete hidden-state environment. Evaluator-only oracle lives here."""

    def __init__(self, cfg: PrivateConfig):
        self._cfg = cfg
        self._rng = random.Random(0)

    # ---------------- oracle API (EVALUATION ONLY) ----------------
    def oracle_condition(self, st: dict[str, object]) -> bool:
        c = self._cfg
        fams = c.family.split("+")
        ok = True
        for fam in fams:
            ok = ok and self._fam_open(fam, st)
        return ok

    def _fam_open(self, fam: str, st: dict[str, object]) -> bool:
        c = self._cfg
        if fam == "possession":
            return bool(st["collected_primary"])
        if fam == "timed":
            return int(st["t"]) >= c.timed_t  # type: ignore[call-overload]
        if fam == "attempt_counter":
            return int(st["push_attempts"]) >= c.counter_n  # type: ignore[call-overload]
        if fam == "switch_parity":
            return int(st["switch_presses"]) % 2 == 1  # type: ignore[call-overload]
        if fam == "ordered_sequence":
            return bool(st["ordered_ok"])
        if fam == "multi_resource":
            return bool(st["collected_all"])
        if fam == "phase_window":
            t = int(st["t"])  # type: ignore[call-overload]
            return c.phase_lo <= (t % c.phase_period) < c.phase_hi
        if fam == "other_actor":
            return int(st["t"]) >= c.npc_open_step  # type: ignore[call-overload]
        if fam == "ungated":
            return True
        if fam == "decoy_correlated":
            return int(st["t"]) >= c.timed_t  # type: ignore[call-overload]
        raise ValueError(fam)

    def oracle_meta(self) -> dict[str, object]:
        """Evaluator-only metadata. Must never enter agent modules/prompts."""
        c = self._cfg
        return {"family": c.family, "barrier": (c.barrier_row, c.wall_col)}

    # ---------------- session ----------------
    def reset(self, seed: int) -> PublicObservation:
        self._rng = random.Random(seed)
        c = self._cfg
        self._t = 0
        self._agent = c.agent_start
        self._alive = {i: True for i in range(len(c.objects))}
        self._st: dict[str, object] = {
            "t": 0,
            "collected_primary": False,
            "collected": set(),
            "collected_all": False,
            "push_attempts": 0,
            "switch_presses": 0,
            "ordered_ok": False,
            "order_progress": 0,
        }
        return self._render()

    def actions(self) -> tuple[PrimitiveAction, ...]:
        return ACTIONS

    def step(self, action: PrimitiveAction) -> StepResult:
        c = self._cfg
        self._t += 1
        self._st["t"] = self._t
        dy, dx = DELTAS[action]
        ny, nx = self._agent[0] + dy, self._agent[1] + dx
        reward, terminated = 0.0, False
        if action != "S":
            if (ny, nx) == (c.barrier_row, c.wall_col):
                self._st["push_attempts"] = int(self._st["push_attempts"]) + 1  # type: ignore[call-overload]
                if self.oracle_condition(self._st):
                    self._agent = (ny, nx)
            elif self._passable(ny, nx):
                self._agent = (ny, nx)
        # object interactions at agent cell
        for i, ob in enumerate(c.objects):
            if not self._alive[i] or ob.kind == "npc":
                continue
            if self._agent == ob.pos:
                if ob.kind in ("consumable", "decoy_consumable"):
                    self._alive[i] = False
                    self._on_collect(i, ob)
                elif ob.kind == "persistent":
                    # a press only registers on entry (not while standing)
                    if self._entered_this_step:
                        self._st["switch_presses"] = int(self._st["switch_presses"]) + 1  # type: ignore[call-overload]
        # scheduled despawns + npc movement
        for i, ob in enumerate(c.objects):
            if self._alive[i] and ob.despawn_at >= 0 and self._t >= ob.despawn_at:
                if self._agent != ob.pos:
                    self._alive[i] = False
            if ob.kind == "npc" and ob.patrol:
                ob.pos = ob.patrol[self._t % len(ob.patrol)]
        if self._agent == c.goal:
            reward, terminated = 1.0, True
        return StepResult(self._render(), reward, terminated)

    @property
    def _entered_this_step(self) -> bool:
        return True  # movement already applied; standing still handled by action S

    def _on_collect(self, i: int, ob: _Obj) -> None:
        st = self._st
        if ob.kind == "decoy_consumable":
            return
        collected = st["collected"]
        assert isinstance(collected, set)
        collected.add(i)
        if ob.order_index == 0 or (ob.order_index < 0 and not st["collected_primary"]):
            st["collected_primary"] = True
        # ordered sequence bookkeeping
        if ob.order_index >= 0:
            if ob.order_index == int(st["order_progress"]):  # type: ignore[call-overload]
                st["order_progress"] = int(st["order_progress"]) + 1  # type: ignore[call-overload]
            else:
                st["order_progress"] = -99  # wrong order: unrecoverable this episode
            need = sum(1 for o in self._cfg.objects if o.order_index >= 0)
            st["ordered_ok"] = int(st["order_progress"]) >= need  # type: ignore[call-overload]
        need_all = {
            j
            for j, o in enumerate(self._cfg.objects)
            if o.kind == "consumable" and o.order_index < 0
        }
        st["collected_all"] = need_all.issubset(collected) and bool(need_all)

    def _passable(self, y: int, x: int) -> bool:
        c = self._cfg
        if not (0 <= y < c.h and 0 <= x < c.w):
            return False
        if y in (0, c.h - 1) or x in (0, c.w - 1):
            return False
        if x == c.wall_col and (y, x) != (c.barrier_row, c.wall_col):
            return False
        return True

    def _render(self) -> PublicObservation:
        c = self._cfg
        g = [[0] * c.w for _ in range(c.h)]
        for y in range(c.h):
            for x in range(c.w):
                if y in (0, c.h - 1) or x in (0, c.w - 1) or x == c.wall_col:
                    g[y][x] = c.colors["wall"]
        g[c.barrier_row][c.wall_col] = c.colors["barrier"]
        gy, gx = c.goal
        g[gy][gx] = c.colors["goal"]
        for i, ob in enumerate(c.objects):
            if self._alive.get(i, True):
                oy, ox = ob.pos
                if g[oy][ox] == 0:
                    g[oy][ox] = ob.color
        ay, ax = self._agent
        g[ay][ax] = c.colors["agent"]
        return PublicObservation(tuple(tuple(r) for r in g), self._t)
