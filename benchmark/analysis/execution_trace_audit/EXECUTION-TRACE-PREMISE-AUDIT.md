# Execution-trace premise audit

**ZERO COST. Provider calls 0. Additional spend $0.00.**
**No selector change. No RIFT change. Representation experiment remains on HOLD.**

## Scope

One conditional selector-v2 proposal needed its premise tested before being
built:

> For the 6 BM-08 cases whose historical fix file was never discovered, would
> deterministic execution tracing during the frozen failing reproduction have
> exposed the historical fix file and/or the historical fix region?

Two measurements, deliberately kept apart. A file can execute while the fix
region inside it never runs, and that gap is the difference between *tracing
would have found the file* and *tracing would have ranked the right definition*.

The historical fix is evaluator-only: read to ask "did this region run", written
to no prompt, context, or runtime artifact. No model is involved.

## Method

```
manifest frozen BEFORE any trace   669dc5bae7711c2cfe66a76dfb055a72cdcdacee0e2f10aca9a50398a92fef74
trace mechanism                    stdlib sys.settrace + threading.settrace, line
                                   events, repository-tree filtered
trace implementation hash          (recorded in the manifest)
python                             3.12.14
repeats                            N = 3, each in its own process
network                            repository-controlled execution denied
```

Order of operations was the discipline: freeze the manifest and its thresholds,
verify the untraced reproduction against the frozen official failure identity,
*then* trace. Each traced repeat had to reproduce the untraced identity or be
discarded as observer perturbation rather than counted.

## Result

```
case                     attribution bucket                  fix file        fix region      valid
click-a17b5447           TRACEBACK_DID_NOT_REACH_FIX_FILE    STABLE_3_OF_3   STABLE_3_OF_3     3/3
dnspython-227eace4       GREP_DID_NOT_FIND_FIX_REGION        STABLE_3_OF_3   STABLE_3_OF_3     3/3
dnspython-c9f6c819       TRACEBACK_DID_NOT_REACH_FIX_FILE    STABLE_3_OF_3   STABLE_3_OF_3     3/3
isort-49bb9bab           TRACEBACK_DID_NOT_REACH_FIX_FILE    TRACE_INVALID   TRACE_INVALID     0/3
lark-96873d64            GREP_DID_NOT_FIND_FIX_REGION        STABLE_3_OF_3   STABLE_3_OF_3     3/3
mistune-f4237046         TRACEBACK_DID_NOT_REACH_FIX_FILE    STABLE_3_OF_3   STABLE_3_OF_3     3/3

fix file    STABLE_3_OF_3  5/6      TRACE_INVALID 1      unstable 0     STABLE_0_OF_3 0
fix region  STABLE_3_OF_3  5/6      TRACE_INVALID 1      unstable 0     STABLE_0_OF_3 0
```

Not one observation was unstable. Every valid repeat agreed with the other two.

### Classification — SUPERSEDED, aggregate authority unresolved

```
EXECUTION_CITATION_STRONGLY_SUPPORTED     <- computed under case-level ANY
```

**This classification is no longer treated as established.** Drafting the
selector-v2 DAR required quoting the operative aggregate, and the frozen manifest
does not unambiguously specify one: its threshold reads `">=5/6 fix files"`,
whose unit word is *files* while its denominator is *cases*, and it never says
`ANY` or `ALL`. The same unchanged observations give:

```
case-level ANY fix file     5/6     STRONGLY_SUPPORTED   <- what was published
case-level ALL fix files    3/6     PARTIALLY_SUPPORTED
individual fix files      10/13     PARTIALLY_SUPPORTED  <- the manifest's literal unit
case-level ANY fix region   5/6     STRONGLY_SUPPORTED
case-level ALL fix regions  2/6     NOT_SUPPORTED
individual fix regions     8/13     PARTIALLY_SUPPORTED
```

The aggregate is not chosen here, because choosing it now — with the outcomes
already visible — is what the freeze exists to prevent. The measurements are
unchanged and remain valid; only the verdict drawn from them is escalated. See
`AGGREGATE-AUTHORITY-BLOCK.md`.

The one-invalid-case robustness note still holds within the ANY reading: scoring
`isort-49bb9bab` as a failure leaves that count at 5/6.

## The one invalid case, diagnosed rather than relaxed

`isort-49bb9bab` produced `identity_invariant = False` on all three repeats, so
it is `TRACE_INVALID` under the frozen rule and contributes no execution
evidence.

It is **not** observer perturbation. The traced and untraced messages differ only
in a process-specific memory address:

```
untraced   assert False\n +  where False = <function check_code_string at 0x7c991812f7e0>(...)
traced 1   assert False\n +  where False = <function check_code_string at 0x75658fa15120>(...)
```

This is the volatile-signature class BM-08-v4 already documented and rejected
cases for. The *governed* observer — the authority BM-08 actually uses — returned
`{"exception_type": "AssertionError", "message": "assert False"}` for this case,
address-free, and it **matched the frozen official identity**. The defect is in
this audit's harness-side comparison, which extracted a more volatile signature
from pytest's `reprcrash` than the governed contract uses.

The frozen rule is kept and the case is reported invalid. It is not re-run under
a relaxed rule: rewriting a comparison rule after seeing which case it excluded
is precisely the move this project's rules exist to prevent. (An earlier revision
also justified this by saying the threshold was met without it — that reasoning
is withdrawn, since the threshold's aggregate is itself unresolved. The rule
stands on its own.)

## File execution is not region ranking

Per fix file, across all six cases:

```
fix-file entries    13     STABLE_3_OF_3  10
fix-region entries  13     STABLE_3_OF_3   8
```

Three fix files never executed at all, and two executed without their fix region
executing:

```
executed, region did NOT execute
  lark/lark.py                        file 3/3   region 0/3
  src/mistune/renderers/html.py       file 3/3   region 0/3

neither file nor region executed
  dns/rdtypes/ANY/TKEY.py             file 0/3   region 0/3
  src/mistune/directives/image.py     file 0/3   region 0/3
  isort/parse.py                      TRACE_INVALID
```

### Multi-file fixes: `ANY` is not `ALL`

```
case                     ALL files   ANY file   ALL regions   ANY region
click-a17b5447              yes         yes         yes           yes
dnspython-227eace4          no          yes         no            yes
dnspython-c9f6c819          yes         yes         yes           yes
isort-49bb9bab              no          no          no            no
lark-96873d64               yes         yes         no            yes
mistune-f4237046            no          yes         no            yes
```

**Three of six cases had every historical fix file execute** (`click-a17b5447`,
`dnspython-c9f6c819`, `lark-96873d64`); two of six had every fix *region*
execute. The 5/6 headline is an `ANY` measure — execution tracing would have
surfaced *a* fix file in five cases, not *all* of them. For a multi-file
historical fix, execution citation is a partial witness.

> **Correction.** An earlier revision of this document said "only 2 of 6 cases
> had every historical fix file execute". That was wrong: 2/6 is the fix-*region*
> figure, and the file figure is 3/6. The table above was correct; the prose
> beneath it conflated the two columns. See `AGGREGATE-AUTHORITY-BLOCK.md`.

## By original attribution bucket

```
traceback misses (4)   click ✓   dnspython-c9f6c819 ✓   mistune-f4237046 ✓   isort INVALID
grep misses (2)        dnspython-227eace4 ✓   lark-96873d64 ✓
```

Both grep misses executed. Three of four traceback misses executed; the fourth is
the invalid case. The pattern is consistent: these files ran during the failure
and neither the traceback frames nor bounded grep surfaced them.

## What this supports, and what it does not

**Measured, and not in dispute.** For five of six never-discovered cases the
historical fix file executed during the frozen failing reproduction, stably
across three fresh processes, without disturbing the failure being observed.
Both grep misses executed; three of four traceback misses executed. Zero
observations were unstable. These files ran during the failure and neither the
traceback frames nor bounded grep surfaced them.

**Not established.** Whether that clears the bar for `EXECUTION_CITATION_*` is
unresolved, because the frozen threshold does not say which aggregate it counts
(see above and `AGGREGATE-AUTHORITY-BLOCK.md`). Under the manifest's literal unit
the result is `PARTIALLY_SUPPORTED`, not `STRONGLY_SUPPORTED`.

**Limits that hold under every reading.** Execution citation is not a complete
answer to the six-case discovery class:

- one case (`isort-49bb9bab`) is unmeasured here;
- three of thirteen fix files never executed, so no amount of tracing reaches them;
- two executed files did not execute their fix region, so execution gives
  file-level discovery but not region-level ranking in those cases;
- only three of six cases had *all* fix files execute, and only two had all fix
  regions execute.

Execution citation would therefore be one citation source among several, not the
mechanism the six-case class is delegated to.

## Selector-v2 DAR input

Supported independently of this audit, from the earlier attribution:

```
A. citation-ranked definitions
B. definition-first editable rendering
C. explicit non-editable symbol map
D. rank-before-cap
```

Conditional item, **still conditional**:

```
E. execution citation   UNRESOLVED — the frozen threshold's aggregate authority
                        is not established; see AGGREGATE-AUTHORITY-BLOCK.md
```

The 7 of 14 misses that were budget and truncation on an already-selected file
are addressed by the items above, not by E. E addresses the discovery half, and
whether the evidence clears its own frozen bar is the open question.

## Recorded, not implemented

Future design direction only, with no runtime authority earned yet:

```
failed exact edit -> compiler diagnostic -> new citation -> additional evidence -> repair
```

The S format has not earned runtime authority, and this audit does not grant it.

## Generalization limit

> The 24 BM-08 cases are the selector-v2 development and regression set, because
> their misses informed its design.

Any future selector v2 must additionally be evaluated model-free on a **fresh
historical-bug selector corpus not used in its design**. No generalization is
claimed from these 24 cases.

## Status

```
execution trace audit             measurements complete and retained
execution citation                UNRESOLVED — BLOCKED_AUDIT_AGGREGATE_AUTHORITY
selector v2                       NOT implemented
paid representation experiment    HOLD
provider calls                    0
additional spend                  $0.00
```
