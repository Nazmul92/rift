# RIFT-Code: the RIFT runtime applied to debugging

## What was converted

The gridworld is gone; the epistemic runtime is not. `src/riftcode/` reuses
`rift.hypothesis` (IR, execution, description length), `rift.population`
(scoring, supported/contradicted/underdetermined, equivalence classes) and
`rift.probes.js_divergence` unchanged. Only the vocabulary and the boundary
are new.

| gridworld | code |
|---|---|
| rendered grid | command exit code, stdout, stderr, duration, path listing |
| primitive moves | whitelisted pytest runs with structured interventions |
| anonymous components `c0..cN` | anonymous intervention handles `r0..rN` |
| barrier push -> pass/blocked | target test run -> pass/fail |
| episode reset | fresh sandbox copy |
| `overlap`, `disappeared` | `applied`, `run` |
| state aliasing | same inputs, different outcome (flakiness) |
| portable causal schema | repo playbook (not yet built) |

One IR serves both domains: `rift.hypothesis.register_event_kinds()` is the
only change made to the original package.

## The loop

    baseline run -> discover handles -> populate ledger -> select probe
                 -> execute -> eliminate -> identify -> interventional gate

No LLM is in this loop. Scoring, elimination and probe selection are
deterministic Python. That is the cost argument for the architecture: current
agents re-derive their whole mental model of the bug on every iteration, which
is where the tokens go. Here a model is needed only to propose handles the
generic discoverer missed and to write the final source patch.

Four failure modes of current coding agents are targeted directly:

1. **Shotgun debugging.** Probe selection maximises Jensen-Shannon divergence
   of predicted outcomes per unit estimated cost — run the one command whose
   result the surviving hypotheses disagree about.
2. **Verification theatre.** `verify()` accepts a fix only if the failure
   reproduces on a clean episode, disappears with the cause applied, and
   *reappears* when it is withdrawn. "It passes now" is consistent with a
   stale cache or a retry; the revert phase is what excludes them.
3. **Committing to one wrong theory.** The ledger keeps every hypothesis until
   evidence kills it; nothing is selected while two behavioural classes remain.
4. **Confusing correlation with cause.** The decoy family names an irrelevant
   variable in the failure message; the runtime ignores it because applying it
   changes no outcome.

One thing the grammar deliberately does NOT license: when `{"const": false}`
is the last survivor, the only supported statement is that no handle in the
current action space changes the outcome. That is a property of the
representation, not of the repository. The action space covers environment
variables, state directories and test ordering, so a missing binary, a
dependency version, a locale or a parallelism effect is indistinguishable from
a genuine source defect at this boundary — all four leave `const False`
standing. The status is therefore `unexplained_by_representation`, and the
runtime is not entitled to say "the bug is in your code". Deriving a source
claim from an exhausted action space is the same error class as concluding a
fix works because the test passes now.

## Results (deterministic, offline, no LLM)

6 fault families x 3 seeds x 3 probe policies = 54 runs, 162.2s of charged
command time. All policies receive the identical candidate probe set and
budget.

| policy | correct cause | mean commands |
|---|---|---|
| disagreement | 18/18 | 12.2 |
| random | 10/18 | 21.4 |
| cheapest (goal-greedy analogue) | 0/18 | 18.7 |

Per family under disagreement: 3/3 each for `env_gated`, `cache_stale`,
`retry_flake`, `order_dependent`, `decoy_correlated`, `code_defect`;
7-15 commands each; gate verdict `verified` on all four environmental causes
(12 runs) and `not_applicable` on the two families with no environmental cause
(6 runs).

Every figure in this table is recomputed from `results/riftcode_demo.json` by
`tests/test_m0_honesty.py::test_riftcode_results_table_matches_raw_records`,
so the table cannot drift from the artifact again. An earlier revision of this
file reported 18/18 at 5.5, 12/18 at 9.8 and 3/18 at 9.0 — figures from a run
that predates the three real-repository defect fixes described in
`REAL_REPO_EVIDENCE.md`. Command counts rose because discovery now proposes
more handles and the probe allowance scales with them. Wall-clock time is not
recorded in the raw artifact, so the earlier "80s wall clock" claim is not
reproducible and has been replaced with the charged command time the artifact
does record.

## What this does not show

- **The faults are mine.** Six synthetic single-cause families in three-file
  repositories. This is a controlled instrument, not evidence about real
  repositories, real build systems, or multi-cause failures.
- **Discovery was easy here.** In every family the true cause had a handle in
  the candidate set by construction. On real code the discoverer is the
  bottleneck: a cause with no handle can never be hypothesised, and no amount
  of elimination downstream recovers from that. This mirrors the gridworld
  finding and is the thing to attack next.
- **No source reading.** The agent works black-box from command outcomes. That
  keeps the isolation audit clean but is not how a real coding agent operates.
- **`retry_flake` was explained, not merely detected.** The nondeterminism
  detector did not fire, because the retry counter accounted for the outcomes.
  Genuine flakiness (unexplained by any hypothesis) is untested here.
- **No LLM proposer, no schema transfer, no SWE-bench.** Those are the next
  milestones, and the LLM proposal quality question is exactly the one the
  gridworld benchmark could not answer either.
- **`code_defect` was not shown to be a code defect.** The runtime reports
  `unexplained_by_representation` on that family and is correct to. It scores
  as a hit only because the evaluator-side oracle records "no environmental
  cause exists" as the ground truth, and the runtime abstained instead of
  inventing one. Abstaining correctly is not the same capability as locating a
  source bug, and nothing here demonstrates the latter.
- **The raw artifacts record the vocabulary in force when they ran.** The
  `status` field in `results/riftcode_demo.json` predates the correction above,
  so the `code_defect` rows read `identified` there; rerunning produces
  `unexplained_by_representation`. The artifacts are left as recorded rather
  than rewritten — the `correct` and command-count columns the tables draw from
  are computed against the oracle cause and are unaffected.

## Files

    src/riftcode/contracts.py   observations, interventions, budget, diagnosis
    src/riftcode/sandbox.py     disposable repo copies, whitelisted execution
    src/riftcode/observe.py     handle discovery, anonymisation, event stream
    src/riftcode/probes.py      grammar, probe generation, JS-divergence choice
    src/riftcode/loop.py        ledger loop, flake detection, revert gate
    src/riftcode/harness/       PRIVATE fault injector (evaluator-only)
    tests/test_riftcode.py      15 tests incl. isolation and gate behaviour
    tests/test_m0_honesty.py    11 tests pinning the abstention status and
                                recomputing this file's table from raw records
    results/riftcode_demo.json  raw per-run records
