# BM-06 — the M1 fix benchmark protocol

Frozen before any arm runs. Nothing in this document may be chosen, revised or
reinterpreted after seeing a result; a change here invalidates every run made
under the previous version and the manifest hash changes with it.

`BM-06` is required for the **M1 expansion claim** and for nothing else. M1
itself is approved without it. This protocol governs `riftagent_design_v1.2.4.md`
§15 "Fix benchmark" and acceptance rows BM-01 … BM-06.

**No benchmark request has been made. No spending is authorized by this
document.** It exists so that the authorization decision is made against a
protocol that already exists, rather than one written afterwards.

---

## 1. What is being measured

The product thesis is that a deterministic acceptance kernel reduces false fixes
without giving up correct ones. The benchmark's job is to make that falsifiable.

BM-06 passes only if arm **C**:

1. **lowers false-fix acceptance** against arm A, and
2. **retains ≥ 90% of A's correct-fix yield**, and
3. **lowers token cost per correct fix**.

All three. A run that lowers false-fix acceptance by abstaining on everything
fails (2). A run that matches A's yield at ten times the cost fails (3).

### The measurement this benchmark cannot make

The shipped `fix` is **single-attempt** (DAR-010): a candidate that fails the
gate behaviourally is rejected with no second proposal. BM-06 therefore measures
the product as it exists, which is the point of running it before implementing
the repair loop. If candidate behavioural failure turns out to be a material
share of arm C's lost yield, that is the evidence that would justify the loop —
and it is evidence this protocol is designed to produce rather than assume.

`failed_phase` is recorded per case for exactly that reason.

---

## 2. Arms

One independent variable per comparison. Every arm sees the **same** cases, the
same repository states, the same model, and the same per-request caps.

| Arm | What it is | Acceptance rule |
|---|---|---|
| **A** | the model alone | the model's patch is accepted if the target test passes after applying it |
| **B** | model + ledger + **random** probe selection, frozen seed | the same counterfactual gate as C |
| **C** | the full kernel: disagreement-per-cost probe selection, frozen reproducer, counterfactual gate | `verified_against_approved_checks` and nothing else |

### What each comparison can and cannot claim

**A-versus-C measures the complete end-to-end product effect, not acceptance
authority.** Arm C differs from arm A in diagnosis, probe selection, context,
proposal basis *and* acceptance. Its result is a compound of all of them, and
attributing it to the gate alone would be a causal claim this design cannot
support. Any reported difference is stated as the RIFT product effect.

Acceptance authority in isolation is measured elsewhere and is **not** given
another arm here:

- **M1a** already isolated it: `benchmark/frozen/` runs the identical patch
  through the standard protocol and through the counterfactual gate, with the
  proposer held constant because the patches are pre-existing.
- **Shadow evaluation** inside BM-06, where it can be computed: take the exact
  candidate patch arm A accepted and evaluate it under C's gate without
  re-proposing. Same patch, same repository state, two acceptance rules. This is
  a scoring step over recorded artifacts, not a fourth arm, and it costs no
  additional model request.

**B-versus-C isolates probe selection.** Both arms draw from the identical
candidate probe **pool** (`kernel.generate_probes`) under identical command,
token, wall-clock and attempt budgets. They do **not** execute an identical
ordered list — if they did, selection would not be under test. The only
difference is the policy that picks the next probe from the pool: B chooses
randomly from a **frozen seed** recorded in the manifest, C chooses by
disagreement per estimated cost.

The frozen seed is what makes B reproducible. Without it, a rerun of B is a
different experiment and the comparison is not repeatable.

Arm A is the incumbent practice being tested against, not a straw man: it gets
the same model, the same bounded context selection, and the same number of
proposal attempts.

---

## 3. Case labels — frozen before any arm runs (BM-01)

Every case carries a label assigned by review **before** any arm runs. The agent
never chooses its own class or denominator (BM-02).

| Label | Meaning | Scored in |
|---|---|---|
| `gateable` | a safe apply/withdraw intervention exists, so a patch can be counterfactually gated | verified-fix yield (BM-03) |
| `observationally_diagnosable` | an executable assertion can support a finding, but nothing can be applied and withdrawn | observational diagnosis yield (BM-04) |
| `neither` | out of the action space entirely | useful-outcome yield only |

**`gate: not_applicable` earns no fix credit, in any arm** (BM-04). An
observational finding is a diagnosis; counting it as a fix is the exact
overclaim the verdict vocabulary exists to prevent.

### Cause classes

Each case also carries a cause class, from §15:

`state_leakage`, `order_dependence`, `missing_dependency`, `version_mismatch`,
`locale_timezone`, `nondeterminism`, `two_cause`, and the negative class
`genuine_source_bug`.

The negative class is not padding — it measures **false attribution**: a
genuine source bug must not be reported as an environmental cause, and an
environmental cause must not be reported as a source defect. A case set without
it can only measure one direction of error.

**Cause class is frozen separately from gateability, and the two must not be
conflated.** A `genuine_source_bug` is routinely **`gateable`** — a patch exists,
it can be applied and withdrawn, and the counterfactual runs — while its
*deterministic diagnosis* is expected to be `representation_inadequate`, because
no handle in the action space varies the outcome. That combination is the
correct, expected result for the class, not a failure: the case is scored on
whether the gate accepted a ground-truth-correct patch, and the diagnosis is
scored against its own expected scope.

Each case therefore also freezes an **expected diagnostic scope** and a
**per-branch tag**, `cause_supported` or `diagnosis_unresolved`, so a repair made
on the weaker basis (DAR-001) is scored as such rather than silently credited to
a located cause.

### `GROUND_TRUTH_INVALID` (DAR-003)

A case whose predeclared label does not survive scrutiny is marked
`GROUND_TRUTH_INVALID`, **excluded from scoring**, disclosed by name with its
invalidation reason, and its spend reported separately — never pooled with
valid-case spend. Neither "correct" nor "incorrect" is a truthful score for a
mislabelled case, and scoring it either way corrupts the denominator.

### Adversarial pre-review (DAR-006)

A case intended to be **unfixable** must be **structurally** unsatisfiable — a
change check requiring `f(2) == 5` against a frozen preservation check requiring
`f(2) == 4`. A contradiction expressible inside a single assertion is satisfiable
by a pathological return value; C5 was labelled unsatisfiable and was not,
because a class whose `__gt__` and `__lt__` both return `True` satisfies
`v > 0 and v < 0`.

Every intended-unfixable case is adversarially reviewed before it is run, and its
label predeclared. This is a precondition of freezing the manifest, not a
post-hoc check.

### What every case freezes

A case is not frozen until all of these are recorded:

| Field | Why it must be frozen |
|---|---|
| repository, **resolved ref and commit** | a branch name is not a state; the resolved sha is |
| runner / instrument hash | the judge must be the same instrument in every arm |
| exact reproduction contract and **target-specific signature** | a failure's identity, not merely its existence |
| **cause class**, separately from gateability | see the note above — they are independent axes |
| preservation checks | an empty preservation set passes vacuously |
| reviewed **known-correct patch**, for `fixable` / `gateable` labels | ground truth by demonstration |
| reviewed **structural argument**, for `unfixable` labels | ground truth by proof (DAR-006) |
| `GROUND_TRUTH_DISPUTED` | when **neither** proof exists — the label is withheld, not guessed |
| expected diagnostic scope | what the diagnosis may legitimately claim |
| per-branch tag: `cause_supported` \| `diagnosis_unresolved` | which basis a repair was made on (DAR-001) |

`GROUND_TRUTH_DISPUTED` is a first-class outcome, not an admission of defeat.
Calibration case C4 was scored an abstention against a label that turned out to
describe a limitation of the bare-target gate rather than task truth; recording
the dispute is what let that be corrected instead of quietly counted.

### Composition requirements

30 real cases, from **at least five** unrelated repositories, covering **all
eight** cause classes. **At least four natural order-dependent cases across at
least two repositories** — that class is the one the product exists for, and one
repository's idiosyncrasy is not evidence about the class.

**No synthetic substitution.** A constructed fixture may not stand in for a
natural case in any class. Synthetic fixtures belong in the test suite, where
they already are; a benchmark built on them measures the fixture author.

---

## 4. Metrics

Co-primary, reported together, never one without the other:

- **False-fix acceptance** — accepted patches that are not ground-truth correct,
  over all accepted patches.
- **Verified-fix yield** (BM-03) — ground-truth-correct, gate-passed fixes
  divided by **all attempted frozen-`gateable` tasks**. Abstentions and failures
  stay in the denominator.

**Abstentions remain attempted tasks.** An abstention is an outcome, not a
withdrawal from the sample; it stays in every denominator it entered.

**`gate: not_applicable` receives diagnosis credit where warranted, and never
verified-fix credit** (BM-04). The two ledgers are separate and neither borrows
from the other.

**Zero correct fixes makes cost per correct fix undefined or infinite — never
zero** (BM-05). A run that fixes nothing has not achieved perfect efficiency.

Also reported, each separately:

- observational diagnosis yield, on its frozen class only (BM-04);
- overall useful-outcome yield across every attempted task, so class
  partitioning cannot hide low product value;
- abstention calibration — how often an abstention was the right answer;
- commands, wall time, provider-reported tokens;
- **total cost per correct verified fix** (BM-05), including the cost of
  abstained and failed attempts. Zero correct outcomes is infinite or undefined,
  **never zero**.

Every rate is recomputed from the raw per-case records at report time. A number
that cannot be recomputed from `results.json` is not reported.

---

## 5. Model configuration — frozen

| Field | Frozen value |
|---|---|
| provider interface | OpenAI-compatible chat completions (`llm.post_chat`), no streaming, no tool calling |
| endpoint | `RIFT_LLM_URL`, HTTPS enforced by `ProviderConfig.from_env`; recorded in the manifest at freeze time |
| model id | **`claude-sonnet-5`** — the complete identifier. There is no dated snapshot variant for this model; a date suffix would 404 |
| snapshot evidence | the `/v1/models/claude-sonnet-5` response recorded **immediately before execution**, carrying `id`, `display_name`, `max_input_tokens`, `max_tokens` |
| temperature | **see the blocking precondition below** |
| `--max-output-tokens` | 4000 |
| `--max-probes` | 16 |
| `--max-attempts` | 1 (the shipped single-attempt behaviour, DAR-010) |
| `--max-commands` | 400 |
| `--timeout` | 600s |
| price input | $3.00 / MTok list · $2.00 / MTok introductory through **2026-08-31** |
| price output | $15.00 / MTok list · $10.00 / MTok introductory through **2026-08-31** |
| pricing verified | 2026-08-17, against the published Anthropic pricing table |

The model is identical across A, B and C. Varying it would make the arms
incomparable — the independent variable is the kernel, not the model.

Pricing is **configured, not fetched**. A price discovered at run time is a price
that can change between the reservation and the charge, and the reservation is
what bounds the run.

### Two preconditions, both checked immediately before execution

**1. `temperature` — blocking, and not yet resolved.** `llm.post_chat` defaults
to `temperature=0.0` and sends it on every request. `claude-sonnet-5` **rejects
non-default sampling parameters with a 400** — the default is not 0.0, so a
literal pass-through fails. Whether the OpenAI-compatible endpoint forwards,
remaps or drops the field is unverified, and verifying it requires a provider
request, which is not authorized.

Left unresolved, this fails every task in all three arms and consumes the
authorization on 90 rejections. The runtime change is one line — pass
`temperature=None` from the benchmark harness so the field is omitted entirely —
but it is a change to the **approved M1 tree** and is therefore a reviewer
decision, not something taken here. Recorded rather than fixed.

**2. Snapshot and price confirmation.** Both are re-confirmed immediately before
execution and recorded with the run. **If either cannot be confirmed, the run
does not start and nothing is spent.** The introductory price expires
**2026-08-31**; a run after that date costs list, and the difference is
$10.24 → $15.35 worst case for the same work.

---

## 6. Worst-case budget

Computed from the runtime's own reservation arithmetic — `token_ceiling` at
`chars/3 + 1500`, `reserve_cost` at `(input_ceiling × price_in + max_output ×
price_out) / 1e6` — not from an average of past runs. This is the ceiling the
`SpendLedger` would reserve, and it is what an authorization must cover.

Per-operation worst case, at the frozen caps:

| Operation | input ceiling | max output |
|---|---|---|
| `propose_handles` | 3,633 | 800 |
| `propose_hypotheses` | 4,166 | 1,600 |
| `propose_change` | 23,666 | 4,000 |

`propose_change` dominates because it carries the bounded source context:
`MAX_CONTEXT_CHARS` 60,000 plus a 6,000-character failure excerpt.

Worst case for **30 cases across three arms**, assuming every task makes every
optional request:

| Model | per C task | per A task | 30 × C | 30 × B | 30 × A | **total** |
|---|---|---|---|---|---|---|
| `claude-haiku-4-5` ($1 / $5) | $0.0635 | $0.0437 | $1.90 | $1.90 | $1.31 | **$5.12** |
| `claude-sonnet-5` intro ($2 / $10) | $0.1269 | $0.0873 | $3.81 | $3.81 | $2.62 | **$10.24** |
| `claude-sonnet-5` list ($3 / $15) | $0.1904 | $0.1310 | $5.71 | $5.71 | $3.93 | **$15.35** |
| `claude-opus-5` ($5 / $25) | $0.3173 | $0.2183 | $9.52 | $9.52 | $6.55 | **$25.59** |

Rates verified against the published Anthropic pricing table, not recalled. The
`claude-haiku-4-5` row matches the runtime's configured defaults exactly, which
is why the historical `$0.068157` is arithmetically sound.

**Actual spend will be far below these figures.** The historical 27 requests
averaged ≈ $0.0025 each against a worst case of $0.0437–$0.0635 per task; the
reservation assumes every prompt fills its context cap and every response fills
its output cap. The ceiling is what gets authorized; the ledger records what was
charged.

Add a **contingency of one full re-run** for a run invalidated by a harness
defect — this has already happened once, in the M1a verify benchmark, where
`derive_judge_weakening` built its diff against the index and all four
judge-weakening cases were malformed. Doubling the figure is the honest
authorization ask.

### Scope enforcement

One frozen `--scope` for the whole run (DAR-005), so all 90 tasks draw on a
single cumulative authorization rather than 90 individually affordable ones.
`--max-usd` is set to the authorized figure. A refusal is recorded and the task
abstains; it never silently proceeds.

---

## 7. What is frozen here, and what is not

**Frozen by this document:** the arms, their acceptance rules and what each
comparison may claim; the per-arm protocols and the frozen random seed for B; the
common external scoring procedure; the label taxonomy and the independence of
cause class from gateability; the per-case freeze fields; the composition
requirements; the metric definitions, denominators and co-primary reporting rule;
the exclusion rule and the adversarial-review precondition; the model
configuration and its two pre-execution checks; and the budget arithmetic.

**Not frozen, and blocking a run:**

1. **The `temperature` precondition** (§5). A one-line change to the approved M1
   tree, and therefore a reviewer decision. Unresolved, it fails all 90 tasks.
2. **The case set.** 30 natural cases, ≥ 5 repositories, all eight cause classes,
   ≥ 4 natural order-dependent cases across ≥ 2 repositories.

The case set cannot be honestly frozen by assertion. Building it requires
cloning pinned repositories, locating real failures whose fix is the project's
own commit, resolving each to a commit sha, classifying each by cause, recording
a reproduction contract and signature, and reviewing every intended-unfixable
case adversarially — none of which spends a model request, and all of which must
happen before an arm runs.

The M1a harness (`benchmark/verify_bench.py`) already discovers "test fails at
parent commit, passes at fix commit" across six repositories, which is the same
primitive. What it does not do is classify by cause class, and the classes this
protocol needs — order dependence, state leakage, locale, nondeterminism — are
not what a generic bug-fix scan surfaces: they are found by searching commit
history for isolation and flakiness fixes, then confirming the failure
reproduces at the parent. That is the gap between the existing harness and this
manifest, and it is real work rather than a parameter change.

**Until both are settled, BM-06 has not started, and no benchmark request is
authorized.**
