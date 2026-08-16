"""Public session boundary.

The agent receives an OpaqueSession, which holds the private environment in a
closure-private attribute and exposes only the PublicEnvironment protocol.
Nothing semantic crosses: observations are rendered grids; step results carry
reward and termination only. No `info` dict exists.
"""

from __future__ import annotations

from rift.contracts import PrimitiveAction, PublicObservation, StepResult


class OpaqueSession:
    def __init__(self, private_env: object):
        # stored name-mangled; tests assert agents never receive the private env
        self.__private = private_env

    def reset(self, seed: int) -> PublicObservation:
        return self.__private.reset(seed)  # type: ignore[attr-defined]

    def step(self, action: PrimitiveAction) -> StepResult:
        return self.__private.step(action)  # type: ignore[attr-defined]

    def actions(self) -> tuple[PrimitiveAction, ...]:
        return self.__private.actions()  # type: ignore[attr-defined]
