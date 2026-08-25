"""Provider identity is what the provider said, not what we asked for.

DAR-026 bound the manifest model to the configured model before spending, which
was right, and then read `spend.jsonl` → `pricing.model` as "the provider
reported model", which was not. That field is
`os.environ.get("RIFT_LLM_MODEL")` written back out by the runtime — our own
configuration. A run configured for `claude-sonnet-4-6` whose provider answered
with `claude-sonnet-5` would have re-read its own configuration and agreed with
itself.

The authoritative source is `model_reported` on each `MODEL_RESPONSE_RECEIVED`
event in the arm's own task ledger, which the adapter copies off the provider's
response. This is not hypothetical: a captured event from the aborted run reads
`{"model_reported": "claude-sonnet-5", "finish_reason": "length", ...}`.

Three identities stay distinct throughout:

    manifest_model           what the experiment declares
    configured_model         RIFT_LLM_MODEL, what was requested
    provider_reported_model  what came back

No provider call is made anywhere in this file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark" / "bm06"))

import driver as d  # noqa: E402

FOUR_SIX = "claude-sonnet-4-6"
FIVE = "claude-sonnet-5"


def task_ledger(repo: Path, task_id: str, reported: list[str | None]) -> Path:
    """A task ledger carrying one `MODEL_RESPONSE_RECEIVED` per entry."""
    path = repo / ".rift" / "tasks" / task_id / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = [{"kind": "task_started", "payload": {"task_id": task_id}}]
    for i, name in enumerate(reported):
        payload: dict = {"operation": "propose_change" if i == 0 else "propose_change_repair"}
        if name is not None:
            payload["model_reported"] = name
        rows.append({"kind": "model_response_received", "payload": payload})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def spend_ledger(repo: Path, scope: str, priced: str) -> Path:
    """A spend ledger priced under `priced` — our configuration, echoed back."""
    path = repo / ".rift" / "spend.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"scope": scope, "kind": "settled", "pricing": {"model": priced}}) + "\n",
        encoding="utf-8",
    )
    return path


# ------------------------------------------------------------------ Test A


def test_a_matching_provider_identity_is_accepted(tmp_path: Path):
    spend_ledger(tmp_path, "s", FOUR_SIX)
    task_ledger(tmp_path, "fix-abc-0001", [FOUR_SIX])

    reported = d.provider_reported_models(tmp_path, "fix-abc-0001")
    assert reported == [FOUR_SIX]
    assert d.provider_identity_failure(reported, FOUR_SIX) == ""


# ------------------------------------------------------------------ Test B


def test_pricing_says_four_six_while_the_provider_ledger_says_five(tmp_path: Path):
    """The critical regression.

    Configuration and price both say 4.6; the provider said 5. Reading
    `pricing.model` returns 4.6 and agrees with itself — which is exactly the
    defect. The provider ledger must be consulted and must disagree.
    """
    spend_ledger(tmp_path, "s", FOUR_SIX)
    task_ledger(tmp_path, "fix-abc-0001", [FIVE])

    # What the old implementation would have concluded, kept here to show the
    # two sources genuinely differ rather than asserting that they might.
    assert d.priced_models(tmp_path, "s") == [FOUR_SIX], "the configured/priced identity is not 4.6"

    reported = d.provider_reported_models(tmp_path, "fix-abc-0001")
    assert reported == [FIVE], "the provider ledger was not consulted"

    problem = d.provider_identity_failure(reported, FOUR_SIX)
    assert problem, "a run priced as 4.6 and served by 5 was accepted"
    assert FIVE in problem and FOUR_SIX in problem


# ------------------------------------------------------------------ Test C


def test_a_repair_served_by_a_different_model_fails_closed(tmp_path: Path):
    """A task whose first response matched and whose schema repair came from a
    different model is a task that ran on two models."""
    task_ledger(tmp_path, "fix-abc-0001", [FOUR_SIX, FIVE])

    reported = d.provider_reported_models(tmp_path, "fix-abc-0001")
    assert reported == [FOUR_SIX, FIVE], "not every response was inspected"
    assert d.provider_identity_failure(reported, FOUR_SIX), "the arm was accepted because the first response matched"


def test_every_response_is_returned_in_sequence(tmp_path: Path):
    task_ledger(tmp_path, "fix-abc-0001", [FOUR_SIX, FOUR_SIX, FOUR_SIX])
    assert d.provider_reported_models(tmp_path, "fix-abc-0001") == [FOUR_SIX] * 3
    assert d.provider_identity_failure([FOUR_SIX] * 3, FOUR_SIX) == ""


# ------------------------------------------------------------------ Test D


@pytest.mark.parametrize("absent", [None, "", "   "])
def test_an_absent_identity_is_recorded_as_unavailable(tmp_path: Path, absent):
    """A provider that does not identify itself has not agreed with anything.
    Substituting the model we asked for would fabricate the agreement."""
    task_ledger(tmp_path, "fix-abc-0001", [absent])

    reported = d.provider_reported_models(tmp_path, "fix-abc-0001")
    assert reported == ["unavailable"]
    assert FOUR_SIX not in reported, "the requested model was substituted for a missing one"
    # Absence is not a mismatch; it is the absence of a claim.
    assert d.provider_identity_failure(reported, FOUR_SIX) == ""


def test_a_mismatch_alongside_an_unavailable_still_fails(tmp_path: Path):
    task_ledger(tmp_path, "fix-abc-0001", [None, FIVE])
    reported = d.provider_reported_models(tmp_path, "fix-abc-0001")
    assert reported == ["unavailable", FIVE]
    assert d.provider_identity_failure(reported, FOUR_SIX)


# ------------------------------------------------------------------ Test E


def test_only_the_arms_own_task_is_evaluated(tmp_path: Path):
    """A benchmark run writes a ledger per arm per case. A check that swept all
    of them would attribute one arm's provider identity to another."""
    task_ledger(tmp_path, "task-a", [FOUR_SIX])
    task_ledger(tmp_path, "task-b", [FIVE])

    assert d.provider_reported_models(tmp_path, "task-a") == [FOUR_SIX]
    assert d.provider_identity_failure(d.provider_reported_models(tmp_path, "task-a"), FOUR_SIX) == ""
    # And the neighbouring task is neither ignored by accident nor conflated.
    assert d.provider_reported_models(tmp_path, "task-b") == [FIVE]


# ------------------------------------------------------------------ Test F


def test_an_unresolvable_task_fails_closed_rather_than_reporting_unavailable(tmp_path: Path):
    """`unavailable` is for a valid response with no identity in it. An arm
    whose evidence cannot be found is a different thing, and downgrading the
    first to the second is how a missing check reads like a passed one."""
    with pytest.raises(d.ModelIdentityUnresolved, match="not readable"):
        d.provider_reported_models(tmp_path, "fix-missing-0001")


def test_an_arm_with_no_task_id_fails_closed(tmp_path: Path):
    with pytest.raises(d.ModelIdentityUnresolved, match="no task_id"):
        d.provider_reported_models(tmp_path, "")


def test_a_corrupt_ledger_fails_closed(tmp_path: Path):
    path = tmp_path / ".rift" / "tasks" / "fix-abc-0001" / "ledger.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(d.ModelIdentityUnresolved):
        d.provider_reported_models(tmp_path, "fix-abc-0001")


def test_a_task_with_no_model_responses_yields_nothing(tmp_path: Path):
    """A model-free arm made no requests. That is not a failure and not an
    identity — the caller records `unavailable` for the row."""
    path = tmp_path / ".rift" / "tasks" / "fix-abc-0001" / "ledger.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"kind": "task_started", "payload": {}}) + "\n", encoding="utf-8")
    assert d.provider_reported_models(tmp_path, "fix-abc-0001") == []


# --------------------------------------------- the three identities stay apart


def test_the_priced_identity_is_named_for_what_it_is(tmp_path: Path):
    """`pricing.model` is the configured model echoed back by the runtime. It is
    still useful — it says what the price was applied to — but it is not
    provider evidence, and the function name now says so."""
    spend_ledger(tmp_path, "s", FOUR_SIX)
    assert d.priced_models(tmp_path, "s") == [FOUR_SIX]
    assert d.priced_models(tmp_path, "another-scope") == []
    assert not hasattr(d, "reported_models"), "the misleading name still exists"


def test_the_three_identities_are_separate_functions():
    """No single call answers all three questions, because they are three
    different questions with three different sources."""
    assert callable(d.configured_model)  # RIFT_LLM_MODEL
    assert callable(d.priced_models)  # spend ledger, our configuration
    assert callable(d.provider_reported_models)  # task ledger, the provider


def test_the_driver_never_treats_priced_models_as_provider_evidence():
    """Asserted on the source: `priced_models` must not feed the provider
    identity check."""
    import ast

    tree = ast.parse(Path(d.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "provider_identity_failure":
                first = node.args[0] if node.args else None
                assert not (
                    isinstance(first, ast.Call)
                    and isinstance(first.func, ast.Name)
                    and first.func.id == "priced_models"
                ), "the priced (configured) identity is being checked as though it were the provider's"


# ------------------------------ an arm that does not finish says so (DAR-028)


def test_a_streamed_event_is_not_mistaken_for_a_receipt():
    """`--json` streams every ledger event, so "the last JSON object" is the
    receipt only when the command completed.

    An arm that died mid-gate leaves the last *event* there instead, and taking
    it as the receipt reported the crash as whatever that event happened to
    lack — which is how a missing `task_id` came to stand in for "the arm never
    finished".
    """
    import subprocess

    streamed = "\n".join(
        json.dumps(row)
        for row in [
            {"kind": "task_started", "payload": {"task_id": "t1"}},
            {"kind": "changeset_registered", "payload": {"attempt": 1}},
        ]
    )
    proc = subprocess.CompletedProcess(args=["fake"], returncode=1, stdout=streamed, stderr="")
    assert d.receipt_of(proc) == {}, "a streamed event was accepted as a receipt"

    complete = streamed + "\n" + json.dumps({"verdict": "unverifiable", "task_id": "t1"})
    proc = subprocess.CompletedProcess(args=["fake"], returncode=2, stdout=complete, stderr="")
    assert d.receipt_of(proc)["verdict"] == "unverifiable"
    assert d.receipt_of(proc)["task_id"] == "t1"
