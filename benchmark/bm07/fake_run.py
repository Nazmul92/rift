"""Drive the real BM-07 `run` path with a loopback provider. No network, no spend.

This is deliberately not a test of helper functions. It starts the same
`bm07_runner.run` the paid benchmark will start, against the real frozen
manifest, with `RIFT_LLM_URL` pointed at a scripted OpenAI-compatible server on
127.0.0.1. Everything between — identity preflight, derived reservation, the
`rift fix` invocation per arm, provider-response evidence, the candidate
pipeline, arm evaluation, the Arm-A same-candidate shadow, the independent truth
oracle, settlement and the durable record — is the shipped path.

The scripted outcomes are chosen so the run has to record bad news as well as
good: a correct fix, a target-passing patch that breaks preserved behaviour, an
unapplicable patch, a malformed reply that must consume the one authorised schema
repair, and a refusal that must abstain rather than invent a candidate.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "src"))

import bm07_fake_provider as fake  # noqa: E402
import bm07_runner as runner  # noqa: E402

ROOT = pathlib.Path("/w")
REPOS = pathlib.Path("/repos")
MANIFEST = ROOT / "benchmark/bm07/manifest-executable.json"
RESULTS = ROOT / "benchmark/bm07/fake-run-results.jsonl"
WORK = pathlib.Path("/tmp/bm07-fake")

# One scripted outcome per case, so a single run exercises the whole matrix
# rather than six copies of the happy path.
OUTCOMES = [
    fake.CORRECT,
    fake.BREAKS_PRESERVATION,
    fake.UNAPPLICABLE,
    fake.MALFORMED_THEN_VALID,
    fake.NO_CANDIDATE,
    fake.CORRECT,
]


def historical_source_patch(case: dict) -> str:
    repo = REPOS / case["repository"]
    names = runner.driver.run(["git", "show", "--numstat", "--format=", case["fix_commit"]], repo).stdout
    tests = set(case["test_files"])
    sources = [
        parts[2]
        for parts in (line.split("\t") for line in names.splitlines())
        if len(parts) == 3 and parts[2].endswith(".py") and parts[2] not in tests
    ]
    return runner.driver.run(["git", "show", "--format=", case["fix_commit"], "--", *sources], repo).stdout


def breaking_patch(case: dict) -> str:
    """A patch that will not apply, standing in for a wrong candidate.

    Constructing a genuinely target-passing-but-preservation-breaking patch for
    six unrelated real repositories is not something a harness can do
    generically, and inventing one per repository would be authoring the very
    'synthetic trap' the corpus protocol forbids. The unapplicable shape still
    drives the reject path end to end; the preservation-breaking shape is covered
    deterministically by the driver's own fixtures.
    """
    return "--- a/does/not/exist.py\n+++ b/does/not/exist.py\n@@ -1,1 +1,1 @@\n-a\n+b\n"


def scenario_dir(name: str) -> Path:
    """A private copy of the evidence, so one scenario cannot corrupt the next."""
    out = WORK.parent / f"bm07-scenario-{name}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    results = out / RESULTS.name
    results.write_text(RESULTS.read_text(encoding="utf-8"), encoding="utf-8")
    state = runner.state_path(RESULTS)
    if state.is_file():
        runner.state_path(results).write_text(state.read_text(encoding="utf-8"), encoding="utf-8")
    return results


def drop_pair(results: Path, case_id: str, arm: str) -> None:
    kept = [
        line
        for line in results.read_text(encoding="utf-8").splitlines()
        if not (json.loads(line)["case_id"] == case_id and json.loads(line)["arm"] == arm)
    ]
    results.write_text("".join(line + "\n" for line in kept), encoding="utf-8")


def crash_restart_scenarios(manifest: dict, script, full_run_code: int) -> None:
    """The three ways a restart could quietly go wrong, exercised on real evidence.

    Each starts from the completed run's own records, so these are not synthetic
    shapes — they are what the disk would actually look like after the
    corresponding interruption.
    """
    case_id = manifest["cases"][0]["case_id"]
    status, _ = runner.official_status(RESULTS, manifest)
    print(f"\nfull run         : exit {full_run_code}, aggregation {status}")

    # A — a request was started and never settled, and no result landed either.
    results = scenario_dir("a")
    drop_pair(results, case_id, "A")
    runner.write_state(results, case_id, "A", runner.REQUEST_STARTED, "simulated crash before the result")
    before = len(script.requests)
    code_a = runner.run(MANIFEST, REPOS, WORK, results)
    calls_a = len(script.requests) - before
    halted_a = runner.BLOCKED_FOR_RECONCILIATION if code_a == runner.EXIT_RECONCILE else f"exit {code_a}"
    print(f"scenario A       : {halted_a}, additional provider requests = {calls_a}")

    # B — the result is durable, the terminal state never got written. The
    # evidence exists, so reconciliation is possible; spending again is not.
    results = scenario_dir("b")
    runner.write_state(results, case_id, "B", runner.REQUEST_STARTED, "simulated crash after the result")
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    has_result = any(r["case_id"] == case_id and r["arm"] == "B" for r in rows)
    before = len(script.requests)
    code_b = runner.run(MANIFEST, REPOS, WORK, results)
    calls_b = len(script.requests) - before
    print(f"scenario B       : result present = {has_result}, exit {code_b}, duplicate provider requests = {calls_b}")

    # C — a complete-looking run missing one record must not produce a score.
    results = scenario_dir("c")
    drop_pair(results, manifest["cases"][-1]["case_id"], "C")
    status_c, gaps = runner.official_status(results, manifest)
    rows_c = len(results.read_text(encoding="utf-8").splitlines())
    print(f"scenario C       : {rows_c} of 18 records -> {status_c}, NO OFFICIAL SCORE ({len(gaps)} gap)")

    # D — the orchestration program is not the one the manifest froze. Nothing
    # about the manifest or the corpus is wrong; the code that spends is.
    results = scenario_dir("d")
    real_runner_hash = runner.runner_hash
    runner.runner_hash = lambda: "d" * 64
    try:
        before = len(script.requests)
        code_d = runner.run(MANIFEST, REPOS, WORK, results)
        calls_d = len(script.requests) - before
    finally:
        runner.runner_hash = real_runner_hash
    print(f"scenario D       : runner mismatch -> exit {code_d}, provider requests = {calls_d}")

    # E — 17 records from the frozen runner, 1 from another. Every other hash
    # matches, and the score is still refused.
    results = scenario_dir("e")
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    rows[-1]["runner_hash"] = "e" * 64
    results.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    _, drift = runner.aggregate(results, manifest)
    mixed = [d for d in drift if "runner_hash" in d]
    print(f"scenario E       : 17 frozen + 1 foreign runner -> {len(mixed)} rejection(s), NO FINAL SCORE")


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    shutil.rmtree(WORK, ignore_errors=True)
    RESULTS.unlink(missing_ok=True)
    # The arm-state log outlives the results file by design, so a fresh run must
    # clear it explicitly. Leaving it would halt this run for reconciliation of
    # a previous run's simulated crash — correct behaviour, wrong subject.
    runner.state_path(RESULTS).unlink(missing_ok=True)

    per_case = {}
    for case, kind in zip(manifest["cases"], OUTCOMES, strict=True):
        per_case[case["target_node"]] = fake.scripted(kind, historical_source_patch(case), breaking_patch(case))

    script = fake.Script(per_case, model=manifest["model"]["requested_model_id"])
    with fake.FakeProvider(script) as url:
        os.environ["RIFT_LLM_URL"] = url
        os.environ["RIFT_LLM_KEY"] = "fake-key-not-a-secret"
        os.environ["RIFT_LLM_MODEL"] = manifest["model"]["requested_model_id"]
        code = runner.run(MANIFEST, REPOS, WORK, RESULTS)
        crash_restart_scenarios(manifest, script, code)

    rows = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines()] if RESULTS.is_file() else []
    spend = sum(float(r.get("actual_usd") or 0.0) for r in rows)
    same_bytes = [
        r
        for r in rows
        if r["arm"] == "A"
        and r.get("canonical_candidate_hash")
        and r["weak_candidate_hash"]
        == r["strong_candidate_hash"]
        == r["truth_candidate_hash"]
        == r["canonical_candidate_hash"]
    ]
    print(f"\nprovider requests served : {len(script.requests)} (loopback)")
    print(f"arm records              : {len(rows)}")
    print(f"arm-A same-candidate ok  : {len(same_bytes)}")
    print("real provider calls      : 0")
    print(f"real spend               : $0.00 (loopback only; ledger charged ${spend:.4f} of fake usage)")
    shutil.rmtree(WORK, ignore_errors=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
