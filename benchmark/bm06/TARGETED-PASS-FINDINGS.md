# BM-06 targeted curation pass — protocol infeasibility

Appended, not merged. `STAGE2-WIDE-FINDINGS.md` and every record it cites stay
byte-identical; their hashes are in `frozen-evidence-hashes.json`.

Model-free. No provider request, no spending, no benchmark arm.

## Result

**150 targeted confirmations attempted, the full authorized budget. Zero
accepted.** Two halves, both retained: `targeted-records.json` (82 attempts) and
`targeted-records-2.json` (68).

| class | target | confirmed | attempts |
|---|---|---|---|
| missing_dependency | +4 | 0 | 64 |
| two_cause | +2 | 0 | 51 |
| nondeterminism | +1 | 0 | 35 |

### The first half tested my harness, not the corpus

Of its 82 attempts, **67 were harness-limited and 56 were rejected for "no test
file in the commit"** — the three targeted paths required a candidate to carry
its own test file, while the widened round routinely confirmed source-only
commits through the repository's existing suite. Omitting that fallback would
have turned a gap in this harness into a reported fact about the repositories.

The fallback was added and the remaining 68 confirmations were spent with it.
The first half is retained as the evidence for why.

### The second half tested the corpus, and the answer is still zero

`missing_dependency`, 32 attempts with per-parent environment reconstruction:

| reason | count | nature |
|---|---|---|
| no collectable node ids in the reconstructed environment | 13 | environment |
| the parent suite could not be run in the reconstructed environment | 5 | environment |
| the parent commit's own package will not install | 5 | environment |
| pytest could not be made available in the reconstruction | 2 | environment |
| **no missing-dependency signature at the parent that the fix removed** | **7** | corpus |

Seventy-eight per cent of the class is still unobservable *after* building each
candidate's environment from its own parent commit's declarations. The obstacle
is now precisely named: decade-old Python packages do not rebuild from their own
historical declarations under Python 3.12. Remedying that needs pinned
interpreters and per-era toolchains — a substantially larger build than this
pass, and not started.

`two_cause` and `nondeterminism` failed on their own criteria rather than on
environment limits: no candidate produced two targets with distinct signatures
failing jointly at a parent, and none reached the frozen 2-of-5 failure
threshold. Both criteria were frozen before execution.

## The finding that matters most: the criterion cannot see a feature

Four of the 36 confirmed cases are **feature additions**, not repairs:

- `tenacity 21137e79` — "Add async strategies (#451)"
- `pluggy 6f6ea680` — "Add support deprecating hook parameters"
- `filelock 03b0ab7e` — "feat(unix): delete lock file on release (#408)"
- `pygments 9fca2a10` — "Add analyze_text to make make check happy (#1549)"

Parent-fail → fixed-pass is satisfied by any feature that ships with a test: the
test fails at the parent because *the feature does not exist there*. Nothing in
the confirmation can distinguish that from a defect being repaired, because
nothing about it is different at the level the confirmation observes.

BM-06 measures repair. A benchmark that asks an agent to "fix" a missing feature
is measuring something else, and it would have measured it silently. All four are
withdrawn as `GROUND_TRUTH_INVALID`, and any future curation needs a filter for
this before confirmation rather than after.

## Applied label review

Every one of the 36 confirmed cases was reviewed. **16 survive, 20 are
withdrawn** — 4 `GROUND_TRUTH_INVALID`, 3 `GROUND_TRUTH_DISPUTED`, 13 reassigned
or rejected as not exemplars of their class. Decisions and rationales are in
`label-decisions.json`.

A reassignment is recorded but **never applied as a promotion**. A case reaches a
class by being discovered and confirmed for it, not by being moved there to fill
a quota — so `click 93c6966e` reading as `genuine_source_bug` withdraws it from
`order_dependence` and adds nothing anywhere.

The commonest withdrawal reason is that a marker matched incidental wording:
"Fix UnboundLocalError in click.Path" filed under `locale_timezone`, a wrong
Danish BBAN length constant filed under `order_dependence`. Commit-message
discovery finds candidates; it does not classify them.

## Proposed manifest

`manifest-proposed.json`, `manifest_hash`
`4c06e87777e3c84e8e217370db777e16ea334ad6570c57c81d9548723b7a3813`.

**15 cases across 9 repositories. NOT FROZEN.**

| class | allocated | in manifest |
|---|---|---|
| genuine_source_bug | 5 | 6 |
| locale_timezone | 3 | 3 |
| version_mismatch | 4 | 3 |
| order_dependence | 4 | 2 |
| state_leakage | 4 | 1 |
| missing_dependency | 4 | 0 |
| nondeterminism | 3 | 0 |
| two_cause | 3 | 0 |

Also failing: 9 repositories against the minimum of 10, and `order_dependence`
present in 1 repository against the minimum of 2. The 4-per-repository cap binds
on filelock and icalendar, displacing one further state_leakage case.

Two of eight classes meet allocation. Half the manifest is `genuine_source_bug`
and `locale_timezone`.

## Conclusion: protocol infeasibility

The protocol requires 30 cases across eight cause classes, each confirmed by
executed parent-fail → fixed-pass evidence with a reviewed label. Against 1,676
candidates from 30 repositories, 559 confirmation attempts and a corrected
harness, it yields **15**.

Three classes cannot be filled by this method:

- **missing_dependency** — blocked by historical environment reconstruction,
  not by scarcity. 78% unobservable even with per-parent environments.
- **two_cause** — no commit produced joint executed evidence of two causes.
  Combining two separately observed handles into a conjunction nobody executed
  is forbidden, and correctly so.
- **nondeterminism** — no candidate reached the frozen intermittency threshold.

This is reported as infeasibility of the protocol as specified, not as a
property of RIFT. **No benchmark arm has run.** Nothing here measures the
runtime, and nothing here should be read as evidence about it.

No class was substituted, no fixture manufactured, no confirmed case relabelled
to fill a quota, and no rejected candidate discarded without a recorded reason.
