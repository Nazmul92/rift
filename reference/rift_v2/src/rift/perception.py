"""Generic perception over rendered integer grids.

No semantic grounding: components get anonymous ids c0, c1, ... in order of
first appearance. The agent's own component is identified behaviourally during
a short calibration (movement correlates with issued actions), never by colour
convention. Occlusion (agent standing on a component's cell) is represented as
uncertainty, not disappearance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rift.contracts import DELTAS, PrimitiveAction, PublicObservation

Cell = tuple[int, int]


def components(grid: tuple[tuple[int, ...], ...]) -> list[tuple[int, frozenset[Cell]]]:
    """4-connected same-colour components of non-empty cells."""
    h, w = len(grid), len(grid[0])
    seen: set[Cell] = set()
    out: list[tuple[int, frozenset[Cell]]] = []
    for y in range(h):
        for x in range(w):
            if grid[y][x] == 0 or (y, x) in seen:
                continue
            col = grid[y][x]
            stack, comp = [(y, x)], set()
            while stack:
                cy, cx = stack.pop()
                if (cy, cx) in seen or grid[cy][cx] != col:
                    continue
                seen.add((cy, cx))
                comp.add((cy, cx))
                for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and (ny, nx) not in seen:
                        stack.append((ny, nx))
            out.append((col, frozenset(comp)))
    return out


@dataclass
class Track:
    tid: str
    color: int
    pos: Cell  # representative cell (single-cell components; multi-cell keep min)
    cells: frozenset[Cell]
    present: bool = True
    occluded: bool = False
    uncertain: bool = False
    moved_last: bool = False


@dataclass
class Event:
    step: int
    kind: str  # overlap | disappeared | adjacent_enter | blocked | comp_moved | tick
    role: str | None = None


@dataclass
class TrackedTrace:
    steps: int = 0
    agent_track: str = ""
    agent_pos: Cell = (0, 0)
    tracks: dict[str, Track] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    pushes: list[tuple[int, str, str]] = field(default_factory=list)  # (step, role, outcome)
    actions: list[PrimitiveAction] = field(default_factory=list)
    obs_log: list[PublicObservation] = field(default_factory=list)


class Tracker:
    """Maintains anonymous entity tracks across frames."""

    def __init__(self) -> None:
        self.trace = TrackedTrace()
        self._next = 0
        self._prev_grid: tuple[tuple[int, ...], ...] | None = None

    def _new_tid(self) -> str:
        self._next += 1
        return f"c{self._next - 1}"

    def observe_first(self, obs: PublicObservation) -> None:
        for col, cells in components(obs.grid):
            t = Track(self._new_tid(), col, min(cells), cells)
            self.trace.tracks[t.tid] = t
        self._prev_grid = obs.grid
        self.trace.obs_log.append(obs)

    def identify_self(
        self, before: PublicObservation, action: PrimitiveAction, after: PublicObservation
    ) -> None:
        """Behavioural self-identification: the single-cell component whose
        position changed consistently with the issued action."""
        dy, dx = DELTAS[action]
        pre = {cells: col for col, cells in components(before.grid)}
        post = {cells: col for col, cells in components(after.grid)}
        for cells, col in pre.items():
            if len(cells) != 1:
                continue
            (y, x) = next(iter(cells))
            target = frozenset({(y + dy, x + dx)})
            if target in post and post[target] == col and cells not in post:
                for t in self.trace.tracks.values():
                    if t.color == col and t.cells == cells:
                        self.trace.agent_track = t.tid
                        # leave position at the PRE-move cell; the subsequent
                        # update() call detects the move and emits events
                        self.trace.agent_pos = (y, x)
                        return

    def update(self, action: PrimitiveAction, obs: PublicObservation) -> None:
        tr = self.trace
        tr.steps = obs.step_index
        tr.actions.append(action)
        tr.obs_log.append(obs)
        tr.events.append(Event(obs.step_index, "tick"))
        grid = obs.grid
        cur = components(grid)
        cur_cells: dict[Cell, int] = {}
        for col, cells in cur:
            for c in cells:
                cur_cells[c] = col
        # agent movement
        a = tr.tracks.get(tr.agent_track)
        prev_apos = tr.agent_pos
        if a is not None:
            dy, dx = DELTAS[action]
            intended = (prev_apos[0] + dy, prev_apos[1] + dx)
            found = None
            for col, cells in cur:
                if col == a.color and len(cells) == 1:
                    p = next(iter(cells))
                    if p in (intended, prev_apos):
                        found = p if found is None or p == intended else found
            if found is None:
                found = prev_apos
            moved = found != prev_apos
            tr.agent_pos = found
            a.pos, a.cells = found, frozenset({found})
            if action != "S" and not moved and intended != prev_apos:
                # blocked move: attribute to whichever track's cells include target
                blocked_role = None
                for t in tr.tracks.values():
                    if t.tid != tr.agent_track and t.present and intended in t.cells:
                        blocked_role = t.tid
                        break
                tr.events.append(Event(obs.step_index, "blocked", blocked_role))
                if blocked_role is not None:
                    tr.pushes.append((obs.step_index, blocked_role, "blocked"))
            if action != "S" and moved:
                # did we pass INTO a cell previously occupied by a non-agent track?
                for t in tr.tracks.values():
                    if t.tid != tr.agent_track and t.present and found in t.cells:
                        tr.events.append(Event(obs.step_index, "overlap", t.tid))
                        tr.pushes.append((obs.step_index, t.tid, "pass"))
                        t.occluded, t.uncertain = True, True
        # per-track presence update
        for t in tr.tracks.values():
            if t.tid == tr.agent_track:
                continue
            visible = all(cur_cells.get(c) == t.color for c in t.cells)
            if visible:
                if t.occluded and tr.agent_pos not in t.cells:
                    t.occluded = False
                if not t.present:
                    t.present = True  # reappearance
                t.uncertain = t.occluded
                t.moved_last = False
            else:
                if tr.agent_pos in t.cells:
                    t.occluded, t.uncertain = True, True  # hidden under agent
                else:
                    # moved or disappeared: search same colour single cell nearby
                    cand = [
                        c
                        for c, col in cur_cells.items()
                        if col == t.color and len(t.cells) == 1 and c not in t.cells
                    ]
                    near = [c for c in cand if abs(c[0] - t.pos[0]) + abs(c[1] - t.pos[1]) <= 2]
                    if len(t.cells) == 1 and near:
                        t.pos = near[0]
                        t.cells = frozenset({near[0]})
                        t.moved_last = True
                        tr.events.append(Event(obs.step_index, "comp_moved", t.tid))
                    elif t.present:
                        t.present = False
                        t.occluded = False
                        tr.events.append(Event(obs.step_index, "disappeared", t.tid))
        # adjacency-entry events
        ay, ax = tr.agent_pos
        for t in tr.tracks.values():
            if t.tid == tr.agent_track or not t.present:
                continue
            if any(abs(ay - y) + abs(ax - x) == 1 for (y, x) in t.cells):
                py, px = prev_apos
                was_adj = any(abs(py - y) + abs(px - x) == 1 for (y, x) in t.cells)
                if not was_adj:
                    tr.events.append(Event(obs.step_index, "adjacent_enter", t.tid))
        # brand new components
        known = {c for t in tr.tracks.values() if t.present for c in t.cells}
        for col, cells in cur:
            if a is not None and (cells == a.cells or col == a.color):
                continue
            if not cells & known:
                matched = any(t.color == col and t.cells == cells for t in tr.tracks.values())
                if not matched:
                    nt = Track(self._new_tid(), col, min(cells), cells)
                    tr.tracks[nt.tid] = nt
        self._prev_grid = grid


# ---------------- generic derived structure ----------------


def reachable(
    grid: tuple[tuple[int, ...], ...], start: Cell, treat_open: frozenset[Cell] = frozenset()
) -> set[Cell]:
    h, w = len(grid), len(grid[0])
    seen, stack = {start}, [start]
    while stack:
        y, x = stack.pop()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and (ny, nx) not in seen:
                if grid[ny][nx] == 0 or (ny, nx) in treat_open:
                    seen.add((ny, nx))
                    stack.append((ny, nx))
    return seen


def barrier_candidates(trace: TrackedTrace) -> list[str]:
    """Blocking single/multi-cell tracks whose removal expands reachability.

    Purely structural: no colour or name conventions.
    """
    obs = trace.obs_log[-1]
    grid = [list(r) for r in obs.grid]
    ay, ax = trace.agent_pos
    grid[ay][ax] = 0
    g = tuple(tuple(r) for r in grid)
    base = reachable(g, (ay, ax))
    out: list[tuple[int, str]] = []
    for t in trace.tracks.values():
        if t.tid == trace.agent_track or not t.present or len(t.cells) > 2:
            continue
        gain = len(reachable(g, (ay, ax), treat_open=t.cells)) - len(base) - len(t.cells)
        if gain > 0:
            # must be on the frontier of the reachable set
            if any(
                (c[0] + dy, c[1] + dx) in base
                for c in t.cells
                for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0))
            ):
                out.append((gain, t.tid))
    out.sort(reverse=True)
    return [tid for _, tid in out]


def encoded_state(trace: TrackedTrace, upto_step: int) -> tuple[object, ...]:
    """Auto-derived observational state signature at a step: presence vector of
    all tracks + agent-adjacent relations. No hand-picked variables."""
    pres = []
    for tid in sorted(trace.tracks):
        t = trace.tracks[tid]
        pres.append((tid, t.present or t.occluded))
    return tuple(pres)


def path_to(
    grid: tuple[tuple[int, ...], ...], start: Cell, target: Cell, stop_adjacent: bool
) -> list[PrimitiveAction] | None:
    """BFS path over empty cells; optionally stop adjacent to target."""
    from collections import deque

    h, w = len(grid), len(grid[0])
    goals = (
        {target}
        if not stop_adjacent
        else {
            (target[0] + dy, target[1] + dx)
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0))
            if 0 <= target[0] + dy < h
            and 0 <= target[1] + dx < w
            and grid[target[0] + dy][target[1] + dx] == 0
        }
    )
    if start in goals:
        return []
    q = deque([start])
    prev: dict[Cell, tuple[Cell, PrimitiveAction]] = {}
    seen = {start}
    while q:
        y, x = q.popleft()
        for a, (dy, dx) in (("R", (0, 1)), ("L", (0, -1)), ("D", (1, 0)), ("U", (-1, 0))):
            ny, nx = y + dy, x + dx
            if not (0 <= ny < h and 0 <= nx < w) or (ny, nx) in seen:
                continue
            if grid[ny][nx] != 0 and (ny, nx) != target:
                continue
            if grid[ny][nx] != 0 and stop_adjacent:
                continue
            prev[(ny, nx)] = ((y, x), a)
            if (ny, nx) in goals:
                path = [a]
                cur = (y, x)
                while cur != start:
                    cur, pa = prev[cur]
                    path.append(pa)
                return list(reversed(path))
            seen.add((ny, nx))
            q.append((ny, nx))
    return None
