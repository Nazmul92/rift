# Frozen checkpoint — canonicaliser / candidate provenance

**Status: `CLOSED`.** DAR-030 through DAR-035. Reopened only by a reproducible
defect, not by a new idea.

## Reference-environment gate

`REFERENCE_ENVIRONMENT_GREEN` — measured in `python:3.12-slim`, Python 3.12.14,
linux/amd64, git 2.47.3, with the exactly-pinned toolchain
(ruff 0.16.3, mypy 2.3.1, pytest 9.1.1):

```
ruff check           clean
ruff format --check  53 files clean
mypy                 8 source files clean
pytest               899 passed, 5 skipped, 0 failed
```

The four tests that failed in the earlier substitute image all pass here, with
**no code change**. They were environment artifacts, and the substitute image is
now identified precisely: it added `bubblewrap` (which the interrupt test's
one-shot `Popen` patch intercepted) and a global `init.defaultBranch=main`
(which the merge fixture, assuming `master`, tripped on), and it had no network
for the packaging test's `pip wheel` build isolation. None of that was a product
defect, and none of it was worked around in code.

## Identity

| | |
|---|---|
| `runtime_hash` | `75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26` |
| governed runtime files | 8 |
| governed LOC | **10,385 / 10,400** |
| `driver_hash` | `d54e7658be4c5d0f0fe4c5eb253314ef…` |
| BM-06 manifest hash | `7275dbfe3fe97c820ed3c74db0b0572c…` |
| frozen verify manifest hash | `3ad13690d474c42c9b302841c1ab50ed…` |

## Frozen product facts

```
canonicalizer historical recovery        9/9
originally working candidates modified   0

candidate pipeline:
    exact model diff
      -> raw
      -> normalized      (canonical_diff only)
      -> canonical       (git-conditioned hunk-count canonicalization)
      -> content-addressed ChangeSet

multi-attempt provenance   durable, attempt-addressed, immutable
stage provenance           fail closed
```

These components are not to change during corpus curation.

## Results, kept separate

| | |
|---|---|
| **MEASURED** (BM-06 preliminary) | A = 3/8, B = 3/8, C = 3/8 |
| **POST-HOC representation diagnostic** | A = 6/8, B = 6/8, C = 6/8 |

The second is a model-free counterfactual over frozen artifacts. It is not the
benchmark result and the two are never merged.

## Still deferred

```
semantic repair loop        DEFERRED — 0 observed cases
application repair loop     DEFERRED
LLM hunk repair             NOT IMPLEMENTED
fuzzy patch application     NOT IMPLEMENTED
repository-based rewriting  NOT IMPLEMENTED
diagnosis ontology          UNCHANGED
```

Additional provider spend for this phase: **$0.00**.
