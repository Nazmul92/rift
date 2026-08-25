# Second run stopped: every patch fails to apply, and the cause is upstream of the model

Stopped after 9 of 27 arm-runs. **Charged $0.053946** of the $7.62 ceiling.
No arm produced an accepted patch and `results.json` was never written, so there
is no partial report to mistake for a result.

This is a different failure from `RUN-ABORTED-FINDING.md`. That run died because
the model emitted nothing. This one dies because the model is asked to patch
code it was not shown.

## What is working, and should not be re-litigated

Everything the previous two passes fixed:

| | aborted run (Sonnet 5) | this run (Sonnet 4.6) |
|---|---|---|
| empty responses | 4 of 5 | **0 of 5** |
| `finish_reason: length` | 4 of 5 | **0** |
| response chars | 0 | 1,470 – 3,062 |
| output tokens used | 4,000, the entire cap | 433 – 777 of 4,000 |
| extraction failures | 4 | **0** |
| schema repairs needed | not implemented | **0** |

The DAR-021 extractor accepted every reply on the first attempt. The pipeline
runs end to end: the baseline reproduces under its frozen signature, 7 handles
are discovered, 291 hypotheses are enumerated, and probes eliminate 15–16 at a
time. Reservation, settlement and the $7.62 ceiling all behave.

## The failure

Every completed arm-run ends the same way:

```
gate phase   candidate
reason       the patch does not apply to the baseline tree:
             git apply --check failed (forward) at every strip level
verdict      unverifiable
```

`unverifiable` is the correct verdict for a patch that will not apply. The gate
is not at fault. The question is why every patch is like this.

## The cause

For `cachetools-c0fdf6ab`, the model was shown **lines 575–599** of
`src/cachetools/__init__.py`. It returned a hunk at **line 620**:

```diff
@@ -620,6 +620,11 @@ class TLRUCache(_TimedCache):
         expires = self.__ttu(key, value, self.timer())
         if expires <= self.timer():
             # remove if already expired
```

The file at that location actually reads:

```python
    def __setitem__(self, key, value, cache_setitem=Cache.__setitem__):
        with self.timer as time:
            expires = self.__ttu(key, value, time)
```

The context lines are fabricated — `self.timer()` where the code says `time` —
because `__setitem__` was never in the window. A unified diff cannot apply if
its context lines are invented, and they must be invented for any region the
model was not shown.

The proposed *change* is not obviously wrong. Deleting an expired key before
reinsertion is the same fix the earlier live diagnostic produced and the same
one the project's own commit makes. The model is failing at transcription, not
at diagnosis.

## The window is the defect, not the cap

`context_selected` records both the budget and what was spent of it:

| task | chars sent | cap_chars | fraction used | file size |
|---|---|---|---|---|
| cachetools, arms A/B/C | 770 | 60,000 | **1.3%** | 776 lines |
| pygments, arm A | 1,646 | 60,000 | 2.7% | 82 lines |
| pygments, arm B | 3,994 | 60,000 | 6.7% | 82 lines |

The selector had sixty thousand characters available and spent seven hundred
and seventy. `cachetools/__init__.py` is 776 lines; it would have fitted whole,
several times over, inside the budget that was already authorized.

## Why the run was stopped rather than finished

Finishing would have cost about $7 and roughly twenty-five minutes, and would
have produced a null result.

Every arm fails this way for the same reason, so arms A, B and C would all score
approximately zero. BM-06 exists to compare them. A comparison in which every
arm is defeated by the same upstream defect measures context starvation, not
acceptance authority — and it would read, to anyone seeing only the numbers, as
"the model cannot fix these bugs." What it would actually say is "we did not
show it the code."

That is the specific way this project has been wrong before, and it is worth
$0.05 to not be wrong that way again.

## Not diagnosed further, deliberately

The context selector is approved M1 runtime and it has not been touched. Two
explanations remain open and the evidence above does not separate them:

1. the line-range picker is simply too narrow, and a file that fits the budget
   should be sent whole;
2. the ranges are keyed to the diagnosed cause, and the diagnosis pointed at the
   wrong region — in which case widening the window treats a symptom.

Note that arm A shows the same starvation with **no diagnosis at all**
(`--model-alone`, 770 chars, same as B and C), which is evidence for (1) — but
one case is not enough to rule on, and the previous pass established the cost of
proposing a fix before the cause is captured.

Nine arm-runs of ledgers are preserved in the review archive.

## State

- **Spend:** $0.053946 this run; **$0.357593** cumulative across everything.
- **Manifest:** unchanged, `e48e4fc5…f51408d3`. Cases, arms, ceiling untouched.
- **Runtime:** unchanged, 9,391 / 9,397.
- **Suite:** 652 passed, 5 skipped. Unaffected by this finding.
- **Results:** none. `results.json` was never written.
