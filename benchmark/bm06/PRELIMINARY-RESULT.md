# Preliminary benchmark result — 24 arm-runs, 8 cases, $0.824

**This is not BM-06.** It is a preliminary run over 8 curated cases across three
cause classes. BM-06's frozen denominator is 30 cases across eight classes, and
nothing here may be reported as BM-06 or as evidence for the eight-class thesis.

| | |
|---|---|
| completed | 24 of 24 arm-runs, 8 cases × 3 arms |
| charged | **$0.824** of the $6.77 authorized ceiling |
| model | `claude-sonnet-4-6` — declared, configured and provider-reported |
| manifest | `7275dbfe3fe97c820ed3c74db0b0572c1b57887b80f493549c1a57159dd92dac` |
| runtime | `064f0e84a74364e0f80f8b7ef9c5e764…` |
| driver | `d54e7658be4c5d0f0fe4c5eb253314ef…` |

## The headline: the three arms tied

| arm | accepted | ground-truth correct | verified fix yield | **false-fix acceptance** | cost / correct fix |
|---|---|---|---|---|---|
| A — model alone | 3 / 8 | 3 | 0.375 | **0.0** | $0.075 |
| B — kernel, random probes | 3 / 8 | 3 | 0.375 | **0.0** | $0.085 |
| C — full kernel | 3 / 8 | 3 | 0.375 | **0.0** | $0.115 |

**RIFT's kernel did not outperform the model alone on this corpus**, and cost
53% more per correct fix than arm A. That is the result, and it is reported
first because it is the one that does not flatter the product.

## What did hold: acceptance authority

**Zero false-fix acceptances in all three arms.** Every patch any arm accepted
was independently confirmed correct by re-scoring under C's gate. No arm
accepted a patch that ground truth then rejected, and no arm rejected a patch
that ground truth would have accepted:

```
A: accepted=3  gt_correct=3  false_accept=0  missed=0
B: accepted=3  gt_correct=3  false_accept=0  missed=0
C: accepted=3  gt_correct=3  false_accept=0  missed=0
```

Arm A's own patches, re-scored under C's gate without re-proposing, gave
3 `verified_against_approved_checks` and 5 `unverifiable` — the same 3 it
accepted. On this corpus the weaker acceptance rule and the full gate agreed on
every case.

**This does not demonstrate that the gate is unnecessary.** It demonstrates that
on 8 cases the model produced no patch that passed its target while breaking
something else — the failure mode the gate exists to catch simply did not occur
often enough to separate the arms. A benchmark where the dangerous case never
arises cannot measure defence against it.

## Why the comparison is underpowered

| cause class | cases | cases where any arm produced a correct patch |
|---|---|---|
| `genuine_source_bug` | 3 | **3** |
| `version_mismatch` | 3 | 1 |
| `locale_timezone` | 2 | **0** |

**Five of eight cases produced no accepted patch from any arm.** Those cases
distinguish nothing: all three arms failed identically. The arms were actually
separated on three cases, and three cases cannot separate three arms.

| case | A | B | C |
|---|---|---|---|
| cachetools | accepted, correct | unverifiable | unverifiable |
| pygments | accepted, correct | **verified** | **verified** |
| pyparsing | accepted, correct | **verified** | **verified** |
| freezegun | unverifiable | **verified** | **verified** |
| dateutil | unverifiable | unverifiable | unverifiable |
| icalendar ×3 | unverifiable | unverifiable | unverifiable |

A won cachetools; B and C won freezegun; they tied on pygments and pyparsing.
That is 1–1 with two draws.

## The dominant failure was diff formatting

**15 of 24 arm-runs failed at `failed_phase: candidate`**, essentially all of
them because `git apply --check` rejected the patch at every strip level. The
patch would not apply to the tree it was written against.

This is a **model limitation, not an infrastructure one**, and it is exactly the
distinction DAR-022 through DAR-028 were built to preserve:

| | |
|---|---|
| inadequate context → bad patch | infrastructure defect |
| **adequate context → bad patch** | **a measurement of the model** |

Context adequacy was audited at **8/8 COVERED** before the run, at 26.9–72.0% of
the character budget. The model was shown what it needed and still emitted
unappliable diffs on two-thirds of arms. The `locale_timezone` and larger
`version_mismatch` cases produced the longest responses — up to 2,303 output
tokens and 6,526 characters — and those are precisely the ones that failed.
Longer multi-hunk diffs, more hunk headers, more opportunities to be wrong.

**This swamped what the benchmark was designed to measure.** Arm quality cannot
be compared through a failure mode that affects all arms equally.

## Provider and adapter health

The failure that destroyed the first attempt at this run did not recur once.

| | first attempt (Sonnet 5) | this run |
|---|---|---|
| empty responses | 4 of 5 | **0 of 30** |
| `finish_reason: length` | 4 of 5 | **0** |
| schema repairs needed | not implemented | **1** |
| provider-reported model | — | `claude-sonnet-4-6`, all 30 responses |

One schema repair fired across the whole run — the DAR-020/021 entitlement being
used once, sparingly, exactly as intended.

## Provenance

Every identity check passed on every arm: manifest, runtime and driver hashes
frozen at startup; `manifest_model == configured_model == provider_reported`;
all 8 baseline trees matching their frozen hashes before and after each arm; all
8 parent pins confirmed against git. Each of the 24 records carries its case's
`baseline_tree_hash`, its `priced_models` and its `provider_reported_models`.

## What this run does and does not support

**Supported:**

- the adapter, the gate, the ledger, the spend ceiling and the identity bindings
  all work end to end on real repositories;
- acceptance authority did not fail: 0 false-fix acceptances across 24 arm-runs;
- `claude-sonnet-4-6` can produce correct, gate-passing repairs on real bugs —
  3 of 8 cases, confirmed independently.

**Not supported:**

- any claim that RIFT's kernel improves fix yield over the model alone. On this
  corpus it did not, and it cost more;
- any claim about the repair loop, which is not implemented — this run used one
  candidate attempt per arm by design;
- anything about the five cause classes absent from this corpus.

**Unresolved:** whether the arms would separate on a corpus where patch
application is not the dominant failure. That question needs either a model that
formats diffs more reliably, a patch-application path more tolerant of
near-miss hunks, or cases whose fixes are smaller — and choosing among those is
a design decision, not a benchmark one.
