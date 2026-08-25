"""POST-HOC SOURCE-RECALL / PROPOSAL-FORMAT PROBE
NOT BM-08 · NOT BM-09 · NOT OFFICIAL BENCHMARK EVIDENCE · EXPLORATORY — NOT CAUSAL

Durable transaction integrity for the probe's provider calls.

Same contract BM-07 and BM-08 execute under, in miniature: the record that a
request is about to happen is on disk *before* the request happens, so a crash
mid-flight leaves evidence that money may have been spent. The alternative — a
record written afterwards — cannot distinguish "never sent" from "sent and lost",
and those differ by real dollars.

An unreconciled `REQUEST_STARTED` stops the whole probe rather than its own
condition. A run that quietly continued past one would be reporting a completeness
count it had not earned.
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
CONDITION_TERMINAL = "CONDITION_TERMINAL"

TERMINAL_FOR_REQUEST = frozenset({RESPONSE_RECEIVED, REQUEST_FAILED})


class UnreconciledRequest(RuntimeError):
    """A durable REQUEST_STARTED with no terminal outcome. Stops everything."""


def implementation_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class ProbeLedger:
    """Append-only JSONL. Flushed and fsynced before the caller proceeds."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, kind: str, payload: dict) -> dict:
        event = {
            "kind": kind,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **payload,
        }
        line = json.dumps(event, sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def events(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ------------------------------------------------------------ reconciliation

    def unreconciled(self) -> list[dict]:
        """Every REQUEST_STARTED without a terminal event for its request id."""
        started: dict[str, dict] = {}
        settled: set[str] = set()
        for event in self.events():
            if event["kind"] == REQUEST_STARTED:
                started[event["request_id"]] = event
            elif event["kind"] in TERMINAL_FOR_REQUEST:
                settled.add(event["request_id"])
        return [event for rid, event in started.items() if rid not in settled]

    def require_reconciled(self) -> None:
        outstanding = self.unreconciled()
        if outstanding:
            ids = ", ".join(sorted(e["request_id"] for e in outstanding))
            raise UnreconciledRequest(
                f"{len(outstanding)} request(s) started without a durable outcome ({ids}); "
                "the probe stops globally rather than starting another provider request"
            )

    # ------------------------------------------------------------------ requests

    def start_request(
        self,
        *,
        probe_manifest_hash: str,
        case_id: str,
        condition: str,
        ordinal: int,
        prompt_hash: str,
        requested_model: str,
        reserved_usd: float,
    ) -> str:
        """Durable before the provider is touched. Returns the request id."""
        self.require_reconciled()
        request_id = hashlib.sha256(
            f"{probe_manifest_hash}:{case_id}:{condition}:{ordinal}:{prompt_hash}".encode()
        ).hexdigest()[:16]
        self.append(
            REQUEST_STARTED,
            {
                "request_id": request_id,
                "probe_manifest_hash": probe_manifest_hash,
                "case_id": case_id,
                "condition": condition,
                "ordinal": ordinal,
                "prompt_hash": prompt_hash,
                "requested_model": requested_model,
                "reserved_usd": reserved_usd,
            },
        )
        return request_id

    def response(self, request_id: str, *, reported_model: str, raw_hash: str, usage: dict, cost_usd: float) -> None:
        self.append(
            RESPONSE_RECEIVED,
            {
                "request_id": request_id,
                "reported_model": reported_model,
                "raw_response_hash": raw_hash,
                "usage": usage,
                "cost_usd": cost_usd,
            },
        )

    def failure(self, request_id: str, reason: str) -> None:
        self.append(REQUEST_FAILED, {"request_id": request_id, "reason": reason[:1000]})

    def result_persisted(self, case_id: str, condition: str, result_hash: str) -> None:
        self.append(RESULT_PERSISTED, {"case_id": case_id, "condition": condition, "result_hash": result_hash})

    def condition_terminal(self, case_id: str, condition: str) -> None:
        """Only legal once the result evidence is already durable."""
        persisted = {(e["case_id"], e["condition"]) for e in self.events() if e["kind"] == RESULT_PERSISTED}
        if (case_id, condition) not in persisted:
            raise RuntimeError(f"{case_id}/{condition}: terminal state requested before the result was persisted")
        self.append(CONDITION_TERMINAL, {"case_id": case_id, "condition": condition})


# ------------------------------------------------------------------ completeness

EXPECTED_RESULTS = 12


def completeness_problems(
    results: list[dict], probe_manifest_hash: str, expected_pairs: set[tuple[str, str]]
) -> list[str]:
    """Exact 12/12, one per declared pair, one identity, one model. No partials."""
    problems: list[str] = []
    pairs = [(r.get("case_id", ""), r.get("condition", "")) for r in results]

    if len(results) != EXPECTED_RESULTS:
        problems.append(f"expected exactly {EXPECTED_RESULTS} results, found {len(results)}")

    seen: set[tuple[str, str]] = set()
    for pair in pairs:
        if pair in seen:
            problems.append(f"duplicate case-condition pair {pair}")
        seen.add(pair)
    for pair in sorted(seen - expected_pairs):
        problems.append(f"unknown case-condition pair {pair}")
    for pair in sorted(expected_pairs - seen):
        problems.append(f"missing case-condition pair {pair}")

    identities = {r.get("probe_manifest_hash") for r in results}
    if identities - {probe_manifest_hash}:
        problems.append("mixed probe_manifest_hash across results")

    models = {(r.get("requested_model"), r.get("reported_model")) for r in results}
    for requested, reported in models:
        if requested != reported:
            problems.append(f"model identity mismatch: requested {requested!r}, reported {reported!r}")

    for result in results:
        if not result.get("raw_response_hash"):
            problems.append(f"{result.get('case_id')}/{result.get('condition')}: missing raw response")
    return problems
