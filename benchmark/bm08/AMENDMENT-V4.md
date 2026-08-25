# BM-08-v4 amendment — failure-identity reproducibility

Written and frozen **before** any v4 revalidation was run. Nothing below was
chosen after seeing which candidates survive it.

## History, unrewritten

```
BM-08-v1   CORPUS_INSUFFICIENT
BM-08-v2   CORPUS_SHORTFALL
BM-08-v3   threshold passed during construction, but executable preflight
           revealed an untested failure-identity stability assumption
v3 paid execution   NEVER STARTED
provider spend      $0.00
```

v3 is **not** rewritten as though the two unstable identities were known during
its validation. They were not. The v4 amendment exists precisely because
preflight discovered a contract that was missing.

## Rationale — frozen text

> BM-08-v3 preflight revealed that one-time failure-identity capture does not
> establish reproducibility. Two independently different instability mechanisms
> were observed: random generated assertion values and process-specific memory
> addresses. No BM-08 model arm has executed and no A-versus-C outcome exists.
> Therefore the amendment is validity-driven rather than outcome-tuned.

## The new requirement

For every otherwise-valid candidate:

```
N = 3 baseline failure observations

each observation MUST execute in a separate fresh process

same frozen baseline
same target
same governed failure observer
same benchmark environment

require exact governed identity equality:
identity_1 == identity_2 == identity_3
```

Any difference:

```
REJECT
reason = unstable_failure_identity
```

`N` is frozen at 3 and is not changed after seeing results.

## Fresh process is load-bearing

Three observations inside one Python process would not exercise the volatility
that caused the defect. A `repr()` containing an object's memory address is
stable within a process and differs across processes; a module-level random seed
may be drawn once per interpreter. Observing three times in one process would
have declared both defective cases stable and reproduced the v3 failure exactly.

So each observation is produced by an independently launched process that exits
before the next begins. No pytest process, interpreter, in-memory object or
cached module state is reused between observations.

## The amendment adds evidence, not a new judge

Failure-identity semantics are **unchanged**. There is no normalisation of
memory addresses, random names, timestamps, UUIDs, paths, temporary
directories, ordering or `repr` values, and no masking of volatile tokens.
Equality is the existing governed representation's own equality.

Normalising would have "fixed" `pathspec` in about one line. It would also have
made the benchmark accept a case whose failure cannot be identified — which is
the property the signature exists to establish.

## No special-casing

`faker-5128ae64` and `pathspec-b70e3fb4` exposed the defect; they do not define
the amendment's scope. The stability rule is applied uniformly to the entire
frozen candidate population, and neither case receives bespoke handling. Both
are re-run through the amended contract like every other candidate.

## Sequencing

The stability check runs only on candidates that would otherwise be VALID:

```
existing validity checks
  -> would be VALID?
       -> yes: 3 fresh-process stability observations
```

A candidate already rejected for a prior governed reason does not consume three
further baseline processes. The scientific admission criteria are unchanged; the
amendment adds reproducibility evidence at the point of final admission.

## Revalidation scope

v4 starts from the **full frozen post-dedupe population**, not from v3's 14
primary cases. The amended rule changes candidate admission, so the valid set is
regenerated from scratch:

```
frozen post-dedupe candidates
  -> full v4 model-free validation
  -> stability check on every otherwise-valid candidate
  -> VALID set
  -> <=3 VALID cases per repository
  -> threshold
```

No slot is reserved for a v3 case and no unstable case is manually replaced.

## Everything else is unchanged

```
57-repository population    frozen, e9c410a6…
pre-BM08 exclusion set      frozen, d4090113…
author-date floor           2018-01-01 via %aI
deterministic ordering      SHA-256(fix_commit)
near-duplicate collapse     unchanged
repository-resolution       fail-closed infrastructure preflight, unchanged
preservation-set rules      complete, never truncated
repository cap              <=3, applied after validation
minimum denominator         >=12 cases AND >=10 repositories
```

The denominator is not lowered because the new rule removes cases. That is the
situation it exists for.

## Accounting

`unstable_failure_identity` is a distinct governed rejection reason, reported
directly and never merged into a failure-identity, collection or "other"
bucket. It belongs inside `total_rejected`:

```
post_dedupe_candidates = total_rejected + total_valid
```

Repository-resolution failures remain outside that equation; they block the run
before candidate validation.

## Product note — recorded, not actioned

> Exact `verify --expect-signature` style enforcement can be brittle when test
> failure text contains process-volatile values such as object memory addresses
> or random generated values.

This is a future product consideration only. `src/riftagent` is unchanged: no
change to failure-signature representation, verify semantics or normalisation.
RIFT remains frozen for BM-08.
