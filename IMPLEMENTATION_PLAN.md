# riftagent v1 implementation plan

This plan translates the frozen design into a bounded build. It does not add a
planner or a new architecture. The product remains one fixed loop with four
verbs plus resume.

## 1. Delivery boundary

The initial implementation ends at M2:

| Milestone | User capability | Entry condition | Exit condition |
|---|---|---|---|
| M0 | trustworthy prototype baseline | supplied snapshot | honesty status fixed, docs reconciled, clean reproducible gates |
| M1a | `verify`, `resume` | M0 green | zero-LLM counterfactual gate, sandbox, replay, receipt and verify benchmark green |
| M1 | `fix`, `why`, `resume` | M1a green | structural, diagnosis, proposal, context and CLI tests green |
| M1.5 | `edit` | M1 green | characterization and preservation flow green |
| M2 | `build` | M1.5 green | approved spec and counterfactual feature gate green |

Do not implement M2.5 or v2. Comparative benchmarks are built as executable
harnesses, but implementation completion and thesis validation remain separate.

Implementation proceeds one milestone per reviewed checkpoint. After each exit
gate, write `IMPLEMENTATION_STATUS.md` and stop. A capability-dependent check
may be disclosed as `NOT_RUN_<reason>` only where the acceptance matrix permits;
that is an evidence gap, not a pass, and requires human approval before the next
milestone begins.

If the human review formally invokes the designated M1.5 scope cut, record that
decision and its reason in `IMPLEMENTATION_STATUS.md`; M2 may then enter from a
green M1 without pretending M1.5 passed.

## 2. Runtime dependency direction

```text
app ─────────> llm
 │              │
 │              └────> records
 ├──────────> kernel ─> records
 │              ├────> checks
 │              └────> sandbox
 ├──────────> checks ─> records + sandbox
 └──────────> records

kernel -X-> llm, app, provider/networking, renderer
llm    -X-> kernel, sandbox, checks
```

The application flow is explicit Python. Do not build a generic graph, node
registry, dynamic router, event bus, dependency-injection container, or plugin
system.

## 3. Durable data contracts

Use immutable dataclasses/enums and strict `from_dict` validators. Reject
unknown fields in model responses. Durable records carry `schema_version` and
are serialized using canonical JSON (sorted keys, stable separators, UTF-8).

### TaskContract

Minimum fields:

- task ID and verb;
- exact user request or pytest target;
- repository root and baseline tracked-tree hash;
- declared scope and constraints;
- command, elapsed-seconds, token and repair budgets;
- approved repository build-command argv, if any;
- requested and actual sandbox level;
- approval provenance and approved Plan/Spec decisions;
- frozen content hash after approval.

Before build/edit approval it is provisional. After approval it is immutable.
Fix/why freeze the contract after target validation and any required partial-
sandbox authorization.

### Check and CheckSet

Each check contains:

- stable check ID;
- `change` or `preservation` claim type;
- runner kind;
- typed arguments, never a shell string;
- expected baseline and candidate outcomes;
- predicted failure signature for every change check;
- timeout and declared scope.

The CheckSet contains checks, spec/characterization patch hash, protected paths,
runner/discovery configuration hash, approval provenance and content hash. Once
approved, the exact bytes of tests and config are frozen.

### EvidenceLedger event

Each JSONL event contains:

- schema version, task ID, monotonically increasing sequence and event ID;
- UTC timestamp for display/audit, never for control-flow ordering;
- event kind;
- validated payload.

Minimum event families:

- task/contract: started, authorized, frozen, stopped;
- context/baseline: context selected, baseline started/finished, drift detected;
- model: request started, response received, invalid response, repair requested;
- spec: proposed, baseline validated, approved, edited, cancelled, frozen;
- diagnosis: hypotheses proposed, probe selected, hypothesis eliminated,
  equivalence class remained, cause supported, representation inadequate;
- execution: command started, progress, finished, infrastructure blocked;
- change/gate: patch proposed, validated, applied, withdrawn, gate phase finished,
  regression blocked;
- terminal: receipt emitted.

Append one complete JSON line and flush before moving on. A reducer validates
sequence and event legality and produces a `TaskProjection`. It must fail closed
on a malformed middle event. Define and test safe behavior for an incomplete
final line caused by a crash; never silently accept malformed completed data.

### ChangeSet

Minimum fields: canonical unified diff, patch hash, touched paths, originating
operation, attempt number, validation result. A gated patch is immutable.

### VerificationReceipt

Minimum fields:

- exact scoped verdict;
- task, contract, spec/check and patch hashes;
- approval provenance;
- baseline/candidate/withdrawal/reapplication outcomes per check;
- preservation results;
- checks not executed;
- sandbox level;
- commands, seconds and provider-reported/unknown tokens;
- remaining uncertainty, censorship and infrastructure status;
- paths to `repro.sh` and exact diff.

Receipt generation is deterministic and model-free.

## 4. Fixed application flows

### `verify <diff> <test>`

1. Validate the external unified diff and pytest node; create the task ledger.
2. Obtain `--allow-partial-sandbox` authorization when full isolation is
   unavailable. `--yes` is irrelevant and cannot grant this authority.
3. In a pristine baseline worktree, reproduce the target failure and capture
   its exact signature.
4. Apply the accepted diff in a fresh candidate worktree and require the target
   to pass.
5. Withdraw only that diff in a fresh worktree and require the original failure
   signature to return.
6. Reapply the exact content-addressed diff and run declared preservation
   checks.
7. Emit the scoped receipt and replayable settled transcript.

This flow makes zero model calls and evaluates no diagnosis. Its benchmark must
materially reduce incorrect-patch acceptance while retaining at least 90% of
the standard protocol's correct-patch acceptance before M1 begins.

### `fix <test>`

1. Validate repository and pytest node ID; create task ledger.
2. Obtain partial-sandbox authorization if required; freeze TaskContract.
3. Create clean baseline worktree and reproduce the target failure.
4. Capture its predicted signature from actual baseline evidence.
5. Run deterministic handle discovery and hypothesis population.
6. If ambiguity remains, call `propose_hypotheses`; validate and add proposals.
7. Select probes by disagreement per estimated cost; execute and eliminate.
8. If the representation is inadequate, call `propose_handles` once; accept
   only compositions of existing safe primitives and re-enter the same loop.
9. If a cause is supported, call `propose_change` once per bounded attempt.
10. Validate/apply one coherent candidate patch.
11. Gate: baseline failure/signature -> candidate pass -> withdraw patch and
    reproduce failure/signature -> reapply exact patch -> preservation checks.
12. Emit a scoped receipt or explicit abstention/block verdict.

If an executable assertion supports an environmental finding but no safe
apply/withdraw intervention exists, stop before patch generation and emit
`diagnosis_supported` with `support: observational`, `gate: not_applicable`,
the supporting evidence, and a remediation explicitly labeled unverified.
This branch earns diagnosis credit only on benchmark classes frozen as
observationally diagnosable; it never earns verified-fix credit.

An already-passing target is not a verified fix. Report that reproduction was
not established.

### `why <test>`

Perform steps 1–8 from fix. Do not request or apply a ChangeSet. Emit
`diagnosis_supported`, `underdetermined`, `representation_inadequate`,
`infrastructure_blocked`, or a censored current verdict with the surviving
hypotheses, contradicted alternatives, probes and unresolved equivalence
classes.

### `edit "request"`

1. Inspect affected public behavior using deterministic context selection.
2. Run existing relevant checks.
3. Call `propose_spec` in characterization mode for uncovered behavior.
4. Validate that characterization checks pass on the baseline.
5. Present one Spec Card; record explicit/`--yes` approval or cancellation.
6. Freeze the characterization patch, CheckSet and TaskContract.
7. Call `propose_change`; reject any change to frozen judge paths/config.
8. Reject a null or semantically empty ChangeSet; then apply the refactor and
   run all preservation checks.
9. Emit a receipt describing exactly what was characterized. If the request
   has no falsifiable improvement criterion, stamp the result `unverifiable`
   even when preservation checks pass.

M1.5 is the designated first scope cut if the runtime ceiling or benchmark
evidence requires one.

### `build "request"`

1. Select bounded repository context.
2. Call `propose_spec`; validate plain behaviors, test patch, typed checks and
   predicted signatures.
3. Apply only the spec patch to an old-code worktree.
4. Confirm every change check fails for its predicted reason and every
   preservation check passes. One repair call is allowed for an invalid spec.
5. Present one Spec Card; record approve/edit/cancel. An edit becomes explicit
   user constraint and permits a regenerated Spec Card; it is never silently
   reinterpreted.
6. Freeze the TaskContract, spec patch, CheckSet and runner config hashes.
7. Call `propose_change` for one coherent implementation patch.
8. Reject changes to the frozen judge, unsafe paths, invalid/binary diffs or
   patches outside approved scope.
9. Gate with spec retained throughout:
   old code fails predicted change checks -> new code passes -> withdraw only
   implementation and observe predicted failures -> reapply exact patch -> run
   preservation checks.
10. Emit a scoped receipt. If change checks pass but preservation fails, emit
    `regression_blocked`; do not silently fix unrelated regressions.

### `resume [task-id]`

1. Discover incomplete tasks by replaying ledgers.
2. Select exactly one or require the user to choose.
3. Recompute the repository tracked-tree hash.
4. Identical hash: continue at the first non-durable transition.
5. Any drift: append drift event, recreate sandbox, rerun baseline and affected
   checks, then continue from fresh evidence.
6. Never charge again for a recorded completed model response; never treat a
   started-without-finished command as completed.

A `model_request_started` event without a durable response has unknown outcome
and possibly incurred cost. Resume must not automatically repeat it. Require
explicit retry authorization or emit the verb-appropriate abstention.

## 5. Check engine

Define a runner protocol that accepts a typed check and returns one normalized
result. Pytest assumptions exist only in `PytestRunner`.

M1 requires pytest node execution, collection and normalized target outcomes.
By M2, support deterministic shapes for type checker, linter, repository build,
file assertion and dependency assertion. The repository build argv enters only
through the approved TaskContract.

Distinguish:

- expected target failure;
- wrong-signature target failure;
- unrelated preceding-test failure;
- collection/configuration failure;
- timeout/termination;
- missing executable or dependency;
- sandbox infrastructure failure.

The last four cannot satisfy a change check.

## 6. Sandbox and patch boundary

- Prefer git worktrees rooted outside the repository; use a disposable copy for
  non-git repos.
- The runtime never executes from the developer's working tree.
- Every subprocess receives explicit cwd, argv, environment and timeout.
- Capture and terminate the whole process tree on timeout/Ctrl-C.
- Strip model/provider/cloud/git credentials and other secrets from the child
  environment; pass only the documented allowlist plus approved interventions.
- Full Linux isolation uses bubblewrap/user namespaces and disables network.
- If unavailable, disclose partial isolation and require the separate explicit
  `--allow-partial-sandbox` authority before repository code runs. `--yes`
  approves only a Spec Card and never grants safety authority.
- Native Windows uses a tested Job Object or equivalently reliable whole-tree
  termination. If descendants cannot be terminated, block execution.
- Patch validation rejects absolute paths, `..`, symlink escape, `.git`,
  `.rift`, binary patches, frozen paths/config, and unapproved files.
- Use `git apply --check` before application and verify the resulting tree diff
  equals the accepted ChangeSet.

## 7. Context selection

No RAG or embeddings. Use one deterministic bounded pipeline:

1. traceback/test target paths;
2. direct Python import neighbors;
3. symbol/file grep matches;
4. hard per-file, total-character and estimated-token caps;
5. explicit truncation metadata in the model request and ledger.

Ordering must be stable. Secrets and `.rift/` are excluded. Add unit fixtures
and one pinned Django-scale repository test measuring that relevant traceback
and import files survive the cap. If context is insufficient, abstain or request
safe handles; do not grant file-fetch tools to the model.

## 8. Model operation contracts

Prompts may evolve; output schemas are fixed and versioned.

### `propose_spec`

Returns: numbered plain-language behaviors, mode (`build` or
`characterization`), unified test-only patch, typed checks, predicted baseline
signatures for change checks, declared assumptions and unverified aspects.

### `propose_hypotheses`

Returns 3–6 hypotheses in the inherited executable IR with stable IDs, required
handles and predicted outcomes. Reject confidence as an input to scoring.

### `propose_handles`

Returns bounded compositions of only `env`, `unsetenv`, `clear`, `first`, and
file/dependency assertion primitives. Reject novel executable primitives,
commands, code snippets or unconstrained paths.

### `propose_change`

Returns one canonical text unified diff plus a non-authoritative summary. The
diff is validated separately. No command, tool call or test weakening.

When no model is configured, retain deterministic/template hypothesis and
handle discovery for fix/why. If a spec or source patch is required, emit the
verb-appropriate abstention with explicit `model_unavailable` reason; never
silently substitute fixture output.

## 9. CLI and artifacts

Use `argparse` unless a demonstrated UX requirement cannot be met. Required
common options: repository path, command/seconds/token budgets, repair count,
`--yes`, `--allow-partial-sandbox`, `--allow-network`, sandbox requirement, model configuration overrides
without exposing secrets, and task ID for resume.

Exit codes must distinguish success, abstention/unverifiable, regression or
verification rejection, infrastructure block, censorship and user cancellation.
Document the mapping and test it.

Each task directory contains only authoritative records and review artifacts:

```text
.rift/tasks/<task-id>/
  ledger.jsonl
  task-contract.json
  check-set.json          # absent for why until appropriate
  change-set.diff         # absent when no change exists
  receipt.json            # terminal tasks only
  receipt.txt             # deterministic projection
  repro.sh                # fixed, safely quoted reproduction argv
```

Derived files must be reproducible from ledger and immutable records. They are
not independent execution state.

## 10. Packaging and verification

- `pyproject.toml` exposes `rift = riftagent.app:main`.
- Build wheel and sdist; install wheel into a clean environment.
- Run tests against the installed wheel, not only source-tree imports.
- Run Ruff and mypy with pinned versions.
- Generate CLI help and a short README showing each verb and every verdict.
- Measure runtime line count at M1a, M1 and M2 against the 8,600-line M2
  disclosure ceiling (amended once from ~8,000; DAR-008); report
  tests/fixtures/benchmarks separately.
- Keep benchmark harnesses and fixtures outside the six runtime modules.
- Record all actually executed commands and skipped external checks in
  `IMPLEMENTATION_STATUS.md`.

## 11. Benchmark accounting

Freeze case ground truth and gateability (`gateable`,
`observationally_diagnosable`, `neither`) before any arm runs. The agent cannot
select its class or remove a task from a denominator.

```text
verified_fix_yield =
  ground-truth-correct gate-passed fixes / all attempted gateable tasks

observational_diagnosis_yield =
  correct supported findings / all attempted observationally-diagnosable tasks

correct_feature_yield =
  ground-truth-correct accepted features / all attempted build requests

cost_per_correct_outcome =
  total cost over every attempted task / ground-truth-correct outcomes
```

Abstentions remain attempted tasks. An all-abstain arm has zero yield and
undefined/infinite cost per correct outcome. M1 requires C to materially reduce
false-fix acceptance, retain at least 90% of A's correct-fix yield on the frozen
gateable set, and lower token cost per correct fix. M2 requires materially lower
wrong-thing-built rate, at least 90% of A's correct-feature yield, and no higher
token cost per correct feature.
