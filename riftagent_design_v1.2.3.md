# riftagent — Design Document v1.2.3

Status: FROZEN for implementation (M0/M1a next). Redesign trigger: §15
benchmark data only. Incorporates the RIFT-Code prototype evidence, three Sol
review rounds, and the implementation-boundary review (2026-08-15).
v1.1: execution model (§10.1), UX rules (§10.2–10.4), approval round-trip
metric (§15), doc-scale build (§17).
v1.2 (Sol round 2, eight corrections): process isolation to M1 with
disclosed sandbox level (§4.1); P3 split by check type; false confinement
claim removed; TaskContract provisional-until-approval; resume drift
detection; `diagnosis_supported`; provider-adapter honesty;
`propose_handles` constrained to composition of primitives; premature
empirical claims reworded.
v1.2.1 (Sol round 3, freeze pass): "40 turns" figure removed entirely
(§2, §11); executed-code danger vs command-shape safety corrected +
partial-sandbox session authorization (§10.3); isolation floor tiered by
platform (§4.1); fail-safe resume drift rule (§10.3); Anthropic compat
qualified, native adapter -> v2 (§8, §16); token accounting honest (§10.3);
abstention-channel claim softened (§2); approval provenance in receipts
(§9).
v1.2.2 (implementation-boundary freeze): the application loop, not the
kernel, owns LLM invocation; an AST-enforced kernel/LLM import boundary;
execution state derived only by ledger replay; settled rendering reproducible
from the ledger; no orchestration framework or second checkpoint system in
v1; dependency minimalism made an implementation acceptance rule (§4.2,
§10.2, §12, §16).
v1.2.3 (pre-implementation falsifier pass): verified-fix yield closes the
all-abstain benchmark loophole; observational/ungatable diagnoses are scoped;
the simplicity budget is restated at ~8,000 runtime lines; `--yes` is separated
from partial-sandbox authorization; Windows process-tree containment is made
explicit; null edits are rejected; standalone `rift verify` becomes M1a, the
first shippable slice (§4.1, §7, §9–§16).
Everything below is implementation work; no architectural questions remain
open except the empirical one named in §15.

---

## 1. Thesis

An independent, LLM-agnostic coding agent in which a deterministic kernel
holds all epistemic authority and a language model is a bounded proposal
subroutine.

> The LLM proposes what should be true and how to change the code.
> The kernel and executable checks decide what the evidence supports.

Or, in the reviewer's sharper formulation (Sol): other agents optimize how
quickly a model changes code; riftagent optimizes whether the resulting
claim can be justified by executable evidence.

One invariant governs every feature:

> **No claim without an executable check.**

## 2. Problem

riftagent targets four failure modes of current coding agents (Claude Code,
Cursor, Codex class) — one measured directly, three observed or hypothesized.
The §15 benchmarks determine whether they occur materially and whether this
design improves them:

1. **Verification theatre** (measured — as a protocol weakness, not per
   product). On `pallets/click` @ 8b44edf, a semantically inert edit (a
   comment) was accepted as a fix by both standard verification protocols
   (run the full suite; run the test's file), because an order-dependent
   import masked the failure. An isolated counterfactual gate rejected it in
   0.7s. This measures the protocols agents use, not any named agent
   individually.
2. **Single-theory commitment.** One mental model of the bug, patched against
   until it thrashes. Ablation on 6 fault families x 3 seeds: disagreement-
   driven elimination 18/18 correct causes at 12.2 mean commands; random
   probing 10/18 at 21.4; greedy 0/18.
3. **Token burn from re-derivation** (hypothesized; measured in §15). The
   model re-reads logs and rebuilds its mental state every loop iteration;
   evidence lives in prose, not in a persistent structure.
4. **Uncalibrated confidence.** "Done!" is emitted with identical confidence
   for verified and unverifiable work; public workflows do not expose the
   standardized, machine-readable abstention verdicts used here.

riftagent is designed against these four, and its evaluation (§15) measures
exactly these four.

## 3. Principles

P1. **Kernel authority.** Selection of experiments, elimination of
    hypotheses, and acceptance of results are deterministic and LLM-free.
P2. **Bounded proposal.** The LLM is invoked at four named operations only
    (§8); its output is typed, validated, and stripped of authority — model
    confidence is discarded, only testable predictions are kept.
P3. **Counterfactual acceptance, by check type.** Change claims require
    baseline failure (with predicted signature), candidate success, and
    failure on withdrawal. Preservation claims require baseline success and
    candidate success. Nothing is "done" without the evidence its claim type
    demands (§7).
P4. **Scoped honesty.** Every verdict carries its verification scope; the
    agent abstains (`unverifiable`, `representation_inadequate`) rather than
    confabulate.
P5. **Simplicity budget.** One binary, one state directory, three env vars,
    ~6 substantive runtime modules, ≤ ~8,000 runtime lines at M2. Tests,
    fixtures, and benchmark harnesses are measured separately, never hidden in
    the runtime number. The estimate reflects crash-safe ledger/replay,
    cross-platform process control, isolation tiers, four validated model
    schemas, context selection, streaming, receipts, and the CLI. Any addition
    must still displace something; 8,000 is a disclosure ceiling, not a target.
P6. **Degrade, never block.** No API key -> template proposers still diagnose
    environmental causes and abstain on the rest. A weak model costs more
    probes and more gate rejections — never unverified claims. Verdict
    quality is bounded by check quality (frozen checks can correctly verify
    an inadequate spec within its declared scope), which is why every verdict
    carries its scope (P4).

## 4. Architecture

```
 user ──> CLI / application loop ───────────────> LLM client
              │                           four typed operations │
              │ validated proposals                             ▼
              ▼                                      OpenAI-compatible endpoint
        ┌───────────── kernel (deterministic, 0 tokens) ─────────────┐
        │ checks -> baseline -> hypotheses -> probe selection ->      │
        │ elimination -> gate evaluation -> verdict                   │
        └────────────────────────┬────────────────────────────────────┘
                                 │ typed command plans only
                                 ▼
                       check engine + sandbox
                                 │
                                 ▼
                     .rift/ ledger + records
```

Components (complete list — anything absent is out of scope):

1. **Sandbox** — disposable git worktrees (directory copy for non-git
   repos) plus the process-isolation floor of §4.1. Cheap resets are what
   make counterfactuals possible. Worktrees give reset semantics; they do
   NOT confine executed code — isolation does (§4.1).
2. **Kernel** — hypothesis scoring, elimination, disagreement-per-cost probe
   selection, bisection, gate evaluation, verdict derivation. Ports
   `rift.hypothesis`, `rift.population`, `riftcode` probe economics. It accepts
   validated proposal data but cannot import or invoke the LLM client.
3. **LLM client** — a ~50-line provider-adapter interface
   (messages -> text); the KERNEL is provider-agnostic, v1 ships exactly one
   adapter: OpenAI-compatible chat completions (OpenAI, Ollama, vLLM,
   llama.cpp; Anthropic exposes a compatibility layer documented as
   evaluation-oriented, so production Anthropic support is a native adapter,
   listed v2). "Any LLM" means "any endpoint an adapter exists for", not an
   automatic universal promise. One repair-retry on invalid JSON. No SDKs,
   no provider tool-calling.
   Config: `RIFT_LLM_URL`, `RIFT_LLM_KEY`, `RIFT_LLM_MODEL`.
4. **Check engine** — runs CheckSets, matches failure signatures, produces
   scoped results.
5. **Records store** — `.rift/` directory holding the five records (§5).
6. **CLI/application loop/renderer** — the thin fixed workflow driver. It
   invokes the LLM client at the four named call sites, validates returned
   data, passes proposals into the kernel, appends transition events, and
   streams the ledger. Receipt and settled-transcript rendering are
   deterministic and contain no LLM-authored narration.

### 4.1 Process isolation (M1 trust floor)

Argv whitelisting stops injection INTO the agent; it does nothing about code
run BY the agent — pytest executes arbitrary repository code that can read
credentials, write outside the worktree, and reach the network.

M1 floor, tiered by platform:
- all platforms: environment constructed by allowlist (credentials never
  inherited), per-command timeout, process-tree termination;
- supported POSIX/Linux platforms: memory/process rlimits;
- Linux with bubblewrap/user namespaces: host filesystem read-only except
  worktree and tmp; network disabled by default (`--allow-network` opt-in).

Native Windows process-tree termination is an M1 requirement, implemented with
a Job Object or an equivalently tested whole-tree mechanism (`taskkill /T` may
be a fallback only if the acceptance test proves descendants are terminated).
If reliable tree termination is unavailable, execution is
`infrastructure_blocked`, not merely disclosed as partial. Linux/WSL2 is the
reference environment for full-isolation and release evidence; native Windows
receipts remain `sandbox: partial` in v1.

**Disclosure rule:** every receipt states the isolation level it actually ran
under (`sandbox: full | partial`). A disclosed partial sandbox is a trust
floor; an undisclosed one is the verification theatre this product exists to
eliminate. v1 does not promise full isolation on every platform — it promises
never to misrepresent the isolation it had.

### 4.2 Structural implementation boundaries (M1)

These are executable architecture constraints, not naming conventions:

1. **The loop owns model calls.** The application loop imports both the LLM
   client and the kernel. The kernel never imports the LLM client, provider
   adapters, networking modules, CLI, or renderer and never invokes a model.
   Model responses are schema-validated outside the kernel and enter it only
   as typed proposal data. An AST test enforces the import boundary.
2. **The ledger is the sole durable execution state.** Current phase and all
   resumable state are reduced from ledger events. No mutable `state.json`,
   checkpoint database, framework checkpoint, hidden conversation state, or
   second event log exists. An in-memory projection is permitted only as a
   disposable cache; after interruption it is reconstructed from the ledger.
3. **Transitions are write-before-advance.** Every accepted transition is
   appended and flushed before the next transition starts. If no durable event
   exists, the transition did not occur. LLM usage metadata is recorded before
   its result can influence a later step, so interruption cannot erase paid
   work.
4. **Rendering has no epistemic state.** A completed ledger replay must produce
   the identical settled transcript and receipt. Spinners and elapsed-time
   ticks may be transient projections of a recorded `command_started` event,
   but they cannot add durable claims. Per-check progress that affects the
   settled transcript is recorded as events.
5. **No orchestration framework in v1.** No LangGraph, LangChain agent runtime,
   workflow engine, or other checkpointing/control-state framework. It would
   duplicate responsibilities deliberately owned by the loop and ledger.
6. **Dependencies must earn their place.** M1 starts with standard-library
   dataclasses and explicit validators for the small fixed schemas. A schema
   framework such as Pydantic is admitted only if measured schema growth makes
   the hand-written validation less auditable. CLI framework choice is a leaf
   concern and cannot alter the architecture.

## 5. Data model — five durable records

| record | content | mutability |
|---|---|---|
| **TaskContract** | request text, verb, scope, constraints (incl. Plan Card decisions), budgets (commands, seconds, tokens, repair retries) | provisional until scope/spec approval, then frozen + content-addressed |
| **CheckSet** | executable checks, each typed `change` or `preservation`, with expected baseline/candidate results and predicted failure signatures | frozen at approval; content-addressed hash |
| **EvidenceLedger** | append-only JSONL: every command, outcome, hypothesis status change, gate phase | append-only |
| **ChangeSet** | exact patch, content-addressed hash | immutable once gated |
| **VerificationReceipt** | check hash, patch hash, per-check results, verification scope, checks NOT executed, remaining uncertainty | immutable |

The ledger is simultaneously the sole durable control state, memory, resume
file, streaming UI feed, and audit trail. Current execution phase is always a
deterministic reduction of ledger events; it is never stored independently.
One structure, five jobs.

## 6. Check model

### 6.1 Checks, not tests

A check is any executable with a deterministic pass/fail interpretation.
v1 check types (deterministic only):

- unit / integration test (pytest)
- type checker, linter, build command
- file / dependency assertion (path exists, version pinned, symbol absent)

**Deferred to v2, explicitly:** benchmarks-with-thresholds, browser checks,
anything stochastic. These require repeated-run statistical gating (the flaky
machinery is unbuilt); admitting them in v1 would silently soften the gate's
guarantees.

### 6.2 Change checks vs preservation checks

| type | baseline | candidate |
|---|---|---|
| change | must FAIL (with predicted signature) | must PASS |
| preservation | must PASS | must PASS |

This split is the abstraction that unifies the verbs: `fix` = one
pre-existing change check + related preservation checks; `build` = generated
change checks + preservation checks; `edit` = (almost) all preservation;
`why` = probes only, no CheckSet.

### 6.3 Predicted failure signatures

Every change check carries an expected baseline failure signature (exception
type and/or message pattern), emitted at proposal time. The kernel verifies
the check fails **for the predicted reason** at baseline. A check predicted
to fail with `404` that fails with `ImportError` is an invalid check: one
repair attempt, then abstain. (Lesson from the R1 instrumentation bug: a
failure's existence is weaker evidence than its identity.)

## 7. The gate

### 7.1 Frozen checks — single enforcement mechanism

After approval, the CheckSet and runner configuration are content-addressed,
read-only inputs. The gate executes in a **pristine worktree** where the
ChangeSet is applied and structurally cannot modify check files, runner
config, or test discovery. A ChangeSet that touches them is rejected
automatically and the flow returns to spec approval. This one mechanism
subsumes detection of skips, deleted tests, weakened assertions, config
changes, and narrowed discovery. The model cannot satisfy its own judge.

### 7.2 Gate per verb

**verify** — accept an external unified diff and pre-existing failing check;
reproduce baseline and freeze its observed failure signature; apply the exact
diff in a pristine worktree; require candidate success; withdraw the diff and
require the original failure signature to return; reapply the exact diff; run
declared preservation checks; emit a scoped receipt. No LLM, diagnosis loop,
or source proposal. This validates the acceptance-authority half of the thesis
only; it says nothing about proposal quality.

**fix** — reproduce cleanly; diagnose if ambiguous (hypothesis loop);
freeze baseline evidence; one candidate ChangeSet; target passes with patch;
withdraw patch -> failure returns; reapply exact patch; run preservation
checks; scoped receipt. If an executable assertion supports an environmental
finding but no safe intervention can apply/withdraw it (for example a missing
binary or dependency version), do not fabricate a gate or request a patch.
Emit `diagnosis_supported` with `support: observational`,
`gate: not_applicable`, the supporting checks, and a clearly unverified
remediation note. It is a diagnosis outcome, never a verified fix.

**build** — inspect context; propose spec (human-readable card + executable
checks + predicted signatures); run checks at baseline; confirm change checks
fail for predicted reasons; stream Spec Card for one approval; freeze hash;
one coherent implementation ChangeSet; change checks pass; withdraw
implementation (spec retained) -> change checks fail again; reapply; run
preservation checks; scoped receipt.

**edit** — identify affected public behaviour; run existing checks; generate
characterization checks for uncovered behaviour; confirm they pass at
baseline; freeze; refactor; preservation checks; receipt states exactly what
was characterized. Characterization checks pin observed behaviour — including
existing bugs — and are never described as correctness specifications. A null
or semantically empty ChangeSet is rejected before the preservation gate; it
cannot satisfy an edit request merely because the baseline already passes.

**why** — reproduce; competing hypotheses; discriminating probes;
elimination; return `diagnosis_supported` / `underdetermined` /
`representation_inadequate`. No ChangeSet, no gate.

### 7.3 Repair policy

One coherent ChangeSet per attempt — never one patch per check (overfitting,
fragmentation, token waste). On failure, only the failure evidence is
returned to the model for a repair call. Configured retry budget; then
abstain. Streaming still shows per-check progress as checks flip.

## 8. LLM interface — four operations

All: messages in, typed JSON out, schema-validated before use, one
repair-retry, confidence discarded.

| op | reads | emits | fires |
|---|---|---|---|
| `propose_spec` | request, repo context | Spec Card + checks + signatures | build, edit (characterization) |
| `propose_hypotheses` | failure context, source, handle list | 3–6 causal hypotheses in the IR | fix (ambiguous), why |
| `propose_handles` | full ledger | new variables/measurements COMPOSED from existing safe primitives (env, unsetenv, clear, first, file/dep assertion) — never a new executable primitive; if no composition can perform the experiment -> `representation_inadequate` | only on `representation_inadequate` signal |
| `propose_change` | verified cause or approved spec, source | one ChangeSet (diff) | fix, build, edit |

Typical call counts: simple fix = 1; ambiguous fix = 2–4; build = 2 + repairs;
why = 1–2. Everything between calls is shell + kernel.

Source-reading lives entirely in the LLM operations. The kernel returns a
deterministic context selection (tracebacks, imports, and grep); the
application loop reads that selection and constructs the model request. The
kernel does not perform the HTTP call. No embeddings or RAG in v1.

## 9. Verdicts

```
spec_pending_approval
verified_against_approved_checks     (never bare "verified")
diagnosis_supported                  (why/fix: surviving hypothesis or observed
                                      environmental finding, support level,
                                      contradicted alternatives, unresolved
                                      equivalence classes, gate status)
underdetermined
representation_inadequate            (never mislabeled "code defect")
regression_blocked
unverifiable
infrastructure_blocked               (broken sandbox ≠ underdetermined)
```

Every completed verdict carries: verification scope, checks executed, checks
NOT executed, patch hash, spec hash, spec-approval provenance
(`explicit | --yes` — a reviewed spec and a pre-authorized one must not look
identical), sandbox level, remaining uncertainty. Diagnosis receipts also carry
`support: interventional | observational` and
`gate: passed | failed | not_applicable`. `gate: not_applicable` never counts as
a verified fix. Any remediation generated from an observational finding is
labeled unverified. Example receipt:

```
✓ Verified against approved checks
  Change checks:        5/5 passed (failed at baseline as predicted)
  Preservation checks: 31/31 affected checks passed
  Full repository suite: NOT run
  patch a1b2c3…  spec 9f8e7d…
```

## 10. CLI, execution model & UX

```
pip install riftagent
export RIFT_LLM_URL=… RIFT_LLM_KEY=… RIFT_LLM_MODEL=…
rift verify candidate.diff tests/test_x.py::test_y
rift fix  tests/test_x.py::test_y
rift build "add CSV export to /records"
rift edit  "extract the retry logic into a helper"
rift why   tests/test_x.py::test_y
rift resume
```

### 10.1 Execution model — the model never gets a shell

The LLM never emits an executable shell string. Its proposals are typed data
(check definitions, hypotheses over handles, diffs); after external schema
validation, the kernel compiles them into argv arrays. No string interpolation
into a shell — injection is structurally impossible, not filtered. The
whitelist is not a list of
forbidden commands; only ~6 command SHAPES exist in the codebase: test
runner, type checker, linter, build command, file/dependency assertion,
git/worktree ops.

Every execution: cwd pinned to the worktree, environment explicitly
constructed (env/unsetenv interventions are env-dict edits, never `export`),
per-command timeout, stdout/stderr captured, one ledger event, one budget
unit. The only non-kernel-authored command is a repo's own build command:
it enters via the TaskContract, is user-approved once, then runs under the
same discipline.

There is no tool-calling protocol. Exactly two external interfaces exist:
the LLM endpoint (plain HTTP chat completions) and the repo's toolchain
(kernel-authorized processes). Ordinary context needs are pre-empted — the
kernel selects context (traceback -> import graph -> grep, token-capped) and
the loop assembles it before each call; the model reads, it does not fetch.
Extraordinary needs go through `propose_handles`: the model requests a
measurement as data, the
kernel decides whether to run it, results land in the ledger. Tool use with
the authority inverted; the kernel can refuse.

### 10.2 Streaming — the ledger, rendered live

A deterministic renderer maps each ledger event to a line the moment it is
appended: hypothesis ◇, command ▶ (live spinner + duration, then outcome),
elimination ✗, gate phase ✓/✗, check flip, receipt. Narration cannot diverge
from reality because it IS the event log. The CheckSet with live statuses is
the analogue of an agent's todo list, except items are executable and
checkmarks are earned by processes, not asserted by a model.

The renderer stores no durable or epistemic state. Killing it and replaying a
completed ledger must reproduce the byte-identical settled transcript and
receipt. Transient spinner frames and clock ticks are excluded from that
comparison because they make no claim; completed durations and outcomes come
from ledger events and are included.

```
◇ 4 hypotheses proposed
▶ pytest tests/test_termui.py tests/x.py::test_y      → PASS
✗ killed: H2 stale-cache, H4 env
▶ pytest tests/x.py::test_y   (isolated)              → FAIL
● cause: tests/test_termui.py must run first  [gate ✓ fail→pass→fail]
```

**Liveness requirement (M1):** evidence-streaming is only satisfying when
evidence moves. Long-running commands must show per-check progress from the
runner, elapsed-time ticks, and counters ("preservation 3/31"). A frozen
spinner reads as hung regardless of how principled the architecture is.

### 10.3 Interaction rules

- **Approvals:** `verify`/`fix`/`why` never ask for spec approval (the check or
  diff pre-exists, or no spec exists). `build`/`edit` show the Spec Card once;
  `--yes` bypasses that Spec Card only. It never authorizes weaker isolation.
  Repository code executed by approved test/build shapes may still have side
  effects: full isolation contains them; `sandbox: partial` requires the
  separate explicit `--allow-partial-sandbox` authority before any repository
  code executes. Non-interactive partial-sandbox runs without that flag stop
  `infrastructure_blocked`. Receipt provenance records spec approval and
  partial-sandbox authorization independently. No per-command permission
  prompts exist because no arbitrary command SHAPE can be proposed mid-run.
- **Interrupt & resume, fail-safe drift rule:** Ctrl-C is always safe; the
  ledger is append-only and stores the baseline tree hash (`.rift/` excluded
  from the hash). Resume first reconstructs the current phase exclusively by
  replaying ledger events. On `rift resume`: identical tree -> continue; ANY
  tracked drift -> recreate the candidate sandbox and rerun baseline and affected
  checks. No scope guessing about which files "can't matter" — a changed
  conftest or fixture outside the patch can matter. Costs a few commands,
  eliminates freshness inference. Stale evidence is never silently reused.
  A model request with a durable start event but no durable response has
  unknown outcome and possibly incurred cost; resume never repeats it without
  explicit retry authorization.
- **Spend:** commands and elapsed seconds are measured exactly and shown
  live. Tokens are provider-reported when available; otherwise shown as
  `unknown` — never silently estimated.
- **Deliberately not conversational (v1):** start -> watch evidence ->
  approve once -> receipt; abort/resume are the only mid-run controls. It
  feels like a test runner with a brain, not a pair programmer. A developer
  who wants to interrogate the diagnosis runs `rift why` — the ledger is the
  answer. If mid-run steering ever ships, it enters as a ledger event, not a
  side-channel to the model.

### 10.4 Spec Card and receipts

Spec Card (build/edit): numbered plain-language behaviours, check counts by
type, expected baseline outcomes with predicted reasons; generated test diff
expandable; `approve / edit / cancel`.

Terminal artifact per task: `repro.sh` + diff + receipt — what a reviewer
wants attached to a PR. Receipts lead with the result and counts; scope
disclosure directly below (honest verdicts are an acquired taste; the layout
carries that friction, the wording never hides it).

## 11. Cost model

Model calls are bounded by operation and repair budgets; the
elimination/verification middle is shell and Python at zero tokens. Whether
this reduces tokens and wall time relative to conventional agents is measured
in §15; no cost advantage is assumed before that benchmark.
Primary cost is total provider-reported tokens across every attempted task
divided by the number of ground-truth-correct verified outcomes. Commands and
wall time use the same denominator. Abstained and failed attempts remain in the
numerator; an all-abstain system has zero yield and undefined/infinite cost per
correct outcome, never an artificial cost advantage.

## 12. Non-goals (v1) — the over-engineering firewall

No planner (the CheckSet is the plan). No multi-agent. No RAG/embeddings.
No memory/schema-transfer system (v2 candidate). No IDE plugin. No
parallel workers. No non-Python. No stochastic checks. No integration with
existing agent harnesses — independent product by decision (§2 of prior
discussion: this trades easy adoption for independence and auditability).
No LangGraph, LangChain agent runtime, or equivalent orchestration/checkpoint
framework: the fixed application loop and ledger already own those jobs, and
a second control-state system would break the single-source-of-truth invariant.

Requests with no falsifiable spec ("make it prettier") are executed under a
characterization guard and stamped `unverifiable` — stated in the product,
not hidden.

**Scope honesty:** v1 is a general *debugging-and-repair + spec-verified
feature* agent — general across repos, models, and cause classes; not across
all task types. The `unverifiable` channel is the bridge, not a claim of
universality.

## 13. Failure handling

- Invalid LLM JSON: one repair retry, then abstain (`underdetermined` or
  `unverifiable` per verb). Never silent fallback between proposers.
- All hypotheses contradicted: `propose_handles` (once), re-enter loop; if
  still contradicted -> `representation_inadequate` with full ledger.
- Assertion-supported environmental finding with no safe apply/withdraw
  intervention: `diagnosis_supported`, `support: observational`,
  `gate: not_applicable`; emit evidence and an unverified remediation note,
  never a verified-fix claim.
- Sandbox/toolchain failure: `infrastructure_blocked`.
- Budget exhaustion: current verdict with `censored: true` in the receipt.
- Multi-cause: Diagnosis carries `causes: list`; conjunctive gate withdraws
  each cause independently (A∧B verified only if withdrawing either restores
  failure).

## 14. Provenance

Inherited with evidence: hypothesis IR + boundary-reset execution, population
scoring/elimination, JS-divergence probe economics (gridworld: 33 tests; code
domain: 15 tests, 18/18 ablation, click case studies R1–R3 incl. one honest
miss and the false-fix experiment). The synthetic fault harness becomes CI
for the agent itself. Known prior art acknowledged: DoVer (intervention-driven
hypothesis validation), iDFlakies/iPFlakies (order-dependence), Causal
Testing, delta debugging. Differentiation claimed only for: LLM-free
deterministic inner loop, unified probe economics across heterogeneous cause
classes, frozen-check counterfactual gate as acceptance authority, and
first-class abstention.

## 15. Evaluation — the design's falsifier

**Frozen evaluation labels:** before any arm runs, benchmark maintainers label
each case `gateable`, `observationally_diagnosable`, or `neither` under v1's
safe primitive set and freeze the ground truth. The agent never chooses its
class or denominator. Missing-dependency/version cases with no safe
apply/withdraw operation are scored on diagnosis accuracy and actionable
observational yield, not verified-fix yield; `gate: not_applicable` can never
earn fix credit. Overall useful-outcome yield across all attempted tasks is
also reported so class partitioning cannot hide low product value.

**Verify benchmark (M1a):** 20–30 real patch/check pairs across ≥5 Python
repositories, including correct fixes and known-bad patches (semantically inert,
order-masked, unrelated, judge/config weakening). Compare standard
post-change verification with the frozen counterfactual gate. Metrics:
incorrect-patch acceptance, correct-patch acceptance/yield, false rejection,
commands, and wall time. This tests acceptance authority only; it provides no
evidence about LLM proposal quality.

**Fix benchmark** (before SWE-bench): 20–30 naturally occurring failures,
≥5 unrelated Python repos, cause classes: state leakage, order dependence,
missing dependency, version mismatch, locale/timezone, nondeterminism,
two-cause, plus a negative class of genuine source bugs (measures false
attribution). Arms: (A) strong model alone; (B) model + ledger + random
probes; (C) full kernel. Co-primary metrics: false-fix acceptance and
**verified-fix yield** (ground-truth-correct, gate-passed fixes divided by all
attempted frozen-`gateable` tasks). Also report observational diagnosis yield
on its frozen class, overall useful-outcome yield, abstention calibration,
commands, tokens, wall time, and total cost per correct verified fix.

**Build benchmark**: 20–30 small real feature requests across repos. Arms:
(A) model alone; (B) model + tests-written-after; (C) spec-first + frozen
gate. Metrics: wrong-thing-built rate, regression rate, spec quality (change
checks that fail for predicted reasons at baseline), median approval
round-trips per accepted spec (UX is load-bearing on this: above ~1.5 the
one-approval interaction model weakens), **correct-feature yield** (correct
accepted features divided by all attempted requests), tokens, wall time, and
total cost per correct feature.

**Acceptance criteria, frozen before execution:** M1a materially lowers
incorrect-patch acceptance while retaining at least 90% of the correct-patch
acceptance achieved by the standard protocol. For fix, C materially lowers
false-fix acceptance, reaches at least 90% of A's ground-truth-correct fix
yield on frozen gateable cases, and lowers total token cost per correct fix.
For build, C materially reduces wrong-thing-built rate, reaches at least 90% of
A's correct-feature yield, and does not increase total token cost per correct
feature. An all-abstain system fails the yield floors by construction. If these
criteria fail, the corresponding thesis is wrong and expansion stops — same
epistemics as the agent itself.

**The open question** (empirical, not architectural): whether mid-tier models
produce sufficiently discriminating specs and hypotheses. The benchmarks
decide it.

## 16. Build order

- **M0** — honesty fixes to riftcode (status `unexplained_by_representation`,
  doc reconciliation, pinned test deps). *Done before any new code.*
- **M1a** — standalone `rift verify <diff> <test>`: worktree/copy sandbox,
  pytest runner interface, frozen baseline-signature matching,
  baseline/candidate/withdraw/reapply gate, minimal append-only ledger and
  replay, evidence streaming, scoped receipt, partial-sandbox authorization,
  Windows process-tree termination. Zero LLM and zero proposal-quality risk.
  Run the verify benchmark before expanding. This is the first shippable slice
  and validates only acceptance authority.
- **M1** — `rift fix` + `rift why`: sandbox, kernel port, check engine
  reuse from M1a; hypothesis population/probes, four-operation client subset,
  patch proposal, and **large-repo context selection**
  (traceback -> import graph -> grep, hard token caps) with its own tests
  against a Django-scale repository; **recovery basics** (`rift resume` from
  the ledger, `infrastructure_blocked` vs `censored` separation, no token
  loss on interruption); **structural acceptance tests**: kernel cannot import
  LLM/provider/network/CLI modules, current state reconstructs from ledger
  only, every transition is write-before-advance, resume uses no secondary
  checkpoint, and a completed transcript is byte-identical after renderer
  restart. Fix benchmark.
- **M1.5** — `rift edit`: characterization machinery plus mandatory non-null,
  non-check-touching ChangeSet guard. This is the designated first cut if the
  simplicity budget or benchmark evidence forces scope reduction.
- **M2** — `rift build`: propose_spec, Spec Card, approval flow, build
  benchmark.
- **M2.5** — multi-cause end-to-end; auto-bisection in-loop; doc-scale
  build (§17) — only after the build benchmark proves unit-level spec
  quality, because doc scale multiplies any spec-quality problem by N.
- **v2 candidates** — stochastic checks (statistical gate), schema-transfer
  memory, non-Python runners, additional provider adapters (native Anthropic
  first), full isolation parity on non-Linux platforms.

Estimated size at M2: ~8,000 runtime lines, ~6 substantive runtime modules,
one binary, `.rift/` state directory. Tests, fixtures, and benchmark harnesses
are reported separately. If the runtime exceeds the simplicity budget,
something gets deleted or the scope is explicitly amended—not hidden.

Suggested six-module implementation boundary (file names are replaceable;
dependency direction is not): `records.py` (contracts + ledger), `kernel.py`,
`sandbox.py`, `checks.py`, `llm.py`, and `app.py` (fixed loop + CLI + renderer).
`app.py` may import both `kernel.py` and `llm.py`; `kernel.py` must never import
`llm.py` or call through an injected model callback.

## 17. Doc-scale build (M2.5)

`rift build --from-doc design.md` extends `build` to a whole design document
without adding a planner. Mechanism:

- **Decomposition (one LLM call):** the doc becomes N falsifiable behaviour
  units in dependency order, plus two explicit buckets surfaced on a Plan
  Card: DECISIONS REQUIRED (architecture choices — recorded into the
  TaskContract as constraints at approval, never "verified") and
  UNVERIFIABLE (declared up front, not discovered later). Kernel validates:
  acyclic order, each unit small enough for one CheckSet.
- **One approval for the whole doc.** Per-unit Spec Cards are derived under
  the approved plan and stream by without blocking (`--approve-each` for the
  cautious). Ambiguity is resolved at the Plan Card — the cheapest moment.
- **Composition rule (the load-bearing line):** every completed unit's change
  checks join the preservation set of all subsequent units. Verified progress
  is monotone: unit 7's gate runs unit 2's checks, so the agent structurally
  cannot regress what it already delivered.
- **The plan is inert data** — an ordered list of CheckSets executed by the
  same single loop. No orchestration engine, no autonomous re-planning. If a
  unit exhausts its repair budget, the agent STOPS and emits a partial
  receipt: verified units listed with receipts, the stuck unit
  `underdetermined` with its ledger, independent later units still attempted,
  dependent ones marked not-attempted. `rift resume` continues after the
  developer edits the unit spec or intervenes.
- **Cost:** ~2 LLM calls per unit + 1 decomposition call + bounded repairs —
  linear in units.

Known new risk, named: decomposition quality (bad ordering costs rework even
though the gate keeps it from costing correctness). Mitigation is that the
Plan Card makes ordering human-reviewable at approval; measured when M2.5
lands, not assumed.

## Appendix A — Conceded gaps vs incumbent agents (priced, not pending)

Thirteen axes where Claude Code / Cursor class agents are clearly ahead
(review: Sol, 2026-08-15). v1 concedes eleven of them by design; two are
absorbed into M1 in minimal form. This appendix exists so none of these
re-enter scope as "small additions" — each is either priced here or requires
amending this document.

**Absorbed into M1 (minimal form only):**

| gap | v1 obligation |
|---|---|
| large-repository navigation | dumb-but-tested context selection: traceback paths -> import graph -> grep, hard token caps, tested on a Django-scale repo. No RAG, no embeddings. Failure here breaks the core thesis (`propose_change` fed wrong files), which is why it is mandatory. |
| recovery polish | trust floor only: `rift resume` from the ledger, `infrastructure_blocked` ≠ `censored`, interruptions never lose paid tokens. Toolchain-specific polish accrues with usage; it cannot be front-loaded. |

**Conceded — wrong product, not wrong version:** code completion, IDE inline
editing, mobile control. These compete on latency and keystroke UX;
riftagent competes on verified results. Building them makes a worse Cursor,
not a better riftagent.

**Conceded — priced by the independence decision (§12):** MCP/external
integrations, cloud/background execution, enterprise policies/deployment.
The "independent agent" constraint traded the easy adoption path for
auditability and vendor independence. This is that trade's invoice.

**Conceded — firewalled to v2 with mechanisms named:** parallel agents;
browser/visual verification (needs the stochastic-check statistical gate,
§6.1); persistent project knowledge — the one axis where riftagent can
eventually beat incumbents rather than trail them, because schema-transfer
memory carries evidence and refusal semantics where incumbent memory carries
notes.

**Conceded — can only be earned:** years of usage and reliability testing.
Counter-asset: the synthetic fault harness runs as CI for the agent itself
and the §15 benchmarks are rerun per release — a regression suite for the
agent's epistemics, which no incumbent publishes.

**Conceded — out of v1 scope by declaration:** language/build-system
coverage (Python/pytest only). Sole v1 obligation: the check engine's runner
interface must keep pytest a plugin so v2 runners (Jest, Go) are additions,
not redesigns.

Strategic summary: these are the incumbents' thirteen axes. v1 concedes them
loudly and competes on the axis none of them occupy — false-fix rate,
scoped verification receipts, cost per correct verified task. Chasing their
axes is how the 8,000-line budget becomes 40,000 before the thesis is ever
tested.
