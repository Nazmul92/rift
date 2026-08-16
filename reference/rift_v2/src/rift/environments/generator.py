"""Procedural instance generator (PRIVATE; evaluator-side only).

Varies grid size, wall column, barrier row, object positions, colours,
decoy count, timing parameters and path topology per seed. Colour codes are
shuffled per instance so no integer is semantically stable.

Partitions:
    development           : seed % 3 == 0
    held-out instantiation: seed % 3 == 1
    held-out composition  : composition families, any seed
"""

from __future__ import annotations

import random
import zlib

from rift.environments.private_runtime import (
    COMPOSITIONS,
    FAMILIES,
    PrivateConfig,
    PrivateEnv,
    _Obj,
)


def _free_cells(h: int, w: int, wall_col: int, left: bool) -> list[tuple[int, int]]:
    xs = range(1, wall_col) if left else range(wall_col + 1, w - 1)
    return [(y, x) for y in range(1, h - 1) for x in xs]


def make_env(family: str, seed: int) -> PrivateEnv:
    if family not in FAMILIES and family not in COMPOSITIONS:
        raise ValueError(family)
    rng = random.Random(zlib.crc32(family.encode()) * 1_000_003 + seed)
    h = rng.randint(7, 10)
    w = rng.randint(9, 13)
    wall_col = rng.randint(4, w - 4)
    barrier_row = rng.randint(1, h - 2)
    palette = list(range(1, 13))
    rng.shuffle(palette)
    colors = {
        "wall": palette[0],
        "agent": palette[1],
        "barrier": palette[2],
        "goal": palette[3],
    }
    left = _free_cells(h, w, wall_col, True)
    right = _free_cells(h, w, wall_col, False)
    rng.shuffle(left)
    rng.shuffle(right)
    agent_start = left.pop()
    goal = right.pop()
    objs: list[_Obj] = []
    fams = family.split("+")
    ci = 4

    def col() -> int:
        nonlocal ci
        ci += 1
        return palette[ci - 1]

    for fam in fams:
        if fam in ("possession", "decoy_correlated"):
            k = "consumable" if fam == "possession" else "decoy_consumable"
            objs.append(_Obj(left.pop(), col(), k))
        elif fam == "switch_parity":
            objs.append(_Obj(left.pop(), col(), "persistent"))
        elif fam == "ordered_sequence":
            objs.append(_Obj(left.pop(), col(), "consumable", order_index=0))
            objs.append(_Obj(left.pop(), col(), "consumable", order_index=1))
        elif fam == "multi_resource":
            objs.append(_Obj(left.pop(), col(), "consumable"))
            objs.append(_Obj(left.pop(), col(), "consumable"))
        elif fam == "other_actor":
            patrol_cells = [right.pop(), right.pop()]
            objs.append(_Obj(patrol_cells[0], col(), "npc", patrol=patrol_cells))
    # decoys (irrelevant objects), 0-3
    for _ in range(rng.randint(0, 3)):
        if left:
            objs.append(_Obj(left.pop(), col(), "decoy_consumable"))
    timed_t = rng.randint(6, 14)
    cfg = PrivateConfig(
        family=family,
        h=h,
        w=w,
        wall_col=wall_col,
        barrier_row=barrier_row,
        agent_start=agent_start,
        goal=goal,
        objects=objs,
        colors=colors,
        timed_t=timed_t,
        counter_n=rng.randint(2, 4),
        phase_period=rng.randint(6, 9),
        phase_lo=0,
        phase_hi=rng.randint(2, 4),
        npc_open_step=rng.randint(5, 12),
    )
    if family == "decoy_correlated":
        # decoy object despawns just before the timer opens the barrier
        for ob in cfg.objects:
            if ob.kind == "decoy_consumable":
                ob.despawn_at = max(2, timed_t - 1)
                break
    return PrivateEnv(cfg)


def partition_of(seed: int, family: str) -> str:
    if family in COMPOSITIONS:
        return "held_out_composition"
    return "development" if seed % 3 == 0 else "held_out_instantiation"
