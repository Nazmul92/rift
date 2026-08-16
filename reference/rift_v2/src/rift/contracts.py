"""Agent-visible contracts.

This module defines the ONLY data types that may cross the boundary from
an environment to the agent. Nothing here may carry semantic labels,
hidden state, or generator configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

PrimitiveAction = str  # one of ACTIONS
ACTIONS: tuple[PrimitiveAction, ...] = ("L", "R", "U", "D", "S")

DELTAS: dict[PrimitiveAction, tuple[int, int]] = {
    "L": (0, -1),
    "R": (0, 1),
    "U": (-1, 0),
    "D": (1, 0),
    "S": (0, 0),
}


@dataclass(frozen=True)
class PublicObservation:
    """Rendered integer grid plus step index. Nothing else."""

    grid: tuple[tuple[int, ...], ...]
    step_index: int

    def serialize(self) -> dict[str, object]:
        return {"grid": [list(r) for r in self.grid], "step_index": self.step_index}


@dataclass(frozen=True)
class StepResult:
    observation: PublicObservation
    reward: float
    terminated: bool

    def serialize(self) -> dict[str, object]:
        return {
            "observation": self.observation.serialize(),
            "reward": self.reward,
            "terminated": self.terminated,
        }


class PublicEnvironment(Protocol):
    def reset(self, seed: int) -> PublicObservation: ...

    def step(self, action: PrimitiveAction) -> StepResult: ...

    def actions(self) -> tuple[PrimitiveAction, ...]: ...
