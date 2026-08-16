# riftagent — M1a usage

M1a ships the acceptance-authority half of the product and nothing else.
`rift verify` takes a patch that already exists and decides whether the
evidence supports the claim that it fixes a failing check. It invokes no model,
contains no provider code, and does not care what produced the diff.

`fix`, `why`, `edit` and `build` are **not implemented yet**. They are M1, M1.5
and M2.

## Install

```
pip install riftagent          # no runtime dependencies
```

## Commands

```
rift verify <diff> <pytest-node-id>   gate an external patch against a failing check
rift resume [task-id]                 continue an interrupted task from its ledger
rift replay <task-id>                 re-render a completed task's settled transcript
```

Common options: `--repo`, `--json`.

`verify` options:

| option | meaning |
|---|---|
| `--preserve NODE` | a pytest node that must pass both before and after (repeatable). **If none are given, the receipt says so and claims nothing about regressions.** |
| `--allow-partial-sandbox` | authorise executing repository code when full isolation is unavailable |
| `--require-full-sandbox` | refuse to run at all unless full isolation is available |
| `--allow-network` | permit network access inside a full sandbox |
| `--yes` | pre-approve a Spec Card. Accepted for interface stability; it can never grant isolation authority and `verify` has no Spec Card |
| `--max-commands`, `--max-seconds`, `--timeout` | budgets |

## What the gate does

```
baseline     target must FAIL in a pristine worktree; its signature is frozen
candidate    apply the exact diff; target must PASS
withdrawal   reverse the exact diff in the same worktree; the ORIGINAL failure
             signature must return
reapply      re-apply the exact bytes; the tree must match the gated candidate
preservation the declared preservation checks must pass on that tree
```

Passing with the patch is consistent with a stale cache, a retry, an unrelated
edit, or a neighbouring test supplying a missing import. Only the return of the
original failure excludes them.

The target's own test file and the runner configuration (`pytest.ini`,
`pyproject.toml`, `setup.cfg`, `tox.ini`, `conftest.py`) are frozen: a diff that
touches them is rejected before anything runs.

## Verdicts

Every verdict carries its scope. There is no bare `verified`.

| verdict | meaning | exit |
|---|---|---|
| `verified_against_approved_checks` | every gate phase passed, within the stated scope | 0 |
| `unverifiable` | the gate rejected the patch, or could not be completed. `rejected_phase` names where | 2 if rejected, else 1 |
| `regression_blocked` | the change claim held but declared preservation checks failed | 2 |
| `infrastructure_blocked` | the target could not be observed, or isolation authority was missing. Never a statement about the repository | 3 |

Other exit codes: `4` budget exhausted (`censored`), `5` interrupted, `64` usage.

## What a receipt discloses

Result and counts first, then scope: which checks ran, which did **not**, the
isolation level actually used, the two authorities separately, spend, the patch
and check hashes, and remaining uncertainty. `tokens` is
`not_applicable (no model is invoked by verify)`.

## State

Everything durable lives in `.rift/tasks/<task-id>/ledger.jsonl`. Current phase,
budgets and verdict are reduced from it; there is no `state.json` and no second
log. `receipt.json`, `receipt.txt`, `transcript.txt`, `task-contract.json`,
`check-set.json`, `change-set.diff` and `repro.sh` are derived projections, and
replaying the ledger reproduces them byte for byte.

Ctrl-C is always safe. `rift resume` replays the ledger, and any tracked change
to the repository re-establishes the baseline rather than reusing stale
evidence.

## Isolation

| platform | tier |
|---|---|
| Linux with bubblewrap | `full` — host read-only except the worktree and tmp, network unshared |
| Linux without it | `partial` — env allowlist, rlimits, timeout, process-group kill |
| Windows | `partial` — env allowlist, timeout, Job Object process-tree kill. If descendants cannot be terminated, execution is blocked rather than attempted |

A partial sandbox requires `--allow-partial-sandbox` before any repository code
runs, and the receipt states the tier that was actually used. The product does
not promise full isolation everywhere; it promises never to misrepresent the
isolation it had.
