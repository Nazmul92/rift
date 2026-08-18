# BM-06 label review — proposed, not frozen

Stage 2 proves one thing about a candidate: the target **fails at the parent and
passes at the fix**, reproducibly, in a stated container. It proves nothing about
the *cause class*. That label comes from stage 1's keyword match on the commit
message and is unverified by construction.

This file separates the two claims so they cannot be conflated. Every row below
is a stage-2 **confirmed** case; the assessment column is my reading of the
evidence and is offered for review, not applied. Nothing here changes a stage-2
record.

## Why this matters more than it sounds

The manifest requires all eight cause classes. If a keyword-assigned label is
taken at face value, BM-06 would report per-class results for classes whose cases
are not actually of that class — and a benchmark that mislabels its own strata
cannot support a claim about which failures the kernel handles well. A case that
is genuinely reproducible but mislabelled is still a usable case; it just belongs
in a different stratum, or in none.

## The ten confirmed cases

| repo | stage-1 label | commit | target and observed failure | assessment |
|---|---|---|---|---|
| boltons | version_mismatch | `ce7c7d2b15` | `test_human_readable_list` — `assert 'apple banana and cherry' == 'appleba…'` | **Reassign.** A string-joining defect. Nothing version-dependent in the evidence. Reads as `genuine_source_bug`. |
| chardet | missing_dependency | `2fe89933d4` | `test_ratio_cold_vs_warm_model_loading` — `Cold start too slow vs warm: 29.1x (max 25x)` | **Reject as a case.** The assertion is a wall-clock ratio against a threshold. It reproduces here but its outcome depends on host load, so it is not a stable reproducer and it is not a missing dependency. |
| chardet | order_dependence | `605e691acb` | `test_attribute_access_emits_deprecation_warning` — `DID NOT WARN` | **Weak.** Warning-registry state does make these order-sensitive, so the label is plausible, but the commit is "Fix the 15 findings from the since-72dc7f4 review" — a multi-purpose commit, not a single-cause exemplar. |
| click | order_dependence | `93c6966eb3` | `test_pipeline[args0-foo\nbar-expect0]` — `assert not SystemExit(1)` | **Reassign.** Subject is "Fix regression related to EOF introduced in 262bdf0"; the fix is in `src/click/testing.py` EOF handling. `genuine_source_bug`. |
| click | version_mismatch | `051725fa7e` | `test_stream_helper_deprecated[...]` — `DID NOT WARN` | **Reassign.** A missing deprecation warning is a source defect, not a dependency-version conflict. |
| markdown | version_mismatch | `23c301de28` | `test_raw…` — HTML block output mismatch | **Reassign.** Parser output defect. `genuine_source_bug`. |
| markdown | version_mismatch | `9980cb5b27` | `test_not…` — `'<!--[\n\n-->' != '<p>&lt;![</p>'` | **Reassign.** As above. |
| pluggy | version_mismatch | `0258484dc1` | `test_varnames_legacy_noself_warns` — `assert False` | **Weak.** Commit is "Address review: DeprecationWarning, add self to test, suppress pytest-timeout" — multi-purpose. |
| sqlparse | state_leakage | `907fb496f9` | `test_float_numbers[1.0]` — `AttributeError: type object 'Lexer' has no attribute 'get_de…'` | **Plausible.** sqlparse's `Lexer` is a process-global singleton and the failure is about its mutated class state. The strongest state-leakage candidate here. |
| sqlparse | version_mismatch | `d66b9247d1` | `test_valid_args` — `RuntimeError: generator raised StopIteration` | **Supported.** This is PEP 479: `StopIteration` inside a generator became a `RuntimeError` in Python 3.7. A genuine interpreter-version mismatch. |

## What the review implies, if accepted as written

- **Supported as labelled:** 1 (`sqlparse` version_mismatch)
- **Plausible:** 1 (`sqlparse` state_leakage)
- **Weak — multi-purpose commit, not a clean exemplar:** 2
- **Reassign, most to `genuine_source_bug`:** 5
- **Reject as a case:** 1 (timing-ratio assertion)

Class coverage after reassignment would be roughly `genuine_source_bug` 5,
`version_mismatch` 1, `state_leakage` 1, plus 2 weak — against a requirement of
eight classes with four order-dependent cases across two repositories. The
order-dependence stratum would drop from 2 to at most 1.

This is a worse position than the raw stage-2 count suggests, and it is the
honest one. The alternative — keeping keyword labels because they make the
strata look populated — is the failure mode this file exists to prevent.
