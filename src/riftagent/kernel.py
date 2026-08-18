"""Deterministic decision layer. Zero tokens, zero network, zero model.

Everything in this module is a pure function of recorded evidence. It decides
what the evidence supports; it never gathers evidence, renders it, or asks
anyone. The application loop owns those jobs.

The import boundary is structural and enforced by an AST test: this module may
not import `riftagent.app`, `riftagent.llm`, any provider SDK, or any
networking package, and it accepts no injected model callback.
"""

from __future__ import annotations

import itertools
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from riftagent.records import (
    FAIL,
    PASS,
    Check,
    CheckResult,
    ClaimType,
    Diagnosis,
    GatePhase,
    GateStatus,
    Handle,
    IRValidationError,
    Outcome,
    Primitive,
    ReproductionContract,
    Signature,
    Support,
    TaskProjection,
    ValidationError,
    Verdict,
)

# --------------------------------------------------------------------------
# patch validation
# --------------------------------------------------------------------------

_DIFF_GIT = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
_OLD = re.compile(r"^--- (?:a/)?(?P<path>.+?)(?:\t.*)?$")
_NEW = re.compile(r"^\+\+\+ (?:b/)?(?P<path>.+?)(?:\t.*)?$")
_MODE = re.compile(r"^(?:new|old|new file|deleted file) mode (?P<mode>\d{6})$")

FORBIDDEN_PREFIXES = (".git", ".rift")
SYMLINK_MODE = "120000"


@dataclass(frozen=True)
class PatchValidation:
    ok: bool
    reason: str
    touched: tuple[str, ...]

    @property
    def rejected(self) -> bool:
        return not self.ok


def _unsafe_path(path: str) -> str | None:
    if not path or path == "/dev/null":
        return None
    if path.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", path):
        return f"absolute path in diff: {path}"
    if "\\" in path:
        return f"backslash path separator in diff: {path}"
    parts = PurePosixPath(path).parts
    if ".." in parts:
        return f"parent-directory traversal in diff: {path}"
    if parts and parts[0] in FORBIDDEN_PREFIXES:
        return f"diff modifies {parts[0]}/: {path}"
    return None


def validate_patch(diff: str, protected_paths: tuple[str, ...] = ()) -> PatchValidation:
    """Structural rejection, not heuristic detection.

    A patch that can reach the judge, escape the repository, or arrive as
    opaque bytes is refused before it is ever applied. The frozen-judge rule is
    one mechanism that subsumes skips, deleted tests, weakened assertions and
    narrowed discovery: none of them are searched for individually because none
    of them can be expressed in an accepted patch.
    """
    if not diff.strip():
        return PatchValidation(False, "empty diff", ())
    if "GIT binary patch" in diff or re.search(r"^Binary files .* differ$", diff, re.MULTILINE):
        return PatchValidation(False, "binary patch", ())

    touched: list[str] = []
    saw_hunk = False
    for line in diff.splitlines():
        m = _DIFF_GIT.match(line)
        if m:
            for path in (m.group("a"), m.group("b")):
                problem = _unsafe_path(path)
                if problem:
                    return PatchValidation(False, problem, ())
                if path != "/dev/null":
                    touched.append(path)
            continue
        for pattern in (_OLD, _NEW):
            m = pattern.match(line)
            if m:
                path = m.group("path").strip()
                problem = _unsafe_path(path)
                if problem:
                    return PatchValidation(False, problem, ())
                if path != "/dev/null":
                    touched.append(path)
        m = _MODE.match(line)
        if m and m.group("mode") == SYMLINK_MODE:
            return PatchValidation(False, "diff creates or alters a symlink (mode 120000)", ())
        if line.startswith("@@"):
            saw_hunk = True

    if not touched:
        return PatchValidation(False, "diff declares no file paths", ())
    if not saw_hunk:
        return PatchValidation(False, "diff contains no hunks", ())

    unique = tuple(sorted(set(touched)))
    for path in unique:
        for protected in protected_paths:
            if path == protected or path.startswith(protected.rstrip("/") + "/"):
                return PatchValidation(
                    False,
                    f"diff modifies the frozen judge: {path} (protected: {protected})",
                    unique,
                )
    return PatchValidation(True, "", unique)


# --------------------------------------------------------------------------
# gate phase decisions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseDecision:
    passed: bool
    reason: str
    infrastructure: bool = False


def _measurement_failure(result: CheckResult) -> PhaseDecision | None:
    """A check that could not be observed says nothing about the target.

    Timeouts, collection errors, a missing runner and sandbox faults are
    measurement failures. Treating any of them as a target failure is how a
    broken environment becomes a confident verdict.
    """
    if result.outcome.is_evidence:
        return None
    return PhaseDecision(
        False,
        f"{result.node_id}: {result.outcome.value} ({result.detail or 'no detail'})",
        infrastructure=True,
    )


def decide_baseline(check: Check, result: CheckResult) -> PhaseDecision:
    """The change check must fail before the patch, or there is nothing to fix."""
    infra = _measurement_failure(result)
    if infra:
        return infra
    if result.outcome is Outcome.PASSED:
        return PhaseDecision(
            False,
            f"{result.node_id} already passes at baseline: reproduction was not established, "
            "so no change claim can be gated",
        )
    if check.predicted_signature is not None and not check.predicted_signature.matches(result.signature):
        observed = result.signature.render() if result.signature else "no signature"
        return PhaseDecision(
            False,
            f"{result.node_id} failed for a different reason than predicted "
            f"(predicted {check.predicted_signature.render()}, observed {observed})",
        )
    return PhaseDecision(True, "")


def decide_candidate(result: CheckResult) -> PhaseDecision:
    infra = _measurement_failure(result)
    if infra:
        return infra
    if result.outcome is Outcome.PASSED:
        return PhaseDecision(True, "")
    observed = result.signature.render() if result.signature else "no signature"
    return PhaseDecision(False, f"{result.node_id} still fails with the patch applied ({observed})")


def decide_withdrawal(result: CheckResult, frozen: Signature | None) -> PhaseDecision:
    """The counterfactual. Passing with the patch is consistent with a stale
    cache, a retry, an unrelated edit, or a neighbouring test having supplied a
    missing import. Only the return of the original failure excludes them."""
    infra = _measurement_failure(result)
    if infra:
        return infra
    if result.outcome is Outcome.PASSED:
        return PhaseDecision(
            False,
            f"{result.node_id} still passes after the patch is withdrawn: the patch is not what makes it pass",
        )
    if frozen is not None and not frozen.matches(result.signature):
        observed = result.signature.render() if result.signature else "no signature"
        return PhaseDecision(
            False,
            f"withdrawal restored a different failure than the baseline "
            f"(baseline {frozen.render()}, withdrawal {observed})",
        )
    return PhaseDecision(True, "")


def decide_withdrawal_state(withdrawn_state: str, baseline_state: str) -> PhaseDecision:
    """Did reversing the patch restore the baseline phase state?

    Phase state, not whole tree: runtime debris written by the candidate is
    still present at this point and is cleared by the withdrawal episode's
    reset. Judging on a whole-tree hash rejects a sound counterfactual because
    of a log file.

    Both operands cover the manifest plus the paths the patch owns, so a
    patch-added file that reversal failed to remove is visible here as a path
    that is present when it should be absent.
    """
    if baseline_state and withdrawn_state != baseline_state:
        return PhaseDecision(
            False,
            "withdrawing the patch did not restore the baseline state; the candidate phase left "
            "changes behind and the counterfactual is not sound",
        )
    return PhaseDecision(True, "")


def decide_reapply(
    candidate_state_hash: str,
    reapplied_state_hash: str,
    frozen_patch_hash: str,
    reloaded_patch_hash: str,
) -> PhaseDecision:
    """Reapplication must compare independently derived values.

    `frozen_patch_hash` is the hash recorded in the ledger when the ChangeSet
    was accepted. `reloaded_patch_hash` must be recomputed from bytes read back
    off disk. Comparing two values derived from one in-memory object would
    assert nothing at all, so an absent hash is refused here rather than
    trusted from the call site.
    """
    if not frozen_patch_hash or not reloaded_patch_hash:
        return PhaseDecision(
            False,
            "reapplication integrity cannot be established: a patch hash was not supplied",
            infrastructure=True,
        )
    if frozen_patch_hash != reloaded_patch_hash:
        return PhaseDecision(
            False,
            f"the durable ChangeSet no longer hashes to its accepted value "
            f"(accepted {frozen_patch_hash[:12]}…, reloaded {reloaded_patch_hash[:12]}…)",
        )
    if candidate_state_hash != reapplied_state_hash:
        return PhaseDecision(
            False,
            "phase state after reapplication differs from the gated candidate tree",
        )
    return PhaseDecision(True, "")


def decide_preservation(results: tuple[CheckResult, ...]) -> PhaseDecision:
    failures = [r for r in results if r.outcome is not Outcome.PASSED]
    if not failures:
        return PhaseDecision(True, "")
    infra = [r for r in failures if not r.outcome.is_evidence]
    if infra and len(infra) == len(failures):
        return PhaseDecision(
            False,
            "; ".join(f"{r.node_id}: {r.outcome.value}" for r in infra),
            infrastructure=True,
        )
    named = ", ".join(r.node_id for r in failures[:5])
    more = "" if len(failures) <= 5 else f" (+{len(failures) - 5} more)"
    return PhaseDecision(False, f"preservation checks failed: {named}{more}")


# --------------------------------------------------------------------------
# verdict derivation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictDecision:
    verdict: Verdict
    reason: str
    rejected_phase: GatePhase | None
    uncertainty: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        """Exit codes distinguish the outcome classes the plan names.

        NOTE (recorded conflict, see IMPLEMENTATION_STATUS.md): the CLI section
        of the plan names "regression or verification rejection" as a distinct
        outcome class, but the frozen verdict vocabulary of design §9 has no
        member for a rejected counterfactual. A rejection is therefore recorded
        as `unverifiable` carrying `rejected_phase`, and only the exit code
        separates it from an ordinary abstention.
        """
        if self.verdict is Verdict.VERIFIED_AGAINST_APPROVED_CHECKS:
            return 0
        if self.verdict is Verdict.REGRESSION_BLOCKED:
            return 2
        if self.verdict is Verdict.INFRASTRUCTURE_BLOCKED:
            return 3
        return 2 if self.rejected_phase is not None else 1


def _uncertainty(proj: TaskProjection) -> tuple[str, ...]:
    notes: list[str] = []
    if proj.contract is not None and proj.contract.actual_sandbox.value == "partial":
        notes.append(
            "repository code executed under a partial sandbox: side effects outside the "
            "worktree were possible and are not accounted for"
        )
    if proj.checkset is not None and not proj.checkset.by_type(ClaimType.PRESERVATION):
        notes.append(
            "no preservation checks were declared, so this receipt says nothing about "
            "regressions elsewhere in the repository"
        )
    for fb in proj.fallbacks:
        notes.append(
            f"{fb.get('node_id')} could not be collected as a single node; it was observed "
            f"through {fb.get('selector')}, so its evidence was gathered with other tests in "
            "that file present"
        )
    if proj.truncated_tail:
        notes.append("the ledger's final line was torn by an interruption and was discarded")
    if proj.drift:
        notes.append("the repository drifted during the task; baseline evidence was re-established")
    return tuple(notes)


def derive_verdict(proj: TaskProjection) -> VerdictDecision:
    """The single place a verdict is produced. Deterministic and model-free."""
    uncertainty = _uncertainty(proj)
    if proj.ablation == "model_alone":
        # The ablation's ceiling, derived here rather than asserted downstream.
        # Arm A runs no withdrawal, reapplication or preservation phase, so the
        # ordinary derivation below could never reach the verified verdict for
        # it anyway — but "could never" is a property worth stating in code
        # rather than leaving to arithmetic about which phases happen to run.
        if proj.blocked_reason:
            return VerdictDecision(Verdict.INFRASTRUCTURE_BLOCKED, proj.blocked_reason, None, uncertainty)
        passed = GatePhase.CANDIDATE in proj.completed_phases and proj.failed_phase is None
        return VerdictDecision(
            Verdict.ACCEPTED_BY_TARGET_PASS if passed else Verdict.UNVERIFIABLE,
            "the target passed after the patch was applied; no withdrawal, reapplication or preservation "
            "gate was run, and this is not product-verification evidence"
            if passed
            else (proj.failed_phase_reason or "arm A produced no accepted patch"),
            proj.failed_phase,
            uncertainty,
        )
    if proj.blocked_reason:
        return VerdictDecision(Verdict.INFRASTRUCTURE_BLOCKED, proj.blocked_reason, None, uncertainty)
    if proj.failed_phase is not None:
        # The rejection reason is the kernel's own words, recorded on the gate
        # event. Falling back to a check's detail would substitute a symptom
        # for the decision.
        reason = proj.failed_phase_reason
        if not reason:
            for result in reversed(proj.results):
                if result.phase is proj.failed_phase:
                    reason = result.detail
                    break
        if proj.failed_phase is GatePhase.PRESERVATION:
            return VerdictDecision(
                Verdict.REGRESSION_BLOCKED,
                reason or "preservation checks failed with the patch applied",
                proj.failed_phase,
                uncertainty,
            )
        return VerdictDecision(
            Verdict.UNVERIFIABLE,
            reason or f"the counterfactual gate rejected the patch at the {proj.failed_phase.value} phase",
            proj.failed_phase,
            uncertainty,
        )
    required = (
        GatePhase.BASELINE,
        GatePhase.CANDIDATE,
        GatePhase.WITHDRAWAL,
        GatePhase.REAPPLY,
        GatePhase.PRESERVATION,
    )
    missing = [p.value for p in required if p not in proj.completed_phases]
    if missing:
        return VerdictDecision(
            Verdict.UNVERIFIABLE,
            f"gate incomplete: {', '.join(missing)} not executed",
            None,
            uncertainty,
        )
    return VerdictDecision(Verdict.VERIFIED_AGAINST_APPROVED_CHECKS, "", None, uncertainty)


def checks_not_executed(proj: TaskProjection) -> tuple[str, ...]:
    """What the receipt must disclose it did not look at."""
    out = ["full repository suite"]
    if proj.checkset is not None and not proj.checkset.by_type(ClaimType.PRESERVATION):
        out.append("preservation checks (none declared)")
    executed = {r.check_id for r in proj.results}
    if proj.checkset is not None:
        for check in proj.checkset.checks:
            if check.check_id not in executed:
                out.append(f"{check.node_id} ({check.claim_type.value}, not run)")
    return tuple(out)


__all__ = [
    "PatchValidation",
    "PhaseDecision",
    "VerdictDecision",
    "checks_not_executed",
    "decide_baseline",
    "decide_candidate",
    "decide_preservation",
    "decide_reapply",
    "decide_withdrawal",
    "decide_withdrawal_state",
    "derive_verdict",
    "validate_patch",
]


# ==========================================================================
# diagnosis kernel — ported from the RIFT v2 / RIFT-Code prototype
#
# Hypotheses are JSON-shaped dicts executed over a generic event trace. No
# arbitrary Python ever runs: the IR is validated, depth- and operation-bounded,
# and side-effect free. A model may propose hypotheses in this IR, but the
# kernel scores them, eliminates them, and decides what survives.
#
# The vocabulary is anonymous by construction — theories are written over roles
# r0..rN, never over handle names — so a proposed theory cannot smuggle in
# semantics from a label it happened to see.
# ==========================================================================


def description_length(h: dict[str, Any]) -> int:
    """Deterministic complexity: canonical node count.

    Used only to break ties between behaviourally identical theories.
    """

    def nodes(x: Any) -> int:
        if isinstance(x, dict):
            return 1 + sum(nodes(v) for v in x.values())
        if isinstance(x, list):
            return 1 + sum(nodes(v) for v in x)
        return 1

    return nodes({k: h[k] for k in ("roles", "latents", "condition") if k in h})


# -------------------------------------------------------------- IR execution


def _eval_pred(p: dict[str, Any], state: dict[str, int]) -> bool:
    if "const" in p:
        return bool(p["const"])
    if "var" in p and "op" not in p:
        return bool(state.get(p["var"], 0))
    op = p["op"]
    if op == "ge":
        return state.get(p["var"], 0) >= p["value"]
    if op == "and":
        return _eval_pred(p["args"][0], state) and _eval_pred(p["args"][1], state)
    if op == "or":
        return _eval_pred(p["args"][0], state) or _eval_pred(p["args"][1], state)
    if op == "not":
        return not _eval_pred(p["arg"], state)
    raise IRValidationError(op)


def _init_state(h: dict[str, Any]) -> dict[str, int]:
    return {la["name"]: 0 for la in h.get("latents", [])}


def _apply_event(h: dict[str, Any], state: dict[str, int], kind: str, role: str | None) -> None:
    for la in h.get("latents", []):
        if la["type"] == "bool":
            e = la["set_on"]
            if e["event"] == kind and (e.get("role") is None or e.get("role") == role):
                state[la["name"]] = 1
            rs = la.get("reset_on")
            if rs and rs["event"] == kind and (rs.get("role") is None or rs.get("role") == role):
                state[la["name"]] = 0
        elif la["type"] == "counter":
            e = la["inc_on"]
            if e["event"] == kind and (e.get("role") is None or e.get("role") == role):
                state[la["name"]] = min(la["max"], state.get(la["name"], 0) + 1)


@dataclass
class Evidence:
    """The observed event trace.

    Episode boundaries reset latent state — the code-domain equivalent of the
    gridworld episode reset that makes order-dependent and accumulating causes
    distinguishable at all.
    """

    events: list[tuple[int, str, str | None]] = field(default_factory=list)
    pushes: list[tuple[int, str]] = field(default_factory=list)
    boundaries: set[int] = field(default_factory=lambda: {0})
    interventional: set[int] = field(default_factory=set)
    step: int = 0
    observed: list[dict[str, Any]] = field(default_factory=list)

    def new_episode(self) -> None:
        self.step += 2
        self.boundaries.add(self.step)

    def record(self, applied: tuple[str, ...], repeats: int, outcome: str, interventional: bool = True) -> None:
        for role in applied:
            self.step += 1
            self.events.append((self.step, "applied", role))
        n = max(1, repeats)
        for i in range(n):
            self.step += 1
            self.events.append((self.step, "run", None))
            if i == n - 1:
                self.pushes.append((self.step, outcome))
                if interventional:
                    self.interventional.add(self.step)
        self.observed.append({"applied": list(applied), "repeats": repeats, "outcome": outcome})

    def copy(self) -> Evidence:
        return Evidence(
            events=list(self.events),
            pushes=list(self.pushes),
            boundaries=set(self.boundaries),
            interventional=set(self.interventional),
            step=self.step,
            observed=list(self.observed),
        )


def execute(h: dict[str, Any], ev: Evidence) -> list[tuple[int, str]]:
    """Run the latent program over the trace and predict every observed run.

    Deterministic and side-effect free. Predictions use state accumulated
    strictly *before* the step being predicted, because the outcome of a run
    cannot be its own cause.
    """
    state = _init_state(h)
    bounds = sorted(ev.boundaries or {0})
    seg = 0
    push_at = {s for s, _ in ev.pushes}
    preds: list[tuple[int, str]] = []
    ei = 0
    steps = sorted({s for s, _, _ in ev.events} | push_at)
    for t in steps:
        while seg + 1 < len(bounds) and t >= bounds[seg + 1]:
            seg += 1
            state = _init_state(h)
        pre: list[tuple[int, str, str | None]] = []
        while ei < len(ev.events) and ev.events[ei][0] <= t:
            pre.append(ev.events[ei])
            ei += 1
        for s, kind, role in pre:
            if s < t:
                _apply_event(h, state, kind, role)
        if t in push_at:
            preds.append((t, PASS if _eval_pred(h["condition"], state) else FAIL))
        for s, kind, role in pre:
            if s == t:
                _apply_event(h, state, kind, role)
    return preds


# -------------------------------------------------------------- scoring

LAMBDA_INTERVENTION = 1.0
LAMBDA_DL = 0.002


@dataclass(frozen=True)
class Scored:
    hypothesis: dict[str, Any]
    status: str  # supported | contradicted | underdetermined
    j: float
    dl: int
    n_preds: int
    predictions: tuple[tuple[int, str], ...]


def score(h: dict[str, Any], ev: Evidence) -> Scored:
    """Score one theory against the evidence.

    A theory is *contradicted* the moment it mispredicts a single observed
    outcome — elimination is by evidence, never by preference. Nothing is
    selected while two behavioural classes remain live.
    """
    preds_list = execute(h, ev)
    preds = dict(preds_list)
    errs = interr = npr = ninter = 0
    for step, outcome in ev.pushes:
        p = preds.get(step)
        if p is None:
            continue
        npr += 1
        wrong = p != outcome
        errs += wrong
        if step in ev.interventional:
            ninter += 1
            interr += wrong
    pred_loss = errs / npr if npr else 1.0
    interv_loss = interr / ninter if ninter else 0.0
    dl = description_length(h)
    j = pred_loss + LAMBDA_INTERVENTION * interv_loss + LAMBDA_DL * dl
    status = "underdetermined"
    if npr >= 1 and errs > 0:
        status = "contradicted"
    elif ninter >= 2 and interr == 0 and npr >= 3:
        status = "supported"
    return Scored(h, status, j, dl, npr, tuple(preds_list))


# -------------------------------------------------------------- grammar


def role_map(handles: list[Handle]) -> tuple[dict[str, Handle], list[str]]:
    """Anonymous role ids.

    The hypothesis language sees r0..rN and never a handle name, so a theory
    cannot borrow meaning from a label.
    """
    mapping = {f"r{i}": h for i, h in enumerate(handles)}
    return mapping, [*mapping.keys(), "rT"]


def _hyp(i: int, roles: list[str], lats: list[dict], cond: dict) -> dict[str, Any]:
    return {
        "hypothesis_id": f"c{i}",
        "roles": roles,
        "target_role": "rT",
        "latents": lats,
        "condition": cond,
    }


def code_grammar(roles: list[str], max_conj: int = 2) -> list[dict[str, Any]]:
    """Enumerate the candidate theory space over anonymous roles.

    `const False` is the "nothing in this action space helps" theory. Its
    survival is a representation failure, not a source-defect finding — the
    distinction M0 exists to preserve.
    """
    obj = [r for r in roles if r != "rT"]
    atoms: list[tuple[list[dict], dict]] = [([], {"const": False}), ([], {"const": True})]
    for i, r in enumerate(obj):
        atoms.append(
            (
                [
                    {
                        "name": f"z{i}",
                        "type": "bool",
                        "init": False,
                        "set_on": {"event": "applied", "role": r},
                        "reset_on": None,
                    }
                ],
                {"var": f"z{i}"},
            )
        )
        # ...and its negation. Without this the grammar can only express
        # *remedies* — handles whose application makes the target pass — and an
        # order-dependent failure, where applying the handle is what *causes*
        # the failure, has no expressible theory at all. Every candidate would
        # then be contradicted and the verdict would be
        # `representation_inadequate` for a cause the action space actually
        # contains. The IR already supports `not`; only the enumeration was
        # missing it.
        atoms.append(
            (
                [
                    {
                        "name": f"n{i}",
                        "type": "bool",
                        "init": False,
                        "set_on": {"event": "applied", "role": r},
                        "reset_on": None,
                    }
                ],
                {"op": "not", "arg": {"var": f"n{i}"}},
            )
        )
    for n in (2, 3, 4):
        atoms.append(
            (
                [
                    {
                        "name": f"k{n}",
                        "type": "counter",
                        "max": 16,
                        "inc_on": {"event": "run", "role": None},
                    }
                ],
                {"op": "ge", "var": f"k{n}", "value": n},
            )
        )
    out: list[dict[str, Any]] = []
    for lats, cond in atoms:
        out.append(_hyp(len(out), roles, lats, cond))
    real = [(la, c) for la, c in atoms if "const" not in c]
    if max_conj >= 2:
        for (l1, c1), (l2, c2) in itertools.combinations(real, 2):
            if {x["name"] for x in l1} & {x["name"] for x in l2}:
                continue
            # Conjunction: two stacked causes. Disjunction: either suffices,
            # which is required because discovered handles form a hierarchy —
            # a directory handle subsumes a file handle.
            out.append(_hyp(len(out), roles, l1 + l2, {"op": "and", "args": [c1, c2]}))
            out.append(_hyp(len(out), roles, l1 + l2, {"op": "or", "args": [c1, c2]}))
    return out


# -------------------------------------------------------------- probe economics


@dataclass(frozen=True)
class Probe:
    fresh: bool
    applied: tuple[str, ...]
    repeats: int

    @property
    def name(self) -> str:
        where = "fresh" if self.fresh else "same"
        return f"{where}[{'+'.join(self.applied) or 'none'}]x{self.repeats}"

    @property
    def est_cost(self) -> float:
        return self.repeats + (0.5 if self.fresh else 0.0) + 0.2 * len(self.applied)


def generate_probes(roles: list[str], max_pairs: int = 3) -> list[Probe]:
    """The candidate probe set.

    Every policy receives this identical list — that equality is what makes the
    benchmark's B-versus-C comparison a test of probe *selection* rather than of
    probe availability.
    """
    obj = [r for r in roles if r != "rT"]
    probes = [Probe(True, (), 1), Probe(True, (), 3), Probe(False, (), 1)]
    probes += [Probe(True, (r,), 1) for r in obj]
    probes += [Probe(True, (a, b), 1) for a, b in list(itertools.combinations(obj, 2))[:max_pairs]]
    return probes


def js_divergence(dists: list[dict[str, float]]) -> float:
    if len(dists) < 2:
        return 0.0
    keys = {k for d in dists for k in d}
    m = {k: sum(d.get(k, 0.0) for d in dists) / len(dists) for k in keys}

    def kl(p: dict[str, float], q: dict[str, float]) -> float:
        return sum(pv * math.log(pv / q[k]) for k, pv in p.items() if pv > 0 and q.get(k, 0) > 0)

    return sum(kl(d, m) for d in dists) / len(dists)


def predict_probe(sc: Scored, probe: Probe, ev: Evidence) -> str | None:
    """Simulate a probe against one theory's latent program."""
    sim = ev.copy()
    if probe.fresh:
        sim.new_episode()
    sim.record(probe.applied, probe.repeats, "?")
    later = [o for s, o in execute(sc.hypothesis, sim) if s > ev.step]
    return later[-1] if later else None


def future_classes(live: list[Scored], probes: list[Probe], ev: Evidence) -> list[Scored]:
    """Group theories that are behaviourally identical *and would stay identical*
    under every candidate probe.

    Two theories in one class cannot be told apart by any experiment available,
    so a surviving class — not a surviving theory — is the unit of remaining
    ambiguity.
    """
    groups: dict[tuple, Scored] = {}
    for sc in sorted(live, key=lambda s: (s.j, s.dl)):
        key = (sc.predictions, tuple(predict_probe(sc, p, ev) for p in probes))
        groups.setdefault(key, sc)
    return list(groups.values())


def select_probe(policy: str, probes: list[Probe], live: list[Scored], ev: Evidence, rng: random.Random) -> Probe:
    """Choose the next experiment.

    `disagreement` maximises Jensen-Shannon divergence of predicted outcomes per
    unit estimated cost: run the one command whose result the surviving theories
    disagree about most, per unit of budget spent. This is the only intended
    independent variable between benchmark arms B and C.
    """
    if policy == "random":
        return rng.choice(probes)
    if policy == "cheapest":
        return min(probes, key=lambda p: p.est_cost)
    if policy != "disagreement":
        raise ValueError(f"unknown probe policy {policy!r}")
    best, best_key = probes[0], (-1.0, 0.0)
    for p in probes:
        preds = [x for x in (predict_probe(sc, p, ev) for sc in live) if x is not None]
        if not preds:
            continue
        d = js_divergence([{x: 1.0} for x in preds])
        key = (d / p.est_cost, -p.est_cost)
        if key > best_key:
            best, best_key = p, key
    return rng.choice(probes) if best_key[0] <= 0.0 else best


# -------------------------------------------------------------- discovery

_IDENT = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")
_NOISE = frozenset(
    {
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
        "MODULE",
        "IMPORT",
        "COLUMNS",
        "LINES",
    }
)
_STANDARD_ENV = frozenset(
    {
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
        "COLUMNS",
    }
)
_STATE_HINT = ("cache", "tmp", "state", "lock", "build", "artifact", ".mark")

# Explicit missing-thing evidence. Deliberately narrow: the interpreter names
# what it could not find, and only that name becomes an assertion handle.
_MISSING_MODULE = re.compile(r"No module named ['\"]([\w.]+)['\"]")
_MISSING_PATH = re.compile(r"No such file or directory:? ['\"]([^'\"]+)['\"]")


def _first_handles(collected: list[str], target: str, per_level: int = 3) -> list[Handle]:
    """Coarse-to-fine `run this before the target` handles.

    Coarse handles precede fine ones because a real suite has thousands of
    tests: the handle set is a hierarchy to bisect, not an enumeration.
    """
    others = [n for n in collected if n != target and "::" in n]
    files = sorted({n.split("::")[0] for n in others})
    tgt_file = target.split("::")[0]
    dirs = sorted({f.rsplit("/", 1)[0] + "/" for f in files if "/" in f})
    out = [Handle(Primitive.FIRST, d) for d in dirs[:per_level]]
    out += [Handle(Primitive.FIRST, f) for f in files[:per_level] if f != tgt_file]
    return out


def discover_handles(
    failure_text: str,
    paths: list[str],
    collected: list[str],
    target: str,
    cap: int = 8,
    ambient_env: dict[str, str] | None = None,
    quotas: tuple[int, int, int, int, int] = (2, 2, 2, 4, 2),
) -> list[Handle]:
    """Enumerate pullable handles from observable signals only.

    Four generic sources, none fault-specific. Sources get quotas rather than
    first-come order: on a real repository one source — typically cache
    directories — otherwise floods the budget and crowds out the others.

    This is the step most likely to be the real bottleneck. A cause with no
    handle here can never be hypothesised downstream, which is exactly why the
    resulting abstention is `representation_inadequate` and not a claim about
    the source.
    """
    env_c: list[Handle] = []
    for name in _IDENT.findall(failure_text):
        if name not in _NOISE and all(h.arg != name for h in env_c):
            env_c.append(Handle(Primitive.ENV, name))
    unset_c = [
        Handle(Primitive.UNSETENV, n)
        for n in sorted(ambient_env or {})
        if n not in _STANDARD_ENV and _IDENT.fullmatch(n)
    ]
    clear_c: list[Handle] = []
    seen_dirs: set[str] = set()
    for p in paths:
        if not p.endswith("/"):
            continue
        name = p.rstrip("/").split("/")[-1]
        if name in seen_dirs or not any(h in name.lower() for h in _STATE_HINT):
            continue
        seen_dirs.add(name)
        clear_c.append(Handle(Primitive.CLEAR, name))
    first_c = _first_handles(collected, target)
    # Assertions, from explicit evidence only: the one source that observes
    # rather than intervenes, so a cause found here can never be gated. Proposed
    # only when the failure text names the missing thing outright.
    assert_c: list[Handle] = []
    for kind, pattern in ((Primitive.DEP_ASSERT, _MISSING_MODULE), (Primitive.FILE_ASSERT, _MISSING_PATH)):
        for match in pattern.finditer(failure_text):
            arg = match.group(1)
            if not arg or any(h.arg == arg for h in assert_c):
                continue
            try:
                # Through the same contract a model proposal must pass. A
                # failure message is untrusted text like any other input: it can
                # name `/etc/passwd` or `../../secrets`, and a handle built
                # directly would skip the traversal and absolute-path rules that
                # exist for exactly that.
                assert_c.append(Handle.from_dict({"kind": kind.value, "arg": arg}))
            except ValidationError:
                continue

    out: list[Handle] = []
    seen: set[str] = set()
    for pool, quota in zip((env_c, unset_c, clear_c, first_c, assert_c), quotas, strict=True):
        for h in pool[:quota]:
            if h.label not in seen and len(out) < cap:
                seen.add(h.label)
                out.append(h)
    return out


def refine_first(cause: Handle, collected: list[str], target: str) -> list[Handle]:
    """Split a coarse `first` handle in half for bisection.

    Delta debugging falls out of the same discipline: halve the selection, keep
    the half that still changes the outcome. Every run is charged.
    """
    if cause.kind is Primitive.FIRSTSET:
        inside = sorted(set(cause.arg.split(",")))
    elif cause.kind is Primitive.FIRST and "::" not in cause.arg and cause.arg.endswith("/"):
        inside = sorted({n.split("::")[0] for n in collected if n.startswith(cause.arg)})
    else:
        return []
    inside = [f for f in inside if f != target.split("::")[0]]
    if len(inside) < 2:
        return []
    mid = len(inside) // 2
    return [
        Handle(Primitive.FIRSTSET, ",".join(inside[:mid])),
        Handle(Primitive.FIRSTSET, ",".join(inside[mid:])),
    ]


def _selector_key(label: str) -> frozenset[str]:
    """What a handle runs, independent of how it is spelled."""
    kind, _, arg = label.partition(":")
    if kind in ("first", "firstset"):
        return frozenset(x for x in arg.split(",") if x)
    return frozenset({label})


@dataclass(frozen=True)
class ProbeRecord:
    """One executed experiment, as the ledger recorded it.

    The kernel reads these rather than a signature handed in by the caller. The
    previous shape took a signature parameter, and the application supplied the
    *isolated* baseline observation — which passes for an order-dependent
    failure, so its signature was None and no reproducer was ever built for the
    one class the mechanism exists for.
    """

    applied: frozenset[str]
    reproduced: bool
    signature: Signature | None
    event_id: str


def select_reproducer(
    diagnosis: Diagnosis,
    node_id: str,
    probes: list[ProbeRecord],
    runner_config_hash: str,
    tree_digest: str,
    judge_artifacts: dict[str, str],
) -> ReproductionContract | None:
    """Freeze how to reproduce the failure, from the experiment that showed it.

    The contract is issued only when a *single recorded probe applied exactly
    the selected cause set and reproduced the target-specific failure*. Its
    signature and its event id are what get frozen.

    The exactness matters more than it looks. Combining handles from separate
    experiments would assert that their conjunction reproduces the failure when
    no run ever applied that conjunction — a claim about an experiment that was
    never performed, frozen into the judge that decides every later phase.

    Every other state falls back to the bare target: `underdetermined` did not
    separate the candidates, `representation_inadequate` could not express the
    failure, an observational cause has nothing to apply, and a probe that did
    not reproduce is not support for anything.
    """
    if diagnosis.status is not Verdict.DIAGNOSIS_SUPPORTED:
        return None
    if diagnosis.support is not Support.INTERVENTIONAL:
        return None
    causes = tuple(c for c in diagnosis.causes if c.is_intervention)
    if not causes or len(causes) != len(diagnosis.causes):
        # A mixed cause set cannot be reproduced as a whole: the assertion half
        # cannot be applied, so no probe can ever have applied "exactly" it.
        return None
    wanted = frozenset(_selector_key(c.label) for c in causes)

    for probe in probes:
        # Compared by what was actually run. `first:X` and `firstset:X` compile
        # to the identical argv, and refinement records the narrowed handle in
        # whichever spelling it produced; matching on labels would miss the very
        # experiment that supports the cause.
        if frozenset(_selector_key(a) for a in probe.applied) != wanted:
            continue
        if not probe.reproduced or probe.signature is None:
            continue
        return ReproductionContract(
            preconditions=causes,
            node_id=node_id,
            signature=probe.signature,
            runner_config_hash=runner_config_hash,
            tree_digest=tree_digest,
            supporting_event_ids=(probe.event_id,),
            judge_artifacts=tuple(sorted(judge_artifacts.items())),
        )
    return None


def reproducer_still_valid(contract: ReproductionContract, tree_digest: str, runner_config_hash: str) -> str:
    """Empty string if the frozen reproducer still describes this repository.

    Checked before every phase rather than once at the start: a reproducer that
    silently stopped applying mid-gate would make the remaining phases measure
    a different experiment than the one that was frozen.
    """
    if contract.tree_digest != tree_digest:
        return "the tracked tree changed since the reproducer was frozen"
    if contract.runner_config_hash != runner_config_hash:
        return "the runner configuration changed since the reproducer was frozen"
    return ""


def judge_artifacts_intact(contract: ReproductionContract, observed: dict[str, str]) -> str:
    """Empty string if every frozen executable judge artifact is unchanged.

    A byte-identical contract is not enough. The contract *names* files — the
    target test, and every test a `first`/`firstset` precondition runs — and if
    those files change, the experiment changes while the record that describes
    it does not. A patch editing the polluter test would weaken the reproducer
    and leave no trace in the frozen contract at all.
    """
    for path, frozen in contract.judge_artifacts:
        current = observed.get(path)
        if current is None:
            return f"a frozen reproducer artifact is missing: {path}"
        if current != frozen:
            return f"a frozen reproducer artifact changed: {path}"
    return ""


# -------------------------------------------------------------- diagnosis


def cause_of(h: dict[str, Any], mapping: dict[str, Handle]) -> list[Handle]:
    """Read the handles a surviving theory actually depends on."""
    out: list[Handle] = []
    for la in h.get("latents", []):
        if la["type"] == "bool":
            role = la["set_on"].get("role")
            if role in mapping and mapping[role] not in out:
                out.append(mapping[role])
    return out


def observational_diagnosis(
    absent: list[Handle], notes: list[str], contradicted: tuple[str, ...] = ()
) -> Diagnosis | None:
    """A finding supported by an executed assertion, and gateable by nothing.

    Called only where the action space could not explain the failure. An
    assertion observes: nothing is applied, so nothing can be withdrawn, so the
    gate is `not_applicable` and this is a diagnosis rather than a verified fix.
    None when nothing came back absent, so a dependency that is present is never
    reported as a missing one.
    """
    if not absent:
        return None
    named = ", ".join(h.label for h in absent)
    return Diagnosis(
        Verdict.DIAGNOSIS_SUPPORTED,
        Support.OBSERVATIONAL,
        GateStatus.NOT_APPLICABLE,
        tuple(absent),
        1,
        contradicted,
        (
            *notes,
            f"an assertion executed in the sandbox found {named} missing, which explains the "
            "failure the intervention grammar could not express",
            "an assertion observes rather than intervenes: nothing can be withdrawn, so no "
            "counterfactual gate is possible. This locates a cause; it verifies no fix",
        ),
        remediation_unverified=f"UNVERIFIED: remediation for {named} was not applied, withdrawn, or tested here.",
    )


def derive_diagnosis(
    scored: list[Scored],
    probes: list[Probe],
    ev: Evidence,
    mapping: dict[str, Handle],
    notes: list[str],
) -> Diagnosis:
    """The single place a diagnosis is produced. Deterministic and model-free.

    Carries forward the M0 correction: a surviving `const False` means the
    action space could not explain the failure. That is a property of the
    representation, and it attributes nothing to the repository.
    """
    live = [s for s in scored if s.status != "contradicted"]
    contradicted = tuple(s.hypothesis["hypothesis_id"] for s in scored if s.status == "contradicted")
    classes = future_classes(live, probes, ev) if live else []
    best = min(live, key=lambda s: (s.j, s.dl)) if live else None

    if not live:
        return Diagnosis(
            Verdict.REPRESENTATION_INADEQUATE,
            None,
            GateStatus.NOT_APPLICABLE,
            (),
            0,
            contradicted,
            (
                *notes,
                "every candidate theory was contradicted, including 'nothing here helps'; "
                "the handle set cannot express what is happening",
            ),
        )
    if len(classes) != 1 or best is None:
        return Diagnosis(
            Verdict.UNDERDETERMINED,
            None,
            GateStatus.NOT_APPLICABLE,
            (),
            len(classes),
            contradicted,
            (
                *notes,
                f"{len(classes)} behavioural classes remain live and no available probe "
                "distinguishes them; a cause is not guessed",
            ),
        )
    if best.hypothesis["condition"].get("const") is False:
        return Diagnosis(
            Verdict.REPRESENTATION_INADEQUATE,
            None,
            GateStatus.NOT_APPLICABLE,
            (),
            1,
            contradicted,
            (
                *notes,
                "no handle in the current action space changes the outcome, so this "
                "representation cannot explain the failure. A cause outside the action space "
                "(missing binary, dependency version, locale, parallelism) is indistinguishable "
                "from an unconditional failure at this boundary; the result attributes nothing "
                "to the repository and locates no cause",
            ),
        )

    causes = tuple(cause_of(best.hypothesis, mapping))
    if not causes:
        # A mechanism the IR models (e.g. a retry counter) explains the outcome,
        # but there is no handle to apply or withdraw.
        return Diagnosis(
            Verdict.DIAGNOSIS_SUPPORTED,
            Support.OBSERVATIONAL,
            GateStatus.NOT_APPLICABLE,
            (),
            1,
            contradicted,
            (
                *notes,
                "the surviving theory explains the outcome by a mechanism with no "
                "apply/withdraw intervention, so it cannot be counterfactually gated",
            ),
            remediation_unverified="UNVERIFIED: no remediation was tested for this finding.",
        )
    if all(not c.is_intervention for c in causes):
        return Diagnosis(
            Verdict.DIAGNOSIS_SUPPORTED,
            Support.OBSERVATIONAL,
            GateStatus.NOT_APPLICABLE,
            causes,
            1,
            contradicted,
            (
                *notes,
                "an executable assertion supports this finding, but an assertion observes "
                "rather than intervenes: there is nothing to withdraw, so no counterfactual "
                "gate is possible. This is a diagnosis, never a verified fix",
            ),
            remediation_unverified=(
                "UNVERIFIED: remediation for "
                + ", ".join(c.label for c in causes)
                + " was not applied, withdrawn, or tested by this runtime."
            ),
        )
    # The handle was manipulated, not merely observed: probes ran both with it
    # applied and without it, and the outcome tracked it. That is interventional
    # *evidence for the cause*.
    #
    # It is emphatically not a passed acceptance gate. `why` produces no patch,
    # so there is nothing to apply, withdraw and reapply, and `gate` stays
    # `not_applicable`. Leaving that pairing unexplained would invite exactly
    # the reading the verdict vocabulary exists to prevent — that something was
    # fixed and verified — so the distinction is stated in the receipt rather
    # than left to the reader.
    return Diagnosis(
        Verdict.DIAGNOSIS_SUPPORTED,
        Support.INTERVENTIONAL,
        GateStatus.NOT_APPLICABLE,
        causes,
        1,
        contradicted,
        (
            *notes,
            "the cause was manipulated across probes — applied and withheld — and the target's "
            "outcome tracked it; that is interventional evidence for the cause",
            "no counterfactual acceptance gate ran: `why` produces no patch, so nothing was "
            "applied, withdrawn and reapplied. This locates a cause; it verifies no fix",
        ),
        remediation_unverified=(
            "UNVERIFIED: no remediation for "
            + ", ".join(c.label for c in causes)
            + " was produced or tested by this runtime. Use `rift verify` on a candidate patch."
        ),
    )
