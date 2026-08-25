"""POST-HOC SOURCE-RECALL / PROPOSAL-FORMAT PROBE
NOT BM-08 · NOT BM-09 · NOT OFFICIAL BENCHMARK EVIDENCE · EXPLORATORY — NOT CAUSAL

The atomic search/replace executor for condition S.

The point of condition S is to ask one question — *can the model quote source
that actually exists?* — so the executor must not quietly do the model any
favours. In particular it must not be a more expressive editing language than
the unified diff it is compared against.

That is why every `search` is resolved against the **original unmodified
baseline** and never against the result of an earlier edit. A sequential editor
lets edit N reshape the text edit N+1 looks for, which is strictly more powerful
than a patch and would make condition S win for reasons that have nothing to do
with source recall. Here, validation is complete before any byte is written, and
a replacement can never manufacture the match a later edit depends on.

No fuzzy matching, no line-number locators, no wildcards, no regex. Exact
literal text, matching exactly once, in a file that already exists.

This module executes edits against a disposable tree. It calls no provider.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

BANNER = (
    "POST-HOC SOURCE-RECALL / PROPOSAL-FORMAT PROBE — NOT BM-08 — NOT BM-09 — "
    "NOT OFFICIAL BENCHMARK EVIDENCE — EXPLORATORY, NOT CAUSAL"
)

# Conservative, mutually exclusive outcome classes.
PATH_NOT_FOUND = "PATH_NOT_FOUND"
SEARCH_TEXT_NOT_FOUND = "SEARCH_TEXT_NOT_FOUND"
SEARCH_TEXT_AMBIGUOUS = "SEARCH_TEXT_AMBIGUOUS"
SEARCH_REGIONS_OVERLAP = "SEARCH_REGIONS_OVERLAP"
SCHEMA_INVALID = "SCHEMA_INVALID"
APPLIED = "APPLIED"

MAX_EDITS = 20
MAX_SEARCH_CHARS = 20000


@dataclass
class EditOutcome:
    """What happened to one proposed edit, before anything was written."""

    index: int
    path: str
    status: str
    detail: str = ""
    start: int = -1
    end: int = -1


@dataclass
class ApplyResult:
    status: str
    apply_ok: bool
    exact_source_quote_valid: bool
    outcomes: list[EditOutcome] = field(default_factory=list)
    modified_paths: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "apply_ok": self.apply_ok,
            "exact_source_quote_valid": self.exact_source_quote_valid,
            "modified_paths": self.modified_paths,
            "detail": self.detail[:2000],
            "edits": [
                {
                    "index": o.index,
                    "path": o.path,
                    "status": o.status,
                    "detail": o.detail[:400],
                    "start": o.start,
                    "end": o.end,
                }
                for o in self.outcomes
            ],
        }


def executor_hash() -> str:
    """This file's bytes. Bound into every probe result."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def validate_schema(payload: object) -> tuple[list[dict], str]:
    """Structural validation only. Returns (edits, error)."""
    if not isinstance(payload, dict):
        return [], "response is not a JSON object"
    edits = payload.get("edits")
    if not isinstance(edits, list) or not edits:
        return [], "'edits' must be a non-empty list"
    if len(edits) > MAX_EDITS:
        return [], f"'edits' exceeds {MAX_EDITS}"
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return [], f"edits[{i}] is not an object"
        for key in ("path", "search", "replace"):
            if key not in edit:
                return [], f"edits[{i}] missing {key!r}"
            if not isinstance(edit[key], str):
                return [], f"edits[{i}].{key} is not a string"
        if not edit["path"].strip():
            return [], f"edits[{i}].path is empty"
        if not edit["search"]:
            return [], f"edits[{i}].search is empty"
        if len(edit["search"]) > MAX_SEARCH_CHARS:
            return [], f"edits[{i}].search exceeds {MAX_SEARCH_CHARS} characters"
        if edit["path"].startswith("/") or ".." in Path(edit["path"]).parts:
            return [], f"edits[{i}].path escapes the repository"
    return edits, ""


def _overlaps(spans: list[tuple[int, int]]) -> bool:
    ordered = sorted(spans)
    return any(ordered[i][1] > ordered[i + 1][0] for i in range(len(ordered) - 1))


def apply_edits(tree: Path, payload: object) -> ApplyResult:
    """Validate every edit against the untouched baseline, then apply or none.

    `exact_source_quote_valid` is the probe's primary endpoint and is decided
    entirely by validation: it says the model named files that exist and quoted
    text that is really in them, exactly once, without overlapping. Whether the
    edit *fixes* anything is a different question, asked later by the oracle.
    """
    edits, error = validate_schema(payload)
    if error:
        return ApplyResult(SCHEMA_INVALID, False, False, detail=error)

    # ---- phase one: validate everything against the original bytes.
    originals: dict[str, str] = {}
    outcomes: list[EditOutcome] = []
    spans: dict[str, list[tuple[int, int]]] = {}
    failed = False

    for i, edit in enumerate(edits):
        path, search = edit["path"], edit["search"]
        target = tree / path
        if not target.is_file():
            outcomes.append(EditOutcome(i, path, PATH_NOT_FOUND, "no such file in the frozen baseline"))
            failed = True
            continue
        if path not in originals:
            originals[path] = target.read_text(encoding="utf-8", errors="replace")
        source = originals[path]

        # Counted against the ORIGINAL text, always. Never against a partially
        # edited buffer — that is the whole point of the atomic contract.
        occurrences = source.count(search)
        if occurrences == 0:
            outcomes.append(EditOutcome(i, path, SEARCH_TEXT_NOT_FOUND, "search text is not present in the baseline"))
            failed = True
            continue
        if occurrences > 1:
            outcomes.append(EditOutcome(i, path, SEARCH_TEXT_AMBIGUOUS, f"search text occurs {occurrences} times"))
            failed = True
            continue

        start = source.index(search)
        span = (start, start + len(search))
        spans.setdefault(path, []).append(span)
        outcomes.append(EditOutcome(i, path, APPLIED, "", span[0], span[1]))

    for path, path_spans in spans.items():
        if _overlaps(path_spans):
            for outcome in outcomes:
                if outcome.path == path and outcome.status == APPLIED:
                    outcome.status = SEARCH_REGIONS_OVERLAP
                    outcome.detail = "search regions overlap within one file"
            failed = True

    quote_valid = not failed
    if failed:
        # Nothing is written. One invalid edit voids the whole set.
        first = next((o for o in outcomes if o.status != APPLIED), None)
        return ApplyResult(
            first.status if first else SCHEMA_INVALID,
            False,
            False,
            outcomes=outcomes,
            detail=first.detail if first else "validation failed",
        )

    # ---- phase two: apply. Offsets came from the original, so the edits are
    # applied right-to-left per file and cannot disturb each other.
    modified: list[str] = []
    for path in spans:
        text = originals[path]
        replacements = {}
        for i, edit in enumerate(edits):
            if edit["path"] != path:
                continue
            outcome = outcomes[i]
            replacements[(outcome.start, outcome.end)] = edit["replace"]
        for (start, end), replacement in sorted(replacements.items(), reverse=True):
            text = text[:start] + replacement + text[end:]
        (tree / path).write_text(text, encoding="utf-8")
        modified.append(path)

    return ApplyResult(APPLIED, True, quote_valid, outcomes=outcomes, modified_paths=sorted(modified))


def main() -> int:  # pragma: no cover - manual inspection helper
    print(BANNER)
    print(json.dumps({"executor_hash": executor_hash()}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
