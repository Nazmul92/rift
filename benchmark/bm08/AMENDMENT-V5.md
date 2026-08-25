# BM-08-v5 amendment — repository-population expansion

Written and frozen **before** the v5 repositories were mined and before any
validation outcome from them was known.

## History, unrewritten

```
BM-08-v1   CORPUS_INSUFFICIENT
BM-08-v2   CORPUS_SHORTFALL
BM-08-v3   construction threshold passed, but paid preflight exposed a missing
           failure-identity stability proof
BM-08-v4   12 cases / 8 repositories, CORPUS_SHORTFALL
```

Two invalidated infrastructure attempts are retained as historical invalid
runs, not scientific outcomes: the v3 dual-root run (5 cases, every new
repository unevaluated) and the first v4 stability run (22 of 22 "unstable",
caused by observing a tree with the fix already applied).

## Rationale — frozen text

> BM-08-v4 produced 12 primary cases across 8 repositories. The case-count
> minimum passed, while the independent-repository minimum failed. No BM-08
> model arm has executed and no A-versus-C outcome exists. BM-08-v5 therefore
> changes only the source repository population. All v4 eligibility, exclusion,
> deduplication, validation, failure-stability, diversity-cap, and
> minimum-denominator rules remain unchanged.

> The expansion is repository-level rather than case-level. Repositories are
> frozen before mining/validation outcomes are known and are not selectively
> replaced based on yield.

## What is failing, precisely

The corpus does not need more bugs. It needs more **independent repositories**.

```
v2   8 cases /  4 repositories
v3  14 cases / 10 repositories at construction, 12 / 8 executable
v4  12 cases /  8 repositories
```

Case count has cleared or nearly cleared its bar three times running.
Repository spread has failed every time. The dominant cause is unchanged: the
frozen Python 3.12 environment cannot collect most historical states of these
projects, and 107 of 178 v4 rejections were collection failures.

## The expansion is broad, not targeted

The benchmark is two repositories short. Adding two would optimise directly to
the threshold and the resulting denominator would be worthless — a corpus
engineered to clear its own bar measures the engineering, not the question.

v5 therefore adds a substantially broader batch than the shortfall requires and
accepts whatever the frozen pipeline returns, including another shortfall.

## Selection criteria — objective and outcome-independent

A repository qualifies if it is a real public Python project with available Git
history, substantial post-2018 development, ordinary source-plus-test commits,
and a test suite compatible in principle with the existing pytest pipeline.

Explicitly **not** criteria: small or easy patches, known model performance,
similarity to current successes, bug categories RIFT already handles well,
expected validation yield, or ability to clear the 10-repository threshold.

Repositories already heavily represented in BM-06, BM-07 or BM-08 v1–v4 are
avoided as a diversity preference. The contamination authority remains the
frozen **commit-level** pre-BM-08 exclusion set; no repository-level exposure
exclusion is invented.

## Frozen before outcomes

The complete old-plus-new population is written to
`repository-population-v5.json` with `repository_population_hash_v5`, recorded
before a single v5 candidate is mined. Once frozen: no additions, no removals,
no replacements within this pass. A repository producing zero candidates or zero
valid cases stays in the record.

## The v4 contract is untouched

```
pre-BM08 exclusion authority     frozen, d4090113…
author date >= 2018-01-01 (%aI)  unchanged
eligibility before dedupe        unchanged
deterministic ordering           SHA-256(fix_commit)
near-duplicate semantics         unchanged
repository resolution            fail-closed infrastructure preflight
model-free validity checks       unchanged
preservation-set logic           complete, never truncated
failure_identity representation  unchanged, no normalisation
N                                exactly 3
observations                     three independent fresh processes
equality                         exact governed identity equality
unstable_failure_identity        unchanged rejection reason
repository cap                   <=3 VALID cases, applied after validation
minimum denominator              >=12 cases AND >=10 repositories
```

No validation amendment is authorised in this task. The corpus is recomputed
from the whole expanded population; v4's twelve cases receive no reserved slot
and may or may not survive.

## Stopping rule

If v5 reaches the minimum, population expansion stops in this pass. The
aspirational 20–30 target does not license adding repositories after a pass to
inflate headline N.

If v5 fails, that is reported as `CORPUS_SHORTFALL` — with no further batch, no
swaps, and no change to `N`, the era floor, dedupe, validation, the cap or the
threshold.

## Product note — unchanged, still non-actionable

> Exact `verify --expect-signature` style enforcement can be brittle when test
> failure text contains process-volatile values such as object memory addresses
> or random generated values.

`src/riftagent` is unchanged. No structured-signature masking or normalisation.
