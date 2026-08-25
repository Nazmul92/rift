"""POST-HOC SOURCE-RECALL / PROPOSAL-FORMAT PROBE
NOT BM-08 · NOT BM-09 · NOT OFFICIAL BENCHMARK EVIDENCE · EXPLORATORY — NOT CAUSAL

Aggregate the twelve conditions. Counts first, and no effect size at all.

With one stochastic sample per condition per case there is no quantity here that
could be a format effect, so the interpretation vocabulary is deliberately
limited to four signal names and every one of them carries the sampling caveat.
The order-stratified section exists for the same reason: if every apparent
success sits in one request order, that is a confound to disclose, not a finding
to report.
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

import transactions  # noqa: E402

MANIFEST = HERE / "probe-manifest.json"
RESULTS = HERE / "probe-results.jsonl"

SIGNALS = (
    "SOURCE_RECALL_GROUNDING_SIGNAL",
    "FORMAT_CONTRIBUTION_SIGNAL",
    "MIXED",
    "INCONCLUSIVE",
)

SAMPLING_CAVEAT = (
    "One stochastic sample per condition cannot estimate a causal format effect. "
    "Repeated counterbalanced sampling would be required for that claim."
)


def measured(subset: list[dict], field: str) -> str:
    """`k of n` when the endpoint was evaluated, `N/A (not evaluated)` when not.

    The probe never invoked the oracle, so reporting `0 of 6` for truth
    correctness would state a result that was never observed.
    """
    evaluated = [r for r in subset if r.get(field) is not None]
    if not evaluated:
        return "N/A (not evaluated)"
    return f"{sum(1 for r in evaluated if r[field])} of {len(evaluated)}"


def load() -> tuple[dict, list[dict]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    results = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    return manifest, results


def interpret(u_applied: int, s_quote_valid: int, n: int) -> tuple[str, str]:
    """Four permitted signals. Never a magnitude, never a cause."""
    if s_quote_valid > u_applied and s_quote_valid >= max(2, n // 2):
        return (
            "FORMAT_CONTRIBUTION_SIGNAL",
            "The exploratory observations are consistent with a possible unified-diff "
            "format contribution, but " + SAMPLING_CAVEAT.lower(),
        )
    if s_quote_valid <= u_applied and s_quote_valid <= n // 2:
        return (
            "SOURCE_RECALL_GROUNDING_SIGNAL",
            "The observations are consistent with source-grounding/source-recall failure that "
            "persists even when the output representation requires exact source quotation.",
        )
    if s_quote_valid >= max(2, n // 2) and u_applied == 0:
        return (
            "MIXED",
            "The observations suggest that source quotation alone may not be the binding "
            "problem; edit localization or semantic correctness remains plausible.",
        )
    return ("INCONCLUSIVE", "The observations do not separate the readings.")


def main() -> int:
    manifest, results = load()
    print(manifest["label"])
    print("=" * 72)

    expected_pairs = {(e["case_id"], c) for e in manifest["cases"] for c in e["order"]}
    problems = transactions.completeness_problems(results, manifest["probe_manifest_hash"], expected_pairs)
    print(f"\n## Completeness: {len(results)} of {transactions.EXPECTED_RESULTS}")
    if problems:
        for problem in problems:
            print(f"  REJECTED  {problem}")
        print("\nNo partial aggregate interpretation is produced.")
        return 1
    print("  exactly 12/12 unique case-condition records, one manifest identity, one model")

    u = [r for r in results if r["condition"] == "U"]
    s = [r for r in results if r["condition"] == "S"]

    u_raw = sum(1 for r in u if r.get("raw_apply_ok"))
    u_canon = sum(1 for r in u if r.get("canonical_apply_ok"))
    s_quote = sum(1 for r in s if r.get("exact_source_quote_valid"))
    s_apply = sum(1 for r in s if r.get("apply_ok"))

    print("\n## Primary counts")
    print(f"  U raw applicable                 {u_raw} of {len(u)}")
    print(f"  U canonical applicable           {u_canon} of {len(u)}")
    print(f"  S exact_source_quote_valid       {s_quote} of {len(s)}")
    print(f"  S deterministic apply            {s_apply} of {len(s)}")

    print("\n## S failure classes")
    classes = Counter(r.get("status", "") for r in s if not r.get("apply_ok"))
    for name in (
        "PATH_NOT_FOUND",
        "SEARCH_TEXT_NOT_FOUND",
        "SEARCH_TEXT_AMBIGUOUS",
        "SEARCH_REGIONS_OVERLAP",
        "SCHEMA_INVALID",
    ):
        print(f"  {name:26} {classes.get(name, 0)}")

    print("\n## Secondary endpoints (n is tiny; do not overinterpret)")
    # An endpoint that was never evaluated is N/A, not 0. Printing "0 of 6" for
    # truth-correctness would read as "measured, and none were correct" when the
    # oracle was never invoked in this probe at all — the difference between an
    # observed failure and an unasked question.
    print(f"  U target pass                    {measured(u, 'target_pass')}")
    print(f"  S target pass                    {measured(s, 'target_pass')}")
    print(f"  U truth correct                  {measured(u, 'truth_correct')}")
    print(f"  S truth correct                  {measured(s, 'truth_correct')}")
    print("  the oracle was not run in this exploratory probe; truth is unmeasured")

    print("\n## Cost")
    for label, subset in (("U", u), ("S", s)):
        print(
            f"  {label}: requests {sum(r['request_count'] for r in subset)}"
            f"  in {sum(r['input_tokens'] for r in subset):,}"
            f"  out {sum(r['output_tokens'] for r in subset):,}"
            f"  ${sum(r['cost_usd'] for r in subset):.4f}"
        )
    print(f"  total requests {sum(r['request_count'] for r in results)}")
    print(f"  total spend    ${sum(r['cost_usd'] for r in results):.4f}")

    print("\n## Order-stratified (disclosure, not an estimate)")
    for label in ("U->S", "S->U"):
        subset = [r for r in results if r["order_label"] == label]
        su = [r for r in subset if r["condition"] == "U"]
        ss = [r for r in subset if r["condition"] == "S"]
        print(
            f"  {label}: U applicable {sum(1 for r in su if r.get('canonical_apply_ok'))}/{len(su)}"
            f"   S quote-valid {sum(1 for r in ss if r.get('exact_source_quote_valid'))}/{len(ss)}"
        )
    clustered = {r["order_label"] for r in s if r.get("exact_source_quote_valid")}
    if len(clustered) == 1 and s_quote:
        print(f"  CONFOUND SIGNAL: every S success sits in the {clustered.pop()} order")
    else:
        print("  successes are not confined to a single request order")
    print("  n=3 per sequence: no order effect is estimated.")

    signal, sentence = interpret(u_canon, s_quote, len(s))
    print(f"\n## Exploratory interpretation: {signal}")
    print(f"  {sentence}")
    print(f"  {SAMPLING_CAVEAT}")
    print("\n  This is a hypothesis-generating signal, not a causal finding, and it does")
    print("  not modify any BM-08 or BM-07 measured result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
