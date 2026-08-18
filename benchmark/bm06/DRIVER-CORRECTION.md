# BM-06 driver — correction pass evidence

Benchmark infrastructure only. No product-runtime change, no new runtime module.
Runtime remains 8,694 / 8,700. No provider request, no spending, no arm run.

## The eleven defects, and where each is now addressed

| # | defect in the previous consuming path | correction |
|---|---|---|
| 1 | A, B and C all invoked the same `rift fix` | `arm_argv` builds a per-arm command; `orchestration_key` fingerprints it |
| 2 | B's random value controlled nothing | B passes `--probe-policy random --probe-seed N` |
| 3 | seed used `hash(case_id)`, unstable per process | `probe_seed` is SHA-256 over manifest seed and case id |
| 4 | arm A's patch never captured | `capture_patch` copies `change-set.diff` verbatim |
| 5 | shadow evaluation always received `None` | it receives arm A's captured bytes; `None` only when no patch exists |
| 6 | `ground_truth_correct` never computed live | independent `rift verify` of the arm's own patch under C's gate |
| 7 | acceptance inferred from return code | read from the receipt verdict |
| 8 | required `case["worktree"]`, absent from manifests | validation fails closed when it is missing or does not exist on disk |
| 9 | proposed manifest `arms`/`budget` empty | validation fails closed |
| 10 | spend copied into rows | stored as `{ledger, event_ids}`; summed from `.rift/spend.jsonl` at report time |
| 11 | tests never executed live arm paths | 29 tests drive the live orchestration against a fake CLI |

## Removal mutations — all five detected

Each mutation was applied to a disposable copy of the tree and the named test
re-run. A mutation that leaves the suite green means the property was never
under test.

| mutation | test that went red |
|---|---|
| arms collapse to one command | `test_the_three_arms_are_three_different_experiments` |
| seed reverts to `hash()` | `test_arm_b_seed_is_stable_across_processes` |
| shadow always receives `None` | `test_ground_truth_correctness_is_set_on_a_live_run` |
| validation never fails | `test_an_invalid_manifest_makes_zero_requests` |
| unsupported arm substituted instead of refused | `test_an_unsupported_arm_is_refused_not_substituted` |

## Recorded conflict: two arms are not expressible by the shipped CLI

`kernel.select_probe` already implements `policy == "random"` and its docstring
names it "the only intended independent variable between benchmark arms B and
C", but `app.py` hardcodes `"disagreement"` at its single call site and no flag
reaches it. Arm A needs a model-alone proposal path that does not exist.

Closing either gap is a product-runtime change, which this pass forbids. The
smallest proposed resolution, for a ruling rather than for action here: expose
`--probe-policy {disagreement,random}` and `--probe-seed N` on `fix`, defaulting
to today's behaviour, plus `--model-alone`.

Until then the driver probes `rift fix --help` and records any arm it cannot
express as `NOT_RUN_ARM_UNSUPPORTED`. It never substitutes another arm's
command: three identical runs labelled A, B and C is the failure this refusal
exists to prevent repeating.

## Validation of the proposed 15-case manifest

Fails closed. Not frozen, not run. Full output in `manifest-validation.txt`:
empty `arms` and `budget`; every one of the 15 cases has an empty preservation
set and no materialized worktree; `filelock-fc277001-order_dependence` also has
no expected signature and no exact reproducer despite being order-dependent.

**Zero cases are currently runnable.** Preservation sets, worktree
materialization and order-dependent reproducers are prerequisites the curation
passes never produced. A narrower preliminary benchmark is therefore not yet
executable, and the obstacle is curation rather than driver correctness.

## Deterministic checks, pinned toolchain

`ruff 0.16.3` check and format clean over `src tests benchmark`; `mypy src`
clean; `tests/test_bm06_driver.py` 29 passed.
