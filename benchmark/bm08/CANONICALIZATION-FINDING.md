# BM-08 canonicalization cleared the observed representation defects; the remaining non-applicability is source/context/path related

**POST-HOC CANONICALIZATION REPLAY — NOT AN OFFICIAL BENCHMARK RERUN.**

**No raw-applicable patch was transformed, so an empirical damage rate is not
estimable from this replay.**

Evidence: `canonicalization-replay.json`, from `canonicalization_replay.py`
replaying the exact retained `raw.diff`, `normalized.diff` and `canonical.diff`
bytes of 47 BM-08 arms through `git apply --check` — each stage against its own
fresh copy of the frozen baseline, `tree_hash` re-verified before every check.
No provider call, no spend, no change to RIFT or to any measured result.

## Measured

```
47 complete replay records            1 skipped: isort-49bb9bab/A

raw        applicable   5    non-applicable  42
normalized applicable   5    non-applicable  42
canonical  applicable  18    non-applicable  29

RESCUED      13     rescue rate  13/42 = 31.0%
PRESERVED     5
UNRECOVERED  29

canonical-stage failures
  26  context_mismatch
   3  file_not_found_or_wrong_path
   0  representation-level
```

Verified alongside: `raw_hash` and `canonical_hash` agree with the official
records for all 47; `baseline_tree_hash` verified for every replayed case;
`canonical_apply_ok` agrees with the official `ground_truth.applied` for all 47.

## Damage is not estimable, and the earlier claim was vacuous

An earlier revision reported `damage_rate = 0/5 = 0%` and called canonicalizer
damage "refuted". That was wrong in a way worth naming: it implied five patches
were transformed and survived. **None were transformed.**

```
raw-applicable candidates                  5
raw-applicable candidates transformed      0
damage opportunities                       0
damage rate                                N/A
```

All five are byte-identical across raw, normalized and canonical. The governing
implementation behaviour is explicit in `canonicalize_patch`:

> A raw patch that Git structurally accepts is returned unchanged.

So the five `PRESERVED` cases are instances of that early-return guard, not
empirical evidence from transformed raw-applicable patches. A damage rate needs
a transformed raw-applicable patch to exist, and this replay contains none.

## Rescue, unchanged

```
RESCUED = 13     raw failures = 42     rescue rate = 13/42 = 31.0%
```

All 13 are demonstrated from actual `git apply --check` transitions, not from
hash inequality.

## The representation boundary

```
31 raw candidates showed representation-level failure

after canonicalization
   0 representation-level failures remained
  13 became applicable
  18 remained non-applicable because an additional source/context/path
     defect was exposed
```

**Canonicalization cleared all observed representation-level defects in the
retained BM-08 candidate set.** It did not "fully repair" all 31 candidates: for
18 of them a second, independent defect was underneath. Repairing the first
exposed the second; it did not create it.

Every one of the 18 is `context_mismatch` at the canonical stage — none is still
a representation failure.

## Normalization contributed nothing to applicability

```
RAW FAIL -> NORMALIZED PASS          0
RAW PASS -> NORMALIZED FAIL          0
NORMALIZED FAIL -> CANONICAL PASS   13
NORMALIZED PASS -> CANONICAL FAIL    0
```

`raw != canonical` in 62–70% of candidates was true and told us nothing: hash
inequality proves a transformation happened, not that it helped.

## BM-07 comparison: asymmetric evidence, not a matching rate

BM-07's raw and normalized bytes do not exist. `bm07_runner.py` read the stage
files from the task directory, recorded their hashes, and let the worktree be
destroyed — the gap BM-08's evidence-retention fork closed.

**BM-07 rescue and damage rates cannot be reconstructed from retained artifacts,
so comparative transfer rates across BM-07 and BM-08 are not measurable.**

The residual diagnostics are also not equally informative. BM-08's come from a
fresh `git apply --check --verbose`, e.g.:

```
error: while searching for:
    ...
```

BM-07 retained only weaker historical strings:

```
patch failed: file:line
patch does not apply
```

> BM-07 and BM-08 residual diagnostics may pass through the same classification
> logic, but BM-07 retained materially less diagnostic detail. Fine-grained
> cross-benchmark failure-class comparisons are therefore asymmetric.

The safe BM-07 statement is: **no retained evidence of representation corruption
remained in BM-07 canonical residue.** No BM-07 context-mismatch *rate*
comparable to BM-08's is inferred here.

## Coverage, stated narrowly

**No representation-level coverage gap was observed among the BM-08 candidate
defect shapes exercised in this replay.**

That is not "coverage gap ruled out", and it is not a global refutation of
corpus-specific tuning. The canonicalizer was written after observing BM-07
outputs, so that concern remains open; this replay simply provides no evidence
for it among the shapes BM-08 exercised.

## Symmetry, distribution, shape

Per-arm rescue rates are close — arm A 6/21 (28.6%), arm C 7/21 (33.3%) — with
zero damage opportunities on either side, so none of this explains the measured
5-versus-3 outcome between the arms.

Failure is broadly distributed rather than driven by one repository: `mistune`
6 of 6 unrecovered, `dnspython` 5 of 6, against `rich` with 4 of 6 rescued. No
causal claim is made from repository frequency alone.

Canonical patches that applied and those that failed are both 100% single-file;
failures carry slightly more hunks (1.76 vs 1.28) and bytes (812 vs 617).

## What this implies

The residual constraint is source/context/path correctness — whether the model
quotes and locates source that actually exists — not patch representation. That
question is taken up by the separate exploratory probe under
`benchmark/analysis/source_recall_probe/`, which is not BM-08, not BM-09, and
not official benchmark evidence.

**This file does not change the measured BM-08 result** — arm A 5 of 24, arm C
3 of 24 truth-correct, $1.8366. Any applicability figure here is POST-HOC and
counterfactual, never benchmark performance.

## Revision history

The first revision of this file over-claimed in two ways, both corrected above:
it reported a vacuous `damage_rate = 0/5 = 0%` and called damage "refuted", and
it treated BM-07 and BM-08 residual failure classes as equivalently observed
when BM-07's retained diagnostics are materially weaker. The provisional wording
that preceded both is retained below.

---

# SUPERSEDED — provisional wording, retained as history

Raised after BM-08 executed, from a comparison the BM-08 result does not make on
its own. It is a finding about the **pipeline**, not about either arm, and it
does not change any BM-08 number.

## The observation

BM-07 and BM-08 ran against the **same frozen RIFT**:

```
BM-07 runtime_hash   75196d8756b749d6…
BM-08 runtime_hash   75196d8756b749d6…      identical
```

The runners are the same too. Diffing `bm07_runner.py` against `bm08_runner.py`,
the only differing lines that touch repair, canonicalization or attempts are the
four in the evidence-retention copy loop — the one approved fork difference.
The repair loop and the canonicalizer are the same code.

The outcomes are not:

```
                      truth correct     patch does not apply
BM-07 arm C            4 of 6  (67%)     1 of 6  (17%)
BM-08 arm C            3 of 24 (13%)    14 of 24 (58%)
BM-08 arm A            5 of 24 (21%)    15 of 24 (63%)
```

Canonicalization is not idle in BM-08. It fires at almost the same rate:

```
BM-07 all arms     13 of 18 candidates changed by canonicalization (72%)
BM-07 arm C         5 of 6                                         (83%)
BM-08 arm C        15 of 24                                        (62%)
BM-08 arm A        16 of 23                                        (70%)
```

**It engages as often and rescues far less.** Truth evaluates the canonical
candidate in every arm of both benchmarks, so this is not a case of the pipeline
being bypassed.

## What this rules out

It is not a change in model coding ability, and it is not a RIFT code change:
the model is the same and the runtime hash is identical. It is also not an
asymmetry between the arms — within BM-08 both arms fail to apply at
indistinguishable rates (58% and 63%), so it explains nothing about arm A
scoring above arm C.

## What remains confounded

Two explanations predict exactly this observation and the existing evidence
cannot separate them:

1. **The canonicalizer does not generalize.** It was developed and validated
   against BM-06 and BM-07 candidates — the DAR-030/031/032 work, whose replay
   matrix recovered 9 candidates through the full gate. BM-08 draws on 15
   repositories, 14 of which BM-07 never touched. A normalizer tuned on one set
   of diff shapes may simply not fit others.

2. **The BM-08 corpus is harder.** It was built under different rules — a 2018
   author-date floor, a 2707-commit prior-exposure exclusion set, near-duplicate
   collapse, and a `<=3` per-repository cap — explicitly to be broader and
   previously unseen. Broader and unseen may just mean harder to patch.

Stating a preference between these without evidence would be exactly the kind of
plausible-but-unverified claim this project has repeatedly caught.

## The test that separates them, at $0 and no provider call

Replay BM-07's retained raw candidates through the **current** canonicalizer and
`git apply --check` against BM-07's own baselines:

```
if BM-07's candidates still apply at ~83%   -> the pipeline is intact and
                                               BM-08's corpus is harder
if they now apply at ~40%                   -> the pipeline regressed
```

BM-07's per-arm evidence is retained and its baselines are reconstructible from
its frozen manifest, so this needs no model, no network and no spend. It is a
replay of existing bytes, in the same class as the DAR-032 replay matrix that
already exists in this tree.

**Not run here.** It is a new experiment and BM-08's result is frozen.

## Why it matters more than the BM-08 headline

If explanation 1 holds, the reported BM-07 figure of 4 of 6 truth-correct for
full RIFT is inflated by a component tuned on the corpus it was measured on, and
BM-08's 3 of 24 is closer to the honest out-of-sample number. That would make
BM-08's contribution larger than "the efficiency comparison came out
unfavourable": it would be the first measurement of how much of RIFT's apparent
capability was corpus-specific.

If explanation 2 holds, the pipeline is fine and BM-08 simply selected harder
bugs — in which case the acceptance-authority thesis is still untested, for the
same reason it was untested in BM-07, and the corpus needs redesigning around
cases where proposals are good enough for acceptance to be the binding question.

Either way the next step is a replay, not another paid run.
