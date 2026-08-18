# BM-06 widened discovery and stage-2 confirmation — complete results

Model-free throughout. No provider request was made and nothing was spent.
Confirmation ran in disposable containers with `--network none` while repository
tests executed; no credentials, no mounted home, no developer repository.

Every one of the 409 attempted candidates has a durable record in
`stage2-wide-records.json` carrying repository, parent and fix SHA, container
image, commands, target, both outcomes, signature, and the exact accept or
reject reason. Nothing was discarded silently.

## What was frozen before searching

`allocation.json` (the 30-case split across eight classes) and
`repo-selection.md` (the admission rule and the 30 repositories) were both
written **before** the widened search ran and before any stage-2 outcome from
the added repositories was inspected. No repository here has ever been used to
evaluate RIFT, so none could have been admitted because RIFT succeeds on it.

## Stage 1

1,676 candidates across 30 repositories, every class above its floor. The
domain-spread clause in the selection rule was load-bearing: `locale_timezone`
went from a handful to 686 candidates across 15 repositories once projects
containing timezone code were admitted. The first round was not short of that
class because such bugs are rare — it was searching ten repositories that barely
contain timezone handling.

## Stage 2 — 409 attempted, 36 confirmed

| class | allocated | confirmed | repos | status |
|---|---|---|---|---|
| order_dependence | 4 | 8 | 5 | met, full surplus |
| version_mismatch | 4 | 8 | 5 | met, full surplus |
| locale_timezone | 3 | 6 | 3 | met, full surplus |
| genuine_source_bug | 5 | 6 | 6 | met, thin surplus |
| state_leakage | 4 | 5 | 2 | met, one spare |
| nondeterminism | 3 | 2 | 2 | **short by 1** |
| two_cause | 3 | 1 | 1 | **short by 2** |
| missing_dependency | 4 | 0 | 0 | **short by 4** |

**23 of 30 allocated cases are covered. Five of eight classes met allocation.**

Confirmations by repository: icalendar 10, filelock 8, pygments 3, humanize 2,
freezegun 2, click 2, and one each from tenacity, pyparsing, pluggy, jinja,
faker, dateutil, croniter, cachetools, babel. **Fifteen of the thirty admitted
repositories produced nothing.**

### Why candidates were rejected

| reason | count |
|---|---|
| the parent's own suite is green, so it has no reproducer | 200 |
| every node failing at the parent still fails at the fix | 80 |
| the parent's suite could not be run: pytest reported no summary | 81 |
| whole-suite confirmation skipped: suite over the 300s budget | 11 |
| the fix commit's suite could not be run | 1 |

93 records carry `harness_limited: true` — 23% of all attempts were never
fairly tested. That figure is reported rather than folded into the rejections,
because an unobservable candidate is not a candidate without a reproducer.

## The two findings that block a freeze

### 1. `missing_dependency` cannot be filled by this method

Zero confirmed, with 15 of its 33 attempts unobservable. The cause is structural
and close to circular: each repository's dependencies are installed **as
declared at `HEAD`**, and old commits are then checked out into that single
environment. Commits in this class are precisely the ones that *change what the
project depends on*, so one HEAD-derived environment is the wrong environment
for at least one side of the parent/fix comparison by construction.

More walking cannot fix this. The remedy is per-commit environment
reconstruction — resolving and installing each parent's declared dependencies,
which is what SWE-bench-style corpora do and why building one is a project in
itself. That is substantially more than a harness patch and has not been started.

### 2. Half the confirmations come from two projects

icalendar (10) and filelock (8) supply 18 of 36. The per-class cross-repository
rules do not catch this, because they check within a class rather than across the
manifest. Classes with surplus can be rebalanced at label review; `state_leakage`
(5 across 2 repositories) and `locale_timezone` (6 across 3, four of them
icalendar) largely cannot.

A benchmark weighted this heavily toward one calendar library and one locking
library measures something narrower than its title claims.

## Harness defects found and corrected during this round

Recorded because each had already produced a complete set of confident, wrong
results, and every one made the corpus look **emptier** than it is.

1. **Promisor partial clones.** Diagnosed in the first round but fixed by hand in
   the shell rather than in the script, so it returned the moment new
   repositories were added: 92 "could not check out" rejections. `git fetch
   --refetch` re-applies whatever filter is still configured, so the promisor
   keys must be unset first. Now in the install phase.
2. **A shared virtual environment.** All 30 repositories were installed editable
   into one venv, and five of them — pluggy, packaging, attrs, jsonschema,
   more-itertools — are dependencies of pytest itself. Checking out the *pluggy*
   repository at a commit predating its `src/` layout left `pluggy.__file__` as
   `None` and killed pytest for **every other repository**. Now one environment
   per repository.
3. **An empty failure list read as a green suite.** The consequence of defect 2
   went unnoticed because `suite_failures` equated *no parsed failures* with
   *everything passed*. 142 candidates were rejected with "the parent's own suite
   is green" — a sentence asserting a healthy passing suite, about runs in which
   no test ever executed. A green verdict now requires a pytest summary line, and
   its absence is recorded as `harness_limited` instead.
4. **A missing project build step.** babel's own Makefile declares
   `test: import-cldr`; without it every locale test raises `UnknownLocaleError`.
   The install phase now runs the prerequisites of a project's own `test` target,
   and only the Python commands the project wrote. babel subsequently supplied a
   confirmed `locale_timezone` case.
5. **pytest upgraded past a project's pin.** Introduced while fixing defect 2:
   installing pytest with `-U` *before* the project's own dependencies overrides
   whatever version it pins. pytest is now installed last and only if the
   project's declarations did not already provide it.
6. **A criterion that excluded the class it most needed.** The first source-only
   implementation required the target to fail *in isolation* at the parent. A
   test that fails inside a suite run and passes alone is not a bad candidate —
   it is the definition of order dependence. Such cases are now accepted with an
   explicit `ordering_precondition`.
7. **A false exhaustion message.** `after every candidate was attempted` was
   untrue — `--per-class` caps attempts per repository. A reader trusting it
   would conclude the corpus was out of cases when it was only out of *reached*
   cases, which is the difference between "widen discovery" and "abandon the
   class".

## One duplicate, and how it is handled

`filelock 059cca26ce` was confirmed into **both** `order_dependence` and
`state_leakage`; stage 1's marker sets overlap, so one commit message can match
several classes. Shipping both would count one repair as two independent cases
and inflate two strata from the same evidence. `build_manifest.py` excludes
**every** entry for a duplicated commit unless label review explicitly sets
`primary_class` on exactly one — letting run order decide a cause label is
precisely the accident that separating class from confirmation exists to prevent.

## Status

**The manifest is NOT FROZEN and cannot be frozen on this evidence.** Seven of
thirty allocated cases have nothing behind them, one class is empty, and the
distribution leans on two projects.

No class was substituted, no fixture was manufactured, no confirmed case was
relabelled, and no rejected candidate was discarded without a recorded reason.
