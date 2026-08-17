"""Check engine: a runner interface with exactly one adapter in v1.

Every pytest assumption lives in :class:`PytestRunner`. Nothing above this
module knows what a node id is, so a second runner is an addition rather than
a redesign.

Two rules earned by the reference prototype's real-repository run:

1. **The verdict comes from the target's own report line, never the exit
   code.** A probe that runs other tests first would otherwise be scored by
   their failures — a confident wrong answer produced by a measurement error.
2. **A failure's identity is stronger evidence than its existence.** Every
   observed failure carries a normalised signature, and a withdrawal that
   fails for a different reason has not restored the original failure.
"""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from riftagent.records import (
    FAIL,
    PASS,
    Check,
    CheckResult,
    ClaimType,
    GatePhase,
    Handle,
    Outcome,
    Primitive,
    RunnerKind,
    Signature,
)
from riftagent.sandbox import CommandResult, IsolationProbe, SandboxError, Worktree, build_env, run_argv

# Paths differ between worktrees by construction, so an unnormalised message
# would never match across the baseline and withdrawal phases.
_TMP_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/](?:[\w.\-]+[\\/])*[\w.\-]+\.py\b")
_ADDR = re.compile(r"0x[0-9a-fA-F]{4,}")
_TMPDIR = re.compile(r"(?:/tmp|/private/var/folders|[A-Za-z]:\\+[^\s]*Temp)[\w\\/.\-]*", re.IGNORECASE)
_WS = re.compile(r"\s+")
_EXC_PREFIX = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Warning|Failure|Exit|Interrupt)\b")
_MESSAGE_CAP = 200


def normalise_message(text: str) -> str:
    text = _ADDR.sub("<addr>", text)
    text = _TMPDIR.sub("<tmp>", text)
    text = _TMP_PATH.sub("<path>", text)
    text = _WS.sub(" ", text).strip()
    return text[:_MESSAGE_CAP]


class Runner(Protocol):
    """What the engine needs from a runner. A second runner (Jest, Go) is an
    addition here, not a redesign anywhere else."""

    def argv(self, check: Check, worktree: Worktree, selector: str | None = None) -> list[str]: ...

    def interpret(self, check: Check, res: CommandResult) -> tuple[Outcome, Signature | None, str]: ...


@dataclass(frozen=True)
class PytestRunner:
    kind: RunnerKind = RunnerKind.PYTEST

    def argv(self, check: Check, worktree: Worktree, selector: str | None = None) -> list[str]:
        return [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=long",
            "-rA",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            selector or check.node_id,
        ]

    @staticmethod
    def containing_file(node_id: str) -> str | None:
        """The bounded widening step: one file, never the suite."""
        head = node_id.split("::")[0].strip()
        return head if head and head != node_id else None

    # -- interpretation -------------------------------------------------

    def interpret(self, check: Check, res: CommandResult) -> tuple[Outcome, Signature | None, str]:
        if res.timed_out:
            return Outcome.TIMEOUT, None, f"timed out after {res.duration_s:.1f}s"
        text = res.combined
        if "No module named pytest" in text or res.exit_code == 127:
            return Outcome.MISSING_RUNNER, None, "pytest is not installed in the sandbox interpreter"
        verdict = self._report_line_verdict(text, check.node_id)
        if verdict == "PASSED":
            return Outcome.PASSED, None, ""
        if verdict in ("FAILED", "ERROR"):
            sig = self._signature(text, check.node_id)
            if verdict == "ERROR" and self._is_collection_error(text):
                return Outcome.COLLECTION_ERROR, sig, "error during collection/setup, not a target failure"
            return Outcome.FAILED, sig, ""
        # The target produced no report line at all.
        if self._is_collection_error(text) or res.exit_code in (2, 3, 4):
            return Outcome.COLLECTION_ERROR, None, self._collection_detail(text)
        if "no tests ran" in text.lower():
            return Outcome.COLLECTION_ERROR, None, f"node id did not select any test: {check.node_id}"
        if res.exit_code == 0:
            return Outcome.PASSED, None, "no report line; exit code 0"
        return Outcome.INFRASTRUCTURE, None, f"unclassifiable runner output (exit {res.exit_code})"

    @staticmethod
    def _report_line_verdict(text: str, node_id: str) -> str | None:
        """Read the target's own short-summary line.

        `-rA` emits `PASSED <node>` / `FAILED <node> - <reason>`. Matching the
        node id exactly (or followed by ` - `) avoids crediting the target with
        a neighbour's outcome.
        """
        for line in text.splitlines():
            stripped = line.strip()
            verdict, _, rest = stripped.partition(" ")
            if verdict not in ("PASSED", "FAILED", "ERROR", "XFAIL", "XPASS", "SKIPPED"):
                continue
            rest = rest.strip()
            if rest == node_id or rest.startswith(node_id + " - "):
                return verdict
        return None

    @staticmethod
    def _is_collection_error(text: str) -> bool:
        markers = (
            "ERROR collecting",
            "errors during collection",
            "INTERNALERROR",
            "ImportError while importing test module",
            "file or directory not found",
            "unrecognized arguments",
        )
        return any(m in text for m in markers)

    @staticmethod
    def _collection_detail(text: str) -> str:
        for line in text.splitlines():
            if "ERROR collecting" in line or "ImportError while importing" in line:
                return normalise_message(line)
        return "collection or configuration error"

    @staticmethod
    def _signature(text: str, node_id: str) -> Signature | None:
        """Prefer the traceback's `E` line over the short summary.

        pytest truncates the short-summary reason to the terminal width, which
        would make a frozen signature depend on where it was captured. The
        traceback line is complete. `COLUMNS` is also pinned for the child so
        the fallback stays reproducible.
        """
        candidates = [
            line.strip()[1:].strip()
            for line in text.splitlines()
            if line.strip().startswith("E ") or line.strip() == "E"
        ]
        qualifying = [c for c in candidates if c and (_EXC_PREFIX.match(c) or c.startswith("assert "))]
        if qualifying:
            return _split_signature(qualifying[-1])
        for line in text.splitlines():
            stripped = line.strip()
            if (stripped.startswith("FAILED ") or stripped.startswith("ERROR ")) and node_id in stripped:
                _, _, reason = stripped.partition(" - ")
                if reason.strip():
                    return _split_signature(reason.strip())
        return None


def _split_signature(reason: str) -> Signature:
    head, sep, tail = reason.partition(":")
    head = head.strip()
    if sep and re.fullmatch(r"[A-Za-z_][\w.]*(Error|Exception|Warning|Failure|Exit)?", head):
        return Signature(exception_type=head, message=normalise_message(tail))
    return Signature(exception_type="Failure", message=normalise_message(reason))


RUNNERS: dict[RunnerKind, Runner] = {RunnerKind.PYTEST: PytestRunner()}


def _execute(
    check: Check,
    worktree: Worktree,
    probe: IsolationProbe,
    allow_network: bool,
    selector: str | None,
    on_start,
    on_done,
) -> CommandResult:
    """One command, announced before it starts and recorded after it ends, so
    every executed command is charged and appears in the ledger."""
    runner = RUNNERS[check.runner]
    argv = runner.argv(check, worktree, selector)
    if on_start is not None:
        on_start(argv, selector)
    env = build_env(worktree.path, worktree.tmpdir)
    res = run_argv(argv, worktree.path, env, check.timeout_s, probe, allow_network)
    if on_done is not None:
        on_done(argv, res)
    return res


def run_check(
    check: Check,
    worktree: Worktree,
    phase: GatePhase,
    probe: IsolationProbe,
    allow_network: bool = False,
    on_start=None,
    on_done=None,
    allow_file_fallback: bool = True,
) -> tuple[CheckResult, CommandResult]:
    """Run one check, with a bounded widening step when the node cannot be seen.

    Some repositories cannot collect a single node in isolation — a conftest,
    a plugin or an import cycle needs the module's other tests present. That is
    a limit of the observation, not a property of the target, and abstaining on
    it costs real yield (it cost two `chardet` cases in the M1a benchmark).

    The fallback widens by exactly one step, to the node's own file, and then
    still reads **the declared target's own report line**. A neighbour's
    outcome, a missing report line, a timeout or an infrastructure error can
    never satisfy the check. This is an observation fallback, never permission
    to substitute a whole-suite verdict for the target.
    """
    runner = RUNNERS[check.runner]
    try:
        res = _execute(check, worktree, probe, allow_network, None, on_start, on_done)
    except SandboxError as exc:
        return (
            CheckResult(check.check_id, check.node_id, phase, Outcome.INFRASTRUCTURE, None, 0.0, -1, str(exc)),
            CommandResult((), -1, "", str(exc), 0.0),
        )
    outcome, signature, detail = runner.interpret(check, res)

    fallback = ""
    if allow_file_fallback and outcome is Outcome.COLLECTION_ERROR:
        selector = PytestRunner.containing_file(check.node_id)
        if selector:
            try:
                wider = _execute(check, worktree, probe, allow_network, selector, on_start, on_done)
            except SandboxError:
                wider = None
            if wider is not None:
                w_outcome, w_signature, w_detail = runner.interpret(check, wider)
                # Only an observable result for the declared target counts.
                if w_outcome.is_evidence:
                    outcome, signature = w_outcome, w_signature
                    res = wider
                    fallback = selector
                    detail = (
                        f"single-node collection failed; observed via its containing file {selector}. "
                        f"Scope widened from one node to one file; the verdict is still the declared "
                        f"target's own report line."
                    )
                else:
                    detail = f"{detail}; file-scoped retry on {selector} also could not observe the target ({w_detail})"

    return (
        CheckResult(
            check_id=check.check_id,
            node_id=check.node_id,
            phase=phase,
            outcome=outcome,
            signature=signature,
            duration_s=res.duration_s,
            exit_code=res.exit_code,
            detail=detail,
            fallback=fallback,
        ),
        res,
    )


def collect_exists(node_id: str, worktree: Worktree, probe: IsolationProbe, timeout_s: float = 120.0) -> bool:
    argv = [sys.executable, "-m", "pytest", "-q", "--collect-only", "-p", "no:cacheprovider", node_id]
    env = build_env(worktree.path, worktree.tmpdir)
    try:
        res = run_argv(argv, worktree.path, env, timeout_s, probe)
    except SandboxError:
        return False
    return res.exit_code == 0 and "no tests ran" not in res.stdout.lower()


def runner_config_hash(repo_root: Path) -> str:
    """Hash of the files that decide what the runner discovers and how.

    These are part of the frozen judge: a candidate patch that edits them is
    rewriting the test that judges it.
    """
    import hashlib

    names = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "conftest.py")
    digest = hashlib.sha256()
    for name in names:
        path = Path(repo_root) / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"<absent>")
        digest.update(b"\0")
    return digest.hexdigest()


# --------------------------------------------------------------------------
# diagnosis probes
#
# `verify` runs one declared check. `why` runs *experiments*: the same target
# under different applied handles, to see which of them changes its outcome.
# The two share this module's hard-won rule — the verdict is read from the
# target's own report line — because a probe deliberately runs other tests
# first, so its exit code belongs to them.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeObservation:
    """One executed experiment. Data only; it decides nothing."""

    outcome: str
    node_outcome: Outcome
    signature: Signature | None
    failure_text: str
    selectors: tuple[str, ...]
    runs: int
    duration_s: float
    detail: str = ""

    @property
    def is_evidence(self) -> bool:
        return self.node_outcome.is_evidence

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "node_outcome": self.node_outcome.value,
            "signature": self.signature.to_dict() if self.signature else None,
            "selectors": list(self.selectors),
            "runs": self.runs,
            "duration_s": round(self.duration_s, 3),
            "detail": self.detail,
        }


def _probe_check(target: str, timeout_s: float) -> Check:
    """A synthetic Check so probe results are interpreted by exactly the same
    code that interprets gate results. Two interpreters would eventually
    disagree, and the disagreement would be invisible."""
    return Check(
        check_id="probe",
        claim_type=ClaimType.CHANGE,
        runner=RunnerKind.PYTEST,
        node_id=target,
        expected_baseline=Outcome.FAILED,
        expected_candidate=Outcome.PASSED,
        timeout_s=timeout_s,
        scope="diagnosis probe",
    )


def compile_handles(
    handles: tuple[Handle, ...],
    worktree: Worktree,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Turn typed handles into an env delta and a selector prefix.

    This is the only place a handle becomes an effect. It returns data — no
    command string is built here and none is ever accepted from a model. A
    handle whose primitive is an assertion is skipped: assertions observe, and
    there is nothing to apply.
    """
    env_extra: dict[str, str] = {}
    env_drop: list[str] = []
    prefix: list[str] = []
    for h in handles:
        if h.kind is Primitive.ENV:
            # A marker value, not a guess at what the variable should contain.
            # The experiment asks "does this variable being set change the
            # outcome", which is answerable; "what is its correct value" is not.
            env_extra[h.arg] = "1"
        elif h.kind is Primitive.UNSETENV:
            env_drop.append(h.arg)
        elif h.kind is Primitive.CLEAR:
            _clear_state(worktree.path, h.arg)
        elif h.kind is Primitive.FIRST:
            prefix.append(h.arg)
        elif h.kind is Primitive.FIRSTSET:
            prefix.extend(p for p in h.arg.split(",") if p)
    return env_extra, env_drop, prefix


# One fixed program, run inside the sandbox. Kind and argument arrive as argv
# elements read from `sys.argv`: nothing is interpolated into source or a shell.
_ASSERT_SRC = (
    "import importlib.util, os, shutil, sys\n"
    "k, a = sys.argv[1], sys.argv[2]\n"
    "try:\n"
    "    p = importlib.util.find_spec(a) is not None if k == 'dep_assert' else bool(\n"
    "        os.path.exists(a) or shutil.which(a))\n"
    "except Exception as exc:\n"
    # An import machinery failure is a broken measurement, not an absence. A
    # namespace package with a missing parent, an unreadable path or an
    # importer that raises must be distinguishable from "it is not there".
    "    print('error', type(exc).__name__)\n"
    "    sys.exit(2)\n"
    "print('present' if p else 'absent')\n"
    "sys.exit(0 if p else 1)\n"
)

# The three outcomes of measuring an assertion. Only ABSENT is evidence.
PRESENT, ABSENT, UNOBSERVABLE = "present", "absent", "unobservable"


def evaluate_assertion(
    handle: Handle, worktree: Worktree, probe: IsolationProbe, timeout_s: float
) -> tuple[str, CommandResult | None]:
    """Observe whether what the failure named is actually present.

    An assertion is not applied and not withdrawn; it is *measured*, by running
    a real command in the same sandbox the target ran in.

    Returns one of `PRESENT`, `ABSENT`, `UNOBSERVABLE`. The third is not a
    quieter form of the second: a measurement that could not be taken says
    nothing about whether the thing exists, and only an executed, valid `ABSENT`
    may support an observational diagnosis.
    """
    argv = [sys.executable, "-c", _ASSERT_SRC, handle.kind.value, handle.arg]
    env = build_env(worktree.path, worktree.tmpdir, {})
    try:
        res = run_argv(argv, worktree.path, env, timeout_s, probe, False)
    except SandboxError:
        return UNOBSERVABLE, None
    first = res.stdout.split(maxsplit=1)[0] if res.stdout.split() else ""
    if res.timed_out or res.exit_code not in (0, 1) or first not in (PRESENT, ABSENT):
        return UNOBSERVABLE, res
    # Exit code and reported word must agree, or the measurement is not trusted.
    if (res.exit_code == 1) != (first == ABSENT):
        return UNOBSERVABLE, res
    return first, res


def _clear_state(root: Path, name: str) -> None:
    """Delete matching state directories inside the disposable sandbox only.

    `Handle.from_dict` already rejects absolute paths, traversal and shell
    metacharacters. This adds the containment check the filesystem can actually
    enforce: a resolved victim outside the worktree is skipped, whatever the
    argument claimed to be.
    """
    root = root.resolve()
    for victim in sorted(root.rglob(name)):
        try:
            resolved = victim.resolve()
            resolved.relative_to(root)
        except (ValueError, OSError):
            continue
        if resolved == root:
            continue
        if resolved.is_dir():
            shutil.rmtree(resolved, ignore_errors=True)
        elif resolved.exists():
            try:
                resolved.unlink()
            except OSError:
                pass


def probe_argv(prefix: list[str], target: str) -> list[str]:
    """Fixed argv shape. Selectors are handle arguments, already validated as
    paths or node ids — never model prose, never a shell string."""
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=long",
        "-rA",
        "-p",
        "no:randomly",
        "-p",
        "no:cacheprovider",
        *prefix,
        target,
    ]


def run_probe(
    target: str,
    handles: tuple[Handle, ...],
    repeats: int,
    worktree: Worktree,
    probe: IsolationProbe,
    allow_network: bool = False,
    timeout_s: float = 120.0,
    on_start=None,
    on_done=None,
) -> ProbeObservation:
    """Apply handles, run the target `repeats` times, report the LAST run.

    Every run is executed and every run is charged; repetition is the whole
    point of a `x3` probe, since an accumulating cause is invisible in one run.
    The returned outcome is read from the target's own report line, so tests
    the probe ran first cannot supply the verdict.
    """
    env_extra, env_drop, prefix = compile_handles(handles, worktree)
    check = _probe_check(target, timeout_s)
    argv = probe_argv(prefix, target)
    env = build_env(worktree.path, worktree.tmpdir, env_extra)
    for name in env_drop:
        env.pop(name, None)

    runner = RUNNERS[check.runner]
    total = 0.0
    last: CommandResult | None = None
    n = max(1, repeats)
    for i in range(n):
        if on_start is not None:
            on_start(argv, i + 1, n)
        try:
            res = run_argv(argv, worktree.path, env, timeout_s, probe, allow_network)
        except SandboxError as exc:
            return ProbeObservation(FAIL, Outcome.INFRASTRUCTURE, None, "", tuple(prefix), i, total, str(exc))
        total += res.duration_s
        last = res
        if on_done is not None:
            on_done(argv, res)

    assert last is not None
    outcome, signature, detail = runner.interpret(check, last)
    # Only PASSED and FAILED are evidence. A collection error or a timeout says
    # the target was not observed, which is not the same as the target failing —
    # conflating them is how a measurement failure becomes a confident cause.
    return ProbeObservation(
        outcome=PASS if outcome is Outcome.PASSED else FAIL,
        node_outcome=outcome,
        signature=signature,
        failure_text=last.combined if outcome is not Outcome.PASSED else "",
        selectors=tuple(prefix),
        runs=n,
        duration_s=total,
        detail=detail,
    )


def collect_nodes(
    worktree: Worktree,
    probe: IsolationProbe,
    timeout_s: float = 120.0,
    allow_network: bool = False,
) -> tuple[list[str], str]:
    """The node ids pytest can see, used to build `run this first` handles.

    A repository whose collection fails yields an empty list rather than an
    exception: that is a real and reportable limit on what can be hypothesised,
    not a crash.
    """
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--collect-only",
        "-p",
        "no:randomly",
        "-p",
        "no:cacheprovider",
    ]
    env = build_env(worktree.path, worktree.tmpdir)
    try:
        res = run_argv(argv, worktree.path, env, timeout_s, probe, allow_network)
    except SandboxError as exc:
        return [], str(exc)
    nodes: list[str] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if "::" in line and not line.startswith("<") and " " not in line:
            nodes.append(line)
    return nodes, "" if nodes else "pytest collected no node ids"


def observable_paths(root: Path, cap: int = 4000) -> list[str]:
    """Directory names visible in the repository, as `name/` strings.

    Feeds `clear` handle discovery. Bounded, and it never leaves the worktree.
    """
    out: list[str] = []
    root = root.resolve()
    for p in sorted(root.rglob("*")):
        if len(out) >= cap:
            break
        if p.is_dir() and ".git" not in p.parts and ".rift" not in p.parts:
            out.append(p.name + "/")
    return out


DEFAULT_PROTECTED_PATHS = (
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "conftest.py",
)


__all__ = [
    "DEFAULT_PROTECTED_PATHS",
    "ProbeObservation",
    "PytestRunner",
    "RUNNERS",
    "Runner",
    "collect_exists",
    "collect_nodes",
    "compile_handles",
    "normalise_message",
    "observable_paths",
    "probe_argv",
    "run_check",
    "run_probe",
    "runner_config_hash",
]
