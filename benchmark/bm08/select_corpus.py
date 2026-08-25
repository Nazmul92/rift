"""BM-08-v2 stage B: apply the ratified selection rule. No model, no network.

Executes `AMENDMENT-V2.md` in the approved order and reports what each stage
removed:

    raw mined candidates
      -> previous-exposure exclusion
      -> author date >= 2018-01-01
      -> frozen deterministic ordering
      -> near-duplicate collapse
      -> (everything surviving goes to model-free validation)

Two orderings here are load-bearing and were wrong in v1.

**Eligibility precedes deduplication.** Collapsing first lets an ineligible 2015
commit win a duplicate family; the era filter then deletes it and the eligible
2020 sibling is already gone, so the family vanishes from the benchmark
entirely. Filtering first means only eligible candidates ever compete to
represent a family.

**There is no repository cap here.** Capping before validation spends a
repository's quota on candidates that turn out to be unrunnable. The cap is
applied to *valid* survivors, in `validate_cases.py`, after reproduction.

Overlap with prior benchmarks is counted before it is filtered: "newly mined"
would otherwise quietly mean "some number of bugs this project has already
looked at", and that difference is the whole basis of BM-08's unseen-bug claim.
"""

import collections
import json
import pathlib
import sys

S = pathlib.Path("/s")
POOL = S / "bm08_pool.json"
EXCLUSIONS = S / "bm08_exclusions.json"
OUT = S / "bm08_queue.json"

# BM-08-v2: author date, `%aI`. Never committer date.
AUTHOR_DATE_FLOOR = "2018-01-01"


def eligible_by_author_date(candidate: dict) -> bool:
    """The BM-08-v2 era floor, reading `%aI` and no other field."""
    return candidate["author_date"] >= AUTHOR_DATE_FLOOR


def collapse_near_duplicates(candidates: list[dict]) -> list[dict]:
    """One case per (repository, primary source file) and (repository, primary test file).

    Unchanged from v1 in every respect except the population it receives. The
    representative is whichever eligible candidate comes first in the frozen
    deterministic order, so the caller must sort before calling.
    """
    seen_source: set[tuple[str, str]] = set()
    seen_test: set[tuple[str, str]] = set()
    kept = []
    for c in candidates:
        primary_source = sorted(c["source_files"])[0]
        primary_test = sorted(c["test_files"])[0]
        if (c["repo"], primary_source) in seen_source or (c["repo"], primary_test) in seen_test:
            continue
        seen_source.add((c["repo"], primary_source))
        seen_test.add((c["repo"], primary_test))
        kept.append(c)
    return kept


def main() -> int:
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    blob = json.loads(EXCLUSIONS.read_text(encoding="utf-8"))
    excluded = set(blob["excluded_commits"])
    named = blob["named_prior_cases"]

    print(f"raw mined candidates          : {len(pool)} from {len({c['repo'] for c in pool})} repositories")
    print(f"frozen exclusion set          : {len(excluded)} previously-seen commits")

    # ---- overlap, counted before anything is removed
    by_fix = [c for c in pool if c["fix_commit"] in excluded]
    by_parent = [c for c in pool if c["parent"] in excluded and c["fix_commit"] not in excluded]
    official = {e["fix_commit"] for e in named.get("BM-07 official", [])}
    bm06 = {e["fix_commit"] for e in named.get("BM-06 manifest", [])}
    print()
    print("OVERLAP WITH PRIOR WORK (counted before filtering)")
    print(f"  fix_commit already seen                    : {len(by_fix)}")
    print(f"  ...of which BM-07 official cases           : {len([c for c in by_fix if c['fix_commit'] in official])}")
    print(f"  ...of which BM-06 cases                    : {len([c for c in by_fix if c['fix_commit'] in bm06])}")
    print(f"  parent already seen                        : {len(by_parent)}")

    stages = [("raw mined candidates", len(pool))]

    # ---- §2 prior-exposure exclusion, conservative
    unseen = [c for c in pool if c["fix_commit"] not in excluded and c["parent"] not in excluded]
    stages.append(("after prior-exposure exclusion", len(unseen)))

    # Every BM-07 official commit must be gone, asserted rather than assumed.
    survivors = {c["fix_commit"] for c in unseen} | {c["parent"] for c in unseen}
    leaked = sorted(official & survivors)
    assert not leaked, f"BM-07 official commits survived exclusion: {leaked}"
    print(f"\n  BM-07 official commits surviving exclusion : {len(leaked)}  (must be 0)")

    # ---- §6 author-date floor, BEFORE any deduplication
    before_era = len(unseen)
    in_era = [c for c in unseen if eligible_by_author_date(c)]
    dropped_by_era = before_era - len(in_era)
    stages.append((f"after author date >= {AUTHOR_DATE_FLOOR}", len(in_era)))
    print()
    print("AUTHOR-DATE ELIGIBILITY (%aI, not %cI)")
    print(f"  candidates before author-date floor        : {before_era}")
    print(f"  excluded by author-date floor              : {dropped_by_era}")
    print(f"  candidates after author-date floor         : {len(in_era)}")
    disagree = [c for c in in_era if c["author_date"][:10] != c["committer_date"][:10]]
    print(f"  eligible whose committer date differs      : {len(disagree)} (author date governs)")

    # ---- §8 frozen deterministic ordering, established on the eligible population
    in_era.sort(key=lambda c: c["order_key"])

    # ---- §9 near-duplicate collapse, on eligible candidates only
    collapsed = collapse_near_duplicates(in_era)
    stages.append(("after near-duplicate collapse", len(collapsed)))

    # ---- §10 no repository cap here. Everything surviving is validated.
    queue = collapsed
    stages.append(("submitted to model-free validation", len(queue)))

    print()
    print("SELECTION RULE, STAGE BY STAGE")
    for label, n in stages:
        print(f"  {label:44} {n:5}")

    print()
    print(f"queue repositories : {len({c['repo'] for c in queue})}")
    print("queue by repo      :", dict(collections.Counter(c["repo"] for c in queue).most_common()))
    print("queue by category  :", dict(collections.Counter(k for c in queue for k in c["categories"]).most_common()))
    oldest = min(queue, key=lambda c: c["author_date"])["author_date"][:10] if queue else "n/a"
    print(f"oldest eligible author date : {oldest} (floor {AUTHOR_DATE_FLOOR})")

    OUT.write_text(json.dumps(queue, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwritten: {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
