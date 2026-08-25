> **HISTORICAL BM-08-v1 RULE — SUPERSEDED**
>
> This file records the original v1 selection rule as it was written, and is
> retained unmodified as history. It is **not** the operational rule.
>
> Operational BM-08 corpus rules are defined by:
>
> * `AMENDMENT-V2.md` — author-date eligibility floor, eligibility before
>   deduplication, repository cap after validation, frozen minimum denominator;
> * `AMENDMENT-V3.md` — repository-population expansion, all v2 rules unchanged.
>
> Where this file and the amendments disagree, the amendments govern. Nothing
> below has been altered to match them: silently mutating a superseded rule
> would destroy the record of what was actually predeclared at the time.

# BM-08 selection rule — predeclared

Written **before** the full-history pool was inspected and before any candidate
was validated. It is frozen here so the corpus cannot be tuned to the outcome:
if the rule produces an awkward corpus, the corpus is awkward.

BM-07's corpus was chosen *for* RIFT's verification mechanism, which made it a
fair mechanism study and an unusable efficiency benchmark. BM-08 must not repeat
that. Nothing below scores preservation surface, shortcut room, adjacent test
coverage, or anything else correlated with the property under test.

## The question

Across ordinary real-world Python bugs, does full RIFT (arm C) produce more
truth-correct fixes per dollar than the same model alone (arm A)?

## 1. Eligibility (mining, already applied)

A candidate is eligible if, from its own history alone:

- the commit message reads as a bug fix and not as docs, style, lint, rename,
  refactor, packaging or a version bump;
- it modifies 1–5 Python source files, 1–60 added lines, ≤60 removed;
- it also modifies a test file and adds ≥1 new test function, so a natural
  reproducer exists;
- it touches ≤12 files in total.

These filters are **approximate on purpose**. Commit-message matching includes
broad terms (`handle`, `error`, `raise`), so the pool will contain some feature
work whose message resembles a fix, and will miss real bugs whose fix modified
an existing test rather than adding one. That is acceptable for candidate
generation because **model-free validation decides eligibility**, not the
message. No subjective "this one looks good" filtering is permitted at any
stage.

## 2. Exclusion — previously-seen commits

BM-08 claims evaluation on bugs this project has not already looked at. That
claim is established by exclusion, not assumed.

A frozen exclusion set is harvested from every benchmark artifact in the tree —
BM-06, BM-07, the frozen pilot, the abandoned freeze, the invalidated run, every
mined and shortlisted pool, and the test fixtures. Every 40-character hex object
id found in any of them, at any depth, is excluded. The harvest is deliberately
over-broad: dropping a usable candidate costs one case out of hundreds, while
missing one silently converts a reused bug into a "previously unseen" result.

```
reject if fix_commit ∈ excluded
reject if parent     ∈ excluded
```

Overlap counts are reported **before** filtering, so the degree of prior
exposure is visible rather than hidden.

## 3. Near-duplicate collapse

Two commits fixing the same underlying defect are one bug, not two.

```
at most one case per (repository, primary source file)
at most one case per (repository, target test file)
```

Ties are broken by the deterministic order key, never by inspection.

## 4. Repository spread

```
at most 3 cases per repository
```

At the 20–30 target this forces **≥10 repositories** and prevents two or three
large projects dominating the result.

## 5. Category mix

Categories are labels assigned during mining, never filters. Selection fills
them round-robin — rarest category first — so the corpus spans small,
multi-file, state/order, API misuse, edge case, parsing, configuration
interaction, inheritance and data transformation rather than concentrating on
whichever is most common in the pool.

A category that cannot be filled after validation is reported as unfilled. It is
not manufactured.

## 6. Deterministic order

Candidates are ordered by `SHA-256(fix_commit)`. Never `hash()` — it is
randomised per process, and BM-07's arm-B probe seed already made that mistake
once. The same pool yields the same corpus in any interpreter, on any machine.

## 7. Model-free validation gate

A candidate becomes a case only if, with **no model involved**:

1. the parent and fix commits both exist;
2. the parent materialises into a fresh worktree — never the shared checkout,
   which may carry staged modifications from earlier work;
3. the fix commit's **test half alone** applies to that parent;
4. the target test **fails** there, with a failure identity captured through the
   same component the gate later enforces;
5. the upstream source fix makes the target **pass**;
6. the complete preservation set passes both before and after the source fix;
7. the case runs under the frozen reference environment.

Validation is attempted in the deterministic order until the target count is
reached. Every rejection is recorded with its reason. **No candidate is ever
rejected for producing an inconvenient result** — validation cannot see arm
outcomes, because no arm has run.

## 8. Target

```
20–30 validated cases, ≥10 repositories
```

If fewer than 20 survive, the shortfall is reported and the benchmark runs at
the size the evidence supports. The count is never padded by relaxing a rule
after seeing the outcome.

## 9. What the model never sees

No historical patch content, no commit message, no upstream fix. The model
receives the failing target and the repository at its baseline, exactly as in
BM-07.

## Known limitations, disclosed up front

- **23 of 30 repositories** produced eligible candidates under these rules;
  seven yielded none. The corpus represents the surviving repositories, not the
  whole volume.
- Six source checkouts (`attrs`, `click`, `filelock`, `icalendar`, `packaging`,
  `pyparsing`) carry staged modifications from earlier benchmark work. Baselines
  are built by `git clone --shared` plus `git checkout --force <parent>`, which
  copies committed objects only, so working-tree state cannot reach a baseline —
  asserted by a regression test, not by argument.
- Eligibility is message-based and therefore approximate in both directions, as
  described in §1.
- BM-08 measures fixes-per-dollar on ordinary bugs. It does **not** test the
  false-fix prevention mechanism; that is a separate study, and BM-08 must not
  be bent toward it.
