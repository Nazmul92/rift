# RIFT v2 + RIFT-Code

Two packages sharing one hypothesis runtime.

- `src/rift/` — RIFT v2, the gridworld study: opaque environment boundary, ten
  causal families, runtime-generated executable hypotheses, active probing,
  schema compilation and transfer.
- `src/riftcode/` — the same runtime applied to debugging: a repository plus
  toolchain is the hidden-state environment, commands are the actions, and a
  test outcome is the observation.

`rift.hypothesis`, `rift.population` and `rift.probes.js_divergence` are shared
by both. The only change made for the second domain was making the IR's event
vocabulary extensible (`register_event_kinds`).

## Running it

    cd rift_v2
    python3 -m pip install -r requirements-dev.txt   # exact pins
    PYTHONPATH=src python3 -m pytest tests -q        # 59 tests
    python3 -m ruff check src tests
    python3 -m ruff format --check src tests
    python3 -m mypy src

Gridworld benchmark (E1-E5, several minutes, writes `results/aggregate.json`):

    PYTHONPATH=src python3 src/rift/runner.py

Code-domain demo against the bundled synthetic fault families:

    PYTHONPATH=src python3 -c "
    import tempfile; from pathlib import Path
    from riftcode.contracts import Budget
    from riftcode.harness.injector import TARGET, build_repo
    from riftcode.loop import localize, verify
    from riftcode.sandbox import Sandbox
    t = Path(tempfile.mkdtemp()); build_repo('cache_stale', t, 0)
    sb = Sandbox(t, Budget(max_commands=90, max_seconds=300))
    d, _, _ = localize(sb, TARGET)
    print(d.status, d.cause, verify(sb, TARGET, d))"

Live LLM proposal requires `ANTHROPIC_API_KEY`; without it the live experiment
is reported as `NOT_RUN_LIVE_LLM` and never silently replaced by grammar or
fixture results.

## Status, honestly

| | state |
|---|---|
| tests / ruff / mypy | 59 passing, clean, clean (all actually executed on python:3.12-slim) |
| gridworld E1-E5 | implemented; only smoke-scale runs executed, never a full benchmark |
| live LLM proposer | implemented, never run — no credentials in the build sandbox |
| code domain, synthetic | 6 fault families x 3 seeds x 3 policies; disagreement 18/18, random 10/18, cheapest 0/18 |
| code domain, real repo | 3 cases on `pallets/click`: 2 identified and gate-verified, 1 `unexplained_by_representation` (a cause with no handle in the action space) |

Read `REAL_REPO_EVIDENCE.md` before drawing conclusions — it lists what the
real repository proved, the miss, and three defects the real repository found
in this code. `RIFTCODE.md` covers the conversion and its limits.

Nothing here is AGI, and none of it is evidence about SWE-bench, other
languages, or repositories beyond the one tested. The bottleneck in both
domains is the same: generating the candidate representations in the first
place.
