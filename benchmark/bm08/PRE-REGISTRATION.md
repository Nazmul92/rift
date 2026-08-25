# BM-08 official run — pre-registration

Written and frozen **before** the official 48-arm execution began, and after the
one-case paid smoke had already been observed. It exists because the smoke was
observed first: that ordering has to be on the record rather than inferred.

## What was observed before the official run

A single paid case — `click-a17b5447`, arms A and C — was executed on
2026-08-24 as a feasibility check. Its outcome was seen before the official run
started, and it is stated here in full:

```
2 of 2 records, OFFICIAL_COMPLETE, identity_problems [] on both
both arms          unverifiable
reason             the proposed patch does not apply to the baseline tree
                   (git apply --check failed forward at every strip level)
truth by arm       A:wrong, C:wrong          classification  both_reject
spend              $0.0258 + $0.0331 = $0.0589
sandbox            level=full, "network unshared", partial_sandbox authority: none
```

## Why it cannot contaminate the official result

The smoke manifest carries one case instead of twenty-four, so it recomputes to a
different identity:

```
official manifest_hash   b0ea08dc9ae7fb6c9317260fdc606d3772168570477b3055b0525a759ff7e26b
smoke    manifest_hash   3793b9e5d64a2753c038dd42e435cf681931bcd9887a9ffb7f426dd101ad9fca
```

Aggregation rejects a mixed-identity result set, so a smoke record cannot enter an
official score even by accident. The two runs also write to different paths —
`results-smoke.jsonl` and `results-smoke-evidence/` for the smoke,
`results.jsonl` and `results-evidence/` for the official run.

Every other identity is carried through unchanged: `runtime_hash`,
`driver_hash`, `runner_hash`, `oracle_hash`, `corpus_manifest_hash`,
`repository_population_hash_v5`, `exclusion_set_hash`,
`execution_environment_hash`.

## Nothing changed because of what the smoke showed

No corpus, case, prompt, model, price, budget, harness, canonicalization rule,
gate, oracle, selection rule or stopping rule was modified in response to the
smoke outcome. The frozen identities above are the proof: any such change would
have moved one of them.

In particular, **no repair loop is added and canonicalization is not adjusted**
because a patch failed to apply. The frozen system is the system under
measurement.

## The stopping rule, declared in advance

The official run executes **all 48 arms — 24 cases x {A, C} — regardless of
intermediate outcomes.** There is no interim look, no conditional continuation,
no early stop on an unfavourable trend, and no additional pilot cases.

It stops early only for a governed failure:

```
budget          a reservation cannot be covered by remaining authority
identity        any runtime/driver/runner/oracle/manifest/corpus/population/
                exclusion/environment hash fails to match
reconciliation  a model_request_started without durable response evidence
infrastructure  repository resolution, isolation unprovable, preflight failure
safety          an explicit operator stop
```

None of these is a scientific outcome, and none of them is a candidate
rejection.

## What the run is allowed to conclude

BM-08 may return a null or unfavourable result and that is a result. If
`unverifiable` repeats across the corpus because proposed patches do not apply,
that is not wasted measurement: it answers the efficiency question by showing
that proposal representation, not acceptance authority, remains the dominant
constraint.

## Budget framing

`$23.04` is the worst-case reservation envelope, not a forecast. The smoke
settled at about `$0.0295` per arm, which extrapolates to roughly `$1.41` for 48
arms. Actual cost may exceed that; the envelope is what bounds it.

```
derived reservation per arm   $0.4800
worst case                    24 x 2 x $0.4800 = $23.04
ceiling                       $25.00
```
