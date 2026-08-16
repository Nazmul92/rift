"""M0 honesty regression tests.

These pin the two corrections made in M0 and the reconciliation that keeps the
documented figures tied to the raw artifacts:

* M0-01 a constant-false survivor (nothing in the action space changes the
  outcome) abstains as `unexplained_by_representation` instead of claiming an
  identification;
* M0-02 no diagnosis text derives a claim about the repository's source from
  the mere absence of an environmental explanation;
* M0-03 the results table in `RIFTCODE.md` is recomputed from
  `results/riftcode_demo.json` rather than transcribed.

The failure these guard against is specific and was actually made: an
exhausted action space was reported as a located cause, which is the same error
as accepting "the test passes now" as proof of a fix.
"""

from __future__ import annotations

import json
import re
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path

import pytest

from riftcode.contracts import (
    IDENTIFIED,
    STATUSES,
    UNEXPLAINED_BY_REPRESENTATION,
    Budget,
)
from riftcode.harness.injector import ORACLE_CAUSE, TARGET, build_repo
from riftcode.loop import localize, verify
from riftcode.sandbox import Sandbox

ROOT = Path(__file__).resolve().parents[1]

# Claims the agent side is not entitled to make from an empty action space.
# The evaluator-only injector may assert ground truth; a diagnosis may not.
SOURCE_CLAIMS = (
    "defect in the code",
    "defect is in the code",
    "code defect",
    "defect in the source",
    "bug in your code",
    "bug is in the code",
    "points at a defect",
    "stop looking at your environment",
)


def _sandbox(fault: str, budget: Budget | None = None, seed: int = 0) -> Sandbox:
    tmp = Path(tempfile.mkdtemp(prefix="tmpm0_"))
    build_repo(fault, tmp, seed)
    return Sandbox(tmp, budget or Budget(max_commands=60, max_seconds=240))


def _diagnose(fault: str):
    sb = _sandbox(fault)
    try:
        diag, _, _ = localize(sb, TARGET, rng_seed=0)
        gate = verify(sb, TARGET, diag)
        return diag, gate
    finally:
        sb.dispose()


# --------------------------------------------------------------------- M0-01


def test_exhausted_action_space_abstains_instead_of_identifying():
    """The regression itself: `code_defect` leaves `{"const": false}` standing.

    Before M0 this was reported as `identified`. Nothing was identified — the
    runtime ran out of handles.
    """
    diag, _ = _diagnose("code_defect")
    assert diag.status == UNEXPLAINED_BY_REPRESENTATION, diag.notes
    assert diag.cause is None
    assert diag.hypothesis is not None
    assert diag.hypothesis["condition"] == {"const": False}


def test_identified_status_never_stands_on_a_constant_false_survivor():
    """Structural invariant, independent of any one fault family."""
    for fault in ("code_defect", "env_gated"):
        diag, _ = _diagnose(fault)
        if diag.status == IDENTIFIED:
            assert diag.hypothesis is not None
            assert diag.hypothesis["condition"].get("const") is not False, fault
            oracle = ORACLE_CAUSE[fault]
            # An identification either names a handle or explains the outcome
            # by a mechanism the IR models (e.g. the retry counter).
            assert diag.cause is not None or oracle is None, fault


def test_abstention_status_is_in_the_declared_vocabulary():
    diag, _ = _diagnose("code_defect")
    assert diag.status in STATUSES
    assert UNEXPLAINED_BY_REPRESENTATION in STATUSES


def test_abstention_still_yields_a_not_applicable_gate_not_a_verified_one():
    """Abstention must not leak into the gate as a pass."""
    diag, gate = _diagnose("code_defect")
    assert diag.status == UNEXPLAINED_BY_REPRESENTATION
    assert gate["verdict"] == "not_applicable"
    assert gate["verdict"] != "verified"


# --------------------------------------------------------------------- M0-02


@pytest.mark.parametrize("fault", ["code_defect", "retry_flake"])
def test_diagnosis_text_claims_nothing_about_the_source(fault):
    """M0-02: the absence of an environmental explanation is not evidence of a
    source defect, and the diagnosis text must not say otherwise."""
    diag, _ = _diagnose(fault)
    blob = " ".join(diag.notes or []).lower()
    for claim in SOURCE_CLAIMS:
        assert claim not in blob, f"{fault}: diagnosis text asserts {claim!r}: {blob}"


def test_abstention_text_states_what_it_could_not_do():
    """An abstention that does not say why is not usable evidence."""
    diag, _ = _diagnose("code_defect")
    blob = " ".join(diag.notes or []).lower()
    assert "action space" in blob
    assert "cannot explain" in blob or "attributes nothing" in blob


# --------------------------------------------------------------------- M0-03

_TABLE_ROW = re.compile(r"^\|\s*(\w+)[^|]*\|\s*(\d+)/(\d+)\s*\|\s*([\d.]+)\s*\|")


def _documented_policy_rows() -> dict[str, tuple[int, int, float]]:
    rows: dict[str, tuple[int, int, float]] = {}
    for line in (ROOT / "RIFTCODE.md").read_text(encoding="utf-8").splitlines():
        m = _TABLE_ROW.match(line.strip())
        if m and m.group(1) in ("disagreement", "random", "cheapest"):
            rows[m.group(1)] = (int(m.group(2)), int(m.group(3)), float(m.group(4)))
    return rows


def _raw_policy_rows() -> dict[str, tuple[int, int, float]]:
    raw = json.loads((ROOT / "results" / "riftcode_demo.json").read_text(encoding="utf-8"))
    by: dict[str, list[dict]] = defaultdict(list)
    for rec in raw:
        by[rec["policy"]].append(rec)
    return {
        policy: (
            sum(1 for r in recs if r["correct"]),
            len(recs),
            round(statistics.mean(r["cmds"] for r in recs), 1),
        )
        for policy, recs in by.items()
    }


def test_riftcode_results_table_matches_raw_records():
    """M0-03: every published figure is recomputed from the artifact.

    The stale table this replaced (18/18 at 5.5, 12/18 at 9.8, 3/18 at 9.0)
    described a run that predated three fixes found by the real repository.
    """
    documented = _documented_policy_rows()
    raw = _raw_policy_rows()
    assert set(documented) == {"disagreement", "random", "cheapest"}, documented
    for policy, expected in raw.items():
        assert documented[policy] == expected, (
            f"{policy}: RIFTCODE.md says {documented[policy]}, "
            f"results/riftcode_demo.json says {expected}"
        )


def test_documented_run_count_matches_raw_records():
    raw = json.loads((ROOT / "results" / "riftcode_demo.json").read_text(encoding="utf-8"))
    text = (ROOT / "RIFTCODE.md").read_text(encoding="utf-8")
    assert f"= {len(raw)} runs" in text
    charged = sum(r["secs"] for r in raw)
    assert f"{charged:.1f}s of charged" in text


def test_documented_gate_split_matches_raw_records():
    raw = json.loads((ROOT / "results" / "riftcode_demo.json").read_text(encoding="utf-8"))
    disagreement = [r for r in raw if r["policy"] == "disagreement"]
    verified = sum(1 for r in disagreement if r["gate"] == "verified")
    not_applicable = sum(1 for r in disagreement if r["gate"] == "not_applicable")
    text = (ROOT / "RIFTCODE.md").read_text(encoding="utf-8")
    assert f"({verified} runs)" in text
    assert f"({not_applicable} runs)" in text
    assert verified + not_applicable == len(disagreement)


def test_documented_per_family_command_range_matches_raw_records():
    raw = json.loads((ROOT / "results" / "riftcode_demo.json").read_text(encoding="utf-8"))
    cmds = [r["cmds"] for r in raw if r["policy"] == "disagreement"]
    text = (ROOT / "RIFTCODE.md").read_text(encoding="utf-8")
    assert f"{min(cmds)}-{max(cmds)} commands each" in text
