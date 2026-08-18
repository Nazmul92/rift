# BM-06 repository selection rule

Written **before** the widened search ran and before any stage-2 outcome from
the added repositories was inspected. Recording it afterwards would be
worthless: the entire point is that a repository cannot be admitted because its
results turned out to be convenient, or dropped because they did not.

## Rule

A repository is admitted if and only if all of the following hold. Every clause
is checkable from the repository itself, before any test is run.

1. **Python, pytest-runnable, pure-Python package.** No compiled extension in
   the package under test, so `pip install -e .` succeeds on Python 3.12 in a
   slim container.
2. **Offline-testable.** The suite does not require network access, a database,
   or an external service to run. (Individual tests that reach the network are
   tolerated — they fail identically at parent and fix, so they cannot create a
   false case.)
3. **Permissive licence** — MIT, BSD, or Apache-2.0.
4. **At least 500 commits of history**, so that marker search has something to
   search.
5. **Never used to evaluate RIFT.** Not in the M1a frozen benchmark, and RIFT
   has never been run against it in any form. This is the clause that matters
   most: no repository here was selected because RIFT already succeeds on it,
   because RIFT has never been pointed at any of them.
6. **Not a second project from a family already represented.** The existing set
   includes three Pallets projects (click, jinja, werkzeug); no further Pallets
   projects are admitted, because shared maintainer practice — how a team splits
   fixes and tests across commits — is exactly the variable that determined the
   first round's yield.

## Domain spread, and why it is part of the rule

Selection is additionally spread across problem domains, chosen by what code
*exists* in a project rather than by what results it produces:

- **date, time, timezone and locale handling** — `locale_timezone` cannot be
  discovered in a corpus containing no timezone code, however many repositories
  it has;
- **caching, retry, scheduling and locking** — the plausible home of
  `nondeterminism`;
- **plugin registries, global configuration and logging** — the plausible home
  of `state_leakage`;
- **version and dependency handling** — the plausible home of
  `version_mismatch` and `missing_dependency`;
- **parsers and general-purpose libraries** — `genuine_source_bug`.

This is a statement about where a class of defect can occur at all, not a
prediction about which repositories will yield cases.

## Admitted repositories

Ten from the first round are retained (attrs, boltons, chardet, click, jinja,
markdown, pluggy, pyparsing, sqlparse, werkzeug) together with their complete
rejection records. Twenty are added:

| repository | domain rationale |
|---|---|
| arrow | dates, timezones |
| python-dateutil | dates, timezones, recurrence |
| croniter | cron expressions, timezone arithmetic |
| humanize | locale-dependent formatting |
| freezegun | time mocking; global clock state |
| icalendar | calendar dates, timezones |
| babel | locale data, message formatting |
| faker | locale providers, seeded randomness |
| cachetools | caching, eviction ordering |
| tenacity | retry, timing, backoff |
| filelock | locking, concurrency |
| structlog | logging configuration as global state |
| jsonschema | validation, registry state |
| marshmallow | serialization, class registries |
| cerberus | validation rules |
| packaging | version parsing and comparison |
| pygments | lexers, parsing |
| bleach | HTML sanitization |
| soupsieve | CSS selector parsing |
| more-itertools | pure algorithms |

## Candidate ordering

Deterministic and fixed before stage 2: repositories in the order above, cause
classes in the frozen allocation's order, and within a class, commits in
repository history order (newest first) with commits that touch **both** a test
file and a source file placed before source-only commits.

That last clause is an efficiency ordering, not a filter: source-only commits
are still attempted, via the whole-suite confirmation path. It is recorded here
because it determines which candidates are reached first, and therefore which
surplus candidates exist — and surplus candidates must not be selectable after
results are known.
