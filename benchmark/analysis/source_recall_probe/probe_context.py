"""POST-HOC SOURCE-RECALL / PROPOSAL-FORMAT PROBE
NOT BM-08 · NOT BM-09 · NOT OFFICIAL BENCHMARK EVIDENCE · EXPLORATORY — NOT CAUSAL

Reconstruct the exact governed source context BM-08 gave the model.

This is reconstruction, not re-selection. RIFT's `context_selected` ledger event
records the precise file list and per-file line ranges it chose, so the same
window can be rebuilt from the frozen baseline byte for byte. Re-running context
selection would have produced *a* context; replaying the recorded ranges produces
*the* context, which is the only version that makes the probe comparable to the
proposal it is asking about.

Nothing is added. No extra files, no wider ranges, and above all no sight of the
upstream fix — the probe asks whether the model can quote source it was shown,
so showing it the answer would destroy the question.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

BANNER = (
    "POST-HOC SOURCE-RECALL / PROPOSAL-FORMAT PROBE — NOT BM-08 — NOT BM-09 — "
    "NOT OFFICIAL BENCHMARK EVIDENCE — EXPLORATORY, NOT CAUSAL"
)


class ContextIdentityError(RuntimeError):
    """The recorded context cannot be rebuilt from the frozen baseline."""


def ledger_events(evidence_dir: Path) -> list[dict]:
    path = evidence_dir / "ledger.jsonl"
    if not path.is_file():
        raise ContextIdentityError(f"no ledger at {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def recorded_context(evidence_dir: Path) -> dict:
    """The `context_selected` event that carries files and line ranges."""
    selections = [
        e["payload"]
        for e in ledger_events(evidence_dir)
        if e["kind"] == "context_selected" and e["payload"].get("line_ranges")
    ]
    if not selections:
        raise ContextIdentityError(f"{evidence_dir}: no context_selected event with line ranges")
    return selections[-1]


def recorded_reproducer(evidence_dir: Path) -> dict:
    for event in ledger_events(evidence_dir):
        if event["kind"] == "reproducer_frozen":
            return event["payload"]
    raise ContextIdentityError(f"{evidence_dir}: no reproducer_frozen event")


def render_context(tree: Path, selection: dict) -> str:
    """The recorded window over the frozen baseline, in the recorded order."""
    parts: list[str] = []
    line_ranges = selection["line_ranges"]
    for name in selection["files"]:
        source = tree / name
        if not source.is_file():
            raise ContextIdentityError(f"{name}: recorded in the context but absent from the baseline")
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        ranges = line_ranges.get(name)
        if not ranges:
            raise ContextIdentityError(f"{name}: no recorded line range")
        for start, end in ranges:
            body = "\n".join(lines[start - 1 : end])
            parts.append(f"--- {name} (lines {start}-{end}) ---\n{body}")
    return "\n\n".join(parts) + "\n"


def context_hash(rendered: str) -> str:
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def build(tree: Path, evidence_dir: Path) -> dict:
    """Everything the probe shows the model, plus its identity."""
    selection = recorded_context(evidence_dir)
    reproducer = recorded_reproducer(evidence_dir)
    rendered = render_context(tree, selection)
    return {
        "files": list(selection["files"]),
        "line_ranges": selection["line_ranges"],
        "recorded_chars": selection.get("chars"),
        "rendered_chars": len(rendered),
        "context": rendered,
        "context_hash": context_hash(rendered),
        "node_id": reproducer["reproducer"]["node_id"],
        "expected_signature": reproducer["expected_signature"],
    }
