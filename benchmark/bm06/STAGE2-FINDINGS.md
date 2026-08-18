# BM-06 stage 2 — case confirmation results

Model-free. No provider request was made and nothing was spent. Every attempted
candidate has a durable record in `stage2-records.json` (commit-level pass) and
`stage2-records-merged.json` (with merge recovery), each carrying repository,
parent and fix SHA, container image, commands, target, outcomes, signature, and
the exact accept or reject reason.

## Result

**116 candidates attempted, 10 confirmed.**

| cause class (stage-1 label) | confirmed | repos |
|---|---|---|
| version_mismatch | 6 | 5 |
| order_dependence | 2 | 2 |
| missing_dependency | 1 | 1 |
| state_leakage | 1 | 1 |
| locale_timezone, nondeterminism, two_cause, genuine_source_bug | 0 | 0 |

Against the manifest requirement — 30 cases, at least five repositories, all
eight classes, four order-dependent cases across at least two repositories —
only the repository count is met.

## Why candidates were rejected

| reason | count |
|---|---|
| the parent's own suite is green, so the commit has no reproducer | 75 |
| every node failing at the parent still fails at the fix | 28 |
| a node the fix repaired does not pass in isolation at the fix | 3 |

The dominant reason is a property of how these projects work, not of the
harness: a fix and its regression test are usually separate commits. That is
what the merge-recovery pass was built to address.

## Merge recovery: attempted, and it found nothing

For each rejected candidate the pass re-evaluated the merge that shipped it —
the pull request as the project reviewed it, where both halves are together.
**51 valid shipping merges were evaluated. Zero produced a case.** 55 candidates
have no shipping merge at all: attrs and pyparsing rebase rather than merge (15
and 58 merges across 1,837 and 1,722 commits).

Each recovered record keeps the commit-level rejection in
`commit_level_attempt`, so the two passes cannot be read as independent evidence.

## Three harness defects found and corrected during this stage

These are recorded because each one had produced a full set of confident,
wrong results before it was caught.

1. **Promisor clones.** Stage 1 cloned with `--filter=blob:none`. With egress
   cut for test execution, git cannot fetch a missing blob, so every checkout
   failed — 43 candidates rejected for "could not check out the fix commit",
   none of which was about the candidate. Fixed by unsetting
   `remote.origin.promisor` and `partialclonefilter`, then `git fetch --refetch`.

2. **Plugin autoload disabled.** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` looked like
   extra hermeticity. attrs needs hypothesis, jinja needs trio, werkzeug needs
   pytest-xprocess; those suites collected nothing and every candidate in them
   was rejected. Isolation belongs to the container — no network, no home, no
   credentials — not to crippling the runner. The install phase now installs
   each project's own declared test dependency group or extra, recorded per
   repository in `install-report.json`. All ten suites now collect.

3. **Wrong merge unit.** The first recovery pass took the oldest merge on the
   ancestry path to `HEAD`. For a commit made directly on the mainline, the
   merges that follow it are unrelated — so 32 of 83 evaluations scored *another
   pull request's* diff under this candidate's SHA and label. Corrected by
   requiring that the commit is not already an ancestor of the merge's first
   parent, and the records were regenerated rather than edited.

A fourth defect, in the confirmation criterion itself, is worth naming: the
first source-only implementation required the target to fail **in isolation** at
the parent. A test that fails only inside a suite run and passes alone is not a
bad candidate — it is the definition of order dependence, the class BM-06 most
needs. Such cases are now accepted with an explicit
`ordering_precondition: "full suite in declared collection order"`.

## What this does not establish

Stage 2 establishes reproduction. It does **not** establish the cause class,
which came from stage 1's keyword match on commit messages and is unverified.
See `label-review.md`: on the evidence, roughly half the confirmed cases appear
mislabelled and one is unstable as a reproducer. Confirmed count and class
coverage are separate claims and are reported separately.

## Conclusion

The shortfall is real and is not an artifact of the harness. Ten repositories
yielded ten cases; the binding constraint is that most fixes ship without a
same-unit regression test. Reaching 30 confirmed cases across eight classes
would require widening stage 1 to substantially more repositories, not applying
more pressure to these ten. No class was substituted, no fixture was
manufactured, and no rejected candidate was discarded without a recorded reason.
