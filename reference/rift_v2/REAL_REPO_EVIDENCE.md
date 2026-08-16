# Real-repo evidence

Everything below ran against `pallets/click` at commit `8b44edf`, cloned from
GitHub, installed, and executed in this container. Raw records:
`results/riftcode_real_repo.json`.

## The failures were not planted

Running click's own suite in this container produces 24 real failures in
`tests/test_utils/test_echo_via_pager.py`. Investigating by hand first
established ground truth:

- the container has `more` but **not `less`**, so every `less` parametrisation
  fails on the assertion;
- run in isolation, those tests fail *earlier* with `AttributeError:
  _termui_impl`, because `click._termui_impl` is only reachable as an attribute
  once some other module imports it — and `tests/test_termui.py` does so at
  module import time.

Two stacked hidden-state causes in a real repository, neither of which I
introduced. This is the shape of failure that makes real debugging expensive.

## Results

| case | true cause | riftcode verdict | gate | commands |
|---|---|---|---|---|
| R1 order dependence (`[test0-cat]`) | `tests/test_termui.py` must be collected first | `first:tests/` → bisected to `first:tests/test_termui.py` | verified | 7 |
| R2 missing binary (`[test0-less]`) | `less` not installed | no environmental cause in action space | n/a | 16 |
| R3 env pollution (`test_custom_parser`, `COLUMNS=20`) | ambient `COLUMNS` | `unsetenv:COLUMNS` | verified | 16 |

R1 is the substantive result: from a black-box failing test, the runtime
proposed a coarse "run the tests directory first" cause, then bisected it in
five rounds to the exact file, and the interventional gate confirmed
fail → pass → fail. It never read the source.

R2 is a **miss, and the informative one**. The cause — a missing system binary
— has no handle in the action space, so no amount of elimination downstream
could reach it. The runtime declined to invent an explanation and reported
that nothing it can do accounts for the failure, which is the right behaviour
but is not a diagnosis. Discovery, not elimination, is the bottleneck; the
gridworld predicted this and the real repo confirmed it.

The recorded run labelled that outcome `identified` with a `{"const": false}`
condition and a null cause — the right evidence under a status that oversold
it. The status vocabulary has since been corrected: this outcome is
`unexplained_by_representation`, and the runtime no longer suggests anywhere
that an exhausted action space implicates the source. `results/
riftcode_real_repo.json` is left exactly as recorded, so its `status` field
still reads `identified` for R2; rerunning the case would now record the
corrected status. Reproducing that requires the container described above
(cloned `pallets/click` @ `8b44edf`, `more` present and `less` absent) and has
not been rerun.

## The coding-agent failure mode, measured

Same real R1 failure. The "fix" is a comment appended to
`src/click/termui.py` — semantically inert by construction, so any acceptance
is a false positive.

| verification protocol | result |
|---|---|
| A. apply edit, run the full suite, check the target | target **PASSED** → concludes FIXED |
| B. apply edit, run the target's own test file | target **PASSED** → concludes FIXED |
| C. riftcode gate (isolated baseline / with edit / reverted) | `blocked / blocked / blocked` → **REJECTED** |

Both standard protocols accept a comment as a bug fix, because running other
tests first supplies the missing import. The gate rejects it, and costs less:
3 commands and 0.7s against 2 commands and 5.2s, since it runs the target in
isolation rather than the suite.

This is the concrete claim: *"the test passes now"* is not evidence, and the
protocol nearly every coding agent uses to confirm a fix cannot distinguish a
real fix from an irrelevant edit in the presence of order dependence.

## Bugs the real repo found in riftcode

Contact with a real suite exposed three defects that the synthetic harness
never could, all now fixed:

1. **Verdict read from the process exit code.** Any probe that ran other tests
   first was scored by *their* failures. Outcomes now come from the target's
   own report line.
2. **Discovery flooded by `.pyc` files.** Bounded listing of a real tree filled
   the handle budget with individual cache files. Sources now get quotas and
   only directories yield `clear` handles.
3. **`--tb=no` starved discovery.** Suppressing tracebacks removed the very
   identifiers the env handles are derived from; the short summary truncates
   the message. Tracebacks are now kept.

Defect 1 is worth dwelling on: it made the agent conclude "no environmental
cause" on R1 — a confident wrong answer produced by a measurement error, not a
reasoning error.

## Synthetic ablation, refreshed after those changes

6 fault families x 3 seeds x 3 probe policies, identical candidate probe set
and budget per policy:

| policy | correct | mean commands |
|---|---|---|
| disagreement | 18/18 | 12.2 |
| random | 10/18 | 21.4 |
| cheapest (goal-greedy analogue) | 0/18 | 18.7 |

Gate verdicts: 12 `verified`, 6 `not_applicable` (the two families with no
environmental cause). Command counts rose from the earlier run because
discovery now proposes more handles and the probe allowance scales with them.

## What is still not shown

- **Three real cases on one repository.** Not a benchmark. No claim about
  SWE-bench, other languages, build systems, or multi-repo generality.
- **R2 remains unexplained by the system.** The action space covers env vars,
  state directories and test ordering. It does not cover missing binaries,
  dependency versions, locale, timezone, network, or parallelism. Each is a
  known real cause and each is currently a guaranteed miss.
- **No source reading and no LLM.** A real coding agent reads code; this reads
  command outcomes. Adding an LLM proposer for handles is the obvious next
  step and is exactly the untested component.
- **The gate assumes a deterministic reproduction.** Genuinely flaky targets
  need repeated phases and a statistical criterion, which is not implemented.
- **`bisect_cause` is invoked by the caller, not the loop.** It is not yet part
  of the automatic pipeline.
