"""POST-HOC CANONICALIZATION REPLAY — NOT AN OFFICIAL BENCHMARK RERUN.

Metrics over `canonicalization-replay.json`. Counts first, rates second, and a
rate is never printed without the counts that produced it.

BM-07 columns are mostly `N/A` by necessity rather than by choice: its runner
recorded stage *hashes* and deleted the worktree, so the raw and normalized bytes
no longer exist and its raw applicability is unknowable. Canonical applicability
is recoverable for BM-07 because its ground-truth evaluation acted on the
canonical candidate and its reason strings are retained in `results.jsonl`.
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter, defaultdict

BM08 = pathlib.Path(__file__).parent
BANNER = "POST-HOC CANONICALIZATION REPLAY — NOT AN OFFICIAL BENCHMARK RERUN"


def rate(numerator: int, denominator: int) -> str:
    return "N/A" if denominator == 0 else f"{numerator}/{denominator} = {numerator / denominator * 100:.1f}%"


def official(path: pathlib.Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[(r["case_id"], r["arm"])] = r
    return out


def canonical_applicable_from_official(records: dict) -> tuple[int, int]:
    """Canonical applicability as the benchmark itself measured it."""
    applies = nonapplies = 0
    for r in records.values():
        gt = r.get("ground_truth") or {}
        if not gt:
            continue
        if "does not apply" in (gt.get("reason") or "").lower():
            nonapplies += 1
        else:
            applies += 1
    return applies, nonapplies


def main() -> int:
    print(BANNER)
    print("=" * len(BANNER))
    blob = json.loads((BM08 / "canonicalization-replay.json").read_text(encoding="utf-8"))
    recs = blob["records"]
    off8 = official(BM08 / "results.jsonl")
    off7 = official(BM08.parent / "bm07" / "results.jsonl")

    trans = Counter(r["primary_transition"] for r in recs)
    raw_ok = sum(1 for r in recs if r["raw_apply_ok"])
    norm_ok = sum(1 for r in recs if r["normalized_apply_ok"])
    canon_ok = sum(1 for r in recs if r["canonical_apply_ok"])
    raw_fail = len(recs) - raw_ok

    print(f"\n## BM-08 stage applicability (n={len(recs)} arms with complete artifacts)")
    print(f"  raw        applicable {raw_ok:3}   non-applicable {len(recs) - raw_ok:3}")
    print(f"  normalized applicable {norm_ok:3}   non-applicable {len(recs) - norm_ok:3}")
    print(f"  canonical  applicable {canon_ok:3}   non-applicable {len(recs) - canon_ok:3}")

    print("\n## BM-08 primary transitions")
    for key in ("RESCUED", "PRESERVED", "DAMAGED", "UNRECOVERED"):
        print(f"  {key:12} {trans.get(key, 0):3}")
    print(f"\n  rescue_rate       (RESCUED / raw non-applicable)  {rate(trans.get('RESCUED', 0), raw_fail)}")
    print(f"  unrecovered_rate  (UNRECOVERED / raw non-applicable)  {rate(trans.get('UNRECOVERED', 0), raw_fail)}")
    print(f"  preservation_rate (PRESERVED / raw applicable)     {rate(trans.get('PRESERVED', 0), raw_ok)}")

    # Damage rate over "raw applicable" is vacuous here. `canonicalize_patch`
    # returns a git-parseable patch UNCHANGED — "a patch git accepts is never
    # rewritten" — so every raw-applicable candidate is byte-identical at the
    # canonical stage and was never at risk. The denominator that means
    # something is the number of raw-applicable candidates the canonicalizer
    # actually transformed, and that is zero.
    opportunities = sum(1 for r in recs if r["raw_apply_ok"] and r["raw_hash"] != r["canonical_hash"])
    print()
    print("  raw-applicable candidates              ", raw_ok)
    print("  raw-applicable candidates transformed  ", opportunities)
    print("  damage opportunities                   ", opportunities)
    print(f"  damage_rate                             {rate(trans.get('DAMAGED', 0), opportunities)}")
    print("  (the 5 PRESERVED cases are instances of the early-return guard,")
    print("   not evidence from transformed raw-applicable patches)")

    print("\n## BM-08 normalized-stage transitions")
    norm_trans = Counter(r["raw_to_normalized"] for r in recs)
    canon_trans = Counter(r["normalized_to_canonical"] for r in recs)
    print(f"  RAW FAIL -> NORMALIZED PASS   {norm_trans.get('FAIL->PASS', 0)}")
    print(f"  RAW PASS -> NORMALIZED FAIL   {norm_trans.get('PASS->FAIL', 0)}")
    print(f"  NORMALIZED FAIL -> CANONICAL PASS  {canon_trans.get('FAIL->PASS', 0)}")
    print(f"  NORMALIZED PASS -> CANONICAL FAIL  {canon_trans.get('PASS->FAIL', 0)}")

    print("\n## BM-08 git failure classes (per stage, failures only)")
    for stage in ("raw", "normalized", "canonical"):
        classes = Counter(r[f"failure_class_{stage}"] for r in recs if not r[f"{stage}_apply_ok"])
        print(f"  {stage}:")
        for name, n in classes.most_common():
            print(f"      {n:3}  {name}")
    print("\n  defect level of the 29 unrecovered canonical failures:")
    levels = Counter(r["defect_level_canonical"] for r in recs if not r["canonical_apply_ok"])
    for name, n in levels.most_common():
        print(f"      {n:3}  {name}")

    print("\n## BM-08 per-arm")
    for arm in ("A", "C"):
        rs = [r for r in recs if r["arm"] == arm]
        rf = sum(1 for r in rs if not r["raw_apply_ok"])
        ro = len(rs) - rf
        t = Counter(r["primary_transition"] for r in rs)
        print(f"  arm {arm} (n={len(rs)}): raw ok {ro}, canonical ok {sum(1 for r in rs if r['canonical_apply_ok'])}")
        print(f"      rescue      {rate(t.get('RESCUED', 0), rf)}")
        print(f"      unrecovered {rate(t.get('UNRECOVERED', 0), rf)}")
        print(f"      damage      {rate(t.get('DAMAGED', 0), ro)}")

    print("\n## BM-08 per-repository (rescued / unrecovered / damaged)")
    per = defaultdict(Counter)
    for r in recs:
        per[r["repository"]][r["primary_transition"]] += 1
    for repo in sorted(per):
        c = per[repo]
        print(
            f"  {repo:22} n={sum(c.values()):2}  rescued {c.get('RESCUED', 0):2}"
            f"  unrecovered {c.get('UNRECOVERED', 0):2}  preserved {c.get('PRESERVED', 0):2}"
            f"  damaged {c.get('DAMAGED', 0):2}"
        )

    print("\n## BM-08 diff shape, by whether the canonical patch applied")
    for label, subset in (
        ("canonical APPLIED", [r for r in recs if r["canonical_apply_ok"]]),
        ("canonical FAILED", [r for r in recs if not r["canonical_apply_ok"]]),
    ):
        if not subset:
            continue
        s = [r["canonical_shape"] for r in subset]
        n = len(s)
        print(
            f"  {label:18} n={n:2}  files {sum(x['files_touched'] for x in s) / n:.2f}"
            f"  hunks {sum(x['hunk_count'] for x in s) / n:.2f}"
            f"  +{sum(x['added_lines'] for x in s) / n:.1f}"
            f"  -{sum(x['removed_lines'] for x in s) / n:.1f}"
            f"  bytes {sum(x['patch_bytes'] for x in s) / n:.0f}"
            f"  single-file {sum(1 for x in s if x['single_file'])}"
            f"  single-hunk {sum(1 for x in s if x['single_hunk'])}"
        )

    print("\n## BM-07 vs BM-08 — side by side")
    a7, n7 = canonical_applicable_from_official(off7)
    a8, n8 = canonical_applicable_from_official(off8)
    rows = [
        ("candidates with complete raw/norm/canon bytes", "0 (not retained)", str(len(recs))),
        ("raw applicable", "N/A", str(raw_ok)),
        ("normalized applicable", "N/A", str(norm_ok)),
        ("canonical applicable", f"{a7} of {a7 + n7}", f"{a8} of {a8 + n8}"),
        ("rescued", "N/A", str(trans.get("RESCUED", 0))),
        ("rescue rate", "N/A", rate(trans.get("RESCUED", 0), raw_fail)),
        ("preserved", "N/A", str(trans.get("PRESERVED", 0))),
        ("damage opportunities", "N/A", "0"),
        ("damage rate", "N/A", "N/A (no raw-applicable patch transformed)"),
        ("unrecovered", "N/A", str(trans.get("UNRECOVERED", 0))),
        ("unrecovered rate", "N/A", rate(trans.get("UNRECOVERED", 0), raw_fail)),
    ]
    print(f"  {'metric':46} {'BM-07':20} {'BM-08':20}")
    for name, seven, eight in rows:
        print(f"  {name:46} {seven:20} {eight:20}")
    print("\n  BM-07 raw/normalized bytes were never retained: bm07_runner.py read")
    print("  them from the task directory, recorded hashes, and deleted the tree.")
    print("  Every BM-07 cell above that needs raw applicability is therefore N/A,")
    print("  not zero and not inferred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
