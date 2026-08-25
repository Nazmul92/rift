# Execution-citation threshold: aggregate authority is unresolved

**`BLOCKED_AUDIT_AGGREGATE_AUTHORITY`. The selector-v2 DAR is not drafted as
premise-proven. Provider calls 0. Additional spend $0.00.**

Drafting the selector-v2 DAR required quoting the operative definition behind
the execution-citation decision. Inspecting the frozen manifest against the
implementation that produced the published classification shows the two do not
agree, and the disagreement is not cosmetic: it changes the verdict.

## The frozen language

```
EXECUTION_CITATION_STRONGLY_SUPPORTED   ">=5/6 fix files STABLE_3_OF_3 with identity preserved"
EXECUTION_CITATION_PARTIALLY_SUPPORTED  "3-4/6 fix files STABLE_3_OF_3"
EXECUTION_CITATION_NOT_SUPPORTED        "<=2/6 fix files STABLE_3_OF_3"
```

The unit word is **"fix files"**. The denominator is **6**. The audit covers
**6 cases** and **13 fix files**, so `/6` cannot be a per-file fraction — the two
halves of the same sentence name different units.

The manifest nowhere says `ANY` or `ALL`. Both string occurrences inside it are
incidental: a repository path (`dns/rdtypes/ANY/LP.py`) and the substring in
`PARTIALLY`. Multi-file historical fixes make `ANY` and `ALL` genuinely
different, and nothing frozen chooses between them.

## What the implementation actually did

```python
record["any_fix_file_executed"] = STABLE_TRUE in file_statuses
record["file_status"] = STABLE_TRUE if record["any_fix_file_executed"] else classify(...)
```

The published `5/6` counted `file_status`, so the operative aggregate was
**case-level ANY** — a case scored as executed if *at least one* of its
historical fix files executed stably.

## Why this is material

The same frozen observations, unchanged, under each candidate aggregate:

```
aggregate                          value    classification under the frozen bands
case-level ANY fix file             5/6     EXECUTION_CITATION_STRONGLY_SUPPORTED   <- published
case-level ALL fix files            3/6     EXECUTION_CITATION_PARTIALLY_SUPPORTED
individual fix files              10/13     EXECUTION_CITATION_PARTIALLY_SUPPORTED  <- manifest's literal unit
case-level ANY fix region           5/6     EXECUTION_CITATION_STRONGLY_SUPPORTED
case-level ALL fix regions          2/6     EXECUTION_CITATION_NOT_SUPPORTED
individual fix regions             8/13     EXECUTION_CITATION_PARTIALLY_SUPPORTED
```

Reading the manifest by its unit word gives `PARTIALLY_SUPPORTED`. Reading it by
its denominator, with the aggregate the implementation chose, gives
`STRONGLY_SUPPORTED`. On regions the spread runs from `NOT_SUPPORTED` to
`STRONGLY_SUPPORTED`.

The published verdict therefore rests on an aggregate the frozen manifest never
specified, and the more literal reading of that manifest does not support it.

## Why this is not resolved here

Choosing the aggregate now, after the outcomes are known, is exactly the move the
freeze exists to prevent. Any of the three readings could be argued for on its
merits; what disqualifies all of them is that the argument would be made with the
answers already visible. The threshold was frozen precisely so this choice could
not be made at this moment.

So no reinterpretation is applied. The measurements stand unchanged and the
decision is escalated.

## A separate correction to the audit finding

`EXECUTION-TRACE-PREMISE-AUDIT.md` previously stated:

> Only 2 of 6 cases had every historical fix file execute.

**That is wrong. Three of six** cases had every historical fix file execute —
`click-a17b5447`, `dnspython-c9f6c819` and `lark-96873d64`. The figure `2/6`
belongs to fix *regions*, not files; the two columns of that document's own table
were conflated in the prose beneath it. The table itself was correct. The
finding document has been corrected and this correction is recorded here rather
than made silently.

## What must be decided

One question, for the reviewer who froze the threshold:

> Which aggregate did `">=5/6 fix files STABLE_3_OF_3"` mean — individual fix
> files, case-level ANY, or case-level ALL?

Each has a defensible reading:

- **individual fix files** matches the sentence's unit word, and is the strictest
  measure of how much of a historical fix execution evidence would surface;
- **case-level ANY** matches the denominator, and answers "would tracing have
  exposed *a* missed fix file in this case" — arguably the question a discovery
  citation needs to answer;
- **case-level ALL** answers "would tracing have exposed the *whole* fix", which
  is what a complete repair would require.

They are not interchangeable, and the audit measured all three, so whichever is
chosen the number already exists and no re-run is needed.

## Status

```
execution-trace measurements       unchanged, valid, retained
execution-citation classification  UNRESOLVED — aggregate authority not established
selector-v2 DAR                    NOT DRAFTED as premise-proven
selector v2                        not implemented
paid representation experiment     HOLD
repair loop                        DEFERRED
provider calls                     0
additional spend                   $0.00
```

Changes B–E of the proposed selector-v2 amendment — failure-anchored definition
ranking, definition-first editable rendering, explicit non-editable symbol map,
and rank-before-cap — do **not** depend on this audit. They rest on the
context-miss attribution's 7 wrong-region misses and 1 cap miss, which are
unaffected. Only change A, execution-trace citation, is blocked.
