"""Agent-visible contracts for the code domain.

The debugger observes ONLY what a shell gives it: command exit codes, stdout,
stderr, durations, and the set of paths it can list. It never reads the fault
injector, and (in this milestone) it does not read repository source — the
claim under test is that the elimination runtime can localise a cause from
command outcomes alone.

Outcome labels reuse the IR's vocabulary: a passing target test is "pass",
a failing one is "blocked". This is a naming alias, not a semantic change.
"""

from __future__ import annotations

from dataclasses import dataclass

from rift.hypothesis import register_event_kinds

# Code-domain generic event vocabulary.
register_event_kinds("applied", "run", "absent")

PASS = "pass"
FAIL = "blocked"

# Structured intervention kinds the agent is permitted to perform.
#   env      : run the target with an environment variable defined
#   unsetenv : run the target with an ambient variable removed
#   clear : delete a discovered state path before running
#   first : run another collected test in the same process first
#   (retries are not an intervention; they are counted by the IR)
INTERVENTION_KINDS = ("env", "unsetenv", "clear", "first", "firstset")

# Localisation statuses.
#   identified                  : one behavioural class survives and it names a handle
#   unexplained_by_representation : one class survives and it is "nothing here helps"
#   underdetermined             : two or more behavioural classes remain live
#   censored                    : the budget ran out before either could be decided
#
# `unexplained_by_representation` is a statement about THIS runtime's action
# space, never about the repository's source. The distinction is load-bearing:
# the action space covers environment variables, state directories and test
# ordering, so a real cause outside it (a missing binary, a dependency version,
# locale, timezone, parallelism) produces exactly the same empty result as a
# genuine source defect. Reporting the two identically is the overclaim this
# vocabulary exists to prevent.
IDENTIFIED = "identified"
UNEXPLAINED_BY_REPRESENTATION = "unexplained_by_representation"
UNDERDETERMINED = "underdetermined"
CENSORED = "censored"
STATUSES = (IDENTIFIED, UNEXPLAINED_BY_REPRESENTATION, UNDERDETERMINED, CENSORED)


@dataclass(frozen=True)
class Intervention:
    kind: str
    arg: str

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.arg}"


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float

    target: str | None = None

    @property
    def outcome(self) -> str:
        """Verdict for the target test specifically.

        Reading the process exit code instead is wrong whenever a probe runs
        other tests before the target: their failures would be attributed to
        the target. Falls back to the exit code only if the target produced no
        report line (e.g. collection error).
        """
        if self.target:
            for line in self.stdout.splitlines():
                verdict, _, rest = line.strip().partition(" ")
                # a FAILED line carries a trailing " - <reason>" the node id lacks
                if rest == self.target or rest.startswith(self.target + " - "):
                    if verdict == "PASSED":
                        return PASS
                    if verdict in ("FAILED", "ERROR"):
                        return FAIL
        return PASS if self.exit_code == 0 else FAIL


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    """Every command and every second is charged. LLM tokens are charged
    separately so the proposal cost of a run is visible."""

    max_commands: int = 40
    max_seconds: float = 180.0
    commands: int = 0
    seconds: float = 0.0
    llm_tokens: int = 0

    def charge(self, duration_s: float) -> None:
        self.commands += 1
        self.seconds += duration_s
        if self.commands > self.max_commands or self.seconds > self.max_seconds:
            raise BudgetExceeded(f"commands={self.commands} seconds={self.seconds:.1f}")


@dataclass
class Diagnosis:
    """Result of localisation.

    `cause` is None when no environmental hypothesis survives. That is a
    negative result about the handles this runtime can pull, not a positive
    finding about the source: it says "nothing I can intervene on accounts for
    this failure", which is compatible both with a source defect and with a
    real environmental cause that has no handle here. Status
    `unexplained_by_representation` records the negative result as such and
    leaves the source question open for evidence that can actually decide it.
    """

    status: str  # one of STATUSES
    cause: Intervention | None
    hypothesis: dict | None
    surviving_classes: int
    commands_used: int
    seconds_used: float
    flaky: bool = False
    notes: list[str] | None = None
