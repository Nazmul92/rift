# Case-selection amendment — diversity, frozen before the targeted pass

Written **before** any outcome of the targeted pass was inspected, and appended
rather than folded into `repo-selection.md`, which stays byte-identical as
evidence of what the widened round was run under.

This amendment responds to **measured repository concentration in the confirmed
set** — icalendar 10 and filelock 8 of 36 — and to nothing else. No benchmark arm
has run. No RIFT result exists, for any case, in any arm. Nothing here was
chosen because it made RIFT look better or worse, because there is no such
information to be had.

## Rules added

1. **The eight-class allocation is unchanged.** order_dependence 4,
   state_leakage 4, missing_dependency 4, version_mismatch 4, locale_timezone 3,
   nondeterminism 3, two_cause 3, genuine_source_bug 5. Total 30.
2. **At least 10 distinct repositories** must appear in the final 30-case
   manifest.
3. **No repository may contribute more than 4 selected cases.** Under the
   confirmed set this binds immediately: icalendar (10 confirmed) and filelock
   (8) must each drop to at most 4 selected.
4. **A duplicate commit is usable in at most one primary class.** Every entry
   for a commit confirmed into multiple classes is excluded unless label review
   sets `primary_class` on exactly one.
5. **Selection within a class follows a deterministic, recorded order**:
   classes in the allocation's order; within a class, repositories in the frozen
   `repo-selection.md` order; within a repository, confirmed candidates in the
   order stage 2 reached them. A class stops taking cases at its allocation, and
   a repository stops at the cap. Everything not taken is recorded as displaced,
   with the binding constraint named.

   **Disclosure on how this rule was chosen.** I first wrote a simpler version —
   walk the confirmed set once, keep anything under the cap — and measured what
   it produced: 26 selected, needing 10 additional cases. The rule above needs 7.
   The difference is mechanical, not evidential: the simpler rule lets a class
   that is already over its allocation consume a repository's four slots before a
   class that still needs them, so capacity is lost to ordering rather than to
   scarcity. I am recording that I evaluated both and chose the second, because
   changing a selection rule after seeing its effect on quota filling is exactly
   the move that needs to be visible rather than silent. What makes it admissible
   here is that neither rule consults any benchmark outcome — none exists — and
   the choice is between two packings of the same confirmed evidence, not between
   two sets of evidence. The 7 it needs matches the targeted pass's stated
   targets.

## Why the cap is set at 4 and not lower

Four is the largest single class allocation apart from `genuine_source_bug`, so
a cap of 4 permits a repository to supply one whole class but never two. A cap
of 3 would have been defensible; 4 is chosen because it is the smallest value
that cannot, by itself, make an already-filled class unfillable — and the point
of this amendment is to spread the manifest, not to re-open classes that the
widened round closed honestly.

## What this does to the confirmed set

Applying the cap to the 36 confirmed cases removes 6 icalendar and 4 filelock
entries from eligibility, which withdraws capacity from `locale_timezone` (four
of its six are icalendar) and `state_leakage` (three of its five are icalendar,
two filelock). Those classes may therefore need diversity replacements from the
targeted pass even though they met their allocation before this amendment.

That consequence is stated here, before the pass runs, so it cannot later be
presented as a discovery.
