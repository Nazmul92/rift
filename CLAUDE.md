# Claude Code implementation contract — riftagent v1

You are implementing riftagent, not reviewing or redesigning it. Read this
file completely, then read `riftagent_design_v1.2.4.md` completely before
changing code. Read `IMPLEMENTATION_PLAN.md` and `ACCEPTANCE_MATRIX.md` next.
The existing research prototype is under `reference/rift_v2/`.

## Authority and conflict order

1. `riftagent_design_v1.2.4.md` is the product and architecture authority.
   `DESIGN_AMENDMENT_RECORD.md` records the derivation of every clause it
   amends and is where a new amendment is written first.
   `riftagent_design_v1.2.3.md` is superseded for those clauses, byte-identical,
   and retained as history.
2. This file fixes implementation boundaries left open by the design.
3. `IMPLEMENTATION_PLAN.md` fixes milestone scope and exit gates.
4. `ACCEPTANCE_MATRIX.md` fixes the minimum executable acceptance evidence.
5. `reference/rift_v2/` is evidence and reusable mechanism, never authority.

If two authoritative requirements truly conflict, do not silently choose one.
Record the exact conflict and the smallest proposed resolution. Ordinary
implementation choices are yours; do not turn them into design questions.

## Required outcome

Implement the independent Python/pytest riftagent v1 through M2:

- `rift verify <unified-diff> <pytest-node-id>`
- `rift fix <pytest-node-id>`
- `rift why <pytest-node-id>`
- `rift edit "<request>"`
- `rift build "<request>"`
- `rift resume [task-id]`

The result must be installable as one `rift` CLI, use an OpenAI-compatible
HTTP model adapter, stream evidence events as they happen, survive interruption
through ledger replay, and issue only the scoped verdicts in the design.

This is the full product target, not authorization to build every milestone in
one uninterrupted run. Implement exactly one milestone, produce its evidence,
and stop for human review before starting the next.

M2.5 (`build --from-doc`) is not part of the initial implementation. It is
explicitly gated on the M2 build benchmark. Do not implement v2 candidates.

## Non-negotiable architecture

### One source of truth

`.rift/` ledger events are the only durable execution state. Derive the current
phase, budgets consumed, approvals, pending work, and final verdict by replay.
Do not create `state.json`, SQLite checkpoints, pickle snapshots, framework
checkpoints, hidden chat history, or another event log. An in-memory projection
is a disposable cache only.

Every accepted transition is append-and-flush before the following transition.
On crash, absence of the event means the transition did not happen. Never infer
a successful command, model call, approval, patch application, or gate phase
from an in-memory flag.

### Kernel/model isolation

The application loop owns all four model calls. The kernel consumes validated
proposal values. The kernel must never:

- import `riftagent.llm` or `riftagent.app`;
- import a provider SDK or networking package;
- accept an injected model callback that bypasses the import rule;
- read model confidence or allow it to affect a verdict;
- write prose presented as evidence.

Enforce this with AST tests. `app.py` may import both `kernel.py` and `llm.py`.
`llm.py` and `kernel.py` share contracts only through `records.py`.

### No orchestration framework

Do not use LangGraph, LangChain agents, CrewAI, AutoGen, workflow engines,
multi-agent frameworks, checkpoint databases, RAG, embeddings, or a planner.
Use fixed Python command flows and a small explicit phase enum reduced from
ledger events.

### Model has zero execution authority

The model may return only validated values for:

- `propose_spec`
- `propose_hypotheses`
- `propose_handles`
- `propose_change`

It never receives a shell or tool-calling interface. It never returns a command
string to execute. The kernel compiles accepted check and intervention values
into fixed argv arrays. Never use `shell=True` or interpolate model text into a
shell command.

### The judge is frozen

Generated spec/characterization checks and runner configuration are separate
from implementation changes. Once approved, hash and freeze them. Reject a
candidate patch that touches check files, test discovery, or runner config.
The withdrawal phase removes only the implementation patch; spec tests remain.

### Honest degradation

No API key, invalid model output, unsupported sandboxing, unavailable tools,
budget exhaustion, missing checks, and representation failure are explicit
states. Never replace a failed live path with fixture/template output while
claiming the live path ran. Never emit bare `verified` or `done`.

## Minimal repository shape

Keep the runtime close to six substantive modules:

```text
src/riftagent/
  records.py    # immutable contracts, validators, hashes, JSONL ledger/reducer
  kernel.py     # deterministic hypotheses, probe choice, gates, verdict rules
  sandbox.py    # worktrees/copies, isolation, argv execution, budgets
  checks.py     # runner interface, pytest adapter, signatures, CheckSets
  llm.py        # HTTP adapter, prompts, four response validators
  app.py        # fixed loops, CLI, approval, context assembly, renderer
  __init__.py
  __main__.py
tests/
pyproject.toml
```

Small `__init__`, `__main__`, test fixtures, and packaging files do not count
against the six-module target. Do not split modules merely for aesthetics. If
the implementation approaches 9,108 runtime lines by M2 — amended from 8,000 to
8,600 (DAR-008), to 8,700 (DAR-012), to 8,920 (DAR-016), to 9,090 (DAR-017) and
to 9,108 (DAR-018), each with measurements — remove
accidental abstractions before adding more. Tests, fixtures, and benchmarks are
measured separately; the runtime figure must include all shipped support code.

Use Python 3.12. Prefer standard-library dataclasses, enums, `argparse`, JSON,
hashing, subprocess, and HTTP support. Hand-write the small validators first.
Do not add Pydantic, Typer, Rich, an ORM, or a provider SDK unless an actual
measured need is documented and an existing dependency is displaced. Pytest,
Ruff, and mypy are development dependencies and must be exactly pinned in a
reproducible lock or constraints file.

## Work sequence

Do the milestones in order. Never claim a later milestone while an earlier exit
gate is red.

### Milestone review protocol

1. Begin with M0 only.
2. At the end of the current milestone, update `IMPLEMENTATION_STATUS.md` with:
   requirement IDs completed, exact commands actually executed, summarized
   results, changed files, skipped or environment-blocked checks, measured
   runtime line count where applicable, and remaining uncertainty.
3. State one of: `READY_FOR_MILESTONE_REVIEW`, `CONDITIONALLY_READY` with every
   `NOT_RUN_<reason>` disclosure, or `BLOCKED`.
4. Stop. Do not begin the next milestone, create its scaffolding, or make
   anticipatory changes until the user explicitly approves continuation.
5. After approval, preserve the prior status evidence and repeat this protocol
   for M1a, M1, M1.5, and M2.

A disclosed `NOT_RUN_<reason>` is not a passing test. It permits honest review
when the environment lacks an external capability; only the human reviewer may
authorize continuing with that evidence gap.

### M0 — repair the reference evidence first

Work inside `reference/rift_v2/` before creating new runtime code:

1. Replace the overclaim where a surviving constant-false hypothesis is called
   `identified` or a source defect. Its status is
   `unexplained_by_representation`: the current handles did not explain the
   failure; that does not prove a code defect.
2. Reconcile stale synthetic benchmark figures in `RIFTCODE.md` against raw
   results and `REAL_REPO_EVIDENCE.md`. Do not choose the more flattering set.
3. Add reproducible, pinned test/lint/type-check dependencies.
4. Run the existing test, Ruff, and mypy commands in a clean environment.
5. Add regression tests for the corrected abstention status.

M0 is complete only with captured command output. If the snapshot's claims do
not reproduce, correct the docs rather than weakening checks.

Linux/WSL2 is the reference release environment. Native-Windows temporary-
directory permission failures are neither product failures nor passing
evidence: resolve the environment or report M0 `BLOCKED`; never convert them
into confirmation of the documented Linux result.

`unexplained_by_representation` and `representation_inadequate` are deliberately
different-layer names. The former is an internal status in the inherited
research prototype: its current representation failed to explain the result.
The latter is the public riftagent task verdict. Preserve the prototype name in
M0; when porting evidence into the product, map it explicitly at the product
verdict boundary. Do not "clean up" the two layers into one shared enum.

### M1a — standalone verify

Implement and ship `rift verify <diff> <test>` before any LLM-backed agent
behavior. It consumes an external patch and a pre-existing failing pytest node,
then runs the exact baseline-fail/candidate-pass/withdraw-original-fail/reapply
plus preservation gate. It requires the worktree/copy sandbox, check runner,
frozen baseline-signature matching, minimal ledger/reducer, streaming, resume,
receipt, partial-sandbox authority, and cross-platform process-tree control.

M1a makes zero model calls and imports no provider code. Run its dedicated
real-patch benchmark. It must materially reduce incorrect-patch acceptance
while retaining at least 90% of the standard protocol's correct-patch
acceptance. Stop for review after its exit gate. Passing M1a validates only the
acceptance-authority thesis, not proposal quality.

### M1 — fix and why

Extend—not replace—the M1a ledger, runner, sandbox, renderer, receipt, and
resume path with the deterministic kernel port, context selection and
OpenAI-compatible adapter operations needed by `fix`/`why`.

Port the useful mechanisms from `reference/rift_v2`; do not import its private
harness or ship the research package as a runtime dependency. Preserve and
extend its isolation tests.

The M1 gate includes all structural and behavioral tests listed in
`ACCEPTANCE_MATRIX.md`, plus a clean-package install and CLI smoke test.

For an assertion-supported environmental finding that has no safe
apply/withdraw intervention, emit `diagnosis_supported` with
`support: observational` and `gate: not_applicable`. Include the executable
evidence and label remediation unverified. Never count this branch as a
verified fix.

### M1.5 — edit

Implement affected-behavior discovery, baseline characterization checks,
one Spec Card approval, frozen preservation checks, refactor ChangeSet,
regression gate, scoped receipt, and `unverifiable` handling.

Reject null or semantically empty ChangeSets before the preservation gate.
M1.5 is the designated first cut if the runtime budget or benchmark evidence
forces scope reduction.

Characterization proves preservation of observed behavior, not correctness.
The receipt must say exactly what was characterized and what was not checked.

### M2 — build

Implement `propose_spec`, human-readable Spec Card, generated change checks,
predicted baseline signatures, one approval, frozen spec hash, coherent
implementation patch, and the full old-fail/new-pass/withdraw-fail/reapply plus
preservation gate.

Implementation patches cannot modify the approved spec patch or runner config.
If the proposed checks do not fail on old code for their predicted reasons,
repair the spec once and then abstain. Do not proceed to implementation with a
vacuous or invalid spec.

## Required execution behavior

- Use a disposable git worktree for git repositories and a disposable copy for
  non-git repositories.
- Exclude `.rift/` from repository tree hashes.
- Build argv from typed command shapes; never parse model-produced shell text.
- Construct a minimal environment allowlist; never inherit credentials into
  repository processes.
- Apply timeout and process-tree termination everywhere. Native Windows must
  use a tested Job Object or equivalently reliable whole-tree mechanism; if it
  cannot terminate descendants, block execution.
- Use POSIX rlimits where available.
- On Linux, use bubblewrap/user namespaces when available, with network off by
  default. Otherwise disclose `sandbox: partial` and require the separate
  explicit `--allow-partial-sandbox` authority before executing repository
  code. `--yes` approves only a Spec Card and can never authorize partial
  isolation. Record both authorities independently.
- Capture stdout, stderr, exit code, duration, and affected test node outcome.
- Distinguish collection/infrastructure failure from a predicted test failure.
- Match change-check baseline failures by predicted signature, not exit code
  alone.
- Record provider-reported token usage when available; otherwise record
  `unknown`. Do not estimate and present an estimate as measured usage.
- Reject diffs that escape the repository, modify `.rift/`, modify frozen
  checks/config, contain binary patches, or fail `git apply --check`.

## Streaming and replay

The durable transcript is a pure projection of ledger events. Show live:

- command start and elapsed time;
- pytest per-check progress when available;
- hypothesis proposal/elimination;
- gate phase changes;
- preservation counters;
- final scoped receipt.

Spinner frames may use the clock but carry no claim. After completion, killing
the renderer and replaying the ledger must reproduce the identical settled
transcript and receipt byte-for-byte.

## LLM adapter requirements

Ship one OpenAI-compatible chat-completions adapter configured only by:

```text
RIFT_LLM_URL
RIFT_LLM_KEY
RIFT_LLM_MODEL
```

Keep provider HTTP mechanics behind one interface. Use fakes for deterministic
tests. For each operation:

1. append a request-started event before network I/O;
2. perform one bounded request;
3. append response metadata and provider usage;
4. extract JSON without executing or trusting prose;
5. validate every required field, enum, path, budget, and bounded list;
6. allow one schema-repair request;
7. abstain explicitly if validation still fails.

If resume finds `model_request_started` without a durable response event, the
request outcome and cost are unknown. Do not automatically repeat it. Report
the interrupted request and require explicit retry authorization, or abstain
when the remaining interaction policy does not permit another approval.

No provider tool calling. No automatic fallback from one provider to another.
A live smoke test is optional when credentials are unavailable, but its status
must be reported as `NOT_RUN_LIVE_PROVIDER`, never passing by substitution.

Without a configured model, keep deterministic/template hypothesis and handle
discovery available for `why` and `fix`. Emit a verb-appropriate abstention with
reason `model_unavailable` whenever a required spec or source patch cannot be
produced. Missing credentials must not be mislabeled as verified success or as
a repository defect.

## Resume rules

`rift resume [task-id]` scans ledgers, reduces the selected ledger to its phase,
and continues from the first transition without durable completion evidence.
If no task ID is supplied and exactly one incomplete task exists, select it. If
multiple exist, list them and require a choice; do not create an authoritative
mutable "active task" pointer.

Compare the current tracked tree hash to the recorded baseline. Any tracked
drift recreates the candidate sandbox and reruns baseline and affected checks.
Never guess that a changed file is irrelevant.

## Testing discipline

- Write a failing acceptance test before each material behavior.
- Tests must exercise the public CLI or runtime boundary when possible.
- Use fake model adapters with recorded typed responses; never require paid
  credentials for the main suite.
- Include crash injection after every durable-transition boundary and verify
  replay resumes without inventing or duplicating completed work.
- Include adversarial model outputs: invalid JSON, extra fields, path escape,
  shell strings, config/test edits, oversized responses, and misleading
  confidence.
- Include a false-fix fixture where a semantically inert change makes a broad
  suite pass through order dependence; the isolated withdrawal gate must reject
  it.
- Do not weaken or delete a test to make implementation pass.

## Working rules

- Preserve user changes and inspect before editing.
- Keep design files immutable during implementation.
- Do not claim a command ran unless it actually ran in this environment.
- Do not report a milestone complete from unit tests alone when its exit gate
  includes packaging, isolation, replay, or real-repository evidence.
- Do not broaden scope with plugins, server mode, IDE integration, cloud jobs,
  multi-language runners, or M2.5.
- Maintain `IMPLEMENTATION_STATUS.md` with requirements completed, commands
  actually executed, failures, skipped live checks, measured line count, and
  remaining uncertainty.

## Definition of implementation-complete

Implementation-complete means M0, M1a, M1, M1.5, and M2 exit gates pass; if
the human reviewer formally invokes the designated M1.5 cut, record it as
out-of-scope rather than passed. The package installs cleanly and every
in-scope command works against temporary real git repositories;
receipts and settled transcripts replay identically; structural isolation tests
pass; and no known failing requirement is hidden.

Do not call the product thesis validated merely because implementation is
complete. The §15 comparative benchmarks are a separate empirical gate. Final
reporting must distinguish:

- implementation status;
- deterministic acceptance-test status;
- live-provider status;
- real-repository benchmark status;
- product-thesis status.
