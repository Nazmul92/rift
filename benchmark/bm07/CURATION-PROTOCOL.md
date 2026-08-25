# BM-07 corpus curation protocol — curating for disagreement

**Status: candidate pool assembled, not authorized.** No provider call has been
made and none is authorized by this document. Additional spend so far: **$0.00**.

## The question this corpus exists to answer

> Does RIFT's stronger acceptance authority reject plausible-but-wrong fixes
> that target-pass acceptance would accept?

BM-06 could not answer it. Every candidate arm A accepted also passed arm C's
full gate, so the two authorities never had an opportunity to disagree. A corpus
of harder bugs would not have helped: what was missing was not difficulty but
**room for a shortcut**.

So the selection criterion is not "is this a bug" and not "can RIFT diagnose
this". It is:

> does this defect's natural behavioural surface admit a plausible repair that
> makes the failing target pass while being wrong?

## What is being curated for

A case is interesting when the correct behaviour is **more general than the one
input its reproducer names**, over a module that already carries adjacent tests.
That combination is what lets a special-case at the tested point pass the target
and break something else.

Structures, taken from the ruling and carried as data so a reviewer can see which
hypothesis each case was selected under:

| structure | the shortcut it admits |
|---|---|
| `cache` | bypass or clear the cache at the tested entry point; invalidation semantics elsewhere are lost |
| `exception` | catch and swallow at the tested call site; legitimate error propagation disappears |
| `locale_tz` | hardcode the tested zone or offset; another supported environment regresses |
| `mutation_order` | mutate or re-sort in place on the tested path; ordering/preservation breaks for other callers |
| `boundary` | special-case the tested boundary; the neighbouring valid edge regresses |

**The shortcut hypothesis is harness-only evidence.** It is recorded per case so
selection is auditable, and it is never shown to the model — like the upstream
patch, the preservation checks and the task-required ground truth. Curating a
case is not predicting what a model will do.

## Explicitly not done

- **No synthetic traps.** Nothing is authored to defeat arm A. Every case is a
  real historical defect in a real repository with its own reproducer.
- **No hand-edited source.** The repository is placed at the pinned parent; the
  fix commit's own test changes make the target fail. Nothing is written by hand.
- **No selection for RIFT's convenience.** A case is not preferred because
  diagnosis can represent it, nor rejected because diagnosis would return
  `representation_inadequate`. Proposal and verification are general; diagnosis
  is a bounded ontology, and a case where it abstains is still a valid test of
  proposal plus verification.
- **No ontology expansion.** The diagnosis vocabulary is frozen. The next
  experiment is not an ontology exercise.

## How the pool was built (model-free, offline)

Two stages over 30 real repositories already cloned locally, using git history
only. Both scripts are in this directory and reproduce from the repositories.

**Stage A — `mine_corpus.py`.** Scan up to 4,000 commits per repository for
fix-shaped commits that change source *and* a test, then keep those whose shape
leaves room for a shortcut: one or two source files, 1–4 edit sites, ≤25 added
source lines, at least one control-flow or comparison change, ≤3 new test
functions, and adjacent test coverage of the touched module.

> A first version of this scored **breadth of fix** and was wrong. It ranked
> 35-site sweeps highest — "pep8 and pyflakes fixes", "Improve documentation",
> "Move key functions to separate package" — on the theory that a broad diff is
> one a narrow patch could undercut. Those are not single defects, and a model
> asked to fix one has no shortcut available because no single behaviour is under
> test. The scoring was inverted to prefer focused behavioural fixes with narrow
> reproducers. The correction is recorded because the first ranking looked
> plausible and would have produced a corpus with no disagreement available —
> the same way BM-06 did.

**Stage B — `shortlist_corpus.py`.** Fail closed on provenance:

```
fix_commit^ == declared parent          or the case is dropped
a test function the fix commit adds     or the case is dropped  (the target)
>= 3 pre-existing tests in those files  or the case is dropped  (preservation surface)
a named shortcut structure              or the case is dropped
```

## Pool as it stands

```
181  candidates from stage A
 53  passed provenance
 21  shortlist, best per repository
```

One case per repository, so the pool cannot be dominated by whichever project
writes the most fix-shaped commit messages.

| | count |
|---|---|
| `exception` | 11 |
| `boundary` | 6 |
| `locale_tz` | 5 |
| `cache` | 4 |
| `mutation_order` | 3 |

All 21 are parent-pinned. Rows with repository, fix commit, pinned parent,
target node id, source and test files, preservation counts, structures and
shortcut hypotheses are in `candidate-pool.json`.

## What remains before any paid run

1. Execute each shortlisted case model-free: place the repository at the pinned
   parent with the fix commit's test changes applied, confirm the target
   **fails** there and **passes** with the upstream source patch. A case that
   does not reproduce is not a case.
2. Choose preservation checks from existing adjacent tests. They must not encode
   the upstream patch.
3. Reduce to a reviewed set and have it authorized separately.

Discrimination is **not** claimed from case structure. It is claimed only when a
model actually proposes a shortcut and the two authorities disagree about it.

## Arm design, fixed now

Canonicalisation is common infrastructure, not a C-arm feature:

```
model
  -> raw persistence
  -> normalization
  -> deterministic canonicalizer
  -> arm-specific protocol
```

```
A   strong model + common proposal infrastructure -> target-pass acceptance
B   same proposal infrastructure -> random-probe kernel
C   same proposal infrastructure -> full RIFT kernel
```

Giving only C the canonicaliser would make patch serialisation a confound again,
and it would be measured as a kernel advantage. `_canonicalize_proposal` sits on
the single path all three arms take, which is enforced by the placement itself
rather than by convention.
