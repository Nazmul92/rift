"""BM-08 CONTEXT-MISS ATTRIBUTION — cross-tabs. ZERO COST, NO PROVIDER.

Counts first, rates second, and no significance testing: 24 cases, and the two
arms are not independent case populations, so a p-value here would be decoration
over the same 24 bugs counted twice.

Every statement this prints is descriptive. "Concentrated in" is a count;
"caused by" would be a claim this evidence cannot support.
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter, defaultdict

HERE = pathlib.Path(__file__).parent
DATA = HERE / "context-miss-attribution.jsonl"
COVERAGE = ("COVERED", "PARTIALLY_COVERED", "NOT_COVERED")


def rows() -> list[dict]:
    return [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]


def rate(numerator: int, denominator: int) -> str:
    return "N/A" if denominator == 0 else f"{numerator}/{denominator} = {numerator / denominator * 100:.0f}%"


def main() -> int:
    data = rows()
    print("BM-08 CONTEXT-MISS ATTRIBUTION — ZERO COST — DESCRIPTIVE ONLY")
    print("=" * 72)

    counts = Counter(r["coverage"] for r in data)
    print(f"\n## Coverage (n={len(data)})")
    for status in COVERAGE:
        print(f"  {status:20} {counts.get(status, 0)}")

    print("\n## 24-case coverage / outcome table")
    print(f"  {'case':28} {'coverage':18} {'A':>22}   {'C':>22}")
    for row in sorted(data, key=lambda r: (r["coverage"], r["case_id"])):
        a, c = row["arm_A"], row["arm_C"]
        print(f"  {row['case_id']:28} {row['coverage']:18} {a['failure_class']:>22}   {c['failure_class']:>22}")

    for arm in ("A", "C"):
        print(f"\n## Arm {arm} outcomes by coverage")
        print(f"  {'coverage':20} {'n':>3} {'cand':>5} {'rawOK':>6} {'canonOK':>8} {'target':>7} {'truth':>6}")
        for status in COVERAGE:
            subset = [r[f"arm_{arm}"] for r in data if r["coverage"] == status]
            if not subset:
                continue
            print(
                f"  {status:20} {len(subset):>3} "
                f"{sum(1 for s in subset if s['candidate_available']):>5} "
                f"{sum(1 for s in subset if s['raw_apply_ok']):>6} "
                f"{sum(1 for s in subset if s['canonical_apply_ok']):>8} "
                f"{sum(1 for s in subset if s['target_pass']):>7} "
                f"{sum(1 for s in subset if s['truth_correct']):>6}"
            )
        print(f"  {'-' * 60}")
        for status in COVERAGE:
            subset = [r[f"arm_{arm}"] for r in data if r["coverage"] == status]
            if not subset:
                continue
            canon = sum(1 for s in subset if s["canonical_apply_ok"])
            truth = sum(1 for s in subset if s["truth_correct"])
            print(
                f"  {status:20} canonical applicable {rate(canon, len(subset)):>16}"
                f"   truth {rate(truth, len(subset)):>16}"
            )

    print("\n## Where the canonical non-applicable candidates came from")
    for arm in ("A", "C"):
        nonapp = [r for r in data if not r[f"arm_{arm}"]["canonical_apply_ok"]]
        dist = Counter(r["coverage"] for r in nonapp)
        print(f"  arm {arm}: {len(nonapp)} non-applicable  ->  " + ", ".join(f"{s} {dist.get(s, 0)}" for s in COVERAGE))

    print("\n## Where the truth-correct fixes came from")
    for arm in ("A", "C"):
        correct = [r for r in data if r[f"arm_{arm}"]["truth_correct"]]
        dist = Counter(r["coverage"] for r in correct)
        print(f"  arm {arm}: {len(correct)} truth-correct  ->  " + ", ".join(f"{s} {dist.get(s, 0)}" for s in COVERAGE))
    print("\n  truth-correct WITHOUT the historical fix region in context:")
    any_found = False
    for row in data:
        for arm in ("A", "C"):
            if row[f"arm_{arm}"]["truth_correct"] and row["coverage"] == "NOT_COVERED":
                any_found = True
                print(f"    {row['case_id']:28} arm {arm}  — a valid repair that was not the historical one")
    if not any_found:
        print("    none")

    print("\n## Canonical failure class by coverage")
    table: dict[str, Counter] = defaultdict(Counter)
    for row in data:
        for arm in ("A", "C"):
            table[row["coverage"]][row[f"arm_{arm}"]["failure_class"]] += 1
    classes = ("truth_correct", "non_applicable", "target_still_fails", "no_candidate", "other")
    print(f"  {'coverage':20} " + " ".join(f"{c:>18}" for c in classes))
    for status in COVERAGE:
        print(f"  {status:20} " + " ".join(f"{table[status].get(c, 0):>18}" for c in classes))
    print("\n  (arms pooled here for the failure-class view only; arm tables above keep them separate)")

    print("\n## Fine-grained canonical diagnostics by coverage (replay evidence)")
    fine: dict[str, Counter] = defaultdict(Counter)
    for row in data:
        for arm in ("A", "C"):
            klass = row[f"arm_{arm}"]["canonical_failure_class"] or "applied"
            fine[row["coverage"]][klass] += 1
    seen = sorted({k for c in fine.values() for k in c})
    print(f"  {'coverage':20} " + " ".join(f"{k:>30}" for k in seen))
    for status in COVERAGE:
        print(f"  {status:20} " + " ".join(f"{fine[status].get(k, 0):>30}" for k in seen))

    print("\n## Selector miss taxonomy")
    taxonomy = Counter(r["selector_miss_class"] for r in data)
    for name, count in taxonomy.most_common():
        print(f"  {name:48} {count}")

    print("\n## Discovery failure vs budget failure")
    never = sum(1 for r in data if r["never_discovered"])
    excluded = sum(1 for r in data if r["excluded_by_budget_or_cap"])
    wrong = sum(1 for r in data if r["selected_file_wrong_region"])
    other = len(data) - never - excluded - wrong - taxonomy.get("COVERED_NO_MISS", 0)
    print(f"  never discovered                 {never}")
    print(f"  discovered but budget/cap excluded {excluded}")
    print(f"  selected file but wrong region   {wrong}")
    print(f"  covered (no miss)                {taxonomy.get('COVERED_NO_MISS', 0)}")
    print(f"  other / unresolved               {other}")

    print("\n## Per-case miss reason (PARTIALLY_COVERED and NOT_COVERED)")
    for row in sorted(data, key=lambda r: (r["coverage"], r["case_id"])):
        if row["coverage"] == "COVERED":
            continue
        print(f"  {row['case_id']:28} {row['coverage']:18} {row['selector_miss_class']:40}")
        print(f"      {row['selector_miss_detail'][:150]}")

    print("\n## Six probe cases overlay")
    print(f"  {'case':28} {'coverage':18} {'U applied':>10} {'S quote ok':>11} {'S applied':>10}")
    for row in sorted((r for r in data if r["probe_case"]), key=lambda r: r["case_id"]):
        print(
            f"  {row['case_id']:28} {row['coverage']:18} "
            f"{str(row.get('probe_U_apply')):>10} {str(row.get('probe_S_quote_valid')):>11} "
            f"{str(row.get('probe_S_apply')):>10}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
