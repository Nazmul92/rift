# BM-07 protocol — same-candidate shadow evaluation

**Frozen before any paid execution.** No provider call has been made and none is
authorized by this document. Spend so far: **$0.00**.

## What BM-07 is

A **mechanism benchmark**. Its corpus is deliberately curated for natural cases
where target-pass acceptance *can* be insufficient, so it is not a sample of
arbitrary Python bugs and cannot support a general performance claim.

Correct claim, if the result supports it:

> On natural historical bugs where target-pass acceptance can be insufficient,
> RIFT's stronger verification rejects wrong target-passing candidates that the
> weak protocol would accept.

Incorrect claim, at any result:

> RIFT is generally better than Sonnet on arbitrary Python bugs.

Recorded here before execution so the distinction cannot be renegotiated after
seeing the numbers.

## The question, and why arm comparison alone cannot answer it

> Does strong RIFT verification reject wrong candidates that weak target-pass
> acceptance would accept?

BM-06 compared arms that each proposed their own patch. When arm A accepted
patch X and arm C rejected patch Y, the difference mixed **proposal quality**
with **verification policy**, and nothing could separate them. Worse, on that
corpus every A acceptance also cleared C's gate, so no disagreement existed to
attribute at all.

Independent proposals cannot isolate acceptance authority. The same candidate
must be judged twice.

## Same-candidate shadow evaluation

For every canonical arm-A candidate:

```
                exact canonical patch X
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
      weak protocol            strong RIFT gate
      target-pass              full counterfactual
      acceptance               verification
              │                       │
         weak_verdict          strong_shadow_verdict
                          │
                   ground_truth_verdict
```

Recorded per candidate:

```
weak_verdict            accept | reject
strong_shadow_verdict   accept | reject   (existing verdict vocabulary)
ground_truth_verdict    correct | wrong   (see below)
```

The shadow gate runs the **same bytes** the weak protocol judged — the canonical
candidate from the shared pipeline — so any disagreement is attributable to
acceptance policy alone. **This requires no additional model call**: the patch
already exists, and both evaluations are deterministic.

Never inferred from `A accepted X, C rejected Y`. That comparison is not evidence
about verification.

## Primary metric, frozen now

```
same-candidate harmful weak acceptances prevented

count of arm-A canonical candidates where
    weak target-pass      = ACCEPT
    strong shadow gate    = REJECT
    ground truth          = WRONG
```

Reported alongside its mirror, because a gate can buy apparent safety by
rejecting good work:

```
same-candidate correct rejections

count where
    strong shadow gate    = REJECT
    ground truth          = CORRECT
```

Full matrix, always reported whole:

| | ground truth CORRECT | ground truth WRONG |
|---|---|---|
| weak ACCEPT · strong ACCEPT | agreed, correct | **agreed miss** — both wrong |
| weak ACCEPT · strong REJECT | **false rejection** — the cost | **desired disagreement** — the primary metric |
| weak REJECT · strong ACCEPT | strong recovers | strong error |
| weak REJECT · strong REJECT | agreed rejection | agreed, correct |

A result is not reported as success unless the desired-disagreement cell is
non-empty **and** the false-rejection cell is stated next to it.

## Ground truth

Decided from the frozen curation record, never from either arm's verdict:

- the target node passes with the candidate applied, **and**
- every untouched preservation node still passes, **and**
- the candidate does not touch frozen check files or runner configuration.

A candidate that passes the target and breaks a preservation node is **wrong**,
regardless of how plausible it looks. Preservation nodes come from
`validated-cases.json`, are natural pre-existing tests the fix commit left
untouched, and are harness-only: never shown to the model.

## Arms, and what stays secondary

```
A   strong model + common proposal infrastructure -> target-pass acceptance
B   same proposal infrastructure -> random-probe kernel
C   same proposal infrastructure -> full RIFT kernel
```

A/B/C outcomes are still reported — correct verified fix yield, false-fix
acceptance, abstention, tokens, commands, cost, wall time — but the central
acceptance-authority claim comes from the same canonical candidate under both
protocols. Proposal differences and verification differences are reported
separately and never summed.

## Canonicaliser parity

```
model response -> raw persistence -> normalization
               -> deterministic canonicalizer -> canonical candidate
               -> arm/protocol evaluation
```

Identical for A, B and C. `_canonicalize_proposal` sits on the single path all
arms take, so no arm can gain a serialisation advantage; giving only C the
canonicaliser would have re-created BM-06's confound and measured it as a kernel
result.

## Identity to record with every run

```
model identity          provider-reported, bound to the manifest
manifest hash           frozen before execution
runtime_hash            75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26
driver_hash             recorded per run
baseline_tree_hash      per case
reference environment   python:3.12-slim, Python 3.12.14, linux/amd64
```

## Cost accounting

Reserve → request → settle per call, scope-keyed, with provider-reported usage.
Estimates are never presented as measured usage. Spend is reported per arm and
per case, and the shadow evaluation adds **$0.00** because it makes no call.

## Run status, and what may be scored

These name what the **runner** did. They are not benchmark outcome categories
and never enter a score.

```
BLOCKED_FOR_RECONCILIATION  a durable request_started was never settled, so a
                            provider call may already have been paid for. The
                            whole run stops at startup, before preflight and
                            before any adapter, with 0 provider calls. Nothing
                            is reconciled or retried automatically.
OFFICIAL_COMPLETE           exactly 18 unique case-arm records, each with a
                            compatible terminal state, and every identity
                            matching. Only this may produce an official score.
INCOMPLETE_RUN              a gap, a duplicate, a state without its result, a
                            result without its state, or an unreadable row.
INVALID_RUN                 a record for a case or arm outside the frozen set.
DEVELOPMENT_PARTIAL_RUN     a deliberately restricted run (`--arms A`). Useful,
                            and explicitly not an official score.
```

Official BM-07 is **6 cases × 3 arms = 18 arm executions**, derived from the
manifest rather than asserted. Identity and completeness are both required:
identity drift refuses on its own, and a complete-but-drifted set is refused the
same way an identity-clean but incomplete one is.

An arm is never marked terminal before the evidence needed to reconstruct its
outcome is durable. A crash in that window leaves the result on disk with the
state still at `request_started` — fail-closed, and reconcilable.

## What is frozen and must not move

```
RIFT runtime            frozen at the checkpoint above
diagnosis ontology      frozen; representation_inadequate remains a valid outcome
semantic repair loop    DEFERRED
application repair loop DEFERRED
canonicalizer authority recompute @@ counts only
```

Diagnosis compatibility is **not** a curation criterion. A case that yields
`representation_inadequate` during autonomous diagnosis is still a valid test of
the proposal and verification mechanism, which is what BM-07 measures.

## Corpus size

No denominator is targeted. Whatever survives model-free validation is the
corpus, and the validated count is reported honestly. Eight strong natural cases
are preferable to twenty weak or contaminated ones.

## Discrimination is opportunity, not result

Curation may label `discrimination_potential = high | medium | low` from the
natural preservation surface. It may **not** claim a case discriminates A from C.
That claim requires an actual same-candidate model patch producing protocol
disagreement, and it will be made from the primary metric or not at all.
