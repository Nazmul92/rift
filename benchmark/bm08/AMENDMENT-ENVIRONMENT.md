# BM-08 environment-equivalence amendment

Written and frozen **before** any corrected preflight was executed.

## Rationale — frozen text

> BM-08-v5 corpus admission established validity under repository network-denied
> execution. Mandatory paid-path preflight revealed that some benchmark
> evaluation paths executed repository tests with network available. This
> produced environment-dependent preservation behavior in dnspython and also
> exposed a potential gate-versus-truth confound. No provider request had
> occurred and no A/C model outcome exists. The correction aligns paid execution
> to the already-established admission environment rather than changing corpus
> admission.

## The frozen invariant

```
all repository-controlled execution   network DENIED
provider / controller communication   network ALLOWED
```

> Failure to prove repository network isolation is infrastructure failure and
> blocks the benchmark; it is not a candidate rejection.

## Root cause: a missing package, not a design flaw

RIFT already implements this invariant. `sandbox.probe_isolation()` selects
`IsolationLevel.FULL` when `bwrap` is usable and wraps every repository child
command with `--unshare-net`, while the controller process itself is never
confined — exactly the required shape.

The reference image had no `bubblewrap`. RIFT therefore degraded to
`IsolationLevel.PARTIAL` — *"no filesystem or network confinement"* — and the
benchmark proceeded under `--allow-partial-sandbox`. The isolation the design
provides was silently absent because of an image contents gap.

`dnspython`'s `LiveResolverTests` then behaved one way during network-denied
admission and another during network-available paid preflight. That is the
observable symptom; the confound it threatens is worse: an oracle or weak-path
evaluation reaching the live network could disagree with a network-denied gate
for reasons that have nothing to do with the candidate, manufacturing a
gate-versus-truth result out of infrastructure.

## The boundary

Network-denied, without exception:

```
corpus validation subprocesses
failure-identity observation subprocesses
mandatory preflight pytest/checks
Arm A target and preservation evaluation
weak evaluation
independent oracle / truth evaluation
Arm C repository commands
any benchmark-issued pytest or repository subprocess
```

Network-allowed:

```
provider adapter / provider HTTP
controller operations that are not repository-controlled execution
```

**The whole controller is never network-disabled.** Arm C performs provider
communication and repository execution inside one invocation; confining the
whole process would block the provider, and relaxing repository confinement to
restore it would reinstate the defect. Confinement belongs to the children.

## Mechanism

```
Arm C repository commands      RIFT's own bwrap --unshare-net (IsolationLevel.FULL)
benchmark-side subprocesses    harness confinement via unshare --user --net
```

`src/riftagent` is not modified. The correction is achieved by supplying the
environment RIFT already expects.

## Execution-environment identity

Because the execution environment is now demonstrably scientifically relevant,
it becomes a frozen identity — `execution_environment_hash` — binding the
container image digest, Python version, isolation mechanism and version, and the
network-isolation configuration. It is recorded in preflight evidence and in
every future arm result, checked before the first provider call and again before
official aggregation. Source-code hashes alone cannot express it.

## What does not change

The v5 corpus, its 24 cases, `dnspython-246febc4` and its two divergent
preservation nodes, preservation sets, failure-identity semantics, the N=3
stability contract, selection and validation rules, the `<=3` per-repository cap
and the 12/10 threshold are all untouched.

Admission remains **network-denied**. The corpus is not revalidated with public
network available: live DNS, external HTTP, service availability, remote rate
limits and internet state must never enter benchmark truth. The paid path is
aligned to admission, not the reverse.

Oracle independence is preserved — only its repository execution environment
changes, never its correctness criteria. Arm A remains the frozen model-alone
baseline and is not strengthened into a verifier.
