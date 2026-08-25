"""BM-08-v2 selection rule: eligibility before collapse, cap after validation.

Two orderings in v1 were wrong in ways that silently shrink or distort the
corpus, and neither announces itself in the output — you get a smaller number
and no reason. Both are pinned here.

**Eligibility must precede deduplication.** Collapsing first lets an ineligible
2015 commit win a duplicate family; the era filter then deletes it, and the
eligible 2020 sibling was already discarded as a duplicate. The family vanishes
from the benchmark entirely, and the count just looks low.

**The repository cap must follow validation.** Capping first spends a
repository's quota on candidates that turn out to be unrunnable. A repo whose
first two candidates fail validation should contribute its third, fourth and
fifth valid ones — not be reduced to whatever survived of the first three.

The frozen minimum denominator is conjunctive and is asserted at its exact
boundaries, because "12 cases across 9 repositories" is precisely the kind of
near-miss that invites a discretionary judgement it must not receive.

No model is called and no network is used.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BENCH = Path(__file__).parents[1] / "benchmark"
if str(BENCH / "bm08") not in sys.path:
    sys.path.append(str(BENCH / "bm08"))

import select_corpus  # noqa: E402
from benchmark_modules import load  # noqa: E402

# `validate_cases` exists in both bm07 and bm08; a bare import resolves by
# whichever directory is earlier on sys.path, which collection order decides.
validate_cases = load("bm08", "validate_cases")

FLOOR = "2018-01-01"


def candidate(repo: str, sha: str, author: str, committer: str | None = None, src: str = "a.py", test: str = "t.py"):
    return {
        "repo": repo,
        "fix_commit": sha,
        "parent": sha[::-1],
        "author_date": author,
        "committer_date": committer or author,
        "source_files": [src],
        "test_files": [test],
        "order_key": sha,
        "categories": ["small"],
    }


# ------------------------------------------------ author-date eligibility


def test_the_floor_is_2018_01_01():
    assert select_corpus.AUTHOR_DATE_FLOOR == FLOOR


def test_author_date_2017_12_31_is_excluded():
    assert not select_corpus.eligible_by_author_date(candidate("r", "a", "2017-12-31T23:59:59+00:00"))


def test_author_date_2018_01_01_is_eligible():
    assert select_corpus.eligible_by_author_date(candidate("r", "a", "2018-01-01T00:00:00+00:00"))


def test_eligibility_reads_the_author_date_not_the_committer_date():
    """A rebase moves `%cI` and not `%aI`. Eligibility must not follow the rebase."""
    rebased_forward = candidate("r", "a", author="2015-06-01T00:00:00+00:00", committer="2024-01-01T00:00:00+00:00")
    assert not select_corpus.eligible_by_author_date(rebased_forward), "committer date decided eligibility"

    authored_recently = candidate("r", "b", author="2020-06-01T00:00:00+00:00", committer="2014-01-01T00:00:00+00:00")
    assert select_corpus.eligible_by_author_date(authored_recently)


def test_the_miner_records_both_dates_from_aI_and_cI():
    source = (BENCH / "bm08" / "mine_corpus.py").read_text(encoding="utf-8")
    assert "%aI%x1f%cI" in source, "the miner must capture both author and committer dates"
    assert '"author_date": author_date' in source
    assert '"committer_date": committer_date' in source


def test_the_mined_pool_carries_author_dates():
    pool = json.loads((BENCH / "bm08" / "pool.json").read_text(encoding="utf-8"))
    assert pool and all("author_date" in c and "committer_date" in c for c in pool)


# ------------------------- eligibility happens before duplicate collapse


def test_an_old_duplicate_cannot_erase_a_modern_eligible_sibling():
    """The exact failure mode: collapse-then-filter deletes the whole family.

    Both candidates are the same duplicate family (same repo, same source and
    test file). The 2015 one wins the deterministic tie. Filtering afterwards
    would remove it and leave nothing; filtering first leaves the 2020 one to
    represent the family.
    """
    old = candidate("repo", "aaa", "2015-06-01T00:00:00+00:00")  # wins ordering
    new = candidate("repo", "bbb", "2020-06-01T00:00:00+00:00")
    family = [old, new]
    assert old["order_key"] < new["order_key"], "fixture must have the old one winning the tie"

    # Correct order: eligibility, then ordering, then collapse.
    eligible = [c for c in family if select_corpus.eligible_by_author_date(c)]
    eligible.sort(key=lambda c: c["order_key"])
    kept = select_corpus.collapse_near_duplicates(eligible)

    assert [c["fix_commit"] for c in kept] == ["bbb"], "the modern eligible duplicate must survive"

    # The invalid order, shown to be the thing being prevented.
    wrong = select_corpus.collapse_near_duplicates(sorted(family, key=lambda c: c["order_key"]))
    wrong_after_filter = [c for c in wrong if select_corpus.eligible_by_author_date(c)]
    assert wrong_after_filter == [], "collapse-then-filter erases the family — this is what §7 forbids"


def test_the_selection_script_filters_before_collapsing():
    """Structural: the era floor must appear before the collapse call."""
    source = (BENCH / "bm08" / "select_corpus.py").read_text(encoding="utf-8")
    body = source.split("def main(")[1]
    assert body.index("eligible_by_author_date") < body.index("collapse_near_duplicates(")


def test_no_repository_cap_runs_before_validation():
    """§10: the whole post-collapse eligible set is validated."""
    source = (BENCH / "bm08" / "select_corpus.py").read_text(encoding="utf-8")
    assert "MAX_PER_REPO" not in source, "a pre-validation repository cap reappeared in selection"


def test_near_duplicate_semantics_are_unchanged():
    """Only the population entering collapse changed, not the relation."""
    rows = [
        candidate("r", "a1", "2020-01-01T00:00:00+00:00", src="x.py", test="tx.py"),
        candidate("r", "a2", "2020-01-01T00:00:00+00:00", src="x.py", test="ty.py"),  # same source
        candidate("r", "a3", "2020-01-01T00:00:00+00:00", src="y.py", test="tx.py"),  # same test
        candidate("r", "a4", "2020-01-01T00:00:00+00:00", src="z.py", test="tz.py"),  # distinct
        candidate("other", "a5", "2020-01-01T00:00:00+00:00", src="x.py", test="tx.py"),  # other repo
    ]
    kept = [c["fix_commit"] for c in select_corpus.collapse_near_duplicates(rows)]
    assert kept == ["a1", "a4", "a5"]


# --------------------------- repository cap applies to VALID survivors only


def valid(case_id: str, repo: str, position: int, status: str = "validated") -> dict:
    return {"case_id": case_id, "repository": repo, "queue_position": position, "curation_status": status}


def apply_cap(rows: list[dict]) -> set[str]:
    """The shipped selection, exercised exactly as `validate_cases.main` does."""
    validated = [r for r in rows if r["curation_status"] == "validated"]
    ordered = sorted(validated, key=lambda r: r.get("queue_position") or 0)
    per_repo: dict[str, int] = {}
    primary = set()
    for r in ordered:
        if per_repo.get(r["repository"], 0) >= validate_cases.MAX_PER_REPO:
            continue
        per_repo[r["repository"]] = per_repo.get(r["repository"], 0) + 1
        primary.add(r["case_id"])
    return primary


def test_invalid_candidates_do_not_consume_repository_quota():
    """§10's worked example: candidates 1 and 2 fail, so 3, 4 and 5 are taken."""
    rows = [
        valid("c1", "repo", 1, status="rejected"),
        valid("c2", "repo", 2, status="rejected"),
        valid("c3", "repo", 3),
        valid("c4", "repo", 4),
        valid("c5", "repo", 5),
        valid("c6", "repo", 6),
    ]
    assert apply_cap(rows) == {"c3", "c4", "c5"}


def test_the_cap_survivors_follow_the_deterministic_order():
    rows = [valid(f"c{n}", "repo", n) for n in (5, 1, 3, 9, 7)]
    assert apply_cap(rows) == {"c1", "c3", "c5"}


def test_no_repository_exceeds_three_final_cases():
    rows = [valid(f"a{n}", "ra", n) for n in range(6)] + [valid(f"b{n}", "rb", 10 + n) for n in range(6)]
    primary = apply_cap(rows)
    counts: dict[str, int] = {}
    for row in rows:
        if row["case_id"] in primary:
            counts[row["repository"]] = counts.get(row["repository"], 0) + 1
    assert counts == {"ra": 3, "rb": 3}
    assert all(n <= validate_cases.MAX_PER_REPO for n in counts.values())


def test_the_cap_is_applied_after_validation_in_the_shipped_script():
    """Structural: rejection filtering must precede the cap."""
    source = (BENCH / "bm08" / "validate_cases.py").read_text(encoding="utf-8")
    body = source.split("def main(")[1]
    assert body.index('curation_status"] == "validated"') < body.index("MAX_PER_REPO")


# ------------------------------- the frozen minimum executable denominator


def sufficient(cases: int, repos: int) -> bool:
    return cases >= validate_cases.MIN_CASES and repos >= validate_cases.MIN_REPOS


def test_the_frozen_minimum_is_twelve_cases_and_ten_repositories():
    assert (validate_cases.MIN_CASES, validate_cases.MIN_REPOS) == (12, 10)


def test_twelve_cases_across_ten_repositories_is_sufficient():
    assert sufficient(12, 10)


def test_eleven_cases_across_ten_repositories_is_a_shortfall():
    assert not sufficient(11, 10)


def test_twelve_cases_across_nine_repositories_is_a_shortfall():
    assert not sufficient(12, 9)


def test_the_conditions_are_conjunctive():
    """A large case count cannot buy its way past thin repository spread."""
    assert not sufficient(30, 9)
    assert not sufficient(11, 30)
    assert sufficient(14, 10) and sufficient(20, 11)


# ------------------------------------------ prior exposure stays conservative


def test_every_bm07_official_commit_remains_excluded():
    blob = json.loads((BENCH / "bm08" / "exclusions.json").read_text(encoding="utf-8"))
    excluded = set(blob["excluded_commits"])
    official = {e["fix_commit"] for e in blob["named_prior_cases"]["BM-07 official"]}
    assert official and official <= excluded

    queue = json.loads((BENCH / "bm08" / "queue.json").read_text(encoding="utf-8"))
    survivors = {c["fix_commit"] for c in queue} | {c["parent"] for c in queue}
    assert not (official & survivors), "a BM-07 official commit reached the BM-08 queue"


def test_the_exclusion_set_is_not_narrowed_to_official_cases_only():
    blob = json.loads((BENCH / "bm08" / "exclusions.json").read_text(encoding="utf-8"))
    excluded = set(blob["excluded_commits"])
    official = {e["fix_commit"] for e in blob["named_prior_cases"]["BM-07 official"]}
    # Mined and shortlisted pools are exposure too, not just the six that ran.
    assert len(excluded) > 1000
    assert len(excluded - official) > len(official)


def test_the_queue_contains_no_pre_floor_candidate():
    queue = json.loads((BENCH / "bm08" / "queue.json").read_text(encoding="utf-8"))
    assert queue
    assert all(c["author_date"] >= FLOOR for c in queue)
