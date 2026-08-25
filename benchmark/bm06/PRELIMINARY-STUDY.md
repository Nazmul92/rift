# BM-06 preliminary study — a null comparison, and what it cost to learn that

**Status: complete and frozen.** Measured on 8 curated cases × 3 arms, 24 runs,
$0.824 of provider spend. Nothing in this note has been recomputed since.

Measured facts and derived facts are separated throughout, and never combined
into a single figure.

---

## The question

> Does the full RIFT kernel outperform a strong model baseline on correct
> verified fixes, false-fix rejection, and cost?

Three arms over one corpus, sharing every piece of infrastructure except the
acceptance protocol:

```
A   --model-alone       strong model, target-pass acceptance
B   --probe-policy random   same proposals, random-probe kernel
C   (default)           same proposals, full RIFT kernel
```

---

## Measured result

```
A = 3/8
B = 3/8
C = 3/8
```

That is the result. It is a **tie**.

| | |
|---|---|
| cases | 8 |
| arm-runs | 24 |
| provider spend | $0.824 |
| model | `claude-sonnet-4-6`, provider-reported, bound to the manifest |
| false fixes accepted by any arm | 0 |

---

## Why it is inconclusive

The tie is not evidence that the arms are equivalent. It is evidence that this
corpus could not tell them apart, and the reasons are specific:

```
5/8 cases  no arm produced an applicable candidate at all
3/8 cases  produced accepted candidates — the only cases that could discriminate
0          weak-versus-strong acceptance disagreements
```

Every candidate arm A accepted **also** passed arm C's stronger gate. The
disagreement the kernel exists to catch never arose, so the mechanism was never
exercised. `0 false fixes` therefore proves neither superiority nor equivalence:
it records that the situation the gate is for did not occur in these 24 runs.

On cost, the measured run is unambiguous in one direction only: **C cost more per
correct fix than A**, because it bought the same three fixes with more work.
That figure is real and should not be explained away. It is also the price of a
guarantee that this corpus gave no opportunity to collect on.

---

## Post-hoc representation diagnostic — a separate thing

A deterministic, model-free replay of the frozen candidates found that the
dominant confound was not reasoning but **serialisation**:

```
13/15  candidate-phase failures were structurally malformed unified diffs
 9     became applicable after recomputing hunk counts only
 9/9   of those then passed the full verification gate
```

Projected back onto the corpus, that is:

```
POST-HOC REPRESENTATION DIAGNOSTIC
A = 6/8   B = 6/8   C = 6/8
```

**This is not the benchmark result.** It is a counterfactual computed after the
fact, on frozen artifacts, with no model involved. It says what the arms might
have scored had the patches been well-formed; it does not say what they did
score. The measured result remains 3/8 per arm, and the two figures are never
merged.

What the diagnostic does establish is that a large fraction of the corpus was
wasted on a defect that has nothing to do with the thesis under test — the model
choosing a reasonable change and then miscounting the lines it wrote.

---

## Engineering consequence

A deterministic unified-diff canonicaliser was productized (DAR-030 → DAR-035).
Its authority is exactly one operation:

```
PERMITTED   recompute @@ -a,b +c,d @@ counts from the hunk body

FORBIDDEN   change any other byte · invent source · search the repository ·
            relocate or fuzzy-match hunks · change paths or start lines ·
            repair truncation · call a model
```

Eligibility is decided by git itself (`git apply --numstat`, with and without
`--recount`, in a temporary directory), so repository content cannot influence
whether metadata is rewritten. Validated across all 24 frozen candidates:

```
originally working candidates modified   0
recovered through the full gate          9/9
```

The candidate pipeline records three independently hashed, immutable,
attempt-addressed stages:

```
exact model diff -> raw -> normalization -> normalized -> canonicalization -> canonical
                                                                                  |
                                                            content-addressed ChangeSet
```

Every rejected proposal remains reconstructable. No model-authored code was
changed by any of this.

---

## Scientific consequence

**The kernel advantage remains unmeasured.** Not disproved — unmeasured.

The next corpus must contain natural opportunities for weak target-pass
acceptance and strong counterfactual/preservation verification to *disagree*.
Cases where a plausible repair makes the failing target pass while breaking
preserved behaviour, broader existing tests, or counterfactual causality.

Selection criteria, fixed before the next run:

- real repository, real historical defect, `fix_commit^` pinned and fail-closed;
- a narrow reproducer over behaviour that is more general than the tested input;
- adjacent existing tests the fix did not author, as preservation surface;
- **no synthetic traps** built to defeat arm A, and no hand-edited source.

Discrimination will not be claimed from case structure. It is claimed only when
observed in real model output.

---

## What this study does not say

It does not say RIFT works. It does not say RIFT does not work. A null
comparison on a corpus with three discriminating cases and zero acceptance
disagreements is not a verdict on the central thesis in either direction, and
reporting it as one — in either direction — would be the same error.

The honest summary is narrower and more useful: **the experiment was
underpowered for its own question, the largest observed confound was
representation rather than reasoning, and that confound has been removed
deterministically so the next corpus can be spent on the question itself.**
