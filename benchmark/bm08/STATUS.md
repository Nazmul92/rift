# BM-08 status

```
BM-08-v1   CORPUS_INSUFFICIENT     6 cases /  4 repositories
BM-08-v2   CORPUS_SHORTFALL        8 cases /  4 repositories
BM-08-v3   14 / 10 at construction; paid preflight refuted it (2 cases could not
           reproduce their frozen failure identity). Paid execution NEVER STARTED.
BM-08-v4   CORPUS_SHORTFALL       12 cases /  8 repositories
BM-08-v5   THRESHOLD PASS         24 cases / 15 repositories   FROZEN
```

**BM-08 executed 2026-08-24. 48 of 48 official records. Spend $1.8366.**

## Current status: `EXECUTED — OFFICIAL_COMPLETE`

Full result in `RESULT.md`; pre-registration in `PRE-REGISTRATION.md`.

```
                     arm A (model alone)   arm C (full frozen RIFT)
truth-correct        5 of 24               3 of 24
false accepts        0                     0
correct but rejected 0                     0
spend                $0.9384               $0.8982
correct per dollar   5.33                  3.34
```

**Full RIFT did not produce more truth-correct fixes per dollar than the model
alone.** The two divergent cases carry different candidate hashes per arm — each
arm gets its own proposal — so the gap is proposal-side, not gate conservatism,
and at n=24 two cases carry no weight.

Each arm gets one `propose_change` plus at most one schema repair, and registers
exactly one changeset. Repair opportunity is near-symmetric (A 6, C 5; C made 31
total requests to A's 30). But on `parse-e9aa02bd` arm A got a repair round that
C did not and its repaired proposal was the correct one, so that divergence is
confounded. Clean, unconfounded divergences in A's favour: **one, not two**.

The acceptance-authority mechanism was **again not exercised**: arm A accepted 5
candidates and all 5 were truth-correct, so the weak rule made zero false
accepts for a gate to catch. BM-07 found zero weak-versus-strong disagreements;
BM-08 finds zero false accepts. Third failure to test the thesis, same cause.

Same `runtime_hash` as BM-07, same canonicalizer, yet arm C fell from 4 of 6
truth-correct to 3 of 24 while "patch does not apply" rose from 17% to 58%.
Canonicalization fires as often and rescues far less. See
`CANONICALIZATION-FINDING.md` — the pipeline may not generalize beyond the corpus
it was tuned on, or BM-08 may simply be harder; a $0 replay separates them.

```
 29  the patch does not apply to the baseline tree
 10  applies, but the target still fails
  8  truth correct
  1  no proposal produced (output exhausted)
```

39 of 48 arms failed before any acceptance decision was possible. Proposal
representation, not acceptance authority, is the binding constraint on this
corpus.

Disclosed: `isort-49bb9bab` arm A carries no truth record (output exhausted at
the 4000-token ceiling, no proposal produced), so arm A has 23 truth verdicts
across 24 cases. All figures count it as not-correct. `OFFICIAL_COMPLETE`
validates record count and terminal states, not the presence of a truth verdict.

Every arm recorded `partial_sandbox: none` and `level=full, network unshared` —
paid-path confirmation that the environment correction held for all 48.

## Superseded: `READY_FOR_PAID_EXECUTION_AUTHORIZATION`

The v5 executable experiment — 24 cases x arms A and C, 48 official records —
passes the complete paid-path preflight in the corrected execution environment.
It has **not** been authorized to execute and this record does not authorize it.

```
isolation probes            5 of 5 repository paths DENIED, controller REACHED
mandatory preflight        24 of 24 cases pass every check
identity problems           none (full manifest, manifest_hash included)
provider calls                  0
additional spend            $0.00
```

The preflight harness had been filtering `manifest_hash` out of its identity
report unconditionally. That filter belongs only to a spot subset, where the
manifest handed in is a trimmed copy whose recorded hash legitimately cannot
recompute; on the full 24 it suppressed a check that must pass before spend. It
is now scoped to subsets and the evidence above was regenerated with manifest
identity actually verified. The check passed — but it had not been *run*, which
is the fourth time in BM-08 that a harness fault produced self-consistent output
with a missing check inside it.

Two paid runs were previously blocked in preflight, both at $0.00 — first by
non-reproducible failure identities (v3), then by the network-environment
mismatch (v5). Both are resolved; neither cost anything.

### Paid smoke: one case, both arms, $0.0589

Authorized as a de-risking step before the remaining ~$22. **Not the official
experiment**: it carries one case instead of twenty-four, so its `manifest_hash`
is `3793b9e5...` rather than `b0ea08dc...` and its records can never be
aggregated with an official set. Every other identity is carried unchanged.
Written to `results-smoke.jsonl`; the official `results.jsonl` does not exist.

```
case                click-a17b5447  (click, 20 preservation nodes)
arms                A and C, 2 of 2 records, OFFICIAL_COMPLETE
identity_problems   [] on both records
provider            2 requests per arm, provider_reported_model == requested
reservation         $0.4800 per arm reserved -> $0.0258 / $0.0331 settled
sandbox_probed      level=full, "network unshared"
authorities         partial_sandbox: none
total spend         $0.0589 of a $1.00 ceiling
```

`partial_sandbox: none` on both receipts is the paid-path confirmation that the
environment correction is live: RIFT selected `FULL` on its own and needed no
partial-isolation authority. Under the old image this run would have required
`--allow-partial-sandbox`.

**Both arms returned `unverifiable`, and that is an honest outcome rather than a
harness failure.** The model's patch did not apply to the baseline tree —
`git apply --check failed (forward) at every strip level` — so no candidate ever
reached the gate and no preservation node executed. The evidence shows the
protocol ran properly up to that point: Arm C executed 13 commands and 11 checks,
discovered handles, proposed and eliminated 8 hypotheses, emitted a diagnosis,
froze the real baseline signature (`AssertionError`), passed the baseline gate
phase at the frozen `baseline_tree_hash`, and only then rejected the changeset.
Both arms exercised the one permitted schema repair and settled both reservations.

One case is not evidence of a systemic problem, but the failure mode is worth
recording before the full run: it is proposal quality, not acceptance authority.
A corpus-wide repeat would leave BM-08 measuring very little, because a patch
that never applies is rejected by both arms and separates them not at all.

### What changed since `BLOCKED_PREFLIGHT`

The corpus was admitted with repository execution network-denied, and the paid
path ran some evaluation paths with network available. `dnspython`'s live
resolver tests behaved differently in the two environments. The symptom was a
preservation mismatch; the risk was a gate-versus-truth disagreement manufactured
by infrastructure rather than by a candidate.

**The root cause was a missing package, not a design flaw.** RIFT already
implements the required invariant: `sandbox.probe_isolation()` selects
`IsolationLevel.FULL` when `bwrap` is usable and wraps every repository child in
`--unshare-net`, while the controller keeps provider access. The reference image
contained no `bubblewrap`, so RIFT degraded to `PARTIAL` — *"no filesystem or
network confinement"* — and the run proceeded under `--allow-partial-sandbox`.

`src/riftagent` is **not modified**. The environment now supplies what RIFT
already expects. The benchmark's own subprocesses — oracle, Arm-A weak
evaluation, preflight, corpus validation — bypass RIFT entirely and invoke pytest
directly; `confinement.py` gives them the same boundary and refuses to run when
it cannot be established, rather than degrading silently.

The corpus was **not** re-validated with network available. Live DNS, external
HTTP and service availability must never enter benchmark truth; the paid path was
aligned to admission, not the reverse. `dnspython-246febc4` keeps both divergent
preservation nodes and now passes.

### The frozen invariant, proven behaviourally

Proof is an attempted socket, not a flag and not `dnspython`:

```
harness confinement (validation / preflight)   DENIED
oracle / truth path                            DENIED
Arm A / weak path                              DENIED
mandatory preflight path                       DENIED
Arm C repository path (RIFT sandbox)           DENIED   bwrap --unshare-net
controller-side connection                     REACHED
controller process itself confined?            NO       children only
```

Confining the whole controller would satisfy "network denied" and break the
benchmark: Arm C performs provider communication and repository execution inside
one invocation. Confinement belongs to the children.

### Execution environment as a frozen identity

Source hashes pin *what* runs and said nothing about *where*. This incident
proved the difference is scientific, so the environment became an identity of the
same standing as `runtime_hash` — checked before the first provider call and
again before aggregation, and carried in every arm record.

```
execution_environment_hash  9de6886df3d526affb979312048437580a6860ce5491abfd84371c36fed83de3
container image             rift-reference-iso:3.12-slim
image digest                sha256:279f2c1816d32e452951eae861bb3bbb65aad5b3da0ff5035e27a17069a2f8f3
docker flags                --cap-add SYS_ADMIN --cap-add NET_ADMIN --security-opt seccomp=unconfined
python                      CPython 3.12.14
harness confinement         unshare --user --net --map-root-user (util-linux 2.41)
RIFT repository isolation   level=full, bubblewrap 0.11.0, network unshared
network policy              repository-controlled execution DENIED / provider and controller ALLOWED
```

### Identity chain

```
runtime_hash                   75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26   unchanged
driver_hash                    0676ebe54b9dafd032b4005354cdd7f7676dfc32d837d061bebeb8160971eee5
runner_hash                    7d630230847fbb7ad582db90e233a92f2a5ede026bee5edd20437765b0f26b17
oracle_hash                    25ac41827067f0de175b321ac800f6ddd3cadfefbef11da3e713dcc3d518cf91
manifest_hash                  b0ea08dc9ae7fb6c9317260fdc606d3772168570477b3055b0525a759ff7e26b
corpus_manifest_hash           e6bdd3f116981bc58daf7f21eb4a5e0a524e9a067227cd2cc40fc994a19ad3f9   unchanged
repository_population_hash_v5  4645de61c549bf8ad06697e1b8279ddfee51d19af24379e1dd45880f350fe0bc   unchanged
exclusion_set_hash             d4090113b0670321b1d5a9c48ebe3949adeb60f865e8b07bb414aea21f137e87   unchanged
execution_environment_hash     9de6886df3d526affb979312048437580a6860ce5491abfd84371c36fed83de3   new
```

Every one of these appears in every arm record, is checked before spend, and is
rechecked before scoring. Aggregation rejects a mixed-identity result set.

### Budget authority

The previous $15.00 ceiling was **mathematically incapable** of reserving the
official set and would have stranded the run part-way through with money already
spent:

```
derived reservation per arm    $0.4800    unchanged, from the token and price ceilings
official arms                  exactly {A, C}
worst case                     24 x 2 x $0.4800 = $23.04
ceiling                        $25.00
covers worst case              yes
```

`budget_authority_problems()` blocks before the first provider call if a ceiling
cannot cover the worst case. Arms are validated as **exactly** `{A, C}`:
membership alone would have admitted `A,B,C` and silently turned 48 expected
records into 72.

### A RIFT test that only passed because isolation was degraded

Turning isolation on broke one test in RIFT's own M1a suite —
`test_r07_an_interrupt_kills_the_child_process_tree`, the process-tree
termination row. It passes under `partial` and failed under `full`, which is the
signature of a test coupled to the absence of confinement rather than a defect in
what it tests. Three separate causes, all in the test:

```
1  the handshake file was written under `tmp_path`, outside the worktree
   -> correctly denied once the sandbox was real
2  `monkeypatch.setattr(sandbox.subprocess, "Popen", ...)` patches the shared
   `subprocess` module, so `probe_isolation`'s own `bwrap ... /bin/true` check
   consumed the single simulated interrupt
   -> the probe is now taken before the patch
3  liveness was read from the pids the child reports for itself, which are
   namespace-local under `bwrap --unshare-pid` (pgid 1, grandchild 3)
   -> liveness now comes from this process's own /proc, walking the parent chain
```

`src/riftagent` is unchanged and the row is not weakened: the rewritten test
asserts the **whole** descendant set dies rather than one nominated grandchild,
and it passes at both isolation levels. Cause 2 was latent in the reference image
and would have fired on any host that had bubblewrap installed.

### Frozen trees

```
src/riftagent    newest mtime 2026-08-20   runtime_hash verified against the manifest
benchmark/bm07   newest mtime 2026-08-22   content digest 2202e0744d7c6ebe…  asserted by test
```

Every 2026-08-23 modification is confined to `benchmark/bm08/` and `tests/`.

### Files added by this correction

```
benchmark/bm08/confinement.py             one authoritative boundary; refuses rather than degrades
benchmark/bm08/prove_isolation.py         behavioural probes across all five repository paths
benchmark/bm08/execution_environment.py   the environment as a frozen identity
benchmark/bm08/AMENDMENT-ENVIRONMENT.md   rationale, frozen before implementation
benchmark/bm08/preflight-v5-isolated.log  probes + 24/24 preflight evidence
tests/test_bm08_environment.py            25 regressions
```

## BM-08-v5 funnel

```
repository population                        85   (57 previous + 28 v5 expansion)
raw mined candidates                       1905
after prior-exposure exclusion             1727
after author date >= 2018-01-01            1146
after near-duplicate collapse                322   (55 repositories)
repository-resolution preflight         54 of 54   unique, before any candidate
curated                                      262   (54 repositories)
otherwise-valid reaching stability             38
fresh-process observations                    114   (38 x 3)
stable                                         36
unstable_failure_identity                       2
VALID before repository cap                    36   (15 repositories)
removed by <=3/repo cap                        12
final primary cases                            24   (15 repositories)
```

```
150  collection/import/runtime incompatibility
 52  insufficient untouched preservation
 42  other governed validation failure
 16  target already passes at baseline
 14  baseline preservation fails
  8  target-resolution failure
  2  unstable failure identity
  1  reproducer does not apply to parent
  1  historical source fix does not apply
----
post-dedupe 322 = rejected 286 + valid 36   conserved
```

## Why the expansion worked

28 repositories were added for a two-repository shortfall — deliberately broad
rather than targeted. Of those 28, only **14 contributed any post-dedupe
candidate** and only **6 produced a valid case**. Adding the two the arithmetic
called for would almost certainly have failed again.

## The stability rule, independently reproduced a third time

`faker-5128ae64` and `pathspec-b70e3fb4` were rejected again, from a candidate
pool 63% larger, while the other 36 were admitted.

An earlier revision of this file reported the funnel as `36 reaching / 34
stable`, conflating the VALID count with the number of candidates that reached
the stability stage. The evidence is `38 = 36 + 2` with 114 observations; the
corpus itself was never affected, only the reporting. Random generated values and
a process-specific memory address — two mechanisms, three independent
reproductions.

## The 24 frozen cases

15 repositories: charset-normalizer, click, dnspython (3), icalendar, isort,
lark (2), mistune (3), packaging (2), parse, rich (3), schema,
sortedcontainers (2), tabulate, voluptuous, xmltodict.

Every case carries `baseline_tree_hash`, a stable `failure_identity`, and three
identical fresh-process observations.

Categories: edge_case 12, config_interaction 11, parsing 10, small 7,
state_order 5, data_transform 4, api_misuse 4, multi_file 3, inheritance 1.
Recorded, never quota'd.

## Caveat for review

`rich` and `dnspython` produced 11 and 7 valid cases and were each capped to 3.
The corpus is broad at 15 repositories, but its underlying valid yield remains
concentrated in a few cooperative projects; the cap is doing real work to keep
the denominator honest.

## Gates

| | |
|---|---|
| `src/riftagent` · `benchmark/bm07` | **UNCHANGED** (runtime_hash verified; bm07 digest asserted by test) |
| isolation probes | **5 of 5 repository paths DENIED, controller REACHED** |
| all-24 preflight | **24/24 every check; identity problems none, manifest_hash included** |
| BM-08 suites | **119 passed** |
| full suite (isolated image) | **1183 passed, 5 skipped, 0 failed** |
| ruff / format / mypy | clean / 134 files / clean |
| identity chain | **6 of 6 recomputed MATCH, 3 carried — FREEZE VERIFIED** |
| provider calls · spend | **0** · **$0.00** |

---

# BM-08-v3 record — retained as history

Superseded by v5. The current status is at the top of this file.

## BM-08-v3 funnel

```
repository population                        57   (30 previous + 27 v3 expansion)
raw mined candidates                       1302   (48 repositories contributing)
after prior-exposure exclusion             1124   (168 fix_commit + 10 parent seen)
after author date >= 2018-01-01             651   (473 excluded by the floor)
after near-duplicate collapse                198   (34 repositories)
repository-resolution preflight        34 of 34   unique, before any candidate
curated into runnable cases                 167   (33 repositories)
submitted to model-free validation          198
VALID before repository cap                  22   (10 repositories)
removed by post-validation <=3/repo cap       8
final primary cases                          14   (10 repositories)
```

## Rejection accounting

```
107  collection/import/runtime incompatibility
 27  insufficient untouched preservation
 19  other governed validation failure
 10  baseline preservation fails
  9  target already passes at baseline
  4  target-resolution failure
----
post-dedupe 198 = rejected 176 + valid 22   conserved
```

`direct_parent_invalid` is **0**, and repository resolution appears nowhere in
this breakdown: it blocks the run in preflight and is excluded from the
taxonomy by explicit comment. Both facts are consequences of the invalidated
run below.

## The invalidated first v3 attempt

```
INVALIDATED_INFRASTRUCTURE_RUN     5 cases / 4 repositories — not a scientific outcome
```

> Newly added repositories were mined correctly, but downstream curation and
> validation resolved only the old repository root, so the run did not actually
> evaluate the frozen v3 population.

Every new-repository candidate pointed at a nonexistent directory. Git failing
against a missing path is indistinguishable — to that code — from a genuine bad
parent, so 147 candidates were filed under `direct_parent_invalid`, a real
governed reason, and the conservation identity still balanced at
`198 = 193 + 5`. **The accounting was self-consistent and wrong.**

Reported as-is, `5 cases / 4 repositories` would have read as a plausible
shortfall slightly worse than v2 and supported the conclusion that repository
expansion does not help. That conclusion would have been fabricated by a path
bug. Raw evidence retained in `v3-discarded-run.log`.

The lesson was not "fix the path". It was that an infrastructure failure must
never be able to wear a scientific reason's clothes — hence `repo_resolution.py`
and the fatal preflight.

## The 14 frozen primary cases

| case | repository | author date | preservation | failure identity |
|---|---|---|---|---|
| `voluptuous-44593ce7` | voluptuous | 2026-07-10 | 174 | TypeError |
| `rich-260508bf` | rich | 2021-12-14 | 22 | AssertionError |
| `rich-0138b189` | rich | 2020-11-27 | 13 | AssertionError |
| `rich-a5eada7a` | rich | 2021-05-19 | 4 | AssertionError |
| `isort-49bb9bab` | isort | 2020-10-12 | 46 | AssertionError |
| `tabulate-293ff59a` | tabulate | 2021-08-19 | 29 | Failure |
| `faker-5128ae64` | faker | 2022-03-23 | 23 | AssertionError |
| `click-a17b5447` | click | 2018-05-14 | 20 | AssertionError |
| `packaging-553582d9` | packaging | 2026-03-18 | 72 | AssertionError |
| `icalendar-9b72b0b1` | icalendar | 2021-10-15 | 24 | TypeError |
| `sortedcontainers-2b037039` | sortedcontainers | 2020-06-03 | 53 | Failure |
| `pathspec-b70e3fb4` | pathspec | 2026-03-06 | 89 | AssertionError |
| `sortedcontainers-7dc426c9` | sortedcontainers | 2020-11-04 | 123 | Failure |
| `packaging-f8f16338` | packaging | 2026-03-08 | 10 | Failure |

`baseline_tree_hash` 14/14 · `failure_identity` 14/14, captured through
`checks.run_check` — the same component the gate calls at execution.

Categories: edge_case 6, config_interaction 6, small 5, state_order 3,
parsing 3, multi_file 2, api_misuse 2, inheritance 1. Recorded, never quota'd.

## Two caveats a reviewer should weigh

**`rich` produced 11 of the 22 valid cases** and was capped to 3. The corpus
clears 10 repositories partly because one project was unusually amenable to the
frozen environment. The margin above the minimum is two cases and zero
repositories.

**107 of 176 rejections remain Python-3.12 collection failures** despite the
2018 floor. The environment, not the selection rule, is still the dominant
filter on this corpus.

## Gates

| | |
|---|---|
| `src/riftagent` | **BYTE-IDENTICAL** |
| `benchmark/bm07` | **BYTE-IDENTICAL** |
| BM-08 suites | **60 passed** |
| full suite | **1124 passed, 5 skipped, 0 failed** |
| ruff check / format / mypy | clean / 121 files / 8 source files |
| provider calls | **0** |
| additional spend | **$0.00** |

---

# BM-08-v1 record — retained as history

Everything below documents the superseded v1 pass. It is kept verbatim per the
amendment's "do not rewrite v1 history" clause. Its counts describe the
era-blind pipeline and are **not** the current result.

## The harness fork, and why BM-07 was not edited

The one approved harness change was evidence retention: BM-07 summarised each
arm's ledger and candidate diffs into a result record, then deleted the
worktree, so the material the summary was derived from existed only for the
length of the run.

Applying that to `bm07_runner.py` in place would have changed `runner_hash`, and
BM-07's own aggregation would then have rejected its own frozen evidence as *"the
orchestration program changed after execution"* — destroying the verifiability of
a paid result. So the harness was **forked** to `benchmark/bm08/`, exactly as
`bm06/driver.py` and `bm07_driver.py` already coexist.

BM-07 is asserted byte-frozen by test, not by claim: `runner_hash` still
`d1b2fd31…`, and its aggregation still accepts all 18 records with zero drift.

Retained per arm, before the worktree is destroyed:

```
case/arm/
    ledger.jsonl
    raw.diff
    normalized.diff
    canonical.diff
    result.json
```

Copied, never moved, and never able to fail a paid arm — losing evidence is bad,
failing a paid arm because a copy failed is worse. `result.json` is written
*after* the append-only results file, which remains the authority.

## Pipeline, stage by stage

```
eligible pool                       553   (23 of 30 repositories)
after excluding previously-seen     375
after near-duplicate collapse       120
after repository cap (3/repo)        55   (22 repositories)
curated into runnable cases          42   (21 repositories)
model-free validated                  6   (4 repositories)
```

### Previously-seen commits: 30% of the pool

The miner scans the same repository volume every prior benchmark scanned, so
"newly mined" does not mean "unseen". A frozen exclusion set was harvested from
every benchmark artifact in the tree — BM-06, BM-07, the frozen pilot, the
abandoned freeze, the invalidated run, every mined and shortlisted pool, and the
test fixtures — taking every 40-character hex object id at any depth.
Deliberately over-broad: dropping a usable candidate costs one case, missing one
silently converts a reused bug into a "previously unseen" result.

```
frozen exclusion set                          2707 commits
candidates whose fix_commit was already seen   168   (30% of the pool)
  ...of which BM-07 official cases               6
  ...of which BM-06 cases                        1
candidates whose parent was already seen        10
repositories with some overlap                  22 of 23
```

Without this, BM-08 would have claimed evaluation on unseen bugs while partly
re-running BM-07's own corpus.

### The recency cap was removed, not disclosed

An earlier miner stopped after 40 eligible commits per repository. The walk is
newest-first, so five large projects contributed only recent history while small
ones contributed all of theirs, and deterministic ordering afterwards would have
been shuffling an already-biased pool. Removing the cap took the pool from 466 to
553; click 40→76, werkzeug 40→70, jinja 40→50, marshmallow 40→48, faker 40→43,
**every other repository unchanged** — confirming only those five were truncated.

## Why validation yielded 6

```
27 of 36 rejections   pytest collection error at parent (exit 4)
 6                    preservation already failing on the buggy baseline
 3                    target already passes at parent
```

Yield splits sharply by era:

```
2019 or later    3 validated of  8   (38%)
before 2019      3 validated of 34   ( 9%)
```

The predeclared SHA-256 ordering is deterministic but **era-blind**, so the queue
filled with 2007–2018 commits that cannot import under Python 3.12 —
`@asyncio.coroutine`, packaging predating `importlib.metadata`, syntax the
interpreter no longer accepts. The frozen reference environment is a fixed
constraint; those commits were never runnable in it. This is an environment
property, not a statement about bug quality.

## The validated six

| case | date | category | preservation | subject |
|---|---|---|---|---|
| `click-9da17914` | 2015-03-31 | config_interaction | 25 | Error on nargs=-1 for options |
| `click-b964c1e2` | 2020-10-17 | api_misuse | 22 | handle case_sensitive=False when completing |
| `click-b6256540` | 2014-07-08 | parsing | 4 | Fixed a regression for echo_via_pager |
| `croniter-18104ccf` | 2015-05-05 | data_transform | 26 | Fixed #40 converting sun in range |
| `faker-5128ae64` | 2022-03-23 | multi_file | 23 | Fix factory selection when Faker has been… |
| `packaging-553582d9` | 2026-03-18 | small | 72 | fix(metadata): charset error message |

Four repositories, three of the six from `click`. This is **not** a usable
efficiency corpus: it is smaller than BM-07 and less diverse.

## What is available if the rule is amended

No arm has run, so **no A-versus-C outcome information exists anywhere**. A
corpus change now cannot be outcome-tuning; it can only be validity-driven. That
is materially different from amending after seeing results, and it is the only
reason an amendment is worth proposing at all.

Adding an era floor to the post-exclusion pool of 375:

| era floor | after collapse | 3/repo | 5/repo |
|---|---|---|---|
| ≥2018 | 51 | 28 cases, 13 repos | **40 cases, 13 repos** |
| ≥2019 | 44 | 26 cases, 11 repos | 34 cases, 11 repos |
| ≥2020 | 39 | 23 cases, 11 repos | 31 cases, 11 repos |

At the observed 38% modern-era yield: ≥2018 with 3/repo projects to ~11
validated; ≥2018 at 5/repo to ~15; validating the whole ≥2018 collapsed set (51)
to ~19. Reaching 20 needs both the era floor and a wider cap, and is still
marginal.

**What must not be relaxed**: the exclusion set and the near-duplicate collapse.
Neither is limiting yield, and they protect the unseen-bug claim and the
one-bug-one-case property respectively.

## Disclosed limitations

- **23 of 30 repositories** produced eligible candidates; boltons, chardet,
  jsonschema, markdown, more-itertools, pyparsing and soupsieve yielded none.
  The corpus represents the survivors, not the volume.
- Eligibility is commit-message based and approximate in both directions: broad
  terms (`handle`, `error`, `raise`) admit some feature work, and requiring a new
  test function excludes real bugs fixed by modifying an existing test.
  Model-free validation, not the message, decides eligibility.
- Six source checkouts (`attrs`, `click`, `filelock`, `icalendar`, `packaging`,
  `pyparsing`) carry staged edits from earlier work. Baselines use
  `git clone --shared` plus `checkout --force <parent>`, which take committed
  objects only — **asserted by regression test**, not by argument.
- BM-08 measures fixes-per-dollar. It does **not** test the false-fix prevention
  mechanism, and must not be bent toward it. That remains a separate study.

## Files

```
SELECTION-RULE.md     predeclared, written before the pool was inspected
mine_corpus.py        stage A — eligible pool
build_exclusion_set.py  the frozen 2707-commit exclusion set
select_corpus.py      stage B — the rule, applied and reported stage by stage
curate_queue.py       stage C — target nodes and preservation sets
validate_cases.py     stage D — model-free reproduction
pool.json queue.json cases.json validated.json exclusions.json validation.log
bm08_runner.py bm08_driver.py bm08_oracle.py curation.py
```

## Status of the v1 record (historical)

`BM08_CORPUS_INSUFFICIENT` — pipeline complete and green, corpus below target.
Awaiting a decision on whether to amend the predeclared selection rule.

**Superseded.** The one authoritative current status is at the top of this file.
