"""BM-07 transaction integrity: don't re-spend, don't lose evidence, don't score a partial run.

Three defects, one shape. Each is a place where the harness could have been
*confident about something it had no evidence for*.

**A request that may already have been paid for.** `request_started` with no
terminal state means the money may or may not be gone, and nothing on disk can
say which. Refusing to re-run that one arm is not enough: every later arm would
still add fresh, certain spend on top of prior spend of unknown size. The whole
run stops.

**A terminal state written before its evidence.** Marking an arm `completed` and
then crashing before its record lands leaves a restart skipping an arm whose
outcome can no longer be reconstructed — silently, and forever. Reversing the
order makes the crash window fail closed instead: the result exists, the state
still says `request_started`, and reconciliation is possible because the evidence
is there.

**A score computed from an incomplete set.** Official BM-07 is frozen as six
cases by three arms. Seventeen records, or eighteen with a duplicate standing in
for a gap, describe a different experiment; averaging them reports a number for a
benchmark that was not run.

No provider is configured and no request leaves the process.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

BM07 = Path(__file__).parents[1] / "benchmark" / "bm07"
if str(BM07) not in sys.path:
    sys.path.append(str(BM07))

import bm07_runner as runner  # noqa: E402
import test_bm07_driver as driver_tests  # noqa: E402

# The BM-07 case repository and manifest builders live with the driver tests.
# Re-bound rather than re-implemented: two fixtures each claiming to build "the
# same" case repository is how a suite starts testing a shape the benchmark does
# not actually have.
case_repo = driver_tests.case_repo
manifest_for_run = driver_tests.manifest_for_run

FROZEN_MANIFEST = BM07 / "manifest-executable.json"


# ------------------------------------------------------------------ helpers


def frozen() -> dict:
    return json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))


def record_row(manifest: dict, case_id: str, arm: str, status: str = "completed", **extra: object) -> dict:
    return {
        "benchmark_id": manifest["benchmark_id"],
        "case_id": case_id,
        "arm": arm,
        "manifest_hash": manifest.get("manifest_hash", ""),
        "runtime_hash": manifest["runtime_hash"],
        "driver_hash": manifest["driver_hash"],
        "runner_hash": manifest.get("runner_hash", ""),
        "oracle_hash": manifest["oracle_hash"],
        "status": status,
        "actual_usd": 0.0,
        **extra,
    }


def lay_down(results: Path, manifest: dict, pairs, status: str = "completed", state: str | None = None) -> None:
    """Write result rows and their matching terminal states, in the shipped order."""
    with results.open("a", encoding="utf-8", newline="\n") as fh:
        for case_id, arm in pairs:
            fh.write(json.dumps(record_row(manifest, case_id, arm, status), sort_keys=True) + "\n")
    for case_id, arm in pairs:
        resolved = state or (runner.COMPLETED if status == "completed" else runner.BLOCKED)
        runner.write_state(results, case_id, arm, resolved)


def all_official(manifest: dict) -> list[tuple[str, str]]:
    return sorted(runner.expected_pairs(manifest))


def counted_run(monkeypatch, manifest_path, repos, work, results, arms=runner.ARMS):
    """Run with every outward-facing step counted, so 'zero calls' is measured."""
    calls: list[str] = []
    monkeypatch.setattr(runner, "run_arm_command", lambda *a, **k: calls.append("adapter") or ({"verdict": "x"}, None))
    monkeypatch.setattr(runner.driver, "preflight", lambda *a, **k: calls.append("preflight") or [])
    monkeypatch.setattr(runner.driver, "materialise_baseline", lambda *a, **k: calls.append("materialise") or None)
    monkeypatch.setenv("RIFT_LLM_MODEL", "m")
    code = runner.run(manifest_path, repos, work, results, arms=arms)
    return code, calls


class _Proc:
    returncode = 0
    stdout = ""
    stderr = ""


def let_arms_run(monkeypatch, manifest, task_id: str = "task-1"):
    """Get past the gates that already have their own dedicated tests.

    Baseline identity, provider-model identity and candidate binding are each
    covered in `test_bm07_driver.py`. Stubbing them here keeps these tests about
    the one thing they exist to observe: the order in which durable writes
    happen, and whether an adapter is reached at all.
    """
    monkeypatch.setattr(runner, "tree_hash", lambda tree: manifest["cases"][0]["baseline_tree_hash"])
    monkeypatch.setattr(runner.driver, "preflight", lambda *a, **k: [])
    monkeypatch.setenv("RIFT_LLM_MODEL", manifest["model"]["requested_model_id"])

    def arm(argv, tree, case):
        task = Path(tree) / ".rift" / "tasks" / task_id
        task.mkdir(parents=True, exist_ok=True)
        event = {
            "kind": "model_response_received",
            "payload": {
                "model_reported": manifest["model"]["requested_model_id"],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
        (task / "ledger.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
        return {"verdict": "unverifiable", "task_id": task_id}, _Proc()

    monkeypatch.setattr(runner, "run_arm_command", arm)


# ============================================ 1. the global reconciliation stop


def test_an_unsettled_request_stops_the_whole_run_before_any_adapter_call(case_repo, tmp_path, monkeypatch):
    """Prior spend of unknown size must not have known spend added on top of it."""
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    runner.write_state(results, m["cases"][0]["case_id"], "A", runner.REQUEST_STARTED)

    code, calls = counted_run(monkeypatch, path, repos, work, results)

    assert code == runner.EXIT_RECONCILE
    assert "adapter" not in calls, "a provider call was made while a request was unreconciled"


def test_the_blocker_is_found_before_any_later_case_or_arm_runs(case_repo, tmp_path, monkeypatch):
    """Not 'skip that arm and carry on' — the run does not start."""
    repos, case, work = case_repo
    m, _ = manifest_for_run(case, tmp_path)
    # A second case, never materialised because the run stops first.
    second = dict(m["cases"][0])
    second["case_id"] = "second-case"
    m["cases"] = [m["cases"][0], second]
    m["manifest_hash"] = runner.driver.manifest_hash(m)
    path = tmp_path / "two.json"
    path.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    results = tmp_path / "r.jsonl"
    runner.write_state(results, m["cases"][0]["case_id"], "A", runner.REQUEST_STARTED)

    code, calls = counted_run(monkeypatch, path, repos, work, results)

    assert code == runner.EXIT_RECONCILE
    assert calls == [], f"work happened after a reconciliation blocker: {calls}"
    assert not results.exists(), "a result was recorded during a halted run"


def test_the_scan_covers_arms_this_invocation_was_not_asked_to_run(case_repo, tmp_path, monkeypatch):
    """`--arms A` must not tiptoe past an unsettled request in arm C."""
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    runner.write_state(results, m["cases"][0]["case_id"], "C", runner.REQUEST_STARTED)

    code, calls = counted_run(monkeypatch, path, repos, work, results, arms=("A",))

    assert code == runner.EXIT_RECONCILE
    assert calls == []


def test_a_settled_arm_does_not_trigger_the_halt(case_repo, tmp_path, monkeypatch):
    """The stop is for *unreconciled* requests; a completed arm is settled evidence."""
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    lay_down(results, m, [(m["cases"][0]["case_id"], "A")])

    code, calls = counted_run(monkeypatch, path, repos, work, results, arms=("A",))

    assert code != runner.EXIT_RECONCILE
    assert "preflight" in calls, "a settled log should not have blocked the run"


def test_a_blocked_paid_arm_is_never_automatically_re_spent(case_repo, tmp_path, monkeypatch):
    """A response was already received and paid for. Retrying is a second charge."""
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    lay_down(results, m, [(m["cases"][0]["case_id"], "A")], status="blocked_model_identity")

    code, calls = counted_run(monkeypatch, path, repos, work, results, arms=("A",))

    assert "adapter" not in calls, "a blocked paid arm was retried"
    assert code != runner.EXIT_RECONCILE, "a terminal blocked state is settled, not unreconciled"


def test_unreconciled_lists_every_pending_pair(tmp_path):
    results = tmp_path / "r.jsonl"
    runner.write_state(results, "c1", "A", runner.REQUEST_STARTED)
    runner.write_state(results, "c2", "B", runner.REQUEST_STARTED)
    runner.write_state(results, "c3", "C", runner.REQUEST_STARTED)
    runner.write_state(results, "c3", "C", runner.COMPLETED)

    assert runner.unreconciled(results) == [("c1", "A"), ("c2", "B")]


def test_the_halt_happens_before_preflight_reaches_a_repository(case_repo, tmp_path, monkeypatch):
    """The cheapest possible refusal: nothing is materialised to discover it."""
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    runner.write_state(results, m["cases"][0]["case_id"], "B", runner.REQUEST_STARTED)

    _, calls = counted_run(monkeypatch, path, repos, work, results)

    assert "preflight" not in calls and "materialise" not in calls


# ================================== 2. evidence is durable before terminal state


def test_run_case_arm_writes_no_terminal_state_of_its_own():
    """Structural: the terminal marker belongs to the caller, after the record."""
    source = Path(runner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_case_arm")
    written = [
        node.args[3].id
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "write_state"
        and len(node.args) > 3
        and isinstance(node.args[3], ast.Name)
    ]
    assert written == ["REQUEST_STARTED"], f"run_case_arm writes terminal states: {written}"


def test_the_result_is_already_durable_when_the_terminal_state_is_written(case_repo, tmp_path, monkeypatch):
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    seen: dict = {}

    def observe(record):
        seen["rows"] = len(results.read_text(encoding="utf-8").splitlines()) if results.is_file() else 0
        seen["state"] = runner.load_states(results).get((record.case_id, record.arm))

    let_arms_run(monkeypatch, m)
    monkeypatch.setattr(runner, "_after_result_persisted", observe)
    runner.run(path, repos, work, results, arms=("A",))

    assert seen["rows"] == 1, "the terminal state was reached before the result was durable"
    assert seen["state"] == runner.REQUEST_STARTED


def test_a_crash_between_the_result_and_the_terminal_state_is_fail_closed(case_repo, tmp_path, monkeypatch):
    """The acceptable shape: result present, state still `request_started`."""
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"

    def crash(record):
        raise KeyboardInterrupt("simulated crash after the record was written")

    let_arms_run(monkeypatch, m)
    monkeypatch.setattr(runner, "_after_result_persisted", crash)
    with pytest.raises(KeyboardInterrupt):
        runner.run(path, repos, work, results, arms=("A",))

    case_id = m["cases"][0]["case_id"]
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    assert [(r["case_id"], r["arm"]) for r in rows] == [(case_id, "A")], "the evidence did not survive the crash"
    assert runner.load_states(results)[(case_id, "A")] == runner.REQUEST_STARTED

    # And a restart refuses to spend again, with the evidence available to
    # reconcile against.
    monkeypatch.setattr(runner, "_after_result_persisted", lambda record: None)
    code, calls = counted_run(monkeypatch, path, repos, work, results, arms=("A",))
    assert code == runner.EXIT_RECONCILE
    assert "adapter" not in calls


def test_a_crash_before_the_result_does_not_re_spend(tmp_path, case_repo, monkeypatch):
    """No record at all — the harder case, and still no second request."""
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    runner.write_state(results, m["cases"][0]["case_id"], "A", runner.REQUEST_STARTED)
    assert not results.exists()

    code, calls = counted_run(monkeypatch, path, repos, work, results, arms=("A",))

    assert code == runner.EXIT_RECONCILE
    assert calls == []


def test_the_normal_path_persists_the_result_then_marks_completed(case_repo, tmp_path, monkeypatch):
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    order: list[str] = []

    real_append, real_state = runner.append_record, runner.write_state
    monkeypatch.setattr(runner, "append_record", lambda r, rec: order.append("result") or real_append(r, rec))

    def traced(res, case_id, arm, state, detail=""):
        order.append(state)
        real_state(res, case_id, arm, state, detail)

    let_arms_run(monkeypatch, m)
    monkeypatch.setattr(runner, "write_state", traced)
    runner.run(path, repos, work, results, arms=("A",))

    assert order == [runner.REQUEST_STARTED, "result", runner.COMPLETED], order


def test_an_arm_that_never_reached_the_adapter_stays_re_runnable(case_repo, tmp_path, monkeypatch):
    """A budget skip spent nothing, so it must not be sealed as terminal."""
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    m["budget"]["total_usd_ceiling"] = 0.0
    path.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    results = tmp_path / "r.jsonl"

    monkeypatch.setattr(runner.driver, "preflight", lambda *a, **k: [])
    monkeypatch.setenv("RIFT_LLM_MODEL", "m")
    runner.run(path, repos, work, results, arms=("A",))

    key = (m["cases"][0]["case_id"], "A")
    assert runner.load_states(results).get(key) is None, "a never-asked arm was sealed with a terminal state"
    assert runner.unreconciled(results) == []


def test_terminal_state_is_derived_from_the_record_status():
    assert runner.terminal_state(runner.ArmRecord(**_stub(status="completed"))) == runner.COMPLETED
    assert runner.terminal_state(runner.ArmRecord(**_stub(status="blocked_model_identity"))) == runner.BLOCKED
    assert runner.terminal_state(runner.ArmRecord(**_stub(status="blocked_candidate_identity"))) == runner.BLOCKED


def _stub(**over) -> dict:
    base = dict(
        benchmark_id="b",
        case_id="c",
        arm="A",
        runtime_hash="r",
        driver_hash="d",
        runner_hash="n",
        oracle_hash="o",
        manifest_hash="m",
        baseline_tree_hash="t",
        requested_model="x",
    )
    base.update(over)
    return base


# ================================ 3. official scoring requires all 18 records


def test_the_official_evidence_set_is_six_cases_by_three_arms():
    manifest = frozen()
    pairs = runner.expected_pairs(manifest)
    assert len(manifest["cases"]) == 6
    assert runner.OFFICIAL_ARMS == ("A", "B", "C")
    assert len(pairs) == 18, f"official BM-07 is 18 case-arms, derived {len(pairs)}"
    assert {arm for _, arm in pairs} == {"A", "B", "C"}


def test_exactly_eighteen_compatible_records_may_score(tmp_path):
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    lay_down(results, manifest, all_official(manifest))

    status, problems = runner.official_status(results, manifest)

    assert status == runner.OFFICIAL_COMPLETE
    assert problems == []


def test_seventeen_of_eighteen_cannot_score(tmp_path):
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    pairs = all_official(manifest)
    lay_down(results, manifest, pairs[:-1])

    status, problems = runner.official_status(results, manifest)

    assert status == runner.INCOMPLETE_RUN
    assert any("no result record" in p for p in problems), problems


def test_a_duplicate_pair_standing_in_for_a_gap_cannot_score(tmp_path):
    """Eighteen rows is not eighteen case-arms."""
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    pairs = all_official(manifest)
    lay_down(results, manifest, pairs[:-1])
    lay_down(results, manifest, [pairs[0]])  # duplicate, total row count back to 18

    rows = results.read_text(encoding="utf-8").splitlines()
    status, problems = runner.official_status(results, manifest)

    assert len(rows) == 18
    assert status == runner.INCOMPLETE_RUN
    assert any("2 result records for one case-arm" in p for p in problems), problems
    assert any("no result record" in p for p in problems), problems


def test_an_unknown_case_is_an_invalid_run(tmp_path):
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    lay_down(results, manifest, all_official(manifest))
    lay_down(results, manifest, [("a-case-not-in-the-manifest", "A")])

    status, problems = runner.official_status(results, manifest)

    assert status == runner.INVALID_RUN
    assert any("not a manifest case" in p for p in problems), problems


def test_an_unknown_arm_is_an_invalid_run(tmp_path):
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    lay_down(results, manifest, all_official(manifest))
    lay_down(results, manifest, [(manifest["cases"][0]["case_id"], "D")])

    status, problems = runner.official_status(results, manifest)

    assert status == runner.INVALID_RUN
    assert any("not an official arm" in p for p in problems), problems


def test_a_terminal_state_without_a_result_is_detected(tmp_path):
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    pairs = all_official(manifest)
    lay_down(results, manifest, pairs[:-1])
    missing = pairs[-1]
    runner.write_state(results, missing[0], missing[1], runner.COMPLETED)

    status, problems = runner.official_status(results, manifest)

    assert status == runner.INCOMPLETE_RUN
    assert any(f"{missing[0]}/{missing[1]}: durable state" in p for p in problems), problems


def test_a_result_without_a_terminal_state_is_detected(tmp_path):
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    pairs = all_official(manifest)
    lay_down(results, manifest, pairs[:-1])
    orphan = pairs[-1]
    with results.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record_row(manifest, orphan[0], orphan[1]), sort_keys=True) + "\n")

    status, problems = runner.official_status(results, manifest)

    assert status == runner.INCOMPLETE_RUN
    assert any("result exists but durable state is" in p for p in problems), problems


def test_a_status_incompatible_with_its_terminal_state_is_detected(tmp_path):
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    pairs = all_official(manifest)
    lay_down(results, manifest, pairs[:-1])
    odd = pairs[-1]
    with results.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record_row(manifest, odd[0], odd[1], status="completed"), sort_keys=True) + "\n")
    runner.write_state(results, odd[0], odd[1], runner.BLOCKED)

    status, problems = runner.official_status(results, manifest)

    assert status == runner.INCOMPLETE_RUN
    assert any("incompatible with state" in p for p in problems), problems


def test_an_unreadable_result_row_cannot_score(tmp_path):
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    lay_down(results, manifest, all_official(manifest))
    with results.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("{not json\n")

    status, problems = runner.official_status(results, manifest)

    assert status == runner.INCOMPLETE_RUN
    assert any("unreadable" in p for p in problems), problems


def test_a_development_partial_run_is_named_and_does_not_score(tmp_path):
    """`--arms A` stays useful; it just cannot pass itself off as BM-07."""
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    lay_down(results, manifest, [(c["case_id"], "A") for c in manifest["cases"]])

    status, problems = runner.official_status(results, manifest, arms=("A",))

    assert status == runner.DEVELOPMENT_PARTIAL_RUN
    assert len(problems) == 12, problems
    assert status != runner.OFFICIAL_COMPLETE


def test_a_restricted_run_missing_a_requested_arm_is_incomplete_not_partial(tmp_path):
    """The partial label excuses arms never requested — nothing else."""
    manifest = frozen()
    results = tmp_path / "r.jsonl"
    lay_down(results, manifest, [(c["case_id"], "A") for c in manifest["cases"][:-1]])

    status, _ = runner.official_status(results, manifest, arms=("A",))

    assert status == runner.INCOMPLETE_RUN


def test_a_run_that_scores_nothing_returns_the_no_score_exit_code(case_repo, tmp_path, monkeypatch):
    """End to end: an incomplete evidence set refuses at the `run` boundary."""
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"

    let_arms_run(monkeypatch, m)

    # Only arm A of a three-arm manifest was requested.
    code = runner.run(path, repos, work, results, arms=("A",))

    assert code == runner.EXIT_NO_OFFICIAL_SCORE


def test_a_complete_run_reports_official_complete(case_repo, tmp_path, monkeypatch, capsys):
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"

    let_arms_run(monkeypatch, m)

    code = runner.run(path, repos, work, results, arms=("A", "B", "C"))
    out = capsys.readouterr().out

    assert code == runner.EXIT_OK
    assert runner.OFFICIAL_COMPLETE in out
    assert "3 of 3 case-arm records" in out


# ================================ 4. the existing identity checks still hold


def test_identity_drift_still_refuses_before_completeness_is_considered(case_repo, tmp_path):
    """Completeness is additional to identity, never a replacement for it."""
    _, case, _ = case_repo
    m, _ = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    lay_down(results, m, all_official(m))
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    rows[0]["oracle_hash"] = "a-different-oracle"
    results.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    _, drift = runner.aggregate(results, m)
    status, _ = runner.official_status(results, m)

    assert any("oracle_hash does not match" in d for d in drift), drift
    assert status == runner.OFFICIAL_COMPLETE, "completeness alone must not be treated as sufficient"


def test_official_scoring_needs_identity_and_completeness_together():
    """Structural: `run` consults both before printing a summary."""
    source = Path(runner.__file__).read_text(encoding="utf-8")
    body = source.split("def run(")[1]
    drift_at = body.index("identity drift detected")
    completeness_at = body.index("official_status(")
    summary_at = body.index('print(f"summary')
    assert drift_at < summary_at and completeness_at < summary_at
