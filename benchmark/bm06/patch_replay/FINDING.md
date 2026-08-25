# Post-hoc replay: how much of the failure was representation?

**POST-HOC DIAGNOSTIC. NOT BM-06. NOT A REPLACEMENT BENCHMARK RESULT.**

The preliminary run tied A/B/C at 3/8 correct-fix yield. Fifteen of twenty-four
arm-runs failed at the candidate phase. This replay asks one question, offline
and deterministically:

> If RIFT repaired only mechanical unified-diff metadata while preserving the
> model's proposed content exactly, how many failed candidates would become
> applicable, and how many would pass the existing verification gate?

No provider call was made. Additional spend: **$0.00**. The original result,
records, patches, verdicts, costs and identities are untouched; everything here
was written under `benchmark/bm06/patch_replay/`.

## Classification, recomputed from the raw records

Git is the authority, not a regex. `git apply` distinguishes the two failures in
its own words, and that distinction is the one this experiment turns on:

| git says | meaning | count |
|---|---|---|
| `corrupt patch at line N` | the diff cannot be parsed — **structurally invalid** | **13** |
| `patch failed: <path>` | the diff parses; the tree disagrees — **parseable, non-applicable** | **2** |
| | **candidate failures** | **15** |

An earlier regex-based classifier of mine called all 15 structurally invalid.
Git's own taxonomy gives 13/2, which matches the independent inspection, and git
is the parser that actually rejected them. The regex classifier was wrong and
was replaced rather than reconciled.

## The normaliser

Permitted edits, all to diff *control* metadata:

* hunk `@@ -a,b +c,d @@` counts recomputed from the hunk's own body.

That is the entire list. Everything else fails closed:

* a content line with no `+`/`-`/space prefix — deciding whether it is context
  or an addition is deciding what the model meant;
* a hunk header that will not parse;
* a hunk with no body;
* **a missing final newline.** This began as a permitted repair. The invariant
  check rejected it: appending a newline to a diff whose last line is content
  changes that line's bytes, and unified-diff states a missing final newline
  explicitly with `\ No newline at end of file`. The allowance was removed
  rather than the invariant loosened.

Never done: changing any content line, inventing source text, searching the
repository to reconstruct context, relocating a hunk, or asking a model
anything.

### The invariant, and how it is proved

`semantic_lines()` extracts every context, added and deleted line, with its
prefix and bytes. `normalize()` asserts the sequence is identical before and
after and returns UNSAFE if it is not — so the guarantee is enforced in the code
as well as in the tests, because a normaliser that silently altered content
would be worse than one that failed.

**All 13 normalised patches passed this check byte-for-byte.**

## The funnel

```
24 total arm-runs
│
├── 9 reached the acceptance path originally
│
└── 15 candidate-phase failures
     │
     ├── 13 structurally invalid (git: "corrupt patch")
     │    │
     │    ├── 13 safely normalised (metadata only)
     │    │    │
     │    │    ├── 9 apply after normalisation
     │    │    │    ├── 9 verify under the FULL gate
     │    │    │    └── 0 fail the deterministic gate
     │    │    └── 4 still non-applicable
     │    │
     │    └── 0 normalisation unsafe
     │
     └── 2 parseable but non-applicable (content wrong, not metadata)
```

Every replay verified the case's frozen `baseline_tree_hash` and pinned parent
before applying, and restored the tree afterwards — 15/15 verified before, 9/9
restored after. The gate used was the existing five-phase counterfactual gate,
not a target-pass shortcut.

## Counterfactual yield — POST-HOC, NOT THE BENCHMARK RESULT

| arm | original correct | recovered by metadata fix alone | counterfactual |
|---|---|---|---|
| A | 3/8 | +3 | **6/8** |
| B | 3/8 | +3 | **6/8** |
| C | 3/8 | +3 | **6/8** |

**Nine of fifteen candidate failures — 60% — were representation failures, not
wrong fixes.** The model had chosen a change that passes the full gate, and
serialised it with hunk counts that did not match its own body.

The arms remain tied. Metadata normalisation recovered exactly three fixes for
each arm, so it does not separate them either — it raises the floor uniformly.

## Representation versus semantics

| | count |
|---|---|
| representation-recoverable, verified by metadata fix alone | **9** |
| remaining failures | 6 |
| — normalised but still non-applicable (context/content wrong) | 4 |
| — parseable but non-applicable (content wrong) | 2 |
| — normalised, applied, then failed the gate | **0** |
| — normalisation unsafe | 0 |

**Zero patches applied and then failed the gate.** Every patch that could be
made to apply also verified. On this evidence the boundary is sharp: a patch
either had correct content wrapped in broken metadata, or had wrong content.
Nothing in between showed up.

## Recommendation

```
DETERMINISTIC PATCH CANONICALIZER JUSTIFIED
```

**Outcome A.** 9 of 13 structurally invalid patches became applicable and all 9
verified under the full gate, through arithmetic on hunk counts alone. No model
call is warranted for this class — recomputing a count from the body it
describes is deterministic, free, and cannot introduce content the model did not
write.

The remaining 6 are **not** a case for the same mechanism:

* 4 normalised cleanly and still would not apply — their context lines do not
  match the tree. That is Outcome B territory, and it is the only place a
  bounded format/application repair request could plausibly help, since the
  feedback available is the exact `git apply` error;
* 2 were parseable all along and failed on content.

**No evidence for the semantic repair loop appears in this run.** Outcome C
requires patches that apply and then fail behavioural verification, and there
were zero. The repair loop remains unmeasured — and this replay is evidence
against prioritising it over the canonicalizer, not evidence for it.

Nothing has been implemented. The next step is a ruling.

## The two loops must stay separate

| | trigger | feedback | this run's evidence |
|---|---|---|---|
| representation/application repair | diff invalid or will not apply | parser or `git apply` error | **strong: 9 recoverable, 4 candidates for a bounded retry** |
| semantic repair | patch applies, behaviour still wrong | execution evidence from the gate | **none: 0 occurrences** |

Collapsing these into one generic "retry loop" would spend a model request on
the 9 cases arithmetic already solves.

## The safety result is unchanged

This replay does not touch it. Arm A's three acceptances were
`accepted_by_target_pass`; B and C's were `verified_against_approved_checks`;
all three of A's also passed the stronger shadow gate. So:

> RIFT's safety mechanism did not fail, and its comparative safety advantage was
> not exercised, because there were **zero weak-versus-strong acceptance
> disagreements**.

`0 false fixes` proves neither superiority nor equivalence. It records that the
disagreement the gate exists to catch did not arise in these 24 arm-runs.

## Addendum — the product canonicaliser now reaches all nine (DAR-031)

DAR-030 shipped a heuristic that recovered 6 of the 9 above, declining three it
could not prove safe. DAR-031 replaced the heuristic with git's own parse
verdict (`git apply --numstat`, with and without `--recount`, in a temporary
directory) and re-ran all 24 candidates through the **full** gate rather than
`git apply` alone:

| | |
|---|---|
| working candidates modified | 0 |
| working candidates that stopped applying | 0 |
| recovered through the full gate | **9 of 9** — A 3, B 3, C 3 |

Rows in `v2_matrix.json`. The counterfactual figure this file reports is
unchanged; what changed is that the product now achieves it under a rule
validated against every frozen candidate, and that the raw model bytes are
persisted alongside the canonical ones so the comparison is auditable.
