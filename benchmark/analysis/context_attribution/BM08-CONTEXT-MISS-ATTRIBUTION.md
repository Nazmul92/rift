# BM-08 context-miss attribution

**ZERO COST. EVALUATOR-ONLY. Provider calls 0. Additional spend $0.00.**
**The paid representation experiment remains on HOLD.**

## 1. Scope

Two questions, answered from evidence that already exists:

> Do historical-fix-region misses actually track BM-08 proposal, application and
> truth failures, and why did the frozen selector miss those regions?

`NOT_COVERED` keeps its narrow meaning throughout: the model-visible frozen
context did not contain the source region the known historical upstream fix
touched. It does **not** mean the bug was unsolvable, that no alternative correct
repair existed, or that the historical patch is the only valid one. The data
below contains a case that proves the point.

Nothing here re-runs the selector. RIFT's own `context_selected` event retains
the full decision path, so every miss is attributed from what the frozen selector
actually did — running a modified selector would measure a selector BM-08 never
used.

## 2. Frozen evidence sources

```
coverage audit    fix-region-coverage.json   audit_manifest_hash verified
                  85a9b90830a046dc41778fd3f106287606435a5fd76632887b67080fcde40785
outcomes          benchmark/bm08/results.jsonl              48 official records
applicability     benchmark/bm08/canonicalization-replay.json   47 replayed arms
selector trace    context_selected event per case (stages, caps, skipped,
                  selection_reason, line_ranges)
probe overlay     source_recall_probe/probe-results.jsonl    12 records
```

## 3. Coverage

```
COVERED              2 / 24
PARTIALLY_COVERED    8 / 24
NOT_COVERED         14 / 24
```

## 4–5. Outcomes by coverage, arms kept separate

```
ARM A                n   cand  rawOK  canonOK  target  truth
COVERED              2      2      1        2       1      1
PARTIALLY_COVERED    8      8      1        5       3      3
NOT_COVERED         14     13      0        1       1      1

  canonical applicable   COVERED 2/2 = 100%   PARTIAL 5/8 = 62%   NOT 1/14 = 7%
  truth correct          COVERED 1/2 =  50%   PARTIAL 3/8 = 38%   NOT 1/14 = 7%

ARM C                n   cand  rawOK  canonOK  target  truth
COVERED              2      2      1        1       0      0
PARTIALLY_COVERED    8      8      1        6       2      2
NOT_COVERED         14     14      1        3       1      1

  canonical applicable   COVERED 1/2 =  50%   PARTIAL 6/8 = 75%   NOT 3/14 = 21%
  truth correct          COVERED 0/2 =   0%   PARTIAL 2/8 = 25%   NOT 1/14 = 7%
```

Arm A is monotone across the three strata. Arm C is not, but its `COVERED`
cell holds **two cases** — that column is noise, not a reversal.

## 6. Where the non-applicable candidates came from

```
arm A   16 non-applicable  ->  COVERED 0   PARTIALLY 3   NOT_COVERED 13
arm C   14 non-applicable  ->  COVERED 1   PARTIALLY 2   NOT_COVERED 11
```

**Descriptive statement, and the strongest one the evidence supports:** canonical
non-applicability was concentrated in `NOT_COVERED` cases — 13 of 16 for arm A
and 11 of 14 for arm C. This is a concentration, not a cause.

## 7. Where the truth-correct fixes came from

```
arm A    5 truth-correct  ->  COVERED 1   PARTIALLY 3   NOT_COVERED 1
arm C    3 truth-correct  ->  COVERED 0   PARTIALLY 2   NOT_COVERED 1
```

### Truth-correct without the historical fix region in context

```
lark-adad165e   arm A   truth-correct, NOT_COVERED
lark-adad165e   arm C   truth-correct, NOT_COVERED
```

Both arms produced a **valid repair that was not the historical one**, from a
context that never contained the historical fix region. This is the concrete
reason `NOT_COVERED` is not read as "unsolvable" anywhere in this analysis.

## 8. Failure class by coverage

Arms pooled for this view only (48 arm-outcomes):

```
coverage             truth_correct  non_applicable  target_still_fails  no_candidate
COVERED                          1               1                   2             0
PARTIALLY_COVERED                5               5                   6             0
NOT_COVERED                      2              23                   2             1
```

Fine-grained canonical diagnostics, from the replay's own `git apply --check`
output:

```
coverage             applied   context_mismatch   file_not_found_or_wrong_path
COVERED                    3                  1                              0
PARTIALLY_COVERED         11                  5                              0
NOT_COVERED                5                 20                              3
```

**Diagnostic-quality note:** these classes come from fresh
`git apply --check --verbose` output retained by the replay. They are the
strongest diagnostics available and are not compared against BM-07, whose
retained strings are materially weaker.

## 9–10. Why each region was missed

Attribution uses **code fix files only**. The selector selects source, so a
changelog the historical commit also touched was never a candidate; attributing
a "miss" to it would describe the audit rather than the selector.

```
class                                          count
TRACEBACK_DID_NOT_REACH_FIX_FILE                   4
LARGE_DEFINITION_TRUNCATION                        4
PER_FILE_BUDGET                                    3
GREP_DID_NOT_FIND_FIX_REGION                       2
MAX_CONTEXT_FILES_CAP                              1
COVERED_NO_MISS                                   10
FIX_FILE_NOT_DISCOVERED                            0
IMPORT_TRAVERSAL_LIMIT                             0
REEXPORT_HOP_LIMIT                                 0
GLOBAL_CONTEXT_BUDGET                              0
SELECTED_FILE_BUT_WRONG_REGION                     0
MULTI_FILE_FIX_PARTIAL                             0
HISTORICAL_FIX_OUTSIDE_SELECTOR_DISCOVERY_PATH     0
UNRESOLVED_FROM_RETAINED_EVIDENCE                  0
```

Every one of the 14 `NOT_COVERED` cases is attributed to a specific frozen
selector decision. None is `UNRESOLVED_FROM_RETAINED_EVIDENCE`.

### Discovery failure versus budget failure

```
never discovered                     6    (traceback 4, grep 2)
discovered but cap-excluded          1    (mistune-1bef343a)
selected file but wrong region       7    (large-definition 4, per-file budget 3)
covered, no miss                    10
other / unresolved                   0
```

**Half of the misses are not discovery failures.** In 7 of 14 the selector had
already chosen the right *file* and rendered the wrong *part* of it — a budget
and truncation outcome, not a search outcome. That distinction matters for what
a future mechanism experiment should vary.

The 8 `PARTIALLY_COVERED` cases all classify as `COVERED_NO_MISS` under code-only
attribution: their code fix regions were selected, and the "partial" came from
changelog and packaging files the commit also touched.

## 11. Limitations

- 24 cases, and arm A and arm C are the same 24 bugs — not independent
  populations. No significance testing is reported; counts are primary.
- `COVERED` holds 2 cases. Any rate computed on it is unstable.
- Coverage is measured against **one** known repair. A case can be
  `NOT_COVERED` and still solvable, as `lark-adad165e` demonstrates.
- Concentration is not causation. Whether context starvation *caused* the
  non-applicability requires a controlled experiment that varies context while
  holding everything else fixed.

## 12. Decision classification

```
BOTH_CONTEXT_AND_FORMAT_SIGNALS
```

Both hold, and the probe overlay is what separates them.

**Context signal.** Non-applicability is heavily concentrated in `NOT_COVERED`
(13/16 arm A, 11/14 arm C), canonical applicability falls monotonically across
arm A's strata (100% → 62% → 7%), and every miss traces to a specific selector
decision.

**Format signal survives as a distinct observation.** All six exploratory probe
cases were `NOT_COVERED` (5) or `PARTIALLY_COVERED` (1), and on that same
starved context:

```
case                          coverage            miss class                   U applied  S quote  S applied
charset-normalizer-2bc26076   NOT_COVERED         PER_FILE_BUDGET                   True     True       True
dnspython-227eace4            NOT_COVERED         GREP_DID_NOT_FIND_FIX_REGION     False     True       True
lark-96873d64                 NOT_COVERED         GREP_DID_NOT_FIND_FIX_REGION     False    False      False
packaging-553582d9            NOT_COVERED         LARGE_DEFINITION_TRUNCATION      False     True       True
tabulate-293ff59a             NOT_COVERED         LARGE_DEFINITION_TRUNCATION      False     True       True
xmltodict-927a025a            PARTIALLY_COVERED   COVERED_NO_MISS                  False     True       True
```

U and S saw **identical frozen context per case**, so poor coverage cannot
explain a difference between them. S quoted visible source exactly in 5 of 6
while U applied in 1 of 6.

The honest reading of those two facts together:

> The model was accurately editing what it saw, and what it saw largely did not
> contain the known repair region.

Those are different problems. Both appear real; neither is established as causal.

## 13. Recommended next experiment

**A selector mechanism experiment, before the paid representation study.**

The representation experiment is frozen over these same 24 cases, 14 of which are
`NOT_COVERED`, and its primary endpoint is truth-correctness — the outcome most
strongly associated with coverage. Spending $45 to compare two representations
on a corpus where over half the cases never showed the model a known repair
region risks measuring context starvation in both arms and calling the residue a
representation result.

The cleanest order isolates the cheaper, more concentrated factor first:

1. **Selector mechanism experiment.** Its most informative arm is largely
   model-free: for each case, does an alternative discovery or budget policy
   place the historical fix region inside the context? That is measured by
   coverage, not by model output, so most of it costs nothing. The taxonomy says
   where to look — 7 of 14 misses were budget and truncation on an
   already-selected file, and 6 were discovery reaching only the test frame.
2. **Then** the representation contrast, either on a coverage-balanced case set
   or with coverage as the stratifying variable it is already frozen to be.

Not recommended: changing the selector now. This analysis measured the frozen
selector; it did not test a better one.

## 14. Status

```
paid representation experiment   HOLD
provider calls                   0
additional spend                 $0.00
src/riftagent                    unchanged
context selector                 unchanged
canonicalizer                    unchanged
BM-07 / BM-08 official artifacts unchanged
```
