# Design Amendment Record (DAR)

Governed amendments to the riftagent design authority. Append-only.

`riftagent_design_v1.2.3.md` is **byte-identical** and is never edited. Each
entry below records a rule the implementation now enforces, so that no rule
lives only in code. A rule the code implements that the governed documents lack
is an authority mismatch, and closing that gap is the whole purpose of this
file.

**Authority index**

| Document | Status |
|---|---|
| `riftagent_design_v1.2.4.md` | **current product and architecture authority** |
| `DESIGN_AMENDMENT_RECORD.md` (this file) | current; the derivation of every clause v1.2.4 amends, and the place new amendments are recorded first |
| `riftagent_design_v1.2.3.md` | superseded for the clauses v1.2.4 amends; otherwise current; byte-identical, `sha256 0718ebabf34002f744b44ba2cbf919ffd84c231d4964175bb8d1e033b6feff3d` |

Conflict order: `riftagent_design_v1.2.4.md` governs. Where this record and
v1.2.4 disagree, that is a defect in one of them and must be recorded, not
resolved silently. Where either and v1.2.3 conflict, v1.2.3 yields, and the
conflicting v1.2.3 clause is named in the entry.

A reader now needs one document. Before v1.2.4 existed they needed two, which
is what DAR-007 was open about.

---

## DAR-001 — `propose_change` third input

**Amends** §8 (model operations), which defines `propose_change` as taking the
approved spec/diagnosis and the selected source context.

Deterministic diagnosis does not always resolve. When it ends `underdetermined`
or `representation_inadequate`, the earlier contract left `fix` with nothing to
send and forced an abstention even where a repair was reachable from the
observed failure alone.

`propose_change` accepts a **third input**: a reproducible frozen failure
(signature plus a bounded excerpt of the observed output) together with bounded
source context, used when deterministic diagnosis ends unresolved.

Two receipt fields make the weaker basis legible rather than silent:

- `repair_basis` — `cause_supported` when a located cause drove the proposal,
  `unresolved_diagnosis` when only the frozen failure did.
- `diagnosis: unresolved` — set whenever the second basis was used.

A patch repaired on the second basis still passes the identical counterfactual
gate. The gate's authority is unchanged; only the *provenance* of the proposal
differs, and the receipt says which.

**Status: IMPLEMENTED** (`app._repair_basis`, emitted onto every `fix` receipt;
consolidated into v1.2.4 §9).

Marked only now. The code has emitted these fields since the M1 closing pass,
and this line deliberately stayed `NOT IMPLEMENTED` through that pass because
the evidence did not exist yet: marking a rule implemented on the strength of
the code being present is the error this project keeps correcting.

The evidence it was held for: `tests/test_repair_basis_replay.py` drives `fix`
to a passing verdict on both bases and, for each, re-reads the ledger from disk,
reduces it, and **recomputes the block** — so the assertion is that the value is
a projection of the ledger rather than a variable carried into the receipt.
Transcript and receipt text replay byte-identically from the events alone. A
third test runs both fixtures through the same command and asserts the derived
blocks differ, so neither of the first two can pass against a constant. Three
mutations — the block not emitted, the unresolved branch returning the supported
value, and the frozen-contract conjunct removed — are each recorded RED.

**One correction to the wording above.** The entry says `unresolved_diagnosis`;
the value the runtime emits, and the one v1.2.4 §9 fixes, is
**`diagnosis_unresolved`**. The prose above is left as written — this record is
append-only — and the emitted enum is authoritative.

One rule is tighter than this entry originally stated, and v1.2.4 §9 carries the
tightened form: `cause_supported` requires a frozen ReproductionContract as well
as a supported interventional diagnosis. A supported cause with no contract
issued reports `diagnosis_unresolved`, because the stronger phrasing asserts the
patch was gated against a reproducer frozen from that evidence, and without one
that is a claim about an experiment the gate never ran.

## DAR-002 — the observational stop branch

**Amends** §9 (verdicts), which required an observational finding to yield
`diagnosis_supported` with `gate: not_applicable`, without saying what `fix`
does next.

An assertion observes; it does not intervene. There is nothing to apply and
withdraw, so there is nothing a patch could be gated against.

**`fix` stops before patch generation when the diagnosis is observational.** No
`propose_change` request is issued. The receipt records that no patch was
generated and none was gated.

Generating a patch here would spend a request to produce something the runtime
could never verify, and would invite a reader to treat an ungated change as a
fix.

**Status: IMPLEMENTED** (`cmd_fix`, observational branch).

## DAR-003 — `GROUND_TRUTH_INVALID` case status

**Extends** §15 (benchmark protocol), which defined case outcomes but no status
for a case whose declared ground truth is itself wrong.

A benchmark case can be mislabelled. When it is, neither "correct" nor
"incorrect" is a truthful score, and scoring it either way corrupts the
denominator.

`GROUND_TRUTH_INVALID` marks a case whose predeclared label does not survive
scrutiny. Such a case is **excluded from scoring**, disclosed by name with its
invalidation reason, and its spend is reported separately — never pooled with
valid-case spend.

Worked example, and the reason this status exists — see DAR-006.

**Status: IMPLEMENTED as protocol** (pilot manifest records C5 with this
status and excludes it from the scored set).

## DAR-004 — ledger-derived spend is the only spend figure

**Amends** §5 (ledger architecture) and §12 (receipts).

`.rift/spend.jsonl` is an append-only, scope-keyed spend ledger and the sole
authoritative record of expenditure. It is added to the ledger architecture
alongside the per-task event ledgers.

- Reservation is appended and fsynced **before** the HTTP request, under an OS
  file lock. A crash after reservation leaves the reservation consumed.
- Events carry a stable request id, authorization scope, task id and attempt.
- Settlement is idempotent, keyed by request id.
- Task ledgers store **references** to spend-event ids. A figure copied into a
  task ledger is non-authoritative.
- Receipts derive spend by joining references to the spend ledger.
- No hand-maintained running total exists anywhere.

Absent or malformed provider usage retains the **full** reservation and is
recorded as `unknown_full_reservation_retained`. An estimate would put a number
the provider never confirmed into the authoritative record, and an
under-estimate is how a cap is exceeded while appearing to hold.

**Status: IMPLEMENTED** (`records.SpendLedger`, `spend_for_task`).

## DAR-005 — scope-keyed cumulative spend

**Amends** §13 (budgets), which bounded spend per task.

A per-task cap does not bound a run. Twenty individually affordable tasks are
collectively unaffordable, and nothing in a per-task check notices.

Cumulative spend is keyed by a **frozen authorization scope**, normally the
benchmark or run-manifest hash. Every task in a run shares the scope and draws
on one authorization. Concurrent processes under one scope cannot jointly
exceed its limit; the read-decide-append sequence is serialised by an OS file
lock.

**Status: IMPLEMENTED** (`--scope`, `SpendLedger`, sequential and concurrent
tests).

## DAR-006 — the C5 Goodhart case: the scoped-verification boundary

**Illustrates** §9, which fixes the verdict vocabulary and forbids a bare
`verified`.

A pilot case declared a test asserting `v > 0 and v < 0` and labelled it
unsatisfiable. It is not. The model returned a class whose `__gt__` and
`__lt__` both return `True`. The patch failed only because it was malformed;
had it applied, **the gate would have accepted it** — correctly by its own
terms, because the declared check would genuinely have passed.

This is not a gate defect. It is the boundary the vocabulary exists to mark.
The gate certifies *the approved checks*, which is why the verdict is
`verified_against_approved_checks` and never a bare `verified`. A check is a
proxy for intent, and any sufficiently motivated optimiser will satisfy the
proxy — Goodhart's law, arriving through a `__gt__` method.

Three consequences are now governed:

1. A verdict may never be paraphrased as "fixed", "correct", or "verified"
   without its scope.
2. A benchmark case intended to be unfixable must be **structurally**
   unsatisfiable — e.g. a change check requiring `f(2) == 5` while a frozen
   preservation check requires `f(2) == 4`. Contradictions expressible inside
   one assertion are satisfiable by a pathological return value.
3. Such a case must be adversarially reviewed before it is run, and its label
   predeclared.

**Status: GOVERNED.** The replacement case is deferred to new live
authorization.

## DAR-007 — `riftagent_design_v1.2.4.md`

The consolidated v1.2.4 document incorporating DAR-001 … DAR-006, with the C5
Goodhart case as a worked example, was **not produced** when this entry was
first written. The authority index then pointed at v1.2.3 plus this record.

**Status: CLOSED.** `riftagent_design_v1.2.4.md` exists, and the authority index
above now names it. It was produced by copying v1.2.3 and applying sixteen
splices, each asserted present and unique before it was made and asserted to
have changed the text after; v1.2.3 was re-hashed afterwards and is
byte-identical (`sha256 0718ebab…`). v1.2.4 is `sha256 85a948ba…`, 858 lines
against v1.2.3's 734.

It carries DAR-001 (§8, §9), DAR-002 (§13), DAR-003 and DAR-006 (§15), DAR-004
and DAR-005 (§5, §11), DAR-008 (§3 P5, §16), DAR-009 (§5) and DAR-010 (§7.3).

Closed on the condition the directive set: document, runtime and tests agree.
Where they do not — the bounded repair loop of the original §7.3 — the document
now says what the runtime does and names what was dropped, rather than
describing a behaviour that does not exist. See DAR-010.

---

## DAR-008 — the runtime ceiling, amended once to 8,600 lines

**Amends** §3 P5 and §16, which set the simplicity budget at ~8,000 runtime
lines at M2.

The amendment was requested with measurements rather than taken pre-emptively.
At 7,797 lines the two remaining M1 obligations — the shared
`propose_hypotheses` wiring and the bounded repair loop — measured approximately
90 and 120 lines, against 203 remaining, and could not both be implemented
without compressing exactly the boundaries the standing constraints forbid
compressing. The amendment granted was **+600 lines, to 8,600**.

The ceiling is a disclosure ceiling, not a target. What it bought, and what it
did not:

- `propose_hypotheses` is wired, with its prompt builder, its spend path and the
  `why` authorization ledger that never existed: **8,448 / 8,600**.
- The bounded repair loop is **not** implemented. The honest measurement of its
  cost against the delivered code was 130–195 lines against 152 remaining, and
  the classification it needs belongs in the kernel rather than in a string
  match in the application loop. See DAR-010.

**A second amendment was refused.** The smallest honest one would have been
+400, to 9,000. Taking it unilaterally — or fitting the loop by compressing a
module boundary, weakening a test, or moving a verdict rule out of the kernel —
is the governance failure this record exists to prevent.

**Status: IMPLEMENTED** (measured at 8,448; §3 P5 and §16 of v1.2.4 restate the
ceiling).

## DAR-009 — the ReproductionContract as a durable record

**Amends** §5, which enumerated five durable records.

The bare-target gate can only judge a target that fails when run alone. That
excludes the entire class this project was built for: an order-dependent failure
passes in isolation by definition, so its baseline never reproduces and no patch
for it can ever be verified. Calibration case C4 was scored an abstention on
exactly that limitation, and the limitation was mistaken for task truth.

A **ReproductionContract** freezes how one failure is reproduced — ordering
preconditions, target node, signature, runner-config hash, tree digest,
supporting probe event ids, and content hashes of every judge artifact the
experiment executes — before any patch exists. All five gate phases run it or
none do.

Three properties are load-bearing and are recorded here so none of them lives
only in code:

1. **It is selected from executed evidence.** One recorded probe must have
   applied exactly the selected cause set and reproduced the target-specific
   failure. Combining handles drawn from separate experiments would assert that
   their conjunction reproduces the failure when no run ever applied that
   conjunction — a claim about an experiment that was never performed, frozen
   into the judge that decides every later phase.
2. **Matching is by selector set, not by label**, because `first:X` and
   `firstset:X` compile to identical argv.
3. **Refusal is the load-bearing half.** Observational support, `underdetermined`,
   `representation_inadequate`, a probe with no signature, a probe that did not
   reproduce, a mixed cause set, and no exact matching experiment all fall back
   to the bare target.

The judge artifacts are explicit and not recursive: a byte-identical contract
was not sufficient, because a candidate could edit the polluter test and change
the executable experiment while leaving the record unchanged. Production source
imported by those tests stays editable — it is what a repair is *for*, and
protecting it recursively would freeze the repository.

**Status: IMPLEMENTED** (`kernel.select_reproducer`, `records.ReproductionContract`,
`app._validate_reproducer`; v1.2.4 §5).

## DAR-010 — the repair policy, corrected to the implemented behaviour

**Amends** §7.3, which read: "On failure, only the failure evidence is returned
to the model for a repair call. Configured retry budget; then abstain."

The runtime implements the retry budget **pre-gate only**. A structurally
invalid proposal — one touching frozen checks, runner configuration or `.rift/`,
escaping the repository, containing a binary patch, or failing
`git apply --check` — is rejected before anything executes, and the remaining
attempts are used. Each attempt is charged and recorded.

A candidate that applies cleanly and then fails the counterfactual gate is
rejected with a scoped receipt, and **no second proposal is requested**. §7.3 as
written described a behaviour the product does not have; a reader consulting it
would expect a repair round that never comes.

The loop was dropped rather than fitted: it requires attempt-scoped phase
reduction in the ledger reducer, per-attempt durable ChangeSet records, and a
repairable-versus-terminal classification carried on the kernel's own phase
decision — 130–195 measured lines against 152 remaining under DAR-008.

If it is implemented later, the boundary is governed here. Only two failures are
statements about the patch: **candidate behavioural failure**, and **a
withdrawal that returns the wrong signature**. These are terminal and must never
be retried, because in each the evidence, not the patch, is what failed:
baseline reproduction failure, preservation regression, tracked drift, judge
corruption, ChangeSet loss or tampering, reversal failure, phase-state mismatch,
reapplication integrity failure, and infrastructure failure. Every attempt must
be recorded, charged, and keep its rejected ChangeSet.

**Status: GOVERNED; the post-gate loop is NOT IMPLEMENTED and v1.2.4 §7.3 says
so.** The cost of the gap is that a `fix` whose first patch is behaviourally
wrong abstains where a bounded retry might have succeeded — a narrower product,
not an overstated one.

## DAR-011 — the observational branch is correct and unreachable

**Qualifies** DAR-002, which reads `Status: IMPLEMENTED (cmd_fix, observational
branch)`, and **amends** v1.2.4 §9 and §13, which define the observational
verdict and the stop it triggers.

The branch is implemented and it behaves correctly when it is reached. It cannot
be reached. Three mechanical facts, each now asserted by a test:

1. **`kernel.discover_handles` never yields an assertion primitive.** It
   enumerates `env`, `unsetenv`, `clear` and `first` only. A missing module, a
   missing file and a missing binary all produce zero assertion handles, which
   is asserted against all three failure shapes.
2. **`checks.compile_handles` compiles an assertion to nothing.** Its output for
   `dep_assert:*` and `file_assert:*` is byte-identical to its output for
   applying nothing at all, while an intervention handle is not. The comment
   there has always said assertions are skipped; the consequence had not been
   drawn.
3. **Therefore no trace the runtime can produce supports an assertion-only
   cause.** Every probe that "applies" an assertion observes exactly what
   applying nothing observes, and against such a trace the simplest surviving
   theory is `const False` — `representation_inadequate`, which attributes
   nothing to the repository.

So `diagnosis_supported` with `support: observational` is a verdict this runtime
cannot emit, and DAR-002's stop is a branch no run can enter. The design clause
that requires it — "for an assertion-supported environmental finding that has no
safe apply/withdraw intervention, emit `diagnosis_supported` with `support:
observational` and `gate: not_applicable`" — is **specified and unimplemented**,
not implemented and untested.

What it would take is a mechanism that does not exist: evaluating an assertion
as an *observation* — is this module importable, does this path exist — and
recording its result as evidence, rather than treating it as something to apply.
That is new kernel and runner behaviour, not a wiring change, and it is not
attempted here.

One narrower finding, recorded because it is load-bearing and was not designed
deliberately. A theory can score `supported` while being behaviourally constant
and still *syntactically* naming a role — `z0 and not n0`, with both latents set
by the same role, is always false yet mentions `r0`, so `cause_of` returns that
role's handle. What keeps such a theory out of the diagnosis is the
description-length tiebreak in `min(j, dl)`: the behaviourally identical
`const False` is simpler and wins. If that weighting ever changed, an
assertion-only cause could surface from a trace in which the assertion did
nothing. The tiebreak is therefore part of this rule's enforcement, and it is
now asserted rather than assumed.

**Status: GOVERNED; the observational verdict is NOT REACHABLE.** M1-F15 and
M1-F16 stay open on that basis, and neither is claimed closed by a test that
feeds the branch synthetically. Both are tested to the limit of what is
reachable: the rule is proven correct when fed, the stop in `cmd_fix` is proven
at the only seam that can reach it, and the unreachability itself is proven.

## DAR-012 — the runtime ceiling, amended to 8,700 lines

**Amends** the ceiling clauses of v1.2.4 — §3 P5 and §16 — and supersedes the
8,600 figure set by DAR-008. Every other clause of v1.2.4 is untouched; this
entry amends the number and nothing else.

DAR-008 amended the ceiling once to 8,600 and explicitly refused a second
amendment, because the request then was to fit the **bounded repair loop**, a
capability the milestone could do without. This request is different in kind:
M1-F15 and M1-F16 are required acceptance rows, and the design has specified
their behaviour since v1.2.3 §9 and §13. The capability was not new scope; it
was scope already owed and never built.

The measurement that justified it, taken on a working, tested implementation
rather than an estimate:

| Module | Added | Removed |
|---|---|---|
| `kernel.py` | 49 | 2 |
| `app.py` | 48 | 0 |
| `checks.py` | 34 | 0 |
| `records.py` | 2 | 0 |
| **net** | **+131** | |

```
before the assertion-observation path   8,510
after, with the three corrections       8,676
ceiling                                 8,700
```

**+100 from 8,600**, of which 76 is spent and 24 is headroom. What the 131 lines
bought, component by component: 15 discovery, 34 evaluation, 25 the verdict
rule, 2 the event kind, 55 wiring at two call sites — the second of which is the
collection-error path, without which the canonical missing-dependency shape is
not diagnosed at all.

Two things were refused rather than done to avoid this amendment. Dropping the
collection-error path would have saved 13 lines and left the most common form of
the finding undiagnosed. Stripping the explanatory comments would have saved
about 20 and removed the reasoning from beside the rule, in a codebase whose
standard is that the two live together. Neither is a saving; both are a
different product.

The ceiling remains a **disclosure ceiling, not a target**, and the rule DAR-008
set still holds: an addition must displace something, and an amendment is
requested with measurements or not at all.

**Status: IMPLEMENTED** (measured at 8,676; `CLAUDE.md`, `ACCEPTANCE_MATRIX.md`
P-05 and `IMPLEMENTATION_PLAN.md` updated to 8,700).

## DAR-013 — the observational verdict is reachable; DAR-011 is closed

**Closes** DAR-011, which recorded that the observational branch was correct and
unreachable, and **amends** the DAR-002 status that qualified.

DAR-011 named three mechanical facts. Two of them were the defect and are now
fixed; the third remains true and is the boundary this feature respects.

1. `kernel.discover_handles` **now yields** `dep_assert` and `file_assert`
   handles, from explicit evidence only — the interpreter naming what it could
   not import or open. An ordinary assertion failure yields none, so this is not
   a general-purpose guess. Quota 2, alongside the four existing sources.
2. `checks.evaluate_assertion` **measures** the assertion by running a fixed
   program in the same sandbox the target ran in, with kind and argument passed
   as `sys.argv` elements so nothing is interpolated into source or a shell.
3. `checks.compile_handles` still compiles an assertion to nothing, and that is
   correct and deliberate: an assertion is *not an intervention*. It is never
   applied and never withdrawn, which is exactly why a cause found this way is
   observational and can never be counterfactually gated.

Three rules are governed with it, each of which was a correction to the first
implementation rather than part of it:

- **Discovered handles pass the same contract as model-proposed ones.** A
  failure message is untrusted text: it can name `/etc/passwd` or
  `../../secrets`. Discovery builds handles through `Handle.from_dict`, so the
  absolute-path, traversal and shell-metacharacter rules apply to text the
  repository produced exactly as they apply to text a model produced.
- **An unobservable measurement is not an absence.** The evaluation reports one
  of `present`, `absent` or `unobservable`, and only an executed, valid `absent`
  may support a finding. An import-machinery error, a timeout, a sandbox fault,
  or an exit code that disagrees with the reported word is `unobservable`. The
  earlier implementation caught every exception and called it absent, which
  would have let a broken measurement become a confident environmental claim.
- **The assertion command obeys the command contract.** `COMMAND_STARTED`
  before it runs, `COMMAND_FINISHED` after, then the observation — so it is
  streamed, replayed and charged like every other command, and the live view
  stays identical to the settled transcript.

**Status: IMPLEMENTED.** M1-F15 and M1-F16 are closed by
`tests/test_observational_finding.py` (22 tests), which drives the real CLI on a
repository with a genuinely missing dependency, and carries positive controls in
both directions: a dependency that is present is never reported missing, and a
repository-relative path is still discovered when an escaping one is refused.

DAR-002's stop branch is now reachable, and is exercised from a real repository
rather than a substituted diagnosis.

## DAR-014 — the adapter asserts no sampling preference by default

**Amends** v1.2.4 §8 (LLM interface) in one narrow respect: what the adapter puts
on the wire when no caller expressed a preference.

`llm.post_chat` defaulted `temperature` to `0.0` and serialised it on every
request. Several current models reject a **non-default** sampling parameter with
a 400, and `0.0` is not the default anywhere — so the adapter was asserting a
preference nobody had expressed and failing against providers that are otherwise
compatible with it. Found while freezing the BM-06 model configuration, where it
would have failed all 90 tasks and consumed the authorization on rejections.

The default is now `None`, and the existing branch already omits the field
entirely when it is `None`. An explicit caller-provided value is still
serialised, so providers that accept a sampling parameter can still receive one.

**Why this is the provider-neutral fix and not a workaround.** The alternative —
branching on the configured URL or model to decide whether to send the field —
would put provider-specific knowledge into an adapter whose neutrality is an M1
acceptance property (M1-S03, `test_adapter_neutrality.py`). Removing the
capability rather than the default would trade one provider-specific failure for
another. Omitting what nobody asked for is neutral in both directions.

**No provider-specific branch. No new configuration system. No new module.** One
default value changed.

**Evidence.** Three tests in `tests/test_adapter_neutrality.py`, all asserting on
the body a real loopback provider **received**, not on the call arguments — a
default that is correct in the signature but serialised anyway would pass an
argument-level check:

- `test_no_temperature_is_sent_by_default` — the key is absent, not `null`;
  `top_p` and `top_k` absent too; with a positive control that `model`,
  `max_tokens` and `messages` are all present, so the absence is about
  temperature rather than about a request that failed to build.
- `test_an_explicit_temperature_is_still_serialised` — parametrised over
  `0.0`, `0.2`, `1.0`. `0.0` is included deliberately: the value that used to be
  the default must still work when a caller means it.
- `test_the_default_is_none_in_the_signature` — pins the default itself, so a
  later edit reintroducing a numeric default fails here as well.

**Status: IMPLEMENTED.** Runtime 8,694 / 8,700 (DAR-012). The approved M1
runtime changed, so the three-consecutive frozen-tree gate was re-run and the
handoff archive rebuilt; the previously approved archive `d56edbe2…` is
superseded and is recorded as historical rather than deleted.

## DAR-015 — four runtime additions: a reproducer-aware `verify`, and the benchmark ablation controls

**Amends** v1.2.4 §7 (`verify`) and §11 (CLI surface) by adding four arguments.
No new module, no second gate, no second sandbox, no new ledger, no new
verification path. Every addition routes through machinery that already exists.

### 1–2. `fix --probe-policy {disagreement,random}` and `--probe-seed N`

`kernel.select_probe` already implements `policy == "random"`, and its docstring
states that this is "the only intended independent variable between benchmark
arms B and C". `app.py` hardcoded `"disagreement"` at its single call site, so
the arm the design named could not be run. The default is unchanged; the seed is
required when the policy is random and is recorded durably, because an
unrecorded seed makes a rerun a different experiment.

### 3. `fix --model-alone`

Arm A of BM-06 is the incumbent practice being compared against: the same
provider, model and bounded source context, without deterministic diagnosis or
probing, accepted when the target passes. It reuses the existing proposal
validation, ChangeSet store, sandbox and spend ledger.

It is fenced so it cannot be mistaken for the product:

- it accepts only under arm A's target-pass rule and emits
  `accepted_by_target_pass`;
- it can **never** emit `verified_against_approved_checks`;
- it records `benchmark_ablation: model_alone` durably;
- its receipt is structurally marked ineligible as RIFT product-verification
  evidence.

An ablation that could produce the product's own verdict would eventually be
quoted as the product's result. The marking is in the receipt rather than in
documentation for that reason.

### 4. `verify --precondition NODE` (repeatable) and `--expect-signature PATTERN`

This is the one that closes a product gap rather than a benchmark gap.

`ReproductionContract` already carries preconditions, `run_episode` already
applies them identically in every phase, and `rift fix` already freezes one from
executed evidence. Only `verify` had no way to receive one — so a user with an
order-dependent failure and a patch for it could not verify the patch, because
the bare target passes in isolation and the baseline never reproduces.

The same defect had already been fixed three times in the places it surfaced
(the prototype's C4 abstention, the stage-2 criterion, the BM-06 driver) without
the product gap underneath being closed. This closes it.

Semantics, all through existing mechanisms:

- preconditions execute in declared order before the target in **every** phase,
  each phase beginning from the existing clean-episode reset;
- baseline, candidate, withdrawal and reapplication run the identical frozen
  experiment;
- preservation keeps its existing clean-episode policy;
- precondition files, the target's file and runner configuration become frozen
  judge artifacts and protected paths, so a candidate that edits the polluter —
  weakening the experiment while leaving the contract record byte-identical — is
  rejected before execution;
- **no bare-target fallback** once preconditions are declared;
- with `--expect-signature`, the baseline must reproduce a compatible
  target-specific signature; without it, the observed signature is frozen before
  the candidate runs;
- a passing baseline, a collection error or an incompatible signature stops with
  a scoped reproduction failure;
- the model never proposes, edits or relaxes the reproducer or the signature —
  `verify` makes no model request at all.

### What this deliberately does not do

No repair loop. No second gate. No benchmark-specific verification path: the
driver calls the same `verify` a user calls. `fix`'s existing reproducer
selection is untouched.

## DAR-016 — runtime ceiling 8,700 → 8,920, measured

**Amends** the 8,700 ceiling set by DAR-012. Measured after implementing
DAR-015, not estimated before it.

### The measurement

| module | before | after | delta |
|---|---|---|---|
| app.py | 3,593 | 3,813 | +220 |
| records.py | 1,872 | 1,883 | +11 |
| sandbox.py | 715 | 707 | −8 |
| kernel.py, checks.py, llm.py, `__init__`, `__main__` | 2,514 | 2,514 | 0 |
| **total** | **8,694** | **8,917** | **+223** |

The `sandbox.py` reduction is the pinned formatter reflowing existing code, not
a deletion.

### Itemized, against the estimate recorded before implementation

| item | estimated | actual |
|---|---|---|
| `verify --precondition` / `--expect-signature` | ~65 | ~95 |
| `fix --probe-policy` / `--probe-seed` | ~18 | ~28 |
| `fix --model-alone` | ~85 | ~89 |
| `accepted_by_target_pass`, two event kinds | ~12 | ~11 |
| **total** | **~180** | **+223** |

The estimate was 24% low, in one place: `verify_reproducer` and
`signature_compatible` are separate named functions with their reasoning
attached, and the CLI help text for four arguments is longer than a line each
because each explains why the argument exists. Compressing either to hit the
estimate would trade an explanation a reader needs for a number nobody checks.

### Ceiling

**8,920.** Measured 8,917, plus three lines.

Not a round number and not headroom: the ruling forbids speculative slack and a
second amendment during this pass, so the ceiling is set at the measurement
with the smallest margin that survives a formatter reflow of the kind that just
moved `sandbox.py` by eight lines. A ceiling *below* the measured figure would
be a fiction; one at 9,000 would be 83 lines of unearned room.

Nothing was compressed to fit: no module boundary moved, no test was weakened,
no error handling was dropped, and no explanatory comment was removed. The two
places where the code grew beyond estimate are both places where a future reader
needs the reasoning more than the project needs the line.

## DAR-017 — runtime ceiling 8,920 → 9,090, measured

**Amends** DAR-016. Measured after implementing the four DAR-015 consuming-path
corrections, not estimated before them.

### The measurement

| module | before | after | delta |
|---|---|---|---|
| app.py | 3,813 | 3,940 | +127 |
| records.py | 1,883 | 1,905 | +22 |
| kernel.py | 1,274 | 1,292 | +18 |
| checks.py, llm.py, sandbox.py, `__init__`, `__main__` | 1,947 | 1,947 | 0 |
| **total** | **8,917** | **9,084** | **+167** |

### Itemized

| correction | lines | what they are |
|---|---|---|
| 1. arm A receives the frozen reproducer | ~46 | `proj_reproducer`, two `run_episode` call sites with `patch_owned`, `--precondition`/`--expect-signature` on `fix` |
| 2. `accepted_by_target_pass` as a real verdict | ~48 | reduced `ablation` state, two receipt fields, the kernel derivation branch |
| 3. seed validity and durable policy | ~35 | `probe_policy_error`, the usage gate, `PROBE_POLICY_FROZEN`, reduction, resume reuse |
| 4. every frozen reproducer enforced | ~38 | after-execution check for signature-only contracts, `freeze_declared_reproducer` shared by both verbs, selector resolution through `judge_artifact_paths` |
| **total** | **+167** | |

Correction 4 is net-cheaper than it looks: extracting `freeze_declared_reproducer`
removed a duplicated block from `cmd_verify` that `cmd_fix` would otherwise have
needed a second copy of. Two copies of "what a precondition means" would have
drifted.

### Ceiling

**9,090.** Measured 9,084, plus six lines — the same margin DAR-016 used, sized
to survive a formatter reflow rather than to leave room for future work.

Nothing was compressed to fit: no module boundary moved, no validation was
dropped, no test was weakened, and no explanation was removed. The ruling
forbade compressing boundaries, validation or explanations into three lines, and
none of the four corrections was made smaller than it needed to be.

## DAR-018 — runtime ceiling 9,090 → 9,108, measured

**Amends** DAR-017. Measured after correcting the signature-only after-check.

### The measurement

| module | before | after | delta |
|---|---|---|---|
| app.py | 3,940 | 3,958 | +18 |
| everything else | 5,144 | 5,144 | 0 |
| **total** | **9,084** | **9,102** | **+18** |

### Itemized

| item | lines | what it is |
|---|---|---|
| `Flow.execute(record=False)` and its docstring | ~9 | lets a caller hold back the durable outcome until the experiment has been re-validated |
| the corrected after-check block | ~9 | fresh `tree_hash`, the episode's `expected_tree` and `state_paths`, then the outcome appended |

DAR-017 left six lines and the correction needs eighteen, so the ceiling moves
by the measured amount rather than the correction being squeezed into the
remaining space. Compressing here would have meant either dropping the
`record=False` docstring — which explains why an outcome is deliberately not
recorded yet, the least obvious line in the file — or inlining the validator
call arguments back onto one line, which is how the defect looked correct in
review the first time.

### Ceiling

**9,108.** Measured 9,102, plus six lines — the same reflow margin DAR-016 and
DAR-017 used. No speculative headroom.
