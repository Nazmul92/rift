"""REPRESENTATION EXPERIMENT — durable transaction discipline for 144 samples.

PREPARATION ONLY. This module never contacts a provider; the runner injects one.

Same contract BM-07 and BM-08 execute under. `REQUEST_STARTED` is fsynced before
the request leaves, because a record written afterwards cannot tell "never sent"
from "sent and lost", and those differ by real money. An unreconciled request
stops the whole study rather than its own sample.

Completeness is exact: 144 unique case-repeat-condition results, one manifest
identity, one model, a raw response for every one. No partial scientific
aggregate — a study that reports 143 of 144 is reporting a different study.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

REQUEST_STARTED = "REQUEST_STARTED"
RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
REQUEST_FAILED = "REQUEST_FAILED"
RESULT_PERSISTED = "RESULT_PERSISTED"
SAMPLE_TERMINAL = "SAMPLE_TERMINAL"

TERMINAL_FOR_REQUEST = frozenset({RESPONSE_RECEIVED, REQUEST_FAILED})
EXPECTED_RESULTS = 144

INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"


class UnreconciledRequest(RuntimeError):
    """A durable REQUEST_STARTED with no outcome. Stops the study globally."""


class ResultDurabilityError(RuntimeError):
    """Terminal state was requested before the result evidence existed."""


def implementation_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class StudyLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, kind: str, payload: dict) -> dict:
        event = {"kind": kind, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **payload}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def events(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ------------------------------------------------------------ reconciliation

    def unreconciled(self) -> list[dict]:
        started: dict[str, dict] = {}
        settled: set[str] = set()
        for event in self.events():
            if event["kind"] == REQUEST_STARTED:
                started[event["request_id"]] = event
            elif event["kind"] in TERMINAL_FOR_REQUEST:
                settled.add(event["request_id"])
        return [e for rid, e in started.items() if rid not in settled]

    def require_reconciled(self) -> None:
        outstanding = self.unreconciled()
        if outstanding:
            ids = ", ".join(sorted(e["request_id"] for e in outstanding))
            raise UnreconciledRequest(
                f"{len(outstanding)} request(s) started without a durable outcome ({ids}); "
                "the study stops globally rather than starting another provider request"
            )

    # ------------------------------------------------------------------ requests

    def start_request(
        self,
        *,
        manifest_hash: str,
        sample_id: str,
        case_id: str,
        repeat: int,
        condition: str,
        ordinal: int,
        prompt_hash: str,
        requested_model: str,
        reserved_usd: float,
    ) -> str:
        self.require_reconciled()
        request_id = hashlib.sha256(f"{manifest_hash}:{sample_id}:{ordinal}".encode()).hexdigest()[:16]
        self.append(
            REQUEST_STARTED,
            {
                "request_id": request_id,
                "representation_experiment_manifest_hash": manifest_hash,
                "sample_id": sample_id,
                "case_id": case_id,
                "repeat": repeat,
                "condition": condition,
                "ordinal": ordinal,
                "prompt_hash": prompt_hash,
                "requested_model": requested_model,
                "reserved_usd": reserved_usd,
            },
        )
        return request_id

    def response(self, request_id: str, *, reported_model: str, raw_hash: str, usage: dict, actual_usd: float) -> None:
        self.append(
            RESPONSE_RECEIVED,
            {
                "request_id": request_id,
                "reported_model": reported_model,
                "raw_response_hash": raw_hash,
                "usage": usage,
                "actual_usd": actual_usd,
            },
        )

    def failure(self, request_id: str, reason: str) -> None:
        self.append(REQUEST_FAILED, {"request_id": request_id, "reason": reason[:1000]})

    def result_persisted(self, sample_id: str, result_hash: str) -> None:
        self.append(RESULT_PERSISTED, {"sample_id": sample_id, "result_hash": result_hash})

    def sample_terminal(self, sample_id: str) -> None:
        persisted = {e["sample_id"] for e in self.events() if e["kind"] == RESULT_PERSISTED}
        if sample_id not in persisted:
            raise ResultDurabilityError(f"{sample_id}: terminal state requested before the result was persisted")
        self.append(SAMPLE_TERMINAL, {"sample_id": sample_id})


# ------------------------------------------------------------------ completeness

REQUIRED_RESULT_FIELDS = (
    "representation_experiment_manifest_hash",
    "case_id",
    "repeat",
    "pair_id",
    "condition",
    "request_position",
    "baseline_tree_hash",
    "context_hash",
    "historical_fix_region_coverage",
    "prompt_hash",
    "compiler_authority_contract_hash",
    "canonicalizer_identity",
    "execution_environment_hash",
    "requested_model",
    "reported_model",
    "raw_response_hash",
    "actual_usd",
    "input_tokens",
    "output_tokens",
    "request_count",
)


def completeness_problems(results: list[dict], manifest_hash: str, expected: set[tuple[str, int, str]]) -> list[str]:
    problems: list[str] = []
    if len(results) != EXPECTED_RESULTS:
        problems.append(f"expected exactly {EXPECTED_RESULTS} results, found {len(results)}")

    seen: set[tuple[str, int, str]] = set()
    for record in results:
        key = (record.get("case_id", ""), record.get("repeat", -1), record.get("condition", ""))
        if key in seen:
            problems.append(f"duplicate sample {key}")
        seen.add(key)
    for key in sorted(seen - expected):
        problems.append(f"unknown sample {key}")
    for key in sorted(expected - seen):
        problems.append(f"missing sample {key}")

    if {r.get("representation_experiment_manifest_hash") for r in results} - {manifest_hash}:
        problems.append("mixed representation_experiment_manifest_hash across results")

    for record in results:
        label = f"{record.get('case_id')}/{record.get('repeat')}/{record.get('condition')}"
        if record.get("requested_model") != record.get("reported_model"):
            problems.append(
                f"{label}: model identity mismatch "
                f"({record.get('requested_model')!r} vs {record.get('reported_model')!r})"
            )
        if not record.get("raw_response_hash"):
            problems.append(f"{label}: missing raw response")
        if record.get("outcome_class") == INFRASTRUCTURE_FAILURE:
            problems.append(f"{label}: infrastructure failure must be reconciled, not aggregated")
        for field in REQUIRED_RESULT_FIELDS:
            if field not in record:
                problems.append(f"{label}: missing required field {field!r}")
    # S samples must additionally carry the compiler identity they were built by.
    for record in results:
        if record.get("condition") == "S" and not record.get("compiler_hash"):
            problems.append(f"{record.get('case_id')}/{record.get('repeat')}/S: missing compiler_hash")
    return problems
