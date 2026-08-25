"""BM-07 runner identity: the program that spends the money is frozen too.

The identity chain bound the runtime, the evaluator (`bm07_driver.py`), the
ground-truth oracle and the experiment declaration. It did not bind
`bm07_runner.py` — the orchestration program that decides *when a provider call
happens*, how a restart behaves, and when a score may be produced.

That is the one component whose bytes could change without any recorded identity
changing. A modified runner could re-send a request a frozen one would have
refused, or score a set a frozen one would have rejected, and every hash in the
record would still match.

`runner_hash` is the exact bytes of `bm07_runner.py`. It is kept separate from
`driver_hash` rather than folded into a dependency digest: evaluating a candidate
and deciding to spend money are different authorities, they fail differently, and
they are audited separately.

It is enforced in three places, because one is not enough:

* **before the first request** — a mismatched runner never reaches an adapter;
* **in every arm record** — so evidence carries the identity that produced it;
* **before scoring** — so a runner swapped between execution and aggregation
  cannot quietly score someone else's run.

No provider is configured and no request leaves the process.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BM07 = Path(__file__).parents[1] / "benchmark" / "bm07"
if str(BM07) not in sys.path:
    sys.path.append(str(BM07))

import bm07_driver as driver  # noqa: E402
import bm07_runner as runner  # noqa: E402
import test_bm07_driver as driver_tests  # noqa: E402
import test_bm07_transactions as tx  # noqa: E402

case_repo = driver_tests.case_repo
manifest_for_run = driver_tests.manifest_for_run
minimal_manifest = driver_tests.minimal_manifest

OTHER = "b" * 64


# ------------------------------------------------- what runner_hash is


def test_runner_hash_is_the_exact_bytes_of_the_orchestration_file():
    """Not normalised, not derived from imports — the bytes that actually run."""
    import hashlib

    expected = hashlib.sha256((BM07 / "bm07_runner.py").read_bytes()).hexdigest()
    assert runner.runner_hash() == expected
    assert len(runner.runner_hash()) == 64


def test_runner_identity_is_separate_from_driver_identity():
    """Two authorities, two hashes. Folding them together would hide which moved."""
    assert runner.runner_hash() != driver.driver_hash()
    assert driver.driver_hash() == __import__("hashlib").sha256((BM07 / "bm07_driver.py").read_bytes()).hexdigest()


def test_the_frozen_manifest_records_the_observed_runner():
    manifest = json.loads((BM07 / "manifest-executable.json").read_text(encoding="utf-8"))
    assert manifest["runner_hash"] == runner.runner_hash()


# ------------------------------------------------- manifest validation


def test_a_missing_runner_hash_fails_validation(case_repo):
    _, case, _ = case_repo
    m = minimal_manifest(case)
    del m["runner_hash"]
    assert any("runner_hash" in p for p in driver.validate_manifest(m))


def test_a_bad_length_runner_hash_fails_validation(case_repo):
    _, case, _ = case_repo
    m = minimal_manifest(case)
    m["runner_hash"] = "abc123"
    assert any("64 lowercase hex" in p for p in driver.validate_manifest(m))


def test_a_non_hex_runner_hash_fails_validation(case_repo):
    _, case, _ = case_repo
    m = minimal_manifest(case)
    m["runner_hash"] = "z" * 64
    assert any("64 lowercase hex" in p for p in driver.validate_manifest(m))


def test_an_uppercase_runner_hash_fails_validation(case_repo):
    _, case, _ = case_repo
    m = minimal_manifest(case)
    m["runner_hash"] = "AB" * 32
    assert any("64 lowercase hex" in p for p in driver.validate_manifest(m))


def test_a_well_formed_runner_hash_is_accepted(case_repo):
    _, case, _ = case_repo
    assert driver.validate_manifest(minimal_manifest(case)) == []


def test_the_real_manifest_still_validates_with_zero_failures():
    manifest = json.loads((BM07 / "manifest-executable.json").read_text(encoding="utf-8"))
    assert driver.validate_manifest(manifest) == []
    assert manifest["manifest_hash"] == driver.manifest_hash(manifest)


# ------------------------------------------- checked before any spend


def test_a_runner_mismatch_is_caught_by_the_pre_spend_identity_check(case_repo, tmp_path):
    _, case, _ = case_repo
    m, _ = manifest_for_run(case, tmp_path)
    m["runner_hash"] = OTHER
    problems = runner.identity_problems(m)
    assert any("runner identity" in p for p in problems), problems


def test_a_runner_mismatch_makes_zero_adapter_calls(case_repo, tmp_path, monkeypatch):
    """The whole run stops. Not one arm, not a warning — no request at all."""
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    m["runner_hash"] = OTHER
    m["manifest_hash"] = driver.manifest_hash(m)
    path.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    code, calls = tx.counted_run(monkeypatch, path, repos, work, tmp_path / "r.jsonl")

    assert code == runner.EXIT_REFUSED
    assert calls == [], f"work happened under an unfrozen runner: {calls}"


def test_one_changed_byte_of_the_runner_aborts_the_paid_path(case_repo, tmp_path, monkeypatch):
    """The realistic shape: the manifest is untouched, the program is not."""
    repos, case, work = case_repo
    _, path = manifest_for_run(case, tmp_path)

    monkeypatch.setattr(runner, "runner_hash", lambda: OTHER)
    code, calls = tx.counted_run(monkeypatch, path, repos, work, tmp_path / "r.jsonl")

    assert code == runner.EXIT_REFUSED
    assert "adapter" not in calls


def test_the_runner_check_happens_before_preflight_touches_a_repository(case_repo, tmp_path, monkeypatch):
    repos, case, work = case_repo
    _, path = manifest_for_run(case, tmp_path)

    monkeypatch.setattr(runner, "runner_hash", lambda: OTHER)
    _, calls = tx.counted_run(monkeypatch, path, repos, work, tmp_path / "r.jsonl")

    assert "preflight" not in calls and "materialise" not in calls


def test_identity_is_checked_before_the_reconciliation_scan_and_the_loop():
    """Structural: order matters, because both must precede any adapter."""
    source = Path(runner.__file__).read_text(encoding="utf-8")
    body = source.split("def run(")[1]
    assert body.index("identity_problems(") < body.index("unreconciled(")
    assert body.index("unreconciled(") < body.index("driver.preflight(")


# --------------------------------------------- carried in every record


def test_every_arm_record_carries_the_runner_hash():
    assert "runner_hash" in runner.ArmRecord.__dataclass_fields__
    for name in ("runtime_hash", "driver_hash", "runner_hash", "oracle_hash", "manifest_hash"):
        assert name in runner.ArmRecord.__dataclass_fields__, name


def test_a_recorded_arm_carries_the_manifests_runner_hash(case_repo, tmp_path, monkeypatch):
    repos, case, work = case_repo
    m, path = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"

    tx.let_arms_run(monkeypatch, m)
    runner.run(path, repos, work, results, arms=("A",))

    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    assert rows and all(r["runner_hash"] == m["runner_hash"] for r in rows)


def test_the_durable_state_row_records_the_runner_that_wrote_it(tmp_path):
    results = tmp_path / "r.jsonl"
    runner.write_state(results, "c1", "A", runner.REQUEST_STARTED)
    row = json.loads(runner.state_path(results).read_text(encoding="utf-8").splitlines()[0])
    assert row["runner_hash"] == runner.runner_hash()


def test_a_state_row_without_the_stamp_still_replays(tmp_path):
    """The state machine is unchanged; older rows must not become unreadable."""
    results = tmp_path / "r.jsonl"
    path = runner.state_path(results)
    path.write_text(json.dumps({"case_id": "c1", "arm": "A", "state": runner.REQUEST_STARTED}) + "\n", encoding="utf-8")
    assert runner.load_states(results) == {("c1", "A"): runner.REQUEST_STARTED}
    assert runner.unreconciled(results) == [("c1", "A")]


# ------------------------------------------------ rechecked before scoring


def test_a_runner_changed_after_execution_refuses_to_score(case_repo, tmp_path, monkeypatch):
    _, case, _ = case_repo
    m, _ = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    tx.lay_down(results, m, tx.all_official(m))

    monkeypatch.setattr(runner, "runner_hash", lambda: OTHER)
    _, problems = runner.aggregate(results, m)

    assert any("orchestration program changed after execution" in p for p in problems), problems


def test_one_record_from_a_different_runner_blocks_the_official_score(case_repo, tmp_path):
    """17 agree, 1 does not. Every other hash matches, and it is still refused."""
    _, case, _ = case_repo
    m, _ = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    pairs = tx.all_official(m)
    tx.lay_down(results, m, pairs)

    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    rows[-1]["runner_hash"] = OTHER
    results.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    _, problems = runner.aggregate(results, m)

    assert len(rows) == len(pairs)
    assert all(r["oracle_hash"] == m["oracle_hash"] for r in rows)
    assert any("runner_hash does not match this run" in p for p in problems), problems


def test_a_record_missing_its_runner_hash_blocks_the_official_score(case_repo, tmp_path):
    _, case, _ = case_repo
    m, _ = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    tx.lay_down(results, m, tx.all_official(m))

    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    del rows[0]["runner_hash"]
    results.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    _, problems = runner.aggregate(results, m)
    assert any("runner_hash does not match this run" in p for p in problems), problems


def test_records_that_all_share_the_frozen_runner_are_accepted(case_repo, tmp_path):
    _, case, _ = case_repo
    m, _ = manifest_for_run(case, tmp_path)
    results = tmp_path / "r.jsonl"
    tx.lay_down(results, m, tx.all_official(m))

    _, problems = runner.aggregate(results, m)
    status, gaps = runner.official_status(results, m)

    assert problems == []
    assert status == runner.OFFICIAL_COMPLETE and gaps == []


def test_the_fake_run_evidence_shares_one_runner_hash():
    """The shipped 18-record evidence set, checked as a reviewer would."""
    results = BM07 / "fake-run-results.jsonl"
    manifest = json.loads((BM07 / "manifest-executable.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]

    assert len(rows) == 18
    assert {r["runner_hash"] for r in rows} == {manifest["runner_hash"]}
