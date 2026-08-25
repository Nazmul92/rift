# BM-07 official result

Executed `2026-08-22T12:07:11Z` → `2026-08-22T12:15:28Z`. Status
**`BM07_COMPLETE`** — 18 of 18 official case-arm records, `OFFICIAL_COMPLETE`.

Nothing was modified during or after execution.

```
python benchmark/bm07/bm07_runner.py run \
  --manifest benchmark/bm07/manifest-executable.json \
  --repos /repos --work /tmp/bm07-official \
  --results benchmark/bm07/results.jsonl \
  --arms ABC --adapter real
```

## Identity, before and after

All five recomputed independently before the first request and again before
scoring. All 18 records agree on each.

```
runtime_hash   75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26
driver_hash    e2154631641a6e9b3fb4bfcbcd36a66d5440a4a2416631af57ed448eceb6ebfd
runner_hash    d1b2fd312d2eacd7436f7401981c302bc2fb025f9bb675680ea15ab93212da48
oracle_hash    4829ceb823a30e1094b09de90322b2242b8b3dd0af7b2168946e1bb34bd3431c
manifest_hash  183f9e6d731f513d9f60ef296372ca040d8bf562cff224e4026efae5fc5061fc
```

Six-case preflight: **6 reconstructed, 0 failures**, before the first request.
Configured model `claude-sonnet-4-6` = manifest requested model. Provider
reported `claude-sonnet-4-6` on all 21 responses; zero identity problems.
Spend **$0.4997** of the authorized $6.00.

## The primary measurement

Arm A produced six canonical candidates. Each was judged three times — weak,
strong shadow, independent oracle — on **identical bytes**, verified by
`weak = strong = truth = canonical` hash for all six.

| case | weak | strong | truth | canonical candidate |
|---|---|---|---|---|
| `cachetools-462e8679` | reject | reject | wrong | `9b63eeed…` |
| `click-2bc3b2c1` | accept | accept | correct | `364b5ddb…` |
| `croniter-7d319c51` | accept | accept | correct | `2903af8e…` |
| `icalendar-63fcf743` | accept | accept | correct | `457a0821…` |
| `structlog-bf80fa60` | reject | reject | wrong | `f5b47e5a…` |
| `tenacity-0b1cef0b` | reject | reject | wrong | `f30fb130…` |

Cells, zeros included:

```
weak ACCEPT / strong REJECT / truth WRONG    harmful weak acceptance prevented  0
weak ACCEPT / strong REJECT / truth CORRECT  strong false rejection             0
weak ACCEPT / strong ACCEPT / truth CORRECT                                     3
weak ACCEPT / strong ACCEPT / truth WRONG    shared false accept                0
weak REJECT / strong REJECT / truth WRONG                                       3
```

## Conclusion: the mechanism was not exercised

Weak and strong agreed on all six candidates. **Zero weak-vs-strong
disagreements**, so the acceptance-authority mechanism had no opportunity to
act. This is **inconclusive evidence for the RIFT thesis — not positive
evidence.** On this corpus the cheap check was already right every time: every
candidate the weak protocol accepted was genuinely correct, every one it
rejected was genuinely wrong.

The zero false-rejection count is a real favourable safety property — the gate
cost nothing in correct fixes. But with no disagreements at all, it is equally
consistent with a gate that never fires. It should not be reported as the gate
being *validated*.

`strong_false_rejection`, undemonstrated throughout the harness work, remains
undemonstrated in real execution.

## Secondary: independent proposals, independent truth

Per-arm candidates are generated independently, so this measures proposal
performance, not the same-candidate mechanism. Keep the two separate.

| arm | truth-correct | accepted | false-accept | abstain | in tok | out tok | req | cmds | cost | wall | $/correct |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 3/6 | 3 | 0 | 0 | 41,242 | 3,272 | 8 | 10 | $0.1728 | 120.3s | $0.0576 |
| B | 3/6 | 3 | 0 | 0 | 40,048 | 3,549 | 7 | 129 | $0.1734 | 175.2s | $0.0578 |
| C | 4/6 | 4 | 0 | 0 | 39,240 | 2,389 | 6 | 143 | $0.1536 | 162.7s | $0.0384 |

C's 4/6 versus 3/6 rests on **one case** (`structlog`, where A and B produced
wrong candidates and C a correct one). One case is not a difference worth
defending. No arm falsely accepted a wrong candidate.

Per-case verdicts:

| case | A | B | C |
|---|---|---|---|
| `cachetools-462e8679` | unverifiable · wrong | unverifiable · wrong | unverifiable · wrong |
| `click-2bc3b2c1` | accepted · correct | verified · correct | verified · correct |
| `croniter-7d319c51` | accepted · correct | verified · correct | verified · correct |
| `icalendar-63fcf743` | accepted · correct | verified · correct | verified · correct |
| `structlog-bf80fa60` | unverifiable · wrong | unverifiable · wrong | verified · correct |
| `tenacity-0b1cef0b` | unverifiable · wrong | unverifiable · wrong | unverifiable · wrong |

## Relationship to BM-06

BM-06's **measured** result stands unchanged at A=3/8, B=3/8, C=3/8. Its
post-hoc canonicalization diagnostic (6/8 each) is a separate artifact and does
not replace the measured figure.

BM-07 is a natural-discrimination/mechanism benchmark, not a representative
general coding benchmark. Six cases is a small denominator and no general
capability claim follows from it.

## Evidence

```
benchmark/bm07/results.jsonl          18 arm records
benchmark/bm07/results-state.jsonl    36 durable transitions
benchmark/bm07/manifest-executable.json
```
