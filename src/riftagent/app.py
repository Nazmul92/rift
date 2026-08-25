"""Fixed application loops, CLI and renderer for `verify`, `why`, `resume` and
`replay`.

`verify` is the acceptance-authority half of the thesis and makes zero model
calls: the diff is an input, and where it came from — a human, a coding agent,
a patch file on disk — is not a question this runtime asks or can answer.

`why` is the diagnosis half. It runs experiments rather than checks and is
model-free by default; a configured provider may only propose *additional
measurements*, which are validated exactly like discovered ones. Nothing it
produces is a fix, and its receipt says so.

Two invariants govern the code below:

* **Write before advance.** Every accepted transition is appended and fsynced
  before the next side effect starts. If the process dies, the absence of the
  event is the truth.
* **Rendering carries no epistemic state.** Every settled line is a pure
  function of the ledger, so replaying a completed ledger reproduces the
  transcript and receipt byte for byte.
"""

from __future__ import annotations

import argparse
import ast
import os
import random
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from riftagent import kernel, llm
from riftagent.checks import (
    ABSENT,
    DEFAULT_PROTECTED_PATHS,
    UNOBSERVABLE,
    ProbeObservation,
    collect_nodes,
    evaluate_assertion,
    observable_paths,
    run_check,
    run_probe,
    runner_config_hash,
)
from riftagent.records import (
    PASS,
    Authorities,
    BudgetRefused,
    Budgets,
    ChangeSet,
    Check,
    CheckResult,
    CheckSet,
    ClaimType,
    Diagnosis,
    Event,
    EventKind,
    GatePhase,
    GateStatus,
    Handle,
    IsolationLevel,
    Ledger,
    LedgerCorrupt,
    ModelUsage,
    Outcome,
    Pricing,
    Primitive,
    ReproductionContract,
    RunnerKind,
    Signature,
    SpendLedger,
    Support,
    TaskContract,
    TaskProjection,
    ValidationError,
    Verb,
    Verdict,
    VerificationReceipt,
    allocate_task_dir,
    canonical,
    canonical_candidate_record,
    canonical_diff,
    canonicalize_patch,
    changeset_record,
    content_hash,
    iter_task_dirs,
    normalize_candidate,
    normalized_candidate_record,
    persist_candidate,
    raw_candidate_record,
    read_events,
    reduce,
    replace,
    short,
    spend_for_task,
    spend_ledger_path,
    task_dir,
    task_fingerprint,
    write_repro,
)
from riftagent.sandbox import (
    IsolationProbe,
    SandboxError,
    Worktree,
    probe_isolation,
    structural_parse,
    tree_hash,
)

EXIT_VERIFIED = 0
EXIT_ABSTAINED = 1
EXIT_REJECTED = 2
EXIT_INFRASTRUCTURE = 3
EXIT_CENSORED = 4
EXIT_CANCELLED = 5
EXIT_USAGE = 64

# Bisection halves the selection each step, so this bounds a 2**12-file suite.
# A bound that is never reached is still a bound: an unbounded narrowing loop
# is an unbounded bill.
MAX_REFINEMENT_STEPS = 12


class ReproducerInvalid(RuntimeError):
    """The frozen reproducer no longer describes this repository.

    Never a verdict about the patch: the experiment itself became
    unrepeatable, so nothing measured after this point would mean anything.
    """


# A traceback frame line: `  File "path/to/mod.py", line 12, in fn`. Context is
# selected by what the failure actually cited, so this is the only "relevance"
# signal in the system and it is an observation, not a score.
_TRACEBACK_FILE = re.compile(
    r'File "(?P<quoted>[^"]+\.py)", line (?P<qline>\d+)'  # stdlib traceback frames
    r"|^(?P<pytest>[\w./\\-]+\.py):(?P<pline>\d+)",  # pytest frame and error lines
    re.MULTILINE,
)

# How much observed failure output is made durable and shown to the model.
FAILURE_EXCERPT_CHARS = 6000


# --------------------------------------------------------------------------
# renderer — a pure projection of the ledger
# --------------------------------------------------------------------------


def _outcome_glyph(outcome: str) -> str:
    return {"passed": "PASS", "failed": "FAIL"}.get(outcome, outcome.upper())


def render_event(ev: Event) -> list[str]:
    """One event in, zero or more settled lines out. No clock, no state."""
    p = ev.payload
    if ev.kind is EventKind.TASK_STARTED:
        return [f"task {p['task_id']}  verb={p['verb']}  repo={p['repo']}"]
    if ev.kind is EventKind.SANDBOX_PROBED:
        return [f"sandbox {p['level']} — {p['detail']}"]
    if ev.kind is EventKind.SANDBOX_AUTHORIZED:
        grant = "explicit --allow-partial-sandbox" if p.get("partial_authorized") else "full isolation, no grant needed"
        return [f"authority {grant}"]
    if ev.kind is EventKind.CHANGESET_REGISTERED:
        cs = p["changeset"]
        return [
            f"patch {short(cs['patch_hash'])}  {len(cs['touched_paths'])} file(s): {', '.join(cs['touched_paths'])}"
        ]
    if ev.kind is EventKind.CHANGESET_RELOADED:
        return [f"  reloaded {p['path']} → {short(p['reloaded_patch_hash'])}"]
    if ev.kind is EventKind.CHANGESET_REJECTED:
        return [f"✗ patch rejected — {p['reason']}"]
    if ev.kind is EventKind.CHECKSET_FROZEN:
        cs = p["checkset"]
        change = sum(1 for c in cs["checks"] if c["claim_type"] == "change")
        preserve = sum(1 for c in cs["checks"] if c["claim_type"] == "preservation")
        return [
            f"checks frozen {short(p['checkset_hash'])}  {change} change, {preserve} preservation",
            f"protected {', '.join(cs['protected_paths'])}",
        ]
    if ev.kind is EventKind.CONTRACT_FROZEN:
        return [f"contract {short(p['contract_hash'])}  tree {short(p['contract']['baseline_tree_hash'])}"]
    if ev.kind is EventKind.DRIFT_DETECTED:
        moved = f"{short(p['recorded'])} → {short(p['observed'])}"
        return [f"! repository drifted since baseline ({moved}); re-establishing evidence"]
    if ev.kind is EventKind.COMMAND_STARTED:
        return [f"▶ {p['display']}  ({p['phase']})"]
    if ev.kind is EventKind.COMMAND_FINISHED:
        return [f"  {p['duration_s']:.1f}s  exit={p['exit_code']}"]
    if ev.kind is EventKind.CHECK_FALLBACK:
        return [
            f"  ! {p['node_id']} could not be collected alone; observed via {p['selector']} ({p['scope_expansion']})"
        ]
    if ev.kind is EventKind.CHECK_RESULT:
        r = p["result"]
        sig = ""
        if r.get("signature"):
            sig = f"  {r['signature']['exception_type']}: {r['signature']['message']}".rstrip()
        counter = f"  [{p['index']}/{p['total']}]" if p.get("total") else ""
        return [f"  → {_outcome_glyph(r['outcome'])}{counter}{sig}"]
    if ev.kind is EventKind.SIGNATURE_FROZEN:
        s = p["signature"]
        return [f"  baseline signature frozen: {s['exception_type']}: {s['message']}".rstrip()]
    if ev.kind is EventKind.GATE_PHASE_FINISHED:
        mark = "✓" if p["passed"] else "✗"
        reason = f" — {p['reason']}" if p.get("reason") else ""
        return [f"{mark} gate {p['phase']}{reason}"]
    if ev.kind is EventKind.INFRASTRUCTURE_BLOCKED:
        return [f"✗ infrastructure_blocked — {p['reason']}"]
    if ev.kind is EventKind.BUDGET_EXHAUSTED:
        return [f"! budget exhausted — {p.get('reason') or p.get('limit', 'budget')}"]
    if ev.kind is EventKind.CONTEXT_SELECTED:
        note = f" — {p['note']}" if p.get("note") else ""
        if "files" in p:
            # The context manifest for a model request: what was sent, and what
            # was withheld and why. Both halves matter to a reader auditing it.
            files = ", ".join(p["files"]) or "none"
            line = f"context {len(p['files'])} file(s), {p.get('chars', 0)} chars: {files}"
            skipped = p.get("skipped") or []
            return [line] + ([f"  withheld: {', '.join(skipped)}"] if skipped else [])
        return [f"context {p.get('collected_nodes', 0)} collected node(s){note}"]
    if ev.kind is EventKind.HANDLES_DISCOVERED:
        labels = [h["kind"] + ":" + h["arg"] for h in p.get("handles", [])]
        return [f"handles {p['count']} discovered: {', '.join(labels) or 'none'}"]
    if ev.kind is EventKind.HYPOTHESES_PROPOSED:
        return [
            f"theories {p['count']} over {len(p.get('roles', []))} role(s), {p['probe_candidates']} probe(s) available"
        ]
    if ev.kind is EventKind.PROBE_SELECTED:
        obs = p.get("observation", {})
        applied = ", ".join(p.get("applied", [])) or "nothing"
        return [f"  probe {p['probe']}  applied {applied} → {obs.get('outcome', '?')}"]
    if ev.kind is EventKind.HYPOTHESES_ELIMINATED:
        return [f"  eliminated {p['count']} theory/theories via {p['by_probe']}"]
    if ev.kind is EventKind.CAUSE_SUPPORTED:
        causes = ", ".join(c["kind"] + ":" + c["arg"] for c in p.get("causes", []))
        return [f"cause supported ({p.get('support')}, gate {p.get('gate')}): {causes}"]
    if ev.kind is EventKind.DIAGNOSIS_EMITTED:
        return [f"diagnosis {p['diagnosis']['status']} for {p['target']}"]
    if ev.kind is EventKind.MODEL_REQUEST_STARTED:
        return [f"model → {p['operation']} ({p['model']})"]
    if ev.kind is EventKind.MODEL_RESPONSE_RECEIVED:
        usage = p.get("usage") or {}
        rendered = (
            f"{usage.get('input_tokens')} in / {usage.get('output_tokens')} out"
            if usage.get("input_tokens") is not None and usage.get("output_tokens") is not None
            else "unknown"
        )
        return [f"model ← {p['operation']}  usage {rendered}  finish={p.get('finish_reason')}"]
    if ev.kind is EventKind.MODEL_RESPONSE_INVALID:
        return [f"model ✗ {p['operation']} rejected — {p['reason']}"]
    if ev.kind is EventKind.MODEL_UNAVAILABLE:
        return [f"model unavailable ({p.get('operation', 'model')}) — {p['reason']}"]
    if ev.kind is EventKind.RECEIPT_EMITTED:
        return render_receipt(p["receipt"])
    return []


def render_receipt(receipt: dict) -> list[str]:
    verdict = receipt["verdict"]
    good = (Verdict.VERIFIED_AGAINST_APPROVED_CHECKS.value, Verdict.DIAGNOSIS_SUPPORTED.value)
    mark = "✓" if verdict in good else "✗"
    lines = ["", f"{mark} {verdict.replace('_', ' ').capitalize()}"]
    if receipt.get("reason"):
        lines.append(f"  {receipt['reason']}")
    results = receipt.get("results", [])
    # A diagnosis has no gate and no preservation set. Printing "none declared"
    # for a verb that never declares them would read as an omission rather than
    # as inapplicability.
    if not receipt.get("diagnosis"):
        change = [r for r in results if r["phase"] in ("baseline", "candidate", "withdrawal")]
        preserve = [r for r in results if r["phase"] == "preservation"]
        if change:
            lines.append(f"  Counterfactual gate:  {_gate_summary(receipt)}")
        passed = sum(1 for r in preserve if r["outcome"] == "passed")
        lines.append(
            f"  Preservation checks:  {passed}/{len(preserve)} passed"
            if preserve
            else "  Preservation checks:  none declared"
        )
    else:
        lines.append(f"  Experiments run:      {len(results)} observation(s)")
    for item in receipt.get("checks_not_executed", []):
        lines.append(f"  NOT run:              {item}")
    lines.append(f"  Sandbox:              {receipt['sandbox']} — {receipt['sandbox_detail']}")
    auth = receipt["authorities"]
    lines.append(f"  Authorities:          spec={auth['spec_approval']}  partial_sandbox={auth['partial_sandbox']}")
    lines.append(
        f"  Spend:                {receipt['commands']} commands, {receipt['seconds']:.1f}s, tokens {receipt['tokens']}"
    )
    if receipt.get("censored"):
        lines.append("  Censored:             budget exhausted before completion")
    if receipt.get("patch_hash") or receipt.get("checkset_hash"):
        lines.append(
            f"  patch {short(receipt['patch_hash'])}  checks {short(receipt['checkset_hash'])}"
            f"  contract {short(receipt['contract_hash'])}"
        )
    else:
        lines.append(f"  contract {short(receipt['contract_hash'])}")
    if receipt.get("repair_basis"):
        lines.append(f"  Repair basis:         {receipt['repair_basis']}  (diagnosis: {receipt['diagnosis']})")
        lines.append(f"  Reproducer:           {receipt['reproducer']}")
        lines.append(f"  Claim scope:          {receipt['claim_scope']}")
    diagnosis = receipt.get("diagnosis")
    if isinstance(diagnosis, dict):
        causes = ", ".join(c["kind"] + ":" + c["arg"] for c in diagnosis.get("causes", []))
        lines.append(f"  Cause:                {causes or 'none located'}")
        lines.append(
            f"  Support:              {diagnosis.get('support') or 'not_applicable'}   gate: {diagnosis.get('gate')}"
        )
        lines.append(f"  Theories eliminated:  {len(diagnosis.get('contradicted', []))}")
        if diagnosis.get("remediation_unverified"):
            lines.append(f"  Remediation (UNVERIFIED): {diagnosis['remediation_unverified']}")
        for note in diagnosis.get("notes", []):
            lines.append(f"  note: {note}")
    for note in receipt.get("remaining_uncertainty", []):
        lines.append(f"  uncertainty: {note}")
    return lines


def _gate_summary(receipt: dict) -> str:
    order = ["baseline", "candidate", "withdrawal", "reapply"]
    seen = {r["phase"]: r["outcome"] for r in receipt.get("results", [])}
    parts = []
    for phase in order:
        if phase == "reapply":
            continue
        if phase in seen:
            parts.append(f"{phase}={_outcome_glyph(seen[phase])}")
    return " → ".join(parts) if parts else "not executed"


def render_settled(events: list[Event]) -> str:
    lines: list[str] = []
    for ev in events:
        lines.extend(render_event(ev))
    return "\n".join(lines) + "\n"


class LiveRenderer:
    """Prints exactly the settled lines as they are appended, plus transient
    elapsed ticks that make no claim and never enter the transcript."""

    def __init__(self, stream=None, quiet: bool = False):
        self.stream = stream or sys.stdout
        self.quiet = quiet
        self._tick_active = False

    def emit(self, ev: Event) -> None:
        if self.quiet:
            return
        self._clear_tick()
        for line in render_event(ev):
            print(line, file=self.stream)
        self.stream.flush()

    def tick(self, label: str, started: float) -> None:
        if self.quiet or not sys.stderr.isatty():
            return
        elapsed = time.time() - started
        sys.stderr.write(f"\r  … {label} {elapsed:5.1f}s")
        sys.stderr.flush()
        self._tick_active = True

    def _clear_tick(self) -> None:
        if self._tick_active:
            sys.stderr.write("\r" + " " * 60 + "\r")
            sys.stderr.flush()
            self._tick_active = False


# --------------------------------------------------------------------------
# the verify flow
# --------------------------------------------------------------------------


@dataclass
class VerifyRequest:
    repo_root: Path
    diff_path: Path
    node_id: str
    preserve: tuple[str, ...]
    budgets: Budgets
    allow_partial: bool
    require_full: bool
    allow_network: bool
    task_id: str


class Flow:
    def __init__(self, ledger: Ledger, renderer: LiveRenderer, probe: IsolationProbe, allow_network: bool):
        self.ledger = ledger
        self.renderer = renderer
        self.probe = probe
        self.allow_network = allow_network
        self.last_failure_text = ""

    def append(self, kind: EventKind, payload: dict | None = None) -> Event:
        ev = self.ledger.append(kind, payload or {})
        self.renderer.emit(ev)
        return ev

    def projection(self) -> TaskProjection:
        events, truncated = read_events(self.ledger.path)
        return reduce(events, truncated)

    # -- one check, fully recorded -------------------------------------

    def execute(
        self,
        check: Check,
        worktree: Worktree,
        phase: GatePhase,
        index: int = 1,
        total: int = 1,
        record: bool = True,
    ) -> CheckResult:
        """Run one check. With `record=False` the outcome is returned but not
        appended, so the caller can validate the experiment's integrity first —
        an outcome recorded before that check is evidence about a judge that may
        no longer exist."""
        started = time.time()

        def on_start(argv: list[str], selector: str | None) -> None:
            # One event per command actually issued, so a widened observation
            # is charged and visible rather than folded into the first one.
            display = f"pytest {selector or check.node_id}"
            self.append(
                EventKind.COMMAND_STARTED,
                {
                    "display": display,
                    "phase": phase.value,
                    "node_id": check.node_id,
                    "selector": selector or check.node_id,
                },
            )
            self.renderer.tick(display, started)

        def on_done(argv: list[str], res) -> None:
            self.append(
                EventKind.COMMAND_FINISHED,
                {"duration_s": round(res.duration_s, 3), "exit_code": res.exit_code, "phase": phase.value},
            )

        result, raw = run_check(
            check, worktree, phase, self.probe, self.allow_network, on_start=on_start, on_done=on_done
        )
        # A disposable cache of the last observed output, for the one caller
        # that appends its own CHECK_RESULT after validating the reproducer.
        # The durable record is still the event, written immediately below or
        # by that caller; nothing reads this across a process boundary.
        self.last_failure_text = raw.combined if result.outcome is not Outcome.PASSED else ""
        if result.fallback:
            self.append(
                EventKind.CHECK_FALLBACK,
                {
                    "node_id": check.node_id,
                    "selector": result.fallback,
                    "scope_expansion": "single node → its containing file",
                    "phase": phase.value,
                },
            )
        if record:
            self.append(EventKind.CHECK_RESULT, self.check_payload(result, index, total))
        return result

    def check_payload(self, result: CheckResult, index: int, total: int) -> dict[str, Any]:
        """What a CHECK_RESULT carries. One definition, three call sites.

        A baseline failure also carries a bounded excerpt of the observed
        output. A signature identifies a failure; it does not show where the
        failure happened, and context selection needs the frames. Arm A never
        runs diagnosis, which was the only place this was recorded, so it was
        shown less source than arms B and C for the same failure — the arms
        differed in what they could see rather than in what they did with it
        (DAR-022).
        """
        payload: dict[str, Any] = {"result": result.to_dict(), "index": index, "total": total}
        if result.phase is GatePhase.BASELINE and self.last_failure_text:
            payload["failure_excerpt"] = self.last_failure_text[-FAILURE_EXCERPT_CHARS:]
        return payload

    def finish_phase(self, phase: GatePhase, decision: kernel.PhaseDecision, artifacts: dict | None = None) -> bool:
        self.append(
            EventKind.GATE_PHASE_FINISHED,
            {
                "phase": phase.value,
                "passed": decision.passed,
                "reason": decision.reason,
                "artifacts": artifacts or {},
            },
        )
        if not decision.passed and decision.infrastructure:
            self.append(EventKind.INFRASTRUCTURE_BLOCKED, {"reason": decision.reason})
        return decision.passed


def build_checkset(node_id: str, preserve: tuple[str, ...], repo_root: Path, timeout_s: float) -> CheckSet:
    """The judge for `verify`.

    The target's own test file is protected alongside the runner configuration.
    Without that, a patch could satisfy the change check by editing the test
    that states it — the model-satisfies-its-own-judge failure, arriving from
    outside instead of from a model.
    """
    checks = [
        Check(
            check_id="change-0",
            claim_type=ClaimType.CHANGE,
            runner=RunnerKind.PYTEST,
            node_id=node_id,
            expected_baseline=Outcome.FAILED,
            expected_candidate=Outcome.PASSED,
            timeout_s=timeout_s,
            scope="the single target node supplied on the command line",
        )
    ]
    for i, node in enumerate(preserve):
        checks.append(
            Check(
                check_id=f"preserve-{i}",
                claim_type=ClaimType.PRESERVATION,
                runner=RunnerKind.PYTEST,
                node_id=node,
                expected_baseline=Outcome.PASSED,
                expected_candidate=Outcome.PASSED,
                timeout_s=timeout_s,
                scope="declared by the caller",
            )
        )
    protected = set(DEFAULT_PROTECTED_PATHS)
    for check in checks:
        protected.add(check.node_id.split("::")[0])
    return CheckSet(
        checks=tuple(checks),
        protected_paths=tuple(sorted(protected)),
        runner_config_hash=runner_config_hash(repo_root),
        provenance="derived from the command line; no model involvement",
    )


def _repair_basis(proj: TaskProjection) -> dict[str, Any]:
    """What the repair was built on, and how far the receipt may be read.

    A patch proposed from a *located cause under a frozen reproducer* and a
    patch proposed from *a reproducible failure with no supported diagnosis*
    both pass the identical gate. They are not the same claim, and rendering
    them identically would let the weaker one borrow the stronger one's
    standing. The distinction is the provenance of the proposal; the gate's
    authority over both is unchanged.
    """
    if proj.contract is None or proj.contract.verb is not Verb.FIX:
        return {}
    diagnosis = proj.diagnosis
    supported = (
        diagnosis is not None
        and diagnosis.status is Verdict.DIAGNOSIS_SUPPORTED
        and diagnosis.support is Support.INTERVENTIONAL
        and proj.reproducer is not None
    )
    if supported:
        assert proj.reproducer is not None
        return {
            "repair_basis": "cause_supported",
            "diagnosis": "supported",
            "reproducer": proj.reproducer.render(),
            "reproducer_hash": proj.reproducer.content_hash,
            "claim_scope": (
                "the located cause was applied and withheld across probes, and the patch was gated "
                "against a reproducer frozen from that evidence"
            ),
        }
    return {
        "repair_basis": "diagnosis_unresolved",
        "diagnosis": "unresolved",
        "reproducer": proj.reproducer.render() if proj.reproducer else "bare target",
        "reproducer_hash": proj.reproducer.content_hash if proj.reproducer else "",
        "claim_scope": (
            "no cause was located; this repair may claim only that it satisfies its frozen change "
            "and preservation checks, and nothing about why the failure occurred"
        ),
    }


def _spend_summary(flow: Flow, proj: TaskProjection) -> dict[str, Any]:
    """Derive this task's spend by joining its references to the spend ledger.

    The task ledger records only request ids and spend-event ids. The figures
    live in `.rift/spend.jsonl`, which is the authoritative record; reading
    them from anywhere else would make a copied number authoritative, and two
    sources of truth eventually disagree.
    """
    scope = proj.scope
    if not scope or not proj.contract:
        return {"authoritative": ".rift/spend.jsonl", "scope": scope, "requests": 0, "charged_usd": 0.0}
    summary = spend_for_task(spend_ledger_path(Path(proj.contract.repo_root)), scope, proj.task_id)
    summary["scope"] = scope
    summary["authoritative"] = ".rift/spend.jsonl"
    return summary


def emit_receipt(flow: Flow, proj: TaskProjection, td: Path) -> tuple[dict, int]:
    decision = kernel.derive_verdict(proj)
    contract = proj.contract
    receipt = VerificationReceipt(
        task_id=proj.task_id,
        verdict=decision.verdict,
        reason=decision.reason,
        contract_hash=contract.content_hash if contract else "",
        checkset_hash=proj.checkset.content_hash if proj.checkset else "",
        patch_hash=proj.changeset.patch_hash if proj.changeset else "",
        baseline_signature=proj.baseline_signature,
        results=tuple(proj.results),
        checks_not_executed=kernel.checks_not_executed(proj),
        sandbox=contract.actual_sandbox if contract else (proj.sandbox or IsolationLevel.PARTIAL),
        sandbox_detail=proj.sandbox_detail,
        authorities=contract.authorities if contract else Authorities(),
        commands=proj.commands,
        seconds=proj.seconds,
        tokens="not_applicable (no model is invoked by verify)",
        censored=proj.censored,
        remaining_uncertainty=decision.uncertainty,
        benchmark_ablation=proj.ablation,
    ).to_dict()
    receipt["rejected_phase"] = decision.rejected_phase.value if decision.rejected_phase else None
    receipt["spend"] = _spend_summary(flow, proj)
    receipt.update(_repair_basis(proj))
    flow.append(EventKind.RECEIPT_EMITTED, {"receipt": receipt})
    write_artifacts(td, flow.ledger.path, receipt, proj)
    return receipt, decision.exit_code


def write_artifacts(td: Path, ledger_path: Path, receipt: dict, proj: TaskProjection) -> None:
    """Derived files only. Each is reproducible from the ledger; none is state."""
    events, _ = read_events(ledger_path)
    (td / "receipt.json").write_text(canonical(receipt) + "\n", encoding="utf-8", newline="\n")
    (td / "receipt.txt").write_text(
        "\n".join(render_receipt(receipt)).lstrip("\n") + "\n", encoding="utf-8", newline="\n"
    )
    (td / "transcript.txt").write_text(render_settled(events), encoding="utf-8", newline="\n")
    if proj.contract is not None:
        (td / "task-contract.json").write_text(
            canonical(proj.contract.to_dict()) + "\n", encoding="utf-8", newline="\n"
        )
    if proj.checkset is not None:
        (td / "check-set.json").write_text(canonical(proj.checkset.to_dict()) + "\n", encoding="utf-8", newline="\n")
    # change-set.diff is the durable record written when the patch was
    # accepted. It is deliberately NOT rewritten here: regenerating it would
    # quietly repair a tampered record instead of leaving the evidence of the
    # tamper in place for the reapply phase to have caught.
    if proj.changeset is not None and not changeset_record(td).exists():
        changeset_record(td).write_text(proj.changeset.diff, encoding="utf-8", newline="\n")
    argvs = [["python", "-m", "pytest", "-q", c.node_id] for c in (proj.checkset.checks if proj.checkset else ())]
    write_repro(td / "repro.sh", argvs)


def _probe_records(flow: Flow) -> list[kernel.ProbeRecord]:
    """Every executed experiment, read back from the ledger.

    The kernel selects the reproducer from these. Reading them from the ledger
    rather than from a variable means the frozen contract is derived from what
    was durably recorded, and a resumed run derives the identical one.
    """
    events, _ = read_events(flow.ledger.path)
    out: list[kernel.ProbeRecord] = []
    for ev in events:
        if ev.kind is not EventKind.PROBE_SELECTED:
            continue
        obs = ev.payload.get("observation") or {}
        raw = obs.get("signature")
        out.append(
            kernel.ProbeRecord(
                applied=frozenset(ev.payload.get("applied", [])),
                reproduced=obs.get("outcome") != PASS and obs.get("node_outcome") in ("passed", "failed"),
                signature=Signature.from_dict(raw) if raw else None,
                event_id=ev.event_id,
            )
        )
    return out


def judge_artifact_paths(
    node_id: str, preconditions: tuple[Handle, ...], collected: list[str], repo_root: Path
) -> list[str] | None:
    """The executable files this reproducer runs, or None if they cannot be
    resolved.

    A `first:` selector may name a directory. Freezing the directory string
    would record `<absent>` as its hash and call that protected evidence, while
    every test file inside it stayed editable — protection in name only. The
    directory is therefore resolved against the collected node ids into the
    actual files it selects.

    Returning None refuses contract construction. A reproducer whose executable
    artifact set cannot be pinned down is not a frozen judge, and issuing one
    anyway would be the most damaging kind of error here: it would look
    rigorous.

    Explicit artifacts only. Production source imported by those tests stays
    editable — it is what a repair is *for*, and protecting it recursively
    would freeze the repository and make every fix impossible.
    """
    files = {n.split("::")[0] for n in collected if "::" in n}
    paths = {node_id.split("::")[0]}
    for handle in preconditions:
        selectors = [x for x in handle.arg.split(",") if x] if handle.kind is Primitive.FIRSTSET else [handle.arg]
        for selector in selectors:
            resolved = _resolve_selector(selector, repo_root, files)
            if resolved is None:
                return None
            paths |= resolved
    return sorted(paths)


def _resolve_selector(selector: str, repo_root: Path, collected_files: set[str]) -> set[str] | None:
    """The executable test files one selector runs, or None if unresolvable.

    Resolution is by what the path *is*, not by how it was spelled. Keying on a
    trailing slash left `first:tests` — the same directory, one character
    different — added verbatim and hashed as `<absent>`, which is protection in
    name only.

    A pytest node id is reduced to its file; a file resolves to itself; a
    directory expands to the collected test files beneath it. Anything that
    resolves to nothing returns None, which refuses the contract: a reproducer
    whose executable artifacts cannot be named is not a frozen judge.
    """
    bare = selector.split("::")[0].rstrip("/")
    if not bare:
        return None
    target = repo_root / bare
    if target.is_file():
        return {bare}
    if target.is_dir() or selector.endswith("/"):
        prefix = bare + "/"
        inside = {f for f in collected_files if f.startswith(prefix)}
        return inside or None
    # Neither on disk nor a directory: accept it only if collection saw it.
    return {bare} if bare in collected_files else None


def hash_artifacts(repo_root: Path, paths: list[str]) -> dict[str, str]:
    """Content hash per path. A missing file hashes to a marker rather than
    raising, so deletion is detected as a change instead of as a crash."""
    out: dict[str, str] = {}
    for rel in paths:
        path = repo_root / rel
        out[rel] = content_hash(path.read_bytes()) if path.is_file() else "<absent>"
    return out


def reset_episode(wt: Worktree, patch_paths: frozenset[str]) -> tuple[int, int]:
    """Return the worktree to this phase's expected file set. Fail closed.

    Three categories, and the previous version collapsed two of them. It kept
    only files present when the worktree was materialised, which meant **a file
    the patch adds was deleted before the target ran** — every repair that
    introduces a module failed, and failed as a plausible behavioural failure
    rather than an error, so the gate blamed the patch.

    The discriminator is not *present at materialisation*. It is *produced by
    applying the frozen patch*, and `ChangeSet.touched_paths` already says which
    those are:

    * baseline files — kept, and restored if execution modified them;
    * patch-touched paths — kept exactly as the patch left them;
    * anything else — created by executed repository code, and removed.

    Returns `(removed, restored)`. Raises `SandboxError` rather than swallowing
    the error: a reset that could not complete must not be recorded as a clean
    episode, because every later phase would then be measured against state
    this function claimed to have cleared.
    """
    baseline = wt.tracked_manifest()
    removed = restored = 0
    for path in sorted(wt.path.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        rel = path.relative_to(wt.path).as_posix()
        if ".git" in path.parts or rel in patch_paths:
            continue
        if rel in baseline:
            # A pre-existing file the patch does not own. If execution changed
            # it, restore it from the source the worktree was materialised from.
            if not path.is_file():
                continue
            origin = wt.repo_root / rel
            try:
                if origin.is_file() and origin.read_bytes() != path.read_bytes():
                    path.write_bytes(origin.read_bytes())
                    restored += 1
            except OSError as exc:
                raise SandboxError(f"episode reset could not restore {rel}: {exc}") from exc
            continue
        try:
            if path.is_dir():
                if not any(path.iterdir()):
                    path.rmdir()
                    removed += 1
            else:
                path.unlink()
                removed += 1
        except OSError as exc:
            raise SandboxError(f"episode reset could not remove {rel}: {exc}") from exc
    return removed, restored


def _validate_reproducer(
    flow: Flow,
    wt: Worktree,
    phase: GatePhase,
    reproducer: ReproductionContract,
    source_digest: str,
    expected_tree: str | None,
    when: str,
    patch_paths: frozenset[str] = frozenset(),
) -> None:
    """Four independent checks, run before and after every episode.

    Each compares the frozen contract against something *observed now*. The
    previous version passed `reproducer.tree_digest` as the observed digest,
    which compared the frozen value with itself and could never fail — a guard
    that existed, was called, and enforced nothing.

    The after-run check is not redundant with the before-run one. Repository
    code executes between them, and a precondition test that rewrites the
    target test mid-episode would otherwise be measured, pass, and be recorded
    as evidence about a judge that no longer exists.
    """
    problems = [
        kernel.reproducer_still_valid(reproducer, source_digest, runner_config_hash(wt.path)),
        kernel.judge_artifacts_intact(reproducer, hash_artifacts(wt.path, [a for a, _ in reproducer.judge_artifacts])),
    ]
    if expected_tree is not None and wt.phase_state_hash(patch_paths) != expected_tree:
        # A legitimate candidate change is expected by the caller and passed in
        # as `expected_tree`; anything else is the tree having moved under the
        # experiment.
        problems.append(f"the {phase.value} worktree does not match its expected state")
    reason = next((p for p in problems if p), "")
    if reason:
        flow.append(
            EventKind.INFRASTRUCTURE_BLOCKED,
            {"reason": reason, "phase": phase.value, "checked": when},
        )
        raise ReproducerInvalid(reason)


def run_episode(
    flow: Flow,
    wt: Worktree,
    check: Check,
    phase: GatePhase,
    reproducer: ReproductionContract | None,
    repo_root: Path | None = None,
    expected_tree: str | None = None,
    state_paths: frozenset[str] = frozenset(),
    patch_owned: frozenset[str] = frozenset(),
) -> CheckResult:
    """Execute one gate phase as a clean, deterministic episode.

        reset disposable runtime state
        → apply the frozen precondition handles
        → execute the frozen target
        → record the target-specific outcome

    When a reproducer is frozen, every phase runs *the same* preconditions and
    the same target. That is what makes an order-dependent failure gateable at
    all: the bare target passes in isolation, so without preconditions the
    baseline never reproduces and no patch for it can be verified.

    The patch state of the tree is established by the caller and is not touched
    here — this function makes the *measurement* identical across phases, not
    the tree.
    """
    # Observed now, immediately before this phase executes. A digest computed
    # once at gate entry cannot see drift that happens mid-gate, and a digest
    # read back off the contract cannot see drift at all.
    source_digest = tree_hash(repo_root) if repo_root is not None else ""
    # `state_paths` is what the phase-state hash covers — the same universe in
    # every phase, so the values are comparable. `patch_owned` is what the reset
    # must preserve, which is empty at baseline and withdrawal because no patch
    # is applied there. Collapsing the two made withdrawal validate a
    # manifest-only hash against a manifest-plus-touched expectation, which can
    # only ever match when the patch adds no file.
    if reproducer is not None:
        _validate_reproducer(flow, wt, phase, reproducer, source_digest, expected_tree, "before", state_paths)

    try:
        cleared, restored = reset_episode(wt, patch_owned)
    except SandboxError as exc:
        # No successful EPISODE_RESET is emitted. The phase never started from a
        # clean episode, so nothing measured after it would mean anything, and
        # there is nothing a different patch could correct.
        flow.append(EventKind.INFRASTRUCTURE_BLOCKED, {"reason": str(exc), "phase": phase.value})
        raise ReproducerInvalid(str(exc)) from exc
    # Appended only once the reset succeeded. The durable claim is that this
    # phase started from a clean episode.
    flow.append(
        EventKind.EPISODE_RESET,
        {
            "phase": phase.value,
            "cleared": cleared,
            "restored": restored,
            "patch_owned": sorted(patch_owned),
            "scope": "runtime-created state; baseline and patch-owned files preserved",
        },
    )
    if reproducer is None:
        return flow.execute(check, wt, phase)
    if not reproducer.preconditions:
        # A signature-only contract is still a frozen judge, and gets the same
        # before/after authority as the precondition path: the outcome is held
        # back until the experiment has been re-validated against values
        # observed *after* repository code ran. Passing the pre-execution digest
        # and `expected_tree=None` here would call the validator while disabling
        # both of the authorities it exists to apply.
        result = flow.execute(check, wt, phase, record=False)
        _validate_reproducer(
            flow,
            wt,
            phase,
            reproducer,
            tree_hash(repo_root) if repo_root is not None else source_digest,
            expected_tree,
            "after",
            state_paths,
        )
        flow.append(EventKind.CHECK_RESULT, flow.check_payload(result, 1, 1))
        return result

    started = time.time()

    def on_start(argv: list[str], i: int, n: int) -> None:
        flow.append(
            EventKind.COMMAND_STARTED,
            {
                "display": f"pytest {reproducer.render()}",
                "phase": phase.value,
                "node_id": reproducer.node_id,
                "run": f"{i}/{n}",
            },
        )
        flow.renderer.tick(f"pytest {reproducer.node_id}", started)

    def on_done(argv: list[str], res) -> None:
        flow.append(
            EventKind.COMMAND_FINISHED,
            {"duration_s": round(res.duration_s, 3), "exit_code": res.exit_code, "phase": phase.value},
        )

    obs = run_probe(
        reproducer.node_id,
        reproducer.preconditions,
        reproducer.repeats,
        wt,
        flow.probe,
        flow.allow_network,
        check.timeout_s,
        on_start=on_start,
        on_done=on_done,
    )
    # Before the outcome is recorded: repository code just ran, and if it
    # edited a frozen judge artifact this measurement is about a different
    # experiment than the one frozen.
    _validate_reproducer(
        flow,
        wt,
        phase,
        reproducer,
        tree_hash(repo_root) if repo_root is not None else source_digest,
        expected_tree,
        "after",
        state_paths,
    )
    result = CheckResult(
        check_id=check.check_id,
        node_id=reproducer.node_id,
        phase=phase,
        outcome=obs.node_outcome,
        signature=obs.signature,
        duration_s=obs.duration_s,
        exit_code=0,
        detail=obs.detail,
    )
    # This path runs the experiment through `run_probe`, not `Flow.execute`, so
    # it sets the observed text itself. Without this the payload would carry
    # whatever the previous command happened to leave behind — a stale excerpt
    # attached to a different measurement.
    flow.last_failure_text = obs.failure_text if obs.node_outcome is not Outcome.PASSED else ""
    flow.append(EventKind.CHECK_RESULT, flow.check_payload(result, 1, 1))
    return result


def run_gate(flow: Flow, req: VerifyRequest, td: Path) -> int:
    """The counterfactual gate, with invalid-reproducer failures governed.

    `ReproducerInvalid` is caught here rather than escaping as a traceback. An
    experiment that stopped being repeatable is an integrity stop: the gate
    ends, a scoped receipt is emitted, and no repair request is made — there is
    nothing for a different patch to correct.
    """
    try:
        return _run_gate(flow, req, td)
    except ReproducerInvalid:
        # The reason is already durable, appended at the point of detection.
        return _finish(flow, td)


def _run_gate(flow: Flow, req: VerifyRequest, td: Path) -> int:
    proj = flow.projection()
    checkset = proj.checkset
    changeset = proj.changeset
    assert checkset is not None and changeset is not None
    change_check = checkset.by_type(ClaimType.CHANGE)[0]
    preservation = checkset.by_type(ClaimType.PRESERVATION)
    # The frozen reproducer, if the kernel issued one. All five phases use it or
    # none do; a phase that quietly ran the bare target would be measuring a
    # different experiment than the one that was frozen.
    reproducer = proj.reproducer
    # Paths the frozen patch owns. Anything else appearing in the worktree was
    # created by executed repository code and is not part of the experiment.
    patch_paths = frozenset(changeset.touched_paths)

    def budget_left() -> bool:
        p = flow.projection()
        if p.commands >= req.budgets.max_commands or p.seconds >= req.budgets.max_seconds:
            flow.append(
                EventKind.BUDGET_EXHAUSTED,
                {"reason": f"commands={p.commands} seconds={p.seconds:.1f}"},
            )
            return False
        return True

    # ---- baseline -----------------------------------------------------
    if GatePhase.BASELINE not in proj.completed_phases:
        if not budget_left():
            return _finish(flow, td)
        with Worktree(req.repo_root, "baseline") as wt:
            # Computed before execution: it is both the expectation this phase
            # validates against and the value withdrawal must return to.
            baseline_state = wt.phase_state_hash(patch_paths)
            result = run_episode(
                flow, wt, change_check, GatePhase.BASELINE, reproducer, req.repo_root, baseline_state, patch_paths
            )
            decision = kernel.decide_baseline(change_check, result)
            if decision.passed and result.signature is not None:
                expected = reproducer.signature if reproducer is not None else None
                if expected is not None and not signature_compatible(expected, result.signature):
                    # The baseline failed, but not the way the caller declared.
                    # A different failure is not this failure: accepting it would
                    # let a patch be credited for repairing something else.
                    flow.append(
                        EventKind.REPRODUCTION_FAILED,
                        {
                            "reason": "baseline signature does not match --expect-signature",
                            "expected": expected.render(),
                            "observed": result.signature.render(),
                        },
                    )
                    raise ReproducerInvalid("baseline reproduced an incompatible signature")
                flow.append(EventKind.SIGNATURE_FROZEN, {"signature": result.signature.to_dict()})
            baseline_tree = wt.hash()
        if not flow.finish_phase(
            GatePhase.BASELINE, decision, {"tree_hash": baseline_tree, "state_hash": baseline_state}
        ):
            return _finish(flow, td)

    proj = flow.projection()
    frozen_sig = proj.baseline_signature
    baseline_tree = proj.artifacts.get("baseline.tree_hash", "")
    # The phase-state hash, reduced from the ledger so a resumed run compares
    # against the same value the original run recorded.
    baseline_state = proj.artifacts.get("baseline.state_hash", "")

    # ---- candidate → withdrawal → reapply → preservation ---------------
    #
    # One worktree carries this whole sequence, because withdrawal must
    # reverse the patch in the tree the candidate actually ran in. Doing it in
    # a fresh worktree would only repeat the baseline measurement; reverting in
    # place also catches a candidate run that left behind the state making it
    # pass. On resume this sequence restarts from the candidate phase: the
    # worktree is gone, and re-deriving it is cheaper than trusting evidence
    # whose tree no longer exists.
    remaining = (GatePhase.CANDIDATE, GatePhase.WITHDRAWAL, GatePhase.REAPPLY, GatePhase.PRESERVATION)
    if all(p in proj.completed_phases for p in remaining):
        return _finish(flow, td)
    if not budget_left():
        return _finish(flow, td)

    try:
        wt = Worktree(req.repo_root, "gate")
    except SandboxError as exc:
        flow.append(EventKind.INFRASTRUCTURE_BLOCKED, {"reason": str(exc)})
        return _finish(flow, td)

    try:
        # candidate
        try:
            wt.apply_patch(changeset.diff)
        except SandboxError as exc:
            # A patch that will not apply is a bad patch, not a broken machine.
            # Recording this as infrastructure would blame the repository for
            # the proposal's defect, and in a benchmark it would quietly drop
            # the attempt out of the denominator instead of counting it as the
            # rejection it is. The sandbox, the runner and the tree are all
            # fine; only the diff is not.
            flow.append(
                EventKind.CHANGESET_REJECTED,
                {"reason": f"the patch does not apply to the baseline tree: {exc}", "phase": "candidate"},
            )
            flow.append(
                EventKind.GATE_PHASE_FINISHED,
                {
                    "phase": GatePhase.CANDIDATE.value,
                    "passed": False,
                    "reason": f"the patch does not apply to the baseline tree: {exc}",
                    "artifacts": {},
                },
            )
            return _finish(flow, td)
        candidate_tree = wt.hash()
        candidate_state = wt.phase_state_hash(patch_paths)
        result = run_episode(
            flow,
            wt,
            change_check,
            GatePhase.CANDIDATE,
            reproducer,
            req.repo_root,
            candidate_state,
            patch_paths,
            patch_paths,
        )
        if not flow.finish_phase(
            GatePhase.CANDIDATE,
            kernel.decide_candidate(result),
            {"tree_hash": candidate_tree, "state_hash": candidate_state},
        ):
            return _finish(flow, td)

        # withdrawal — reverse the exact patch in the same tree
        if not budget_left():
            return _finish(flow, td)
        try:
            wt.apply_patch(changeset.diff, reverse=True)
        except SandboxError as exc:
            # Withdrawal is different: the patch applied, so the tree is now in
            # a state this runtime cannot undo. The counterfactual therefore
            # cannot be run at all, which is a limit on what can be concluded
            # rather than a verdict about the patch.
            flow.append(
                EventKind.GATE_PHASE_FINISHED,
                {
                    "phase": GatePhase.WITHDRAWAL.value,
                    "passed": False,
                    "reason": f"the patch is not cleanly reversible, so the counterfactual cannot be run: {exc}",
                    "artifacts": {},
                },
            )
            return _finish(flow, td)
        withdrawn_tree = wt.hash()
        # Phase-state, not whole-tree. A runtime log, cache or database written
        # by the candidate is still present at this point — `reset_episode`
        # clears it at the start of the withdrawal episode below — and judging
        # the counterfactual on a whole-tree hash would reject the patch for
        # debris. The whole-tree hash stays only as a recorded artifact.
        withdrawn_state = wt.phase_state_hash(patch_paths)
        state_decision = kernel.decide_withdrawal_state(withdrawn_state, baseline_state)
        if not state_decision.passed:
            decision = state_decision
        else:
            result = run_episode(
                flow,
                wt,
                change_check,
                GatePhase.WITHDRAWAL,
                reproducer,
                req.repo_root,
                baseline_state or None,
                patch_paths,
            )
            decision = kernel.decide_withdrawal(result, frozen_sig)
        if not flow.finish_phase(
            GatePhase.WITHDRAWAL, decision, {"tree_hash": withdrawn_tree, "state_hash": withdrawn_state}
        ):
            return _finish(flow, td)

        # Reapply the durable bytes, not the in-memory ones. The diff is read
        # back off disk and re-hashed so the comparison is between two
        # independently derived values; comparing an object against itself
        # would assert nothing.
        try:
            reloaded = changeset_record(td).read_text(encoding="utf-8")
        except OSError as exc:
            flow.append(
                EventKind.INFRASTRUCTURE_BLOCKED,
                {"reason": f"the durable ChangeSet record could not be read: {exc}"},
            )
            return _finish(flow, td)
        reloaded_hash = content_hash(reloaded.encode("utf-8"))
        flow.append(
            EventKind.CHANGESET_RELOADED,
            {"path": changeset_record(td).name, "reloaded_patch_hash": reloaded_hash},
        )
        frozen_hash = changeset.patch_hash
        if frozen_hash != reloaded_hash:
            decision = kernel.decide_reapply(candidate_state, "", frozen_hash, reloaded_hash)
            flow.finish_phase(GatePhase.REAPPLY, decision)
            return _finish(flow, td)
        try:
            wt.apply_patch(reloaded)
        except SandboxError as exc:
            flow.append(EventKind.INFRASTRUCTURE_BLOCKED, {"reason": f"reapplication failed: {exc}"})
            return _finish(flow, td)
        reapplied_tree = wt.hash()
        reapplied_state = wt.phase_state_hash(patch_paths)
        # Exactly one completion event for this phase. Emitting a passed event
        # for the tree check and a second one after the behavioural rerun would
        # put a phase in the ledger that was recorded as passing and then as
        # failing — two answers to one question, and a replay that has to guess
        # which is authoritative.
        decision = kernel.decide_reapply(candidate_state, reapplied_state, frozen_hash, reloaded_hash)
        if decision.passed:
            # The tree is byte-identical to the candidate, but the measurement
            # is re-run in its own clean episode. An identical tree that no
            # longer passes means the candidate pass depended on runtime state
            # rather than on the patch — the false fix the gate exists to catch.
            result = run_episode(
                flow,
                wt,
                change_check,
                GatePhase.REAPPLY,
                reproducer,
                req.repo_root,
                candidate_state or None,
                patch_paths,
                patch_paths,
            )
            behaviour = kernel.decide_candidate(result)
            if not behaviour.passed:
                decision = kernel.PhaseDecision(
                    passed=False,
                    reason=f"the reapplied patch no longer passes: {behaviour.reason}",
                    infrastructure=behaviour.infrastructure,
                )
        if not flow.finish_phase(
            GatePhase.REAPPLY, decision, {"tree_hash": reapplied_tree, "state_hash": reapplied_state}
        ):
            return _finish(flow, td)

        # preservation, on the reapplied tree only
        results: list[CheckResult] = []
        for i, check in enumerate(preservation, start=1):
            if not budget_left():
                return _finish(flow, td)
            # Preservation runs repository code like any other phase, so it
            # gets the same clean episode and the same integrity questions —
            # but with no ordering preconditions, because preservation asks
            # whether unrelated behaviour survived, not whether the diagnosed
            # failure reproduces.
            #
            # The reset matters most between preservation checks: state left by
            # reapplication, or by the previous check, could otherwise make the
            # next one pass for a reason that has nothing to do with the patch.
            if reproducer is not None:
                _validate_reproducer(
                    flow,
                    wt,
                    GatePhase.PRESERVATION,
                    reproducer,
                    tree_hash(req.repo_root),
                    candidate_state,
                    "before",
                    patch_paths,
                )
            try:
                cleared, restored = reset_episode(wt, patch_paths)
            except SandboxError as exc:
                flow.append(
                    EventKind.INFRASTRUCTURE_BLOCKED,
                    {"reason": str(exc), "phase": GatePhase.PRESERVATION.value},
                )
                return _finish(flow, td)
            flow.append(
                EventKind.EPISODE_RESET,
                {
                    "phase": GatePhase.PRESERVATION.value,
                    "cleared": cleared,
                    "restored": restored,
                    "patch_owned": sorted(patch_paths),
                    "scope": "runtime-created state; baseline and patch-owned files preserved",
                },
            )
            if reproducer is not None:
                _validate_reproducer(
                    flow,
                    wt,
                    GatePhase.PRESERVATION,
                    reproducer,
                    tree_hash(req.repo_root),
                    candidate_state,
                    "before-execution",
                    patch_paths,
                )
            results.append(flow.execute(check, wt, GatePhase.PRESERVATION, index=i, total=len(preservation)))
            if reproducer is not None:
                _validate_reproducer(
                    flow,
                    wt,
                    GatePhase.PRESERVATION,
                    reproducer,
                    tree_hash(req.repo_root),
                    candidate_state,
                    "after",
                    patch_paths,
                )
        flow.finish_phase(GatePhase.PRESERVATION, kernel.decide_preservation(tuple(results)))
    finally:
        wt.dispose()

    return _finish(flow, td)


def _finish(flow: Flow, td: Path) -> int:
    proj = flow.projection()
    _, code = emit_receipt(flow, proj, td)
    return code


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------


def parse_signature(pattern: str) -> Signature:
    """`ExceptionType: message` or a bare `ExceptionType`.

    Parsed rather than pattern-matched: a signature is an identity, and matching
    it loosely is how a failure's existence gets mistaken for evidence about its
    cause (records.Signature).
    """
    head, sep, tail = pattern.partition(":")
    return Signature(head.strip(), tail.strip() if sep else "")


def signature_compatible(expected: Signature, observed: Signature | None) -> bool:
    """Is the observed baseline failure the one that was declared?

    An expected signature naming only an exception type matches any message of
    that type; naming a message requires both. Exact equality in both fields
    would make `--expect-signature AssertionError` unusable, and dropping the
    type check would let any failure count as this failure — which is the
    error `Signature` exists to prevent.
    """
    if not expected.exception_type:
        return True  # nothing was declared; the baseline freezes what it sees
    if observed is None:
        return False
    if expected.exception_type != observed.exception_type:
        return False
    return not expected.message or expected.message in observed.message


def verify_reproducer(
    node_id: str, preconditions: tuple[str, ...], expected: str, repo_root: Path, collected: list[str]
) -> tuple[ReproductionContract, tuple[str, ...]] | None:
    """The frozen experiment for `verify`, built from the command line.

    `fix` derives its reproducer from executed evidence; here the caller states
    it. Both produce the same contract type and both run through the same gate —
    there is no benchmark-specific or verify-specific verification path.

    Returns the contract and the files it makes protected, or None when a
    selector cannot be resolved — a reproducer whose executable artifact set
    cannot be pinned down is not a frozen judge, and issuing one anyway would
    look rigorous while protecting nothing. Every file the
    experiment executes becomes a judge artifact: a candidate that edits the
    polluter would weaken the experiment while leaving the contract record
    byte-identical, which is the tamper the protected set exists to catch.
    """
    handles = tuple(Handle(kind=Primitive.FIRST, arg=node) for node in preconditions)
    # Resolved the way `fix` resolves them. Splitting on "::" would freeze a
    # directory selector as a literal string, whose hash is `<absent>` — recorded
    # as protected evidence while every test file inside it stayed editable.
    resolved = judge_artifact_paths(node_id, handles, collected, repo_root)
    if resolved is None:
        return None
    artifacts = tuple(resolved)
    return (
        ReproductionContract(
            preconditions=handles,
            node_id=node_id,
            # An empty signature here means "not yet observed"; the baseline
            # freezes what it sees. It never means "anything will do".
            signature=parse_signature(expected) if expected else Signature("", ""),
            runner_config_hash=runner_config_hash(repo_root),
            tree_digest=tree_hash(repo_root),
            supporting_event_ids=(),
            judge_artifacts=tuple(sorted(hash_artifacts(repo_root, list(artifacts)).items())),
        ),
        artifacts,
    )


def freeze_declared_reproducer(
    flow: Flow, args, repo_root: Path, checkset: CheckSet, probe: IsolationProbe, budgets: Budgets
) -> CheckSet | None:
    """Freeze the experiment the caller declared, for `verify` and for arm A.

    One function rather than two call sites: `verify` and `fix --model-alone`
    must freeze the *same* contract from the same arguments, and two copies of
    this would eventually disagree about what a precondition means.

    Declared preconditions make the experiment ordered in every phase. There is
    deliberately no bare-target fallback from here — falling back would measure
    a different experiment than the one the caller froze. Returns the checkset
    with the reproducer's judge artifacts protected, or None when a selector
    cannot be resolved, in which case the caller stops.
    """
    preconditions = tuple(getattr(args, "precondition", None) or ())
    expected = getattr(args, "expect_signature", "")
    if not preconditions and not expected:
        return checkset
    # Collection is needed only when a selector is not already a file on disk:
    # a directory selector expands to the test files beneath it, and those are
    # only knowable from what pytest can see.
    collected: list[str] = []
    if any(not (repo_root / sel.split("::")[0].rstrip("/")).is_file() for sel in preconditions):
        with Worktree(repo_root, "collect") as wt:
            collected, _ = collect_nodes(wt, probe, budgets.command_timeout_s, args.allow_network)
    built = verify_reproducer(args.test, preconditions, expected, repo_root, collected)
    if built is None:
        flow.append(
            EventKind.REPRODUCTION_FAILED,
            {
                "reason": "a declared precondition selector names no executable test file",
                "selectors": list(preconditions),
            },
        )
        return None
    reproducer, artifacts = built
    flow.append(
        EventKind.REPRODUCER_FROZEN,
        {
            "reproducer": reproducer.to_dict(),
            "reproducer_hash": reproducer.content_hash,
            "render": reproducer.render(),
            "protected_added": list(artifacts),
            "expected_signature": expected,
        },
    )
    return replace(checkset, protected_paths=tuple(sorted({*checkset.protected_paths, *artifacts})))


def cmd_verify(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"error: repository not found: {repo_root}", file=sys.stderr)
        return EXIT_USAGE
    diff_path = Path(args.diff).resolve()
    if not diff_path.is_file():
        print(f"error: diff file not found: {diff_path}", file=sys.stderr)
        return EXIT_USAGE
    # Canonicalised at ingestion so an external diff missing its final newline
    # is not mistaken for a malformed hunk.
    diff = canonical_diff(diff_path.read_text(encoding="utf-8"))

    budgets = Budgets(
        max_commands=args.max_commands,
        max_seconds=args.max_seconds,
        command_timeout_s=args.timeout,
    )
    req = VerifyRequest(
        repo_root=repo_root,
        diff_path=diff_path,
        node_id=args.test,
        preserve=tuple(args.preserve or ()),
        budgets=budgets,
        allow_partial=args.allow_partial_sandbox,
        require_full=args.require_full_sandbox,
        allow_network=args.allow_network,
        task_id="",
    )

    probe = probe_isolation()
    # The directory is claimed atomically before anything durable is written,
    # so two identical `rift verify` invocations — including simultaneous ones
    # — get separate ledgers rather than appending into a shared one.
    task_id, td = allocate_task_dir(
        repo_root,
        Verb.VERIFY.value,
        task_fingerprint({"n": args.test, "p": content_hash(diff.encode("utf-8"))}),
    )
    ledger = Ledger(td / "ledger.jsonl", task_id)
    renderer = LiveRenderer(quiet=args.json)
    flow = Flow(ledger, renderer, probe, args.allow_network)

    flow.append(
        EventKind.TASK_STARTED,
        {"task_id": task_id, "verb": Verb.VERIFY.value, "repo": str(repo_root), "target": args.test},
    )
    flow.append(EventKind.SANDBOX_PROBED, {"level": probe.level.value, "detail": probe.detail})

    # Isolation authority. `--yes` is accepted for interface stability across
    # verbs and is deliberately never consulted here.
    if probe.level is IsolationLevel.PARTIAL:
        if req.require_full:
            flow.append(
                EventKind.INFRASTRUCTURE_BLOCKED,
                {"reason": "--require-full-sandbox was given but full isolation is unavailable"},
            )
            return _emit_blocked(flow, td, args)
        if not req.allow_partial:
            flow.append(
                EventKind.INFRASTRUCTURE_BLOCKED,
                {
                    "reason": "full isolation is unavailable and --allow-partial-sandbox was not given; "
                    "repository code was not executed"
                },
            )
            return _emit_blocked(flow, td, args)
    if not probe.tree_kill:
        flow.append(
            EventKind.INFRASTRUCTURE_BLOCKED,
            {"reason": "this platform cannot terminate a process tree reliably; repository code was not executed"},
        )
        return _emit_blocked(flow, td, args)

    flow.append(
        EventKind.SANDBOX_AUTHORIZED,
        {"partial_authorized": probe.level is IsolationLevel.PARTIAL and req.allow_partial},
    )

    checkset = build_checkset(args.test, req.preserve, repo_root, budgets.command_timeout_s)
    frozen = freeze_declared_reproducer(flow, args, repo_root, checkset, probe, budgets)
    if frozen is None:
        return _emit_and_report(flow, td, args)
    checkset = frozen
    flow.append(
        EventKind.CHECKSET_FROZEN,
        {"checkset": checkset.to_dict(), "checkset_hash": checkset.content_hash},
    )

    validation = kernel.validate_patch(diff, checkset.protected_paths)
    if validation.rejected:
        flow.append(EventKind.CHANGESET_REJECTED, {"reason": validation.reason})
        flow.append(
            EventKind.GATE_PHASE_FINISHED,
            {
                "phase": GatePhase.CANDIDATE.value,
                "passed": False,
                "reason": f"patch rejected: {validation.reason}",
                "artifacts": {},
            },
        )
        return _emit_and_report(flow, td, args)
    changeset = ChangeSet(diff=diff, touched_paths=validation.touched, origin="external")
    # Write the content-addressed record before recording acceptance, so the
    # bytes reapplied later are read from durable storage rather than from a
    # process that may not survive.
    changeset_record(td).write_text(changeset.diff, encoding="utf-8", newline="\n")
    flow.append(EventKind.CHANGESET_REGISTERED, {"changeset": changeset.to_dict()})

    contract = TaskContract(
        task_id=task_id,
        verb=Verb.VERIFY,
        request=f"verify {diff_path.name} against {args.test}",
        repo_root=str(repo_root),
        baseline_tree_hash=tree_hash(repo_root),
        scope="one change check plus the preservation checks declared on the command line",
        budgets=budgets,
        requested_sandbox=IsolationLevel.FULL if req.require_full else probe.level,
        actual_sandbox=probe.level,
        authorities=Authorities(
            spec_approval="not_applicable",
            partial_sandbox="--allow-partial-sandbox"
            if (probe.level is IsolationLevel.PARTIAL and req.allow_partial)
            else "none",
        ),
        allow_network=args.allow_network,
    )
    flow.append(EventKind.CONTRACT_FROZEN, {"contract": contract.to_dict(), "contract_hash": contract.content_hash})

    code = run_gate(flow, req, td)
    if args.json:
        print(canonical(flow.projection().receipt or {}))
    return code


# --------------------------------------------------------------------------
# `rift why` — diagnosis
#
# `verify` asks whether a supplied patch survives a counterfactual gate. `why`
# asks a different question with the same discipline: which lever, if any,
# changes this failure. It runs experiments rather than checks, and the answer
# it is allowed to give is bounded by what the experiments distinguished. When
# two theories remain and no available probe separates them, the result is
# `underdetermined` — a cause is never guessed.
#
# The whole loop is model-optional. Handles are discovered from observable
# signals by the kernel; a configured model may only *propose additional
# handles*, which are validated and then treated exactly like discovered ones.
# With no model, `why` runs to completion and says so in the receipt.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WhyRequest:
    repo_root: Path
    node_id: str
    budgets: Budgets
    allow_partial: bool
    require_full: bool
    allow_network: bool
    max_probes: int
    use_model: bool
    # BM-06 arm B (DAR-015). The default is the shipped policy, so an ordinary
    # run is unchanged; `random` draws from the identical candidate pool and
    # differs only in which probe is taken next.
    probe_policy: str = "disagreement"
    probe_seed: int | None = None


def _eliminated_ids(before: list[kernel.Scored], after: list[kernel.Scored]) -> list[str]:
    was_live = {s.hypothesis["hypothesis_id"] for s in before if s.status != "contradicted"}
    now_dead = {s.hypothesis["hypothesis_id"] for s in after if s.status == "contradicted"}
    return sorted(was_live & now_dead)


def _complement(ordering: list[Handle], cause: Handle, collected: list[str], target: str) -> list[str]:
    """Every test file the broadest ordering handle covers, except the cause.

    Probing this answers the question bisection cannot: is the located cause
    the only sufficient one, or merely the first one found?
    """
    universe: set[str] = set()
    for h in ordering:
        if h.kind is Primitive.FIRSTSET:
            universe |= {x for x in h.arg.split(",") if x}
        elif h.arg.endswith("/"):
            universe |= {n.split("::")[0] for n in collected if n.startswith(h.arg)}
        else:
            universe.add(h.arg)
    inside = {x for x in cause.arg.split(",")} if cause.kind is Primitive.FIRSTSET else {cause.arg}
    return sorted(universe - inside - {target.split("::")[0]})


def _breadth(h: Handle, collected: list[str]) -> int:
    """How many test files a handle drags in. Smaller is a narrower claim."""
    if h.kind is Primitive.FIRSTSET:
        return len([x for x in h.arg.split(",") if x])
    if h.kind is Primitive.FIRST:
        if h.arg.endswith("/"):
            inside = {n.split("::")[0] for n in collected if n.startswith(h.arg)}
            return len(inside) or 1 << 20
        return 1
    return 1


def _as_narrow_handle(h: Handle) -> Handle:
    """A one-member set is a single file; say so."""
    if h.kind is Primitive.FIRSTSET:
        members = [x for x in h.arg.split(",") if x]
        if len(members) == 1:
            return Handle(Primitive.FIRST, members[0])
    return h


def _selector_key(h: Handle) -> frozenset[str]:
    """What a handle actually runs.

    `first:X` and `firstset:X` compile to the identical argv, so they are the
    same experiment and must compare equal. Comparing spellings instead would
    let a cosmetic rename defeat the receipt invariant.
    """
    return frozenset(x for x in h.arg.split(",") if x)


def _confirmed_handles(ev: kernel.Evidence, mapping: dict[str, Handle]) -> list[Handle]:
    """Handles that an executed single-handle probe actually showed reproducing.

    This is the whole discipline of the refinement stage in one function: a
    cause may be claimed only if a probe that applied *it alone* was executed
    and the target failed. A handle that merely appears in a surviving theory
    has not been distinguished from its neighbours.
    """
    out: list[Handle] = []
    for obs in ev.observed:
        applied = obs.get("applied") or []
        if len(applied) == 1 and obs.get("outcome") != PASS:
            handle = mapping.get(applied[0])
            if handle is not None and handle not in out:
                out.append(handle)
    return out


def refine_ordering_cause(
    flow: Flow,
    req: WhyRequest,
    diagnosis: Diagnosis,
    collected: list[str],
    worktree: Worktree,
    ev: kernel.Evidence,
    mapping: dict[str, Handle],
) -> tuple[Diagnosis, Worktree, list[str]]:
    """Bisect a coarse ordering cause to the narrowest subset the probes prove.

    `first:tests/` is a true statement and a nearly useless one: it names a
    directory when the failure is caused by one file in it. Delta debugging
    resolves that deterministically — halve the selection, keep the half that
    still reproduces — and every halving is a real executed probe, charged and
    recorded.

    The stopping rules are where the honesty lives:

    * exactly one half reproduces → recurse into it;
    * **both** halves reproduce independently → there is more than one
      sufficient cause, and no single narrower one was distinguished, so the
      coarse handle stands with that stated;
    * **neither** half reproduces → the cause is the combination, not either
      part, so the coarse handle stands with that stated;
    * budget or step limit reached → the narrowest cause *confirmed so far*
      stands, and the receipt says refinement was truncated.

    In every branch the claim is bounded by executed probes. Nothing is
    narrowed by inference.
    """
    ordering = [c for c in diagnosis.causes if c.kind in (Primitive.FIRST, Primitive.FIRSTSET)]
    if not ordering:
        return diagnosis, worktree, []

    # Start from the narrowest handle a probe already confirmed. The main loop
    # commonly runs both `first:tests/` and `first:tests/some_file.py`, and when
    # it has, the file-level handle is already distinguished and the directory
    # one must not be reported.
    notes: list[str] = []
    held = [worktree]
    # Selector sets of handles that a single-handle probe showed reproducing.
    # Seeded from the main loop's evidence and extended by every measurement
    # below, so the receipt invariant is checked against executed probes.
    reproduced_alone: set[frozenset[str]] = {_selector_key(h) for h in _confirmed_handles(ev, mapping)}

    def measure(handle: Handle, label: str) -> ProbeObservation:
        """One handle, one fresh episode, fully recorded.

        A residue from the previous measurement would make this one a lie, so
        the sandbox is rebuilt rather than reused.
        """
        held[0].dispose()
        held[0] = Worktree(req.repo_root, "why")
        ev.new_episode()
        obs = run_probe(
            req.node_id,
            (handle,),
            1,
            held[0],
            flow.probe,
            req.allow_network,
            req.budgets.command_timeout_s,
            on_start=lambda argv, i, n, _h=handle: flow.append(
                EventKind.COMMAND_STARTED,
                {
                    "display": f"pytest {req.node_id} [{label} {_h.arg}]",
                    "phase": "refinement",
                    "node_id": req.node_id,
                    "run": f"{i}/{n}",
                },
            ),
            on_done=lambda argv, res: flow.append(
                EventKind.COMMAND_FINISHED,
                {"duration_s": round(res.duration_s, 3), "exit_code": res.exit_code, "phase": "refinement"},
            ),
        )
        # Every intermediate observation is durable, in the same shape as every
        # other observation, so a resumed run inherits the work already paid for
        # and a reader can audit how the narrowing was reached.
        flow.append(EventKind.CHECK_RESULT, {"result": _probe_as_result(req.node_id, obs), "index": 1, "total": 1})
        flow.append(
            EventKind.PROBE_SELECTED,
            {
                "probe": f"{label}[{handle.label}]x1",
                "applied": [handle.label],
                "repeats": 1,
                "fresh_episode": True,
                "observation": obs.to_dict(),
            },
        )
        if obs.is_evidence and obs.outcome != PASS:
            reproduced_alone.add(_selector_key(handle))
        return obs

    def budget_for(n: int) -> bool:
        if flow.projection().commands + n <= req.budgets.max_commands:
            return True
        flow.append(
            EventKind.BUDGET_EXHAUSTED,
            {"limit": "max_commands", "value": req.budgets.max_commands, "during": "cause refinement"},
        )
        return False

    proven = [c for c in ordering if _selector_key(c) in reproduced_alone]
    if not proven:
        # The probe economics may never have run the ordering handle *alone* —
        # it is often applied alongside another handle. Nothing may be narrowed
        # from a combined probe, so buy the one measurement that makes
        # narrowing legitimate rather than inferring it.
        candidate = min(ordering, key=lambda h: (_breadth(h, collected), h.arg))
        if not budget_for(1):
            return (
                diagnosis,
                held[0],
                ["no single-handle probe confirmed an ordering cause and the budget did not allow one"],
            )
        obs = measure(candidate, "confirm")
        if not obs.is_evidence or obs.outcome == PASS:
            return (
                diagnosis,
                held[0],
                [
                    f"{candidate.label} did not reproduce the failure when applied alone, so no "
                    "narrower ordering cause is claimed"
                ],
            )
        proven = [candidate]

    cause = min(proven, key=lambda h: (_breadth(h, collected), h.arg))

    steps = 0
    while steps < MAX_REFINEMENT_STEPS:
        halves = kernel.refine_first(cause, collected, req.node_id)
        if not halves:
            break  # already atomic: a single file cannot be halved
        if not budget_for(len(halves)):
            notes.append(
                f"refinement stopped at {cause.label} because the command budget was exhausted; "
                "a narrower cause may exist and was not tested"
            )
            break

        outcomes: list[tuple[Handle, ProbeObservation]] = []
        for half in halves:
            outcomes.append((half, measure(half, "bisect")))

        reproducing = [h for h, o in outcomes if o.is_evidence and o.outcome != PASS]
        unobserved = [h for h, o in outcomes if not o.is_evidence]
        steps += 1

        if unobserved:
            notes.append(
                f"refinement stopped at {cause.label}: {len(unobserved)} of {len(halves)} halves "
                "could not be observed, and an unobserved half eliminates nothing"
            )
            break
        if len(reproducing) == 1:
            narrowed = reproducing[0]
            flow.append(
                EventKind.CAUSE_REFINED,
                {
                    "from": cause.to_dict(),
                    "to": narrowed.to_dict(),
                    "tested": [h.label for h, _ in outcomes],
                    "reproduced": [h.label for h in reproducing],
                    "step": steps,
                },
            )
            cause = narrowed
            continue
        if not reproducing:
            notes.append(
                f"neither half of {cause.label} reproduces the failure alone, so the cause is the "
                "combination rather than any single member; the narrower claim is not supported"
            )
            break
        notes.append(
            f"both halves of {cause.label} reproduce the failure independently, so there is more "
            "than one sufficient cause and no single narrower one was distinguished"
        )
        break
    else:
        notes.append(
            f"refinement stopped at {cause.label} after {MAX_REFINEMENT_STEPS} bisection steps; "
            "a narrower cause may exist and was not tested"
        )

    # Narrowing found *a* sufficient cause. It has not shown there is only one.
    # One probe applies everything else in scope, as a single sequence.
    #
    # The asymmetry of what that probe can tell us is the whole point. A
    # complement that reproduces is positive evidence: another sufficient cause
    # exists, and the located one is not the only one. A complement that passes
    # proves far less than it appears to — the members were run together, so a
    # cause inside it may have been masked by another member, or may require an
    # interaction the sequence did not produce. Reading a passing complement as
    # uniqueness would be exactly the confident overclaim this runtime exists to
    # refuse, and establishing real uniqueness would cost one probe per member,
    # which bisection deliberately does not spend.
    others = _complement(ordering, cause, collected, req.node_id)
    if others and budget_for(1):
        rest = Handle(Primitive.FIRSTSET, ",".join(others))
        obs = measure(rest, "complement")
        if not obs.is_evidence:
            notes.append(
                f"the complement of {cause.label} could not be observed "
                f"({obs.node_outcome.value}), so nothing is concluded from it"
            )
        elif obs.outcome != PASS:
            notes.append(
                f"the failure also reproduces from the remaining {len(others)} file(s) without "
                f"{cause.arg}, so {cause.label} is one sufficient cause and not the only one"
            )
        else:
            notes.append(
                f"the tested complement sequence of {len(others)} file(s) did not reproduce the "
                f"failure; interaction or masking within that sequence remains possible, so this "
                f"is not a uniqueness claim for {cause.label}"
            )

    cause = _as_narrow_handle(cause)

    # Receipt invariant, enforced here and not only asserted in tests: a
    # refined cause must appear as the sole applied handle of a recorded probe
    # that reproduced the failure. If the narrowing ever produced a handle that
    # no such probe covers, the claim is retracted to the coarse handle rather
    # than shipped — a bug in this function must not become a false finding in
    # a receipt.
    if _selector_key(cause) not in reproduced_alone:
        notes.append(
            f"the narrowed handle {cause.label} is not covered by a single-handle probe that "
            "reproduced the failure, so the claim is held at the coarser handle it was refined from"
        )
        fallback = [c for c in ordering if _selector_key(c) in reproduced_alone]
        cause = min(fallback or ordering, key=lambda h: (_breadth(h, collected), h.arg))

    kept = [c for c in diagnosis.causes if c.kind not in (Primitive.FIRST, Primitive.FIRSTSET)]
    causes = (*kept, cause)
    # The remediation note names the causes, so it must be rebuilt: leaving the
    # pre-refinement text in place would have the receipt retract a handle in
    # one line and still name it in the next.
    remediation = diagnosis.remediation_unverified
    if remediation and causes != diagnosis.causes:
        remediation = (
            "UNVERIFIED: no remediation for "
            + ", ".join(c.label for c in causes)
            + " was produced or tested by this runtime. Use `rift verify` on a candidate patch."
        )
    refined = replace(
        diagnosis,
        causes=causes,
        notes=(*diagnosis.notes, *notes),
        remediation_unverified=remediation,
    )
    return refined, held[0], notes


def _replay_observations(ev: kernel.Evidence, proj: TaskProjection, mapping: dict[str, Handle]) -> int:
    """Rebuild an evidence trace from the probes a previous run already paid for.

    Only observations whose handles still map to a role in *this* run are
    replayed. Handle discovery is deterministic given the same tree, so they
    normally all do; one that does not is silently dropped rather than
    force-fitted, because an observation about a handle this run cannot express
    is not evidence this run may reason from.

    A probe that was recorded but not observable (a timeout, a collection
    error) is skipped for the same reason it was skipped when it happened: a
    measurement failure eliminates nothing.
    """
    by_label = {h.label: role for role, h in mapping.items()}
    replayed = 0
    for entry in proj.probes:
        obs = entry.get("observation") or {}
        if obs.get("node_outcome") not in ("passed", "failed"):
            continue
        roles = [by_label[label] for label in entry.get("applied", []) if label in by_label]
        if len(roles) != len(entry.get("applied", [])):
            continue
        if entry.get("fresh_episode"):
            ev.new_episode()
        ev.record(tuple(roles), int(entry.get("repeats", 1)), str(obs["outcome"]))
        replayed += 1
    return replayed


def run_diagnosis(
    flow: Flow,
    req: WhyRequest,
    td: Path,
    resume_from: TaskProjection | None = None,
    spend: SpendLedger | None = None,
) -> Diagnosis:
    """The diagnosis loop. Deterministic given the same observations.

    Every decision here — which theories survive, which probe is worth its
    cost, whether the evidence supports a cause at all — is made by
    `kernel.py`. This function executes experiments and records them; it never
    concludes anything itself.
    """
    notes: list[str] = []
    # Seeded from the request under the random policy, so a rerun of arm B is
    # the same experiment; 0 under the default policy, where the rng is only a
    # tiebreak and the selection is deterministic anyway.
    rng = random.Random(req.probe_seed if req.probe_policy == "random" else 0)
    worktree = Worktree(req.repo_root, "why")
    try:
        collected, collect_note = collect_nodes(worktree, flow.probe, req.budgets.command_timeout_s, req.allow_network)
        if collect_note:
            notes.append(f"collection: {collect_note}")
        flow.append(
            EventKind.CONTEXT_SELECTED,
            {
                "collected_nodes": len(collected),
                "collected": collected[:2000],
                "target": req.node_id,
                "note": collect_note,
                "selection": "observable pytest collection, bounded; no embedding or retrieval is used",
            },
        )

        # 1. Establish that there is something to diagnose. A target that
        #    passes cleanly has no failure to explain, and saying otherwise
        #    would be inventing one.
        baseline = run_probe(
            req.node_id,
            (),
            1,
            worktree,
            flow.probe,
            req.allow_network,
            req.budgets.command_timeout_s,
            on_start=lambda argv, i, n: flow.append(
                EventKind.COMMAND_STARTED,
                {"display": f"pytest {req.node_id}", "phase": "diagnosis", "node_id": req.node_id, "run": f"{i}/{n}"},
            ),
            on_done=lambda argv, res: flow.append(
                EventKind.COMMAND_FINISHED,
                {"duration_s": round(res.duration_s, 3), "exit_code": res.exit_code, "phase": "diagnosis"},
            ),
        )
        flow.append(
            EventKind.CHECK_RESULT,
            {
                "result": _probe_as_result(req.node_id, baseline),
                "index": 1,
                "total": 1,
                # A signature identifies a failure; it does not show where the
                # failure happened. Context selection needs the frames, so a
                # bounded excerpt of the observed output is made durable here —
                # what the model is later shown is then exactly what was
                # recorded, not a value carried in a variable.
                "failure_excerpt": baseline.failure_text[-FAILURE_EXCERPT_CHARS:],
            },
        )

        if not baseline.is_evidence:
            # A target that will not even collect is normally an unobservable
            # measurement. But when the interpreter names what it could not
            # import, that is an explanation rather than a broken machine, and
            # the assertion is measured before the run gives up.
            unobservable = kernel.discover_handles(baseline.failure_text, [], [], req.node_id)
            found = kernel.observational_diagnosis(
                _absent_assertions(flow, unobservable, worktree, req.budgets.command_timeout_s), notes
            )
            if found is not None:
                return found
            return Diagnosis(
                Verdict.INFRASTRUCTURE_BLOCKED,
                None,
                GateStatus.NOT_APPLICABLE,
                (),
                0,
                (),
                (
                    f"the target could not be observed: {baseline.node_outcome.value}"
                    f"{' — ' + baseline.detail if baseline.detail else ''}",
                    "no diagnosis is attempted from an unobserved target",
                ),
            )
        # A target that passes *alone* is not a target with nothing wrong with
        # it. Order-dependent failures pass in isolation by definition, so the
        # isolated run is recorded as evidence and the experiments continue.
        # Only if nothing ever fails is there genuinely nothing to explain.
        saw_failure = baseline.outcome != PASS

        # 2. Enumerate the action space from observable signals only.
        handles = kernel.discover_handles(
            baseline.failure_text,
            observable_paths(worktree.path),
            collected,
            req.node_id,
            ambient_env=dict(os.environ),
        )
        # `propose_handles` fires on the representation-inadequate signal, and
        # the design names two of them: no handle exists at all, or every theory
        # gets contradicted. This is the first; the second is in the loop below.
        # One bounded request serves both, so a task asks at most once.
        asked_handles = False
        if not handles and req.use_model and spend is not None:
            asked_handles = True
            handles = _extend_handles_with_model(
                flow, handles, baseline.failure_text, req.node_id, spend, flow.ledger.task_id
            )
        flow.append(
            EventKind.HANDLES_DISCOVERED,
            {"handles": [h.to_dict() for h in handles], "count": len(handles)},
        )
        if not handles:
            if not saw_failure:
                return Diagnosis(
                    Verdict.UNVERIFIABLE,
                    None,
                    GateStatus.NOT_APPLICABLE,
                    (),
                    0,
                    (),
                    (
                        *notes,
                        "the target passes in a clean disposable sandbox and no handle exists to "
                        "vary, so there is no failure here to explain",
                    ),
                )
            return Diagnosis(
                Verdict.REPRESENTATION_INADEQUATE,
                None,
                GateStatus.NOT_APPLICABLE,
                (),
                0,
                (),
                (
                    *notes,
                    "no handle could be discovered from the failure text, the tree or the collected "
                    "tests, so no theory is expressible; this attributes nothing to the repository",
                ),
            )

        mapping, roles = kernel.role_map(handles)
        hypotheses = kernel.code_grammar(roles)
        probes = kernel.generate_probes(roles)
        flow.append(
            EventKind.HYPOTHESES_PROPOSED,
            {"count": len(hypotheses), "roles": roles, "probe_candidates": len(probes)},
        )

        # 3. The observed baseline is itself evidence: a fresh episode with
        #    nothing applied that ended in failure.
        ev = kernel.Evidence()
        ev.record((), 1, baseline.outcome, interventional=False)
        replayed = _replay_observations(ev, resume_from, mapping) if resume_from else 0
        if replayed:
            notes.append(f"resumed: {replayed} probe observation(s) were inherited from the ledger")
            saw_failure = saw_failure or any(o.get("outcome") != PASS for o in ev.observed)
        scored = [kernel.score(h, ev) for h in hypotheses]

        # 4. Probe until one behavioural class remains, the budget is gone, or
        #    no probe can separate what is left.
        asked_model = False
        for _ in range(max(0, req.max_probes)):
            live = [s for s in scored if s.status != "contradicted"]
            if not live and not asked_handles and req.use_model and spend is not None:
                # Every theory contradicted, including "nothing here helps": the
                # action space cannot express what is happening. One bounded
                # `propose_handles` request may widen it; the loop is then
                # re-entered and the widened space is *experimented on*. If it
                # is still contradicted the verdict is
                # `representation_inadequate`, which attributes nothing to the
                # repository.
                asked_handles = True
                widened = _extend_handles_with_model(
                    flow, handles, baseline.failure_text, req.node_id, spend, flow.ledger.task_id
                )
                if len(widened) > len(handles):
                    # Roles are positional, so appending keeps r0..rN bound to
                    # the same handles and every observation already recorded
                    # stays valid against the rebuilt theory space.
                    handles = widened
                    flow.append(
                        EventKind.HANDLES_DISCOVERED,
                        {
                            "handles": [h.to_dict() for h in handles],
                            "count": len(handles),
                            "origin": "model, requested after every enumerated theory was contradicted",
                        },
                    )
                    mapping, roles = kernel.role_map(handles)
                    hypotheses = kernel.code_grammar(roles)
                    probes = kernel.generate_probes(roles)
                    scored = [kernel.score(h, ev) for h in hypotheses]
                    notes.append(
                        "every theory was contradicted, so the action space was widened once and "
                        "the surviving theories were re-tested against the same evidence"
                    )
                    continue
            if len(kernel.future_classes(live, probes, ev)) <= 1:
                # The governed ambiguity point. Deterministic discovery has
                # already run in full — the enumerated grammar, every probe the
                # budget allowed — and no remaining experiment can separate what
                # survives. One bounded `propose_hypotheses` request may widen
                # the theory space here, and nowhere else.
                #
                # It is not requested when the evidence already supports a
                # cause. Widening the space at that point could only split one
                # behavioural class into two and turn a supported diagnosis into
                # `underdetermined` — paying for a request in order to know less.
                settled_now = kernel.derive_diagnosis(scored, probes, ev, mapping, [])
                if asked_model or not req.use_model or spend is None:
                    break
                if settled_now.status is Verdict.DIAGNOSIS_SUPPORTED:
                    notes.append("the evidence already supported a cause, so no model theories were requested")
                    break
                asked_model = True
                extra = _extend_hypotheses_with_model(
                    flow,
                    hypotheses,
                    roles,
                    handles,
                    ev,
                    baseline.failure_text,
                    req.node_id,
                    spend,
                    flow.ledger.task_id,
                )
                if not extra:
                    break
                hypotheses = hypotheses + extra
                flow.append(
                    EventKind.HYPOTHESES_PROPOSED,
                    {
                        "count": len(hypotheses),
                        "roles": roles,
                        "probe_candidates": len(probes),
                        "model_proposed": [h["hypothesis_id"] for h in extra],
                        "origin": ("model, requested where no remaining probe could separate the enumerated theories"),
                    },
                )
                notes.append(
                    f"{len(extra)} model-proposed theor{'y' if len(extra) == 1 else 'ies'} were added "
                    "where the enumerated space stalled, and scored against the same evidence"
                )
                # Scored against the evidence already recorded, by the same
                # function and with no allowance for origin. If they survive
                # that, the loop continues and *experiments* on them.
                scored = [kernel.score(h, ev) for h in hypotheses]
                continue
            if flow.projection().commands >= req.budgets.max_commands:
                flow.append(EventKind.BUDGET_EXHAUSTED, {"limit": "max_commands", "value": req.budgets.max_commands})
                notes.append("the probe budget was exhausted before the theories were separated")
                break

            chosen = kernel.select_probe(req.probe_policy, probes, live, ev, rng)
            applied = tuple(mapping[r] for r in chosen.applied if r in mapping)
            if chosen.fresh:
                worktree.dispose()
                worktree = Worktree(req.repo_root, "why")
                ev.new_episode()

            obs = run_probe(
                req.node_id,
                applied,
                chosen.repeats,
                worktree,
                flow.probe,
                req.allow_network,
                req.budgets.command_timeout_s,
                on_start=lambda argv, i, n, _p=chosen: flow.append(
                    EventKind.COMMAND_STARTED,
                    {
                        "display": f"pytest {req.node_id} [{_p.name}]",
                        "phase": "diagnosis",
                        "node_id": req.node_id,
                        "run": f"{i}/{n}",
                    },
                ),
                on_done=lambda argv, res: flow.append(
                    EventKind.COMMAND_FINISHED,
                    {"duration_s": round(res.duration_s, 3), "exit_code": res.exit_code, "phase": "diagnosis"},
                ),
            )
            flow.append(
                EventKind.CHECK_RESULT,
                {"result": _probe_as_result(req.node_id, obs), "index": 1, "total": 1},
            )
            flow.append(
                EventKind.PROBE_SELECTED,
                {
                    "probe": chosen.name,
                    "applied": [h.label for h in applied],
                    "repeats": chosen.repeats,
                    "fresh_episode": chosen.fresh,
                    "observation": obs.to_dict(),
                },
            )
            if not obs.is_evidence:
                # An unobservable probe is a measurement failure. It is
                # recorded and charged, but it eliminates nothing: treating it
                # as a `blocked` outcome would contradict theories on the
                # strength of a broken measurement.
                notes.append(f"a probe could not be observed ({obs.node_outcome.value}); it eliminated nothing")
                continue

            saw_failure = saw_failure or obs.outcome != PASS
            ev.record(chosen.applied, chosen.repeats, obs.outcome)
            before, scored = scored, [kernel.score(h, ev) for h in hypotheses]
            killed = _eliminated_ids(before, scored)
            if killed:
                flow.append(
                    EventKind.HYPOTHESES_ELIMINATED,
                    {"hypothesis_ids": killed, "by_probe": chosen.name, "count": len(killed)},
                )

        if not saw_failure:
            return Diagnosis(
                Verdict.UNVERIFIABLE,
                None,
                GateStatus.NOT_APPLICABLE,
                (),
                0,
                (),
                (
                    *notes,
                    "the target passed in every experiment run, including in a clean disposable "
                    "sandbox, so there is no failure here to explain",
                    "if it fails elsewhere, the difference is in that environment; this run "
                    "locates no cause and attributes nothing to the repository",
                ),
            )

        diagnosis = kernel.derive_diagnosis(scored, probes, ev, mapping, notes)

        # The action space could not explain the failure. Before reporting that,
        # measure the assertions it discovered: a missing module or file is a
        # real finding the intervention grammar cannot express. Only here — a
        # located interventional cause is stronger and is never displaced.
        if diagnosis.status is Verdict.REPRESENTATION_INADEQUATE:
            absent = _absent_assertions(flow, handles, worktree, req.budgets.command_timeout_s)
            diagnosis = kernel.observational_diagnosis(absent, notes, diagnosis.contradicted) or diagnosis

        # 5. Narrow a coarse ordering cause to the smallest subset the probes
        #    actually distinguish. `first:tests/` is true and nearly useless
        #    when `first:tests/test_a_first.py` is deterministically reachable,
        #    and the receipt may claim only what was executed.
        if diagnosis.status is Verdict.DIAGNOSIS_SUPPORTED and diagnosis.causes:
            diagnosis, worktree, _refine_notes = refine_ordering_cause(
                flow, req, diagnosis, collected, worktree, ev, mapping
            )
        if diagnosis.causes:
            flow.append(
                EventKind.CAUSE_SUPPORTED,
                {
                    "causes": [c.to_dict() for c in diagnosis.causes],
                    "support": diagnosis.support.value if diagnosis.support else None,
                    "gate": diagnosis.gate.value,
                },
            )
        return diagnosis
    finally:
        worktree.dispose()


def _absent_assertions(flow: Flow, handles: list[Handle], worktree: Worktree, timeout_s: float) -> list[Handle]:
    """Measure every assertion handle in the sandbox and record what was seen.

    Each observation is executed and charged like any other command. The result
    is recorded whether the thing was present or absent, so the ledger shows the
    measurement rather than only the findings it supported.
    """
    absent: list[Handle] = []
    for handle in [h for h in handles if not h.is_intervention]:
        # Announced before it runs, like every other command: the live view and
        # the settled transcript are the same projection, and a command that
        # appeared only once it had finished would break that.
        flow.append(
            EventKind.COMMAND_STARTED,
            {"display": f"assert {handle.label}", "phase": "diagnosis", "node_id": handle.arg, "run": "1/1"},
        )
        outcome, res = evaluate_assertion(handle, worktree, flow.probe, timeout_s)
        # Closed either way. A command announced and never finished leaves the
        # ledger unbalanced and the attempt uncharged, and a sandbox that could
        # not run it is an unsuccessful completion — not a silent one, and never
        # a successful one.
        flow.append(
            EventKind.COMMAND_FINISHED,
            {
                "duration_s": round(res.duration_s, 3) if res else 0.0,
                "exit_code": res.exit_code if res else -1,
                "phase": "diagnosis",
                "outcome": outcome,
                "successful": res is not None and outcome != UNOBSERVABLE,
            },
        )
        flow.append(
            EventKind.ASSERTION_OBSERVED,
            {
                "handle": handle.to_dict(),
                "outcome": outcome,
                # Kept as a boolean too, because every reader of this event asks
                # exactly one question of it, and `unobservable` must answer no.
                "absent": outcome == ABSENT,
                "evidence": "executed in the diagnosis sandbox; nothing was applied or withdrawn",
            },
        )
        if outcome == ABSENT:
            absent.append(handle)
    return absent


def _probe_as_result(node_id: str, obs) -> dict:
    """Probe observations are recorded in the same shape as check results, so
    the transcript, the replay and the budget accounting see one kind of
    evidence rather than two."""
    return CheckResult(
        check_id="probe",
        node_id=node_id,
        phase=GatePhase.BASELINE,
        outcome=obs.node_outcome,
        signature=obs.signature,
        duration_s=obs.duration_s,
        exit_code=0,
        detail=obs.detail,
    ).to_dict()


def _extend_handles_with_model(
    flow: Flow,
    handles: list[Handle],
    failure_text: str,
    node_id: str,
    spend: SpendLedger,
    task_id: str,
) -> list[Handle]:
    """Ask a configured model for additional handles.

    The model's entire authority here is to *suggest a measurement*. Every
    suggestion is validated against the closed primitive set, deduplicated
    against what was already discovered, and capped. An unavailable or invalid
    model costs the run nothing: the deterministic handles stand on their own.
    """
    try:
        config = llm.ProviderConfig.from_env()
    except llm.ModelUnavailable as exc:
        flow.append(
            EventKind.MODEL_UNAVAILABLE,
            {"reason": str(exc), "operation": "propose_handles", "effect": "deterministic handles only"},
        )
        return handles

    messages = llm.handles_prompt(failure_text, node_id, handles)
    max_output = 800
    # Every live request draws on the same authorization. A diagnosis request
    # that skipped the reservation would be an unbudgeted call to a paid API —
    # the cap would cover `propose_change` only and quietly not the loop that
    # precedes it.
    request_id = content_hash({"t": task_id, "op": "propose_handles"})[:16]
    try:
        reservation_id, _amount = spend.reserve(request_id, task_id, 0, token_ceiling(messages), max_output)
    except BudgetRefused as exc:
        flow.append(
            EventKind.SPEND_REFUSED,
            {"reason": str(exc), "operation": "propose_handles", "request_id": request_id, "scope": spend.scope},
        )
        return handles
    flow.append(
        EventKind.SPEND_RESERVED,
        {
            "operation": "propose_handles",
            "request_id": request_id,
            "spend_event_id": reservation_id,
            "scope": spend.scope,
        },
    )
    flow.append(EventKind.MODEL_REQUEST_STARTED, {"operation": "propose_handles", "model": config.model})
    try:
        reply = llm.post_chat(config, messages, max_output_tokens=max_output)
    except (llm.ModelUnavailable, llm.ModelResponseInvalid) as exc:
        settled = spend.settle(request_id, task_id, 0, ModelUsage())
        flow.append(
            EventKind.SPEND_SETTLED,
            {"operation": "propose_handles", "request_id": request_id, "spend_event_id": settled["event_id"]},
        )
        flow.append(
            EventKind.MODEL_UNAVAILABLE,
            {"reason": str(exc), "operation": "propose_handles", "effect": "deterministic handles only"},
        )
        return handles

    flow.append(EventKind.MODEL_RESPONSE_RECEIVED, {"operation": "propose_handles", **reply.redacted()})
    settled = spend.settle(request_id, task_id, 0, reply.usage)
    flow.append(
        EventKind.SPEND_SETTLED,
        {"operation": "propose_handles", "request_id": request_id, "spend_event_id": settled["event_id"]},
    )
    proposed = _accept_or_repair(
        flow,
        spend,
        "propose_handles",
        task_id,
        config,
        messages,
        reply,
        max_output,
        0,
        lambda raw: llm.validate_handles(raw, handles),
        effect="deterministic handles only",
    )
    if proposed is None:
        return handles

    seen = {h.label for h in handles}
    out = list(handles)
    for h in proposed:
        if h.label not in seen and len(out) < 12:
            seen.add(h.label)
            out.append(h)
    return out


def _extend_hypotheses_with_model(
    flow: Flow,
    hypotheses: list[dict[str, Any]],
    roles: list[str],
    handles: list[Handle],
    ev: kernel.Evidence,
    failure_text: str,
    node_id: str,
    spend: SpendLedger,
    task_id: str,
) -> list[dict[str, Any]]:
    """Ask a configured model for additional theories, once.

    The model's authority here is to *widen the theory space*, never to settle
    it. Every proposal is validated into the closed IR, refused if it collides
    with a theory already enumerated, and then scored by `kernel.score` against
    the observations already recorded — the same scoring the deterministic
    theories get, with no allowance for where a theory came from. A model theory
    that mispredicts one observed outcome is contradicted immediately.

    Returns the *additional* hypotheses only. An unavailable, refused, invalid
    or empty response returns an empty list, and the caller is unchanged by it.
    """
    try:
        config = llm.ProviderConfig.from_env()
    except llm.ModelUnavailable as exc:
        flow.append(
            EventKind.MODEL_UNAVAILABLE,
            {"reason": str(exc), "operation": "propose_hypotheses", "effect": "enumerated theories only"},
        )
        return []

    messages = llm.hypotheses_prompt(node_id, failure_text, roles, handles, ev.observed)
    max_output = 1600
    request_id = content_hash({"t": task_id, "op": "propose_hypotheses"})[:16]
    try:
        reservation_id, _amount = spend.reserve(request_id, task_id, 0, token_ceiling(messages), max_output)
    except BudgetRefused as exc:
        flow.append(
            EventKind.SPEND_REFUSED,
            {"reason": str(exc), "operation": "propose_hypotheses", "request_id": request_id, "scope": spend.scope},
        )
        return []
    flow.append(
        EventKind.SPEND_RESERVED,
        {
            "operation": "propose_hypotheses",
            "request_id": request_id,
            "spend_event_id": reservation_id,
            "scope": spend.scope,
        },
    )
    flow.append(EventKind.MODEL_REQUEST_STARTED, {"operation": "propose_hypotheses", "model": config.model})
    try:
        reply = llm.post_chat(config, messages, max_output_tokens=max_output)
    except (llm.ModelUnavailable, llm.ModelResponseInvalid) as exc:
        # The request may have been served and billed even though nothing usable
        # came back, so the full reservation is charged rather than released.
        settled = spend.settle(request_id, task_id, 0, ModelUsage())
        flow.append(
            EventKind.SPEND_SETTLED,
            {"operation": "propose_hypotheses", "request_id": request_id, "spend_event_id": settled["event_id"]},
        )
        flow.append(
            EventKind.MODEL_UNAVAILABLE,
            {"reason": str(exc), "operation": "propose_hypotheses", "effect": "enumerated theories only"},
        )
        return []

    flow.append(EventKind.MODEL_RESPONSE_RECEIVED, {"operation": "propose_hypotheses", **reply.redacted()})
    settled = spend.settle(request_id, task_id, 0, reply.usage)
    flow.append(
        EventKind.SPEND_SETTLED,
        {"operation": "propose_hypotheses", "request_id": request_id, "spend_event_id": settled["event_id"]},
    )
    proposed = _accept_or_repair(
        flow,
        spend,
        "propose_hypotheses",
        task_id,
        config,
        messages,
        reply,
        max_output,
        0,
        lambda raw: llm.validate_hypotheses(raw, roles),
        effect="enumerated theories only",
    )
    if proposed is None:
        return []

    # An id already in use is refused rather than renamed. Renaming would put a
    # value into the theory space that the model did not return, and the
    # elimination record in the ledger names theories by id.
    known = {h["hypothesis_id"] for h in hypotheses}
    return [h for h in proposed if h["hypothesis_id"] not in known]


def emit_diagnosis_receipt(flow: Flow, td: Path, diagnosis: Diagnosis, target: str) -> tuple[dict, int]:
    """The `why` receipt.

    Deliberately not routed through `derive_verdict`: that function decides a
    gate outcome, and a diagnosis has no gate. Sharing it would mean inventing
    gate phases that were never run.
    """
    flow.append(EventKind.DIAGNOSIS_EMITTED, {"diagnosis": diagnosis.to_dict(), "target": target})
    proj = flow.projection()
    contract = proj.contract
    usage = proj.model_usage
    receipt = VerificationReceipt(
        task_id=proj.task_id,
        verdict=diagnosis.status,
        reason=diagnosis.notes[-1] if diagnosis.notes else "",
        contract_hash=contract.content_hash if contract else "",
        checkset_hash="",
        patch_hash="",
        baseline_signature=proj.results[0].signature if proj.results else None,
        results=tuple(proj.results),
        checks_not_executed=(),
        sandbox=contract.actual_sandbox if contract else (proj.sandbox or IsolationLevel.PARTIAL),
        sandbox_detail=proj.sandbox_detail,
        authorities=contract.authorities if contract else Authorities(),
        commands=proj.commands,
        seconds=proj.seconds,
        tokens=_render_usage(usage, proj.model_unavailable),
        censored=proj.censored,
        remaining_uncertainty=_diagnosis_uncertainty(proj, diagnosis),
    ).to_dict()
    receipt["diagnosis"] = diagnosis.to_dict()
    receipt["rejected_phase"] = None
    # `why` can now spend, so its receipt must show what it spent — derived by
    # joining this task's references to the authoritative spend ledger.
    receipt["spend"] = _spend_summary(flow, proj)
    flow.append(EventKind.RECEIPT_EMITTED, {"receipt": receipt})
    write_artifacts(td, flow.ledger.path, receipt, proj)
    return receipt, _diagnosis_exit_code(diagnosis)


def _render_usage(usage: list[ModelUsage], unavailable: str) -> str:
    if not usage:
        return "not_applicable (no model was invoked)" if not unavailable else f"none ({unavailable})"
    if any(not u.known for u in usage):
        return "unknown (the provider reported no usage for at least one request)"
    return f"{sum(u.input_tokens or 0 for u in usage)} in / {sum(u.output_tokens or 0 for u in usage)} out"


def _diagnosis_uncertainty(proj: TaskProjection, diagnosis: Diagnosis) -> tuple[str, ...]:
    out: list[str] = []
    if diagnosis.support is Support.OBSERVATIONAL:
        out.append(
            "the cause is supported by an executable assertion, not by applying and withdrawing it; "
            "the remediation is unverified"
        )
    if diagnosis.surviving_classes > 1:
        out.append(f"{diagnosis.surviving_classes} behavioural classes remain live")
    if proj.censored:
        out.append("the probe budget was exhausted, so the action space was not fully explored")
    if proj.model_unavailable:
        out.append(f"no model contributed handles: {proj.model_unavailable}")
    if proj.handles:
        out.append(
            f"only the {len(proj.handles)} discovered handles were testable; a cause outside that "
            "action space cannot be seen from here"
        )
    return tuple(out)


def _diagnosis_exit_code(diagnosis: Diagnosis) -> int:
    if diagnosis.status is Verdict.DIAGNOSIS_SUPPORTED:
        return EXIT_VERIFIED
    if diagnosis.status is Verdict.INFRASTRUCTURE_BLOCKED:
        return EXIT_INFRASTRUCTURE
    return EXIT_ABSTAINED


def cmd_why(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"error: repository not found: {repo_root}", file=sys.stderr)
        return EXIT_USAGE

    budgets = Budgets(
        max_commands=args.max_commands,
        max_seconds=args.max_seconds,
        command_timeout_s=args.timeout,
    )
    req = WhyRequest(
        repo_root=repo_root,
        node_id=args.test,
        budgets=budgets,
        allow_partial=args.allow_partial_sandbox,
        require_full=args.require_full_sandbox,
        allow_network=args.allow_network,
        max_probes=args.max_probes,
        use_model=not args.no_model,
    )

    probe = probe_isolation()
    task_id, td = allocate_task_dir(repo_root, Verb.WHY.value, task_fingerprint({"n": args.test, "v": "why"}))
    ledger = Ledger(td / "ledger.jsonl", task_id)
    renderer = LiveRenderer(quiet=args.json)
    flow = Flow(ledger, renderer, probe, args.allow_network)

    flow.append(
        EventKind.TASK_STARTED,
        {"task_id": task_id, "verb": Verb.WHY.value, "repo": str(repo_root), "target": args.test},
    )
    flow.append(EventKind.SANDBOX_PROBED, {"level": probe.level.value, "detail": probe.detail})

    blocked = _isolation_block_reason(probe, req.require_full, req.allow_partial)
    if blocked:
        flow.append(EventKind.INFRASTRUCTURE_BLOCKED, {"reason": blocked})
        receipt, code = emit_diagnosis_receipt(
            flow,
            td,
            Diagnosis(
                Verdict.INFRASTRUCTURE_BLOCKED,
                None,
                GateStatus.NOT_APPLICABLE,
                (),
                0,
                (),
                (blocked,),
            ),
            args.test,
        )
        if args.json:
            print(canonical(receipt))
        return code

    flow.append(
        EventKind.SANDBOX_AUTHORIZED,
        {"partial_authorized": probe.level is IsolationLevel.PARTIAL and req.allow_partial},
    )

    contract = TaskContract(
        task_id=task_id,
        verb=Verb.WHY,
        request=f"why {args.test}",
        repo_root=str(repo_root),
        baseline_tree_hash=tree_hash(repo_root),
        scope="diagnosis of one failing test from discovered handles; no patch is produced",
        budgets=budgets,
        requested_sandbox=IsolationLevel.FULL if req.require_full else probe.level,
        actual_sandbox=probe.level,
        authorities=Authorities(
            spec_approval="not_applicable",
            partial_sandbox="--allow-partial-sandbox"
            if (probe.level is IsolationLevel.PARTIAL and req.allow_partial)
            else "none",
        ),
        allow_network=args.allow_network,
    )
    flow.append(EventKind.CONTRACT_FROZEN, {"contract": contract.to_dict(), "contract_hash": contract.content_hash})

    # `why` draws on the same scope-keyed authorization as `fix`. Without a
    # ledger here the two optional diagnosis requests could not be reserved, so
    # `why` made no model request at all and `--no-model` governed nothing on
    # this path.
    spend = SpendLedger(
        spend_ledger_path(repo_root),
        scope=args.scope or content_hash({"repo": str(repo_root), "verb": "why"})[:16],
        limit_usd=args.max_usd,
        pricing=_pricing_from_args(args),
    )
    flow.append(
        EventKind.CONTEXT_SELECTED,
        {
            "scope": spend.scope,
            "limit_usd": spend.limit_usd,
            "remaining_usd_at_start": round(spend.remaining_usd(), 8),
            "selection": "authorization scope for cross-task spend",
        },
    )

    try:
        diagnosis = run_diagnosis(flow, req, td, spend=spend)
    except SandboxError as exc:
        flow.append(EventKind.INFRASTRUCTURE_BLOCKED, {"reason": str(exc)})
        diagnosis = Diagnosis(Verdict.INFRASTRUCTURE_BLOCKED, None, GateStatus.NOT_APPLICABLE, (), 0, (), (str(exc),))

    receipt, code = emit_diagnosis_receipt(flow, td, diagnosis, args.test)
    if args.json:
        print(canonical(receipt))
    return code


# --------------------------------------------------------------------------
# `rift fix` — propose a change, then submit it to the gate that already exists
#
# There is exactly one acceptance path in this runtime, and `fix` does not add
# a second one. It produces a candidate patch and then calls `run_gate` — the
# same function `verify` calls, over the same CheckSet, the same sandbox and
# the same receipt. A verb that verified its own output would be the
# model-satisfies-its-own-judge failure wearing a different name.
#
# The model's authority ends at the diff. Every verdict below comes from
# `kernel.derive_verdict` reading the ledger.
# --------------------------------------------------------------------------


# Bounded context: only files the failure itself named or the target's imports
# reached, and only up to these caps. An unbounded prompt is an unbounded bill,
# and a prompt assembled by relevance ranking is a component whose behaviour
# nobody can bound.
#
# `MAX_FILE_CHARS` is the per-file policy and is enforced as
# `min(MAX_FILE_CHARS, MAX_CONTEXT_CHARS - spent)`. It was defined and never
# referenced before DAR-022, while an unnamed `MAX_EXCERPT_CHARS = 8_000` did
# the actual limiting — a governed constant that governed nothing beside an
# ungoverned one that did.
MAX_CONTEXT_FILES = 6
MAX_CONTEXT_CHARS = 60_000
MAX_FILE_CHARS = 20_000

# Rough and deliberately pessimistic. A provider-specific tokenizer would make
# the spend reservation depend on the very component being bounded, and would
# under-count exactly when the prompt is unusual.
CHARS_PER_TOKEN = 3.0
PROMPT_OVERHEAD_TOKENS = 1_500


def token_ceiling(messages: list[dict[str, str]]) -> int:
    """An upper bound on the input tokens of a built request."""
    chars = sum(len(m.get("content", "")) + len(m.get("role", "")) for m in messages)
    return int(chars / CHARS_PER_TOKEN) + PROMPT_OVERHEAD_TOKENS


@dataclass(frozen=True)
class FixRequest:
    repo_root: Path
    node_id: str
    preserve: tuple[str, ...]
    budgets: Budgets
    allow_partial: bool
    require_full: bool
    allow_network: bool
    max_probes: int
    max_attempts: int


# Bounded excerpting, in the order `excerpt` tries: a file inside the per-file
# budget is sent whole, a larger one is sent as complete AST definitions, and a
# window around an anchor is the floor when neither is possible.
#
# It read "never a whole file" until DAR-022. That rule sent 770 characters of a
# 23,272 character file, and the patch built from it could not be applied to any
# line of the tree it described. What has to be bounded is the *budget*, not
# completeness.
WINDOW_RADIUS = 12
MAX_WINDOWS_PER_FILE = 6

# Scoped secret patterns. Deliberately narrow: this is a redaction pass over
# text already selected for sending, not a repository scanner. A broad matcher
# would blank out ordinary source and hide the very lines the model must read.
_SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("private_key_block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("provider_key", r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}\b"),
    ("bearer_token", r"(?i)\bbearer\s+[A-Za-z0-9_\-.=]{16,}"),
    ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    (
        "assigned_secret",
        r"(?i)\b\w*(?:secret|passwd|password|api[_-]?key|access[_-]?token|auth[_-]?token)\w*\s*[:=]\s*"
        r"[\"'][^\"'\n]{6,}[\"']",
    ),
    ("url_credentials", r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s:/@]+@"),
)
_SECRET_RES = tuple((name, re.compile(pattern)) for name, pattern in _SECRET_PATTERNS)


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Replace credential-shaped spans with a marker. Returns (text, counts).

    Only counts are ever recorded. A ledger that stored what was redacted would
    be a durable copy of the secret, written by the component whose job was to
    remove it.
    """
    counts: dict[str, int] = {}
    for name, pattern in _SECRET_RES:
        text, n = pattern.subn(f"<redacted:{name}>", text)
        if n:
            counts[name] = counts.get(name, 0) + n
    return text, counts


def definition_spans(source: str) -> list[tuple[int, int, str]]:
    """Every top-level and nested definition, as (start, end, name).

    Deterministic AST boundaries, not a regex over lines. The distinction is
    the whole of DAR-022: a regex can find where `class TLRUCache` is *written*
    but not where it *ends*, so a window centred on the match captures the
    declaration and the lines that happen to precede it — which is how the
    stopped run sent lines 575-599 of a class occupying 587-713, omitting the
    `__setitem__` the fix had to change.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            end = getattr(node, "end_lineno", None)
            if end:
                # Decorators are part of the definition; a patch whose context
                # starts below them describes a different region of the file.
                start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
                out.append((start, int(end), node.name))
    return sorted(out)


def enclosing_unit(spans: list[tuple[int, int, str]], line: int) -> tuple[int, int, str] | None:
    """The outermost definition containing `line`.

    Outermost, not innermost: a traceback frame inside a method is usually
    fixed by seeing the class it belongs to — its siblings, its `__init__`, the
    attribute names it shares. The frozen design asks for the complete
    enclosing function *or class*, and the class is the larger true answer.
    """
    holders = [s for s in spans if s[0] <= line <= s[1]]
    return min(holders, key=lambda s: (s[0], -s[1])) if holders else None


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Overlapping or adjacent ranges into one. No line is ever sent twice."""
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def excerpt(
    source: str, cited: list[int], wanted: set[str], budget: int = MAX_FILE_CHARS
) -> tuple[str, list[tuple[int, int]], str]:
    """One bounded excerpt of a file. Returns (text, ranges kept, why).

    Three rules, tried in order:

    1. **A file that fits is sent whole.** Sending 770 characters of a 23,272
       character file because a symbol matched on one line is how the stopped
       run produced patches whose context lines were invented. If the whole
       file is inside the per-file budget, the whole file is the answer.
    2. **Otherwise, complete definitions.** Every cited traceback line is
       resolved to its enclosing definition and that definition is sent in
       full; so is every definition the test imports by name. Complete units
       only — a half-sent function is exactly the input that produces an
       unappliable hunk.
    3. **Otherwise, bounded windows.** A single definition larger than the
       budget, or a file with no parseable definitions, falls back to line
       windows around the anchors. Truncation is disclosed in the text.

    Elision is always marked. A prompt that hides the fact that it is partial
    invites a patch whose hunk context is wrong, which is the failure this
    function exists to stop causing.
    """
    lines = source.splitlines()
    total = len(lines)
    if not total:
        return "", [], "empty file"

    if len(source) <= budget:
        return source, [(1, total)], "whole file (within the per-file budget)"

    spans = definition_spans(source)
    # Traceback-cited source outranks everything. It is the only region the
    # failure itself named, and it is where a fix most often has to go.
    #
    # A cited line need not be inside any definition. `dateutil/tz/tz.py` fails
    # on `EPOCH = datetime.datetime.utcfromtimestamp(0)` at module level, where
    # `enclosing_unit` correctly returns None — and the previous implementation
    # then contributed nothing for it, so imported class definitions filled the
    # whole per-file budget and the one region the traceback pointed at was the
    # one region not sent. A module-level cited line gets a bounded window.
    primary: list[tuple[int, int]] = []
    definitions: list[str] = []
    module_level: list[str] = []
    for line in cited:
        unit = enclosing_unit(spans, line)
        if unit:
            primary.append((unit[0], unit[1]))
            definitions.append(f"{unit[2]} encloses cited line {line}")
        else:
            primary.append((max(1, line - WINDOW_RADIUS), min(total, line + WINDOW_RADIUS)))
            module_level.append(str(line))
    # Then, with whatever budget remains, the definitions the test imports.
    secondary: list[tuple[int, int]] = []
    for lo, hi, name in spans:
        if name in wanted:
            secondary.append((lo, hi))
            definitions.append(f"{name} is imported by the test")

    kept: list[tuple[int, int]] = []
    chunks: list[str] = []
    used = 0
    reason = ""
    if primary or secondary:
        # Say what was actually selected. A window around a module-level line is
        # not a complete definition, and a reason that called it one would make
        # the ledger agree with a description of the code rather than the code.
        clauses = []
        if definitions:
            clauses.append("complete definitions: " + "; ".join(dict.fromkeys(definitions)))
        if module_level:
            clauses.append("bounded windows around cited lines at module level: " + ", ".join(module_level))
        reason = "; ".join(clauses)
        # Merged within each tier, then emitted in tier order, so a lower
        # priority unit can never displace a cited one.
        ordered = _merge(primary) + [s for s in _merge(secondary) if s not in _merge(primary)]
        for lo, hi in ordered:
            body = "\n".join(lines[lo - 1 : hi])
            if used + len(body) > budget:
                continue
            used += len(body)
            kept.append((lo, hi))
            chunks.append(f"# lines {lo}-{hi}\n{body}")
        kept.sort()
        chunks = [f"# lines {lo}-{hi}\n" + "\n".join(lines[lo - 1 : hi]) for lo, hi in kept]

    if not kept:
        # No definition fitted, or none was found. Windows are the floor, not
        # the design: they are what is left when a complete unit is impossible.
        anchors = sorted(set(cited)) or [1]
        windows = _merge([(max(1, a - WINDOW_RADIUS), min(total, a + WINDOW_RADIUS)) for a in anchors])
        reason = "bounded windows (no complete definition fits the per-file budget)"
        for lo, hi in windows[:MAX_WINDOWS_PER_FILE]:
            if hi < lo:
                continue
            body = "\n".join(lines[lo - 1 : hi])
            if used + len(body) > budget:
                break
            used += len(body)
            kept.append((lo, hi))
            chunks.append(f"# lines {lo}-{hi}\n{body}")

    if kept and (kept[0][0] > 1 or kept[-1][1] < total or len(kept) > 1):
        chunks.append(f"# ... {total} lines total; only the ranges above were sent ...")
    return "\n\n".join(chunks), kept, reason


# Where a repository keeps importable source. Checked in order; the first hit
# wins. This is a lookup table, not a search heuristic.
SOURCE_ROOTS = ("src", "", "lib")


def imported_names(repo_root: Path, test_file: str) -> set[str]:
    """Symbols the target's test module imports by name.

    These anchor an excerpt window on the definition under test, which is what
    a wrong-value bug needs: nothing raised inside it, so no frame names it.
    """
    path = (repo_root / test_file).resolve()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, SyntaxError, UnicodeDecodeError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            out.update(a.asname or a.name for a in node.names)
    return out


def imported_modules(repo_root: Path, test_file: str, cap: int = 8) -> list[str]:
    """Repository files the target's test module imports.

    Citation alone is not enough. A test that asserts on a *returned value* —
    `assert add(2, 2) == 4` where `add` quietly returns the wrong number —
    produces a traceback containing only the test file, because nothing raised
    inside the implementation. Live calibration hit exactly this: no
    implementation was ever selected, and the model, shown no source, invented
    both the file path and the line it was "fixing".

    So the second signal is the import graph, read by AST from the test module
    itself. It is an observation of what the test actually depends on, not a
    similarity score, and it is bounded to one level: the test's own imports,
    resolved against the repository, and nothing transitive.
    """
    path = (repo_root / test_file).resolve()
    try:
        path.relative_to(repo_root.resolve())
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, SyntaxError, UnicodeDecodeError):
        return []

    dotted: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            dotted.append(node.module)
        elif isinstance(node, ast.Import):
            dotted.extend(a.name for a in node.names)

    out: list[str] = []
    for name in dotted:
        rel = name.replace(".", "/")
        for root in SOURCE_ROOTS:
            for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
                full = repo_root / root / candidate if root else repo_root / candidate
                if full.is_file():
                    found = full.resolve().relative_to(repo_root.resolve()).as_posix()
                    if found not in out:
                        out.append(found)
                    break
        if len(out) >= cap:
            break
    return out[:cap]


def defines(repo_root: Path, rel: str, names: set[str]) -> set[str]:
    """Which of `names` this file actually defines, by AST."""
    try:
        source = (repo_root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    return {name for _, _, name in definition_spans(source)} & names


def reexport_sources(repo_root: Path, rel: str, names: set[str], cap: int = 4) -> list[str]:
    """Where a package re-exports a name from, one hop, by AST.

    `from pygments.formatters import RawTokenFormatter` resolves to
    `pygments/formatters/__init__.py`, which does not define it — the class
    lives in `other.py`. Following the package's own `from . import x` one level
    is deterministic and reaches the implementation. It is an observation of
    what the package says about itself, not a search.
    """
    try:
        tree = ast.parse((repo_root / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError, SyntaxError, UnicodeDecodeError):
        return []
    package = Path(rel).parent
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        exported = {a.asname or a.name for a in node.names}
        if not (names & exported or "*" in exported):
            continue
        module = node.module or ""
        # A relative import resolves against this package. An absolute one
        # resolves against each source root, because `pkg/plugins/other.py` is
        # written `from pkg.plugins.other import X` whether the tree is flat or
        # under `src/`. Only one level is followed either way.
        stem = Path(module.replace(".", "/"))
        bases = [package / stem] if node.level else [Path(r) / stem if r else stem for r in SOURCE_ROOTS]
        found = ""
        for base in bases:
            for candidate in (base.with_suffix(".py"), base / "__init__.py"):
                if (repo_root / candidate).is_file():
                    found = candidate.as_posix()
                    break
            if found:
                break
        if found and found not in out and found != rel:
            out.append(found)
        if len(out) >= cap:
            break
    return out[:cap]


def _resolve_module(repo_root: Path, rel: str, module: str, level: int) -> str:
    """One import statement to one repository file, or "" if it is not local.

    A relative import resolves against the importing file's package; an absolute
    one against each source root, because the same package is written the same
    way whether the tree is flat or under `src/`.
    """
    stem = Path(module.replace(".", "/"))
    bases = [Path(rel).parent / stem] if level else [Path(r) / stem if r else stem for r in SOURCE_ROOTS]
    for base in bases:
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if (repo_root / candidate).is_file():
                return candidate.as_posix()
    return ""


def used_dependencies(repo_root: Path, rel: str, cap: int = 4) -> list[str]:
    """Local modules whose imported names this file actually *uses*.

    The frozen order is traceback, imports, then dependencies of what those
    reached. This is that third step, and it is a traversal of edges the
    selected code itself declares — not a crawl.

    Two bounds make it that. Only **local** imports are followed, so the
    standard library and site-packages are never candidates. And a name must be
    **referenced in the body**, not merely imported: `icalendar/timezone/__init__.py`
    imports four symbols from `.tzid` and one from `.tzp`, and constructs
    `TZP()` — so `tzp.py` is a dependency the selected code demonstrably relies
    on, while a re-export it never touches is not.

    Deterministic: imports are visited in source order and the result is
    deduplicated in that order.
    """
    try:
        source = (repo_root / rel).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, ValueError, SyntaxError, UnicodeDecodeError):
        return []

    imported: list[tuple[str, str]] = []  # (local name, resolved file)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found = _resolve_module(repo_root, rel, node.module or "", node.level)
            if found and found != rel:
                imported.extend((a.asname or a.name, found) for a in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found = _resolve_module(repo_root, rel, alias.name, 0)
                if found and found != rel:
                    imported.append(((alias.asname or alias.name).split(".")[0], found))

    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    referenced |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    out: list[str] = []
    for name, found in imported:
        if name in referenced and found not in out:
            out.append(found)
        if len(out) >= cap:
            break
    return out


def grep_definitions(repo_root: Path, names: set[str], cap_files: int = 3, scan_cap: int = 2000) -> list[str]:
    """Files defining any of `names`, found by scanning source text.

    The last stage of the frozen selection order: traceback, then imports, then
    grep. It runs only for names the first two stages did not resolve, so a
    symbol that the import graph explains never reaches it.

    Deterministic by construction — files are visited in sorted order, the
    pattern is a literal definition of a known name, and both the number of
    files scanned and the number returned are capped. It is a bounded text
    search, not a relevance ranking.
    """
    if not names:
        return []
    pattern = re.compile(
        r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+(" + "|".join(map(re.escape, sorted(names))) + r")\b", re.M
    )
    out: list[str] = []
    scanned = 0
    for path in sorted(repo_root.rglob("*.py")):
        if scanned >= scan_cap or len(out) >= cap_files:
            break
        parts = path.relative_to(repo_root).parts
        if any(p in (".rift", ".git", ".venv", "node_modules", "build", "dist") for p in parts):
            continue
        scanned += 1
        try:
            if pattern.search(path.read_text(encoding="utf-8")):
                out.append(path.relative_to(repo_root).as_posix())
        except (OSError, UnicodeDecodeError):
            continue
    return out


def select_context(
    repo_root: Path, failure_text: str, node_id: str, protected: tuple[str, ...]
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Choose the source files the model may see. Deterministic and bounded.

    Selection is by *citation*, not by similarity: a file is included because
    the observed traceback named it, or because it is the target's own module.
    There is no embedding, no retrieval and no ranking model — a relevance
    score would be a second unbounded component in a system whose entire point
    is bounding them.

    Ordering is deepest-frame-first, because the frame where the exception was
    raised is more often the one that must change than the frame that called
    it. Protected paths and `.rift/` are excluded: the model cannot be asked to
    edit the judge, so it is not shown it.
    """
    root = repo_root.resolve()
    cited: list[str] = []
    lines_for: dict[str, list[int]] = {}
    for match in _TRACEBACK_FILE.finditer(failure_text):
        raw = match.group("quoted") or match.group("pytest")
        if not raw:
            continue
        rel = raw.replace("\\", "/")
        if rel not in cited:
            cited.append(rel)
        number = match.group("qline") or match.group("pline")
        if number:
            lines_for.setdefault(rel, []).append(int(number))
    # Deepest frame last in a traceback, so reverse to put it first.
    cited.reverse()

    target_file = node_id.split("::")[0]
    # Names the target test imports. Their definitions anchor a unit even when
    # nothing raised inside them.
    wanted = imported_names(root, target_file)
    imported = imported_modules(root, target_file)

    # The frozen selection order: traceback, then the import graph, then one
    # re-export hop, then bounded grep. Each stage runs only on what the
    # previous ones did not resolve, so grep is a floor and not a search
    # strategy (DAR-022).
    unresolved = set(wanted)
    for rel in [*cited, *imported]:
        unresolved -= defines(root, rel, unresolved)
    reexported: list[str] = []
    for rel in imported:
        if not unresolved:
            break
        for found in reexport_sources(root, rel, unresolved):
            if found not in reexported:
                reexported.append(found)
            unresolved -= defines(root, found, unresolved)
    grepped = grep_definitions(root, unresolved) if unresolved else []

    # Dependencies the already-reached code actually uses, followed breadth
    # first from what the traceback and the import graph found. Bounded by the
    # file cap rather than a hop count: the frontier stops when there is no more
    # room, which is the only limit that means anything (DAR-024).
    traversed: list[str] = []
    frontier = [*cited, *imported]
    seen_edges = set(frontier)
    while frontier and len(traversed) + len(frontier) < MAX_CONTEXT_FILES * 3:
        nxt: list[str] = []
        for rel in frontier:
            for found in used_dependencies(root, rel):
                if found not in seen_edges:
                    seen_edges.add(found)
                    traversed.append(found)
                    nxt.append(found)
        frontier = nxt

    # Cited frames first (deepest first), then what the test imports, then the
    # dependencies that code uses, then re-exports, then grep, then the test
    # itself. Every signal is an observation; none is a ranking.
    ordered = [*cited, *imported, *traversed, *reexported, *grepped, target_file]

    chosen: list[tuple[str, str]] = []
    skipped: list[str] = []
    sent_ranges: dict[str, list[tuple[int, int]]] = {}
    redactions: dict[str, int] = {}
    total = 0
    seen: set[str] = set()
    reasons: dict[str, str] = {}
    for rel in ordered:
        if len(chosen) >= MAX_CONTEXT_FILES:
            continue
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            skipped.append(f"{rel} (outside the repository)")
            continue
        if not path.is_file():
            continue
        posix = path.relative_to(root).as_posix()
        if posix in seen:
            # Deduplication is on the *resolved* path, not the name it arrived
            # under. One file can be named twice — a traceback cites it by
            # absolute path and the target's import graph by repository-relative
            # path — and keying on the raw string sent its bytes twice and spent
            # two of the six slots on one file, while the manifest listed it
            # twice against a single line-range entry.
            continue
        seen.add(posix)
        parts = path.relative_to(root).parts
        if ".rift" in parts or ".git" in parts or path.relative_to(root).as_posix() in protected:
            skipped.append(f"{rel} (protected or excluded)")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped.append(f"{rel} (unreadable as UTF-8)")
            continue
        # Never more than the per-file budget, and never more than the global
        # budget still unspent: the last file must not be able to blow the cap
        # the first five respected.
        budget = min(MAX_FILE_CHARS, MAX_CONTEXT_CHARS - total)
        text, ranges, why = excerpt(text, sorted(set(lines_for.get(rel, []))), wanted, budget)
        text, counts = redact(text)
        for name, n in counts.items():
            redactions[name] = redactions.get(name, 0) + n
        if not text.strip():
            skipped.append(f"{posix} (nothing to excerpt)")
            continue
        if total + len(text) > MAX_CONTEXT_CHARS:
            skipped.append(f"{posix} (context cap reached)")
            continue
        total += len(text)
        sent_ranges[posix] = ranges
        reasons[posix] = why
        chosen.append((posix, text))

    manifest = {
        "files": [rel for rel, _ in chosen],
        # Exactly which lines left the machine, per file. Ranges and counts
        # only: recording a redacted value would make the ledger a durable copy
        # of the secret the redaction removed.
        "line_ranges": {rel: [list(r) for r in rs] for rel, rs in sent_ranges.items()},
        "chars": total,
        "redaction_counts": redactions,
        "skipped": skipped,
        "cap_files": MAX_CONTEXT_FILES,
        "cap_chars": MAX_CONTEXT_CHARS,
        "cap_file_chars": MAX_FILE_CHARS,
        "window_radius": WINDOW_RADIUS,
        # Why each file looks the way it does, so a reader can tell a whole
        # file from a complete definition from a truncated window without
        # holding the source next to the ledger.
        "selection_reason": reasons,
        "stages": {
            "traceback": cited,
            "imports": imported,
            "used_dependencies": traversed,
            "reexports": reexported,
            "grep": grepped,
            "unresolved": sorted(
                unresolved - set().union(*(defines(root, r, unresolved) for r in grepped)) if grepped else unresolved
            ),
        },
        "selection": (
            "traceback frames, then the import graph, then dependencies that code uses, then "
            "re-exports, then bounded grep; cited regions outrank imported definitions within a file; "
            "whole files when they fit the per-file budget, otherwise complete AST definitions, "
            "otherwise bounded windows; no embedding, retrieval or ranking"
        ),
    }
    return chosen, manifest


def _request_change(
    flow: Flow,
    spend: SpendLedger,
    task_id: str,
    messages: list[dict[str, str]],
    max_output_tokens: int,
    attempt: int,
    td: Path,
) -> tuple[str, str] | None:
    """One bounded `propose_change` request, reserved before it is sent.

    Returns the validated (diff, summary), or None with the reason already
    durable in the ledger. Every outcome — refused, unreachable, invalid — is
    an explicit recorded state, never a silent fallback.
    """
    try:
        config = llm.ProviderConfig.from_env()
    except llm.ModelUnavailable as exc:
        flow.append(EventKind.MODEL_UNAVAILABLE, {"reason": str(exc), "operation": "propose_change"})
        return None

    ceiling = token_ceiling(messages)
    # A stable id ties this task's ledger reference to the authoritative spend
    # event, and makes settlement idempotent across a resume.
    request_id = content_hash({"t": task_id, "op": "propose_change", "a": attempt})[:16]
    try:
        reservation_id, _reserved = spend.reserve(request_id, task_id, attempt, ceiling, max_output_tokens)
    except BudgetRefused as exc:
        # Refused before the request is sent, which is the only point at which
        # refusing costs nothing.
        flow.append(
            EventKind.SPEND_REFUSED,
            {
                "reason": str(exc),
                "operation": "propose_change",
                "request_id": request_id,
                "scope": spend.scope,
            },
        )
        return None

    # The task ledger stores a *reference*. The figure itself lives in
    # `.rift/spend.jsonl`; copying it here would create a second source of
    # truth that can disagree with the first.
    flow.append(
        EventKind.SPEND_RESERVED,
        {
            "operation": "propose_change",
            "request_id": request_id,
            "spend_event_id": reservation_id,
            "scope": spend.scope,
            "authoritative": "\u002erift/spend.jsonl",
        },
    )
    flow.append(
        EventKind.MODEL_REQUEST_STARTED,
        {"operation": "propose_change", "model": config.model, "attempt": attempt},
    )
    try:
        reply = llm.post_chat(config, messages, max_output_tokens=max_output_tokens)
    except (llm.ModelUnavailable, llm.ModelResponseInvalid) as exc:
        # The request may have been served and billed even though no usable
        # response came back, so the full reservation is charged rather than
        # released. An unanswered request is not a free one.
        settled = spend.settle(request_id, task_id, attempt, ModelUsage())
        flow.append(
            EventKind.SPEND_SETTLED,
            {"operation": "propose_change", "request_id": request_id, "spend_event_id": settled["event_id"]},
        )
        flow.append(EventKind.MODEL_UNAVAILABLE, {"reason": str(exc), "operation": "propose_change"})
        return None

    flow.append(EventKind.MODEL_RESPONSE_RECEIVED, {"operation": "propose_change", **reply.redacted()})
    settled = spend.settle(request_id, task_id, attempt, reply.usage)
    flow.append(
        EventKind.SPEND_SETTLED,
        {"operation": "propose_change", "request_id": request_id, "spend_event_id": settled["event_id"]},
    )
    proposal = _accept_or_repair(
        flow, spend, "propose_change", task_id, config, messages, reply, max_output_tokens, attempt, llm.validate_change
    )
    return _canonicalize_proposal(flow, td, attempt, proposal)


def _canonicalize_proposal(
    flow: Flow, td: Path, attempt: int, proposal: tuple[str, str] | None
) -> tuple[str, str] | None:
    """Three deterministic stages, each persisted and hashed. Every arm, one path.

        exact model diff  ->  candidate-attempt-NNN/raw.diff
              |  transport normalisation (canonical_diff: CRLF, bare CR, final newline)
              v
        normalized        ->  candidate-attempt-NNN/normalized.diff
              |  git-conditioned hunk-count canonicalisation (DAR-031, unchanged)
              v
        canonical         ->  candidate-attempt-NNN/canonical.diff  ->  ChangeSet, gate

    They are separate because their authorities are different sizes and neither
    invariant can describe the other. Normalisation rewrites line terminators
    anywhere in the file; canonicalisation may alter only the digits inside a
    valid `@@` header and must leave every other byte identical. Run as one
    step, a receipt could say only that the bytes changed, and a reviewer asking
    *what* changed would have to take the answer on trust (DAR-032).

    `attempt` comes from the repair loop and addresses the artifacts, so a later
    proposal cannot overwrite the evidence an earlier ledger event points at.
    Every rejected candidate was requested, charged and refused on the evidence;
    it stays reconstructable for exactly that reason (DAR-033).

    Placed on the single path arms A, B and C all take: a canonicaliser
    available to the full kernel but not to the model-alone ablation would be an
    advantage the experiment then measured and attributed to the kernel.
    """
    if proposal is None:
        return None
    raw, summary = proposal

    raw_record = raw_candidate_record(td, attempt)
    normalized_record = normalized_candidate_record(td, attempt)
    canonical_record = canonical_candidate_record(td, attempt)

    # First, before anything can touch it. This is the model's output and the
    # only artifact that can be checked against a provider transcript.
    raw_hash = persist_candidate(raw_record, raw)

    normalization = normalize_candidate(raw)
    normalized_hash = persist_candidate(normalized_record, normalization.diff)

    # Structural eligibility is asked of git — `git apply --numstat`, with and
    # without `--recount`, in a temporary directory — so repository content can
    # never influence whether count metadata is rewritten. It is asked of the
    # normalised bytes, because a patch git rejects only for a missing final
    # newline is not a hunk-count defect.
    structural_raw = structural_parse(normalization.diff)
    structural_recount = structural_parse(normalization.diff, recount=True) if structural_raw != 0 else 0
    result = canonicalize_patch(normalization.diff, structural_raw, structural_recount)
    canonical_hash = persist_candidate(canonical_record, result.diff)

    flow.append(
        EventKind.CANDIDATE_CANONICALIZED,
        {
            "attempt": attempt,
            "raw_candidate_hash": raw_hash,
            "normalized_candidate_hash": normalized_hash,
            "canonical_candidate_hash": canonical_hash,
            # Recorded relative to the task directory, so the event stays
            # meaningful when the tree is archived or moved.
            "raw_candidate_record": raw_record.relative_to(td).as_posix(),
            "normalized_candidate_record": normalized_record.relative_to(td).as_posix(),
            "canonical_candidate_record": canonical_record.relative_to(td).as_posix(),
            "normalization": normalization.to_dict(),
            "canonicalization": result.to_dict(),
        },
    )
    # UNSAFE means "this cannot be canonicalised", not "this candidate is
    # rejected". The normalised patch proceeds to `git apply --check` exactly as
    # it would have — refusing to modify a patch is not refusing the patch.
    return result.diff, summary


def _accept_or_repair[T](
    flow: Flow,
    spend: SpendLedger,
    operation: str,
    task_id: str,
    config: llm.ProviderConfig,
    messages: list[dict[str, str]],
    reply: llm.ModelReply,
    max_output_tokens: int,
    attempt: int,
    validate: Callable[[dict[str, Any]], T],
    effect: str = "",
) -> T | None:
    """Validate a reply; on failure make the one repair request, then abstain.

    Shared by all three operations. `CLAUDE.md` grants the repair to every model
    operation, not to `propose_change` alone, and a diagnosis that silently
    discards well-reasoned hypotheses because they were badly serialised is the
    same defect as discarding a patch — it just costs a worse verdict instead of
    a visible abstention (DAR-021).
    """
    try:
        return llm.extract_validated(reply.text, validate)
    except (llm.ModelResponseInvalid, ValidationError) as exc:
        payload = {
            "reason": str(exc),
            "operation": operation,
            "finish_reason": reply.finish_reason,
            "response_chars": len(reply.text),
            "output_exhausted": llm.output_exhausted(reply),
        }
        if effect:
            payload["effect"] = effect
        flow.append(EventKind.MODEL_RESPONSE_INVALID, payload)
        if llm.output_exhausted(reply):
            # Nothing was said, so there is nothing to re-serialise. Asking
            # again under the same allowance buys the identical failure twice,
            # which is what the aborted preliminary run demonstrated at both
            # 4,000 and 8,000 tokens.
            return None
        return _repair_request(
            flow, spend, operation, task_id, config, messages, reply, str(exc), max_output_tokens, attempt, validate
        )


def _repair_request[T](
    flow: Flow,
    spend: SpendLedger,
    operation: str,
    task_id: str,
    config: llm.ProviderConfig,
    messages: list[dict[str, str]],
    bad: llm.ModelReply,
    reason: str,
    max_output_tokens: int,
    attempt: int,
    validate: Callable[[dict[str, Any]], T],
) -> T | None:
    """The one schema-repair request the design allows, then abstention.

    Reached only for a *completed* reply that failed extraction or validation.
    It asks for the same proposal re-serialised and nothing else, so a repair
    cannot become a second attempt at the task — `max_attempts` governs those.
    """
    op_repair = f"{operation}_repair"
    repair_id = content_hash({"t": task_id, "op": op_repair, "a": attempt})[:16]
    repair_messages = llm.repair_prompt(messages, bad.text, reason)
    try:
        reservation_id, _reserved = spend.reserve(
            repair_id, task_id, attempt, token_ceiling(repair_messages), max_output_tokens
        )
    except BudgetRefused as exc:
        flow.append(
            EventKind.SPEND_REFUSED,
            {"reason": str(exc), "operation": op_repair, "request_id": repair_id, "scope": spend.scope},
        )
        return None
    flow.append(
        EventKind.SPEND_RESERVED,
        {
            "operation": op_repair,
            "request_id": repair_id,
            "spend_event_id": reservation_id,
            "scope": spend.scope,
            "authoritative": ".rift/spend.jsonl",
        },
    )
    flow.append(EventKind.MODEL_REPAIR_REQUESTED, {"operation": operation, "reason": reason, "attempt": attempt})
    try:
        repaired = llm.post_chat(config, repair_messages, max_output_tokens=max_output_tokens)
    except (llm.ModelUnavailable, llm.ModelResponseInvalid) as exc:
        settled = spend.settle(repair_id, task_id, attempt, ModelUsage())
        flow.append(
            EventKind.SPEND_SETTLED,
            {"operation": op_repair, "request_id": repair_id, "spend_event_id": settled["event_id"]},
        )
        flow.append(EventKind.MODEL_UNAVAILABLE, {"reason": str(exc), "operation": op_repair})
        return None

    flow.append(EventKind.MODEL_RESPONSE_RECEIVED, {"operation": op_repair, **repaired.redacted()})
    settled = spend.settle(repair_id, task_id, attempt, repaired.usage)
    flow.append(
        EventKind.SPEND_SETTLED,
        {"operation": op_repair, "request_id": repair_id, "spend_event_id": settled["event_id"]},
    )
    try:
        return llm.extract_validated(repaired.text, validate)
    except (llm.ModelResponseInvalid, ValidationError) as exc:
        # One repair, then abstain. A second would be an unbounded loop wearing
        # a retry's name.
        flow.append(
            EventKind.MODEL_RESPONSE_INVALID,
            {"reason": str(exc), "operation": op_repair, "repair_exhausted": True},
        )
        return None


def probe_policy_error(policy: str, seed: int | None) -> str:
    """Reject a probe policy and seed that cannot describe a repeatable run.

    `random` without a seed draws from the OS, so the run is unreproducible
    while carrying every appearance of a frozen experiment — the failure mode
    the frozen seed exists to prevent. A seed with the default policy is the
    same mistake read the other way: it suggests the run was seeded when
    selection was deterministic and the seed changed nothing.
    """
    if policy == "random" and seed is None:
        return "--probe-policy random requires --probe-seed; without it a rerun is a different experiment"
    if policy != "random" and seed is not None:
        return f"--probe-seed has no effect under --probe-policy {policy}; it would record a seed nothing used"
    return ""


def cmd_fix(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"error: repository not found: {repo_root}", file=sys.stderr)
        return EXIT_USAGE

    budgets = Budgets(max_commands=args.max_commands, max_seconds=args.max_seconds, command_timeout_s=args.timeout)
    req = FixRequest(
        repo_root=repo_root,
        node_id=args.test,
        preserve=tuple(args.preserve or ()),
        budgets=budgets,
        allow_partial=args.allow_partial_sandbox,
        require_full=args.require_full_sandbox,
        allow_network=args.allow_network,
        max_probes=args.max_probes,
        max_attempts=max(1, args.max_attempts),
    )
    # Before any sandbox, diagnosis or request. An unreproducible arm B is worse
    # than an absent one: it looks like a frozen experiment and is not.
    usage = probe_policy_error(args.probe_policy, args.probe_seed)
    if usage:
        print(f"error: {usage}", file=sys.stderr)
        return EXIT_USAGE

    probe = probe_isolation()
    task_id, td = allocate_task_dir(repo_root, Verb.FIX.value, task_fingerprint({"n": args.test, "v": "fix"}))
    ledger = Ledger(td / "ledger.jsonl", task_id)
    flow = Flow(ledger, LiveRenderer(quiet=args.json), probe, args.allow_network)

    flow.append(
        EventKind.TASK_STARTED,
        {"task_id": task_id, "verb": Verb.FIX.value, "repo": str(repo_root), "target": args.test},
    )
    # Durable, so resume reuses the policy and seed the run started with rather
    # than whatever the command line says the second time.
    flow.append(
        EventKind.PROBE_POLICY_FROZEN,
        {"policy": args.probe_policy, "seed": args.probe_seed},
    )
    flow.append(EventKind.SANDBOX_PROBED, {"level": probe.level.value, "detail": probe.detail})

    blocked = _isolation_block_reason(probe, req.require_full, req.allow_partial)
    if blocked:
        flow.append(EventKind.INFRASTRUCTURE_BLOCKED, {"reason": blocked})
        return _emit_and_report(flow, td, args)
    flow.append(
        EventKind.SANDBOX_AUTHORIZED,
        {"partial_authorized": probe.level is IsolationLevel.PARTIAL and req.allow_partial},
    )

    # The judge is frozen before any patch exists, so no proposal can influence
    # what it will be measured against.
    checkset = build_checkset(args.test, req.preserve, repo_root, budgets.command_timeout_s)
    flow.append(EventKind.CHECKSET_FROZEN, {"checkset": checkset.to_dict(), "checkset_hash": checkset.content_hash})
    contract = TaskContract(
        task_id=task_id,
        verb=Verb.FIX,
        request=f"fix {args.test}",
        repo_root=str(repo_root),
        baseline_tree_hash=tree_hash(repo_root),
        scope="one change check plus the preservation checks declared on the command line",
        budgets=budgets,
        requested_sandbox=IsolationLevel.FULL if req.require_full else probe.level,
        actual_sandbox=probe.level,
        authorities=Authorities(
            spec_approval="not_applicable",
            partial_sandbox="--allow-partial-sandbox"
            if (probe.level is IsolationLevel.PARTIAL and req.allow_partial)
            else "none",
        ),
        allow_network=args.allow_network,
    )
    flow.append(EventKind.CONTRACT_FROZEN, {"contract": contract.to_dict(), "contract_hash": contract.content_hash})

    # Cross-task spend is keyed by a frozen authorization scope, so every task
    # in a run draws on one authorization rather than each being cheap alone.
    spend = SpendLedger(
        spend_ledger_path(repo_root),
        scope=args.scope or content_hash({"repo": str(repo_root), "verb": "fix"})[:16],
        limit_usd=args.max_usd,
        pricing=_pricing_from_args(args),
    )
    flow.append(
        EventKind.CONTEXT_SELECTED,
        {
            "scope": spend.scope,
            "limit_usd": spend.limit_usd,
            "remaining_usd_at_start": round(spend.remaining_usd(), 8),
            "selection": "authorization scope for cross-task spend",
        },
    )

    # The declared task identity applies to **every** path, not only the
    # ablation. Freezing it inside the `--model-alone` branch meant arm A
    # enforced the manifest's failure identity while arms B and C parsed the
    # same arguments and derived their own from whatever they happened to
    # observe — three arms solving three subtly different tasks, which is the
    # one thing a comparison cannot survive.
    frozen = freeze_declared_reproducer(flow, args, repo_root, checkset, probe, budgets)
    if frozen is None:
        return _emit_and_report(flow, td, args)
    checkset = frozen
    flow.append(EventKind.CHECKSET_FROZEN, {"checkset": checkset.to_dict(), "checkset_hash": checkset.content_hash})

    if args.model_alone:
        # The ablation branch. It shares this verb's setup — sandbox authority,
        # frozen judge, ledger, spend — and diverges only where BM-06 needs it
        # to: no diagnosis, no probing, target-pass acceptance.
        return run_model_alone(flow, req, checkset, td, args, spend)

    declared = flow.projection().reproducer
    if declared is not None and not reproduces_as_declared(flow, req, checkset, declared):
        # Before diagnosis, and therefore before any model request. A task that
        # does not reproduce as declared cannot be repaired against that
        # declaration, and paying to propose against it would buy a patch for a
        # different failure.
        return _emit_and_report(flow, td, args)

    # 1. Diagnose first. A patch proposed without a located cause is a guess,
    #    and the diagnosis costs no model call.
    why_req = WhyRequest(
        repo_root=repo_root,
        node_id=args.test,
        budgets=budgets,
        allow_partial=req.allow_partial,
        require_full=req.require_full,
        allow_network=req.allow_network,
        max_probes=req.max_probes,
        # Diagnosis runs first, always, and may spend one bounded request on
        # additional handles. Skipping it to save a call would mean proposing a
        # patch with no located cause — a guess with a receipt attached.
        use_model=not args.no_model,
        # Read back from the ledger, not from the command line. A resumed run
        # must repeat the experiment it started, and the durable record is the
        # only thing that survives the process that made it.
        probe_policy=flow.projection().probe_policy,
        probe_seed=flow.projection().probe_seed,
    )
    diagnosis = run_diagnosis(flow, why_req, td, spend=spend)
    flow.append(EventKind.DIAGNOSIS_EMITTED, {"diagnosis": diagnosis.to_dict(), "target": args.test})

    # The kernel selects the reproducer from executed evidence, before any
    # patch exists. `fix` only records what it is handed; there is no path from
    # a model proposal to these fields.
    # Derived from the experiment that actually reproduced the failure, never
    # from the isolated baseline: for an order-dependent target that baseline
    # passes and carries no signature at all.
    collected_nodes = [
        str(n)
        for ev in read_events(flow.ledger.path)[0]
        if ev.kind is EventKind.CONTEXT_SELECTED
        for n in ev.payload.get("collected", [])
    ]
    artifact_paths = judge_artifact_paths(args.test, tuple(diagnosis.causes), collected_nodes, repo_root)
    reproducer = (
        None
        if artifact_paths is None
        else kernel.select_reproducer(
            diagnosis,
            args.test,
            _probe_records(flow),
            runner_config_hash(repo_root),
            tree_hash(repo_root),
            hash_artifacts(repo_root, artifact_paths),
        )
    )
    if reproducer is not None and artifact_paths is not None:
        # Every file the reproducer executes becomes protected. Without this a
        # candidate could edit the polluter test — weakening the experiment
        # while leaving the contract record byte-identical.
        checkset = replace(
            checkset,
            protected_paths=tuple(sorted({*checkset.protected_paths, *artifact_paths})),
        )
        flow.append(
            EventKind.REPRODUCER_FROZEN,
            {
                "reproducer": reproducer.to_dict(),
                "reproducer_hash": reproducer.content_hash,
                "render": reproducer.render(),
                "protected_added": artifact_paths,
            },
        )
        flow.append(
            EventKind.CHECKSET_FROZEN,
            {"checkset": checkset.to_dict(), "checkset_hash": checkset.content_hash},
        )

    if diagnosis.support is Support.OBSERVATIONAL:
        # An assertion observes; it does not intervene. There is nothing to
        # apply and withdraw, so there is nothing a patch could be gated
        # against — generating one would spend a request to produce something
        # this runtime could never verify.
        flow.append(
            EventKind.GATE_PHASE_FINISHED,
            {
                "phase": GatePhase.CANDIDATE.value,
                "passed": False,
                "reason": (
                    "the diagnosis is observational: an executable assertion supports it but no "
                    "apply/withdraw intervention exists, so no patch was generated and none was gated"
                ),
                "artifacts": {},
            },
        )
        return _emit_and_report(flow, td, args)

    failure_text = _first_failure_text(flow)
    sources, manifest = select_context(repo_root, failure_text, args.test, checkset.protected_paths)
    flow.append(EventKind.CONTEXT_SELECTED, {**manifest, "target": args.test})

    # 2. Propose. Each rejected attempt stays in the ledger and is charged.
    accepted = False
    for attempt in range(1, req.max_attempts + 1):
        messages = llm.change_prompt(args.test, failure_text, [c.label for c in diagnosis.causes], sources)
        proposal = _request_change(flow, spend, task_id, messages, args.max_output_tokens, attempt, td)
        if proposal is None:
            break
        diff, summary = proposal
        validation = kernel.validate_patch(diff, checkset.protected_paths)
        if validation.rejected:
            # Structural rejection, before anything is executed. The frozen
            # judge is not negotiable and a patch that touches it never runs.
            flow.append(EventKind.CHANGESET_REJECTED, {"reason": validation.reason, "attempt": attempt})
            continue
        changeset = ChangeSet(diff=diff, touched_paths=validation.touched, origin="model")
        changeset_record(td).write_text(changeset.diff, encoding="utf-8", newline="\n")
        flow.append(
            EventKind.CHANGESET_REGISTERED,
            {"changeset": changeset.to_dict(), "attempt": attempt, "summary_not_evidence": summary[:200]},
        )
        accepted = True
        break

    if not accepted:
        proj = flow.projection()
        reason = (
            proj.spend_refused or proj.model_unavailable or "no proposal survived validation; no patch was produced"
        )
        flow.append(
            EventKind.GATE_PHASE_FINISHED,
            {"phase": GatePhase.CANDIDATE.value, "passed": False, "reason": reason, "artifacts": {}},
        )
        return _emit_and_report(flow, td, args)

    # 3. The gate. Not a second verification path — the one `verify` uses.
    gate_req = VerifyRequest(
        repo_root=repo_root,
        diff_path=changeset_record(td),
        node_id=args.test,
        preserve=req.preserve,
        budgets=budgets,
        allow_partial=req.allow_partial,
        require_full=req.require_full,
        allow_network=req.allow_network,
        task_id=task_id,
    )
    code = run_gate(flow, gate_req, td)
    if args.json:
        print(canonical(flow.projection().receipt or {}))
    return code


def proj_reproducer(flow: Flow) -> ReproductionContract | None:
    """The frozen reproducer, read back from the ledger rather than a variable."""
    return flow.projection().reproducer


def run_model_alone(flow: Flow, req: FixRequest, checkset: CheckSet, td: Path, args, spend) -> int:
    """BM-06 arm A: the incumbent practice, run under this runtime's plumbing.

    Same provider, model and bounded context selection; no deterministic
    diagnosis and no probing; accepted when the target passes after the patch is
    applied. It reuses the existing proposal validation, ChangeSet store,
    sandbox and spend ledger, and adds no second verification path.

    It is fenced rather than trusted. `verified_against_approved_checks` is
    unreachable from here — the only acceptance this function can express is
    `accepted_by_target_pass` — because an ablation able to emit the product's
    own verdict would eventually be quoted as the product's result.
    """
    flow.append(EventKind.BENCHMARK_ABLATION, {"ablation": "model_alone", "verdict_ceiling": "accepted_by_target_pass"})
    change_check = checkset.by_type(ClaimType.CHANGE)[0]

    # Arm A gets the *same frozen task* as B and C. Without this it establishes
    # its baseline on the bare target, which for an order-dependent case passes
    # — so arm A reports nothing to repair and never proposes, and the arms stop
    # being comparable. The experiment is all it gets: what follows is still
    # weaker by design.
    reproducer = proj_reproducer(flow)
    with Worktree(req.repo_root, "arm-a") as wt:
        observed = run_episode(flow, wt, change_check, GatePhase.BASELINE, reproducer, req.repo_root)
    if observed.outcome is Outcome.PASSED:
        flow.append(
            EventKind.REPRODUCTION_FAILED,
            {"reason": "the target passes before any patch; there is nothing for arm A to repair"},
        )
        return _emit_and_report(flow, td, args)

    failure_text = _first_failure_text(flow)
    sources, manifest = select_context(req.repo_root, failure_text, req.node_id, checkset.protected_paths)
    flow.append(EventKind.CONTEXT_SELECTED, {**manifest, "target": req.node_id})

    messages = llm.change_prompt(req.node_id, failure_text, [], sources)

    def stop(reason: str) -> int:
        flow.append(
            EventKind.GATE_PHASE_FINISHED,
            {"phase": GatePhase.CANDIDATE.value, "passed": False, "reason": reason, "artifacts": {}},
        )
        return _emit_and_report(flow, td, args)

    proposal = _request_change(flow, spend, flow.ledger.task_id, messages, args.max_output_tokens, 1, td)
    if proposal is None:
        proj = flow.projection()
        return stop(proj.spend_refused or proj.model_unavailable or "no proposal was produced")

    diff, summary = proposal
    validation = kernel.validate_patch(diff, checkset.protected_paths)
    if validation.rejected:
        flow.append(EventKind.CHANGESET_REJECTED, {"reason": validation.reason, "attempt": 1})
        return stop(f"patch rejected: {validation.reason}")

    changeset = ChangeSet(diff=diff, touched_paths=validation.touched, origin="model")
    changeset_record(td).write_text(changeset.diff, encoding="utf-8", newline=chr(10))
    flow.append(
        EventKind.CHANGESET_REGISTERED,
        {"changeset": changeset.to_dict(), "attempt": 1, "summary_not_evidence": summary[:200]},
    )

    # Arm A's whole acceptance rule: apply, run the target, accept if it passes.
    # No withdrawal, no reapplication, no preservation — that difference is the
    # thing BM-06 measures, so it is not quietly repaired here.
    with Worktree(req.repo_root, "arm-a-candidate") as wt:
        try:
            wt.apply_patch(changeset.diff)
        except SandboxError as exc:
            # A model patch that will not apply is an ordinary outcome, not an
            # error in the harness: the arm proposed something the tree rejects,
            # which is a failure to repair and must be *recorded* as one. This
            # path was missing, so the process died with a traceback and emitted
            # no receipt at all — the arm produced no verdict, and the benchmark
            # driver could only report that something had gone wrong somewhere.
            flow.append(
                EventKind.GATE_PHASE_FINISHED,
                {
                    "phase": GatePhase.CANDIDATE.value,
                    "passed": False,
                    "reason": f"the proposed patch does not apply to the baseline tree: {exc}",
                    "artifacts": {"ablation_verdict": ""},
                },
            )
            return _emit_and_report(flow, td, args)
        # The reset that makes each episode clean removes anything the patch is
        # not known to own. Without naming the patch's paths it deletes the
        # patch itself, and the candidate measures the unpatched tree.
        after = run_episode(
            flow,
            wt,
            change_check,
            GatePhase.CANDIDATE,
            reproducer,
            req.repo_root,
            patch_owned=frozenset(changeset.touched_paths),
        )
    passed = after.outcome is Outcome.PASSED
    flow.append(
        EventKind.GATE_PHASE_FINISHED,
        {
            "phase": GatePhase.CANDIDATE.value,
            "passed": passed,
            "reason": "arm A accepts on the target passing after the patch is applied"
            if passed
            else "the target did not pass after the patch was applied",
            "artifacts": {"ablation_verdict": Verdict.ACCEPTED_BY_TARGET_PASS.value if passed else ""},
        },
    )
    return _emit_and_report(flow, td, args)


def reproduces_as_declared(flow: Flow, req: FixRequest, checkset: CheckSet, reproducer: ReproductionContract) -> bool:
    """Run the declared experiment once, before anything is proposed.

    `run_gate` already checks the declared signature at baseline, but that is
    after a patch exists — by then the request has been paid for. When a caller
    states the failure identity, it is enforced here, at zero model cost, so a
    mismatch stops the task instead of buying a proposal against a failure that
    is not the declared one.
    """
    change_check = checkset.by_type(ClaimType.CHANGE)[0]
    with Worktree(req.repo_root, "declared") as wt:
        result = run_episode(flow, wt, change_check, GatePhase.BASELINE, reproducer, req.repo_root)
    if result.outcome is not Outcome.FAILED:
        flow.append(
            EventKind.REPRODUCTION_FAILED,
            {
                "reason": "the declared task does not reproduce before any patch exists",
                "observed_outcome": result.outcome.value,
                "reproducer": reproducer.render(),
            },
        )
        return False
    if not signature_compatible(reproducer.signature, result.signature):
        flow.append(
            EventKind.REPRODUCTION_FAILED,
            {
                "reason": "the declared signature does not match the observed failure",
                "expected": reproducer.signature.render(),
                "observed": result.signature.render() if result.signature else "<none>",
            },
        )
        return False
    return True


def _pricing_from_args(args: argparse.Namespace) -> Pricing:
    return Pricing(
        input_per_mtok=args.price_input,
        output_per_mtok=args.price_output,
        provider="openai-compatible",
        model=os.environ.get(llm.ENV_MODEL, "unset"),
    )


def _first_failure_text(flow: Flow) -> str:
    """The observed failure, read back from the ledger rather than carried in a
    variable: what the model is shown must be what was durably recorded."""
    events, _ = read_events(flow.ledger.path)
    excerpt = ""
    signature = ""
    for ev in events:
        if ev.kind is not EventKind.CHECK_RESULT:
            continue
        if not excerpt and ev.payload.get("failure_excerpt"):
            excerpt = str(ev.payload["failure_excerpt"])
        result = ev.payload.get("result") or {}
        sig = result.get("signature")
        if not signature and sig:
            signature = f"{sig.get('exception_type', '')}: {sig.get('message', '')}"
    # The excerpt carries the frames; a signature alone carries none, and a
    # prompt built from it would name no file for the model to look at.
    return excerpt or signature


def _isolation_block_reason(probe: IsolationProbe, require_full: bool, allow_partial: bool) -> str:
    """The same authority rules `verify` applies, in one place so the two verbs
    cannot drift apart."""
    if probe.level is IsolationLevel.PARTIAL:
        if require_full:
            return "--require-full-sandbox was given but full isolation is unavailable"
        if not allow_partial:
            return (
                "full isolation is unavailable and --allow-partial-sandbox was not given; "
                "repository code was not executed"
            )
    if not probe.tree_kill:
        return "this platform cannot terminate a process tree reliably; repository code was not executed"
    return ""


def _emit_blocked(flow: Flow, td: Path, args: argparse.Namespace) -> int:
    return _emit_and_report(flow, td, args)


def _emit_and_report(flow: Flow, td: Path, args: argparse.Namespace) -> int:
    proj = flow.projection()
    receipt, code = emit_receipt(flow, proj, td)
    if args.json:
        print(canonical(receipt))
    return code


def cmd_resume(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    incomplete: list[tuple[str, TaskProjection]] = []
    for td in iter_task_dirs(repo_root):
        try:
            events, truncated = read_events(td / "ledger.jsonl")
        except LedgerCorrupt as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_INFRASTRUCTURE
        proj = reduce(events, truncated)
        if not proj.complete:
            incomplete.append((td.name, proj))
    if not incomplete:
        print("no incomplete tasks")
        return EXIT_VERIFIED
    if args.task_id:
        selected = [(n, p) for n, p in incomplete if n == args.task_id]
        if not selected:
            print(f"error: no incomplete task {args.task_id}", file=sys.stderr)
            return EXIT_USAGE
    elif len(incomplete) > 1:
        print("multiple incomplete tasks; choose one:", file=sys.stderr)
        for name, proj in incomplete:
            print(f"  {name}  phase={proj.phase.value}", file=sys.stderr)
        return EXIT_USAGE
    else:
        selected = incomplete

    task_id, proj = selected[0]
    td = task_dir(repo_root, task_id)
    if proj.contract is None:
        print(f"error: {task_id} was interrupted before its contract was frozen; nothing to resume", file=sys.stderr)
        return EXIT_USAGE
    # Bound once: the projection is rebuilt below if drift is recorded, and the
    # contract is frozen so it cannot differ between the two reductions.
    contract = proj.contract

    probe = probe_isolation()
    ledger = Ledger(td / "ledger.jsonl", task_id)
    renderer = LiveRenderer(quiet=args.json)
    flow = Flow(ledger, renderer, probe, contract.allow_network)

    observed = tree_hash(repo_root)
    if observed != contract.baseline_tree_hash:
        flow.append(
            EventKind.DRIFT_DETECTED,
            {"recorded": contract.baseline_tree_hash, "observed": observed},
        )
        # Re-reduce: `proj` was built before this event existed, so it still
        # says `drift=False`. Reading a stale projection is exactly how a
        # resumed run would inherit observations the drift just invalidated.
        proj = flow.projection()
    if contract.verb is Verb.WHY:
        return _resume_why(flow, td, proj, contract, repo_root, args)
    if contract.verb is Verb.FIX:
        return _resume_fix(flow, td, proj, contract, args)

    req = VerifyRequest(
        repo_root=repo_root,
        diff_path=td / "change-set.diff",
        node_id=proj.checkset.by_type(ClaimType.CHANGE)[0].node_id if proj.checkset else "",
        preserve=tuple(c.node_id for c in (proj.checkset.by_type(ClaimType.PRESERVATION) if proj.checkset else ())),
        budgets=contract.budgets,
        allow_partial=contract.authorities.partial_sandbox != "none",
        require_full=False,
        allow_network=contract.allow_network,
        task_id=task_id,
    )
    code = run_gate(flow, req, td)
    if args.json:
        print(canonical(flow.projection().receipt or {}))
    return code


def _resume_why(
    flow: Flow,
    td: Path,
    proj: TaskProjection,
    contract: TaskContract,
    repo_root: Path,
    args: argparse.Namespace,
) -> int:
    """Continue an interrupted diagnosis from its recorded observations.

    Every probe this task already paid for is on disk, so resuming does not
    mean restarting: the recorded observations are replayed into a fresh
    `Evidence` trace and the loop continues from there. That is the whole
    reason each intermediate measurement — including every bisection half — is
    appended before the next one starts.

    Tracked drift is the exception. If the tree changed, the earlier
    observations describe a repository that no longer exists, so they are
    discarded rather than reconciled: guessing which file "cannot matter" is
    the inference the ledger exists to remove.
    """
    target = contract.request.removeprefix("why ").strip()
    req = WhyRequest(
        repo_root=repo_root,
        node_id=target,
        budgets=contract.budgets,
        allow_partial=contract.authorities.partial_sandbox != "none",
        require_full=False,
        allow_network=contract.allow_network,
        max_probes=getattr(args, "max_probes", 8),
        use_model=False,
    )
    if proj.drift:
        flow.append(
            EventKind.CONTEXT_SELECTED,
            {
                "collected_nodes": 0,
                "target": target,
                "note": "tracked drift since baseline; recorded observations were discarded",
                "selection": "observations from before the drift describe a tree that no longer exists",
            },
        )
        diagnosis = run_diagnosis(flow, req, td)
    else:
        diagnosis = run_diagnosis(flow, req, td, resume_from=proj)

    receipt, code = emit_diagnosis_receipt(flow, td, diagnosis, target)
    if args.json:
        print(canonical(receipt))
    return code


def _resume_fix(flow: Flow, td: Path, proj: TaskProjection, contract: TaskContract, args: argparse.Namespace) -> int:
    """Continue an interrupted `fix`, from whichever phase it reached.

    Three distinct interruption points, and only one of them is resumable by
    simply carrying on.

    *Before a patch exists* — diagnosis or proposal never completed. Nothing
    durable claims a candidate, so the task is reported as incomplete rather
    than silently restarted: re-running would re-spend, and this function has
    no authorization to do that.

    *During a model request* — a `model_request_started` with no durable
    response means the request's outcome and cost are unknown. It is never
    automatically repeated. The reservation is already consumed in the spend
    ledger, which is the conservative direction, and continuing would risk
    paying twice for one answer.

    *After the patch was registered* — the ChangeSet is on disk, so the gate can
    run from durable state with no further model involvement. That is the only
    case that proceeds.
    """
    started = [s for s in proj.spend if s.get("operation")]
    settled_ids = {e.get("spend_event_id") for e in proj.spend if e.get("spend_event_id") and "request_id" in e}
    interrupted_request = bool(started) and not settled_ids

    if interrupted_request:
        flow.append(
            EventKind.INFRASTRUCTURE_BLOCKED,
            {
                "reason": (
                    "a model request was started with no durable response: its outcome and cost are "
                    "unknown. It is not repeated automatically; retry requires explicit authorization"
                )
            },
        )
        return _emit_and_report(flow, td, args)

    if proj.changeset is None:
        flow.append(
            EventKind.INFRASTRUCTURE_BLOCKED,
            {
                "reason": (
                    "the task was interrupted before a candidate patch was registered; no proposal "
                    "is durable, so there is nothing to gate and nothing is re-requested"
                )
            },
        )
        return _emit_and_report(flow, td, args)

    node_id = contract.request.removeprefix("fix ").strip()
    gate_req = VerifyRequest(
        repo_root=Path(contract.repo_root),
        diff_path=changeset_record(td),
        node_id=node_id,
        preserve=tuple(c.node_id for c in (proj.checkset.by_type(ClaimType.PRESERVATION) if proj.checkset else ())),
        budgets=contract.budgets,
        allow_partial=contract.authorities.partial_sandbox != "none",
        require_full=False,
        allow_network=contract.allow_network,
        task_id=proj.task_id,
    )
    code = run_gate(flow, gate_req, td)
    if args.json:
        print(canonical(flow.projection().receipt or {}))
    return code


def cmd_replay(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    td = task_dir(repo_root, args.task_id)
    path = td / "ledger.jsonl"
    if not path.is_file():
        print(f"error: no ledger for task {args.task_id}", file=sys.stderr)
        return EXIT_USAGE
    events, _ = read_events(path)
    sys.stdout.write(render_settled(events))
    return EXIT_VERIFIED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rift",
        description="Counterfactual verification for Python/pytest repositories. "
        "`verify` is model-free by construction. `why` runs model-free unless a "
        "provider is configured, and then uses it only to propose additional measurements.",
    )
    parser.add_argument("--repo", default=".", help="repository root (default: cwd)")
    parser.add_argument("--json", action="store_true", help="print the receipt as canonical JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("verify", help="gate an external diff against a failing pytest node")
    v.add_argument("diff", help="path to a unified diff")
    v.add_argument("test", help="pytest node id that must fail before the diff and pass after it")
    v.add_argument(
        "--preserve",
        action="append",
        metavar="NODE",
        help="pytest node id that must pass both before and after (repeatable). "
        "If none are given the receipt says so; nothing is inferred.",
    )
    v.add_argument(
        "--precondition",
        action="append",
        metavar="NODE",
        help="run this pytest node before the target in every gate phase; repeatable, order preserved. "
        "An order-dependent failure passes alone, so without this its baseline can never reproduce.",
    )
    v.add_argument(
        "--expect-signature",
        default="",
        metavar="PATTERN",
        help="require the baseline to reproduce this signature, as 'ExceptionType: message' or 'ExceptionType'. "
        "Omitted, the observed target-specific signature is frozen instead.",
    )
    v.add_argument("--max-commands", type=int, default=200)
    v.add_argument("--max-seconds", type=float, default=1800.0)
    v.add_argument("--timeout", type=float, default=600.0, help="per-command timeout in seconds")
    v.add_argument("--yes", action="store_true", help="pre-approve a Spec Card. Never grants isolation authority.")
    v.add_argument(
        "--allow-partial-sandbox",
        action="store_true",
        help="authorise executing repository code under partial isolation",
    )
    v.add_argument(
        "--require-full-sandbox", action="store_true", help="refuse to run unless full isolation is available"
    )
    v.add_argument("--allow-network", action="store_true", help="permit network access inside a full sandbox")
    v.set_defaults(func=cmd_verify)

    w = sub.add_parser("why", help="diagnose a failing pytest node by experiment")
    w.add_argument("test", help="pytest node id that currently fails")
    w.add_argument("--max-commands", type=int, default=60)
    w.add_argument("--max-seconds", type=float, default=1800.0)
    w.add_argument("--timeout", type=float, default=600.0, help="per-command timeout in seconds")
    w.add_argument("--max-probes", type=int, default=8, help="upper bound on experiments")
    w.add_argument(
        "--no-model",
        action="store_true",
        help="skip the optional propose_handles and propose_hypotheses requests, and diagnose "
        "from discovered handles and the enumerated theory space only",
    )
    w.add_argument("--max-usd", type=float, default=0.50, help="cumulative USD authorization for the scope")
    w.add_argument("--scope", default="", help="frozen authorization scope shared by every task in a run")
    w.add_argument("--price-input", type=float, default=1.0, help="USD per 1M input tokens (frozen, configured)")
    w.add_argument("--price-output", type=float, default=5.0, help="USD per 1M output tokens")
    w.add_argument("--yes", action="store_true", help="accepted for interface stability; grants no authority here")
    w.add_argument(
        "--allow-partial-sandbox",
        action="store_true",
        help="authorise executing repository code under partial isolation",
    )
    w.add_argument(
        "--require-full-sandbox", action="store_true", help="refuse to run unless full isolation is available"
    )
    w.add_argument("--allow-network", action="store_true", help="permit network access inside a full sandbox")
    w.set_defaults(func=cmd_why)

    f = sub.add_parser("fix", help="diagnose a failing pytest node, propose a change, and gate it")
    f.add_argument("test", help="pytest node id that currently fails")
    f.add_argument("--preserve", action="append", metavar="NODE", help="node that must pass before and after")
    f.add_argument("--max-commands", type=int, default=200)
    f.add_argument("--max-seconds", type=float, default=1800.0)
    f.add_argument("--timeout", type=float, default=600.0)
    f.add_argument("--max-probes", type=int, default=8)
    f.add_argument(
        "--probe-policy",
        choices=("disagreement", "random"),
        default="disagreement",
        help="how the next experiment is chosen from the identical candidate pool (DAR-015). "
        "The default is the shipped behaviour; 'random' is BM-06 arm B.",
    )
    f.add_argument(
        "--probe-seed",
        type=int,
        default=None,
        help="required with --probe-policy random; recorded durably so a rerun is the same experiment",
    )
    f.add_argument(
        "--precondition",
        action="append",
        metavar="NODE",
        help="run this pytest node before the target in every phase; repeatable. Required for an order-dependent "
        "task, whose target passes when run alone.",
    )
    f.add_argument(
        "--expect-signature",
        default="",
        metavar="PATTERN",
        help="require the reproduction to show this signature, as 'ExceptionType: message' or 'ExceptionType'",
    )
    f.add_argument(
        "--model-alone",
        action="store_true",
        help="BM-06 arm A: propose with the same model and context but no diagnosis or probing, accepted when "
        "the target passes. Never emits a RIFT verification verdict.",
    )
    f.add_argument(
        "--no-model",
        action="store_true",
        help="run diagnosis deterministically; a source patch then abstains as model_unavailable",
    )
    f.add_argument("--max-attempts", type=int, default=1, help="bounded propose_change attempts; each is charged")
    f.add_argument("--max-output-tokens", type=int, default=4000)
    f.add_argument("--max-usd", type=float, default=0.50, help="cumulative USD authorization for the scope")
    f.add_argument(
        "--scope",
        default="",
        help="frozen authorization scope, normally a run-manifest hash. Every task sharing it "
        "draws on one cumulative cap.",
    )
    f.add_argument("--price-input", type=float, default=1.0, help="USD per 1M input tokens (frozen, configured)")
    f.add_argument("--price-output", type=float, default=5.0, help="USD per 1M output tokens")
    f.add_argument("--yes", action="store_true", help="accepted for interface stability; grants no authority here")
    f.add_argument("--allow-partial-sandbox", action="store_true")
    f.add_argument("--require-full-sandbox", action="store_true")
    f.add_argument("--allow-network", action="store_true")
    f.set_defaults(func=cmd_fix)

    r = sub.add_parser("resume", help="continue an interrupted task from its ledger")
    r.add_argument("task_id", nargs="?", default=None)
    r.set_defaults(func=cmd_resume)

    p = sub.add_parser("replay", help="re-render a completed task's settled transcript from its ledger")
    p.add_argument("task_id")
    p.set_defaults(func=cmd_replay)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted; the ledger is complete up to the last durable event", file=sys.stderr)
        return EXIT_CANCELLED
    except LedgerCorrupt as exc:
        print(f"ledger corrupt: {exc}", file=sys.stderr)
        return EXIT_INFRASTRUCTURE
    except SandboxError as exc:
        print(f"infrastructure_blocked: {exc}", file=sys.stderr)
        return EXIT_INFRASTRUCTURE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
