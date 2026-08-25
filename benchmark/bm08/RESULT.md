# BM-08 result — 24 cases × arms A and C

Executed 2026-08-24 against the frozen v5 executable manifest. 48 of 48 official
case-arm records, `OFFICIAL_COMPLETE`, no governed failure, no interim look and
no stopping-rule change. Pre-registration in `PRE-REGISTRATION.md`.

```
total spend     $1.8366 of a $25.00 ceiling   (worst-case envelope $23.04)
arm A           $0.9384        arm C          $0.8982
identity        runtime, driver, runner, oracle, manifest, corpus, population,
                exclusion, environment — all matched, before and after
preflight       24 of 24 cases reconstructed, 0 failures
isolation       level=full, "network unshared", partial_sandbox authority: none
```

## The headline

```
                        arm A                    arm C
                        model alone,             full frozen RIFT
                        weak target-pass
truth-correct fixes     5 of 24                  3 of 24
false accepts           0                        0
correct but rejected    0                        0
spend                   $0.9384                  $0.8982
correct per dollar      5.33                     3.34
```

**Arm C produced fewer truth-correct fixes than the model alone, at
indistinguishable cost.** This is an unfavourable result for the efficiency
thesis and it is reported as it stands.

## What the result does not show

It does **not** show that RIFT's gate rejected correct fixes. `correct but
rejected` is **0 for both arms**, and the reason is structural: each arm
receives its own independent model proposal. The two cases where the arms
diverged carry *different candidate hashes*:

```
parse-e9aa02bd       A  30a60377…  applied, target passes    -> correct
                     C  347139b2…  applied, target still fails
dnspython-246febc4   A  247e23a8…  applied, target passes    -> correct
                     C  beb88578…  does not apply (dns/resolver.py:877)
```

On both, arm C's own proposal was worse. The gate never had a correct candidate
to reject. **The 5-versus-3 gap is proposal-side, not gate conservatism**, and at
n=24 a two-case difference carries no weight.

### Schema repairs, and one confounded divergence

Each arm issues one `propose_change` call and, when the response is invalid, one
permitted schema repair. That repair re-asks for well-formed output; it is not a
second attempt at a fix, and every case registers exactly one changeset.

The opportunity is close to symmetric:

```
                     propose_change   + repair   = fix-proposal calls
arm A                      24              6            30
arm C                      24              5            29   (+2 hypothesis calls)
```

Arm C in fact made more total provider requests than arm A (31 versus 30),
because the full protocol also calls `propose_hypotheses`.

But the repairs did not fall evenly on the cases that mattered:

```
parse-e9aa02bd       A  2 calls (propose_change -> repair)  -> correct
                     C  1 call  (first response valid)      -> wrong
dnspython-246febc4   A  1 call                              -> correct
                     C  1 call                              -> wrong
```

On `parse-e9aa02bd` arm A received a repair round that arm C did not, and it was
A's *repaired* proposal that turned out correct. **That divergence is confounded
by an unequal repair round rather than being clean proposal variance.** The
`dnspython-246febc4` divergence is clean: one call each.

This does not change the direction of the result, and it does not move the cause
to the gate — a repair is triggered by malformed model output, which is
proposal-side. It does mean the honest count of clean, unconfounded divergences
in arm A's favour is **one, not two**.

## The acceptance-authority mechanism was again not exercised

Arm A accepted 5 candidates and **every one of them was truth-correct**. A weak
target-pass rule made zero false accepts on this corpus, so the acceptance
authority that RIFT exists to supply had nothing to catch.

This reproduces BM-07's null from a different direction. BM-07 found zero
weak-versus-strong disagreements; BM-08 finds zero false accepts by the weak
baseline. A gate can only pay for itself when the cheap rule accepts something
wrong, and across two benchmarks the cheap rule has not done so.

## Where the 48 arms actually failed

```
 29  the patch does not apply to the baseline tree
 10  applies, but the target still fails
  8  truth correct
  1  no proposal produced (output exhausted)
```

**39 of 48 arms failed before any acceptance decision was possible.** The
dominant constraint on this corpus is proposal representation — producing a
patch that applies and fixes the target — not deciding whether a proposal is
trustworthy. Measuring acceptance authority requires proposals good enough for
acceptance to be the binding question, and on ordinary bugs from 15 real
repositories they mostly are not.

## A larger question the result raises

BM-07 and BM-08 ran against the **same** `runtime_hash 75196d87…` — the same
repair loop and the same canonicalizer — yet arm C fell from 4 of 6 truth-correct
(67%) to 3 of 24 (13%), and "patch does not apply" rose from 17% to 58%.
Canonicalization still fires at nearly the same rate (72% of BM-07 candidates,
62% of BM-08 arm C's); it simply rescues far less.

That is not a change in model coding ability and not a RIFT code change, and it
is not an asymmetry between arms — both BM-08 arms fail to apply at
indistinguishable rates.

**That replay has since been run** (`CANONICALIZATION-FINDING.md`,
`canonicalization-replay.json`) and it is no longer the next step. Replaying the
exact retained bytes of 47 arms through `git apply --check` found:

```
raw applicable       5 of 47      canonical applicable  18 of 47
RESCUED             13            rescue rate  13/42 = 31.0%
UNRECOVERED         29            all source/context, 0 representation-level
damage opportunities 0            damage rate  N/A
```

Canonicalization cleared all observed representation-level defects in the
retained candidate set; the residual non-applicability is source/context/path
related. No raw-applicable patch was transformed, so a damage rate is not
estimable. BM-07's raw bytes were never retained, so comparative transfer rates
between the benchmarks are **not measurable** and the tuning question stays
open rather than settled either way.

The forward question the replay leaves is therefore not about patch
representation. It is whether the model can quote and locate source that
actually exists. That is taken up separately by the exploratory probe under
`benchmark/analysis/source_recall_probe/` — hypothesis-generating only, not
BM-08, not BM-09, and not official benchmark evidence.

## One accounting gap, disclosed

`isort-49bb9bab` arm A carries **no truth record at all**, so arm A has 23 truth
verdicts across 24 cases while arm C has 24. The cause is honest and visible in
the ledger:

```
model_response_received   finish_reason "length", output_tokens 4000 (the ceiling)
model_response_invalid    "ambiguous response: 11 different objects satisfied
                          the schema", output_exhausted true
gate_phase_finished       candidate, passed false, "no proposal was produced"
```

The response hit `max_output_tokens` and returned eleven schema-satisfying
objects. Because output was exhausted, the one permitted schema repair was
correctly *not* issued — repairing an exhausted response only exhausts it again
— so this arm made one provider request instead of two.

With no candidate, there is nothing to evaluate against truth, so `ground_truth`
is empty rather than `wrong`. Arm C on the same case produced a candidate that
failed to apply and therefore *does* carry `wrong`. That asymmetry means the two
arms have different truth denominators.

**All figures above count that arm as not-correct, giving arm A 5 of 24.** The
alternative reading — 5 of 23 — would flatter arm A, which is the arm that
already wins. `OFFICIAL_COMPLETE` checks record count and terminal states, not
the presence of a truth verdict, so it did not catch this; that is a real
limitation of the completeness definition rather than of this run.

## Environment confirmation

Every arm's receipt records `partial_sandbox: none` and
`sandbox_probed level=full, "network unshared"`. RIFT selected full isolation on
its own throughout the paid run and never required partial-isolation authority.
Under the pre-correction image this run would have needed
`--allow-partial-sandbox` on all 48 arms.

`dnspython`'s three cases executed with their live-resolver preservation nodes
intact, in the same network-denied environment their admission used.

## Status

BM-08 answers the question it was frozen to ask. The answer is that on 24
ordinary bugs from 15 repositories, **full RIFT did not produce more
truth-correct fixes per dollar than the same model alone**, and the mechanism it
exists to provide was not exercised because the cheap baseline made no
mistakes for it to catch.

This does not refute the acceptance-authority thesis; it fails to test it, for
the third time, because proposal quality gates the experiment. A benchmark that
tests acceptance authority needs a corpus where the weak rule demonstrably
false-accepts — which is a corpus-design problem, not a RIFT-design problem.
