"""The BM-07 paid execution runner — the command the real benchmark will use.

Everything before this file was evaluators and preflight helpers. A benchmark
whose operator has to call functions by hand between provider responses is not a
frozen benchmark, so this is the single `run` path: preflight, budget, provider,
identity, candidate pipeline, arm evaluation, same-candidate shadow, independent
truth, settlement, durable record.

Three properties it is built around.

**Nothing is re-implemented that RIFT already owns.** Arms are invoked through
the frozen `rift fix` CLI exactly as BM-06 invoked them, so the provider adapter,
the reserve/request/settle spend ledger, schema-repair policy, model-response
evidence and the raw -> normalized -> canonical candidate pipeline are the
shipped ones. This runner orchestrates; it does not decide.

**The reservation is derived, never supplied.** A caller cannot pass zero. The
required amount comes from the manifest's own pricing and token ceilings, and a
case-arm whose reservation exceeds the remaining budget is skipped without an
adapter call.

**Identity is checked against the artifacts, not the intent.** Runtime, driver,
runner, oracle and manifest hashes must all match before a single request; they
are checked again before scoring; and the model that answered is read from the
task ledger's response evidence rather than from configuration. `runner_hash` is
this file's own bytes — the program that decides when to spend is frozen on the
same terms as the code it orchestrates.

No provider call is made unless a real `RIFT_LLM_URL` is configured. The
fake-provider dry run points that at loopback and spends nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
if str(Path(__file__).parent) not in sys.path:
    sys.path.append(str(Path(__file__).parent))

import bm07_driver as driver  # noqa: E402
import bm07_oracle as oracle  # noqa: E402

from riftagent.records import content_hash  # noqa: E402
from riftagent.sandbox import tree_hash  # noqa: E402

ARMS = ("A", "B", "C")
# The frozen official shape of BM-07: every manifest case against every arm.
# Nothing derives an official score from a different set.
OFFICIAL_ARMS = ("A", "B", "C")

NOT_STARTED = "not_started"
REQUEST_STARTED = "request_started"
COMPLETED = "completed"
BLOCKED = "blocked"
TERMINAL_STATES = (COMPLETED, BLOCKED)
WEAK_ACCEPT_VERDICT = "accepted_by_target_pass"
GATE_ACCEPT_VERDICT = "verified_against_approved_checks"

# Run outcomes. These name what the *runner* did; they are not benchmark
# outcome categories and never enter a score.
OFFICIAL_COMPLETE = "OFFICIAL_COMPLETE"
INCOMPLETE_RUN = "INCOMPLETE_RUN"
INVALID_RUN = "INVALID_RUN"
DEVELOPMENT_PARTIAL_RUN = "DEVELOPMENT_PARTIAL_RUN"
BLOCKED_FOR_RECONCILIATION = "BLOCKED_FOR_RECONCILIATION"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_RECONCILE = 2
EXIT_NO_OFFICIAL_SCORE = 3


# ------------------------------------------------------------- reservation


def required_reservation(manifest: dict) -> float:
    """The cost this runner must have in hand before one case-arm may call out.

    Derived from the manifest's own pricing and ceilings — never taken from a
    caller — so a `reserve_usd=0` argument cannot exist to be passed. The figure
    is the worst case the authorised request could cost, plus the per-arm cap the
    manifest already declares, whichever is larger.
    """
    pricing = manifest["pricing"]
    ceilings = manifest["budget"]
    worst_input = ceilings["max_input_tokens"] / 1_000_000 * pricing["input_per_mtok"]
    worst_output = ceilings["max_output_tokens"] / 1_000_000 * pricing["output_per_mtok"]
    attempts = max(1, int(ceilings.get("max_attempts", 1)))
    # A schema repair is one extra bounded request under the frozen policy.
    worst = (worst_input + worst_output) * attempts * 2
    return round(max(worst, float(ceilings["per_case_arm_max_usd"])), 6)


# ------------------------------------------------------------- identity


def runner_hash() -> str:
    """This file's own bytes — the orchestration program's identity.

    Kept separate from `driver_hash` on purpose. The driver evaluates a
    candidate; this file decides *when a provider call happens*, how a restart
    behaves, and when a score may be produced. Those are different authorities
    that fail differently, so they are audited separately rather than rolled into
    one dependency hash.

    Exact file bytes, not a normalised or import-derived digest: what runs is
    what is hashed.
    """
    return content_hash(Path(__file__).read_bytes())


def identity_problems(manifest: dict) -> list[str]:
    """Everything that must match before any money is committed."""
    problems = driver.validate_manifest(manifest)
    observed_runtime = driver.observed_runtime_hash()
    if manifest["runtime_hash"] != observed_runtime:
        problems.append(f"runtime identity: manifest != observed ({observed_runtime[:12]})")
    if manifest["driver_hash"] != driver.driver_hash():
        problems.append("driver identity: manifest driver_hash != observed")
    if manifest.get("runner_hash") != runner_hash():
        problems.append("runner identity: manifest runner_hash != observed orchestration bytes")
    if manifest.get("oracle_hash") != oracle.oracle_hash():
        problems.append("oracle identity: manifest oracle_hash != observed")
    if manifest.get("manifest_hash") and manifest["manifest_hash"] != driver.manifest_hash(manifest):
        problems.append("manifest identity: recorded manifest_hash != recomputed")
    return problems


def model_evidence(task_dir: Path) -> tuple[list[str], list[str]]:
    """Provider-reported model identities, read from the response evidence.

    Configuration says what was asked for. `MODEL_RESPONSE_RECEIVED` says what
    answered, and that is the only thing that counts — pricing metadata and the
    request body are not proof. Every response is returned, schema-repair
    responses included, because one repair answered by a different model is the
    same defect as the first response being wrong.
    """
    ledger = task_dir / "ledger.jsonl"
    if not ledger.is_file():
        return [], ["no task ledger; provider-reported model identity unavailable"]
    reported: list[str] = []
    problems: list[str] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("kind") != "model_response_received":
            continue
        name = (event.get("payload") or {}).get("model_reported")
        if not name:
            problems.append("a model response carries no provider-reported model identity")
        else:
            reported.append(name)
    if not reported and not problems:
        problems.append("no model response was recorded")
    return reported, problems


def ledger_usage(task_dir: Path) -> dict:
    """Token usage and command counts, from the task ledger rather than a receipt.

    The receipt summarises; the ledger records. Every `MODEL_RESPONSE_RECEIVED`
    is summed, so a schema-repair response is counted rather than silently
    dropped, and `commands` comes from the execution events the runtime already
    appends. Absent usage is reported as unavailable — never written as zero,
    which would read as "free" instead of "unmeasured".
    """
    ledger = task_dir / "ledger.jsonl"
    out: dict = {
        "input_tokens": None,
        "output_tokens": None,
        "request_count": 0,
        "commands": None,
        "usage_available": False,
    }
    if not ledger.is_file():
        return out
    inputs = outputs = 0
    seen_usage = False
    commands = 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        kind = event.get("kind")
        payload = event.get("payload") or {}
        if kind == "command_finished":
            commands += 1
        elif kind == "model_response_received":
            out["request_count"] += 1
            usage = payload.get("usage") or {}
            if usage:
                seen_usage = True
                inputs += int(usage.get("input_tokens") or usage.get("input") or 0)
                outputs += int(usage.get("output_tokens") or usage.get("output") or 0)
    out["commands"] = commands
    if seen_usage:
        out.update({"input_tokens": inputs, "output_tokens": outputs, "usage_available": True})
    return out


# --------------------------------------------------------------- records


@dataclass
class ArmRecord:
    benchmark_id: str
    case_id: str
    arm: str
    runtime_hash: str
    driver_hash: str
    runner_hash: str
    oracle_hash: str
    manifest_hash: str
    baseline_tree_hash: str
    requested_model: str
    provider_reported_model: list[str] = field(default_factory=list)
    identity_problems: list[str] = field(default_factory=list)
    raw_candidate_hash: str = ""
    normalized_candidate_hash: str = ""
    canonical_candidate_hash: str = ""
    arm_verdict: str = ""
    arm_receipt: dict = field(default_factory=dict)
    weak_verdict: str = ""
    strong_verdict: str = ""
    strong_gate_receipt: dict = field(default_factory=dict)
    ground_truth: dict = field(default_factory=dict)
    weak_candidate_hash: str = ""
    strong_candidate_hash: str = ""
    truth_candidate_hash: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_count: int = 0
    commands: int | None = None
    usage_available: bool = False
    probe_seed: int | None = None
    reserved_usd: float = 0.0
    actual_usd: float = 0.0
    wall_seconds: float = 0.0
    classification: str = ""
    status: str = "completed"
    request_started: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def state_path(results: Path) -> Path:
    """Durable arm state, outside the disposable worktrees.

    The results file alone cannot prevent double spending: a crash between
    sending a request and writing the result leaves no trace, and a restart that
    only skips *completed* rows would send the request again. This records
    `request_started` **before** the adapter is invoked, so a restart can tell
    "never asked" from "asked, outcome unknown" — and refuse to guess.
    """
    return results.with_name(results.stem + "-state.jsonl")


def load_states(results: Path) -> dict[tuple[str, str], str]:
    """The last durable state per case-arm, replayed from the append-only log."""
    path = state_path(results)
    states: dict[tuple[str, str], str] = {}
    if not path.is_file():
        return states
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        states[(row["case_id"], row["arm"])] = row["state"]
    return states


def write_state(results: Path, case_id: str, arm: str, state: str, detail: str = "") -> None:
    """Append one durable state transition, stamped with the runner that made it.

    The stamp is evidence for whoever reconciles an unsettled request — it says
    which orchestration program started it. It is not a new transition: the state
    machine is unchanged, and a row written before this field existed still
    replays. A restart under a different runner is already refused by the
    run-level identity check, which runs before the reconciliation scan and long
    before any request.
    """
    path = state_path(results)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"case_id": case_id, "arm": arm, "state": state, "detail": detail, "runner_hash": runner_hash()}
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def resume_decision(state: str | None) -> tuple[bool, str]:
    """Whether a case-arm may run now, and why not if it may not.

    `request_started` without a terminal state means a request may already have
    been sent and paid for. Re-sending would double-spend on evidence we do not
    have, so the run stops for reconciliation rather than assuming the earlier
    attempt failed harmlessly.
    """
    if state in (None, NOT_STARTED):
        return True, ""
    if state == COMPLETED:
        return False, "already completed"
    if state == BLOCKED:
        return False, "previously blocked; will not automatically re-spend"
    return False, (
        "a request was started and never settled; a provider call may already have been paid for. "
        "Reconcile the ledger manually before re-running this case-arm."
    )


def unreconciled(results: Path) -> list[tuple[str, str]]:
    """Case-arms whose last durable state says a request may already be paid for.

    `request_started` with no terminal state is the one thing this harness cannot
    resolve by itself: the money may or may not be gone, and nothing on disk can
    say which. The whole run stops on the first such row — not just that arm —
    because continuing would add fresh spend on top of spend of unknown size.

    Every arm in the log is scanned, including arms this invocation was not asked
    to run: `--arms A` must not tiptoe past an unsettled request in arm C.
    """
    return sorted(key for key, state in load_states(results).items() if state == REQUEST_STARTED)


def terminal_state(record: ArmRecord) -> str:
    """The durable state an arm earns once its evidence is on disk."""
    return COMPLETED if record.status == "completed" else BLOCKED


def _after_result_persisted(record: ArmRecord) -> None:
    """Seam between durable evidence and the terminal state marker.

    Present so a test can crash exactly in that window and prove the resulting
    on-disk shape — result present, state still `request_started` — is the
    fail-closed one. It does nothing in a real run.
    """


def read_rows(results: Path) -> tuple[list[dict], list[str]]:
    """Result records, plus anything unreadable — never silently skipped."""
    rows: list[dict] = []
    problems: list[str] = []
    if not results.is_file():
        return rows, problems
    for line in results.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            problems.append("an unreadable result row was found")
    return rows, problems


def expected_pairs(manifest: dict) -> set[tuple[str, str]]:
    """The frozen official evidence set: every manifest case x every official arm."""
    return {(case["case_id"], arm) for case in manifest["cases"] for arm in OFFICIAL_ARMS}


def official_status(results: Path, manifest: dict, arms: tuple[str, ...] = OFFICIAL_ARMS) -> tuple[str, list[str]]:
    """Whether this evidence set may produce an official BM-07 score.

    Official BM-07 is frozen as six cases by three arms. Anything else — a
    missing arm, a duplicate pair, a case that is not in the manifest, a terminal
    state whose result never landed, a result whose arm was never marked
    terminal — is a different experiment, and averaging it would report a number
    for a benchmark that was not run.

    A deliberately partial development run is separated from a damaged one by
    name only. Both refuse to score.
    """
    expected = expected_pairs(manifest)
    known_cases = {case["case_id"] for case in manifest["cases"]}
    rows, problems = read_rows(results)
    states = load_states(results)

    seen: dict[tuple[str, str], list[dict]] = {}
    invalid = False
    for row in rows:
        case_id, arm = row.get("case_id", ""), row.get("arm", "")
        if case_id not in known_cases:
            problems.append(f"result for {case_id!r} which is not a manifest case")
            invalid = True
            continue
        if arm not in OFFICIAL_ARMS:
            problems.append(f"{case_id}: result for arm {arm!r} which is not an official arm")
            invalid = True
            continue
        seen.setdefault((case_id, arm), []).append(row)

    for key, group in sorted(seen.items()):
        if len(group) > 1:
            problems.append(f"{key[0]}/{key[1]}: {len(group)} result records for one case-arm")
        state = states.get(key)
        if state not in TERMINAL_STATES:
            problems.append(f"{key[0]}/{key[1]}: result exists but durable state is {state or NOT_STARTED!r}")
            continue
        for row in group:
            if (row.get("status") == "completed") != (state == COMPLETED):
                problems.append(f"{key[0]}/{key[1]}: status {row.get('status')!r} is incompatible with state {state!r}")

    for key, state in sorted(states.items()):
        if state in TERMINAL_STATES and key in expected and key not in seen:
            problems.append(f"{key[0]}/{key[1]}: durable state {state!r} but no result record")

    missing = sorted(expected - set(seen))
    for case_id, arm in missing:
        problems.append(f"{case_id}/{arm}: no result record")

    if invalid:
        return INVALID_RUN, problems
    if problems:
        # A development run is only "partial" if the *sole* thing wrong with it
        # is the arms it was never asked to run. A restricted arm set does not
        # excuse a duplicate, a mismatched state or a missing requested arm.
        requested = {pair for pair in expected if pair[1] in arms}
        partial = tuple(arms) != OFFICIAL_ARMS and set(seen) == requested and len(problems) == len(missing)
        return (DEVELOPMENT_PARTIAL_RUN if partial else INCOMPLETE_RUN), problems
    return OFFICIAL_COMPLETE, problems


def completed_keys(results: Path) -> set[tuple[str, str]]:
    """Case-arms already recorded. A crash must not re-spend on them."""
    if not results.is_file():
        return set()
    done = set()
    for line in results.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("status") == "completed":
            done.add((row["case_id"], row["arm"]))
    return done


def append_record(results: Path, record: ArmRecord) -> None:
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ------------------------------------------------------------ arm command


def frozen_signature(case: dict) -> str:
    """The frozen failure identity in RIFT's own `--expect-signature` form.

    Curation captured it through `checks.run_check`, preflight compares it with
    `Signature.matches`, and the paid arm must enforce the same thing. Passing
    only the exception type would accept a different failure with the same class,
    which is exactly the confusion the signature exists to prevent.
    """
    identity = case["failure_identity"]
    message = identity.get("message") or ""
    return f"{identity['exception_type']}: {message}" if message else identity["exception_type"]


def arm_argv(arm: str, case: dict, manifest: dict, tree: Path, scope: str) -> list[str]:
    """One arm's command. The arms differ here and nowhere else.

    Every arm receives the identical frozen task — same target, same complete
    preservation set, same expected failure identity — because arms reproducing
    different failures are not comparable. The candidate pipeline is the CLI's
    own, so no arm can gain a serialisation advantage.
    """
    budget = manifest["budget"]
    pricing = manifest["pricing"]
    argv = [
        sys.executable,
        "-m",
        "riftagent",
        "--repo",
        str(tree),
        "--json",
        "fix",
        case["target_node"],
        "--allow-partial-sandbox",
        "--max-usd",
        str(budget["per_case_arm_max_usd"]),
        "--scope",
        scope,
        "--price-input",
        str(pricing["input_per_mtok"]),
        "--price-output",
        str(pricing["output_per_mtok"]),
        "--max-output-tokens",
        str(budget["max_output_tokens"]),
        "--max-attempts",
        str(budget.get("max_attempts", 1)),
        # The complete frozen identity, not just the exception type: the same
        # vocabulary curation observed and preflight enforced. Passing only the
        # class would accept a different failure of that class.
        "--expect-signature",
        frozen_signature(case),
    ]
    for node in case["preservation_nodes"]:
        argv += ["--preserve", node]
    if arm == "A":
        argv.append("--model-alone")
    elif arm == "B":
        # The seed is frozen in the manifest. `hash()` is randomised per process,
        # so deriving it here would have made arm B unreproducible across runs.
        argv += ["--probe-policy", "random", "--probe-seed", str(case["probe_seed"])]
    return argv


def run_arm_command(argv: list[str], tree: Path, case: dict) -> tuple[dict, subprocess.CompletedProcess]:
    env = {k: v for k, v in os.environ.items()}
    entries = [str(Path(__file__).parents[2] / "src")]
    layout = case.get("src_layout", "flat")
    if layout != "flat":
        entries.insert(0, str((tree / layout).resolve()))
    env["PYTHONPATH"] = os.pathsep.join(entries)
    proc = subprocess.run(argv, cwd=str(tree), capture_output=True, text=True, errors="replace", timeout=3600, env=env)
    receipt: dict = {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict) and "verdict" in parsed:
            receipt = parsed
            break
    return receipt, proc


def candidate_from_task(tree: Path, task_id: str) -> tuple[str, dict]:
    """The canonical candidate bytes and the pipeline hashes RIFT recorded."""
    td = tree / ".rift" / "tasks" / task_id
    canonical = td / "candidate-attempt-001" / "canonical.diff"
    if not canonical.is_file():
        return "", {}
    hashes = {}
    for stage in ("raw", "normalized", "canonical"):
        path = td / "candidate-attempt-001" / f"{stage}.diff"
        if path.is_file():
            hashes[f"{stage}_candidate_hash"] = content_hash(path.read_bytes())
    return canonical.read_text(encoding="utf-8", errors="replace"), hashes


# ------------------------------------------------------------------ run


def run_case_arm(
    case: dict, arm: str, manifest: dict, repos: Path, work: Path, spent: float, results: Path
) -> ArmRecord:
    started = time.time()
    reservation = required_reservation(manifest)
    record = ArmRecord(
        benchmark_id=manifest["benchmark_id"],
        case_id=case["case_id"],
        arm=arm,
        runtime_hash=manifest["runtime_hash"],
        driver_hash=manifest["driver_hash"],
        runner_hash=manifest["runner_hash"],
        oracle_hash=manifest["oracle_hash"],
        manifest_hash=manifest.get("manifest_hash", ""),
        baseline_tree_hash=case["baseline_tree_hash"],
        requested_model=manifest["model"]["requested_model_id"],
        reserved_usd=reservation,
        probe_seed=case.get("probe_seed") if arm == "B" else None,
    )

    remaining = manifest["budget"]["total_usd_ceiling"] - spent
    if remaining < reservation:
        record.status = "skipped_budget"
        record.detail = f"remaining ${remaining:.4f} < required reservation ${reservation:.4f}; no adapter call"
        record.wall_seconds = round(time.time() - started, 2)
        return record

    tree = work / f"{case['case_id']}-{arm}"
    driver.materialise_baseline(case, repos, tree)
    observed = tree_hash(tree)
    if observed != case["baseline_tree_hash"]:
        record.status = "blocked_baseline_identity"
        record.detail = f"baseline {observed[:12]} != manifest {case['baseline_tree_hash'][:12]}"
        shutil.rmtree(tree, ignore_errors=True)
        return record

    # Durable, before the adapter can possibly be reached. A crash after this
    # line is recoverable evidence; a crash without it would be indistinguishable
    # from never having asked. The terminal state is *not* written here: the
    # caller writes it only once this arm's result record is durable.
    write_state(results, case["case_id"], arm, REQUEST_STARTED)
    record.request_started = True

    scope = f"bm07-{case['case_id']}-{arm}"
    receipt, proc = run_arm_command(arm_argv(arm, case, manifest, tree, scope), tree, case)
    record.arm_receipt = {
        "verdict": receipt.get("verdict", "<no receipt>"),
        "exit_code": proc.returncode,
        "seconds": receipt.get("seconds"),
    }
    record.arm_verdict = receipt.get("verdict", "<no receipt>")
    task_id = receipt.get("task_id") or ""
    candidate = ""

    if task_id:
        td = tree / ".rift" / "tasks" / task_id
        reported, problems = model_evidence(td)
        record.provider_reported_model = reported
        wanted = manifest["model"]["requested_model_id"]
        problems += [f"provider reported {name!r}, manifest requires {wanted!r}" for name in reported if name != wanted]
        record.identity_problems = problems

        usage = ledger_usage(td)
        record.input_tokens = usage["input_tokens"]
        record.output_tokens = usage["output_tokens"]
        record.request_count = usage["request_count"]
        record.commands = usage["commands"]
        record.usage_available = usage["usage_available"]
        record.actual_usd = float((receipt.get("spend") or {}).get("charged_usd") or 0.0)

        candidate, hashes = candidate_from_task(tree, task_id)
        record.raw_candidate_hash = hashes.get("raw_candidate_hash", "")
        record.normalized_candidate_hash = hashes.get("normalized_candidate_hash", "")
        record.canonical_candidate_hash = hashes.get("canonical_candidate_hash", "")
    else:
        record.identity_problems = ["no task id in the receipt; provider evidence unavailable"]

    if record.identity_problems:
        # Money may already have been spent on this response. The arm is blocked
        # durably rather than retried, and the spend is recorded.
        record.status = "blocked_model_identity"
        record.detail = "; ".join(record.identity_problems)[:300]
        shutil.rmtree(tree, ignore_errors=True)
        record.wall_seconds = round(time.time() - started, 2)
        return record

    if candidate:
        # Every arm's candidate is scored by the independent oracle, so the
        # secondary A/B/C correctness figures are not protocol-relative — "C
        # accepted it" is not evidence that C was right.
        if arm == "A":
            verdicts = driver.evaluate_candidate(case, candidate, repos, work)
            record.weak_verdict = verdicts.weak_verdict
            record.strong_verdict = verdicts.strong_verdict
            record.strong_gate_receipt = verdicts.strong_gate_receipt
            record.ground_truth = verdicts.ground_truth
            record.weak_candidate_hash = verdicts.weak_candidate_hash
            record.strong_candidate_hash = verdicts.strong_candidate_hash
            record.truth_candidate_hash = verdicts.truth_candidate_hash
            record.classification = verdicts.classification
            bound = (
                record.weak_candidate_hash
                == record.strong_candidate_hash
                == record.truth_candidate_hash
                == record.canonical_candidate_hash
            )
        else:
            truth_tree = work / f"{case['case_id']}-{arm}-truth"
            driver.materialise_baseline(case, repos, truth_tree)
            if tree_hash(truth_tree) != case["baseline_tree_hash"]:
                record.status = "blocked_baseline_identity"
                record.detail = "truth baseline identity differs from the manifest"
                shutil.rmtree(truth_tree, ignore_errors=True)
                shutil.rmtree(tree, ignore_errors=True)
                return record
            record.ground_truth = oracle.evaluate(truth_tree, candidate, case).to_dict()
            record.truth_candidate_hash = content_hash(candidate.encode("utf-8"))
            shutil.rmtree(truth_tree, ignore_errors=True)
            bound = record.truth_candidate_hash == record.canonical_candidate_hash

        if not bound:
            record.status = "blocked_candidate_identity"
            record.detail = "an evaluator did not judge the canonical candidate bytes"

    shutil.rmtree(tree, ignore_errors=True)
    record.wall_seconds = round(time.time() - started, 2)
    return record


def aggregate(results: Path, manifest: dict) -> tuple[dict, list[str]]:
    """Summarise, refusing to mix evidence from different runs.

    Every record must carry the same benchmark, manifest, runtime, driver and
    oracle identity as the manifest being scored. A summary computed across two
    different harnesses is not a result, and silently averaging them would be the
    most expensive kind of quiet mistake.
    """
    problems: list[str] = []
    if oracle.oracle_hash() != manifest["oracle_hash"]:
        problems.append("the oracle changed after execution")
    if driver.driver_hash() != manifest["driver_hash"]:
        problems.append("the driver changed after execution")
    if runner_hash() != manifest.get("runner_hash"):
        problems.append("the orchestration program changed after execution")
    if driver.observed_runtime_hash() != manifest["runtime_hash"]:
        problems.append("the runtime changed after execution")
    if manifest.get("manifest_hash") and manifest["manifest_hash"] != driver.manifest_hash(manifest):
        problems.append("the manifest no longer hashes to its recorded identity")

    rows = []
    if results.is_file():
        for line in results.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                problems.append("an unreadable result row was found")
    for row in rows:
        for field_name, expected in (
            ("benchmark_id", manifest["benchmark_id"]),
            ("manifest_hash", manifest.get("manifest_hash", "")),
            ("runtime_hash", manifest["runtime_hash"]),
            ("driver_hash", manifest["driver_hash"]),
            ("runner_hash", manifest.get("runner_hash", "")),
            ("oracle_hash", manifest["oracle_hash"]),
        ):
            if row.get(field_name) != expected:
                problems.append(f"{row.get('case_id')}/{row.get('arm')}: {field_name} does not match this run")

    summary = {
        "arm_records": len(rows),
        "by_classification": {},
        "truth_correct_by_arm": {},
        "spend_usd": round(sum(float(r.get("actual_usd") or 0.0) for r in rows), 6),
    }
    for row in rows:
        label = row.get("classification") or ""
        if label:
            summary["by_classification"][label] = summary["by_classification"].get(label, 0) + 1
        arm = row.get("arm")
        truth = (row.get("ground_truth") or {}).get("ground_truth_verdict")
        if arm and truth:
            key = f"{arm}:{truth}"
            summary["truth_correct_by_arm"][key] = summary["truth_correct_by_arm"].get(key, 0) + 1
    return summary, problems


def run(
    manifest_path: Path,
    repos: Path,
    work: Path,
    results: Path,
    arms: tuple[str, ...] = ARMS,
) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    problems = identity_problems(manifest)
    configured = os.environ.get("RIFT_LLM_MODEL", "")
    wanted = manifest["model"]["requested_model_id"]
    if configured != wanted:
        # Checked before spending, not only after: catching a wrong model from
        # the response evidence alone means catching it with the money gone.
        problems.append(f"configured RIFT_LLM_MODEL {configured!r} != manifest {wanted!r}")
    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
        print("identity preflight failed; no provider call was made")
        return EXIT_REFUSED
    print("identity         : runtime, driver, runner, oracle, manifest and configured model all match")

    # Before preflight, before the loop, before anything that could reach an
    # adapter. An unsettled request means prior spend of unknown size, so the
    # whole benchmark stops rather than adding known spend on top of it.
    pending = unreconciled(results)
    if pending:
        for case_id, arm in pending:
            print(f"  FAIL  {case_id}/{arm}: request started, never settled; spend is unknown")
        print(f"{BLOCKED_FOR_RECONCILIATION}: {len(pending)} case-arm(s) need manual reconciliation")
        print("provider calls in this invocation: 0. No case-arm was executed.")
        return EXIT_RECONCILE

    # Every case, before the first request. Discovering case 5 is invalid after
    # paying for cases 1-4 is not a preflight, it is a receipt.
    failures = driver.preflight(manifest, repos, work)
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        print(f"six-case preflight failed ({len(failures)}); no provider call was made")
        return EXIT_REFUSED
    print(f"preflight        : {len(manifest['cases'])} cases reconstructed, 0 failures")
    print(f"reservation      : ${required_reservation(manifest):.4f} per case-arm, derived from the manifest")

    states = load_states(results)
    spent = 0.0
    if results.is_file():
        for line in results.read_text(encoding="utf-8").splitlines():
            try:
                spent += float(json.loads(line).get("actual_usd") or 0.0)
            except Exception:
                continue

    work.mkdir(parents=True, exist_ok=True)
    for case in manifest["cases"]:
        for arm in arms:
            may_run, why = resume_decision(states.get((case["case_id"], arm)))
            if not may_run:
                print(f"  {case['case_id']:22} {arm}  skipped: {why[:90]}")
                continue
            record = run_case_arm(case, arm, manifest, repos, work, spent, results)
            spent += record.actual_usd

            # Evidence first, terminal state second. An arm marked terminal
            # before its record is durable would be skipped forever by a restart
            # that could no longer reconstruct what it decided.
            append_record(results, record)
            _after_result_persisted(record)
            if record.request_started:
                write_state(results, case["case_id"], arm, terminal_state(record), record.detail[:160])

            print(
                f"  {case['case_id']:22} {arm}  {record.status:26} "
                f"{record.arm_verdict[:28]:28} {record.classification[:30]:30} ${record.actual_usd:.4f}"
            )

    print(f"\ntotal spend      : ${spent:.4f} of ${manifest['budget']['total_usd_ceiling']:.2f}")
    print(f"records          : {results}")

    summary, drift = aggregate(results, manifest)
    if drift:
        for d in drift:
            print(f"  FAIL  {d}")
        print("NO FINAL SCORE: identity drift detected during aggregation")
        return EXIT_REFUSED

    expected = len(expected_pairs(manifest))
    status, gaps = official_status(results, manifest, tuple(arms))
    print(f"completeness     : {summary['arm_records']} of {expected} official case-arm records -> {status}")
    if status != OFFICIAL_COMPLETE:
        for gap in gaps[:12]:
            print(f"  FAIL  {gap}")
        if len(gaps) > 12:
            print(f"  ...   {len(gaps) - 12} further gaps")
        print(f"{status}: NO OFFICIAL SCORE")
        return EXIT_NO_OFFICIAL_SCORE

    print(f"summary          : {json.dumps(summary['by_classification'], sort_keys=True)}")
    print(f"truth by arm     : {json.dumps(summary['truth_correct_by_arm'], sort_keys=True)}")
    print(f"{OFFICIAL_COMPLETE}: {expected} of {expected} case-arm records with compatible terminal states")
    return EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser(prog="bm07_runner")
    ap.add_argument("command", choices=["run"])
    ap.add_argument("--manifest", default=str(Path(__file__).parent / "manifest-executable.json"))
    ap.add_argument("--repos", default="/repos")
    ap.add_argument("--work", default="/tmp/bm07-run")
    ap.add_argument("--results", default=str(Path(__file__).parent / "results.jsonl"))
    ap.add_argument("--arms", default="ABC")
    ap.add_argument(
        "--adapter",
        choices=["real", "fake"],
        default="real",
        help="'fake' requires RIFT_LLM_URL to already point at a loopback provider; the run path is identical",
    )
    args = ap.parse_args()
    if args.adapter == "fake" and "127.0.0.1" not in os.environ.get("RIFT_LLM_URL", ""):
        print("--adapter fake requires RIFT_LLM_URL pointing at a loopback provider")
        return 1
    return run(
        Path(args.manifest),
        Path(args.repos),
        Path(args.work),
        Path(args.results),
        tuple(a for a in args.arms if a in ARMS),
    )


if __name__ == "__main__":
    raise SystemExit(main())
