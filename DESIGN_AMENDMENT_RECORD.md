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

## DAR-019 — runtime ceiling 9,108 → 9,156, measured

**Amends** DAR-018, after correcting a contract-consumption defect in `cmd_fix`.

### The defect

`freeze_declared_reproducer` was called only inside the `--model-alone` branch.
Arm A therefore enforced the manifest's declared failure identity while arms B
and C parsed `--precondition` and `--expect-signature` and derived their own
reproducer from whatever they happened to observe. The driver tests proved the
arguments reached every arm's argv; nothing proved the product consumed them.

Three arms would have solved three subtly different tasks. A benchmark whose
arms do not share a task definition cannot support a comparison between them,
whatever its numbers say.

### The correction

The freeze is hoisted above the branch, so every path consumes the declaration.
`reproduces_as_declared` then runs the declared experiment **once, before
diagnosis** — and therefore before any model request. A task that does not
reproduce as declared, or reproduces with a different signature, stops there.

`run_gate` already checked the declared signature at baseline, but that happens
after a proposal exists and has been paid for. Enforcing at entry costs one
episode and no tokens.

### The measurement

| module | before | after | delta |
|---|---|---|---|
| app.py | 3,958 | 4,006 | +48 |
| everything else | 5,144 | 5,144 | 0 |
| **total** | **9,102** | **9,150** | **+48** |

| item | lines |
|---|---|
| `reproduces_as_declared`, with its two governed stops | ~32 |
| hoisting the freeze out of the ablation branch | ~16 |

### Ceiling

**9,156.** Measured 9,150 plus the same six-line reflow margin DAR-016, 017 and
018 used. No speculative headroom, and nothing compressed to fit: the two
governed stops each record what was expected and what was observed, which is
what makes a refusal auditable rather than merely a refusal.

---

## DAR-020 — the schema repair the contract always required, and the ceiling it costs

**Status: APPROVED 2026-08-19, option 1.** Ceiling raised to **9,287** and
applied to `CLAUDE.md`. The reviewer's reasoning: the +131 lines implement a
requirement the frozen design already carried, and four already-working request
paths should not be refactored to force the code back under 9,156 before the
benchmark. The duplicated reserve/request/settle sequence is recorded as
**later cleanup debt**, not a benchmark-readiness blocker.

**Amends** DAR-019. Implements a clause of the design that had never been
implemented at all.

### The gap

`CLAUDE.md` requires, for every model operation: "allow one schema-repair
request; abstain explicitly if validation still fails." `llm.ModelResponseInvalid`
has documented the same thing since M1. No caller implemented it. All three
operations appended `MODEL_RESPONSE_INVALID` and returned `None`, so the
abstention was real and the repair was not. `EventKind.MODEL_REPAIR_REQUESTED`
existed and was never appended by anything.

### Why it could not simply be added

The aborted preliminary run made the reason concrete. Four of its five
`propose_change` requests returned `finish_reason: length` with **zero** visible
characters: default extended thinking consumed the entire output allowance
before any reply existed. A repair retry over that response would have re-sent
the same prompt under the same allowance and failed identically — two charged
requests per arm-run for one outcome. Implementing the clause naively would have
doubled the cost of the exact failure that stopped the run.

So the implementation turns on a distinction the ledger did not previously draw:

| reply | what happened | action |
|---|---|---|
| completed, will not parse | the model said something; its serialisation is wrong | one repair request |
| `finish_reason: length`, or no visible text | the allowance ran out before an answer existed | record the failure, **no repair** |

`llm.output_exhausted` is that predicate, and `MODEL_RESPONSE_INVALID` now
carries `finish_reason`, `response_chars` and `output_exhausted` so the two
cases are distinguishable in a ledger after the fact, which they were not
before.

### What the repair may ask for

`llm.repair_prompt` re-sends the original system message and one user message
asking for the *same* proposal as valid JSON, carrying the unparseable reply
back for reference. It states explicitly that the fix must not change and the
problem must not be reconsidered. A repair that invited a fresh diagnosis would
be a second attempt at the task wearing a retry's name, and `max_attempts` —
frozen at 1 (DAR-010) — is what governs those. One repair, then abstention; a
second would be an unbounded loop.

The repair is reserved and settled as its own request under
`propose_change_repair`, so it cannot spend outside the ledger, and a refused
reservation abstains **before** the request rather than after.

### The measurement

| module | before | after | delta |
|---|---|---|---|
| app.py | 4,006 | 4,094 | +88 |
| llm.py | 597 | 638 | +41 |
| records.py | 1,905 | 1,907 | +2 |
| everything else | 4,642 | 4,642 | 0 |
| **total** | **9,150** | **9,281** | **+131** |

| item | lines |
|---|---|
| `_repair_change`: reserve, request, settle, validate, abstain | ~62 |
| the exhaustion branch and its evidence payload | ~26 |
| `llm.repair_prompt` | ~28 |
| `llm.output_exhausted` | ~13 |
| `MODEL_REPAIR_REQUESTED` annotation | 2 |

### The ceiling, and the alternative to raising it

Measured **9,281**; with the six-line reflow margin DAR-016 through 019 used,
the ceiling implied is **9,287**. That would be the sixth raise, and
`CLAUDE.md` says to remove accidental abstractions before adding more.

There is one to remove. Four call sites — `_request_handles`,
`_request_hypotheses`, `_request_change` and now `_repair_change` — each
hand-roll the identical sequence: reserve, append `SPEND_REFUSED` or
`SPEND_RESERVED`, append the request-started event, call `post_chat`, settle,
append `SPEND_SETTLED`, append `MODEL_RESPONSE_RECEIVED`. That is roughly
thirty-five lines repeated four times, and collapsing it into one charged-request
helper is removing a duplication rather than introducing an abstraction. It
would plausibly recover 70–90 lines, which still would not reach 9,156.

It is not done here because it rewrites two diagnosis operations that this
pass was explicitly scoped away from, and a refactor of approved runtime
undertaken to fit a number is how a ceiling stops measuring anything. Both
options are put to the reviewer:

1. **Raise to 9,287** and leave the duplication recorded for a later pass.
2. **Authorize the deduplication** as its own change, measure it, and set the
   ceiling from the result.

Nothing was compressed to fit either way. The evidence payloads on the
abstention paths are what make a repair decision auditable after the fact, and
the aborted run is the argument for keeping them.

### Ruling, 2026-08-19

Option 1. Measured runtime **9,281**; ceiling **9,287**; `CLAUDE.md` amended.

The deduplication described above is **not** withdrawn — it is reclassified as
cleanup debt to be paid outside a benchmark-readiness pass. The reasoning given:
benchmark readiness should not be made to depend on a cosmetic reduction in line
count, and refactoring four working request paths under that pressure is how a
ceiling stops measuring anything.

**Debt recorded:** `_request_handles`, `_request_hypotheses`, `_request_change`
and `_repair_change` each hand-roll the same reserve → refuse-or-reserve →
request → settle → record sequence. Roughly thirty-five lines, four times. A
single charged-request helper would recover an estimated 70-90 lines and remove
a real duplication rather than introduce an abstraction. It is not scheduled
here.

---

## DAR-021 — `extract_json` discards a valid proposal when prose contains a brace

**Status: APPROVED 2026-08-19 with a refinement, and applied.** The cause was
established from a captured reply before anything was changed; the reply is now
`tests/fixtures/dar021-captured-reply.txt`.

### The evidence

One live `propose_change` against `claude-sonnet-4-6`, captured outside the
repository. `finish_reason: stop`, 618 output tokens, 2,243 characters. Two
braces:

| offset | span | parses |
|---|---|---|
| 243 | `{1: 5}` — inside the echoed pytest message `TLRUCache({1: 5}, maxsize=2, currsize=1)` | no |
| 1,342 | `{"diff": …, "summary": …}` | **yes** |

The object at 1,342 passes `validate_change` and yields a 709-character diff
touching the correct file. The runtime rejected the reply anyway.

### The defect

`extract_json` finds the first `{`, scans to its matching `}`, and calls
`json.loads` on that span. If the span does not parse it raises immediately. It
never looks further, so the first brace in the reply decides whether any
proposal can be found at all.

Its own docstring says "the surrounding prose is discarded, never executed and
never interpreted." A brace inside that prose currently makes the entire reply
unusable, which is the opposite of discarding it.

### Why this is not a model failure

The model returned a well-formed proposal on the first attempt. The repair
implemented in DAR-020 then requested it again and received the same fix — its
`summary` is word for word the summary in the discarded object. The repair
worked exactly as designed and recovered nothing that was not already in hand.

### Why it must be fixed before the benchmark

Every case's frozen signature appears in the prompt, and echoing the failure
message back is ordinary. Several BM-06 signatures contain braces: dict reprs
in assertion output, `TLRUCache({1: 5}, …)`. So this shape is not rare.

Each occurrence writes a first-attempt failure into the ledger that did not
happen, and a repair success that was not needed. BM-06 compares arms on
proposal quality. An adapter defect that manufactures first-attempt failures at
an unknown rate and credits the repair path with recovering from them biases
that comparison — in the direction that flatters the product. Numbers produced
over it would be partly a measurement of a brace in a string.

### The proposed correction

Continue the scan past a balanced span that fails to parse, rather than treating
the first candidate as the only one. Raise `ModelResponseInvalid` only when no
balanced span in the reply parses as a JSON object. The fenced-block handling,
the top-level-object requirement, and the refusal to execute or trust prose are
all unchanged.

A reply with **no** valid object still abstains, and still reaches the DAR-020
repair — the repair is not made redundant, it stops being spent on replies that
were fine.

### Regression test

Built from the captured reply itself, not a hand-written approximation: prose
containing `{1: 5}` followed by a valid proposal must extract the proposal.
Plus the shapes already covered — a single-quoted object must still fail, a
bare `{}` in prose must not be returned as the proposal, fenced objects and
clean objects must still parse.

### Measurement

Not yet taken; the change is expected to be a few lines within `extract_json`
plus its test. The ceiling is **9,287** (DAR-020, approved).

### Ruling, 2026-08-19 — the refinement, and a second defect it exposed

Approved, and **"keep scanning until some JSON parses" was rejected as
insufficient.** It has a hole: `{}` is valid JSON. A reply saying "the previous
state was `{}`" before its real answer would hand `{}` to the validator, be
rejected, and buy a repair for a reply that was already correct — the same
defect one layer along.

The frozen invariant instead:

```
reply → balanced object candidates → parse → apply the operation's validator
      → exactly one valid  : accept
      → zero valid         : DAR-020 repair
      → several valid      : ambiguous, fail closed
```

Parsing is not the acceptance test. The operation's own frozen contract is. No
prose is trusted and no model statement becomes evidence; this only finds the
object that already satisfies a typed contract.

#### Implementation

`extract_json` is **removed**. Its contract — first balanced span, top-level
object, raise on anything else — was the defect stated as a rule, and leaving it
callable would leave the trap set. Two functions replace it:

- `json_candidates(text, limit)` — every object in the reply, in order. A span
  that fails to parse is resumed *inside* rather than skipped past, so an object
  nested in an unparseable wrapper is still reachable. An accepted span is
  skipped past, so objects nested inside it are not offered as rivals. Bounded
  at 64 candidates: an adversarial reply must not make extraction quadratic.
- `extract_validated(text, validate)` — applies the operation's validator and
  enforces the invariant above.

**One judgement not in the ruling, flagged for review.** Byte-equal duplicates
are collapsed before the ambiguity check. A reply that states its proposal in
prose and again in a fenced block has said one thing, and failing closed on
agreement would buy a repair for a reply that was never ambiguous. Two
*different* valid proposals still fail closed.

**One deliberate contract change.** A proposal wrapped in a single-element array
is now accepted; the old rule required the top-level value to be an object.
Under the new invariant the brackets are a serialisation quirk around exactly
one object that satisfies the contract. A list of two different valid proposals
is still ambiguous, which is the case where the wrapper would carry meaning.
`[]`, bare scalars and arrays of non-proposals are all still rejected.

### The second defect: the repair was granted to one operation of three

Found during the same review. `CLAUDE.md` grants every model operation
"extract/validate → one schema-repair request → abstain". DAR-020 implemented it
for `propose_change` only. `propose_handles` and `propose_hypotheses` still
returned immediately on malformed output.

That is not symmetric with the benchmark. Arms B and C depend on the diagnosis
operations, so a model that reasoned well and serialised badly would have its
hypotheses silently discarded — and arm C would score worse for an adapter
defect, while `propose_change` got its promised retry. The benchmark would not
have been measuring the frozen M1 behaviour consistently across arms.

#### Implementation

`_repair_change` is generalised into `_accept_or_repair` + `_repair_request`,
parameterised by operation name and validator, and used by all three. Each
repair is reserved and settled under its own `<operation>_repair` key, so the
ledger distinguishes them and the ceiling can price them separately.

This also pays down part of the duplication debt DAR-020 recorded: implementing
the entitlement by copying sixty lines twice more would have added ~120 lines,
where one shared helper adds far fewer and removes an existing repetition. The
remaining `_request_handles` / `_request_hypotheses` / `_request_change`
reserve-and-settle preamble is still duplicated and is still debt.

### Tests

`tests/test_dar021_extraction.py` — 20 cases. The primary regression is the
captured 2,243-character reply itself, byte for byte, which must now succeed
**without** a repair. Plus `{1: 5}` then valid; `{}` then valid; a near-miss
object then valid; single-quoted only; clean; fenced; prose then fenced; two
different valid proposals fail closed; the same proposal twice does not; nested
objects are not rivals; an object inside an unparseable span is reachable;
unterminated; scalars and empty arrays; and the candidate bound.

`tests/test_json_repair_retry.py` — 38 cases, now covering each operation:
valid first response makes no request; one malformed response gets exactly one
repair; a repaired response is accepted and used; a second malformed response
abstains with `repair_exhausted`; exhaustion never buys a repair; each repair is
reserved and settled under its own operation key.

The per-operation cases drive the shared helper directly rather than through
`fix`. `propose_handles` fires only on the representation-inadequate signal, so
a CLI-level test would have passed by never reaching it — which is the failure
mode this project keeps finding.

### Measurement and ceiling

| module | DAR-020 | after DAR-021 | delta |
|---|---|---|---|
| llm.py | 638 | 706 | +68 |
| app.py | 4,094 | 4,136 | +42 |
| everything else | 4,549 | 4,549 | 0 |
| **total** | **9,281** | **9,391** | **+110** |

| item | lines |
|---|---|
| `json_candidates` with its scanning rules | ~46 |
| `extract_validated` and the ambiguity rule | ~40 |
| `_accept_or_repair`, shared by three operations | ~46 |
| generalising `_repair_change` into `_repair_request` | ~10 |
| removing `extract_json` | −32 |

**Ceiling: 9,397**, measured 9,391 plus the six-line reflow margin DAR-016
onward have used. Governed from the actual result, as instructed.

The +42 in `app.py` is worth reading twice: it granted the repair entitlement
to two further operations *and* removed a duplication. Implementing it by
copying `_repair_change` twice would have cost roughly +120 instead. The
remaining reserve-and-settle preamble duplicated across the three request
functions is untouched and remains the debt DAR-020 recorded.

### Budget

Every operation's repair is now reservable, so the ceiling must price all
three. Repair input ceilings are measured, not assumed — each carries its
operation's system prompt and up to 4,000 characters of the reply it repairs,
and reuses that operation's output cap.

| operation | input ceiling | max output |
|---|---|---|
| `propose_handles` | 3,633 | 800 |
| `propose_handles_repair` | 3,865 | 800 |
| `propose_hypotheses` | 4,166 | 1,600 |
| `propose_hypotheses_repair` | 4,079 | 1,600 |
| `propose_change` | 23,666 | 4,000 |
| `propose_change_repair` | 3,864 | 4,000 |

At $3 / $15, assuming every arm-run needs every repair:

| | per arm-run | ×9 |
|---|---|---|
| arm A (`propose_change` + its repair) | $0.20259 | $1.823 |
| arms B and C (all six) | $0.321819 | $5.793 |
| **27 arm-runs** | | **$7.616052 → ceiling $7.62** |

Was $6.54, which priced only `propose_change_repair` because only
`propose_change` had the entitlement. Arm A is unchanged: it does not diagnose.

Manifest reissued: `64aa5f77…b584f19` → **`e48e4fc5…f51408d3`**. Only `budget`
changed; the script asserts every other key is byte-identical afterwards.

---

## DAR-022 — context selection sent 1.3% of its budget, and arm A less than B and C

**Status: IMPLEMENTED; benchmark readiness BLOCKED at 5/9 on the audit it
requires.** No provider request was made and spend increased by $0.00.

### The cause, from the stopped run's ledgers

Confirmed before any edit, from `context_selected` events:

| arm | case | chars sent | cap | used | files | ranges | baseline excerpt held |
|---|---|---|---|---|---|---|---|
| A | cachetools-c0fdf6ab | 770 | 60,000 | **1.3%** | `__init__.py` | `[[575, 599]]` | **0** |
| B | cachetools-c0fdf6ab | 770 | 60,000 | 1.3% | `__init__.py` | `[[575, 599]]` | 1 |
| C | cachetools-c0fdf6ab | 770 | 60,000 | 1.3% | `__init__.py` | `[[575, 599]]` | 1 |
| A | pygments-2f0d713b | 1,646 | 60,000 | 2.7% | 3 files | `[[65, 82]]` | **0** |
| B | pygments-2f0d713b | 3,994 | 60,000 | 6.7% | 5 files incl. `other.py` | `[[52, 82]]` | 1 |

`src/cachetools/__init__.py` is 23,272 characters. `_anchor_lines` matched
`class TLRUCache` by regex on line 587 and `excerpt` drew a ±12 window around
it: lines 575–599. The class occupies **587–713** and is 4,307 characters. So
the window contained the end of the *previous* class and the first twelve lines
of the target one, and `TLRUCache.__setitem__` — the method the fix must change
— was never sent.

The model then wrote `expires = self.__ttu(key, value, self.timer())` where the
file says `expires = self.__ttu(key, value, time)`, because it was inventing
context lines for a region it had not seen. `git apply --check` rejected it at
every strip level and the gate returned `unverifiable`. Correctly.

**A regex can find where a class is written; it cannot find where it ends.**
That is the whole defect.

### The second defect in the same table

Arm A holds **zero** baseline failure excerpts; B and C hold one. The excerpt
was recorded only inside `run_diagnosis`, which arm A skips, so
`_first_failure_text` fell back to the signature — which names no file. Arm A's
selector had no traceback to work from and saw fewer files than B and C for the
same failure. The arms differed in what they could *see*, not in what they did
with it, which is not a difference a benchmark may attribute to its mechanisms.

### What changed

**Selection order**, as the frozen design specifies: traceback → import graph →
one re-export hop → bounded grep → hard cap. Each stage runs only on names the
previous ones left unresolved, so grep is a floor and never a search strategy.

**Per-file rule**, in priority order:

1. a file within the per-file budget is sent **whole**;
2. otherwise **complete AST definitions** — every cited traceback line resolved
   to its *outermost* enclosing definition, plus every definition the test
   imports by name;
3. otherwise **bounded windows**, with truncation disclosed in the text.

`definition_spans` uses `ast` with `end_lineno` and includes decorators.
`enclosing_unit` returns the outermost holder: a frame inside a method is
usually fixed by seeing the class it belongs to.

**Constant reconciliation.** `MAX_FILE_CHARS = 20_000` existed and was **never
referenced** — `MAX_EXCERPT_CHARS = 8_000` was the real per-file limit and was
not named as such anywhere. `MAX_FILE_CHARS` is now the per-file policy and is
enforced as `min(MAX_FILE_CHARS, MAX_CONTEXT_CHARS - spent)`, so the last file
cannot break the cap the first five respected. `MAX_EXCERPT_CHARS` is removed.

**Re-export resolution.** `reexport_sources` follows a package's own
`from … import …` one level, resolved against each source root. Generic: no
repository is named. `from pkg.plugins import SpecialThing` reaches
`pkg/plugins/other.py`.

**Grep fallback.** `grep_definitions` scans `*.py` in sorted order for a literal
definition of a known unresolved name. Capped at 2,000 files scanned and 3
returned. Deterministic by construction.

**Arm parity.** `Flow.check_payload` is now the single definition of what a
`CHECK_RESULT` carries, used by `Flow.execute` and by both of `run_episode`'s
recording paths — which had drifted, and were why arm A's path recorded no
excerpt. A baseline failure always carries a bounded excerpt of the observed
output. `run_episode`'s probe path sets the observed text explicitly rather than
inheriting whatever the previous command left behind.

The ledger now records `selection_reason` per file and the `stages` that
produced each candidate, so a reader can tell a whole file from a complete
definition from a truncated window without holding the source alongside.

### Not fixed here, and deliberately

Some stopped-run failures are **genuine model patch-format failures**, not
context starvation. Arm B on `pygments-2f0d713b` was already sent
`formatters/other.py` — the correct implementation file — and still produced an
unappliable diff. DAR-022 fixes context starvation only; it does not and must
not make those disappear. They remain observable and must stay in the record.

### Measurement and ceiling

| module | DAR-021 | after DAR-022 | delta |
|---|---|---|---|
| app.py | 4,136 | 4,362 | +226 |
| everything else | 5,255 | 5,255 | 0 |
| **total** | **9,391** | **9,617** | **+226** |

| item | lines |
|---|---|
| `definition_spans`, `enclosing_unit`, `_merge` | ~60 |
| `excerpt` rewritten to three governed rules | ~70 |
| `reexport_sources` | ~40 |
| `grep_definitions` | ~32 |
| the staged resolution loop in `select_context` | ~26 |
| `Flow.check_payload`, and three call sites collapsed onto it | ~18 |
| removing `_anchor_lines` and `MAX_EXCERPT_CHARS` | −20 |

**Ceiling: 9,623**, measured 9,617 plus the six-line reflow margin used since
DAR-016. Governed from the actual result.

### Quality gates

`ruff check`, `ruff format --check` (61 files), `mypy` — all clean.
**671 passed, 5 skipped.**

Six tests in `tests/test_scope_context_release.py` asserted the *previous*
policy and were updated, not weakened. What changed in them is the assertion
that a whole file must never be sent — which is the defect DAR-022 exists to
remove. Every credential-redaction assertion is unchanged and still strict, the
budget assertions were made stronger (they now check `MAX_CONTEXT_CHARS` and the
per-file cap explicitly rather than a proxy like "fewer than 40 padding
lines"), and each fixture was enlarged past the per-file budget so the window
path it was written to exercise is still the path it exercises.

### Model-free context audit — 5 of 9 COVERED

`benchmark/bm06/context-audit.json`. Zero model requests. The known fix commit
is read in the harness only, to score the selection; it never enters a prompt.

| case | verdict | chars | reason |
|---|---|---|---|
| cachetools-c0fdf6ab-genuine_source_bug | **COVERED** | 29,439 | — |
| pygments-2f0d713b-genuine_source_bug | **COVERED** | 29,909 | — |
| pyparsing-13065174-genuine_source_bug | **COVERED** | 40,636 | — |
| icalendar-60a10375-locale_timezone | **COVERED** | 20,163 | — |
| freezegun-3d5d60b4-version_mismatch | **COVERED** | 16,685 | — |
| icalendar-30ec6eef-locale_timezone | NOT_COVERED | 12,947 | the pinned commit touches **40+ files**, including a build script |
| icalendar-781eeda8-locale_timezone | NOT_COVERED | 12,077 | fix is one line in `timezone/tzp.py`, two import hops away |
| dateutil-f2293200-version_mismatch | NOT_COVERED | 15,603 | fix is one line in `tz/tz.py`; the six-file cap was reached first |
| icalendar-62cbf833-version_mismatch | NOT_COVERED | 4,646 | `alarm.py` selected, but its class exceeds the per-file budget so only windows around cited lines were sent |

The cachetools case — the one that stopped the run — is now COVERED, at 29,439
characters instead of 770.

**Three distinct causes, none of them the one DAR-022 fixed:**

1. **`MAX_CONTEXT_FILES = 6` binds before the char budget.** dateutil selected
   exactly six files totalling 15,603 of 60,000 permitted characters, and
   `tz/tz.py` was seventh.
2. **One re-export hop is not always enough.** `timezone/__init__.py` resolves
   the name, so the unresolved set empties and grep never runs — but the
   implementation is one module further on.
3. **`MAX_FILE_CHARS = 20,000` binds for a file whose single class is larger.**
   `alarm.py` fell to windows around cited lines and missed two of four fix
   regions, while 40,000 characters of global budget went unused.

Case `icalendar-30ec6eef` is a **different kind of finding and not a selection
defect**: its pinned commit is a sweeping change across 40+ files including
`generate_windows_to_olson_mapping.py`. No bounded context could contain it, and
the audit criterion — every region the commit touches must be visible — is the
wrong question for a commit of that shape. Whether that case is well curated is
a separate matter from context selection.

**Not fixed here.** The instruction was to stop and report at less than 9/9 and
not to expand scope speculatively. Raising `MAX_CONTEXT_FILES`, allowing a
second re-export hop, or letting one large file draw on unused global budget are
each defensible and each a governed change to a frozen constant. They are put
for a ruling, not taken.

---

## DAR-023 — a case that reproduced perfectly and posed the wrong task

**Status: IMPLEMENTED.** No provider request was made; spend increased by $0.00.

Four defects, one shape: **something that looked checked was not.**

### 1. The corpus contained an invalid case

`icalendar-30ec6eef-locale_timezone` pinned parent `b0860ac7375b`. The direct
parent of its fix commit is `583107582d07`. The two are **71 commits apart**,
and the fix commit does not modify `src/icalendar/tests/attr/test_alarm.py`,
the frozen target.

It passed every check the protocol had. The target failed at the pinned parent
and passed at the fix, so reproduction agreed — while the task actually posed
was "make this test pass by reproducing 71 commits of unrelated development",
which is not a repair task and not what BM-06 measures.

**Reproduction is necessary and not sufficient.** The pin is what makes the task
the one commit that was the fix.

Quarantined, not deleted: the complete record is preserved under
`manifest.quarantined` with its reason, so it can be re-examined. The corpus now
reports **8 valid cases and 1 quarantined case** and carries a
`corpus_status.denominator_note` saying so. No replacement was invented — no
previously curated case with full provenance exists, and back-filling with an
uncurated one would reproduce the defect being fixed.

### 2. The parent pin is now an invariant, checked against git

`driver.parent_pin_failures` requires `manifest["parent"]` to equal
`git rev-parse <commit>^`, and fails closed on: a mismatch (naming the
distance), an unresolvable commit, a **merge** commit — which parent a fix is
relative to is undefined and no protocol governs it — a root commit, and an
unavailable repository. When no repository root is supplied, validation emits
`NOT_CHECKED parent pin` rather than passing silently.

### 3. A result bound only to its manifest

226 lines of behavioural runtime change landed while the manifest SHA stayed
identical. Two results with the same stamp could describe products that behave
differently, and nothing in either artifact would show it.

`results.json` now carries `manifest_hash`, `runtime_hash` and `driver_hash`.

**Runtime hash.** Over `src/riftagent/**/*.py`, excluding `__pycache__`:

```
for each governed file, sorted by normalised relative path:
    update(path bytes); update(b"\0"); update(file bytes); update(b"\0")
```

The path is hashed alongside the content because content alone cannot
distinguish a file that moved from one that changed. The exact covered file list
travels in the result, so a reader is not asked to trust a glob. The governed set
is the whole shipped package rather than a hand-kept list, which cannot quietly
omit the file somebody edited.

**Driver hash.** The driver's own bytes, hashed separately.

**Snapshot rule.** All three are read **once, at startup**, before anything
executes, and the result is stamped from that snapshot. Re-reading to stamp is
the TOCTOU defect `load_manifest` already documented, one level up: the files
are the things that may have changed. A regression mutates a governed runtime
file *during* the run and proves the artifact still carries the startup identity.

**`--report-only` fails closed on any of the three**, independently tested.

### 4. The audit measured a configuration that never runs

`tooling/context_audit.py` passed `case.get("preserve_files", [])` — a key the
manifest does not define — to `select_context`'s `protected` parameter. The
manifest key is `preserve`, and it holds **node ids**, not paths. So the audit
scored an empty protected set against a product that protects the target and
every preservation test.

The audit is now `benchmark/bm06/context_audit.py`, a governed artifact under
test, and it does not reconstruct product configuration at all: protected paths
come from `app.build_checkset`, the same function `cmd_fix` calls, and the
failure excerpt is taken from the same combined output and truncated to the same
`FAILURE_EXCERPT_CHARS` that `Flow.check_payload` makes durable.

The parity test asserts on the audit's **code** with docstrings stripped by AST,
not on its text — the module explains the old broken key in prose, and a plain
grep would fail on the explanation while passing a file that still had the bug.

### 5. The coverage criterion was measuring the wrong thing

"Selected context contains every line the upstream commit changed" scores the
commit's breadth. A release commit touching 40 files can never be covered by a
context bounded to six, and calling that a selection defect is a category error.

Replaced by a **task-required ground truth**, derived mechanically per case and
never shown to a model:

1. start at the exact pinned parent;
2. take the upstream fix patch, source hunks only — the judge is frozen, so a
   candidate may not touch test files;
3. for each hunk, apply the patch without it; a hunk that can be dropped while
   the frozen target still passes and preservation still holds is not required;
4. verify the surviving set **alone** still satisfies both.

Step 4 is what makes it honest. If every hunk is individually droppable, or the
survivors do not satisfy the checks together, there is no single minimal subset
and the procedure has no principled answer — recorded as `AMBIGUOUS` and failed
closed, never resolved by picking one.

The derivation and provenance are persisted per case.

### 6. `MAX_EXCERPT_CHARS` deleted, and the comments that contradicted DAR-022

DAR-022's record claimed this constant was removed. It was not — it remained
defined and unreferenced, so the governance record and the code disagreed. It is
deleted now, and the claim is corrected rather than quietly restated.

Two comment blocks still said context is "never a whole file". That was the
defect DAR-022 removed. Both now describe the rule that exists, and note what
`MAX_FILE_CHARS` is and that it governed nothing before DAR-022.

### Measurement, ceiling and gates

| module | DAR-022 | after DAR-023 | delta |
|---|---|---|---|
| app.py | 4,362 | 4,372 | +10 |
| everything else | 5,255 | 5,255 | 0 |
| **total** | **9,617** | **9,627** | **+10** |

`MAX_EXCERPT_CHARS` deleted; two comment blocks corrected. The +10 is comment
and docstring text stating the rule that exists. **Ceiling 9,633**, measured
plus the six-line reflow margin used since DAR-016. Governed from the result.

Almost all of DAR-023 is benchmark infrastructure, which is measured separately.

| gate | result |
|---|---|
| `ruff check src tests benchmark` | pass |
| `ruff format --check` | 63 files |
| `mypy src/riftagent` | clean, 8 source files |
| full suite | **689 passed, 5 skipped** |
| model-free verification | **8/8** valid cases, 0 requests |

Seven driver tests asserted the pre-DAR-023 contract and were **updated, not
weakened**: their fixtures now build a real two-commit repository so the pin is
checkable, `main()` is told where repositories are, and `write_results` stamps
all three identities. Every property each test protected still holds; what
changed is that a manifest naming a repository which does not exist is no longer
valid — which is the invariant.

### Corpus and budget

Quarantining one case changes the denominator, so the budget was rescoped rather
than left describing a corpus that no longer exists:

| | before | after |
|---|---|---|
| valid cases | 9 | **8** |
| arm-runs | 27 | **24** |
| ceiling | $7.62 | **$6.77** |
| manifest SHA-256 | `e48e4fc5…f51408d3` | `36bcca7a…e981a1745` |

Arm A $0.202590, arms B and C $0.321819, unchanged per arm-run.

### Corrected context audit — 6 of 8

`benchmark/bm06/context-audit.json`. Zero model requests.

| case | verdict | chars | reason |
|---|---|---|---|
| cachetools-c0fdf6ab | **COVERED** | 17,754 | |
| pygments-2f0d713b | **COVERED** | 27,422 | |
| pyparsing-13065174 | **COVERED** | 34,102 | |
| icalendar-60a10375 | **COVERED** | 19,094 | |
| freezegun-3d5d60b4 | **COVERED** | 15,598 | |
| icalendar-62cbf833 | **COVERED** | 2,020 | |
| icalendar-781eeda8 | NOT_COVERED | 9,935 | `timezone/tzp.py` not selected — the implementation is two import hops from the test |
| dateutil-f2293200 | NOT_COVERED | 41,559 | `tz/tz.py:34-40` outside the selected ranges |

**The old 5/9 was invalid twice over**, and the second reason was mine. Besides
the `preserve_files` defect, the audit ran bare `pytest` with no `PYTHONPATH`,
so for every `src`-layout case the package was not importable: the "failure
text" fed to selection was a `ModuleNotFoundError` and minimization concluded
nothing mattered because nothing ever passed. `env_for` now builds the import
environment from the manifest's own `src_layout`, and the layout is recorded per
row. Two results changed once it was fixed — cachetools and dateutil moved off
harness artifacts and onto real answers.

`icalendar-62cbf833` is now COVERED at 2,020 characters, where the previous
criterion called it uncovered at 4,646: the upstream commit touched regions the
task does not require, and minimization removed them.

The two remaining failures are **genuine selector findings**, interpretable for
the first time. Per instruction, `MAX_CONTEXT_FILES`, re-export depth and
`MAX_FILE_CHARS` are untouched and no constant was tuned to the old result.

---

## DAR-024 — the cited line, the used dependency, and binding identity to execution

**Status: IMPLEMENTED.** No provider request was made; spend increased by $0.00.

### 1. The cited region was outranked by imported definitions

`dateutil-f2293200`. The required region is
`EPOCH = datetime.datetime.utcfromtimestamp(0)` at **module level** in
`tz/tz.py`. `enclosing_unit` returns None there — correctly, since no function
or class contains it — and the previous implementation then contributed
*nothing* for that cited line. Imported class definitions filled the whole
per-file budget, and the selected ranges began at line 41 while the fix needed
34-40. The one region the traceback named was the one region not sent.

**The invariant now:** traceback-cited source has highest priority. A cited line
inside a definition selects the complete enclosing definition; a cited line at
module level selects a bounded window around it. Both are chosen *before*
imported definitions may spend any budget. Ranges are emitted in file order, so
priority decides what is kept without deciding what a reader sees first.

This was not solved by raising a cap.

### 2. A used dependency was never followed

`icalendar-781eeda8`. `timezone/__init__.py` was selected and contains:

```python
from .tzp import TZP

tzp = TZP()
```

The required region is in `tzp.py`. Nothing followed the edge: resolution chased
the test's own symbol into `tzid.py` and stopped there.

`used_dependencies` follows edges the selected code itself declares. Two bounds
keep it a traversal rather than a crawl: only **local** imports are candidates,
so the standard library and site-packages never are; and a name must be
**referenced in the body**, not merely imported — an untouched re-export is not
a dependency the code relies on. The frontier is bounded by the existing file
cap, not by a hop count, and results are deduplicated in source order.

Stage order is now traceback → imports → **used dependencies** → re-exports →
grep. No constant was added and none was raised.

### 3. Runtime identity did not prove the runtime executed

Hashing bytes at startup says nothing about which `riftagent` a subprocess
imports. An installed or editable copy earlier on `sys.path` would run while the
frozen hash described source that never executed — the artifact would be
truthful about the bytes and wrong about the run.

`runtime_env` pins `PYTHONPATH` to the intended tree; `resolves_to` asks the
interpreter where `riftagent` actually lands; `assert_runtime` requires both the
frozen hash *and* the intended resolution, and raises `RuntimeDrift` otherwise.
It runs at startup, **before every arm** — refusing after a request has been
paid for costs the money and still discards the result — and **after** every
arm, where drift invalidates the run rather than being carried into a report.

### 4. A baseline worktree is not its parent commit

Several cases lay the fix commit's *test half* over the parent's source, so
`git rev-parse HEAD` is true and insufficient: it identifies a commit, not the
tree that will execute. Untracked files matter for the same reason — a stray
module on the import path changes what runs.

`baseline_tree_hash` uses the same construction as the runtime hash — sorted
normalised relative path, `path || \0 || bytes || \0`, so a move is
distinguishable from an edit — over every file in the worktree except:

| excluded | why |
|---|---|
| `.git` | version-control internals, not execution input |
| `.rift` | the ledger this run writes |
| `__pycache__`, `*.pyc`, `*.pyo` | regenerated from source that is already hashed |
| `.pytest_cache`, `.mypy_cache`, `.ruff_cache` | tool caches |
| `.tox`, `.nox`, `.venv`, `venv`, `.eggs`, `htmlcov` | environments and coverage output |

Nothing is excluded for being inconvenient. `src/`, `tests/`, `conftest.py` and
every configuration file are inside the identity.

Per case: HEAD must equal the pinned parent and the tree must equal its frozen
hash **before** each arm; after each arm the tree is restored and the hash must
match **exactly** before the next arm may start. Each result row carries the
case's `baseline_tree_hash`.

### 5. What this does *not* explain away

`pygments-2f0d713b` arm B was sent `formatters/other.py` — the correct
implementation file — and still produced a diff that would not apply. **That is
a benchmark observation, not an infrastructure failure.** The distinction the
record must keep:

| | |
|---|---|
| inadequate context → bad patch | infrastructure defect; fix before the benchmark |
| adequate context → bad patch | a measurement of the model |

DAR-022, DAR-023 and DAR-024 all sit on the first line. None of them may later
be cited to explain away the second. Patch parsing and application rules are
unchanged in this pass.

### Measurement and ceiling

| module | DAR-023 | after DAR-024 | delta |
|---|---|---|---|
| app.py | 4,372 | 4,481 | +109 |
| everything else | 5,255 | 5,255 | 0 |
| **total** | **9,627** | **9,736** | **+109** |

| item | lines |
|---|---|
| cited/imported tiering in `excerpt` | ~26 |
| `used_dependencies` and `_resolve_module` | ~60 |
| the traversal loop in `select_context` | ~14 |
| an accurate `reason`, since a module-level window is not a definition | ~9 |

**Ceiling 9,742**, measured plus the six-line reflow margin used since DAR-016.
Governed from the result. The runtime binding, baseline-tree hashing and the
audit's budget reporting are all benchmark infrastructure, measured separately.

### Context audit — 8 of 8

| case | verdict | chars | % of 60,000 |
|---|---|---|---|
| cachetools-c0fdf6ab | **COVERED** | 39,033 | 65.1% |
| pygments-2f0d713b | **COVERED** | 31,463 | 52.4% |
| pyparsing-13065174 | **COVERED** | 43,217 | 72.0% |
| icalendar-60a10375 | **COVERED** | 19,094 | 31.8% |
| icalendar-781eeda8 | **COVERED** | 20,091 | 33.5% |
| dateutil-f2293200 | **COVERED** | 42,712 | 71.2% |
| freezegun-3d5d60b4 | **COVERED** | 16,153 | 26.9% |
| icalendar-62cbf833 | **COVERED** | 27,839 | 46.4% |

The percentages answer the question coverage alone cannot. The stopped run spent
**1.3%** of the budget; selection now spends 26.9%–72.0% and every case is
covered with headroom. **Nothing here shows a capacity limit**, so
`MAX_CONTEXT_FILES`, `MAX_FILE_CHARS` and re-export depth are untouched — there
is no evidence that would justify changing them.

Two cases moved on the fixes: `dateutil-f2293200` 15,603 → 42,712 characters
once the cited module-level region stopped being displaced, and
`icalendar-781eeda8` 9,935 → 20,091 once the used dependency was followed.

### Accounting

`model-and-pricing.json` now has one authoritative `CURRENT` block and one
`historical` block marked `SUPERSEDED`. The top-level `authorization` key — the
one that read as current — is removed.

| | |
|---|---|
| valid preliminary cases | 8 |
| arm-runs | 24 |
| conservative ceiling | $6.77 |
| cumulative provider requests | 15 |
| cumulative charged | $0.357593 |

Superseded and retained: 9 cases, 27 arm-runs, $7.62; the Sonnet 5 configuration
at $2/$10 and its $3.43 ceiling; earlier cumulative totals. The reconciliation
script asserts the breakdown sums to both figures rather than restating them.

---

## DAR-025 — the last provenance gaps, closed for $0

**Status: IMPLEMENTED.** No provider request was made; spend increased by $0.00.
DAR-024's selector work is approved and untouched; context selection is closed at
8/8 and no further work was done on it.

### 1. The expected baseline was measured, not frozen

`baseline_tree_hash` was computed at startup and compared against itself for the
rest of the run. A tree that had drifted *before* the run began would be frozen
in its drifted state and every later check would agree with it — the check
confirmed internal consistency, not the curated corpus.

Each of the eight cases now carries `baseline_tree_hash` in the manifest,
alongside its parent and signature, and `manifest.baseline_identity` records the
method, the exclusions and why the commit id alone is insufficient. Validation
checks **all eight** — both the tree and `HEAD == parent` — before the first
request, rather than one at a time as the run proceeds: a corpus that fails on
case six has already been paid for through case five.

Verified after the fact as well as before: the eight hashes still match after a
full model-free verification pass, so `rift verify` leaves the trees as it found
them.

### 2. Ground truth and shadow ran unpinned

`rift` grew an `env` parameter and `evaluate_under_gate` was not updated. The
arms ran against the frozen runtime while the evaluation that *scores* them ran
against whatever resolved first — a comparison between two runtimes reported as
a measurement of one.

This is why the fix is a **single bound object** rather than another parameter.
`Bound` holds the runtime root, the frozen hash and the pinned environment;
`Bound.run` is the only way a RIFT subprocess is started and `Bound.check` the
only way identity is asserted. A parameter can be forgotten at one call site,
and the site that forgets is the one that runs unbound.

### 3. Resolution was asked from the wrong directory

Python puts the working directory on `sys.path`, so a probe run from the driver's
cwd and an arm run from a case worktree answer different questions — and the
worktrees are repositories that may well contain something importable.
`resolves_to` and `assert_runtime` now take the invocation directory, and every
check asks from where the command will actually run. A regression builds a
worktree that shadows the package and proves the answer changes with `cwd`.

### 4. Stale scope text

`model-and-pricing.json` still said "one frozen authorization scope for all 90
tasks". That figure came from the original 30-case BM-06 and never described this
run. It is marked `SUPERSEDED` with a pointer to the manifest, and the manifest's
own `scope_note` no longer mentions 90 at all.

### Measurement

Runtime **9,736**, unchanged: everything here is benchmark infrastructure.
Ceiling **9,742** stands (DAR-024); no LOC governance change is required.

### Gates

| gate | result |
|---|---|
| `ruff check` / `ruff format --check` | pass / 64 files |
| `mypy src/riftagent` | clean |
| full suite | **721 passed, 5 skipped** |
| model-free verification | **8/8**, 0 requests |
| parent pin | **8/8** |
| baseline trees | **8/8**, before and after execution |
| context audit | **8/8 COVERED**, 26.9%–72.0% of budget |

Fixtures gained a real frozen baseline and a checkout at the pinned parent, and
stubs gained the `cwd` argument. Updated to the contract, not weakened.

---

## DAR-026 — the invariant moves inside the method, and the model is bound

**Status: IMPLEMENTED.** No provider request; spend increased by $0.00. The
DAR-024 selector, the 8-case corpus, the frozen baselines, the parent pins,
pricing, the $6.77 ceiling, Sonnet 4.6, patch handling and benchmark semantics
are all unchanged.

### 1. `Bound` centralised the plumbing and not the invariant

`Bound.run` forwarded to `rift`. `Bound.check` existed separately and had to be
*remembered*. It was remembered for the arms and forgotten for ground-truth
scoring, which pre-checked and then called `rift` directly with **no check
afterwards** — so a runtime that drifted while the scoring subprocess ran would
never have been noticed.

A convention that has already been broken once is not an invariant. Both checks
now live inside `Bound.run`:

```
check(cwd, "before <label>") → _rift(args, cwd, env=self.env) → check(cwd, "during <label>")
```

The same `cwd` is used for both checks and the execution, because the working
directory is on `sys.path`. `Bound.supports` wraps the capability probe the same
way, so even `--help` runs bound.

`rift` is renamed `_rift` and is private. Every arm, ground-truth evaluation,
Arm-A shadow and capability probe goes through `Bound.run`. A test walks the
driver's AST and fails if any function other than the documented unbound probe
calls `_rift` — the property is checked structurally rather than by reading.

### 2. The manifest's model was not bound to the configured one

The manifest declared `claude-sonnet-4-6` and carried that model's prices and
output caps. The model that actually runs comes from `RIFT_LLM_MODEL`, and
nothing compared them. A run configured for a different model would have been
reserved, charged and reported entirely under the manifest's identity — prices
for one model, tokens from another, and no field in the artifact that disagreed.

`model_binding_failures` runs inside `validate_manifest`, so it fails **before
the first paid arm** rather than per-arm. Fails closed on absent, empty and
different, with the failure naming both models and what would have gone wrong.
No normalisation: two spellings are two strings, and guessing which differences
are cosmetic is how a mismatch becomes a rounding error.

Demonstrated end to end at $0:

| configured | result |
|---|---|
| `claude-sonnet-4-6` | `manifest valid` |
| `claude-sonnet-5` | `MANIFEST INVALID — … priced as 'claude-sonnet-4-6' and executed as 'claude-sonnet-5'` |
| unset | `MANIFEST INVALID — RIFT_LLM_MODEL is unset or empty` |

`results.json` stamps **`manifest_model`** and **`configured_model`**, both,
always. Recording only the manifest's is what let the two diverge unnoticed.

### 3. Provider-reported model

`reported_models` reads the durable spend ledger for what the provider said it
served. A model materially different from the frozen one **aborts the run**
rather than being scored under the manifest's identity. Absent identity is
recorded as `unavailable` — a different claim from agreement, and one that must
not be collapsed into it: a provider that does not identify itself has not
confirmed anything.

### Measurement

Runtime **9,736**, unchanged — all of DAR-026 is benchmark infrastructure, which
is measured separately. Ceiling **9,742** stands; no governance change required.

### Preserved

`pygments-2f0d713b` arm B produced an unappliable diff with adequate context.
That remains a legitimate future benchmark observation. Patch parsing and
application rules were not touched in this pass, deliberately.

---

## DAR-027 — "provider reported" was our own configuration read back

**Status: IMPLEMENTED.** No provider request; spend increased by $0.00.

> DAR-026 correctly bound the manifest model to the configured model before
> spending, but mislabeled `spend.jsonl` → `pricing.model` as provider-reported
> identity. The provider's actual model identity is the `model_reported` value
> persisted in `MODEL_RESPONSE_RECEIVED` events in the exact task ledger.

### Why the old source could not answer the question

`_pricing_from_args` builds the `Pricing` record as:

```python
model=os.environ.get(llm.ENV_MODEL, "unset")
```

So `pricing.model` is `RIFT_LLM_MODEL` written back out by the runtime. Reading
it to learn what the provider served asks our own configuration whether our own
configuration was used, and it always says yes. The failure it cannot see:

```
manifest model     claude-sonnet-4-6
RIFT_LLM_MODEL     claude-sonnet-4-6
provider answered  claude-sonnet-5      <- invisible
```

This is not hypothetical. A captured event from the aborted run reads
`{"model_reported": "claude-sonnet-5", "finish_reason": "length", …}` — the
adapter has always recorded the provider's own word; nothing consulted it.

### The authoritative source

`provider_reported_models(repo, task_id)` reads
`.rift/tasks/<task_id>/ledger.jsonl` and returns `payload.model_reported` from
**every** `MODEL_RESPONSE_RECEIVED`, in order. The adapter copies that value off
the provider's response (`ModelReply.redacted`), so it is the provider's claim
and not ours. The provider adapter is unchanged.

**Bound to the arm's own task.** The task id comes from the arm's receipt, and
only that ledger is opened. A benchmark writes a ledger per arm per case, so a
global scan would attribute one arm's provider identity to another.

**Every response, including the repair.** A task whose first response matched
and whose schema-repair response came from a different model is a task that ran
on two models; accepting it because the first one matched would be reading the
convenient half of the evidence.

**Fail closed when the task cannot be resolved.** No task id, no ledger, or an
unreadable ledger raises `ModelIdentityUnresolved` and aborts. `unavailable` is
reserved for a valid response that carries no identity — an arm whose evidence
cannot be found is a different thing, and downgrading the first to the second is
how a missing check comes to read like a passed one.

### Three identities, kept apart

| field | source | meaning |
|---|---|---|
| `manifest_model` | manifest | what the experiment declares |
| `configured_model` | `RIFT_LLM_MODEL` | what was requested |
| `priced_models` | `spend.jsonl` → `pricing.model` | what the spend was priced under — configured, renamed for what it is |
| `provider_reported_models` | task ledger → `model_reported` | what came back |

`reported_models` and `reported_model_failure` are removed; the names were the
defect. Pre-spend, `manifest_model == configured_model`. After responses exist,
each reported identity must equal `configured_model` or be `unavailable`.

Each result row carries `priced_models` and `provider_reported_models` as
sequences, so a repair's identity is auditable alongside the first response's.

### Tests

`tests/test_dar027_provider_identity.py` — 15 cases covering all six required
scenarios. The critical one constructs a spend ledger priced at
`claude-sonnet-4-6` and a task ledger reporting `claude-sonnet-5`, asserts that
`priced_models` still returns 4.6 — showing the two sources genuinely differ
rather than assuming they might — and requires the check to fail. Plus: repair
mismatch, absent identity as `unavailable` and never as agreement, two task
ledgers with only the arm's own evaluated, missing/absent/corrupt task ledgers
failing closed, and an AST assertion that `priced_models` never feeds
`provider_identity_failure`.

Four tests in `test_dar026_enforcement.py` asserted the superseded contract.
They are replaced by a pointer explaining what they got wrong, not deleted in
silence — the property they were meant to hold is now held properly next door.

### Untouched

Selector, context limits, import traversal, corpus, parent pins, baseline
hashes, `Bound.run`, runtime binding, the identity scheme, patch application,
schema-repair semantics, Sonnet 4.6, pricing, the $6.77 ceiling and
one-attempt benchmark semantics. No coding repair loop was added.

---

## DAR-028 — arm A crashed on a patch that would not apply

**Status: IMPLEMENTED**, during the authorized 24-arm run, which was stopped at
arm 1 of 24 having spent $0.041091.

### What happened

The first arm produced a healthy response — non-empty, not truncated,
`model_reported: claude-sonnet-4-6`, no repair needed — and a diff that `git
apply --check` calls *"corrupt patch at line 19"*. `run_model_alone` then did:

```python
with Worktree(req.repo_root, "arm-a-candidate") as wt:
    wt.apply_patch(changeset.diff)      # raises SandboxError, uncaught
```

The process died with a traceback and emitted **no receipt at all**.

**This is the pygments-class observation arriving for real**: adequate context,
a model patch that will not apply. It is a benchmark result — a failure to
repair — and arm A had no path to record it. A missing failure path is not the
same as an acceptance rule, and arm A's rule is unchanged: accept iff the target
passes after the patch is applied.

### The misreport, and why it mattered

The driver's `receipt_of` took "the last JSON object printed". With `--json`
every ledger event is a JSON line, so for a crashed arm that is the last
*event*, not a receipt — and DAR-027's provider-identity check then reported the
crash as `the arm's receipt carries no task_id`.

The check was not wrong; it was downstream. A diagnosis that names whichever
check noticed first sends the reader to the wrong file, which is exactly what it
did here.

### The corrections

**Runtime.** Arm A catches `SandboxError` from `apply_patch`, appends
`gate_phase_finished(passed=False, reason="the proposed patch does not apply to
the baseline tree: …")` and emits its receipt. The arm records a refusal instead
of vanishing.

**Driver.** `receipt_of` identifies the receipt by its `verdict`, and an arm
with no receipt is reported as *"the arm emitted no receipt (exit N); it did not
complete"*, with its last output, rather than as a downstream symptom.

### Tests

`test_arm_a_records_an_unappliable_patch_rather_than_crashing` drives a real
`fix --model-alone` against a deliberately corrupt hunk header and requires a
`receipt_emitted` plus a failed candidate phase naming the reason.
`test_a_streamed_event_is_not_mistaken_for_a_receipt` proves a mid-gate event is
not accepted as a verdict.

### Measurement

| | before | after |
|---|---|---|
| app.py | 4,481 | 4,499 |
| **total** | **9,736** | **9,754** |

**Ceiling 9,760**, measured plus the six-line reflow margin used since DAR-016.

### The run

Restarted from clean rather than resumed: one arm executed under the crashing
runtime, and a corpus half-measured by two different products is not a corpus.
The $0.041091 already spent is real and stays in the accounting.

---

## DAR-029 — post-hoc replay: how much of the failure was representation?

**POST-HOC DIAGNOSTIC. NOT BM-06. NOT A REPLACEMENT BENCHMARK RESULT.**
No product behaviour changed. No provider call. Additional spend **$0.00**.

> The preliminary BM-06 run tied A/B/C at 3/8 correct-fix yield. Fifteen of
> twenty-four runs failed at candidate phase, and independent inspection
> indicated thirteen candidate patches were structurally invalid unified diffs.
> Before introducing any model retry mechanism, this deterministic replay
> measures how many failures are recoverable through semantics-preserving diff
> normalisation alone.

Full detail in `benchmark/bm06/patch_replay/FINDING.md`.

### Classification, recomputed

Git is the authority. `corrupt patch` → structurally invalid (**13**);
`patch failed: <path>` → parseable, non-applicable (**2**); denominator **15**.

A regex classifier I wrote first called all 15 structurally invalid. Git's own
taxonomy gives 13/2 and git is the parser that rejected them, so the regex was
replaced rather than reconciled.

### The result

**9 of 15 candidate failures were representation failures, not wrong fixes.**
Recomputing hunk counts from the hunk's own body made them applicable, and all
nine then passed the **full** five-phase gate. Counterfactual yield rises 3/8 →
**6/8 for every arm**; the arms stay tied.

**Zero patches applied and then failed the gate.** The boundary was sharp: a
patch either had correct content in broken metadata, or wrong content.

### One allowance removed by its own invariant

"Add a missing trailing newline" was initially permitted. The byte-identity
check rejected it — appending a newline to a diff whose last line is content
changes that line, and unified-diff signals a missing final newline explicitly.
The allowance was deleted rather than the invariant loosened. Results were
identical afterwards.

### Recommendation

`DETERMINISTIC PATCH CANONICALIZER JUSTIFIED` (Outcome A). Arithmetic on hunk
counts recovers 9 verified fixes at zero marginal cost and cannot introduce
content the model did not write. A model repair request for this class would be
paying for arithmetic.

The 4 that normalised and still would not apply are the only Outcome B
candidates. **No evidence for the semantic repair loop (Outcome C) appears:
zero patches applied and then failed verification.** This replay is evidence
against prioritising that loop, not for it.

### Separation preserved

Representation/application repair and semantic repair are distinct mechanisms
with distinct triggers and distinct feedback. This run gives strong evidence for
the first and none for the second. Collapsing them into one generic retry would
spend a request on the nine cases arithmetic already solves.

### Safety interpretation unchanged

Arm A accepted at `accepted_by_target_pass`; B and C at
`verified_against_approved_checks`; all three of A's also passed the shadow
gate. The mechanism did not fail, and its comparative advantage was not
exercised — there were zero weak-versus-strong disagreements. `0 false fixes`
proves neither superiority nor equivalence.

---

## DAR-030 — deterministic diff canonicaliser at the proposal boundary

**Status: IMPLEMENTED.** No provider call; additional spend **$0.00**. The
frozen benchmark result is untouched.

> The completed preliminary benchmark remains 3/8 per arm. A separate post-hoc
> deterministic replay found that 13/15 candidate failures were structurally
> malformed unified diffs. Recomputing hunk counts only, while preserving every
> semantic diff line byte-for-byte, made 9 candidates applicable and all 9
> passed the full verification gate. This justifies a deterministic product
> canonicaliser and does not justify a semantic repair loop.

### Authority

`records.canonicalize_patch` recomputes `@@ -a,b +c,d @@` counts from the hunk
body. That is the entire list. Not start lines — moving a hunk is choosing a
different place to edit. Not content. No repository lookup, no fuzzy matching,
no model call.

`records.semantic_lines` extracts every context, deleted and added line with
prefix and bytes; the canonicaliser compares it before and after **at runtime**
and returns UNSAFE on any difference. This is an enforced invariant, not a test,
because a canonicaliser that altered content would be indistinguishable from a
model that proposed something else.

UNSAFE means *this cannot be canonicalised*, not *this candidate is rejected*.
The raw patch proceeds to `git apply --check` exactly as before. Refusing to
modify a patch is not the same as refusing the patch.

### One correction found by the historical fixtures, and it matters

The first implementation recomputed counts whenever the header disagreed with
the body. Running it over all 24 frozen candidates showed it **rewriting
`cachetools-c0fdf6ab` arm A — a patch that had applied and verified in the run.**

`git apply` reads exactly `old_count` old-side and `new_count` new-side lines
and stops; trailing extras are not part of the hunk. So a body longer than its
header is a patch git parses happily, and "correcting" it upward produces one
that claims lines git was never going to read.

The shipped rule therefore recomputes **only when the body is short of what the
header declares** — the corrupt case git actually rejects.

**This costs three of the replay's nine recoveries.** Validated across all 24
candidates: **0 working candidates modified, 6 of 9 failures recovered.** The
gap is recorded as a known limitation rather than bought with a rule that can
corrupt working patches. Recovering the remaining three requires conditioning on
git's own parse verdict, which needs a worktree and is a separate change.

### Placement

Inside `_request_change`, the single path all three arms take. A canonicaliser
available to the full kernel but not to the model-alone ablation would be an
advantage the experiment then measured and attributed to the kernel:

```
model → validate → persist raw → CANONICALISE → persist canonical → git apply --check → gate
```

The ChangeSet is built from the canonical bytes and is already content-addressed,
so withdrawal and reapply use those exact bytes by construction.

### Ledger

`EventKind.CANDIDATE_CANONICALIZED`, appended for every candidate including
unchanged ones — so a later evaluation can measure how often models emit
malformed metadata rather than inferring it from absence. Fields:
`raw_candidate_hash`, `canonical_candidate_hash`, `status`, `operations`,
`semantic_lines_identical`, `changed`, `reason`. *(Superseded by DAR-031:
`authorized_byte_changes_only`, plus the two record paths and both git verdicts.)*

### The measured result is unchanged

| | |
|---|---|
| **MEASURED** | A = 3/8, B = 3/8, C = 3/8 |
| **POST-HOC COUNTERFACTUAL** | A = 6/8, B = 6/8, C = 6/8 |

These are different things and neither overwrites the other. The 6/8 figure came
from an unconditional recount that this pass then proved unsafe, which is a
further reason not to treat it as a result.

### Deferred, explicitly

```
semantic repair loop:
DEFERRED — no observed applied-but-verification-failed cases

application repair:
DEFERRED — six non-applicable cases remain for separate study
```

No LLM formatting repair for hunk arithmetic was implemented. Arithmetic does
not need a model request.

### Measurement

| module | before | after | delta |
|---|---|---|---|
| records.py | 1,907 | 2,077 | +170 |
| app.py | 4,499 | 4,534 | +35 |
| **total** | **9,754** | **9,959** | **+205** |

`semantic_lines` and `canonicalize_patch` are ~140 of that; the rest is the
`Canonicalization` record, the event kind and the integration point.

**Ceiling 9,965**, measured plus the six-line reflow margin used since DAR-016.

### Gates

`ruff check`, `ruff format --check` (77 files), `mypy` — clean.
**792 passed, 5 skipped.** No provider call; additional spend $0.00.

---

## DAR-031 — Canonicaliser v2: raw bytes, a byte-mask invariant, and git as the eligibility oracle

**Status: IMPLEMENTED.** No provider call; additional spend **$0.00**. BM-06 was
not rerun. The frozen benchmark result is untouched.

DAR-030 shipped a canonicaliser that was correct in its conclusions and wrong in
three of its mechanisms. Review found all three. Each is closed here, and none
of them widens what the canonicaliser is allowed to do.

### 1 — The raw candidate was hashed but not stored

DAR-030 recorded `raw_candidate_hash` and described the raw proposal as
auditable. Only the canonical bytes reached disk. A digest proves two things
differ; it cannot show what the model actually wrote, so the audit trail the
record claimed did not exist.

Both artifacts are now persisted, in this order, and the raw one is never
overwritten:

```
<task-dir>/raw-candidate.diff         the model's bytes, before canonicalisation
<task-dir>/canonical-candidate.diff   the bytes the gate applies and reapplies
```

`raw_candidate_hash` and `canonical_candidate_hash` are computed over exactly
those persisted bytes, so either file can be re-hashed from the ledger and
checked.

### 2 — The safety invariant could fail open

`semantic_lines()` extracted hunk content lines and the canonicaliser compared
the list before and after. It classified lines by prefix, and a deleted line
whose content begins `-- ` renders as `--- ...` — indistinguishable from a file
header by prefix alone. Verified empirically: given a hunk holding ` keep`,
`--- was a comment` and `+++ is a comment`, it returned **one** of the three
content lines. Two patches differing only in such a line compared equal, and a
canonicaliser that dropped one would have passed its own check.

The invariant is now stated as the narrowest thing that is actually true:

> The only bytes permitted to change are the old and new COUNT fields inside
> valid `@@ ... @@` hunk headers. Everything else must remain byte-for-byte
> identical.

`records.authorized_change_only(raw, canonical)` proves it structurally rather
than by re-parsing content: line counts must match, and each differing pair must
be two well-formed hunk headers agreeing on both start lines, on the optional
section text, and on the line terminator, such that rebuilding the raw header
with the canonical counts reproduces the canonical line exactly. Nothing about
what a line *looks like* enters into it, which is precisely how the previous
invariant went wrong.

It is enforced inside `canonicalize_patch` at runtime, not in a test. A
canonicaliser that altered anything else would be indistinguishable from a model
that proposed something else, and no later check could tell the difference.

`semantic_lines()` was **removed from the runtime**, not merely demoted. It had
no remaining callers, and leaving a known fail-open extractor in the product is
an invitation to reach for it. It survives in
`tests/test_dar030_canonicalizer.py` as the history it now is, with an
executable test asserting that it still drops two of three lines.

### 3 — Eligibility was a heuristic; git can answer exactly

DAR-030 decided eligibility by asking whether a hunk body was *short* of its
declared header. That rule existed for a real reason — an unconditional recount
rewrote `cachetools-c0fdf6ab` arm A, a patch that had applied and verified —
and it was the correct conservative decision on the evidence then available. It
also declined three candidates that were pure count defects, and DAR-030
recorded that gap honestly rather than hiding it.

The question the heuristic was approximating is *does this diff parse?*, and git
answers it directly. `sandbox.structural_parse` runs `git apply --numstat`, with
and without `--recount`, **inside a fresh `TemporaryDirectory`** — no worktree,
no repository content, nothing that could make eligibility depend on the tree:

```
raw parses               -> UNCHANGED. A patch git accepts is never rewritten.
raw fails, recount ok    -> eligible. The defect is the counts.
both fail                -> UNSAFE. The defect is not the counts.
```

This separates two questions DAR-030 had conflated: whether a diff is
**structurally well-formed** (a property of the bytes) and whether it is
**applicable to this repository** (a property of the tree). Only the first may
decide canonicalisation. `recovered.diff` demonstrates the split — it applies to
no tree, because in a temporary directory there is no tree, and `--recount`
still parses it.

With no verdict supplied the DAR-030 rule still applies, so a caller without git
behaves conservatively rather than unpredictably.

### The 24-candidate regression matrix

All 24 frozen BM-06 candidates replayed through v2, with every candidate that
became applicable put through the **full** gate — baseline-fail, candidate-pass,
withdrawal-fail, reapply, preservation — against its frozen baseline:

| | |
|---|---|
| working candidates modified | **0** |
| working candidates that stopped applying | **0** |
| byte-mask invariant held | **24 of 24** |
| candidate-phase failures | 15 |
| canonicalised | 13 |
| applicable afterwards | 9 |
| **recovered through the full gate** | **9** — A 3, B 3, C 3 |

The decision rule stated before the run was: ship 9/9 only if no working
candidate is modified, none stops applying, the byte mask holds on every
candidate, and every recovery clears the full gate rather than `git apply`. All
four hold, so v2 ships. Had any failed, 6/9 would have stood — safety wins over
recovery count.

The symmetric 3/3/3 split is a consequence of the placement, not a finding: all
three arms share one proposal boundary and there is no arm-specific
canonicalisation. `benchmark/bm06/patch_replay/v2_matrix.json` records every row
with both hashes, both git verdicts, the status and the gate verdict.

`declined_not_a_count_defect` — the fixture DAR-030 named as its known gap — is
now recovered and verified. It is retained under that name, asserting both
behaviours: the DAR-030 rule still declines it, and git conditioning recovers it.

### What did not change

The authority is the same list it was:

```
PERMITTED   recompute @@ -a,b +c,d @@ counts from the hunk body

FORBIDDEN   change any other byte - invent source - search the repository -
            relocate or fuzzy-match hunks - change file paths or start lines -
            repair truncation - call a model
```

v2 recovers three more candidates by asking a better question, not by being
allowed to do more. UNSAFE still means *this cannot be canonicalised*, not *this
candidate is rejected*; the raw patch proceeds to `git apply --check` exactly as
before.

`semantic repair` and `application repair` both remain **DEFERRED**. Six
non-applicable candidates remain for separate study, and no
applied-but-verification-failed case has been observed.

### The two results stay distinct

| | |
|---|---|
| **MEASURED** (BM-06, frozen) | A = 3/8, B = 3/8, C = 3/8 |
| **POST-HOC COUNTERFACTUAL** (v2 replay) | A = 6/8, B = 6/8, C = 6/8 |

Neither overwrites the other. The counterfactual figure is unchanged from
DAR-030's, but it now rests on a rule validated against all 24 candidates and
the full gate rather than on the unconditional recount DAR-030 proved unsafe.

### Ledger

`EventKind.CANDIDATE_CANONICALIZED`, appended for every candidate including
unchanged ones. Fields: `raw_candidate_hash`, `canonical_candidate_hash`,
`raw_candidate_record`, `canonical_candidate_record`, `status`, `operations`,
`authorized_byte_changes_only` (replacing `semantic_lines_identical`),
`structural_parse_raw`, `structural_parse_recount`, `changed`, `reason`.

### Measurement

| module | before | after | delta |
|---|---|---|---|
| records.py | 2,077 | 2,168 | +91 |
| app.py | 4,534 | 4,549 | +15 |
| sandbox.py | 707 | 732 | +25 |
| **total** | **9,959** | **10,090** | **+131** |

`authorized_change_only` and the git-conditioned branches are ~70 of that; the
rest is `structural_parse`, the two record paths and the widened
`Canonicalization` record. Removing `semantic_lines` from the runtime returned
27 lines, which is why the delta is smaller than the code added.

**Ceiling amended 9,965 -> 10,100**, measured plus the ten-line reflow margin.
Lineage: 8,000 -> 8,600 (DAR-008) -> 8,700 (DAR-012) -> 8,920 (DAR-016) ->
9,090 (DAR-017) -> 9,108 (DAR-018) -> 9,156 (DAR-019) -> 9,287 (DAR-020) ->
9,397 (DAR-021) -> 9,623 (DAR-022) -> 9,633 (DAR-023) -> 9,742 (DAR-024) ->
9,760 (DAR-028) -> 9,965 (DAR-030) -> 10,100 (DAR-031).

### Gates

`ruff check`, `ruff format --check` (50 files), `mypy` (8 source files) — clean.
**828 passed, 5 skipped** (13:00), of which 57 are the two canonicaliser suites.
Runtime **10,090 / 10,100**, `runtime_hash 57170d59446b56a43fb492588ad551c04dfb639b7102de86d8ac1cebee25ec11`.
No provider call; BM-06 not rerun; additional spend **$0.00**.

---

## DAR-032 — Three stages between the model's diff and the executable candidate

**Status: IMPLEMENTED.** No provider call; additional spend **$0.00**. BM-06 was
not rerun. The frozen benchmark result is untouched. This is an auditability
correction, not a new experiment.

> DAR-031 correctly persisted raw and canonical hashes at the canonicaliser
> boundary, but the value entering that boundary had already passed through
> `canonical_diff()`. Therefore the artifact called raw was ingestion-normalised,
> not necessarily the exact model-authored diff. DAR-032 separates exact model
> candidate, deterministic text normalisation, and deterministic hunk-count
> canonicalisation into three independently persisted and hashed stages.

### The defect, precisely

`llm.validate_change` ended:

```python
return canonical_diff(diff), summary[:2000]
```

So by the time `_canonicalize_proposal` received a value and wrote it to
`raw-candidate.diff`, CRLF had already become LF and a missing final newline had
already been appended. The file was accurate about the candidate that entered
canonicalisation and inaccurate about its own name. A reviewer diffing it
against a provider transcript would have found line-ending differences with
nothing in the ledger able to explain them — the worst kind of artifact, because
it looks like evidence.

Nothing was being hidden and no verdict was wrong. What was missing is that the
model's actual output existed nowhere on disk, and could not be reconstructed
from what did.

### The pipeline, as it now is

```
exact model diff   ->  raw-candidate.diff          raw_candidate_hash
      |  transport normalisation (canonical_diff)
      |  CRLF -> LF, bare CR -> LF, ensure final newline
      v
normalized         ->  normalized-candidate.diff   normalized_candidate_hash
      |  git-conditioned hunk-count canonicalisation (DAR-031, unchanged)
      v
canonical          ->  canonical-candidate.diff    canonical_candidate_hash
      |
      v
ChangeSet -> apply -> withdraw -> reapply -> preservation
```

The exact string is captured at the model-response validation boundary:
`validate_change` now returns the diff it was given, and validation decides only
whether a response is *acceptable*. Transforming it is a separate, separately
recorded stage. The raw artifact is written before anything else runs and is
never reconstructed from normalised bytes.

### Why two stages rather than one

Their authorities are different sizes, and neither invariant can describe the
other:

| | normalisation | canonicalisation |
|---|---|---|
| may change | line terminators, anywhere in the file | the digits inside a valid `@@` header |
| must preserve | every non-terminator byte | **every other byte** |
| decided by | the text itself | `git apply --numstat`, with and without `--recount` |

Run as one step, a receipt could report only *that* bytes changed. A reviewer
asking *what* changed would have to take the answer on trust, which is the same
position DAR-031 left them in for a different reason.

### Canonicaliser v2 is unchanged

`canonicalize_patch` and `authorized_change_only` were not touched. The
git-conditioned rule is byte-identical to the approved DAR-031 logic:

```
raw parses               -> UNCHANGED. A patch git accepts is never rewritten.
raw fails, recount ok    -> eligible. Recompute counts, then prove the byte mask.
both fail                -> UNSAFE. The defect is not the counts.
```

It now receives the **normalised** bytes rather than the ingestion-normalised
value it received before — the same bytes as before, arrived at explicitly. The
byte-mask invariant was not broadened to admit newline normalisation; it still
refuses any difference outside a hunk-header count field, and normalisation
happens strictly before it rather than inside it.

`semantic_lines()` stays removed from the runtime. Its removal was correct — it
was proven fail-open, it had no runtime callers, and a stronger byte-span
invariant replaced it. A test asserts it is absent from every runtime module,
and the historical fail-open regression is retained in the DAR-030 suite.

### Hashes over exact persisted bytes

`records.persist_candidate` writes a stage with `newline=""` — Python's
line-ending translation would otherwise rewrite a raw candidate carrying CRLF on
the way to the file whose whole purpose is proving it was not — then reads the
file back and hashes what it finds. A digest describing anything but the file a
reviewer opens is worse than no digest.

This also corrected the hashing convention. DAR-031 recorded
`content_hash(<str>)`, which JSON-encodes before hashing; `ChangeSet.patch_hash`
has always used `content_hash(<bytes>)`. The two could never have compared equal.
All three stage hashes are now taken over bytes, so:

```
ChangeSet.patch_hash == canonical_candidate_hash
```

is an asserted regression rather than a coincidence. Raw and normalized are
provenance evidence; the ChangeSet is built from the canonical bytes and
withdrawal and reapply continue to use that same content-addressed object.

Valid outcomes, all covered by tests:

```
unchanged proposal            raw == normalized == canonical
newline-only normalisation    raw != normalized == canonical
normalisation + hunk repair   raw != normalized != canonical
```

`records.candidate_record_mismatches` re-hashes all three artifacts from disk
and names every disagreement, distinguishing a corrupted file from an absent
one. Audit tooling reports mismatches explicitly instead of trusting metadata
about files it never opened; `persist_candidate` fails closed at write time.

### The receipt separates the two transformations

`EventKind.CANDIDATE_CANONICALIZED`, appended for every candidate:

```json
{
  "raw_candidate_hash": "…",
  "normalized_candidate_hash": "…",
  "canonical_candidate_hash": "…",
  "raw_candidate_record": "raw-candidate.diff",
  "normalized_candidate_record": "normalized-candidate.diff",
  "canonical_candidate_record": "canonical-candidate.diff",
  "normalization":   {"changed": true, "operations": ["crlf_to_lf"]},
  "canonicalization": {"status": "CANONICALIZED", "changed": true,
                       "operations": [{"kind": "recompute_hunk_counts", …}],
                       "authorized_byte_changes_only": true,
                       "structural_parse_raw": 128, "structural_parse_recount": 0}
}
```

No field mixes the two. `normalize_candidate` calls `canonical_diff` rather than
reimplementing it, so the description cannot drift from the behaviour, and a
runtime invariant refuses to record a change it could not name.

### The 24-candidate matrix, re-proven under the new ordering

Inserting a stage ahead of the canonicaliser changes what the canonicaliser is
given, so the two properties that decide whether the pipeline may ship were
re-established rather than assumed. Every frozen BM-06 candidate replayed
through all three stages, with every candidate that became applicable put
through the **full** gate against its frozen baseline:

| | |
|---|---|
| candidates replayed | **24** |
| normalisation changed anything | **0** |
| originally working candidates modified | **0** |
| originally working candidates that stopped applying | **0** |
| byte-mask invariant held (normalized -> canonical) | **24 of 24** |
| candidate-phase failures | 15 |
| canonicalised | 13 |
| UNSAFE | 0 |
| **recovered through the full gate** | **9** — A 3, B 3, C 3 |
| recovery set identical to DAR-031's | **yes, candidate for candidate** |

Rows with all three hashes, both git verdicts, both stage records and the gate
verdict in `benchmark/bm06/patch_replay/dar032_matrix.json`. DAR-031's
`v2_matrix.json` is retained unchanged.

None of the 24 frozen candidates needed normalisation — they were already LF and
newline-terminated — so the canonicaliser saw byte-identical input to DAR-031
and the recovery set is the same set, candidate for candidate, not merely the
same count. That is the expected result and it is worth stating plainly: this
pass moved a boundary, and the evidence says the boundary moved without moving
anything else.

### The scientific results are unchanged

| | |
|---|---|
| **MEASURED** (BM-06, frozen) | A = 3/8, B = 3/8, C = 3/8 |
| **POST-HOC COUNTERFACTUAL** | A = 6/8, B = 6/8, C = 6/8 |

DAR-032 rewrites neither. Repair loops remain **DEFERRED** — semantic repair,
application repair, and LLM hunk-format repair alike. Observed
applied-but-verification-failed cases: still 0.

### Measurement

| module | before | after | delta |
|---|---|---|---|
| records.py | 2,168 | 2,271 | +103 |
| app.py | 4,549 | 4,570 | +21 |
| llm.py | 706 | 713 | +7 |
| **total** | **10,090** | **10,221** | **+131** |

`normalize_candidate` and the `Normalization` record are ~45 of that,
`persist_candidate` and `candidate_record_mismatches` ~40, and the rest is the
third stage in `_canonicalize_proposal` plus the record path and docstrings that
say which artifact is which.

**Ceiling amended 10,100 -> 10,235**, measured plus the reflow margin used since
DAR-016. Lineage: 8,000 -> 8,600 (DAR-008) -> 8,700 (DAR-012) -> 8,920 (DAR-016)
-> 9,090 (DAR-017) -> 9,108 (DAR-018) -> 9,156 (DAR-019) -> 9,287 (DAR-020) ->
9,397 (DAR-021) -> 9,623 (DAR-022) -> 9,633 (DAR-023) -> 9,742 (DAR-024) ->
9,760 (DAR-028) -> 9,965 (DAR-030) -> 10,100 (DAR-031) -> 10,235 (DAR-032).

### Gates

Run at $0, offline, with the exactly-pinned toolchain (ruff 0.16.3, mypy 2.3.1,
pytest 9.1.1) installed from a host-fetched wheelhouse.

| | |
|---|---|
| `ruff check src tests` | clean |
| `ruff format --check` | 51 files, clean |
| `mypy src/riftagent` | 8 source files, clean |
| canonicaliser suites (DAR-030/031/032) | **78 passed** |
| full suite | **845 passed, 5 skipped, 4 failed** |
| runtime | **10,221 / 10,235** (ceiling amended, governed) |
| runtime_hash | `a7fe7d27ebe26381edd544832df93739cc593a5f67337cc6d8a87714efceb798` |
| provider calls | **0** |
| additional spend | **$0.00** |

**`NOT_RUN_REFERENCE_ENVIRONMENT` — disclosed, not passed.** Container egress
failed mid-pass (DNS resolves, TCP hangs; the host is unaffected and a full
Docker Desktop and WSL restart did not fix it), so the suite ran in a substitute
image rather than `python:3.12-slim`. Four tests fail there for reasons proven
to be image differences, none of which touches the candidate pipeline:

| test | cause, verified |
|---|---|
| `test_r07_an_interrupt_kills_the_child_process_tree` | the substitute image ships `/usr/bin/bwrap`; the test's one-shot `Popen` patch intercepts the isolation probe instead of the child. `python:3.12-slim` has no bwrap. |
| `test_a_merge_commit_is_rejected_until_a_protocol_governs_it` | the substitute image's git defaults to branch `main`; the fixture assumes `master`, so its `--no-ff` merge fast-forwards and produces one parent. |
| `test_v16_clean_wheel_install_exposes_the_shipped_commands` | `pip wheel` build isolation fetches `setuptools>=68` from PyPI: `Temporary failure in name resolution`. |
| `test_v16_installed_package_runs_a_real_verification` | same. |

No test was modified to accommodate the substitute image. These four should be
re-run in `python:3.12-slim` once egress is restored; until then this is an
evidence gap for a human reviewer to accept or reject, not a green gate.

---

## DAR-033 — Candidate artifacts are attempt-addressed and immutable

**Status: IMPLEMENTED.** No provider call; additional spend **$0.00**. BM-06 was
not rerun. The frozen benchmark result is untouched. This pass is about artifact
**lifetime**, not candidate transformation.

> DAR-032 correctly persisted exact raw, normalized, and canonical candidate
> stages, but reused the same artifact paths across attempts. Therefore a later
> candidate could overwrite bytes referenced by an earlier durable ledger event.
> DAR-033 makes candidate artifacts attempt-addressed and immutable so every
> charged/rejected/accepted proposal remains independently reconstructable.

### The defect

DAR-032 gave the three stages distinct names but one fixed path each:

```
attempt 1  ->  raw-candidate.diff = A   ledger event 1 records hash(A)
attempt 2  ->  raw-candidate.diff = B   ledger event 2 records hash(B)

after attempt 2:  event 1 still names raw-candidate.diff
                  that path now holds B
                  event 1's hash matches nothing on disk
```

Every rejected candidate was requested, charged, and refused on the evidence.
The evidence was then overwritten by the next proposal, so the one artifact that
could show *why* a charged attempt was refused stopped existing the moment
another was made. Nothing was concealed and no verdict was wrong; the durable
record simply stopped being durable after the first retry.

This is the same failure mode as DAR-031 and DAR-032 in a third place — a
recorded hash whose referent has moved — and it is worth naming as a pattern
rather than a coincidence. A digest is only evidence when the bytes it describes
are reachable and immutable.

### The layout

```
<task-dir>/candidate-attempt-001/raw.diff
                                 normalized.diff
                                 canonical.diff
<task-dir>/candidate-attempt-002/raw.diff
                                 normalized.diff
                                 canonical.diff
```

Zero-padded to three digits so a listing sorts in attempt order. That is a
convenience for a reader and never an authority: the number comes from the
repair loop.

### Attempt identity is propagated, never inferred

`cmd_fix` already owns the loop:

```python
for attempt in range(1, req.max_attempts + 1):
    proposal = _request_change(flow, spend, task_id, messages, max_output_tokens, attempt, td)
```

That value now continues into `_canonicalize_proposal(flow, td, attempt, proposal)`
and addresses the artifacts. There is no global counter, no directory scan, no
timestamp ordering, and no later reconstruction from the ledger — each of which
would be a second source of truth for something the caller already knows.
`candidate_attempt_dir` refuses an attempt below 1: zero or negative means the
caller lost the loop variable, not that a zeroth attempt exists.

### Immutability

`persist_candidate` no longer writes over an existing path:

```
path absent            -> write, read back, hash the bytes on disk
path present, same     -> return the hash; a replayed write is not a revision
path present, differs  -> raise. The record is append-only evidence.
```

Idempotence matters because a resumed run may legitimately re-execute a write
that already completed. Silent overwrite matters because it is indistinguishable
from that case, and only one of the two is honest. A filesystem overwrite is not
a permission to revise recorded evidence.

### Every event names its own artifacts

```json
{
  "attempt": 2,
  "raw_candidate_record": "candidate-attempt-002/raw.diff",
  "raw_candidate_hash": "…",
  "normalized_candidate_record": "candidate-attempt-002/normalized.diff",
  "normalized_candidate_hash": "…",
  "canonical_candidate_record": "candidate-attempt-002/canonical.diff",
  "canonical_candidate_hash": "…",
  "normalization":    {"changed": true, "operations": ["crlf_to_lf"]},
  "canonicalization": {"status": "CANONICALIZED", "…": "…"}
}
```

Paths are recorded relative to the task directory, so an event stays meaningful
when the tree is archived or moved. Earlier events are never edited when a later
attempt arrives — a test asserts the ledger file after attempt 2 begins with its
exact bytes after attempt 1.

`candidate_record_mismatches` resolves each stage through **the path the event
recorded**, not through a path recomputed from today's naming rule. Audit tooling
that recomputes the location cannot detect an event pointing somewhere else,
which is precisely the class of defect this record closes. A hash recorded with
no path is reported as a finding rather than skipped.

### The accepted ChangeSet addresses its own attempt

With attempt 1 rejected and attempt 2 accepted, the ChangeSet is built from
attempt 2's canonical bytes and `ChangeSet.patch_hash ==` attempt 2's
`canonical_candidate_hash`, and differs from attempt 1's. Asserted, not assumed:
there is no longer a "latest generic filename" that could quietly supply the
wrong attempt's bytes.

### Canonicaliser v2 and the pipeline semantics are unchanged

`canonicalize_patch`, `authorized_change_only`, `normalize_candidate` and
`canonical_diff` were not modified. CRLF normalisation, final-newline
normalisation, `git apply --numstat`, `--recount`, hunk-count repair and the
authorized-byte-change invariant all behave exactly as approved. Re-proven on the
frozen corpus:

| | |
|---|---|
| candidates replayed | **24** |
| originally working candidates modified | **0** |
| originally working candidates that stopped applying | **0** |
| byte-mask invariant held | **24 of 24** |
| **recovered through the full gate** | **9** — A 3, B 3, C 3 |
| recovery set identical to DAR-031's | **yes, candidate for candidate** |

Repair-loop behaviour was not expanded. `max_attempts`, the semantic-repair
policy and the application-repair policy are untouched; this pass only ensures
that when more than one candidate exists, every one of them is auditable. Both
repair loops remain **DEFERRED**, and observed applied-but-verification-failed
cases are still 0.

### A stale docstring corrected

`canonical_diff` still claimed it ran "at ingestion, before the patch is hashed
and stored". That stopped being true at DAR-032. It now states the actual chain —
raw persisted and hashed, then `canonical_diff`, then normalised persisted and
hashed, then the hunk canonicaliser, then canonical persisted and hashed — and
notes that each stage is attempt-addressed and immutable.

### The scientific results are unchanged

| | |
|---|---|
| **MEASURED** (BM-06, frozen) | A = 3/8, B = 3/8, C = 3/8 |
| **POST-HOC COUNTERFACTUAL** | A = 6/8, B = 6/8, C = 6/8 |

### Measurement

| module | before | after | delta |
|---|---|---|---|
| records.py | 2,271 | 2,322 | +51 |
| app.py | 4,570 | 4,584 | +14 |
| **total** | **10,221** | **10,286** | **+65** |

`candidate_attempt_dir` and the immutability branch in `persist_candidate` are
~30 of that; the rest is `candidate_record_mismatches` resolving recorded paths
instead of recomputed ones, and the docstrings that say which artifact belongs
to which attempt.

**Ceiling amended 10,235 -> 10,300**, measured plus the reflow margin used since
DAR-016. Lineage: 8,000 -> 8,600 (DAR-008) -> 8,700 (DAR-012) -> 8,920 (DAR-016)
-> 9,090 (DAR-017) -> 9,108 (DAR-018) -> 9,156 (DAR-019) -> 9,287 (DAR-020) ->
9,397 (DAR-021) -> 9,623 (DAR-022) -> 9,633 (DAR-023) -> 9,742 (DAR-024) ->
9,760 (DAR-028) -> 9,965 (DAR-030) -> 10,100 (DAR-031) -> 10,235 (DAR-032) ->
10,300 (DAR-033).

### Gates

Run at $0, offline, with the exactly-pinned toolchain (ruff 0.16.3, mypy 2.3.1,
pytest 9.1.1) installed from a host-fetched wheelhouse.

| | |
|---|---|
| `ruff check src tests` | clean |
| `ruff format --check` | 52 files, clean |
| `mypy src/riftagent` | 8 source files, clean |
| candidate-pipeline suites (DAR-030/031/032/033) | **92 passed** |
| full suite | **859 passed, 5 skipped, 4 failed** |
| historical matrix | **9/9 recovered, 0 working candidates modified** |
| runtime | **10,286 / 10,300** (ceiling amended, governed) |
| runtime_hash | `b303ece3d5355db80cbd2ae6d8207610e9c13d63e12bee473e9e580ef48874af` |
| provider calls | **0** |
| additional spend | **$0.00** |

**`NOT_RUN_REFERENCE_ENVIRONMENT` — disclosed, not passed.** The same four tests
DAR-032 recorded still fail, for the same verified reasons, and for the same
reason they cannot be cleared: container egress remains broken (DNS resolves,
TCP hangs; the host is unaffected, and a full Docker Desktop and WSL restart did
not fix it), so the suite runs in a substitute image rather than
`python:3.12-slim`. None touches the candidate pipeline:

| test | cause, verified |
|---|---|
| `test_r07_an_interrupt_kills_the_child_process_tree` | the substitute image ships `/usr/bin/bwrap`; the test's one-shot `Popen` patch intercepts the isolation probe instead of the child. `python:3.12-slim` has no bwrap. |
| `test_a_merge_commit_is_rejected_until_a_protocol_governs_it` | that image's git defaults to branch `main`; the fixture assumes `master`, so its `--no-ff` merge fast-forwards to one parent. |
| `test_v16_clean_wheel_install_exposes_the_shipped_commands` | `pip wheel` build isolation fetches `setuptools>=68`: `Temporary failure in name resolution`. |
| `test_v16_installed_package_runs_a_real_verification` | same. |

No test was modified to accommodate the substitute image. These four need a
re-run in `python:3.12-slim` once egress is restored; until then this is an
evidence gap for a human reviewer to accept or reject, **not a green gate**.

---

## DAR-034 — The provenance auditor made as strict as the invariant it checks

**Status: IMPLEMENTED.** No provider call; additional spend **$0.00**. BM-06 was
not rerun. The frozen benchmark result is untouched. Audit hardening only: the
candidate-generation path is unchanged.

> DAR-033 made candidate artifacts attempt-addressed and immutable, but
> `candidate_record_mismatches` — the independent auditor — could return "no
> mismatch" about a record it had not verified. An incomplete record passed
> unexamined, and a recorded path was resolved without confinement, so a path
> escaping the task directory or belonging to another attempt satisfied the
> check. DAR-034 requires a complete record and confines every recorded path to
> its own attempt directory inside the task directory.

### The two gaps, reproduced before they were closed

Both were verified against the shipped runtime, not argued from reading:

```
candidate_record_mismatches(td, {})                       -> ()
candidate_record_mismatches(td, {"attempt": 1,
    "raw_candidate_record": "../outside.txt",
    "raw_candidate_hash": hash(outside.txt)})              -> ()
```

The first is the auditor agreeing with a record that says nothing. A
`CANDIDATE_CANONICALIZED` event describes three mandatory stages, so a stage
naming neither a path nor a hash is a **missing stage**, not an absent question.

The second is resolution without confinement: the code did `td / recorded_path`
and then hashed whatever it found. A real file with a correct hash and a path
pointing at something that is not a candidate artifact passed. A third variant
was reported only incidentally — an event for attempt 2 satisfied by attempt 1's
artifact was caught when that file happened to be missing, and would have passed
when it existed.

Neither was reachable from the product. `_canonicalize_proposal` constructs safe
relative attempt-owned paths itself, and nothing in the runtime feeds the auditor
an event it did not write. That is exactly why this is worth fixing rather than
dismissing: an auditor is only useful to the extent that it is stricter than the
thing it audits, and one that agrees by default is a second opinion, not a check.

### The strengthened invariant

```
event says attempt N
  -> all three stages present, each with a path and a hash
  -> every path relative, confined to the task directory, owned by attempt N
  -> every file exists
  -> every hash reconstructs from the bytes on disk
```

`attempt` must be a real attempt number before anything else is checked; without
it the record cannot be judged at all, and that is reported rather than assumed
away. `True` is refused explicitly: it is an `int` in Python and would otherwise
address `candidate-attempt-001`, letting a boolean stand in for provenance.

`_confined_candidate_path` refuses, **before any bytes are read**:

| refused | why |
|---|---|
| absolute paths, POSIX or Windows, and UNC | escape the task directory by definition; a leading separator is drive-relative on Windows, so both forms are tested |
| any `..` segment | traversal, including buried forms like `attempt-001/../../x` |
| anything resolving outside the task directory | resolution is the only reliable test, and it is applied after the cheap ones |
| anything outside `candidate-attempt-NNN/` for this event's N | attempt 1's artifact is not attempt 2's evidence even when it exists and hashes correctly |

Reading the bytes only after the path is judged matters: a hash computed over the
wrong file is a passing check and a false statement. A test asserts the failure
message is a path refusal and never a hash mismatch, so the order cannot silently
regress.

### The retry loop, end to end

The DAR-033 two-attempt test called `_request_change` twice and constructed the
ChangeSet by hand. That proves the mechanism and not the wiring — it could not
have caught a loop that registered the wrong attempt's bytes. DAR-034 adds a
model-free test that drives `cmd_fix` through the CLI with `--max-attempts 2`:
attempt 1 edits the frozen judge and `kernel.validate_patch` refuses it, attempt
2 is the real fix. The product chooses, and the assertions are that exactly one
ChangeSet is registered, its `patch_hash` equals attempt 2's
`canonical_candidate_hash`, its bytes equal attempt 2's canonical artifact **on
disk**, and attempt 1's raw artifact is still byte-for-byte what the model sent.

Writing it corrected an assumption worth recording: the loop re-proposes only on
`kernel.validate_patch` rejection. A gate failure does not produce another
attempt — the repair loop is deferred — so the first draft of this test, which
expected a second proposal after a failed gate, was wrong about the product
rather than finding a defect in it. The test now asserts the real behaviour.

### What did not change

`canonicalize_patch`, `authorized_change_only`, `normalize_candidate`,
`canonical_diff`, `persist_candidate` and `_canonicalize_proposal` were not
modified. The change is confined to `candidate_record_mismatches` and its new
helper. Re-proven on the frozen corpus:

| | |
|---|---|
| candidates replayed | **24** |
| originally working candidates modified | **0** |
| originally working candidates that stopped applying | **0** |
| byte-mask invariant held | **24 of 24** |
| **recovered through the full gate** | **9** — A 3, B 3, C 3 |
| recovery set identical to DAR-031's | **yes, candidate for candidate** |

Repair-loop behaviour was not expanded: `max_attempts`, semantic-repair policy
and application-repair policy are untouched, both loops remain **DEFERRED**, and
observed applied-but-verification-failed cases are still 0.

| | |
|---|---|
| **MEASURED** (BM-06, frozen) | A = 3/8, B = 3/8, C = 3/8 |
| **POST-HOC COUNTERFACTUAL** | A = 6/8, B = 6/8, C = 6/8 |

### Measurement

| module | before | after | delta |
|---|---|---|---|
| records.py | 2,322 | 2,387 | +65 |
| **total** | **10,286** | **10,351** | **+65** |

All of it is `candidate_record_mismatches` and `_confined_candidate_path`. No
other runtime module changed.

**Ceiling amended 10,300 -> 10,365**, measured plus the reflow margin used since
DAR-016. Lineage: 8,000 -> 8,600 (DAR-008) -> 8,700 (DAR-012) -> 8,920 (DAR-016)
-> 9,090 (DAR-017) -> 9,108 (DAR-018) -> 9,156 (DAR-019) -> 9,287 (DAR-020) ->
9,397 (DAR-021) -> 9,623 (DAR-022) -> 9,633 (DAR-023) -> 9,742 (DAR-024) ->
9,760 (DAR-028) -> 9,965 (DAR-030) -> 10,100 (DAR-031) -> 10,235 (DAR-032) ->
10,300 (DAR-033) -> 10,365 (DAR-034).

### Gates

Run at $0, offline, with the exactly-pinned toolchain (ruff 0.16.3, mypy 2.3.1,
pytest 9.1.1) installed from a host-fetched wheelhouse.

| | |
|---|---|
| `ruff check src tests` | clean |
| `ruff format --check` | 53 files, clean |
| `mypy src/riftagent` | 8 source files, clean |
| provenance suites (DAR-030 … DAR-034) | **121 passed** |
| full suite | **888 passed, 5 skipped, 4 failed** |
| historical matrix | **9/9 recovered, 0 working candidates modified** |
| runtime | **10,351 / 10,365** (ceiling amended, governed) |
| runtime_hash | `60cc1431bfb3aa41a5971c465e5e45e29e96eb87bd9636061be817457128c8a1` |
| provider calls | **0** |
| additional spend | **$0.00** |

**`NOT_RUN_REFERENCE_ENVIRONMENT` — disclosed, not passed.** The same four tests
DAR-032 and DAR-033 recorded still fail, for the same verified reasons, and for
the same reason they cannot be cleared: container egress remains broken (DNS
resolves, TCP hangs; the host is unaffected, and a full Docker Desktop and WSL
restart did not fix it), so the suite runs in a substitute image rather than
`python:3.12-slim`. None touches the candidate pipeline or the auditor:

| test | cause, verified |
|---|---|
| `test_r07_an_interrupt_kills_the_child_process_tree` | the substitute image ships `/usr/bin/bwrap`; the test's one-shot `Popen` patch intercepts the isolation probe instead of the child. `python:3.12-slim` has no bwrap. |
| `test_a_merge_commit_is_rejected_until_a_protocol_governs_it` | that image's git defaults to branch `main`; the fixture assumes `master`, so its `--no-ff` merge fast-forwards to one parent. |
| `test_v16_clean_wheel_install_exposes_the_shipped_commands` | `pip wheel` build isolation fetches `setuptools>=68`: `Temporary failure in name resolution`. |
| `test_v16_installed_package_runs_a_real_verification` | same. |

No test was modified to accommodate the substitute image. These four need a
re-run in `python:3.12-slim` once egress is restored; until then this is an
evidence gap for a human reviewer to accept or reject, **not a green gate**.

---

## DAR-035 — The auditor proves stage identity, not merely attempt membership

**Status: IMPLEMENTED.** No provider call; additional spend **$0.00**. BM-06 was
not rerun. The frozen benchmark result is untouched. Audit hardening only: the
candidate-generation path is unchanged.

> DAR-034 confined every recorded artifact path to its own attempt directory,
> but checked only that a path landed *inside* that directory. An event could
> therefore name one stage's artifact as another's — supplying that file's real
> hash — and pass. It could also name an arbitrary filename in the directory, or
> a symlink at the correct path, because `Path.is_file()` follows links. DAR-035
> requires each stage to be exactly its own durable artifact and refuses
> symlinked artifacts and attempt directories.

### The three cases, reproduced before they were closed

Verified against the shipped runtime, with every file real, in the right attempt
directory, and hashing correctly:

```
raw_candidate_record = "candidate-attempt-001/normalized.diff"
raw_candidate_hash   = hash(NORMALIZED)                          -> ()

raw_candidate_record = "candidate-attempt-001/foo.diff"
raw_candidate_hash   = hash(FOO)                                 -> ()

candidate-attempt-001/raw.diff -> symlink to actualraw.diff
raw_candidate_hash   = hash(target bytes)                        -> ()
```

The first is the one that matters. DAR-032 split the pipeline into raw,
normalised and canonical **precisely so the model's own bytes could be told from
the transformed ones**, and DAR-033 made each attempt's copies immutable. An
auditor that accepts any stage's artifact for any stage hands that distinction
back: the event relabels the normalised bytes as the model's output, every digest
reconciles, and the check passes. The invariant being proven was

> this artifact belongs to attempt N and has the claimed hash

when what the governance language claims is

> this is attempt N's exact raw artifact.

The symlink case is the same weakness in a different dimension. `Path.is_file()`
follows links, so a link named `raw.diff` pointing at other bytes satisfies both
the path check and the hash. An immutable record that can be re-aimed after the
fact is not immutable.

None of this was reachable from the product. `_canonicalize_proposal` writes
exact stage paths through the record helpers and never creates links. As with
DAR-034, that is the reason to fix it rather than dismiss it: an auditor is worth
having only insofar as it is stricter than the thing it audits.

### The complete invariant

```
event says attempt N
  -> all three stages present, each with a path and a hash
  -> raw        is exactly candidate-attempt-NNN/raw.diff
     normalized is exactly candidate-attempt-NNN/normalized.diff
     canonical  is exactly candidate-attempt-NNN/canonical.diff
  -> every path relative and confined to the task directory
  -> every artifact a regular file; neither it nor its attempt directory a symlink
  -> every hash reconstructs from the bytes on disk
```

`_confined_candidate_path` now takes the stage and resolves the expected location
through `STAGE_RECORDS` — **the same three record functions
`_canonicalize_proposal` persists through**, with an import-time assertion that
the mapping covers `CANDIDATE_STAGES` exactly. The auditor and the writer cannot
drift into disagreeing about where a stage lives, and a fourth stage added
without a record function fails loudly rather than going silently unchecked.

Attempt and filename are reported separately, so a wrong attempt still reads
"does not belong to attempt NNN" and a wrong stage reads "is not the raw
artifact; attempt 001's raw stage is raw.diff". Symlinks are refused **before**
resolution, so the reason given is the link rather than wherever it pointed. A
symlinked attempt directory — the same re-aiming one level up — is refused too.

Every check still happens before the bytes are read: a hash computed over the
wrong file is a passing check and a false statement.

### Nothing else changed

`canonicalize_patch`, `authorized_change_only`, `normalize_candidate`,
`canonical_diff`, `persist_candidate` and `_canonicalize_proposal` were not
modified. The change is confined to `candidate_record_mismatches` and its helper.
No existing expectation needed weakening — the DAR-033 and DAR-034 assertions
about attempt mismatches still hold as written, because the attempt message was
kept distinct from the new stage message.

Re-proven on the frozen corpus:

| | |
|---|---|
| candidates replayed | **24** |
| originally working candidates modified | **0** |
| originally working candidates that stopped applying | **0** |
| byte-mask invariant held | **24 of 24** |
| **recovered through the full gate** | **9** — A 3, B 3, C 3 |
| recovery set identical to DAR-031's | **yes, candidate for candidate** |

Repair-loop behaviour was not expanded: `max_attempts`, semantic-repair policy
and application-repair policy are untouched, both loops remain **DEFERRED**, and
observed applied-but-verification-failed cases are still 0.

| | |
|---|---|
| **MEASURED** (BM-06, frozen) | A = 3/8, B = 3/8, C = 3/8 |
| **POST-HOC COUNTERFACTUAL** | A = 6/8, B = 6/8, C = 6/8 |

### Measurement

| module | before | after | delta |
|---|---|---|---|
| records.py | 2,387 | 2,421 | +34 |
| **total** | **10,351** | **10,385** | **+34** |

All of it is `_confined_candidate_path` becoming stage-aware plus the
`STAGE_RECORDS` mapping. No other runtime module changed.

**Ceiling amended 10,365 -> 10,400**, measured plus the reflow margin used since
DAR-016. Lineage: 8,000 -> 8,600 (DAR-008) -> 8,700 (DAR-012) -> 8,920 (DAR-016)
-> 9,090 (DAR-017) -> 9,108 (DAR-018) -> 9,156 (DAR-019) -> 9,287 (DAR-020) ->
9,397 (DAR-021) -> 9,623 (DAR-022) -> 9,633 (DAR-023) -> 9,742 (DAR-024) ->
9,760 (DAR-028) -> 9,965 (DAR-030) -> 10,100 (DAR-031) -> 10,235 (DAR-032) ->
10,300 (DAR-033) -> 10,365 (DAR-034) -> 10,400 (DAR-035).

### Gates

Run at $0, offline, with the exactly-pinned toolchain (ruff 0.16.3, mypy 2.3.1,
pytest 9.1.1) installed from a host-fetched wheelhouse.

| | |
|---|---|
| `ruff check src tests` | clean |
| `ruff format --check` | 53 files, clean |
| `mypy src/riftagent` | 8 source files, clean |
| provenance suites (DAR-030 … DAR-035) | **128 passed** |
| full suite | **895 passed, 5 skipped, 4 failed** |
| historical matrix | **9/9 recovered, 0 working candidates modified** |
| runtime | **10,385 / 10,400** (ceiling amended, governed) |
| runtime_hash | `75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26` |
| provider calls | **0** |
| additional spend | **$0.00** |

**`NOT_RUN_REFERENCE_ENVIRONMENT` — disclosed, not passed.** The same four tests
DAR-032, DAR-033 and DAR-034 recorded still fail, for the same verified reasons,
and for the same reason they cannot be cleared: container egress remains broken
(DNS resolves, TCP hangs; the host is unaffected, and a full Docker Desktop and
WSL restart did not fix it), so the suite runs in a substitute image rather than
`python:3.12-slim`. None touches the candidate pipeline or the auditor, and none
should be reinterpreted as a candidate-pipeline failure:

| test | cause, verified |
|---|---|
| `test_r07_an_interrupt_kills_the_child_process_tree` | the substitute image ships `/usr/bin/bwrap`; the test's one-shot `Popen` patch intercepts the isolation probe instead of the child. `python:3.12-slim` has no bwrap. |
| `test_a_merge_commit_is_rejected_until_a_protocol_governs_it` | that image's git defaults to branch `main`; the fixture assumes `master`, so its `--no-ff` merge fast-forwards to one parent. |
| `test_v16_clean_wheel_install_exposes_the_shipped_commands` | `pip wheel` build isolation fetches `setuptools>=68`: `Temporary failure in name resolution`. |
| `test_v16_installed_package_runs_a_real_verification` | same. |

No test was modified to accommodate the substitute image. These four need a
re-run in `python:3.12-slim` once egress is restored; until then this is an
evidence gap for a human reviewer to accept or reject, **not a green gate**.

### Handover packaging correction (no product change)

The DAR-035 handover archive claimed "128 passed" for the provenance suites and
shipped no fixture source, so a reviewer extracting it measured 127 passed and
one setup error: the end-to-end retry test needs `simple_repo`, `run_cli`,
`correct_diff` and `judge_diff` from `tests/conftest.py`. That was a defect in
the archive, not in the product or the suites.

The archive now includes `tests/conftest.py`, `pyproject.toml` and
`requirements-dev.txt`, and the builder proves the claim rather than asserting
it: it extracts the finished archive to a clean directory, runs the included
suites there, refuses to emit an archive whose own suites do not pass, and
writes the measured count into the README. Verified: **128 passed** from an
extraction.

**A reviewed, accepted non-defect.** Python path normalisation means
`candidate-attempt-001/./raw.diff` and `candidate-attempt-001//raw.diff` audit
successfully. They identify the same attempt, stage, file and bytes, so no
provenance property is weakened; only the spelling of the recorded string
differs, and the product writes exactly one spelling. Requiring byte-for-byte
canonical spelling would be a one-line change and is deliberately not made:
canonical path spelling is not a governed requirement, and adding a runtime
invariant without an amendment to justify it is how ungoverned rules accumulate.
Recorded so it is not rediscovered as a finding.

---

## DAR-036 — BM-07 curation measurement corrected; same-candidate shadow gating frozen

**Status: IMPLEMENTED.** No provider call; additional spend **$0.00**. BM-07 was
not started. **The RIFT runtime is unchanged** — `runtime_hash` is identical
before and after this pass, and nothing under `src/riftagent` was touched.

> Target-node extraction could omit an existing enclosing test class, and
> preservation counting included pre-existing tests the fix commit had modified.
> Both were **curation-measurement defects, not RIFT product defects**. BM-07
> freezes same-candidate shadow gating as its primary protocol-isolation method.
> The RIFT runtime, the diagnosis ontology and the deferral of semantic repair
> all remain frozen. BM-07 is a mechanism/discrimination benchmark, not a
> representative general coding benchmark.

### 1 — Target nodes could omit an enclosing class

The node id was assembled from the diff alone. For a method added to a class that
already existed, the diff carries only:

```diff
+    def test_boundary(self):
```

so curation produced `tests/test_cache.py::test_boundary` when the runnable node
is `tests/test_cache.py::TestCache::test_boundary`. The class declaration is
context the diff never contained. A node id that does not resolve is not a
target, and a corpus of them would have failed at execution with no indication
that curation, rather than the repository, was wrong.

The diff now identifies *which* lines are new; the **full test file at the fix
commit** says what they are part of. `curation.collect_tests` parses it with
`ast` and returns only the two shapes pytest addresses this way — a module-level
function, and a method one class deep. Matching on the `def` line number rather
than the name keeps two same-named methods in different classes distinct.
Anything nested further, or defined inside a function, is **excluded rather than
guessed**; indentation is never used to infer a class.

Worth recording: git's hunk header often echoes the enclosing class, so a
diff-only extractor can appear to work. It is a heuristic that shows the nearest
enclosing *definition* — frequently the previous method — and relying on it is
exactly the kind of near-miss that survives review.

The effect on the frozen pool was not marginal: **59 of 181** mined candidates
were dropped for `no_resolvable_target`, and surviving targets that had
previously been emitted bare now read `CroniterRangeTest::test_overflow`,
`TestPluginManager::test_call_with_too_few_args`,
`TZTest::testIsdstZoneWithNoDaylightSaving`.

### 2 — Preservation counted tests the fix had modified

"Existed at the parent" is not the property that matters. A test the fix commit
rewrote cannot witness that a candidate preserved anything — it is part of what
the fix changed.

A preservation candidate must now exist at the parent, still exist at the fix
commit, and have **no changed line anywhere in its own span**, decorators
included. Spans come from `ast`; changed lines come from the diff. A declaration
line being unchanged is never taken to mean a body is unchanged, and pure
insertions are handled explicitly — deletions alone would call a test untouched
after lines were added inside it.

Two deliberately conservative rules:

* a change inside a class but outside every test in it — `setUp`, a class
  attribute, a shared fixture — marks **every** test in that class touched,
  because it can change what those tests observe;
* a change at module level outside any class marks nothing touched on its own. A
  helper being edited is not evidence that a particular test changed.

An insertion anchored on a span's last line lands *after* it, which is what
appending a new test to a file looks like, so it leaves the previous test alone.

Measured effect: `attrs-c1adf5cf` 71 → **68** untouched, `pluggy-f8b0f34a` 56 →
**54**, `humanize-647e7312` 19 → **18**.

### 3 — Model-free construction, and what it found

Every shortlisted case was built and run, in order, with nothing repaired by
hand: parent checked out, the fix commit's **test** changes applied as the frozen
reproducer, target must fail; the commit's **source** changes applied, target
must pass; untouched preservation checks must pass.

**Attempted 51 · validated 10 · distinct target behaviours 9**

| case_id | repo | target node | resolution | untouched preservation | potential | structure |
|---|---|---|---|---|---|---|
| `cachetools-462e8679` | cachetools | `test_keys.py::CacheKeysTest::test_pickle` | class_method | 3 | low | cache |
| `click-2bc3b2c1` | click | `test_testing.py::test_setting_prog_name_in_extra` | module_level | 11 | medium | exception |
| `click-61f8101f` | click | `test_options.py::test_show_default_with_empty_string` | module_level | 58 | medium | boundary |
| `click-d340b0c1` | click | `test_options.py::test_show_default_with_empty_string` | module_level | 83 | medium | boundary |
| `croniter-7d319c51` | croniter | `test_croniter_hash.py::CroniterHashTest::test_invali` | class_method | 22 | medium | exception |
| `icalendar-63fcf743` | icalendar | `test_fixed_issues.py::TestIssues::test_index_error_i` | class_method | 16 | medium | exception |
| `icalendar-66fc205a` | icalendar | `test_conference.py::test_conference_list_params_seri` | module_level | 10 | medium | boundary |
| `structlog-bf80fa60` | structlog | `test_threadlocal.py::TestTmpBind::test_converts_pass` | class_method | 9 | medium | boundary |
| `tenacity-0b1cef0b` | tenacity | `test_asyncio.py::TestContextManager::test_retry_with` | class_method | 10 | medium | exception |
| `tenacity-78c8d4bc` | tenacity | `test_asyncio.py::TestContextManager::test_async_rety` | class_method | 11 | medium | exception |

**Rejections (41)**

| reason | n |
|---|---|
| repository does not run on Python 3.12 | 28 |
| upstream source fix does not fix the target here | 9 |
| reproducer already passes at the parent | 4 |


`click-d340b0c1` and `click-61f8101f` are two commits against the **same** target
node, `test_show_default_with_empty_string` — a later re-fix of the same
behaviour. Both validate independently; they are reported as 10 validated cases
over 9 distinct behaviours, and only one should enter a run so the denominator is
not inflated by a repeat.


The dominant rejection is not case quality. It is that commits mined from deep
history frequently cannot import on Python 3.12 at all — `@asyncio.coroutine`
removed, packages predating `importlib.metadata`, syntax the interpreter no
longer accepts. That is a property of the commit's **era**, and it is a selection
problem for curation rather than a defect in the case or the product.
Candidates are therefore tried newest-first, and the requirement thresholds were
**not** relaxed to raise the yield.

### 4 — Same-candidate shadow evaluation, frozen

BM-07's question is whether strong verification rejects wrong candidates that
weak target-pass acceptance would accept. Independent arm proposals cannot answer
it: `A accepted X, C rejected Y` mixes proposal quality with verification policy.

Every canonical arm-A candidate is therefore judged **twice, on the same bytes** —
once by target-pass acceptance, once by the full RIFT gate — recording
`weak_verdict`, `strong_shadow_verdict` and `ground_truth_verdict`. The shadow
evaluation makes **no additional model call**.

Primary metric, frozen before execution:

```
same-candidate harmful weak acceptances prevented
    weak = ACCEPT, strong = REJECT, ground truth = WRONG
```

always reported beside its mirror — strong REJECT on a ground-truth CORRECT
candidate — because a gate can buy apparent safety by rejecting good work. The
full four-cell matrix is reported whole. A/B/C yields, cost and abstention remain
**secondary**.

### 5 — What did not change

```
RIFT runtime            unchanged; runtime_hash identical before and after
diagnosis ontology      frozen; representation_inadequate stays a valid outcome
semantic repair loop    DEFERRED
application repair loop DEFERRED
canonicalizer authority recompute @@ counts only, common to A, B and C
```

Diagnosis compatibility is not a curation criterion: a case that yields
`representation_inadequate` during autonomous diagnosis still tests the
proposal-and-verification mechanism, which is what BM-07 measures.

No corpus size is targeted. The validated denominator is reported as measured.

### 6 — Claim discipline

BM-07 is a **mechanism benchmark**. Its corpus is curated for natural cases where
target-pass acceptance can be insufficient, so it cannot support a general
performance claim. Discrimination is **opportunity** at curation time and a
**result** only when an actual same-candidate patch produces protocol
disagreement.

### Gates

Run at $0 in the governed reference environment (`python:3.12-slim`, Python
3.12.14, git 2.47.3) with the pinned toolchain (ruff 0.16.3, mypy 2.3.1,
pytest 9.1.1):

| | |
|---|---|
| `ruff check src tests benchmark` | clean |
| `ruff format --check` | 90 files clean |
| `mypy src/riftagent` | 8 source files clean |
| full suite | **927 passed, 5 skipped, 0 failed** (16:23) |
| BM-07 curation regressions | **28 passed** |
| `runtime_hash` | `75196d87…` — **unchanged**, identical to the checkpoint |
| governed LOC | 10,385 / 10,400 — unchanged |
| provider calls | **0** |
| additional spend | **$0.00** |

`test_r07_an_interrupt_kills_the_child_process_tree` failed once during an
earlier run of this pass and passed on a clean re-run, 3/3 in isolation and again
in the full suite. Its failure mode was an empty pidfile after the fixture's
30-second deadline — a process-startup race that lost while a dozen containers
were competing for the machine. The runtime hash was identical across both runs,
so nothing under `src/riftagent` could have caused it. Recorded rather than
quietly re-run: the test is load-sensitive, which is a property worth knowing
before it is seen again in CI.

---

## DAR-037 — BM-07 curation integrity: complete preservation, baseline admission, structural ownership

**Status: IMPLEMENTED.** No provider call; additional spend **$0.00**. BM-07 was
not started. **The RIFT runtime is unchanged** — `runtime_hash` identical before
and after, nothing under `src/riftagent` touched.

Five deterministic defects, all in BM-07 *measurement*. Each would have produced
a corpus that looked sound and was not, and two of them could have manufactured
the very result the benchmark exists to detect.

### 1 — Preservation was truncated

Curation discovered the full untouched surface and persisted `nodes[:12]`;
validation ran `nodes[:8]`. Cases with 83, 58, 22, 16 and 11 available nodes were
each judged on eight.

That is incompatible with the claim the corpus is built to support. A candidate
can pass the first eight nodes and break the thirty-seventh, and the record would
still read *preservation passed*. Ground truth computed from a sample is not
ground truth.

Both truncations are gone. The complete set is persisted and executed — 233 nodes
across the ten validated cases, the widest being 83. Execution batches node ids
into one pytest process with a recorded chunk size; a chunk that fails is
re-run node-by-node to name the offenders. It is a chunk size, never a sample,
and the chunk list is written into the record so the shape of the execution is
visible rather than implied.

### 2 — Preservation was never required to pass on the buggy baseline

The old sequence proved: target fails at parent, target passes after the fix,
preservation passes after the fix. It never asked whether preservation passed
*before*.

A node already failing on the buggy tree would then be read, later, as a model
candidate regressing behaviour that never worked:

```
baseline      target FAIL   preservation_1 FAIL
historical    target PASS   preservation_1 PASS
candidate     target PASS   preservation_1 FAIL   -> "regression"
```

That is a fabricated `weak ACCEPT / strong REJECT` — **the exact event BM-07's
primary metric counts** — produced by curation rather than by verification. A
benchmark that can manufacture its own headline result is worse than no
benchmark.

Admission now requires the complete preservation set green on the buggy baseline
*and* after the historical fix. A node failing at baseline is not redefined as
preserved; the case is rejected.

It caught contamination immediately: **8 of 41 rejections** are cases whose
preservation set fails *entirely* on the buggy tree — werkzeug 81 of 81, 64 of
64, 37 of 37, 36 of 36; jinja 48 of 48; pluggy 54 of 54; pygments 3 of 3. Whole
modules that cannot run there, previously passing through with a sampled surface
that happened to be measured after the fix.

### 3 — Selection contradicted the protocol

The protocol said one case per repository; the corpus held 10 cases from 6
repositories. Both cannot be true.

Selection is now explicit and happens **before any provider call**: among
model-free-validated cases, the highest pre-model curation score per repository
becomes the **primary corpus**; the rest are recorded as **validated fallback**
and are not part of the reported denominator. Fallback substitution is not
permitted, because no replacement conditions were predefined — so the fallbacks
exist as evidence, not as spares to be swapped in after seeing results.

```
validated  10        primary  6        fallback  4
```

### 4 — A nested test could alias a runnable one

Added `def` lines were matched to tests by name and span, so:

```python
def test_outer():
    x = 1

    def test_outer():      # not a pytest node at all
        return 2

    assert x
```

resolved to `tests/test_x.py::test_outer` — the outer, collectable test. The
corpus would have carried a target that runs something other than the code the
fix commit added.

Resolution now binds an added `def` line to the AST node declared at **exactly
that line**, and accepts it only if that node is a module-level function or a
method in the direct body of a top-level class. There is no fallback to name or
span matching, which is what created the alias.

### 5 — Class-level pure insertions did not taint their class

Inserting `setup_method`, a class attribute or an autouse fixture changes what
every method in a class observes while touching no test body, so deletion-based
accounting called those tests untouched.

The first attempt at this used insertion anchors and was wrong, in a way worth
recording: **appending a `setup_method` and appending a new test method land on
the same anchor** — the last line of the previous test. Any position-based rule
must taint both or neither, and both answers are wrong. What separates them is
*what* was inserted.

So class-level change is now decided **structurally**: every direct class member
that is not a test method — setup, teardown, attributes, fixtures, shared helpers
— plus base classes and class decorators, is rendered to source and compared
between parent and fix. Any difference taints every test in that class. Adding a
test method changes none of those members and taints nothing.

### The corpus, recomputed from frozen inputs

| case_id | role | target node | resolution | preservation | baseline target | baseline pres | fixed target | fixed pres |
|---|---|---|---|---|---|---|---|---|
| `cachetools-462e8679` | primary | `tests/test_keys.py::CacheKeysTest::test_pickle` | class_method | **3** | fail | 3/3 pass | pass | 3/3 pass |
| `click-2bc3b2c1` | primary | `sts/test_testing.py::test_setting_prog_name_in_extra` | module_level | **11** | fail | 11/11 pass | pass | 11/11 pass |
| `croniter-7d319c51` | primary | `iter_hash.py::CroniterHashTest::test_invalid_divisor` | class_method | **22** | fail | 22/22 pass | pass | 22/22 pass |
| `icalendar-63fcf743` | primary | `_fixed_issues.py::TestIssues::test_index_error_issue` | class_method | **16** | fail | 16/16 pass | pass | 16/16 pass |
| `structlog-bf80fa60` | primary | `TestTmpBind::test_converts_passed_and_yielded_logger` | class_method | **9** | fail | 9/9 pass | pass | 9/9 pass |
| `tenacity-0b1cef0b` | primary | `yncio.py::TestContextManager::test_retry_with_result` | class_method | **10** | fail | 10/10 pass | pass | 10/10 pass |
| `click-61f8101f` | validated_fallback | `test_options.py::test_show_default_with_empty_string` | module_level | **58** | fail | 58/58 pass | pass | 58/58 pass |
| `click-d340b0c1` | validated_fallback | `test_options.py::test_show_default_with_empty_string` | module_level | **83** | fail | 83/83 pass | pass | 83/83 pass |
| `icalendar-66fc205a` | validated_fallback | `erence.py::test_conference_list_params_serialization` | module_level | **10** | fail | 10/10 pass | pass | 10/10 pass |
| `tenacity-78c8d4bc` | validated_fallback | `.py::TestContextManager::test_async_retying_iterator` | class_method | **11** | fail | 11/11 pass | pass | 11/11 pass |

**Rejections (41)**

| reason | n |
|---|---|
| repository does not run on Python 3.12 | 28 |
| preservation already failing at the buggy baseline | 8 |
| reproducer already passes at the parent | 4 |
| upstream source fix does not fix the target here | 1 |


```
mined                        181
passed corrected curation     51
model-free validated          10   (6 repositories)
primary corpus                 6
validated fallback             4
preservation nodes executed  233   (largest single set: 83)
```

Every validated case: direct parent verified, target **fails** at
parent+reproducer, **complete** preservation set passes there, target **passes**
after the historical source fix, complete preservation set still passes. 10/10 on
every one of those five checks.

### Unchanged

```
RIFT runtime            unchanged; runtime_hash identical before and after
same-candidate shadow   unchanged; still the authority experiment
primary metric          unchanged
diagnosis ontology      frozen
semantic repair         DEFERRED
application repair      DEFERRED
canonicalizer parity    common to A, B and C
```

BM-07 remains a **mechanism benchmark**. No case discriminates until an actual
canonical candidate produces a protocol disagreement; curation establishes
opportunity only.

### Manifest

Frozen only after validation was green, every field recomputed from the case
records and the tree:

```
primary corpus     6 cases, 71 preservation nodes
runtime_hash       75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26
driver_hash        d54e7658be4c5d0f0fe4c5eb253314ef67f58f7061192abd3726ae18e1f1b85b
manifest_hash      5e2b54d13c705b391cc617c63b043747c0ec4fd6e1a300207fb6362f07acc795
```

The manifest carries no historical source patch content. The upstream fix,
preservation expectations and shortcut hypotheses stay harness-only.

### Gates

Governed reference environment (`python:3.12-slim`, Python 3.12.14, git 2.47.3),
pinned toolchain (ruff 0.16.3, mypy 2.3.1, pytest 9.1.1):

| | |
|---|---|
| `ruff check src tests benchmark` | clean |
| `ruff format --check` | 91 files clean |
| `mypy src/riftagent` | 8 source files clean |
| full suite | **936 passed, 5 skipped, 0 failed** (15:19) |
| BM-07 curation regressions | **37 passed** |
| `runtime_hash` | `75196d87…` — **UNCHANGED** |
| governed LOC | 10,385 / 10,400 — unchanged |
| provider calls | **0** |
| additional spend | **$0.00** |

**READY_FOR_BM07_REVIEW** — corpus and manifest frozen. No paid run authorized or
started.

---

## DAR-038 — BM-07 execution infrastructure: independent ground truth, isolated verdicts

**Status: IMPLEMENTED.** No provider call; additional spend **$0.00**. BM-07 was
not started. **The RIFT runtime is unchanged** — `runtime_hash` identical before
and after; nothing under `src/riftagent` touched. Benchmark infrastructure only.

> The BM-07 corpus was already frozen, but the historical BM-06 driver could not
> execute the new manifest and coupled strong shadow evaluation with ground
> truth. BM-07 requires an executable benchmark schema, exact constructed-baseline
> identity, failure identity captured in the enforcing vocabulary, and an
> independent non-RIFT case oracle so strong-verdict-vs-truth divergence remains
> observable.

### Ground truth no longer asks the thing it is judging

BM-06 computed truth from the gate's own verdict. `strong REJECT -> truth WRONG`
was therefore true by construction, and any "RIFT prevented a harmful
acceptance" count was counting itself.

`benchmark/bm07/oracle.py` imports `git`, `pytest` and the standard library —
and nothing else. A test parses its AST and fails if `riftagent` ever appears
among its imports. It applies the candidate, refuses protected-path edits, runs
the target, runs the **complete** preservation set, and returns
`correct`/`wrong` on that basis alone. It never reads the historical patch and
never compares candidate text to it: a different implementation satisfying the
frozen behaviour is correct.

### One candidate, three fresh baselines

Weak, strong and truth each get their own materialised baseline, whose tree hash
must equal the manifest's before anything is evaluated, and which is destroyed
afterwards. Reuse-and-clean was rejected as the isolation mechanism: a residue
bug there would be invisible and would silently couple the verdicts.

The canonical bytes are hashed once, and each verdict records the hash of what it
was actually given. The dry run asserts all three equal the one
`canonical_candidate_hash`.

### Failure identity in the enforcing vocabulary

Signatures are captured through `riftagent.checks.run_check` — the call the gate
makes — so the frozen signature and the observed one are the same kind of object
compared by `Signature.matches`.

This immediately corrected a hand-written assumption: a bare `assert` is reported
by that observer as **`Failure`**, not `AssertionError`. A manifest hand-encoded
with `AssertionError` would have matched nothing. The six frozen identities are
genuinely varied — `AssertionError`, `Failure`, `ZeroDivisionError`,
`IndexError`, `AssertionError`, `TypeError` — which is itself evidence they were
observed rather than assumed.

### Two defects the dry run caught in this very driver

Running the six real cases with the projects' own historical fixes — patches
known to be correct — first reported **4 of 6 as `strong_false_rejection`**.

That was not RIFT rejecting anything. `python -m riftagent` could not start for
flat-layout repositories, because `PYTHONPATH` was being set only for src-layout
cases, so the gate produced **no receipt at all**; and `evaluate_strong` mapped a
missing receipt to `REJECT`. Together they manufactured a false-rejection rate
out of harness breakage — the exact species of fabricated result this benchmark
exists to avoid, produced by the benchmark itself.

Both are fixed. The child always gets `riftagent` on its path, and an unrunnable
strong evaluation is now its own outcome, `strong_unrunnable`, never folded into
a verdict. After the fix all six cases read `weak accept / strong accept / truth
correct`, which is the right answer for an upstream fix and a strong sanity check
on the whole harness.

Worth recording why the synthetic fixtures did not catch it: they run under
pytest, whose path is already configured. Only the real cases, launched as
subprocesses from a bare interpreter, exercised the path the benchmark will
actually use.

### The outcome matrix, and one cell that cannot occur

| fixture | cells | result |
|---|---|---|
| A safety success | weak ACCEPT · strong REJECT · truth WRONG | **reachable** |
| B strong false rejection | weak ACCEPT · strong REJECT · truth CORRECT | **reachable** |
| C normal correct acceptance | weak ACCEPT · strong ACCEPT · truth CORRECT | **reachable** |
| D shared false accept | weak ACCEPT · strong ACCEPT · truth WRONG | **structurally impossible** |

Fixture B was produced without modifying RIFT: a candidate that fixes the bug and
also edits an unrelated test file is correct by the case oracle and refused by
RIFT's frozen-judge rule. The policies genuinely differ, and the harness can now
write that down.

**Cell D cannot occur, and this has a consequence that belongs in the report
rather than a footnote.** The oracle returns WRONG for four reasons — the patch
does not apply, it touches a protected path, the target fails, or a preservation
node fails — and the strong gate checks all four, on the same complete
preservation set. So strong cannot ACCEPT what truth calls WRONG. Which also
means:

> Cell A is **guaranteed** whenever the weak protocol accepts a
> preservation-breaking patch. The primary metric therefore measures how often
> target-pass acceptance admits such a patch — a real and useful quantity — and
> does **not** test whether RIFT catches one, which it will by construction.

The genuinely open questions are cell B, the arm-level yields, and cost. Stating
that before execution is the difference between a measurement and a
foregone conclusion.

### Fail-closed before spend

`validate_manifest` refuses a manifest missing arms, budget, model identity,
pricing, `baseline_tree_hash`, `failure_identity`, or whose
`preservation_count` disagrees with the node list — the truncation defect, kept
out at the schema. `budget_preflight` additionally requires the configured model
to equal the manifest's, remaining budget to cover the reservation, and the
runtime, driver and manifest hashes to match.

That contract fired for real during this pass: after `driver.py` changed, the dry
run refused to proceed with `driver identity: manifest driver_hash != observed`
until the manifest was rebuilt.

### Frozen

```
runtime_hash    75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26  (unchanged)
driver_hash     71fecb039b0e541e68bac35fd943a6da01c3050d6f3a23bbdf509133c24ae47b
manifest_hash   6359d0d0971ae24bb70b2c4e9324a39c740863c50cd870169cff1ebafab6d988
```

Corpus, canonicaliser, diagnosis ontology and repair policy are all unchanged.
Retry behaviour was not expanded; the deterministic canonicaliser remains the
only patch-representation recovery mechanism.

### A defect this pass introduced, and the rename that fixed it

`tests/test_bm07_driver.py` inserted `benchmark/bm07` at the **front** of
`sys.path` at import time. `benchmark/bm06` also defines a module named
`driver`, so for the rest of the pytest session every `import driver` resolved to
BM-07's — and **70 tests failed**. `runtime_hash` was unchanged throughout, which
is what identified it as benchmark tooling rather than the product.

Fixed by giving the BM-07 modules distinct names, `bm07_driver` and
`bm07_oracle`, rather than relying on path ordering. Two benchmarks sharing a
module name is a trap that would otherwise recur every time a third arrives.

### Gates

Governed reference environment (`python:3.12-slim`, Python 3.12.14, git 2.47.3),
pinned toolchain (ruff 0.16.3, mypy 2.3.1, pytest 9.1.1):

| | |
|---|---|
| `ruff check src tests benchmark` | clean |
| `ruff format --check` | 96 files clean |
| `mypy src/riftagent` | 8 source files clean |
| full suite | **972 passed, 5 skipped, 0 failed** (34:25) |
| `validate_manifest` on the real manifest | **0 failures** |
| six-case model-free preflight | **0 failures** |
| fake-provider dry run | 6/6, identical canonical bytes in all three verdicts |
| `runtime_hash` | `75196d87…` — **UNCHANGED** |
| `driver_hash` manifest vs observed | **MATCH** |
| `manifest_hash` recorded vs recomputed | **MATCH** |
| provider calls | **0** |
| additional spend | **$0.00** |

**READY_FOR_BM07_REVIEW** — execution wiring frozen and green. No paid run
authorized or started.

---

## DAR-039 — BM-07 paid execution harness

**Status: IMPLEMENTED.** 0 provider calls; additional spend **$0.00**. BM-07 was
not executed. **The RIFT runtime is unchanged** — `runtime_hash` identical before
and after; nothing under `src/riftagent` touched.

> BM-07's corpus and RIFT runtime remain frozen. This pass completes only the
> paid execution harness: full truth protected-path semantics, independent oracle
> identity binding, actual A/B/C provider orchestration, reservation-based
> spending, response-bound model identity, and full fake-provider execution
> through the same run path intended for paid BM-07.

### 1 — Ground truth now enforces the whole protected-path contract

The correctness contract says a candidate is wrong if it modifies the frozen
tests **or the runner configuration**. The manifest listed only test files, so a
patch that fixed the target and edited `pyproject.toml` would have been recorded
`weak ACCEPT / strong REJECT / truth CORRECT` — a false-rejection reading
produced by an incomplete protected set rather than by a policy difference.

`protected_paths` is now the union of the test files owning the target and
preservation nodes, and every runner-configuration file actually present at the
parent: `conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`.
Only files that exist are listed, so the set names real artifacts. The policy
lives in `bm07_oracle.RUNNER_CONFIG_FILES`, beside the code that enforces it.

### 2 — The dishonest false-rejection fixture is gone

The previous fixture produced `strong REJECT / truth CORRECT` by **narrowing
`protected_paths` for the truth evaluator only**. Strong and truth were therefore
judging different cases, which is precisely the substitution that voids a
comparison. It was deleted rather than repaired, and a test now asserts no
evaluator receives a modified copy of the case.

Under the frozen semantics the strong gate checks a superset of the oracle's
conditions — it applies the patch, runs the target, runs the same complete
preservation set, and enforces the same protected paths. **A model-free candidate
that is CORRECT by the oracle and REJECTED by the gate has therefore not been
demonstrated**, and is reported that way rather than manufactured. The classifier
still recognises the outcome, so a real run producing one records it correctly.

### 3 — The program that defines truth is now pinned

The benchmark froze runtime, driver and manifest identities but not the oracle.
A benchmark that pins everything except the code deciding right from wrong has
not pinned its result.

`oracle_hash` is in the manifest and is checked twice: with the other identities
before any provider call, and **again before scoring**, so the truth program
cannot drift between deciding and aggregating. It is deliberately not folded into
`runtime_hash` — it is benchmark infrastructure and stays independently
identifiable.

### 4 — The actual paid runner

`benchmark/bm07/bm07_runner.py`, invoked as:

```
python benchmark/bm07/bm07_runner.py run --manifest benchmark/bm07/manifest-executable.json
```

One command performs preflight, budget, provider, identity, candidate pipeline,
arm evaluation, the Arm-A same-candidate shadow, independent truth, settlement
and the durable record. Nothing requires an operator to call functions between
provider responses.

**Nothing RIFT owns was re-implemented.** Arms are invoked through the frozen
`rift fix` CLI exactly as BM-06 invoked them — `--model-alone` for A,
`--probe-policy random` for B, the default kernel for C — so the provider
adapter, the reserve/request/settle ledger, the schema-repair policy, the
model-response evidence and the raw → normalized → canonical pipeline are the
shipped ones. A test forbids `urllib`, `http.client`, `requests` and `httpx` in
the runner: a benchmark with its own HTTP path would be measuring a different
client.

**The reservation is derived, not supplied.** `required_reservation(manifest)`
takes one argument — there is no `reserve_usd` parameter a caller could set to
zero. It is computed from the manifest's own pricing and token ceilings,
including the one authorised schema repair, and floored at the declared per-arm
cap. A case-arm whose reservation exceeds remaining budget is skipped with **no
adapter call**.

**Model identity comes from the response evidence.** Every
`MODEL_RESPONSE_RECEIVED` in the task ledger is read, schema-repair responses
included, and any reported model differing from the manifest's blocks the record.
Configuration, pricing metadata and the request body are not treated as proof.

**A crash cannot re-spend.** Records are appended and fsynced; a case-arm already
recorded `completed` is not re-run.

### 5 — The fake-provider run, and what it caught

The dry run drives the **same** `run` entry point with `RIFT_LLM_URL` pointed at
a scripted OpenAI-compatible server on loopback, so the real adapter, ledger and
repair policy are exercised. No network, no spend.

The first attempt looked successful — 18 records, settlement, exit 0 — and was
not: **17 of 18 arms reported `unverifiable` and only 1 of 6 Arm-A comparisons
carried a candidate.** The fake served replies from a flat queue while arms make
differing numbers of calls, so after the first case every arm received a reply
meant for something else. Reporting "full-path dry run passed" on that would have
been true and misleading.

The fake now dispatches on the request: a handles request gets handles, a change
request gets the candidate scripted for *that* case, matched by the target node
id in the prompt. Order-independent and deterministic.

| case | arm | status | arm verdict | classification | $ |
|---|---|---|---|---|---|
| `cachetools-462e8679` | A | completed | accepted_by_target_pass | both_correct_accept | 0.0081 |
| `cachetools-462e8679` | B | completed | verified_against_approved_chec | — | 0.0081 |
| `cachetools-462e8679` | C | completed | verified_against_approved_chec | — | 0.0081 |
| `click-2bc3b2c1` | A | completed | unverifiable | both_reject | 0.0081 |
| `click-2bc3b2c1` | B | completed | unverifiable | — | 0.0081 |
| `click-2bc3b2c1` | C | completed | unverifiable | — | 0.0081 |
| `croniter-7d319c51` | A | completed | unverifiable | both_reject | 0.0081 |
| `croniter-7d319c51` | B | completed | unverifiable | — | 0.0081 |
| `croniter-7d319c51` | C | completed | unverifiable | — | 0.0081 |
| `icalendar-63fcf743` | A | completed | unverifiable | — | 0.0162 |
| `icalendar-63fcf743` | B | completed | verified_against_approved_chec | — | 0.0081 |
| `icalendar-63fcf743` | C | completed | verified_against_approved_chec | — | 0.0081 |
| `structlog-bf80fa60` | A | completed | unverifiable | — | 0.0162 |
| `structlog-bf80fa60` | B | completed | unverifiable | — | 0.0162 |
| `structlog-bf80fa60` | C | completed | unverifiable | — | 0.0162 |
| `tenacity-0b1cef0b` | A | completed | accepted_by_target_pass | both_correct_accept | 0.0081 |
| `tenacity-0b1cef0b` | B | completed | verified_against_approved_chec | — | 0.0081 |
| `tenacity-0b1cef0b` | C | completed | verified_against_approved_chec | — | 0.0081 |

18 arm records · 22 loopback requests · **4 of 6 Arm-A same-candidate comparisons** carried a candidate and had identical bytes in all three verdicts · 0 real provider calls · $0.00 real spend.

Outcomes exercised: correct acceptance, mutual rejection, an unapplicable
candidate, a malformed reply consuming the one authorised schema repair (two
requests on that case), and an abstention that produced no candidate.

**One limitation, stated plainly.** The "wrong candidate" script uses an
unapplicable patch rather than a genuinely target-passing-but-preservation-
breaking one. Constructing the latter generically across six unrelated real
repositories is not something a harness can do, and hand-authoring one per
repository would be the synthetic trap the corpus protocol forbids. That shape is
covered deterministically by the driver's own fixtures, where the repository is
controlled; the full-path run drives the reject path via the unapplicable
candidate.

### Unchanged

```
RIFT runtime            unchanged; runtime_hash identical before and after
corpus                  unchanged; six cases, same targets and preservation sets
canonicalizer           unchanged
diagnosis ontology      unchanged
repair semantics        unchanged; one schema repair, no semantic or application retry
```

Changed: the driver, the oracle's metadata and policy constant, and the
executable manifest.

### Frozen identities

```
runtime_hash    75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26  (unchanged)
driver_hash     91a7717f130bebe070ee18e50723be10a5b55094a743b48777c2c632cb461034
oracle_hash     6c159fed18f3258d6d30f01811c99b0264bc3f3d5b95ec3be7c1bcc25c0aa83a
manifest_hash   cbf8354939466b0678699e37c612669db53c64885c9378fa2f2a346dd4011a76
```

### Gates

Governed reference environment (`python:3.12-slim`, Python 3.12.14, git 2.47.3),
pinned toolchain (ruff 0.16.3, mypy 2.3.1, pytest 9.1.1):

| | |
|---|---|
| identity chain | runtime **UNCHANGED**, driver **MATCH**, oracle **MATCH**, manifest **MATCH** |
| `validate_manifest` | **0 failures** |
| six-case preflight through the real runner | **0 failures** |
| fake-provider full `run` | 18 arm records, 4/6 Arm-A same-candidate, $0.00 real spend |
| `ruff check src tests benchmark` | clean |
| `ruff format --check` | 99 files clean |
| `mypy src/riftagent` | 8 source files clean |
| BM-07 suites | **89 passed** |
| full suite | **983 passed, 5 skipped, 5 failed under load** |
| provider calls | **0** |
| additional spend | **$0.00** |

**The five full-suite failures are load artifacts, and were checked rather than
assumed.** All five are timing, PTY or concurrency tests —
`test_r07_an_interrupt_kills_the_child_process_tree`, two streaming-PTY tests,
`test_concurrent_processes_never_share_a_task_directory`, and one signature-only
integrity case. Re-run in isolation on the same image they pass **30/30**, and
`runtime_hash` was identical across both runs, so nothing under `src/riftagent`
could have caused them. The machine was running several containers concurrently
at the time. Recorded rather than quietly re-run, because load sensitivity in
this group is worth knowing before it is met in CI.

**READY_FOR_PAID_BM07_REVIEW** — the paid run path is frozen and green. No paid
run authorized or started.

---

## DAR-040 — BM-07 paid-run harness: preflight before spend, durable state, independent truth for every arm

**Status: IMPLEMENTED.** 0 provider calls; additional spend **$0.00**. BM-07 was
not executed. **The RIFT runtime is unchanged** — `runtime_hash` identical before
and after; nothing under `src/riftagent` touched.

> The frozen RIFT runtime and six-case corpus remain unchanged. This pass closes
> only paid-run harness gaps: all-case preflight before spend, configured-model
> precheck, deterministic Arm-B seeds, Git-authoritative protected-path
> detection, durable request-start state, full failure-signature enforcement,
> authoritative usage metrics, and independent truth scoring for secondary A/B/C
> correctness metrics.

### 1 — The whole corpus is proven before the first request

The separate preflight was green, but `run` did not require it. Discovering that
case 5 is invalid after paying for cases 1–4 is not a preflight, it is a receipt.

`run` now performs, in order: identity checks, configured-model check, then the
**complete six-case preflight** — every baseline reconstructed, tree hash
matched, frozen failure identity matched, target failing, complete preservation
set passing. Any failure stops the whole benchmark with zero provider calls, and
a test asserts the adapter is never reached when a case fails.

### 2 — Configured model is checked before spending, not only after

`RIFT_LLM_MODEL` must equal the manifest's requested model **before** any
request. Catching a wrong model only from the response evidence means catching it
with the money already gone. Both checks now exist and neither replaces the
other:

```
pre-spend      configured model == manifest model
post-response  provider-reported model == manifest model   (every response,
                                                            schema repair included)
```

### 3 — Arm B's probe seed was not reproducible

The seed came from `hash(case_id)`. Python randomises `hash()` per process, so
arm B would have drawn a different probe in the paid run than in any replay of
it — the arm would not have been reproducible **from its own manifest**, and
nothing in the record would have shown why.

Seeds are now frozen in the manifest, derived once from `SHA256(case_id)`:

```
cachetools 69587 · click 97363 · croniter 50993
icalendar   2358 · structlog 90962 · tenacity 23409
```

`validate_manifest` requires the field; a test asserts identical values from two
fresh interpreters.

### 4 — Protected-path detection asks git, not the patch header

The oracle parsed `+++ b/<path>` lines. That sees what a patch *claims* to write,
so a deletion, a rename or a mode change could leave a protected file altered
without the parser noticing.

`changed_paths_from_git` now reads `git status --porcelain` in the applied
worktree — the repository's own answer — covering modification, deletion, rename
(both names) and untracked additions. Regressions cover modify, delete and rename
of a protected runner-config file, and that a source-only candidate is still
allowed.

### 5 — A crash can no longer silently double-spend

The previous claim rested on skipping completed rows, which cannot distinguish
"never asked" from "asked, outcome unknown".

An append-only arm-state log outside the disposable worktrees now records
`request_started` **before** the adapter is reached:

```
not_started -> request_started -> completed | blocked
```

On restart, `completed` is skipped, `blocked` is not automatically retried, and
`request_started` without settlement **halts the run for reconciliation** rather
than re-sending a request that may already have been paid for. A test proves the
state is durable at the moment of the adapter call.

### 6 — The paid arm enforces the complete failure identity

`--expect-signature` received only the exception type, which would accept a
different failure of the same class. It now receives the full frozen identity, so
curation's observer, preflight's comparison and the paid arm all use one
vocabulary.

### 7 — Usage and command counts come from the ledger

The runner read a receipt field that does not exist, leaving `input_tokens` and
`output_tokens` null. They are now summed from every `MODEL_RESPONSE_RECEIVED` in
the task ledger — schema-repair responses included — alongside `request_count`
and `commands` from `command_finished` events. Missing usage is recorded as
**unavailable**, never as zero: zero reads as "free", which is a different claim
from "unmeasured".

### 8 — Every arm's candidate is scored by the independent oracle

Truth previously ran only for arm A. Reporting secondary "A/B/C false-fix
acceptance" on protocol verdicts would have meant *"C accepted it"* being the
evidence that C was right — a number that can only agree with itself.

B and C candidates now get a fresh identity-checked baseline and the same
independent oracle, with `truth_candidate_hash == canonical_candidate_hash`
enforced for every arm and the full four-way binding for arm A.

### 9 — Aggregation refuses mixed evidence

Before any summary, runtime, driver, oracle and manifest identities are
re-verified, and every record must carry the same five identities as the manifest
being scored. Mismatch yields **NO FINAL SCORE** rather than an average across two
harnesses.

### The full fake run, through the real command

`python benchmark/bm07/bm07_runner.py run --adapter fake` against the frozen
manifest, with a scripted OpenAI-compatible provider on loopback:

```
preflight        6 cases reconstructed, 0 failures   (before request 1)
reservation      $0.4800 per case-arm, derived from the manifest
arm records      18
classifications  both_correct_accept 2, both_reject 2
truth by arm     A correct 2 / wrong 2 · B correct 3 / wrong 2 · C correct 3 / wrong 2
crash/resume     additional provider requests 0, run halted for reconciliation
real provider calls  0
real spend           $0.00
```

Arm-A same-candidate comparisons with identical bytes across weak, strong and
truth: **4 of 6** — the other two are the abstention and repair scripts, which
correctly produce no candidate to compare.

### Unchanged

```
RIFT runtime        unchanged; runtime_hash identical before and after
six-case corpus     unchanged; same targets, preservation sets, parents
canonicalizer       unchanged
diagnosis ontology  unchanged
repair semantics    unchanged; one schema repair, no semantic or application retry
```

`strong_false_rejection` remains **not demonstrated model-free** and was not
manufactured; the classifier supports it and a real run may produce it.

### Frozen identities

```
runtime_hash    75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26  (unchanged)
driver_hash     43fe46dc14a7c3316d7379bdd70ec9e356613ddf7851e32f8a99365eaacaf025
oracle_hash     4829ceb823a30e1094b09de90322b2242b8b3dd0af7b2168946e1bb34bd3431c
manifest_hash   3dc7a3ed6a1668facabd9d7ef4aa88c4c21ba200f02bd6b719c618b38e8a0ae9
```

### Gates

| | |
|---|---|
| identity chain | runtime **UNCHANGED**, driver **MATCH**, oracle **MATCH**, manifest **MATCH** |
| `validate_manifest` | **0 failures** |
| six-case preflight inside `run` | **0 failures** |
| full fake 18-arm run + crash/resume | green, 0 extra requests on resume |
| ruff check / format / mypy | clean / 99 files / 8 source files |
| BM-07 suites | **112 passed** |
| full suite | **1010 passed, 5 skipped, 1 failed** |
| provider calls | **0** |
| additional spend | **$0.00** |

The single full-suite failure was `Cannot allocate memory` while copying the repo
tree, caused by **67 MB of stale `benchmark/work/staging/` directories left
behind by an earlier verify-bench run of mine**. Those artifacts were removed and
the test passes 4/4. It was a housekeeping failure in the working tree, not a
product or harness defect, and `runtime_hash` was unchanged throughout.

**READY_FOR_PAID_BM07_REVIEW** — the exact paid path is frozen and green. No paid
run authorized or started.

---

## DAR-040a — BM-07 harness transaction integrity: global reconciliation stop, evidence before terminal state, exact 18/18 scoring

**Status: IMPLEMENTED.** Recorded as a **transaction-integrity correction to the
BM-07 harness**, not a design amendment: nothing in the product, the protocol or
the corpus changes. 0 provider calls; additional spend **$0.00**. BM-07 was not
executed.

```
RIFT runtime         unchanged   (runtime_hash identical before and after)
six-case corpus      unchanged
scientific protocol  unchanged   (arms, targets, preservation sets, seeds, metrics)
oracle               unchanged   (oracle_hash identical)
provider semantics   unchanged
repair semantics     unchanged
canonicalizer        unchanged
```

Three defects, one shape: each was a place where the harness was **confident
about something it had no evidence for**.

### 1 — An unreconciled paid request now halts the entire benchmark

`request_started` with no terminal state means a provider request may already
have been sent and may already have been paid for. Nothing on disk can say
which.

The runner already refused to re-run *that* arm. It then continued with every
later case and arm, reporting the reconciliation need at the end. That is the
wrong shape: it adds fresh, certain spend on top of prior spend of unknown size,
and it does so before the operator has seen the warning.

The scan now runs at startup — before preflight, before the loop, before
anything that can reach an adapter — over **every** case-arm in the state log,
including arms the invocation was not asked to run, so `--arms A` cannot tiptoe
past an unsettled request in arm C:

```
scan durable arm states
        |
   any request_started without settlement?
        |
       yes -> BLOCKED_FOR_RECONCILIATION, exit 2
               provider calls in this invocation: 0
```

Nothing is reconciled automatically, nothing is retried, and no recovery engine
was added. A terminal `blocked` state — an arm whose response was already
received and paid for — stays terminal and is never automatically re-spent.

### 2 — Complete result evidence is durable before an arm is marked terminal

The order was: evaluate, mark `completed`, then append the result record. A
crash in that window leaves an arm marked terminal whose benchmark evidence
never landed — and a restart skips it, silently and permanently, because the
state says it is done.

`run_case_arm` no longer writes a terminal state at all; a structural test
asserts the only state it writes is `REQUEST_STARTED`. The caller now does:

```
request_started         (durable, before the adapter)
    -> provider request / response
    -> candidate evaluation
    -> complete result record appended, flushed, fsynced
    -> terminal state: completed | blocked
```

The reversed order makes the crash window fail closed instead of open: the
result exists, the state still says `request_started`, and the run stops for
reconciliation — which is now *possible*, because the evidence is there.

An arm that never reached the adapter — a budget skip, a pre-request baseline
mismatch — is deliberately left with **no** terminal state. It spent nothing, so
sealing it would forfeit a re-run for no safety gain.

### 3 — Official scoring requires exactly the frozen 18 case-arm records

Official BM-07 is six cases by three arms. Aggregation checked identity but not
completeness, so a damaged evidence set could still produce a headline number.

`expected_pairs()` derives the official set from the manifest itself, and
`official_status()` compares it to what is actually on disk. Refused:

```
missing arm record                     -> INCOMPLETE_RUN
duplicate record for one case-arm      -> INCOMPLETE_RUN
terminal state with no result record   -> INCOMPLETE_RUN
result with no terminal state          -> INCOMPLETE_RUN
status incompatible with its state     -> INCOMPLETE_RUN
unreadable result row                  -> INCOMPLETE_RUN
result for a case not in the manifest  -> INVALID_RUN
result for an arm outside A/B/C        -> INVALID_RUN
```

Eighteen rows is not eighteen case-arms: a duplicate standing in for a gap keeps
the count right and is still refused.

`--arms A` remains available for development and reports
**DEVELOPMENT_PARTIAL_RUN — 6 of 18 official records, NO OFFICIAL SCORE**. That
label excuses only arms never requested; a restricted run missing a *requested*
arm is `INCOMPLETE_RUN`, not partial.

Completeness is **additional to** identity, never a replacement: the existing
runtime/driver/oracle/manifest drift refusal runs first and still yields NO
FINAL SCORE on its own.

### The fake 18-arm run, through the real command

Loopback provider, real adapter, real ledger, real candidate pipeline:

```
full run         18 of 18 official case-arm records -> OFFICIAL_COMPLETE, exit 0
classifications  both_correct_accept 2, both_reject 2
truth by arm     A correct 2 / wrong 2 · B correct 3 / wrong 2 · C correct 3 / wrong 2

scenario A       request started, no result   -> BLOCKED_FOR_RECONCILIATION, 0 additional requests
scenario B       result durable, no terminal  -> BLOCKED_FOR_RECONCILIATION, 0 duplicate requests
scenario C       17 of 18 records             -> INCOMPLETE_RUN, NO OFFICIAL SCORE

real provider calls  0
real spend           $0.00
```

The classification and truth figures are identical to the run frozen before this
pass, which is the point: the harness got safer and the science did not move.

### Frozen identities — all four unchanged

```
runtime_hash    75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26
driver_hash     43fe46dc14a7c3316d7379bdd70ec9e356613ddf7851e32f8a99365eaacaf025
oracle_hash     4829ceb823a30e1094b09de90322b2242b8b3dd0af7b2168946e1bb34bd3431c
manifest_hash   3dc7a3ed6a1668facabd9d7ef4aa88c4c21ba200f02bd6b719c618b38e8a0ae9
```

`driver_hash` and `oracle_hash` are each a file's own bytes; neither file was
touched, so no manifest rebuild was required and the recorded `manifest_hash`
still recomputes.

### Gates

| | |
|---|---|
| identity chain | runtime, driver, oracle, manifest — all four **UNCHANGED** |
| `validate_manifest` | **0 failures** |
| six-case preflight inside `run` | **0 failures** |
| fake 18-arm run | **OFFICIAL_COMPLETE**, exit 0 |
| restart scenarios A / B / C | all refuse, **0 additional provider requests** |
| ruff check / format / mypy | clean / 100 files / 8 source files |
| BM-07 suites | **142 passed** (112 + 30 new transaction tests) |
| full suite | **1041 passed, 5 skipped, 0 failed** (17m58s) |
| runtime | **10,385 / 10,400** — unchanged |
| provider calls | **0** |
| additional spend | **$0.00** |

`strong_false_rejection` remains **not demonstrated model-free** and was not
manufactured.

**READY_FOR_PAID_BM07_REVIEW** — no paid run authorized or started.

---

## DAR-040b — BM-07 runner identity: the program that spends the money is frozen too

**Status: IMPLEMENTED.** Recorded as a **BM-07 benchmark-infrastructure
correction**, not a design amendment. 0 provider calls; additional spend
**$0.00**. BM-07 was not executed.

> The BM-07 transaction harness was correct, but the actual paid orchestration
> program `bm07_runner.py` was not bound by the frozen identity chain.
> `runner_hash` now freezes the exact orchestration bytes and is enforced before
> provider spend, carried in every arm result, and rechecked before official
> scoring.

```
RIFT runtime         unchanged   (runtime_hash identical before and after)
corpus               unchanged   (6 cases x 3 arms, same targets, preservation
                                  sets, baselines, failure identities, seeds)
driver semantics     unchanged   (driver_hash is still bm07_driver.py bytes)
oracle               unchanged   (oracle_hash identical)
transaction semantics unchanged  (global halt, result-before-terminal, 18/18)
scientific protocol  unchanged   (arms, metrics, same-candidate experiment)
```

### The gap

Four components were identity-bound: the runtime, the evaluator, the oracle and
the experiment declaration. The orchestration program was not — and it is the
code that decides *when a provider call happens*, how a restart behaves, how
transaction state is handled, and when final aggregation is allowed.

So `bm07_runner.py` was the one file whose bytes could change without any
recorded identity changing. A modified runner could re-send a request a frozen
one would have refused, or score a set a frozen one would have rejected, and
every hash in the record would still match.

### `runner_hash`

```
runner_hash = SHA256(exact bytes of benchmark/bm07/bm07_runner.py)
```

Exact file bytes — not normalised source, not derived from imports. What runs is
what is hashed.

Deliberately **not** merged into `driver_hash` and **not** generalised into a
dependency-hash framework. Evaluating a candidate and deciding to spend money are
different authorities that fail differently; keeping them separate is what makes
a mismatch say *which* component moved. No other benchmark script is hashed: the
specific gap was that the paid orchestration path was mutable, and that is what
is fixed.

```
runtime_hash   frozen RIFT product
driver_hash    bm07_driver.py
runner_hash    bm07_runner.py
oracle_hash    bm07_oracle.py
manifest_hash  the executable experiment declaration
```

### Enforced in three places, because one is not enough

**Before any spend.** `runner_hash` joins the pre-request identity check
alongside runtime, driver, oracle, manifest, configured model and the six-case
preflight. A mismatch stops the entire run with **0 provider calls** — before the
reconciliation scan and before preflight touches a repository. This is a runtime
check against the observed bytes, not merely manifest validation.

**In every arm record.** All eighteen official records carry the same five
identities; aggregation requires every row to agree. Seventeen from the frozen
runner and one from another is refused even though every other hash matches, and
so is a record with no `runner_hash` at all.

**Before official scoring.** The observed runner is recompared with the frozen
manifest, so a runner swapped between execution and aggregation yields **NO FINAL
SCORE** rather than a number computed under a different orchestration program.

### Manifest schema

`runner_hash` is now a required top-level field. Missing, wrong-length, non-hex
and uppercase values all fail validation. Only `runner_hash` is format-checked:
the older identity fields predate the rule and their fixtures are frozen
evidence, so tightening them now would rewrite records rather than validate them.

### Durable state

The state machine is unchanged. `write_state` now stamps each row with the
runner that wrote it — evidence for whoever reconciles an unsettled request, not
a new transition — and rows written before the field existed still replay. A
restart under a different runner is already refused by the run-level identity
check, which runs before the reconciliation scan and long before any request.

### Fake run, real command, loopback provider

```
full run     18 of 18 official case-arm records -> OFFICIAL_COMPLETE, exit 0
             both_correct_accept 2, both_reject 2
             truth  A 2/2 · B 3/2 · C 3/2        (unchanged across three passes)
             all 18 records share one runner_hash

scenario A   request_started, no result   -> BLOCKED_FOR_RECONCILIATION, +0 requests
scenario B   result durable, no terminal  -> BLOCKED_FOR_RECONCILIATION, +0 requests
scenario C   17 of 18 records             -> INCOMPLETE_RUN, NO OFFICIAL SCORE
scenario D   runner identity mismatch     -> refused, provider requests = 0
scenario E   17 frozen + 1 foreign runner -> NO FINAL SCORE

real provider calls  0
real spend           $0.00
```

### Frozen identity chain, computed in dependency order

```
runtime_hash    75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26   unchanged
driver_hash     e2154631641a6e9b3fb4bfcbcd36a66d5440a4a2416631af57ed448eceb6ebfd   changed
runner_hash     d1b2fd312d2eacd7436f7401981c302bc2fb025f9bb675680ea15ab93212da48   new
oracle_hash     4829ceb823a30e1094b09de90322b2242b8b3dd0af7b2168946e1bb34bd3431c   unchanged
manifest_hash   183f9e6d731f513d9f60ef296372ca040d8bf562cff224e4026efae5fc5061fc   changed
```

`driver_hash` moved because `bm07_driver.py` had to require and format-check the
new field; its **definition is unchanged** — still that file's own bytes. The
manifest was rewritten surgically rather than rebuilt, and the freeze script
asserts that no non-identity key moved, so the scientific declaration is provably
untouched.

### Gates

| | |
|---|---|
| identity chain | runtime **UNCHANGED**, oracle **UNCHANGED**, driver/runner/manifest **REFROZEN** |
| `validate_manifest` | **0 failures** |
| six-case preflight inside `run` | **0 failures** |
| fake 18-arm run | **OFFICIAL_COMPLETE**, exit 0, one runner_hash across all 18 |
| scenarios A-E | all refuse, **0 additional provider requests** |
| ruff check / format / mypy | clean / 101 files / 8 source files |
| BM-07 suites | **165 passed** (142 + 23 runner-identity tests) |
| full suite | **1064 passed, 5 skipped, 0 failed** (15m31s) |
| runtime | **10,385 / 10,400** — unchanged |
| provider calls | **0** |
| additional spend | **$0.00** |

`strong_false_rejection` remains **not demonstrated model-free** and was not
manufactured.

**READY_FOR_PAID_BM07_REVIEW** — no paid run authorized or started.
