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
| `riftagent_design_v1.2.3.md` | superseded for the amended clauses below; otherwise current; byte-identical |
| `DESIGN_AMENDMENT_RECORD.md` (this file) | current for every clause it records |
| `riftagent_design_v1.2.4.md` | **NOT PRODUCED** — see DAR-007 |

Where this record and v1.2.3 conflict, this record governs, and the conflicting
v1.2.3 clause is named in the entry.

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

**Status: NOT IMPLEMENTED.** `fix` currently proceeds on whatever diagnosis
emerges without distinguishing the two bases, and neither receipt field exists.
Recorded here so the gap is governed rather than undocumented.

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

## DAR-007 — `riftagent_design_v1.2.4.md` not produced

The consolidated v1.2.4 document incorporating DAR-001 … DAR-006, with the C5
Goodhart case as a worked example, was **not produced in this pass**. The
authority index above therefore still points at v1.2.3 plus this record.

This is an open obligation, not a decision. Until it is discharged, a reader
must consult two documents rather than one, and DAR-001 in particular records a
rule that is governed but **not implemented** — the exact class of mismatch
item 2 exists to eliminate.
