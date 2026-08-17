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
