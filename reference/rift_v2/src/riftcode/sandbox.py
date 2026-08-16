"""Sandboxed execution boundary.

A sandbox is a disposable copy of a repository. `fresh()` starts a new
*episode* — the code-domain equivalent of the gridworld episode reset that
made possession and time-based causes distinguishable. Without cheap resets,
"it passes now" is unfalsifiable, so this is load-bearing, not convenience.

Only a whitelisted command shape is executable (pytest on collected node ids).
Nothing here reads the fault injector.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from riftcode.contracts import Budget, CommandResult, Intervention


class Sandbox:
    def __init__(self, template: Path, budget: Budget):
        self.template = Path(template)
        self.budget = budget
        self.root: Path = Path(tempfile.mkdtemp(prefix="riftcode_"))
        self.episode = 0
        self._target: str | None = None
        self._materialise()

    def _materialise(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
        shutil.copytree(
            self.template, self.root, ignore=shutil.ignore_patterns(".git", "*.egg-info")
        )

    def fresh(self) -> None:
        """New episode: discard all accumulated runtime state."""
        self.episode += 1
        self._materialise()

    def dispose(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    # ---------------- observation ----------------

    def list_paths(self, max_depth: int = 3, cap: int = 400) -> list[str]:
        """Bounded listing: real repositories have too many files to enumerate,
        so state-looking paths are sought near the top of the tree."""
        out: list[str] = []
        stack = [(self.root, 0)]
        while stack and len(out) < cap:
            d, depth = stack.pop()
            try:
                entries = sorted(d.iterdir())
            except OSError:
                continue
            for p in entries:
                rel = p.relative_to(self.root).as_posix()
                out.append(rel + ("/" if p.is_dir() else ""))
                if p.is_dir() and depth + 1 < max_depth:
                    stack.append((p, depth + 1))
        return out

    def collect(self) -> list[str]:
        res = self._exec(["-q", "--collect-only"])
        ids = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if "::" in line and not line.startswith("<"):
                ids.append(line.split(" ")[0])
        return ids

    # ---------------- action ----------------

    def run_target(
        self,
        target: str,
        interventions: tuple[Intervention, ...] = (),
        repeats: int = 1,
    ) -> CommandResult:
        """Apply interventions, then run the target test `repeats` times in the
        current episode. Returns the LAST run's result; each run is charged."""
        self._target = target
        env_extra: dict[str, str] = {}
        env_drop: list[str] = []
        node_prefix: list[str] = []
        for iv in interventions:
            if iv.kind == "env":
                env_extra[iv.arg] = "1"
            elif iv.kind == "unsetenv":
                env_drop.append(iv.arg)
            elif iv.kind == "clear":
                for victim in sorted(self.root.rglob(iv.arg)):
                    if victim.is_dir():
                        shutil.rmtree(victim, ignore_errors=True)
                    elif victim.exists():
                        victim.unlink()
            elif iv.kind == "first":
                node_prefix.append(iv.arg)
            elif iv.kind == "firstset":
                node_prefix.extend(iv.arg.split(","))
        last: CommandResult | None = None
        for _ in range(max(1, repeats)):
            last = self._exec(
                ["-q", "--tb=short", "-rA", *node_prefix, target], env_extra, env_drop
            )
        assert last is not None
        return last

    def _exec(
        self,
        args: list[str],
        env_extra: dict[str, str] | None = None,
        env_drop: list[str] | None = None,
    ) -> CommandResult:
        argv = (sys.executable, "-m", "pytest", "-p", "no:randomly", *args)
        env = dict(os.environ)
        env.pop("PYTEST_CURRENT_TEST", None)
        for k in env_drop or []:
            env.pop(k, None)
        # the sandbox copy's own sources must win over any editable install
        src = self.root / "src"
        if src.is_dir():
            env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
        env.update(env_extra or {})
        t0 = time.time()
        proc = subprocess.run(
            argv, cwd=self.root, env=env, capture_output=True, text=True, timeout=60
        )
        dt = time.time() - t0
        self.budget.charge(dt)
        return CommandResult(argv, proc.returncode, proc.stdout, proc.stderr, dt, self._target)
