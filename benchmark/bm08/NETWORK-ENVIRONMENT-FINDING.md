# BM-08 finding: the corpus was validated in an environment the paid run cannot use

**No provider call was made. Additional spend $0.00.** The paid run stopped in
mandatory preflight after 4 minutes, before the first request.

```
status          BLOCKED_PREFLIGHT
provider calls  0
spend           $0.00
results.jsonl   never created
```

## What happened

The BM-08-v5 corpus — 24 cases across 15 repositories — passed every model-free
gate, including a full 24/24 paid-path preflight run minutes earlier. The paid
run then refused to start:

```
FAIL  dnspython-246febc4: 2 preservation nodes fail on the reconstructed baseline
six-case preflight failed (1); no provider call was made
```

The two nodes:

```
tests/test_resolver.py::LiveResolverTests::testResolveAddress
tests/test_resolver.py::LiveResolverTests::testCanonicalNameDangling
```

`LiveResolverTests` performs **real DNS lookups**.

## The cause is a methodology error, not a corpus defect

Every model-free stage of this benchmark ran with `--network none`: v5 mining,
candidate validation, the N=3 fresh-process stability checks, and the 24-case
preflight. **The paid run cannot run that way** — it needs network to reach the
provider.

With network disabled those two nodes do not attempt live resolution. With it
enabled they do, and they fail. The preservation set is green in one environment
and red in the other, and admission used the wrong one.

Reproduced deliberately:

```
--network none     preservation PASS   (55 of 55)
network enabled    preservation FAIL   (2 of 55)
```

## Why this is not confined to one case

This is the same family of defect as the failure-identity finding: **a property
was established under conditions the paid run does not reproduce.**

Any preservation node whose behaviour depends on network availability is
currently unverified across all 24 cases. `dnspython` was caught only because
its live tests fail loudly; a node that silently changes behaviour — a timeout
path, a cached lookup, a fallback branch — would not have been.

The corpus-admission contract requires the validation environment to match the
execution environment. It did not.

## What was NOT done

- the case was not dropped
- the preservation set was not edited
- no node was excluded or marked flaky
- the 12/10 threshold was not touched
- no corpus, population or exclusion identity was changed

Per the standing ruling, preflight has veto power and its findings are reviewed
separately rather than repaired in place.

## Frozen state, unchanged

```
src/riftagent                  BYTE-IDENTICAL
benchmark/bm07                 BYTE-IDENTICAL
corpus_manifest_hash           e6bdd3f116981bc58daf7f21eb4a5e0a524e9a067227cd2cc40fc994a19ad3f9
repository_population_hash_v5  4645de61c549bf8ad06697e1b8279ddfee51d19af24379e1dd45880f350fe0bc
exclusion_set_hash             d4090113b0670321b1d5a9c48ebe3949adeb60f865e8b07bb414aea21f137e87
manifest_hash                  55651ba6a755499b72d934da1aeba442b88a2f05b62d98fe7762f009c76d23ef
```

## The decision this needs

Two coherent options, both methodology changes requiring approval:

**Re-validate with network enabled**, so admission and execution share one
environment. This may reject further cases and could drop the corpus below the
12/10 minimum. Recommended: matching the execution environment is exactly the
property that was missing, and finding more unstable cases is the purpose rather
than a cost.

**Isolate repository code from the network** while the provider call goes out
separately. This is a harness change of real substance and would need its own
governance.

Neither is taken here.

## A note on what caught it

The all-case preflight has now blocked two paid runs before spending anything:
once on non-reproducible failure identities, once on an environment mismatch.
Both defects were invisible to every model-free stage that preceded it, because
both were properties of the *execution* environment rather than of the
candidates. That is the argument for mandatory preflight before spend, stated in
outcomes rather than principle.

---

## Resolution (2026-08-23): option two, and it was already implemented

The ruling took the second option — repository code isolated from the network,
provider communication left alone. The "harness change of real substance"
anticipated above turned out to be smaller than expected, because **RIFT already
implements the invariant**: `sandbox.probe_isolation()` selects
`IsolationLevel.FULL` when `bwrap` is usable and wraps every repository child
with `--unshare-net`, while the controller process is never confined.

The reference image simply had no `bubblewrap`. RIFT degraded to
`IsolationLevel.PARTIAL` — *"no filesystem or network confinement"* — and the run
proceeded under `--allow-partial-sandbox`. **The isolation the design provides
was absent because of an image contents gap, not a design gap.** `src/riftagent`
is unmodified.

What was genuinely missing was the benchmark's own subprocesses. The oracle, the
Arm-A weak evaluation, preflight checks and corpus validation all invoke pytest
directly, bypassing RIFT — and those were the paths that reached the live
network. `confinement.py` gives them the same boundary and refuses to run at all
when it cannot be established.

Proven behaviourally with a synthetic socket, not with `dnspython` and not with
a flag:

```
harness confinement (validation / preflight)   DENIED
oracle / truth path                            DENIED
Arm A / weak path                              DENIED
mandatory preflight path                       DENIED
Arm C repository path (RIFT sandbox)           DENIED
controller-side connection                     REACHED
```

The corpus was **not** re-validated with network available. Live DNS, external
HTTP, service availability and remote rate limits must never enter benchmark
truth; the paid path was aligned to admission rather than the reverse. Corpus,
population and exclusion identities are unchanged, `dnspython-246febc4` retains
both divergent preservation nodes, and all 24 cases now pass the full paid-path
preflight — `dnspython` included.

The execution environment became a frozen identity, `execution_environment_hash`,
because this incident proved it is scientifically relevant and no source hash
could express it.
