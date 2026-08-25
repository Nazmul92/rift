# BM-08-v3 amendment — repository-population expansion

## History preserved

```
BM-08-v1  CORPUS_INSUFFICIENT   6 cases / 4 repositories
BM-08-v2  CORPUS_SHORTFALL      8 cases / 4 repositories
```

Neither record is revised. v2 did not succeed and is not presented as having
succeeded.

## Rationale — frozen text

> BM-08-v2 exhausted the approved candidate population under the frozen rules but
> produced only 8 primary cases across 4 repositories, below the predeclared
> minimum of 12 cases across 10 repositories. No model arm was executed. BM-08-v3
> therefore expands only the source repository population while preserving all v2
> eligibility, ordering, deduplication, validation, diversity, and threshold rules
> unchanged.

> Repository expansion was authorized before any BM-08 model outcome existed and
> cannot be based on A-versus-C performance.

## What changes: the repository population, and nothing else

v3 adds repositories to the mining population. It does not add cases, and it
does not search for bugs. Every rule below is carried forward byte-for-byte from
`AMENDMENT-V2.md`:

```
previous-exposure exclusion          unchanged, conservative
author date >= 2018-01-01 via %aI    unchanged, not moved to 2019/2020/2021
deterministic ordering               unchanged, SHA-256(fix_commit)
near-duplicate definition            unchanged, eligibility still precedes collapse
model-free validation criteria       unchanged
complete preservation-set rules      unchanged, never truncated
<=3 VALID cases per repository        unchanged, applied after validation
minimum denominator                  unchanged, >=12 cases AND >=10 repositories
full-history mining                   unchanged, no recency cap restored
```

The v2 pipeline order is re-run in full against the wider population. Existing v2
cases receive no privileged treatment: the v3 corpus is recomputed from scratch,
and any v2 case that reappears does so because the frozen rules selected it
again.

## The expansion is a population, not a patch

The corpus needs four more cases and six more repositories. Adding exactly six
repositories would be optimising directly to the threshold, and the result would
be worth nothing: a denominator engineered to clear its own bar.

v3 therefore adds a substantially broader population than the shortfall
requires, chosen on project-level criteria alone, and accepts whatever the frozen
pipeline returns — including another shortfall.

## Repository eligibility — objective, and independent of model performance

A repository qualifies if it is a real public Python project with available Git
history, a pytest-compatible executable test suite the existing miner can
analyse, ordinary source-plus-test change commits, and meaningful history after
2018-01-01.

Explicitly **not** criteria: whether RIFT is likely to succeed, whether patches
look easy, whether bugs resemble BM-07's successes, whether a repository
contains known benchmark cases, or whether it yields desired categories.

Repository diversity is a preference at population-selection time: projects that
were major contributors to BM-06, BM-07 or BM-08-v1/v2 corpora are avoided where
practical. This is not a contamination boundary — **commit-level prior exposure
remains the actual contamination boundary**, and it is unchanged.

## The list is frozen before outcomes

The v3 repository population is written to `repository-population.json` with a
deterministic hash, recorded **before** any candidate from those repositories is
mined or validated. A repository that contributes nothing stays in the record.

Selective replacement — add a repository, observe zero valid cases, swap it for
another — is forbidden within this pass. If the expansion is still insufficient,
that is reported as a shortfall, not repaired by substitution.

## Threshold unchanged

```
>= 12 validated primary cases  AND  >= 10 distinct repositories
```

Conjunctive, and not lowered after seeing v3. The aspirational target remains
20–30 cases across 10+ repositories, and the denominator is whatever the frozen
rules produce — 14 across 11 passes, and is not then extended toward 20.

## Scientific question — unchanged

> Across ordinary unseen real historical Python bugs, does frozen full RIFT
> produce more truth-correct fixes per dollar than the same frozen model alone?

`A = model alone`, `C = full RIFT`. The expansion optimises for neither arm.

## Frozen systems

`src/riftagent` and `benchmark/bm07` are unchanged, verified byte-identical
against a snapshot taken before this pass began.
