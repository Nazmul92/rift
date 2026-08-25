"""BM-08 CONTEXT-MISS ATTRIBUTION — ZERO COST, EVALUATOR-ONLY, NO PROVIDER.

Two questions, both answered from evidence that already exists:

    do historical-fix-region misses actually track BM-08 application and truth
    failures, and why did the frozen selector miss those regions?

Nothing here re-runs the selector. RIFT's `context_selected` event retains the
whole decision path — the candidates each discovery stage produced, the files
skipped and why, the per-file form chosen, and every cap in force — so the miss
can be attributed from what the selector actually did rather than from what a
different selector would do. Running a modified selector would measure a
selector BM-08 never used.

`NOT_COVERED` keeps its narrow meaning throughout: the model-visible context did
not contain the region the known historical fix touched. It never means the bug
was unsolvable, and the data contains a case that proves it — see the
truth-correct NOT_COVERED row in the output.

The historical fix stays evaluator-only. It is read here to compute attribution
and is written to no prompt, context, or model-facing artifact.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from collections import Counter

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "benchmark" / "analysis" / "source_recall_probe"))

import probe_context  # noqa: E402

BANNER = "BM-08 CONTEXT-MISS ATTRIBUTION — ZERO COST — EVALUATOR-ONLY — NOT A CAUSAL CLAIM"
BM08 = ROOT / "benchmark" / "bm08"
AUDIT = ROOT / "benchmark" / "analysis" / "fix_region_audit" / "fix-region-coverage.json"
PROBE = ROOT / "benchmark" / "analysis" / "source_recall_probe"
OUT = HERE / "context-miss-attribution.jsonl"

# Deterministic miss classes. Every one is decided by a fact in the retained
# trace; nothing is inferred from what a better selector might have found.
FIX_FILE_NOT_DISCOVERED = "FIX_FILE_NOT_DISCOVERED"
TRACEBACK_DID_NOT_REACH_FIX_FILE = "TRACEBACK_DID_NOT_REACH_FIX_FILE"
IMPORT_TRAVERSAL_LIMIT = "IMPORT_TRAVERSAL_LIMIT"
REEXPORT_HOP_LIMIT = "REEXPORT_HOP_LIMIT"
MAX_CONTEXT_FILES_CAP = "MAX_CONTEXT_FILES_CAP"
GLOBAL_CONTEXT_BUDGET = "GLOBAL_CONTEXT_BUDGET"
PER_FILE_BUDGET = "PER_FILE_BUDGET"
LARGE_DEFINITION_TRUNCATION = "LARGE_DEFINITION_TRUNCATION"
GREP_DID_NOT_FIND_FIX_REGION = "GREP_DID_NOT_FIND_FIX_REGION"
SELECTED_FILE_BUT_WRONG_REGION = "SELECTED_FILE_BUT_WRONG_REGION"
MULTI_FILE_FIX_PARTIAL = "MULTI_FILE_FIX_PARTIAL"
HISTORICAL_FIX_OUTSIDE_SELECTOR_DISCOVERY_PATH = "HISTORICAL_FIX_OUTSIDE_SELECTOR_DISCOVERY_PATH"
UNRESOLVED_FROM_RETAINED_EVIDENCE = "UNRESOLVED_FROM_RETAINED_EVIDENCE"
COVERED_NO_MISS = "COVERED_NO_MISS"

NEVER_DISCOVERED = frozenset(
    {
        FIX_FILE_NOT_DISCOVERED,
        TRACEBACK_DID_NOT_REACH_FIX_FILE,
        GREP_DID_NOT_FIND_FIX_REGION,
        HISTORICAL_FIX_OUTSIDE_SELECTOR_DISCOVERY_PATH,
    }
)
DISCOVERED_BUT_EXCLUDED = frozenset(
    {MAX_CONTEXT_FILES_CAP, GLOBAL_CONTEXT_BUDGET, IMPORT_TRAVERSAL_LIMIT, REEXPORT_HOP_LIMIT}
)
WRONG_REGION = frozenset({SELECTED_FILE_BUT_WRONG_REGION, PER_FILE_BUDGET, LARGE_DEFINITION_TRUNCATION})


class AuditIdentityError(RuntimeError):
    """The frozen coverage audit does not verify. Analysis stops."""


def audit_hash(blob: dict) -> str:
    body = {k: v for k, v in blob.items() if k != "audit_manifest_hash"}
    return hashlib.sha256((json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()


def load_audit() -> dict:
    blob = json.loads(AUDIT.read_text(encoding="utf-8"))
    observed = audit_hash(blob)
    if observed != blob.get("audit_manifest_hash"):
        raise AuditIdentityError(f"observed {observed[:16]} != recorded {str(blob.get('audit_manifest_hash'))[:16]}")
    return blob


def selector_trace(case_id: str, arm: str) -> dict | None:
    evidence = BM08 / "results-evidence" / case_id / arm
    try:
        return probe_context.recorded_context(evidence)
    except probe_context.ContextIdentityError:
        return None


def overlaps(span: list[int] | tuple[int, int], ranges: list[list[int]]) -> bool:
    return any(int(span[0]) <= int(end) and int(start) <= int(span[1]) for start, end in ranges)


def classify_file_miss(path: str, spans: list, trace: dict) -> tuple[str, str]:
    """Why this one fix file's region was not in the context."""
    stages = trace.get("stages") or {}
    selected = set(trace["files"])
    line_ranges = trace["line_ranges"]
    discovered_in = [name for name, items in stages.items() if name != "unresolved" and path in (items or [])]

    if path in selected:
        if any(overlaps(span, line_ranges.get(path, [])) for span in spans):
            return COVERED_NO_MISS, "the fix region is inside the selected spans"
        reason = (trace.get("selection_reason") or {}).get(path, "")
        if "bounded windows" in reason:
            return PER_FILE_BUDGET, f"file selected but rendered as bounded windows: {reason}"
        if "definition" in reason:
            return LARGE_DEFINITION_TRUNCATION, f"file selected as definitions only: {reason}"
        return (
            SELECTED_FILE_BUT_WRONG_REGION,
            f"file selected, region outside the chosen spans ({reason or 'form unrecorded'})",
        )

    if discovered_in:
        # Discovered by some stage and still not selected: a cap decided it.
        candidate_count = len({p for name, items in stages.items() if name != "unresolved" for p in (items or [])})
        if candidate_count > int(trace.get("cap_files") or 0):
            return (
                MAX_CONTEXT_FILES_CAP,
                f"discovered via {', '.join(sorted(discovered_in))}; "
                f"{candidate_count} candidates exceeded cap_files={trace.get('cap_files')}",
            )
        if int(trace.get("chars") or 0) >= int(trace.get("cap_chars") or 0) * 0.9:
            return (
                GLOBAL_CONTEXT_BUDGET,
                f"discovered via {', '.join(sorted(discovered_in))}; "
                f"context {trace.get('chars')} chars against cap_chars={trace.get('cap_chars')}",
            )
        return (
            IMPORT_TRAVERSAL_LIMIT if "imports" in discovered_in else REEXPORT_HOP_LIMIT,
            f"discovered via {', '.join(sorted(discovered_in))} but not selected",
        )

    # Never discovered by any stage. Say which stage was the near miss.
    if (stages.get("grep") or []) != []:
        return GREP_DID_NOT_FIND_FIX_REGION, "bounded grep ran and did not surface this file"
    traceback_files = stages.get("traceback") or []
    if traceback_files and all(f.startswith("test") or "/test" in f for f in traceback_files):
        return (
            TRACEBACK_DID_NOT_REACH_FIX_FILE,
            f"traceback reached only {traceback_files}; the fix file is not on any frame",
        )
    if not any(stages.get(name) for name in ("imports", "reexports", "used_dependencies", "grep")):
        return HISTORICAL_FIX_OUTSIDE_SELECTOR_DISCOVERY_PATH, "no discovery stage produced any candidate for this file"
    return FIX_FILE_NOT_DISCOVERED, f"absent from every discovery stage ({sorted(stages)})"


def classify_case(row: dict, trace: dict) -> dict:
    """Case-level miss class, from the per-file classes."""
    # Attribution uses code files only: the selector selects source, so a
    # changelog the historical commit also touched was never a candidate and
    # attributing a "miss" to it would describe the audit, not the selector.
    regions = {
        path: spans
        for path, spans in (row.get("fix_touched_regions") or {}).items()
        if path in set(row.get("code_only_fix_files") or [])
    }
    if not regions:
        return {
            "selector_miss_class": UNRESOLVED_FROM_RETAINED_EVIDENCE,
            "selector_miss_detail": "the historical commit touched no Python source file",
            "per_file": {},
        }

    per_file = {}
    for path, spans in sorted(regions.items()):
        klass, detail = classify_file_miss(path, spans, trace)
        per_file[path] = {"class": klass, "detail": detail}

    classes = [v["class"] for v in per_file.values()]
    missed = [c for c in classes if c != COVERED_NO_MISS]
    if not missed:
        return {
            "selector_miss_class": COVERED_NO_MISS,
            "selector_miss_detail": "every code fix region was selected",
            "per_file": per_file,
        }
    if len(per_file) > 1 and COVERED_NO_MISS in classes:
        return {
            "selector_miss_class": MULTI_FILE_FIX_PARTIAL,
            "selector_miss_detail": (
                f"{classes.count(COVERED_NO_MISS)} of {len(classes)} fix files covered; missed: {sorted(set(missed))}"
            ),
            "per_file": per_file,
        }
    dominant = Counter(missed).most_common(1)[0][0]
    return {
        "selector_miss_class": dominant,
        "selector_miss_detail": per_file[next(p for p, v in per_file.items() if v["class"] == dominant)]["detail"],
        "per_file": per_file,
    }


def arm_outcome(record: dict, replay: dict) -> dict:
    gt = record.get("ground_truth") or {}
    reason = (gt.get("reason") or "").lower()
    if not gt:
        failure = "no_candidate"
    elif gt.get("ground_truth_verdict") == "correct":
        failure = "truth_correct"
    elif "does not apply" in reason:
        failure = "non_applicable"
    elif "target does not pass" in reason or "still fails" in reason:
        failure = "target_still_fails"
    else:
        failure = "other"
    return {
        "candidate_available": bool(record.get("raw_candidate_hash")),
        "raw_apply_ok": replay.get("raw_apply_ok"),
        "canonical_apply_ok": replay.get("canonical_apply_ok"),
        "canonical_failure_class": replay.get("failure_class_canonical", ""),
        "target_pass": gt.get("ground_truth_target_result") == "pass",
        "truth_correct": gt.get("ground_truth_verdict") == "correct",
        "arm_verdict": record.get("arm_verdict", ""),
        "failure_class": failure,
    }


def main() -> int:
    print(BANNER)
    print("=" * len(BANNER))
    try:
        audit = load_audit()
    except AuditIdentityError as exc:
        print(f"BLOCKED_CONTEXT_AUDIT_IDENTITY: {exc}")
        return 2
    print(f"audit_manifest_hash verified: {audit['audit_manifest_hash']}")

    official: dict[tuple[str, str], dict] = {}
    for line in (BM08 / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            official[(record["case_id"], record["arm"])] = record
    replay = {
        (r["case_id"], r["arm"]): r
        for r in json.loads((BM08 / "canonicalization-replay.json").read_text(encoding="utf-8"))["records"]
    }
    probe_manifest = json.loads((PROBE / "probe-manifest.json").read_text(encoding="utf-8"))
    probe_cases = {c["case_id"] for c in probe_manifest["cases"]}
    probe_results: dict[tuple[str, str], dict] = {}
    for line in (PROBE / "probe-results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            probe_results[(record["case_id"], record["condition"])] = record

    rows: list[dict] = []
    missing_trace: list[str] = []
    for entry in audit["cases"]:
        case_id = entry["case_id"]
        arm_used = entry["context_arm"]
        trace = selector_trace(case_id, arm_used)
        if trace is None:
            missing_trace.append(case_id)
            continue
        attribution = (
            {"selector_miss_class": COVERED_NO_MISS, "selector_miss_detail": "coverage is COVERED", "per_file": {}}
            if entry["coverage_status"] == "COVERED"
            else classify_case(entry, trace)
        )
        klass = attribution["selector_miss_class"]
        record = {
            "case_id": case_id,
            "repository": entry["repository"],
            "baseline_tree_hash": entry["baseline_tree_hash"],
            "context_hash": entry["context_hash"],
            "coverage": entry["coverage_status"],
            "coverage_code_only": entry["code_only_coverage_status"],
            "coverage_reason": entry["coverage_reason"],
            "fix_files": entry["fix_touched_files"],
            "code_fix_files": entry.get("code_only_fix_files", []),
            "fix_regions": entry["fix_touched_regions"],
            "fix_file_selected": sorted(set(entry.get("code_only_fix_files", [])) & set(trace["files"])),
            "fix_region_selected": entry["coverage_status"] == "COVERED",
            "discovered_fix_file": sorted(
                {
                    path
                    for path in entry.get("code_only_fix_files", [])
                    for name, items in (trace.get("stages") or {}).items()
                    if name != "unresolved" and path in (items or [])
                }
            ),
            "excluded_by_budget_or_cap": klass in DISCOVERED_BUT_EXCLUDED,
            "never_discovered": klass in NEVER_DISCOVERED,
            "selected_file_wrong_region": klass in WRONG_REGION,
            "selector_miss_class": klass,
            "selector_miss_detail": attribution["selector_miss_detail"],
            "selector_per_file": attribution["per_file"],
            "selector_stages": trace.get("stages") or {},
            "selector_caps": {
                "cap_files": trace.get("cap_files"),
                "cap_chars": trace.get("cap_chars"),
                "cap_file_chars": trace.get("cap_file_chars"),
                "chars": trace.get("chars"),
                "window_radius": trace.get("window_radius"),
            },
            "selector_skipped": trace.get("skipped") or [],
            "selector_trace_hash": hashlib.sha256(
                (json.dumps(trace, indent=1, sort_keys=True) + "\n").encode("utf-8")
            ).hexdigest(),
            "arm_A": arm_outcome(official[(case_id, "A")], replay.get((case_id, "A"), {})),
            "arm_C": arm_outcome(official[(case_id, "C")], replay.get((case_id, "C"), {})),
            "probe_case": case_id in probe_cases,
        }
        if record["probe_case"]:
            u = probe_results.get((case_id, "U"), {})
            s = probe_results.get((case_id, "S"), {})
            record["probe_U_apply"] = u.get("canonical_apply_ok")
            record["probe_S_quote_valid"] = s.get("exact_source_quote_valid")
            record["probe_S_apply"] = s.get("apply_ok")
        rows.append(record)

    if missing_trace:
        print(f"BLOCKED_MISSING_SELECTOR_TRACE: {missing_trace}")
        return 3

    with OUT.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"cases analysed: {len(rows)}  -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
