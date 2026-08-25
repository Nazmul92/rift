# Preliminary run aborted — `propose_change` responses are not parseable

Stopped after 6 of 27 arm-runs. **Charged $0.180358** across 5 settled requests,
plus the $0.000072 preflight. No arm produced a patch, and none of the 9 cases
has a result.

## What happened

Every arm-run reaches the same point and stops:

```
spend_reserved         propose_change
model_request_started  claude-sonnet-5
spend_settled          propose_change
model_response_invalid "no JSON object found in the response"
```

Four of the five completed arm-runs recorded that reason verbatim; the fifth is
arm A, which reports the same failure as "no proposal was produced" because it
makes a single request and has nothing to fall back to.

Arms B and C then report "no proposal survived validation; no patch was
produced" and the receipt records `failed_phase: candidate`.

**This is not the model failing the repair task.** It never attempts it. The
request is made, charged, and returns something the adapter cannot extract JSON
from, so no proposal exists to validate, apply or gate.

## Why the preflight did not catch it

The preflight asked for one word of prose and asserted the reply parsed. It
exercised authentication, model acceptance, the OpenAI-compatible request shape,
usage accounting and the response envelope — all of which are fine.

It did **not** exercise the operation the benchmark depends on. `propose_change`
requires a structured JSON reply, and that path was never tested. A preflight
that validates the transport but not the payload contract answers a question
nobody was blocked on.

That is a defect in how I designed the preflight, not in the provider or the
manifest.

## What is not yet known

The ledger deliberately does not record model prose as evidence, so the actual
response text is not durable anywhere. Establishing *why* extraction failed —
markdown fences, a preamble, a refusal, an empty completion, or a genuinely
different response shape from this endpoint — requires observing one real
`propose_change` reply.

Plausible causes, none yet confirmed:

- the reply wraps its JSON in a fenced code block and the extractor is stricter
  than the model's habit;
- the reply prefixes prose before the object;
- the OpenAI-compatible endpoint returns the content in a shape
  `_parse_reply` reads as empty for longer structured outputs;
- the model declines the task as phrased.

Guessing between these and changing `llm.py` on that basis would be the wrong
move: `llm.py` is approved runtime, and a fix aimed at the wrong cause would
look like it worked if it happened to change the failure mode.

## State

- **Spend:** $0.180358 run + $0.000072 preflight. Ceiling $3.43 untouched.
- **Manifest:** unchanged, `bf77869aec94477c8cd9347629a97c9f664e1ad2a9813fbf2edb3eefad3c46c0`.
- **Runtime:** unchanged, 9,150 / 9,156.
- **Cases:** all 9 still verify model-free through the full five-phase gate; the
  corpus is not implicated.
- **Results:** `results.json` was never written — the driver was stopped before
  completion, so there is no partial report to mistake for a result.

## Recommended next step, for a ruling

One targeted preflight of the operation that actually matters: issue a single
real `propose_change` request for one case and record the raw response text to a
non-durable location for inspection, with secrets removed. That is one request
at roughly $0.04 and would identify the cause instead of inviting a guess.

Nothing about arm distinctness, signature propagation, manifest identity or
case validity is in question here. The blocker is one contract between the
adapter and the model's reply format.

---

# Cause established from the existing ledgers — no further request made

The five `model_response_received` events answer this for $0. The targeted
diagnostic request proposed above was **not** made.

| request | finish_reason | output_tokens | response_chars | outcome |
|---|---|---|---|---|
| 1–4 | `length` | **4000** (the exact cap) | **0** | no JSON to extract |
| 5 | `stop` | 341 | 675 | valid patch; gate rejected it at candidate |

Four requests consumed the entire output allowance and emitted nothing visible.
`max_tokens` is shared between thinking and the reply, so a 4,000-token ceiling
can be spent before any JSON appears. The smoke request survived because "reply
with one word" needs almost no thinking; `propose_change` is the opposite.

Two of the four causes listed above are now eliminated, as the reviewer noted:
fenced JSON and a prose preamble would both leave a `{` in the response, and
`response_chars: 0` means there was no visible content at all.

## The fifth request matters more than the four

It completed normally, returned valid JSON, registered a real diff against
`pygments/formatters/other.py`, reproduced the baseline and froze
`TypeError: can only concatenate str (not "bytes") to str`, then ran the
candidate phase, which **rejected the patch**.

So the proposal → validation → changeset → gate pipeline works end to end when
the model emits. That case is a genuine single-attempt miss and a legitimate
data point. The corrected tally for the aborted run is **4 infrastructure
failures, 1 real result, 0 successes** — RIFT's repair thesis remains untested
rather than failed.

## A second, separate contract gap

`llm.ModelResponseInvalid` documents "One repair attempt is permitted, then the
caller abstains", and `CLAUDE.md` requires "allow one schema-repair request;
abstain explicitly if validation still fails". No caller implements it: all
three operations append `MODEL_RESPONSE_INVALID` and return `None`.

**It must not be implemented first.** A repair retry against a response that
returned `finish_reason: length` with zero characters would hit the same ceiling
and fail identically — two charged requests per arm-run instead of one, with the
same outcome. The output allowance has to be resolved before the repair retry is
worth anything. They are separate defects and want separate rulings.

## Cost of the obvious remedy — corrected

An earlier version of this section said 8,000 output tokens takes the ceiling to
"roughly $5.15". That was wrong: it doubled the output allowance of **every**
operation rather than only `propose_change`. Corrected, from the manifest's own
reservation model:

| | output ceiling | per task | 27 arm-runs |
|---|---|---|---|
| current | 800 + 1,600 + 4,000 = 6,400 | $0.12693 | **$3.43** |
| `propose_change` 4,000 → 8,000 | 800 + 1,600 + 8,000 = 10,400 | $0.16693 | **$4.51** |

The delta is 4,000 output tokens at $10/MTok — $0.04 per arm-run, $1.08 across
27. `propose_handles` (800) and `propose_hypotheses` (1,600) are unchanged.

It remains a manifest change and a new authorization, not an adjustment.

## Request 5 is not a benchmark datapoint

The one arm-run that completed produced a valid patch that the candidate gate
rejected — a well-formed single-attempt miss. It is retained as evidence that
the proposal → validation → ChangeSet → gate path works, and it is **not**
counted as a result.

The run was invalidated, and the experimental conditions are about to change:
the output allowance that broke the other four requests is the same parameter
this one ran under. Keeping the single arm that happened to work would be
selecting a datapoint by the outcome of the failure that discarded its
siblings.

Nothing here has been changed: no runtime edit, no manifest edit, no further
provider request. Spend remains **$0.180358 run + $0.000072 preflight**.

---

# The 8,000-token preflight also failed: the cap is not the lever

One request, $0.085096. `benchmark/bm06/preflight-propose-record.json`.

```
max_output_tokens 8000
finish_reason     length
output_tokens     8000   (the exact cap, again)
response_chars    0
result            FAIL
```

Doubling the allowance changed nothing: thinking expanded to fill it and emitted
no visible text. Sol's recommended step 7 — "if 8K works, freeze that cap" —
does not apply, and raising the cap further is a bet that costs more each time it
loses. At 16,000 the same failure would cost $0.17 per arm-run to observe.

## What the compatibility layer actually permits

From the OpenAI SDK compatibility documentation, fetched 2026-08-19:

- **`reasoning_effort`: Ignored.** The OpenAI-shaped lever for this problem does
  nothing on this endpoint.
- **Thinking is on by default on Claude 5 models** through this layer, and the
  layer does not return the thinking content — so the tokens are spent and
  invisible, which is exactly what the ledger shows.
- Thinking *is* controllable, but only by sending a **`thinking` object in the
  request body** — an Anthropic-specific field, not an OpenAI one.
- `response_format`: **Ignored**. Guaranteed JSON needs native Structured
  Outputs, which this endpoint does not provide.
- The documentation states the layer "is primarily intended to test and compare
  model capabilities, and is not considered a long-term or production-ready
  solution for most use cases."

## The conflict this creates, recorded rather than resolved

The only working lever is a provider-specific `thinking` field. Sending it puts
Anthropic-specific knowledge into an adapter whose **provider neutrality is an M1
acceptance property** (M1-S03, `tests/test_adapter_neutrality.py`), and DAR-014
was decided on precisely that ground: the fix for the temperature incompatibility
was to stop asserting a preference nobody expressed, *not* to branch on the
provider.

Four options, none free of consequence:

1. **Send `thinking` through the adapter.** Smallest change, directly addresses
   the cause. Breaks provider neutrality unless it is caller-supplied and absent
   by default — the same shape DAR-014 chose for `temperature`.
2. **Raise the cap further.** No evidence it terminates; 4,000 and 8,000 both
   failed identically, and each attempt costs more.
3. **Change the benchmark's model** to one without default thinking. Changes
   what the benchmark measures and needs a manifest reissue.
4. **Native Anthropic adapter.** The design already anticipates one, and this is
   the second incompatibility the compatibility layer has produced. Far too large
   for this pass.

Option 1 as a caller-supplied parameter defaulting to absent is the closest
analogue to DAR-014's resolution and the smallest change that could work. It is
recorded here as a proposal, not implemented: `llm.py` is approved runtime and
adapter neutrality is an acceptance property, so this needs a ruling.

## Spend

$0.180358 aborted run + $0.000072 transport smoke + $0.085096 propose preflight
= **$0.265526**. No benchmark result exists.

---

# Resolution: a different model, and the repair the contract always required

Two separate defects, fixed separately. **No provider request was made for
either.** Spend is unchanged at **$0.265526**.

## 1. The model

`claude-sonnet-5` → **`claude-sonnet-4-6`**.

Of the four options recorded above, this is option 3. Option 1 — sending a
`thinking` object — was the smallest change that could work and is the one not
taken: it puts Anthropic-specific knowledge into an adapter whose provider
neutrality is an M1 acceptance property, and DAR-014 decided that exact question
the other way for `temperature`. Option 2 has now failed twice at increasing
cost. Option 4 is far too large for this pass.

Changing the model requires no adapter change at all. What it costs is the
manifest: the model is part of the frozen experimental configuration, so the
manifest is reissued and the old SHA is dead.

| | before | after |
|---|---|---|
| model | `claude-sonnet-5` | `claude-sonnet-4-6` |
| rates | $2.00 / $10.00 per MTok | $3.00 / $15.00 per MTok |
| manifest SHA-256 | `bf77869a…3c46c0` | `64aa5f77…b584f19` |
| ceiling, 27 arm-runs | $3.43 | **$6.54** |

Rates re-verified 2026-08-19 against the published pricing table, quoted
verbatim in the manifest. No introductory or promotional rate applies to Sonnet
4.6 — the introductory note on that page concerns Sonnet 5 only, and the
disagreement recorded earlier about whether $2/$10 was permanent is now moot for
this benchmark.

Cases, labels, targets, commits, signatures, preservation checks, arms, seeds,
the protocol hash and the claim limit are byte-identical; the reissue script
asserts that after writing.

**This changes what the benchmark measures.** It is a different model, so no
figure produced under it is comparable to anything produced under Sonnet 5 —
which is moot, because nothing was.

## 2. The repair

Implemented, with the distinction this document argued for. `DAR-020` carries
the derivation; in short:

- a **completed** reply that will not parse gets exactly **one** repair request,
  asking for the same proposal re-serialised and explicitly forbidding a fresh
  diagnosis or a different fix. Valid → the ordinary ChangeSet flow continues.
  Invalid again → abstain.
- a reply with `finish_reason: length` or no visible text gets **no** repair.
  The failure is recorded with `finish_reason`, `response_chars` and
  `output_exhausted` so a ledger reader can tell the two apart, which was not
  previously possible. This is the case that broke the run; paying twice for it
  was the objection to implementing the repair at all, and it is the case now
  excluded by construction.

The repair is reserved and settled as its own request. A refused reservation
abstains before the request rather than after.

Nine regression tests, `tests/test_json_repair_retry.py`, all passing: no repair
on a valid reply; exactly one on a malformed completed reply; continuation into
`changeset_registered` when the repair parses; abstention with
`repair_exhausted` when it does not; no repair for truncation, with or without
partial visible text; the reservation and settlement pair; a refused reservation;
and adapter neutrality asserted on the wire request — body keys and headers —
rather than by grepping source, so a comment naming a vendor neither passes nor
fails it.

## Ceiling arithmetic

Per-operation reservations are unchanged except for the new one:

| operation | input ceiling | max output |
|---|---|---|
| `propose_handles` | 3,633 | 800 |
| `propose_hypotheses` | 4,166 | 1,600 |
| `propose_change` | 23,666 | 4,000 |
| `propose_change_repair` | 3,864 | 4,000 |

At $3 / $15, assuming **every** arm-run needs its repair:

| | per arm-run | ×9 cases |
|---|---|---|
| arm A (`propose_change` + repair) | $0.20259 | $1.823 |
| arms B and C (all four operations) | $0.261987 | $4.716 |
| **27 arm-runs** | | **$6.539076 → ceiling $6.54** |

Of the increase from $3.43, roughly $1.18 is the rate change and roughly $1.93
is the repair allowance. The repair portion is a worst case that no arm-run may
actually spend.

The input ceilings are carried over unchanged, and the pricing page records that
Sonnet 4.6 uses the older tokenizer while 4.7-and-later produce about 30% more
tokens for the same text. The ceilings are therefore, if anything, over-estimates
for this model. Over-estimating a reservation is the safe direction.

## What is still open

- **Whether Sonnet 4.6 actually returns a parseable proposal is unverified.**
  The change is reasoned from the compatibility documentation — thinking is
  documented as on by default for Claude *5* models — not from an observation.
  One `propose_change` preflight would settle it and has not been authorized.
- The runtime is **9,281 lines against a 9,156 ceiling**. DAR-020 records the
  measurement and offers the reviewer a deduplication instead of a sixth raise.

---

# Sonnet 4.6 preflight: the exhaustion failure is gone, a different one is not

One request, **$0.016002**. `benchmark/bm06/preflight-propose-46.json`.
Cumulative spend **$0.281528**.

```
model requested   claude-sonnet-4-6
model reported    claude-sonnet-4-6      PASS
manifest sha256   64aa5f77…b584f19
max_output_tokens 4000   (the frozen cap, not a larger one)
finish_reason     stop                   PASS
response_chars    2304                   PASS
output_tokens     662    (of 4000)
output_exhausted  false
json_extracts     false                  FAIL
result            FAIL
```

## What the model change fixed

Everything it was chosen to fix. Under `claude-sonnet-5` this same request
returned `finish_reason: length`, consumed the entire allowance — 4,000 tokens,
then 8,000 on the second attempt — and emitted **zero** visible characters.
Under `claude-sonnet-4-6` it stops normally after 662 of 4,000 tokens and
returns 2,304 characters. The default-thinking exhaustion that stopped the run
does not occur on this model, at this cap, on this operation.

That is the hypothesis this switch rested on, and it held. It was reasoned from
documentation before; it is now observed.

## What still fails

```
malformed JSON object: Expecting property name enclosed in double quotes:
line 1 column 2 (char 1)
```

`extract_json` takes the first `{` in the reply, scans forward to its matching
`}`, and parses that span. The error says the span begins `{` immediately
followed by something that is not a quoted key. Two mechanisms reproduce that
string exactly, verified locally at no cost:

| reply shape | result |
|---|---|
| prose containing an f-string or dict snippet, e.g. `` `f"{self.maxsize}"` ``, before the JSON | **this exact error** |
| an object written with single quotes | **this exact error** |
| prose containing a bare `{}` before the JSON | parses `{}` and returns it — a *different*, quieter failure |
| clean object, fenced object, or prose then a fenced object | all fine |

**Which of these actually happened is not known.** The preflight recorded the
response's shape but not its text, so the cause cannot be read off the record.
That is the same gap this document already criticised once — the recommended
"record the raw response text to a non-durable location" was not implemented in
this script either.

## Why this is not the previous failure wearing a new face

It is the opposite kind of failure, and the difference matters for what to do
next.

The Sonnet 5 failure was **output exhaustion**: nothing was said, so nothing
could be repaired, and a retry would have bought the same result twice. It was
unrecoverable within the request budget.

This one is **completed but malformed**: `finish_reason: stop`,
`output_exhausted: false`, 2,304 visible characters and 3,338 tokens of unused
allowance. It is precisely the case the schema repair implemented in DAR-020
exists for. Run through `_request_change` rather than this bare script, it would
have issued one repair asking for the same proposal correctly serialised, and
continued if that parsed.

So the run is not blocked the way it was before. It may cost one extra request
per affected arm-run — which the $6.54 ceiling already provides for — or it may
be a one-line extractor question. Establishing which requires seeing the text.

## Not guessed at

`llm.py` is approved runtime and `extract_json` is the component under
suspicion. Changing it now would be choosing between two mechanisms without
evidence, and a fix aimed at the wrong one would look like it worked if it
merely changed the failure mode. That objection was recorded here before the
first cause was established and it applies unchanged.

**Recommended, for a ruling:** one further `propose_change` request, identical
except that it writes the raw reply to a scratch path outside the repository
with the key never present in it, at roughly **$0.02**. That distinguishes the
mechanisms, and distinguishes "the extractor is too strict about a preamble"
from "the model emitted non-JSON" — which are different fixes, one of them in
approved runtime and one of them not a fix at all.

No runtime file was changed on the basis of this preflight. The manifest, its
SHA and the ceiling are unchanged.

---

# Live end-to-end diagnostic: the contract works, and it is masking a defect

Two provider calls, hard-capped, driving the real `_request_change` rather than
a bare `post_chat`. **$0.022119.** `benchmark/bm06/live-repair-diagnostic.json`.

```
call 1  propose_change         stop   618 out   2243 chars   -> invalid
        model_response_invalid  malformed JSON object ... line 1 column 2
call 2  propose_change_repair  stop   273 out    901 chars   -> VALID
result  PASS   diff 709 chars, touches src/cachetools/__init__.py
```

Ledger, in order: `spend_reserved`, `model_request_started`,
`model_response_received`, `spend_settled`, `model_response_invalid`,
`spend_reserved`, `model_repair_requested`, `model_response_received`,
`spend_settled`. Exactly one repair. Reserved before it was sent, settled after.

**The production proposal contract works against the live provider.** That was
the question, and the answer is yes.

## But the first reply was not malformed

The raw text was captured this time. It contains **two** braces:

| offset | text |
|---|---|
| 243 | `{1: 5}, maxsize=2, currsize=1)` |
| 1342 | `{"diff": "--- a/src/cachetools/__init__.py …` |

The first is inside the model's echo of the pytest failure message —
`1 unexpectedly found in TLRUCache({1: 5}, maxsize=2, currsize=1)`. It is
brace-balanced and is not JSON.

The second is a complete, well-formed proposal. Extracted by a scan that skips a
span which does not parse instead of raising on it, it yields
`{"diff", "summary"}`, **passes `validate_change`**, and produces a 709-character
diff touching `src/cachetools/__init__.py` — the correct file. Verified locally
at no cost.

So the first reply already contained a valid proposal. `extract_json` took the
first brace-balanced span, failed to parse `{1: 5}`, and raised — discarding a
good proposal 1,100 characters further on.

This is the mechanism the reviewer identified in advance as the one that would
indicate a genuine defect, and it is the one that occurred. The alternative — a
single-quoted object, the model violating the contract — did not.

## Why this matters more than the money

The repair recovered the same fix the first reply already carried. Its
`summary` is, word for word, the summary in the discarded object. One extra
charged request bought nothing that was not already in hand.

The cost is not the problem. The measurement is:

- Every case's frozen signature is in the prompt, and echoing the failure
  message back is ordinary behaviour. Several of these signatures contain
  braces — `TLRUCache({1: 5}, …)`, dict reprs in assertion output. This is not
  a rare shape.
- Each occurrence records a first-attempt failure that **did not happen**, and
  a repair success that was not needed.
- BM-06 compares arms on proposal quality. An adapter defect that manufactures
  first-attempt failures at an unknown rate, and credits the repair path with
  recovering from them, biases exactly the comparison the benchmark exists to
  make — in the direction that flatters the product.

Running 27 arm-runs on top of this would produce numbers that look like evidence
about the model and the repair loop, and are partly evidence about a brace in a
string.

## The remedy, described but not applied

`extract_json` should continue scanning after a balanced span fails to parse,
instead of treating the first candidate as the only one. Its own docstring says
"the surrounding prose is discarded"; a brace in that prose currently makes the
whole reply unusable, which is the opposite.

It is not changed here. `llm.py` is approved runtime and the standing
instruction was not to modify `extract_json` until the cause was established.
It now is, with the reply text on disk as evidence rather than a hypothesis.
Proposed as **DAR-021**, for a ruling.

**Recommendation: do not authorize the 27-arm run yet.** The contract is proven;
one narrow extractor correction and a regression test built from this exact
reply stand between here and a run whose numbers mean what they appear to mean.

## Spend

$0.180358 aborted run + $0.000072 transport smoke + $0.085096 propose preflight
+ $0.016002 Sonnet 4.6 preflight + $0.022119 live diagnostic = **$0.303647**.
Three requests have been made against `claude-sonnet-4-6`. No benchmark result
exists.

---

# Both defects closed. No further provider request was made.

**DAR-021 approved and applied**, with the refinement that extraction must be
decided by the operation's validator rather than by `json.loads`. "Keep scanning
until some JSON parses" was rejected as insufficient: `{}` parses and is not a
proposal, so a reply mentioning one before its real answer would still have
bought a needless repair.

`extract_json` is removed. `json_candidates` + `extract_validated` replace it and
enforce: exactly one object satisfying the frozen contract → accept; zero →
repair; several different → fail closed.

**A second defect was found in the same review and closed.** The repair
entitlement `CLAUDE.md` grants every model operation had been implemented for
`propose_change` only. `propose_handles` and `propose_hypotheses` still returned
immediately on malformed output — so arms B and C, which depend on diagnosis,
could have scored worse for an adapter defect while `propose_change` got its
promised retry. The benchmark would not have measured the frozen M1 behaviour
consistently across arms. All three now share one implementation.

| | |
|---|---|
| captured reply regression | passes **without** a repair |
| extraction tests | 20 passed |
| repair tests, all three operations | 38 passed |
| full suite | 652 passed, 5 skipped |
| ruff / format / mypy | clean |
| runtime | 9,391 / **9,397** |
| model-free verification | **9/9**, 0 requests |
| manifest SHA-256 | **`e48e4fc5b0804e4c65a218379d529cabca5077e44c734473dcd58c53f51408d3`** |
| 27-arm ceiling | **$7.62** (was $6.54) |
| spend | **$0.303647**, unchanged |

The ceiling rose because all three repairs are now reservable, not because
anything got more expensive. It assumes every arm-run needs every repair; on the
one live reply observed, the corrected extractor needs none.

Nothing further is proposed. Sonnet 4.6 has demonstrated normal completion,
visible output, a valid proposal, and a successful live repair; no new preflight
is called for.
