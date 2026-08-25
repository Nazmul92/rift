# BM-08-v2 amendment — ratified before the amended validation pass

## BM-08-v1 — retained, not rewritten

```
BM-08-v1: CORPUS_INSUFFICIENT
```

The original era-blind deterministic selection produced only 6 validated cases
across 4 repositories under the frozen Python 3.12 environment. No BM-08 model
arm ran, no A-versus-C outcome exists, and no provider spend occurred.

This record stands as written. Nothing below revises it.

## Amendment rationale — frozen text

> The original era-blind sampling rule produced a corpus dominated by historical
> repository states incompatible with the already-frozen Python 3.12 benchmark
> environment. BM-08-v2 therefore introduces an author-date eligibility floor of
> 2018-01-01. No BM-08 model arm has been executed, no A-versus-C result exists,
> and no model spend has occurred; therefore this amendment cannot be based on
> benchmark outcome information.

> Eligibility is determined before near-duplicate collapse. Near-duplicate
> collapse operates only on candidates already eligible for the benchmark.

> Repository diversity is applied after model-free validation. Invalid or
> unrunnable candidates do not consume a repository's final-case quota.

## Frozen minimum executable denominator

Declared **before** any BM-08-v2 survival count was known.

```
>= 12 validated primary cases
AND
>= 10 distinct repositories
```

The conditions are conjunctive. A corpus below the 20–30 aspirational target is
not automatically invalid if it satisfies this minimum.

```
>=12 cases AND >=10 repos   ->  may proceed to paid-execution review
anything else               ->  CORPUS_SHORTFALL, no paid A/C run
```

There is no discretionary "sufficient enough" judgement after results are seen.

## Approved pipeline order

```
raw mined candidates
  -> previous-exposure exclusion
  -> author date >= 2018-01-01
  -> frozen deterministic ordering
  -> near-duplicate collapse
  -> MODEL-FREE VALIDATE EVERY SURVIVING CANDIDATE
  -> retain VALID only
  -> <= 3 VALID cases per repository, frozen deterministic order
  -> check frozen minimum denominator
  -> freeze final corpus
```

Two orderings are load-bearing and were wrong in v1.

**Eligibility precedes deduplication.** Collapsing first lets an ineligible 2015
commit win a duplicate family, and the era filter then deletes it — taking an
eligible 2020 sibling with it, and erasing the family from the benchmark
entirely. Filtering first means only eligible candidates ever compete to
represent a family.

**The repository cap follows validation.** Capping first spends a repository's
quota on candidates that later turn out to be unrunnable. A repository whose
first two candidates fail validation should contribute its third, fourth and
fifth valid ones, not be reduced to whatever survived of the first three.

## Author date is exact

Eligibility uses the **author date**, the parsed timestamp of git `%aI`.

```
author date >= 2018-01-01
```

Not committer date, not `%cI`, not a filesystem timestamp, not a release or tag
date. The miner records `author_date` (`%aI`) and `committer_date` (`%cI`)
separately so the distinction is visible in the artifact rather than asserted in
prose, and a regression asserts the floor reads the author field.

## What does not change

Prior-exposure exclusion stays conservative — every commit exposed anywhere in
governed benchmark or research artifacts, not merely official BM-06/BM-07 cases.
A false-positive exclusion loses one candidate; a false negative contaminates the
unseen-bug claim.

Deterministic ordering stays `SHA-256(fix_commit)`. No ranking by expected RIFT
success, bug difficulty, patch size, test count, repository popularity, category
preference or model suitability.

Near-duplicate detection is unchanged. Only the **population** entering collapse
changes: eligible, unseen, in-era candidates.

Preservation sets stay complete and untruncated.

Category labels are recorded and reported. There are no category quotas and no
hand-balancing: BM-08 is an ordinary unseen-bug replication benchmark, not a
curated challenge set.

## Scientific question — unchanged

> Across ordinary unseen real historical Python bugs, does frozen full RIFT
> produce more truth-correct fixes per dollar than the same frozen model alone?

`A = model alone`, `C = full RIFT`. BM-08 is not a false-fix discrimination
benchmark and selection is not optimised for RIFT.

## Frozen systems

BM-07 remains byte-verifiable forever and BM-08 remains a fork. `src/riftagent`
and `benchmark/bm07` are unchanged by this amendment.
