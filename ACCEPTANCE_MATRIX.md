# riftagent v1 acceptance matrix

Every row marked **required** needs executable evidence before the associated
milestone is complete. Test names may change, but coverage and failure meaning
may not. A skipped required test keeps the milestone incomplete.

Rows marked **required-or-disclosed** must run when the named environment
capability is available. If it is unavailable, record the exact standardized
status named in that row plus the capability probe and reason in
`IMPLEMENTATION_STATUS.md`. `NOT_RUN_*` is not a pass: the milestone becomes
`CONDITIONALLY_READY`, and only human review may authorize continuation.

## M0 — prototype honesty

| ID | Requirement | Evidence | Level |
|---|---|---|---|
| M0-01 | constant-false/no-handle result abstains as `unexplained_by_representation` | regression test | required |
| M0-02 | no code-defect claim is derived solely from missing environmental explanation | assertion over diagnosis/receipt text | required |
| M0-03 | synthetic benchmark tables match raw result artifacts | reconciliation test/script | required |
| M0-04 | test/lint/type dependencies reproduce in a clean environment | clean install + commands | required |
| M0-05 | inherited isolation and RIFT-Code suites pass unchanged except intentional honesty assertions | pytest output | required |

## M1a — standalone verify

| ID | Requirement | Evidence | Level |
|---|---|---|---|
| V-01 | `rift verify <diff> <test>` makes zero model/provider calls | import/call/ledger assertions | required |
| V-02 | baseline target fails and its original signature is frozen | temporary real repo | required |
| V-03 | candidate applies the exact accepted diff in a pristine worktree and passes | hash/tree integration test | required |
| V-04 | withdrawal removes only the candidate diff and restores the original failure signature | counterfactual integration test | required |
| V-05 | exact diff is reapplied before preservation checks | content hash/tree assertion | required |
| V-06 | collection/import/infrastructure failures cannot satisfy baseline or withdrawal | parametrized tests | required |
| V-07 | semantically inert/order-masked patch is rejected | Click-shaped fixture | required |
| V-08 | diff cannot escape repo, touch `.git`/`.rift`, weaken declared judge/config, or contain binary patch | adversarial diff tests | required |
| V-09 | minimal ledger is sole durable state and resumes by replay | crash/restart tests | required |
| V-10 | settled transcript and receipt replay byte-identically | golden replay test | required |
| V-11 | `--yes` cannot authorize partial isolation | CLI/ledger test | required |
| V-12 | partial execution requires explicit `--allow-partial-sandbox`, recorded separately | CLI/receipt test | required |
| V-13 | timeout terminates child and descendant processes on the current platform | process-tree integration test | required |
| V-14 | native Windows uses tested whole-tree termination or blocks before repository execution | Windows capability test | required where Windows |
| V-15 | verify benchmark materially lowers incorrect-patch acceptance and retains ≥90% standard-protocol correct-patch acceptance | frozen real-patch benchmark report | required |
| V-16 | clean wheel install exposes `rift verify` and `rift resume` before M1 begins | isolated environment smoke | required |

## M1 — structural boundaries

| ID | Requirement | Evidence | Level |
|---|---|---|---|
| M1-S01 | kernel imports no LLM, app, provider, networking or renderer module | AST test | required |
| M1-S02 | kernel cannot receive an injected model callback | API/AST test | required |
| M1-S03 | LLM module imports no kernel, checks or sandbox authority | AST test | required |
| M1-S04 | no LangGraph/LangChain/workflow/checkpoint dependency | lockfile + import scan | required |
| M1-S05 | current phase and budgets reconstruct only from ledger events | reducer/restart test | required |
| M1-S06 | no mutable secondary state/checkpoint is created | filesystem assertion | required |
| M1-S07 | transition event is flushed before next side effect | crash-injection test | required |
| M1-S08 | invalid event sequence fails closed | reducer test | required |
| M1-S09 | completed transcript and receipt are byte-identical after renderer restart | replay golden test | required |

## M1 — command and sandbox boundary

| ID | Requirement | Evidence | Level |
|---|---|---|---|
| M1-X01 | no runtime path uses `shell=True` | AST test | required |
| M1-X02 | model shell strings and unknown command shapes are rejected | adversarial response tests | required |
| M1-X03 | cwd is always a disposable worktree/copy | integration test | required |
| M1-X04 | child environment excludes model, cloud and git credentials | sentinel-secret test | required |
| M1-X05 | timeout terminates child and descendant processes; native Windows uses a Job Object/equivalent or blocks | process-tree/platform test | required |
| M1-X06 | full Linux sandbox disables network and host writes when available | bubblewrap integration test | required where supported |
| M1-X07 | unavailable full sandbox requires `--allow-partial-sandbox`; `--yes` cannot grant it; authorities have separate provenance | CLI/receipt integration test | required |
| M1-X08 | receipt reports the isolation level actually used | receipt assertion | required |
| M1-X09 | path traversal, symlink escape, binary diff, `.git` and `.rift` modification are rejected | patch adversarial tests | required |

## M1 — fix/why correctness

| ID | Requirement | Evidence | Level |
|---|---|---|---|
| M1-F01 | failing pytest node reproduces with captured signature | temporary real repo | required |
| M1-F02 | wrong-signature failure cannot satisfy baseline | integration test | required |
| M1-F03 | collection/import/infrastructure errors do not count as target failure | parametrized tests | required |
| M1-F04 | hypotheses remain live until contradicted by evidence | inherited + new kernel tests | required |
| M1-F05 | disagreement-per-cost selects the highest-value probe deterministically | unit/golden tests | required |
| M1-F06 | all contradicted hypotheses trigger one bounded `propose_handles` call | fake-adapter test | required |
| M1-F07 | novel executable handle primitive is rejected | schema/adversarial test | required |
| M1-F08 | ambiguous survivors produce `underdetermined`, not a guessed cause | integration test | required |
| M1-F09 | `why` never proposes/applies a patch or runs a gate | call/ledger assertion | required |
| M1-F10 | false fix is rejected unless baseline fail -> candidate pass -> withdrawal fail | order-dependent fixture | required |
| M1-F11 | withdrawal failure must match the original signature | counterfactual test | required |
| M1-F12 | exact accepted patch is reapplied before preservation checks | hash/tree assertion | required |
| M1-F13 | regression yields `regression_blocked` and is not silently repaired | integration test | required |
| M1-F14 | no bare `done`/`verified` verdict can be emitted | enum/render tests | required |
| M1-F15 | assertion-supported ungatable finding emits observational `diagnosis_supported`, `gate: not_applicable`, and unverified remediation | missing-dependency fixture | required |
| M1-F16 | observational/ungatable branch never receives verified-fix credit | receipt + benchmark accounting test | required |

## M1 — streaming, context, cost and recovery

| ID | Requirement | Evidence | Level |
|---|---|---|---|
| M1-R01 | command start appears before completion and long tests show progress/counters; if PTY support is unavailable report `NOT_RUN_PTY_UNAVAILABLE` and still pass renderer/replay unit tests | PTY capability probe + integration test | required-or-disclosed |
| M1-R02 | every settled claim corresponds to a durable event | transcript-to-ledger test | required |
| M1-R03 | context ordering/caps are deterministic and traceback/import files survive | unit tests | required |
| M1-R04 | pinned Django-scale repo stays within caps while retaining target context; if the repo is unavailable and fetching is prohibited report `NOT_RUN_NETWORK_UNAVAILABLE` | local-repo probe + external integration test | required-or-disclosed |
| M1-R05 | secrets and `.rift/` never enter model context | sentinel-secret test | required |
| M1-R06 | provider token count is recorded when returned and otherwise `unknown` | fake-adapter tests | required |
| M1-R07 | Ctrl-C leaves replayable ledger and kills child processes | PTY/crash test | required |
| M1-R08 | identical-tree resume continues without repeating completed model work | fake-usage test | required |
| M1-R09 | interrupted model request with no durable response is never automatically repeated | crash/fake-usage test | required |
| M1-R10 | any tracked drift recreates sandbox and reruns baseline/affected checks | git integration test | required |
| M1-R11 | multiple incomplete tasks require explicit selection | CLI integration test | required |
| M1-R12 | budget exhaustion and infrastructure failure are distinct | verdict tests | required |
| M1-R13 | no-model deterministic diagnosis degrades to explicit abstention when a spec/patch is required | no-credential CLI test | required |

## M1.5 — edit

| ID | Requirement | Evidence | Level |
|---|---|---|---|
| E-01 | characterization checks pass on baseline before approval | integration test | required |
| E-02 | only one Spec Card approval point exists per accepted spec | ledger/PTY assertion | required |
| E-03 | `--yes` provenance differs from explicit approval | receipt test | required |
| E-04 | implementation cannot modify frozen characterization tests/config | adversarial patch test | required |
| E-05 | all frozen characterization and relevant existing checks pass after edit | integration test | required |
| E-06 | receipt describes preservation scope without claiming characterized behavior is correct | golden receipt test | required |
| E-07 | subjective improvement with no falsifiable criterion is stamped `unverifiable` | CLI fixture | required |
| E-08 | null or semantically empty ChangeSet is rejected before preservation gating | null-patch integration test | required |

## M2 — build

| ID | Requirement | Evidence | Level |
|---|---|---|---|
| B-01 | proposed behaviors, checks and predicted signatures validate strictly | schema tests | required |
| B-02 | each change check fails on old code for its predicted reason | temporary real repo | required |
| B-03 | vacuous already-passing change check blocks approval/implementation | integration test | required |
| B-04 | invalid spec gets at most one repair call, then abstains | fake-adapter call count | required |
| B-05 | approve/edit/cancel and `--yes` are durably recorded | PTY/ledger tests | required |
| B-06 | approved spec/check/config bytes and hashes are frozen | mutation/hash tests | required |
| B-07 | one coherent implementation ChangeSet is proposed per attempt | fake-adapter call/patch test | required |
| B-08 | candidate patch cannot modify the frozen judge | adversarial diff tests | required |
| B-09 | change checks pass on candidate | integration test | required |
| B-10 | withdrawing implementation while retaining spec restores predicted failures | integration test | required |
| B-11 | exact patch reapplication followed by preservation checks is enforced | hash/gate test | required |
| B-12 | preservation failure emits `regression_blocked` without unrelated repair | integration test | required |
| B-13 | receipt lists checks not run and remaining uncertainty | golden receipt test | required |

## Benchmark accounting

| ID | Requirement | Evidence | Level |
|---|---|---|---|
| BM-01 | ground truth and `gateable` / `observationally_diagnosable` / `neither` labels freeze before any arm runs | benchmark manifest hash | required |
| BM-02 | the agent cannot choose a class or remove abstentions/failures from denominators | accounting tests | required |
| BM-03 | verified-fix yield is correct gate-passed fixes divided by all attempted frozen-gateable tasks | recomputation from raw records | required for M1 |
| BM-04 | observational diagnosis yield is reported separately and `gate: not_applicable` earns no fix credit | recomputation from raw records | required for M1 |
| BM-05 | cost per correct outcome includes costs from abstained and failed attempts; zero correct outcomes is infinite/undefined, never zero | accounting tests | required |
| BM-06 | M1 C lowers false-fix acceptance, retains ≥90% of A's correct-fix yield, and lowers token cost per correct fix | frozen benchmark report | required for M1 expansion claim |
| BM-07 | M2 C lowers wrong-thing-built rate, retains ≥90% of A's correct-feature yield, and does not increase token cost per correct feature | frozen benchmark report | required for M2 thesis claim |

## Packaging and completion

| ID | Requirement | Evidence | Level |
|---|---|---|---|
| P-01 | wheel and sdist build successfully | build output | required |
| P-02 | clean wheel install exposes `rift` and all six commands | isolated environment smoke | required |
| P-03 | tests run against installed package | clean-environment pytest | required |
| P-04 | Ruff and mypy pass with pinned versions | command output | required |
| P-05 | runtime remains near six substantive modules and at or below the 8,600-line M2 disclosure ceiling (amended once from ~8,000; DAR-008); tests/fixtures/benchmarks reported separately | measured report; justify any deviation | required |
| P-06 | fake-provider suite requires no credentials | clean-env suite | required |
| P-07 | live OpenAI-compatible provider smoke is reported accurately | pass or `NOT_RUN_LIVE_PROVIDER` | disclosure |
| P-08 | §15 verify/fix/build benchmark status is separate from implementation status | final report | required |

## Benchmark reporting rule

The comparative §15 benchmark is the product thesis gate, not a unit test.
Report each arm, repository/commit, request/failure case, model and parameters,
commands, wall time, provider-reported tokens, verdict, ground truth and raw
artifact path. Never convert `NOT_RUN`, partial samples, synthetic fixtures or
unit-test success into a comparative product claim.
