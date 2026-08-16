"""Durable contracts and the evidence ledger.

The ledger is the ONLY durable execution state. Everything the runtime needs to
know about a task after an interruption — which gate phases completed, what the
baseline signature was, how much budget was spent, whether a receipt was issued
— is reduced from the event stream by :func:`reduce`. There is no `state.json`,
no checkpoint database and no second log. If an event is not on disk, the
transition it describes did not happen.

Records are immutable dataclasses with hand-written strict validators. Unknown
fields are rejected rather than ignored: a field the runtime does not
understand is evidence that the file was written by something else.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shlex
import sys
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------
# canonical serialisation and hashing
# --------------------------------------------------------------------------


def canonical(obj: Any) -> str:
    """Canonical JSON: sorted keys, stable separators, UTF-8, no NaN."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_hash(obj: Any) -> str:
    data = obj if isinstance(obj, bytes) else canonical(obj).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def short(h: str, n: int = 12) -> str:
    return h[:n]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ValidationError(ValueError):
    """A durable record or external input failed validation."""


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValidationError(msg)


def strict_fields(raw: dict[str, Any], allowed: Iterable[str], what: str) -> None:
    extra = set(raw) - set(allowed)
    require(not extra, f"{what}: unknown field(s) {sorted(extra)}")


# --------------------------------------------------------------------------
# enums
# --------------------------------------------------------------------------


class Verb(StrEnum):
    VERIFY = "verify"
    WHY = "why"
    FIX = "fix"


class ClaimType(StrEnum):
    CHANGE = "change"
    PRESERVATION = "preservation"


class RunnerKind(StrEnum):
    PYTEST = "pytest"


class Outcome(StrEnum):
    """Normalised result of running one check.

    Only PASSED and FAILED carry evidence about the target's behaviour. The
    remaining values are measurement failures: they say the runtime could not
    observe the target, which can never satisfy a change check.
    """

    PASSED = "passed"
    FAILED = "failed"
    COLLECTION_ERROR = "collection_error"
    TIMEOUT = "timeout"
    MISSING_RUNNER = "missing_runner"
    INFRASTRUCTURE = "infrastructure"

    @property
    def is_evidence(self) -> bool:
        return self in (Outcome.PASSED, Outcome.FAILED)


class IsolationLevel(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class GatePhase(StrEnum):
    BASELINE = "baseline"
    CANDIDATE = "candidate"
    WITHDRAWAL = "withdrawal"
    REAPPLY = "reapply"
    PRESERVATION = "preservation"


class Verdict(StrEnum):
    """The frozen verdict vocabulary (design §9). No bare `verified` exists."""

    VERIFIED_AGAINST_APPROVED_CHECKS = "verified_against_approved_checks"
    DIAGNOSIS_SUPPORTED = "diagnosis_supported"
    UNDERDETERMINED = "underdetermined"
    REPRESENTATION_INADEQUATE = "representation_inadequate"
    REGRESSION_BLOCKED = "regression_blocked"
    UNVERIFIABLE = "unverifiable"
    INFRASTRUCTURE_BLOCKED = "infrastructure_blocked"


class Support(StrEnum):
    """How a diagnosis is supported.

    `interventional` means the cause was applied and withdrawn under the gate.
    `observational` means an executable assertion supports the finding but no
    safe apply/withdraw intervention exists for it — a missing binary cannot be
    installed and uninstalled by this runtime. An observational finding is a
    diagnosis, never a verified fix.
    """

    INTERVENTIONAL = "interventional"
    OBSERVATIONAL = "observational"


class GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class Primitive(StrEnum):
    """The closed set of safe intervention primitives (design §8).

    `propose_handles` may only return compositions of these. A novel executable
    primitive is rejected: the model requests a measurement, and the kernel
    decides whether it can be performed at all.
    """

    ENV = "env"
    UNSETENV = "unsetenv"
    CLEAR = "clear"
    FIRST = "first"
    FIRSTSET = "firstset"
    FILE_ASSERT = "file_assert"
    DEP_ASSERT = "dep_assert"

    @property
    def is_intervention(self) -> bool:
        """Assertions observe; they do not change the world.

        A cause whose only handle is an assertion can be detected but never
        gated, because there is nothing to withdraw. That branch produces an
        observational diagnosis, not a verified fix.
        """
        return self not in (Primitive.FILE_ASSERT, Primitive.DEP_ASSERT)


class Phase(StrEnum):
    """Derived execution phase — never stored, always reduced."""

    INIT = "init"
    AUTHORIZED = "authorized"
    FROZEN = "frozen"
    BASELINE = "baseline"
    CANDIDATE = "candidate"
    WITHDRAWAL = "withdrawal"
    REAPPLY = "reapply"
    PRESERVATION = "preservation"
    RECEIPT_PENDING = "receipt_pending"
    TERMINAL = "terminal"


# --------------------------------------------------------------------------
# failure signatures
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Signature:
    """The identity of a failure, not merely its existence.

    A check that is expected to fail with `AssertionError` and instead fails
    with `ImportError` has not reproduced: the R1 instrumentation bug in the
    reference prototype was exactly this class of error, where a failure's
    existence was mistaken for evidence about its cause.
    """

    exception_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"exception_type": self.exception_type, "message": self.message}

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Signature:
        strict_fields(raw, ("exception_type", "message"), "signature")
        require(isinstance(raw.get("exception_type"), str), "signature.exception_type must be a string")
        require(isinstance(raw.get("message"), str), "signature.message must be a string")
        return Signature(raw["exception_type"], raw["message"])

    def matches(self, other: Signature | None) -> bool:
        if other is None:
            return False
        return self.exception_type == other.exception_type and self.message == other.message

    def render(self) -> str:
        return f"{self.exception_type}: {self.message}" if self.message else self.exception_type


# --------------------------------------------------------------------------
# hypothesis IR — the contract a model proposal must satisfy
#
# This lives in records.py, not in the kernel, because it is the boundary
# both sides agree on: llm.py validates a proposal against it before the
# proposal is allowed to exist as typed data, and kernel.py executes only
# structures that already passed it. Neither module imports the other.
# --------------------------------------------------------------------------


MAX_DEPTH = 4
MAX_OPS = 32
MAX_LATENTS = 4
MAX_COUNTER = 16
MAX_ROLES = 16
EVENT_KINDS = frozenset({"applied", "run", "absent"})
LATENT_TYPES = frozenset({"bool", "counter"})

PASS = "pass"
FAIL = "blocked"

# Every IR object type declares its complete field set. Validation is
# closed-by-default at every nesting level: a field that is not listed here is
# rejected wherever it appears, without anyone having to anticipate it. The
# property under test is "closed unless explicitly allowed", not "rejects the
# smuggling attempts we already thought of".
CLOSED_FIELDS: dict[str, frozenset[str]] = {
    "hypothesis": frozenset({"hypothesis_id", "roles", "target_role", "latents", "condition"}),
    "latent.bool": frozenset({"name", "type", "init", "set_on", "reset_on"}),
    "latent.counter": frozenset({"name", "type", "max", "inc_on"}),
    "event": frozenset({"event", "role"}),
    "pred.const": frozenset({"const"}),
    "pred.var": frozenset({"var"}),
    "pred.ge": frozenset({"op", "var", "value"}),
    "pred.and": frozenset({"op", "args"}),
    "pred.or": frozenset({"op", "args"}),
    "pred.not": frozenset({"op", "arg"}),
}

# Model confidence is refused as an *input* at every level, so no later code can
# begin reading it by accident. This is belt-and-braces: these names are already
# absent from every closed field set above.
BANNED_FIELDS = frozenset({"confidence", "certainty", "probability", "score", "likelihood"})


class IRValidationError(ValueError):
    """A hypothesis is not expressible in the IR.

    Never a statement about the repository — only about the proposal.
    """


def _is_int(value: Any) -> bool:
    """`bool` is a subclass of `int` in Python, so `isinstance(True, int)` is
    True. A count is a count; `True` is not 1 here."""
    return isinstance(value, int) and not isinstance(value, bool)


def _closed(obj: Any, node: str, path: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise IRValidationError(f"{path}: expected an object for {node}")
    allowed = CLOSED_FIELDS[node]
    for key in obj:
        if not isinstance(key, str):
            raise IRValidationError(f"{path}: field names must be strings")
        if key in BANNED_FIELDS:
            raise IRValidationError(f"{path}.{key}: model confidence is not an accepted input at any level")
        if key not in allowed:
            raise IRValidationError(f"{path}.{key}: unknown field for {node}")
    return obj


def _validate_event(e: Any, roles: list[str], path: str) -> None:
    obj = _closed(e, "event", path)
    kind = obj.get("event")
    if kind not in EVENT_KINDS:
        raise IRValidationError(f"{path}.event: {kind!r} is not a generic event kind")
    role = obj.get("role", None)
    if role is not None:
        if not isinstance(role, str):
            raise IRValidationError(f"{path}.role: must be a string or null")
        if role not in roles:
            raise IRValidationError(f"{path}.role: {role!r} is not a declared role")


def _validate_predicate(p: Any, declared: set[str], depth: int, path: str) -> int:
    """Recursive, closed, fail-closed. Returns the operation count consumed."""
    if depth > MAX_DEPTH:
        raise IRValidationError(f"{path}: condition nests deeper than {MAX_DEPTH}")
    if not isinstance(p, dict):
        raise IRValidationError(f"{path}: predicate must be an object")

    if "const" in p:
        obj = _closed(p, "pred.const", path)
        if not isinstance(obj["const"], bool):
            raise IRValidationError(f"{path}.const: must be a boolean")
        return 1

    op = p.get("op")
    if op is None:
        obj = _closed(p, "pred.var", path)
        var = obj.get("var")
        if not isinstance(var, str):
            raise IRValidationError(f"{path}.var: must be a string")
        if var not in declared:
            raise IRValidationError(f"{path}.var: {var!r} is not a declared latent")
        return 1

    if not isinstance(op, str):
        raise IRValidationError(f"{path}.op: must be a string")

    if op == "ge":
        obj = _closed(p, "pred.ge", path)
        var = obj.get("var")
        if not isinstance(var, str):
            raise IRValidationError(f"{path}.var: must be a string")
        if var not in declared:
            raise IRValidationError(f"{path}.var: {var!r} is not a declared latent")
        if not _is_int(obj.get("value")):
            raise IRValidationError(f"{path}.value: must be an integer (booleans are not counts)")
        if not (0 <= obj["value"] <= MAX_COUNTER):
            raise IRValidationError(f"{path}.value: out of range")
        return 1

    if op in ("and", "or"):
        obj = _closed(p, f"pred.{op}", path)
        args = obj.get("args")
        if not isinstance(args, list) or len(args) != 2:
            raise IRValidationError(f"{path}.args: {op} requires exactly two arguments")
        return 1 + sum(_validate_predicate(a, declared, depth + 1, f"{path}.args[{i}]") for i, a in enumerate(args))

    if op == "not":
        obj = _closed(p, "pred.not", path)
        return 1 + _validate_predicate(obj.get("arg"), declared, depth + 1, f"{path}.arg")

    raise IRValidationError(f"{path}.op: unknown operator {op!r}")


def _validate_latent(la: Any, roles: list[str], path: str) -> str:
    if not isinstance(la, dict):
        raise IRValidationError(f"{path}: latent must be an object")
    ty = la.get("type")
    if ty not in LATENT_TYPES:
        raise IRValidationError(f"{path}.type: unknown latent type {ty!r}")
    obj = _closed(la, f"latent.{ty}", path)
    name = obj.get("name")
    if not isinstance(name, str) or not name:
        raise IRValidationError(f"{path}.name: must be a non-empty string")
    if ty == "bool":
        if "init" in obj and not isinstance(obj["init"], bool):
            raise IRValidationError(f"{path}.init: must be a boolean")
        _validate_event(obj.get("set_on"), roles, f"{path}.set_on")
        if obj.get("reset_on") is not None:
            _validate_event(obj["reset_on"], roles, f"{path}.reset_on")
    else:
        _validate_event(obj.get("inc_on"), roles, f"{path}.inc_on")
        if not _is_int(obj.get("max")):
            raise IRValidationError(f"{path}.max: must be an integer (booleans are not counts)")
        if not (1 <= obj["max"] <= MAX_COUNTER):
            raise IRValidationError(f"{path}.max: out of range")
    return name


def validate_hypothesis(h: Any) -> None:
    """One recursive, fail-closed validator for the whole IR.

    A model proposal enters the kernel only after passing this, as typed data
    with no residual prose and no unexpected fields at any depth. Variable
    references are resolved against the latents the hypothesis actually
    declares, so a condition cannot read state that was never defined.
    """
    obj = _closed(h, "hypothesis", "hypothesis")

    if not isinstance(obj.get("hypothesis_id"), str) or not obj["hypothesis_id"]:
        raise IRValidationError("hypothesis.hypothesis_id: must be a non-empty string")

    roles = obj.get("roles")
    if not isinstance(roles, list) or not roles:
        raise IRValidationError("hypothesis.roles: must be a non-empty list")
    if len(roles) > MAX_ROLES:
        raise IRValidationError(f"hypothesis.roles: at most {MAX_ROLES} roles")
    if not all(isinstance(r, str) and r for r in roles):
        raise IRValidationError("hypothesis.roles: every role must be a non-empty string")
    if len(set(roles)) != len(roles):
        raise IRValidationError("hypothesis.roles: role names must be unique")
    if obj.get("target_role") not in roles:
        raise IRValidationError("hypothesis.target_role: must be one of roles")

    lats = obj.get("latents", [])
    if not isinstance(lats, list):
        raise IRValidationError("hypothesis.latents: must be a list")
    if len(lats) > MAX_LATENTS:
        raise IRValidationError(f"hypothesis.latents: at most {MAX_LATENTS} latents")

    declared: set[str] = set()
    ops = 0
    for i, la in enumerate(lats):
        name = _validate_latent(la, roles, f"hypothesis.latents[{i}]")
        if name in declared:
            raise IRValidationError(f"hypothesis.latents[{i}].name: duplicate latent {name!r}")
        declared.add(name)
        ops += 1

    if "condition" not in obj:
        raise IRValidationError("hypothesis.condition: required")
    ops += _validate_predicate(obj["condition"], declared, 0, "hypothesis.condition")

    if ops > MAX_OPS:
        raise IRValidationError(f"hypothesis: {ops} operations exceeds the budget of {MAX_OPS}")


# --------------------------------------------------------------------------
# handles and diagnosis
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Handle:
    """One pullable lever on the environment.

    A handle is data, never a command. The kernel compiles an accepted handle
    into an argv array or an env-dict edit; the model never sees a shell and
    cannot introduce a primitive that isn't in :class:`Primitive`.
    """

    kind: Primitive
    arg: str

    @property
    def label(self) -> str:
        return f"{self.kind.value}:{self.arg}"

    @property
    def is_intervention(self) -> bool:
        return self.kind.is_intervention

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "arg": self.arg}

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Handle:
        strict_fields(raw, ("kind", "arg"), "handle")
        kind = raw.get("kind")
        if not isinstance(kind, str):
            raise ValidationError("handle.kind must be a string")
        require(kind in tuple(Primitive), f"handle.kind {kind!r} is not an approved primitive")
        arg = raw.get("arg")
        if not isinstance(arg, str) or arg.strip() == "":
            raise ValidationError("handle.arg must be a non-empty string")
        require(len(arg) <= 512, "handle.arg is too long")
        # A handle argument is a name or a repository-relative path. Anything
        # that could become a command, a traversal, or an absolute path is not
        # a handle the kernel will compile.
        for bad in ("\n", "\r", "\0", ";", "|", "&", "$", "`", ">", "<"):
            require(bad not in arg, f"handle.arg contains a shell metacharacter: {bad!r}")
        require(".." not in arg.split("/"), "handle.arg contains a parent-directory traversal")
        require(not arg.startswith("/"), "handle.arg must not be an absolute path")
        return Handle(Primitive(kind), arg)


@dataclass(frozen=True)
class ReproductionContract:
    """How to reproduce one failure, frozen before any patch exists.

    The bare-target gate can only judge a target that fails when run alone. That
    excludes the entire class this project was built for: an order-dependent
    failure passes in isolation by definition, so its baseline never reproduces
    and no patch for it can ever be verified. Calibration case C4 was scored an
    abstention on exactly that limitation, and the limitation was mistaken for
    task truth.

    This record closes that gap by making the *preconditions* part of the frozen
    judge. `first:tests/test_a_pollute.py → tests/test_target.py::test_clean →
    signature S` is a reproducer; the gate runs it identically in all five
    phases, so a patch that removes the ordering dependence is gateable.

    The kernel selects it from executed evidence. Model output can neither
    create it, modify it, nor weaken it — the model never sees it and there is
    no path from a proposal to these fields.
    """

    preconditions: tuple[Handle, ...]
    node_id: str
    signature: Signature
    runner_config_hash: str
    tree_digest: str
    supporting_event_ids: tuple[str, ...]
    judge_artifacts: tuple[tuple[str, str], ...] = ()
    reset_untracked: bool = True
    repeats: int = 1

    @property
    def content_hash(self) -> str:
        return content_hash(self.to_dict())

    def render(self) -> str:
        chain = " → ".join([*(h.label for h in self.preconditions), self.node_id])
        return f"{chain} → {self.signature.render()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "preconditions": [h.to_dict() for h in self.preconditions],
            "node_id": self.node_id,
            "signature": self.signature.to_dict(),
            "runner_config_hash": self.runner_config_hash,
            "tree_digest": self.tree_digest,
            "supporting_event_ids": list(self.supporting_event_ids),
            "judge_artifacts": [list(pair) for pair in self.judge_artifacts],
            "reset_untracked": self.reset_untracked,
            "repeats": self.repeats,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> ReproductionContract:
        allowed = (
            "preconditions",
            "node_id",
            "signature",
            "runner_config_hash",
            "tree_digest",
            "supporting_event_ids",
            "judge_artifacts",
            "reset_untracked",
            "repeats",
        )
        strict_fields(raw, allowed, "reproduction_contract")
        node_id = raw.get("node_id")
        require(isinstance(node_id, str) and bool(node_id), "reproduction_contract.node_id is required")
        repeats = raw.get("repeats", 1)
        require(isinstance(repeats, int) and not isinstance(repeats, bool), "repeats must be an integer")
        require(1 <= repeats <= 16, "repeats out of range")
        preconditions = tuple(Handle.from_dict(h) for h in raw.get("preconditions", []))
        # Only interventions can be part of a reproducer. An assertion observes;
        # applying one changes nothing, so a reproducer built on one would be
        # identical to the bare target while claiming to be more.
        for handle in preconditions:
            require(
                handle.is_intervention,
                f"reproduction_contract precondition {handle.label} is an assertion, not an intervention",
            )
        require(len(preconditions) <= 8, "at most 8 preconditions")
        return ReproductionContract(
            preconditions=preconditions,
            node_id=str(node_id),
            signature=Signature.from_dict(raw["signature"]),
            runner_config_hash=str(raw["runner_config_hash"]),
            tree_digest=str(raw["tree_digest"]),
            supporting_event_ids=tuple(str(e) for e in raw.get("supporting_event_ids", [])),
            judge_artifacts=tuple((str(a), str(b)) for a, b in raw.get("judge_artifacts", [])),
            reset_untracked=bool(raw.get("reset_untracked", True)),
            repeats=repeats,
        )


@dataclass(frozen=True)
class Diagnosis:
    """What the evidence supports about a failure's cause."""

    status: Verdict
    support: Support | None
    gate: GateStatus
    causes: tuple[Handle, ...]
    surviving_classes: int
    contradicted: tuple[str, ...]
    notes: tuple[str, ...]
    remediation_unverified: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "support": self.support.value if self.support else None,
            "gate": self.gate.value,
            "causes": [c.to_dict() for c in self.causes],
            "surviving_classes": self.surviving_classes,
            "contradicted": list(self.contradicted),
            "notes": list(self.notes),
            "remediation_unverified": self.remediation_unverified,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Diagnosis:
        allowed = (
            "status",
            "support",
            "gate",
            "causes",
            "surviving_classes",
            "contradicted",
            "notes",
            "remediation_unverified",
        )
        strict_fields(raw, allowed, "diagnosis")
        support = raw.get("support")
        require(support is None or isinstance(support, str), "diagnosis.support must be a string or null")
        return Diagnosis(
            status=Verdict(raw["status"]),
            support=Support(support) if support else None,
            gate=GateStatus(raw["gate"]),
            causes=tuple(Handle.from_dict(c) for c in raw.get("causes", [])),
            surviving_classes=int(raw.get("surviving_classes", 0)),
            contradicted=tuple(str(c) for c in raw.get("contradicted", [])),
            notes=tuple(str(n) for n in raw.get("notes", [])),
            remediation_unverified=str(raw.get("remediation_unverified", "")),
        )


@dataclass(frozen=True)
class ModelUsage:
    """Provider-reported usage. Never estimated.

    `unknown` is a first-class value: a provider that returns no usage block
    produces a receipt saying `unknown`, not a plausible-looking number.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def known(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None

    def to_dict(self) -> dict[str, Any]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}

    def render(self) -> str:
        if not self.known:
            return "unknown"
        return f"{self.input_tokens} in / {self.output_tokens} out"

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> ModelUsage:
        strict_fields(raw, ("input_tokens", "output_tokens"), "usage")
        return ModelUsage(raw.get("input_tokens"), raw.get("output_tokens"))


def canonical_diff(text: str) -> str:
    r"""Normalise a unified diff's line endings and final newline.

    `git apply` requires the last line to end with a newline and reports its
    absence as `corrupt patch at line N`, which reads like a malformed hunk and
    is not one. Live calibration lost four of five cases to this single missing
    byte.

    This is canonicalisation, not repair. Appending a terminator cannot change
    what any hunk does — a file that genuinely lacks a trailing newline is
    represented by the explicit ``\ No newline at end of file`` marker, which is
    itself newline-terminated. CRLF is normalised for the same reason: the bytes
    must mean the same thing to `git apply` on every platform.

    It runs at ingestion, before the patch is hashed and stored, so the bytes
    that are recorded are the bytes that are applied and reapplied.
    """
    if not text:
        return text
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalised if normalised.endswith("\n") else normalised + "\n"


# What a handoff archive may contain. Local state, caches, build output and
# credentials are excluded by construction rather than by whoever remembers to
# clean the directory before zipping it.
ARCHIVE_EXCLUDE_DIRS = frozenset(
    {".git", ".rift", "build", "dist", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", "work"}
)
ARCHIVE_EXCLUDE_NAMES = frozenset({".env", ".env.local"})
ARCHIVE_EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".key", ".pem", ".log"})
ARCHIVE_INCLUDE_SUFFIXES = frozenset({".py", ".toml", ".txt", ".ini", ".cfg", ".md", ".diff", ".json", ".sh"})


def archive_manifest(repo_root: Path) -> list[str]:
    """Repository-relative paths a release archive may contain.

    One rule, used by both the packaging step and the test that guards it. Two
    rules would eventually disagree, and the disagreement would ship.
    """
    root = Path(repo_root).resolve()
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if set(rel.parts) & ARCHIVE_EXCLUDE_DIRS:
            continue
        if rel.name in ARCHIVE_EXCLUDE_NAMES or path.suffix in ARCHIVE_EXCLUDE_SUFFIXES:
            continue
        if any(part.endswith(".egg-info") for part in rel.parts):
            continue
        if path.suffix not in ARCHIVE_INCLUDE_SUFFIXES:
            continue
        out.append(rel.as_posix())
    return out


# --------------------------------------------------------------------------
# spend authorization
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Pricing:
    """Frozen provider pricing, in USD per million tokens.

    Configured, never discovered: a price fetched at run time is a price that
    can change between the reservation and the charge.
    """

    input_per_mtok: float
    output_per_mtok: float
    provider: str
    model: str

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / 1_000_000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_per_mtok": self.input_per_mtok,
            "output_per_mtok": self.output_per_mtok,
            "provider": self.provider,
            "model": self.model,
        }


class BudgetRefused(RuntimeError):
    """A request was not sent because its worst case exceeds the authorization."""


def reserve_cost(pricing: Pricing, input_token_ceiling: int, max_output_tokens: int) -> float:
    """The COMPLETE worst-case cost of one request, in USD.

    Input is not free. The ceiling comes from bounded context assembly, not
    from a provider-specific tokenizer: a tokenizer would make the reservation
    depend on the very component whose behaviour is being bounded, and would
    weaken it exactly when the prompt is unusual.
    """
    require(input_token_ceiling >= 0 and max_output_tokens >= 0, "token bounds must be non-negative")
    return pricing.cost(int(input_token_ceiling), int(max_output_tokens))


@contextlib.contextmanager
def _exclusive(path: Path) -> Iterator[None]:
    """An OS file lock held across the read-decide-append sequence.

    Advisory locking is what makes the cap hold between *processes*. Without
    it, two concurrent runs each read the same remaining balance, each decide
    they can afford a request, and together exceed the authorization — the
    check would be real and the limit would not.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - released in the finally below
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def spend_ledger_path(repo_root: Path) -> Path:
    return Path(repo_root) / ".rift" / "spend.jsonl"


class SpendLedger:
    """Append-only, scope-keyed, cross-task spend authority.

    This is the only authoritative record of what has been spent. It is a
    ledger and not a counter for the same reason `.rift/tasks/*/ledger.jsonl`
    is: a mutable total can be lost, double-applied, or silently rewritten,
    and none of those are detectable afterwards.

    Three properties do the work.

    **Reserve before the request.** The reservation is appended and fsynced
    *before* the HTTP call. A process that dies mid-request leaves its
    reservation consumed, which over-charges by at most one request's worst
    case — the conservative direction. Charging afterwards would record an
    unanswered request as free, and a crash loop would spend without limit
    while the ledger showed zero.

    **Scope, not task.** Every event carries an authorization scope, normally
    a frozen run-manifest hash. The cap applies to the scope, so all tasks in a
    run share one authorization and no task can be cheap by being counted alone.

    **Idempotent settlement.** Settlement is keyed by request id, so replaying
    or resuming cannot charge the same request twice.
    """

    KINDS = ("reserved", "settled", "refused")

    def __init__(self, path: Path, scope: str, limit_usd: float, pricing: Pricing):
        require(bool(scope), "an authorization scope is required")
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(".lock")
        self.scope = scope
        self.limit_usd = float(limit_usd)
        self.pricing = pricing
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- reading -------------------------------------------------------

    def events(self) -> list[dict[str, Any]]:
        """Every event for this scope. A torn final line is dropped, exactly as
        the task ledger does: that is the shape of a crash mid-append."""
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if i == len(lines) - 1 and not line.endswith("\n"):
                break
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as exc:
                if i == len(lines) - 1:
                    break
                raise LedgerCorrupt(f"{self.path}: malformed spend event at line {i + 1}: {exc}") from exc
            if event.get("scope") == self.scope:
                out.append(event)
        return out

    def committed_usd(self, events: list[dict[str, Any]] | None = None) -> float:
        """Charged settlements plus reservations that have not settled.

        An outstanding reservation counts in full against the limit until it
        settles. That is what makes a crashed request conservative rather than
        free.
        """
        rows = self.events() if events is None else events
        settled = {r["request_id"] for r in rows if r["kind"] == "settled"}
        total = 0.0
        for row in rows:
            if row["kind"] == "settled":
                total += float(row.get("charged_usd", 0.0))
            elif row["kind"] == "reserved" and row["request_id"] not in settled:
                total += float(row.get("reserved_usd", 0.0))
        return total

    def remaining_usd(self) -> float:
        return self.limit_usd - self.committed_usd()

    # -- writing -------------------------------------------------------

    def _append(self, event: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(canonical(event) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def reserve(
        self,
        request_id: str,
        task_id: str,
        attempt: int,
        input_token_ceiling: int,
        max_output_tokens: int,
    ) -> tuple[str, float]:
        """Claim worst-case cost, or refuse. Returns (event_id, reserved_usd).

        Read, decide and append happen under one exclusive lock, so two
        processes cannot both observe the same remaining balance and both
        proceed.
        """
        amount = reserve_cost(self.pricing, input_token_ceiling, max_output_tokens)
        with _exclusive(self.lock_path):
            rows = self.events()
            remaining = self.limit_usd - self.committed_usd(rows)
            if amount > remaining + 1e-12:
                refusal = self._event(
                    "refused",
                    request_id,
                    task_id,
                    attempt,
                    reason=(
                        f"worst case ${amount:.6f} exceeds ${remaining:.6f} remaining of the "
                        f"${self.limit_usd:.2f} authorization for scope {self.scope}"
                    ),
                    reserved_usd=0.0,
                )
                self._append(refusal)
                raise BudgetRefused(refusal["reason"])
            event = self._event(
                "reserved",
                request_id,
                task_id,
                attempt,
                reserved_usd=round(amount, 8),
                input_token_ceiling=int(input_token_ceiling),
                max_output_tokens=int(max_output_tokens),
                pricing=self.pricing.to_dict(),
            )
            self._append(event)
        return event["event_id"], amount

    def settle(self, request_id: str, task_id: str, attempt: int, usage: ModelUsage) -> dict[str, Any]:
        """Charge provider-reported usage and release the remainder.

        Idempotent: a second settlement for the same request id is a no-op that
        returns the first one. Absent or malformed usage retains the FULL
        reservation — an estimate would put a number the provider never
        confirmed into the authoritative spend record, and an under-estimate is
        how a cap is exceeded while appearing to hold.
        """
        with _exclusive(self.lock_path):
            rows = self.events()
            for row in rows:
                if row["kind"] == "settled" and row["request_id"] == request_id:
                    return row
            reserved = next(
                (float(r["reserved_usd"]) for r in rows if r["kind"] == "reserved" and r["request_id"] == request_id),
                0.0,
            )
            if usage.known:
                charged = self.pricing.cost(usage.input_tokens or 0, usage.output_tokens or 0)
                source = "provider_reported"
            else:
                charged = reserved
                source = "unknown_full_reservation_retained"
            event = self._event(
                "settled",
                request_id,
                task_id,
                attempt,
                reserved_usd=round(reserved, 8),
                charged_usd=round(charged, 8),
                released_usd=round(max(0.0, reserved - charged), 8),
                usage=usage.to_dict(),
                usage_source=source,
            )
            self._append(event)
            event["remaining_usd"] = round(self.limit_usd - self.committed_usd(), 8)
            return event

    def _event(self, kind: str, request_id: str, task_id: str, attempt: int, **fields: Any) -> dict[str, Any]:
        event = {
            "schema_version": SCHEMA_VERSION,
            "kind": kind,
            "scope": self.scope,
            "request_id": request_id,
            "task_id": task_id,
            "attempt": int(attempt),
            "limit_usd": self.limit_usd,
            "ts": utc_now(),
            **fields,
        }
        event["event_id"] = content_hash({k: v for k, v in event.items() if k != "ts"})[:16]
        return event


def spend_for_task(path: Path, scope: str, task_id: str) -> dict[str, Any]:
    """Join a task's spend-event references back to the authoritative ledger.

    Receipts derive their spend figure this way rather than from numbers copied
    into the task ledger. A copied figure is a second source of truth, and the
    two can disagree.
    """
    if not Path(path).exists():
        return {"charged_usd": 0.0, "reserved_usd": 0.0, "released_usd": 0.0, "requests": 0, "unknown_usage": 0}
    charged = reserved = released = 0.0
    requests = 0
    unknown = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("scope") != scope or row.get("task_id") != task_id:
            continue
        if row["kind"] == "reserved":
            reserved += float(row.get("reserved_usd", 0.0))
            requests += 1
        elif row["kind"] == "settled":
            charged += float(row.get("charged_usd", 0.0))
            released += float(row.get("released_usd", 0.0))
            if row.get("usage_source") != "provider_reported":
                unknown += 1
    return {
        "charged_usd": round(charged, 8),
        "reserved_usd": round(reserved, 8),
        "released_usd": round(released, 8),
        "requests": requests,
        "unknown_usage": unknown,
    }


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    check_id: str
    claim_type: ClaimType
    runner: RunnerKind
    node_id: str
    expected_baseline: Outcome
    expected_candidate: Outcome
    timeout_s: float
    scope: str
    predicted_signature: Signature | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "claim_type": self.claim_type.value,
            "runner": self.runner.value,
            "node_id": self.node_id,
            "expected_baseline": self.expected_baseline.value,
            "expected_candidate": self.expected_candidate.value,
            "timeout_s": self.timeout_s,
            "scope": self.scope,
            "predicted_signature": self.predicted_signature.to_dict() if self.predicted_signature else None,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Check:
        allowed = (
            "check_id",
            "claim_type",
            "runner",
            "node_id",
            "expected_baseline",
            "expected_candidate",
            "timeout_s",
            "scope",
            "predicted_signature",
        )
        strict_fields(raw, allowed, "check")
        sig = raw.get("predicted_signature")
        return Check(
            check_id=str(raw["check_id"]),
            claim_type=ClaimType(raw["claim_type"]),
            runner=RunnerKind(raw["runner"]),
            node_id=str(raw["node_id"]),
            expected_baseline=Outcome(raw["expected_baseline"]),
            expected_candidate=Outcome(raw["expected_candidate"]),
            timeout_s=float(raw["timeout_s"]),
            scope=str(raw["scope"]),
            predicted_signature=Signature.from_dict(sig) if sig else None,
        )


@dataclass(frozen=True)
class CheckSet:
    """Frozen judge. Once hashed, a candidate patch that touches any protected
    path is rejected structurally rather than detected heuristically."""

    checks: tuple[Check, ...]
    protected_paths: tuple[str, ...]
    runner_config_hash: str
    provenance: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "protected_paths": list(self.protected_paths),
            "runner_config_hash": self.runner_config_hash,
            "provenance": self.provenance,
        }

    @property
    def content_hash(self) -> str:
        return content_hash(self.to_dict())

    def by_type(self, claim: ClaimType) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.claim_type is claim)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> CheckSet:
        strict_fields(raw, ("checks", "protected_paths", "runner_config_hash", "provenance"), "checkset")
        return CheckSet(
            checks=tuple(Check.from_dict(c) for c in raw["checks"]),
            protected_paths=tuple(str(p) for p in raw["protected_paths"]),
            runner_config_hash=str(raw["runner_config_hash"]),
            provenance=str(raw["provenance"]),
        )


# --------------------------------------------------------------------------
# change set
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeSet:
    diff: str
    touched_paths: tuple[str, ...]
    origin: str
    attempt: int = 1

    @property
    def patch_hash(self) -> str:
        return content_hash(self.diff.encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "diff": self.diff,
            "touched_paths": list(self.touched_paths),
            "origin": self.origin,
            "attempt": self.attempt,
            "patch_hash": self.patch_hash,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> ChangeSet:
        strict_fields(raw, ("diff", "touched_paths", "origin", "attempt", "patch_hash"), "changeset")
        cs = ChangeSet(
            diff=str(raw["diff"]),
            touched_paths=tuple(str(p) for p in raw["touched_paths"]),
            origin=str(raw["origin"]),
            attempt=int(raw.get("attempt", 1)),
        )
        if "patch_hash" in raw:
            require(cs.patch_hash == raw["patch_hash"], "changeset: patch_hash does not match diff bytes")
        return cs


# --------------------------------------------------------------------------
# task contract
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Budgets:
    max_commands: int = 200
    max_seconds: float = 1800.0
    command_timeout_s: float = 600.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Budgets:
        strict_fields(raw, ("max_commands", "max_seconds", "command_timeout_s"), "budgets")
        return Budgets(
            max_commands=int(raw["max_commands"]),
            max_seconds=float(raw["max_seconds"]),
            command_timeout_s=float(raw["command_timeout_s"]),
        )


@dataclass(frozen=True)
class Authorities:
    """Approval provenance. Spec approval and isolation authority are separate
    grants and are recorded separately: `--yes` approves a Spec Card and can
    never authorise weaker isolation (design §10.3)."""

    spec_approval: str = "not_applicable"
    partial_sandbox: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Authorities:
        strict_fields(raw, ("spec_approval", "partial_sandbox"), "authorities")
        return Authorities(str(raw["spec_approval"]), str(raw["partial_sandbox"]))


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    verb: Verb
    request: str
    repo_root: str
    baseline_tree_hash: str
    scope: str
    budgets: Budgets
    requested_sandbox: IsolationLevel
    actual_sandbox: IsolationLevel
    authorities: Authorities
    allow_network: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": self.task_id,
            "verb": self.verb.value,
            "request": self.request,
            "repo_root": self.repo_root,
            "baseline_tree_hash": self.baseline_tree_hash,
            "scope": self.scope,
            "budgets": self.budgets.to_dict(),
            "requested_sandbox": self.requested_sandbox.value,
            "actual_sandbox": self.actual_sandbox.value,
            "authorities": self.authorities.to_dict(),
            "allow_network": self.allow_network,
        }

    @property
    def content_hash(self) -> str:
        return content_hash(self.to_dict())

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> TaskContract:
        allowed = (
            "schema_version",
            "task_id",
            "verb",
            "request",
            "repo_root",
            "baseline_tree_hash",
            "scope",
            "budgets",
            "requested_sandbox",
            "actual_sandbox",
            "authorities",
            "allow_network",
        )
        strict_fields(raw, allowed, "contract")
        require(raw.get("schema_version") == SCHEMA_VERSION, "contract: unsupported schema_version")
        return TaskContract(
            task_id=str(raw["task_id"]),
            verb=Verb(raw["verb"]),
            request=str(raw["request"]),
            repo_root=str(raw["repo_root"]),
            baseline_tree_hash=str(raw["baseline_tree_hash"]),
            scope=str(raw["scope"]),
            budgets=Budgets.from_dict(raw["budgets"]),
            requested_sandbox=IsolationLevel(raw["requested_sandbox"]),
            actual_sandbox=IsolationLevel(raw["actual_sandbox"]),
            authorities=Authorities.from_dict(raw["authorities"]),
            allow_network=bool(raw["allow_network"]),
        )


# --------------------------------------------------------------------------
# check results and receipt
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    node_id: str
    phase: GatePhase
    outcome: Outcome
    signature: Signature | None
    duration_s: float
    exit_code: int
    detail: str = ""
    # Non-empty when the declared node could not be collected alone and was
    # observed through its containing file instead. Recorded so a widened
    # observation scope can never pass unnoticed into a receipt.
    fallback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "node_id": self.node_id,
            "phase": self.phase.value,
            "outcome": self.outcome.value,
            "signature": self.signature.to_dict() if self.signature else None,
            "duration_s": round(self.duration_s, 3),
            "exit_code": self.exit_code,
            "detail": self.detail,
            "fallback": self.fallback,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> CheckResult:
        allowed = (
            "check_id",
            "node_id",
            "phase",
            "outcome",
            "signature",
            "duration_s",
            "exit_code",
            "detail",
            "fallback",
        )
        strict_fields(raw, allowed, "check_result")
        sig = raw.get("signature")
        return CheckResult(
            check_id=str(raw["check_id"]),
            node_id=str(raw["node_id"]),
            phase=GatePhase(raw["phase"]),
            outcome=Outcome(raw["outcome"]),
            signature=Signature.from_dict(sig) if sig else None,
            duration_s=float(raw["duration_s"]),
            exit_code=int(raw["exit_code"]),
            detail=str(raw.get("detail", "")),
            fallback=str(raw.get("fallback", "")),
        )


@dataclass(frozen=True)
class VerificationReceipt:
    task_id: str
    verdict: Verdict
    reason: str
    contract_hash: str
    checkset_hash: str
    patch_hash: str
    baseline_signature: Signature | None
    results: tuple[CheckResult, ...]
    checks_not_executed: tuple[str, ...]
    sandbox: IsolationLevel
    sandbox_detail: str
    authorities: Authorities
    commands: int
    seconds: float
    tokens: str
    censored: bool
    remaining_uncertainty: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": self.task_id,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "contract_hash": self.contract_hash,
            "checkset_hash": self.checkset_hash,
            "patch_hash": self.patch_hash,
            "baseline_signature": self.baseline_signature.to_dict() if self.baseline_signature else None,
            "results": [r.to_dict() for r in self.results],
            "checks_not_executed": list(self.checks_not_executed),
            "sandbox": self.sandbox.value,
            "sandbox_detail": self.sandbox_detail,
            "authorities": self.authorities.to_dict(),
            "commands": self.commands,
            "seconds": round(self.seconds, 3),
            "tokens": self.tokens,
            "censored": self.censored,
            "remaining_uncertainty": list(self.remaining_uncertainty),
        }


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------


class EventKind(StrEnum):
    TASK_STARTED = "task_started"
    SANDBOX_PROBED = "sandbox_probed"
    SANDBOX_AUTHORIZED = "sandbox_authorized"
    CHANGESET_REGISTERED = "changeset_registered"
    CHANGESET_REJECTED = "changeset_rejected"
    CHANGESET_RELOADED = "changeset_reloaded"
    CHECKSET_FROZEN = "checkset_frozen"
    REPRODUCER_FROZEN = "reproducer_frozen"
    CONTRACT_FROZEN = "contract_frozen"
    DRIFT_DETECTED = "drift_detected"
    COMMAND_STARTED = "command_started"
    COMMAND_PROGRESS = "command_progress"
    COMMAND_FINISHED = "command_finished"
    SIGNATURE_FROZEN = "signature_frozen"
    CHECK_FALLBACK = "check_fallback"
    CHECK_RESULT = "check_result"
    EPISODE_RESET = "episode_reset"
    GATE_PHASE_FINISHED = "gate_phase_finished"
    INFRASTRUCTURE_BLOCKED = "infrastructure_blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CONTEXT_SELECTED = "context_selected"
    HANDLES_DISCOVERED = "handles_discovered"
    HYPOTHESES_PROPOSED = "hypotheses_proposed"
    PROBE_SELECTED = "probe_selected"
    HYPOTHESES_ELIMINATED = "hypotheses_eliminated"
    CAUSE_REFINED = "cause_refined"
    CAUSE_SUPPORTED = "cause_supported"
    DIAGNOSIS_EMITTED = "diagnosis_emitted"
    MODEL_REQUEST_STARTED = "model_request_started"
    MODEL_RESPONSE_RECEIVED = "model_response_received"
    MODEL_RESPONSE_INVALID = "model_response_invalid"
    MODEL_REPAIR_REQUESTED = "model_repair_requested"
    MODEL_UNAVAILABLE = "model_unavailable"
    SPEND_RESERVED = "spend_reserved"
    SPEND_SETTLED = "spend_settled"
    SPEND_REFUSED = "spend_refused"
    RECEIPT_EMITTED = "receipt_emitted"


TERMINAL_KINDS = (EventKind.RECEIPT_EMITTED,)


class LedgerCorrupt(RuntimeError):
    """A completed ledger line is malformed or the sequence is illegal.

    Fails closed: an unreadable middle event means the projection cannot be
    trusted, and guessing would reintroduce exactly the inference the ledger
    exists to remove.
    """


@dataclass(frozen=True)
class Event:
    seq: int
    task_id: str
    kind: EventKind
    ts: str
    payload: dict[str, Any]

    @property
    def event_id(self) -> str:
        return content_hash(
            {"seq": self.seq, "task_id": self.task_id, "kind": self.kind.value, "payload": self.payload}
        )[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "seq": self.seq,
            "task_id": self.task_id,
            "kind": self.kind.value,
            "ts": self.ts,
            "event_id": self.event_id,
            "payload": self.payload,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Event:
        strict_fields(raw, ("schema_version", "seq", "task_id", "kind", "ts", "event_id", "payload"), "event")
        require(raw.get("schema_version") == SCHEMA_VERSION, "event: unsupported schema_version")
        ev = Event(
            seq=int(raw["seq"]),
            task_id=str(raw["task_id"]),
            kind=EventKind(raw["kind"]),
            ts=str(raw["ts"]),
            payload=dict(raw["payload"]),
        )
        require(ev.event_id == raw["event_id"], f"event {ev.seq}: event_id does not match payload")
        return ev


class Ledger:
    """Append-only JSONL. Every append is flushed and fsynced before the
    caller may take the next action — write-before-advance is what makes the
    absence of an event mean the transition did not occur."""

    def __init__(self, path: Path, task_id: str):
        self.path = Path(path)
        self.task_id = task_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        if self.path.exists():
            events, _ = read_events(self.path)
            self._seq = events[-1].seq if events else 0

    @property
    def seq(self) -> int:
        return self._seq

    def append(self, kind: EventKind, payload: dict[str, Any] | None = None) -> Event:
        self._seq += 1
        ev = Event(seq=self._seq, task_id=self.task_id, kind=kind, ts=utc_now(), payload=payload or {})
        line = canonical(ev.to_dict()) + "\n"
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        return ev


def read_events(path: Path) -> tuple[list[Event], bool]:
    """Read a ledger. Returns (events, truncated_tail).

    A torn final line is the expected shape of a crash mid-append and is
    dropped with `truncated_tail=True`. A malformed line anywhere else raises:
    the file was corrupted rather than interrupted, and no projection derived
    from it can be trusted.
    """
    raw_lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)
    events: list[Event] = []
    truncated = False
    for i, line in enumerate(raw_lines):
        is_last = i == len(raw_lines) - 1
        stripped = line.strip()
        if not stripped:
            if is_last:
                continue
            raise LedgerCorrupt(f"{path}: blank line at position {i + 1}")
        if is_last and not line.endswith("\n"):
            truncated = True
            continue
        try:
            events.append(Event.from_dict(json.loads(stripped)))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            if is_last:
                truncated = True
                continue
            raise LedgerCorrupt(f"{path}: malformed event at position {i + 1}: {exc}") from exc
    for expected, ev in enumerate(events, start=1):
        if ev.seq != expected:
            raise LedgerCorrupt(f"{path}: sequence break, expected {expected} got {ev.seq}")
    if events and any(e.kind in TERMINAL_KINDS for e in events[:-1]):
        raise LedgerCorrupt(f"{path}: events recorded after a terminal event")
    return events, truncated


@dataclass
class TaskProjection:
    """The disposable in-memory view of a ledger. Never written to disk."""

    task_id: str = ""
    truncated_tail: bool = False
    contract: TaskContract | None = None
    checkset: CheckSet | None = None
    reproducer: ReproductionContract | None = None
    changeset: ChangeSet | None = None
    baseline_signature: Signature | None = None
    fallbacks: list[dict[str, str]] = field(default_factory=list)
    sandbox: IsolationLevel | None = None
    sandbox_detail: str = ""
    authorized_partial: bool = False
    commands: int = 0
    seconds: float = 0.0
    results: list[CheckResult] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    completed_phases: list[GatePhase] = field(default_factory=list)
    failed_phase: GatePhase | None = None
    failed_phase_reason: str = ""
    blocked_reason: str = ""
    censored: bool = False
    receipt: dict[str, Any] | None = None
    drift: bool = False
    # -- diagnosis (`why`) ------------------------------------------------
    handles: list[Handle] = field(default_factory=list)
    hypotheses: int = 0
    probes: list[dict[str, Any]] = field(default_factory=list)
    refinements: list[dict[str, Any]] = field(default_factory=list)
    eliminated: list[str] = field(default_factory=list)
    diagnosis: Diagnosis | None = None
    model_usage: list[ModelUsage] = field(default_factory=list)
    model_unavailable: str = ""
    spend: list[dict[str, Any]] = field(default_factory=list)
    spend_refused: str = ""
    scope: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def phase(self) -> Phase:
        if self.receipt is not None:
            return Phase.TERMINAL
        if self.blocked_reason or self.failed_phase is not None:
            return Phase.RECEIPT_PENDING
        order = [
            GatePhase.BASELINE,
            GatePhase.CANDIDATE,
            GatePhase.WITHDRAWAL,
            GatePhase.REAPPLY,
            GatePhase.PRESERVATION,
        ]
        nxt = {
            GatePhase.BASELINE: Phase.CANDIDATE,
            GatePhase.CANDIDATE: Phase.WITHDRAWAL,
            GatePhase.WITHDRAWAL: Phase.REAPPLY,
            GatePhase.REAPPLY: Phase.PRESERVATION,
            GatePhase.PRESERVATION: Phase.RECEIPT_PENDING,
        }
        done = [p for p in order if p in self.completed_phases]
        if done:
            return nxt[done[-1]]
        if self.contract is not None:
            return Phase.BASELINE
        if self.sandbox is not None:
            return Phase.AUTHORIZED
        return Phase.INIT

    @property
    def complete(self) -> bool:
        return self.receipt is not None


def reduce(events: Iterable[Event], truncated_tail: bool = False) -> TaskProjection:
    """The single source of derived state. Pure function of the event stream."""
    proj = TaskProjection(truncated_tail=truncated_tail)
    for ev in events:
        proj.task_id = ev.task_id
        p = ev.payload
        if ev.kind is EventKind.SANDBOX_PROBED:
            proj.sandbox = IsolationLevel(p["level"])
            proj.sandbox_detail = str(p.get("detail", ""))
        elif ev.kind is EventKind.SANDBOX_AUTHORIZED:
            proj.authorized_partial = bool(p.get("partial_authorized", False))
        elif ev.kind is EventKind.CHANGESET_REGISTERED:
            proj.changeset = ChangeSet.from_dict(p["changeset"])
        elif ev.kind is EventKind.CHECKSET_FROZEN:
            proj.checkset = CheckSet.from_dict(p["checkset"])
        elif ev.kind is EventKind.REPRODUCER_FROZEN:
            proj.reproducer = ReproductionContract.from_dict(p["reproducer"])
        elif ev.kind is EventKind.CONTRACT_FROZEN:
            proj.contract = TaskContract.from_dict(p["contract"])
        elif ev.kind is EventKind.DRIFT_DETECTED:
            # Fail-safe: any tracked drift invalidates every recorded phase.
            # No scope guessing about which changed file "cannot matter".
            proj.drift = True
            proj.completed_phases.clear()
            proj.results.clear()
            proj.fallbacks.clear()
            proj.artifacts.clear()
            proj.baseline_signature = None
            # The reproducer names a tree digest. Tracked drift means the tree
            # it froze no longer exists, so the reproducer is void with the
            # phases that used it.
            proj.reproducer = None
        elif ev.kind is EventKind.COMMAND_FINISHED:
            proj.commands += 1
            proj.seconds += float(p.get("duration_s", 0.0))
        elif ev.kind is EventKind.SIGNATURE_FROZEN:
            proj.baseline_signature = Signature.from_dict(p["signature"])
        elif ev.kind is EventKind.CHECK_FALLBACK:
            proj.fallbacks.append({k: str(v) for k, v in p.items()})
        elif ev.kind is EventKind.CHECK_RESULT:
            proj.results.append(CheckResult.from_dict(p["result"]))
        elif ev.kind is EventKind.GATE_PHASE_FINISHED:
            phase = GatePhase(p["phase"])
            for key, value in (p.get("artifacts") or {}).items():
                proj.artifacts[f"{phase.value}.{key}"] = str(value)
            if bool(p["passed"]):
                if phase not in proj.completed_phases:
                    proj.completed_phases.append(phase)
            else:
                proj.failed_phase = phase
                proj.failed_phase_reason = str(p.get("reason", ""))
        elif ev.kind is EventKind.INFRASTRUCTURE_BLOCKED:
            proj.blocked_reason = str(p.get("reason", "infrastructure"))
        elif ev.kind is EventKind.BUDGET_EXHAUSTED:
            proj.censored = True
        elif ev.kind is EventKind.CONTEXT_SELECTED:
            proj.context = dict(p)
        elif ev.kind is EventKind.HANDLES_DISCOVERED:
            proj.handles = [Handle.from_dict(h) for h in p.get("handles", [])]
        elif ev.kind is EventKind.HYPOTHESES_PROPOSED:
            proj.hypotheses = int(p.get("count", 0))
        elif ev.kind is EventKind.PROBE_SELECTED:
            proj.probes.append(dict(p))
        elif ev.kind is EventKind.CAUSE_REFINED:
            proj.refinements.append(dict(p))
        elif ev.kind is EventKind.HYPOTHESES_ELIMINATED:
            for hid in p.get("hypothesis_ids", []):
                if hid not in proj.eliminated:
                    proj.eliminated.append(str(hid))
        elif ev.kind is EventKind.DIAGNOSIS_EMITTED:
            proj.diagnosis = Diagnosis.from_dict(p["diagnosis"])
        elif ev.kind is EventKind.MODEL_RESPONSE_RECEIVED:
            usage = p.get("usage")
            if isinstance(usage, dict):
                proj.model_usage.append(ModelUsage.from_dict(usage))
        elif ev.kind is EventKind.SPEND_RESERVED:
            proj.scope = str(p.get("scope", proj.scope))
            proj.spend.append(dict(p))
        elif ev.kind is EventKind.SPEND_SETTLED:
            # References only. There is deliberately no running total here:
            # a hand-maintained counter is a second spend authority, and the
            # append-only spend ledger is the first.
            proj.spend.append(dict(p))
        elif ev.kind is EventKind.SPEND_REFUSED:
            proj.spend_refused = str(p.get("reason", "spend refused"))
            proj.scope = str(p.get("scope", proj.scope))
        elif ev.kind is EventKind.MODEL_UNAVAILABLE:
            proj.model_unavailable = str(p.get("reason", "model unavailable"))
        elif ev.kind is EventKind.RECEIPT_EMITTED:
            proj.receipt = dict(p["receipt"])
    return proj


def load_projection(path: Path) -> TaskProjection:
    events, truncated = read_events(path)
    return reduce(events, truncated)


# --------------------------------------------------------------------------
# task directory layout
# --------------------------------------------------------------------------


def task_dir(repo_root: Path, task_id: str) -> Path:
    return Path(repo_root) / ".rift" / "tasks" / task_id


def changeset_record(td: Path) -> Path:
    """The durable content-addressed ChangeSet.

    Written when the patch is accepted and read back at reapplication, so the
    bytes that reach the tree a second time come from storage rather than from
    a live object.
    """
    return Path(td) / "change-set.diff"


# A task id is `<verb>-<fingerprint>-<sequence>`. The fingerprint identifies
# *what* was asked; the sequence distinguishes repeated asks. Neither reads the
# clock, because a clock is not an allocator: two invocations inside the same
# timestamp tick, or on a filesystem with coarse time, would otherwise collide
# and silently share one ledger and one artifact directory.
TASK_ID_RE = re.compile(r"\A[a-z]+-[0-9a-f]{8}-\d{4,}\Z")
MAX_ALLOCATION_ATTEMPTS = 64


def task_fingerprint(parts: dict[str, Any]) -> str:
    """Stable identity of the *request*, independent of when it was made."""
    return content_hash(parts)[:8]


def allocate_task_dir(repo_root: Path, verb: str, fingerprint: str) -> tuple[str, Path]:
    """Claim a task directory that provably belongs to this invocation.

    The sequence number is derived by reading `.rift/tasks/` — there is no
    counter file, no database and no `state.json`, so the directory listing
    remains the single source of truth for which tasks exist.

    Reading the listing is a *proposal*, not the claim. The claim is
    `mkdir(exist_ok=False)`, which the operating system makes atomic against
    every other process on the machine. A process that loses the race sees
    `FileExistsError`, rereads the listing and proposes the next number, so
    concurrent invocations of the identical command interleave into distinct
    tasks with distinct ledgers rather than overwriting each other. The retry
    is bounded: a directory that cannot be claimed in `MAX_ALLOCATION_ATTEMPTS`
    is a failure to report, never a directory to reuse.
    """
    require(bool(re.fullmatch(r"[a-z]+", verb)), f"task verb must be lowercase letters: {verb!r}")
    require(bool(re.fullmatch(r"[0-9a-f]{8}", fingerprint)), "task fingerprint must be 8 hex digits")
    base = Path(repo_root) / ".rift" / "tasks"
    base.mkdir(parents=True, exist_ok=True)
    prefix = f"{verb}-{fingerprint}-"
    for _ in range(MAX_ALLOCATION_ATTEMPTS):
        taken = [
            int(d.name[len(prefix) :])
            for d in base.iterdir()
            if d.name.startswith(prefix) and d.name[len(prefix) :].isdigit()
        ]
        task_id = f"{prefix}{(max(taken) + 1 if taken else 0):04d}"
        td = base / task_id
        try:
            td.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return task_id, td
    raise ValidationError(f"could not allocate a task directory under {base} after {MAX_ALLOCATION_ATTEMPTS} attempts")


def iter_task_dirs(repo_root: Path) -> Iterator[Path]:
    base = Path(repo_root) / ".rift" / "tasks"
    if not base.is_dir():
        return
    for d in sorted(base.iterdir()):
        if (d / "ledger.jsonl").is_file():
            yield d


def write_repro(path: Path, argv_sets: list[list[str]]) -> None:
    """A fixed, safely quoted reproduction script. No model text reaches it."""
    lines = ["#!/bin/sh", "# Generated by riftagent. Deterministic projection of the ledger.", "set -eux"]
    lines += [" ".join(shlex.quote(a) for a in argv) for argv in argv_sets]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


__all__ = [
    "SCHEMA_VERSION",
    "Authorities",
    "BudgetRefused",
    "Budgets",
    "Check",
    "Diagnosis",
    "CheckResult",
    "CheckSet",
    "ChangeSet",
    "ClaimType",
    "Event",
    "EventKind",
    "GatePhase",
    "GateStatus",
    "Handle",
    "BANNED_FIELDS",
    "CLOSED_FIELDS",
    "IRValidationError",
    "IsolationLevel",
    "Ledger",
    "LedgerCorrupt",
    "ModelUsage",
    "Outcome",
    "Phase",
    "Pricing",
    "Primitive",
    "ReproductionContract",
    "RunnerKind",
    "Signature",
    "SpendLedger",
    "Support",
    "TASK_ID_RE",
    "TaskContract",
    "TaskProjection",
    "allocate_task_dir",
    "ValidationError",
    "Verb",
    "Verdict",
    "VerificationReceipt",
    "archive_manifest",
    "canonical",
    "canonical_diff",
    "changeset_record",
    "content_hash",
    "iter_task_dirs",
    "load_projection",
    "read_events",
    "reduce",
    "replace",
    "require",
    "reserve_cost",
    "spend_for_task",
    "spend_ledger_path",
    "short",
    "task_dir",
    "task_fingerprint",
    "utc_now",
    "validate_hypothesis",
    "write_repro",
]
