"""The benchmark's arithmetic is itself checked.

A harness that quietly drops failures or reports against a manifest it did not
run is the same class of error the product exists to refuse, so the accounting
gets tests too.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))

import verify_bench  # noqa: E402


def _write(out: Path, cases: list[dict], records: list[dict], mismatch: bool = False) -> None:
    manifest = {"schema": 1, "arms": {}, "cases": cases}
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest["manifest_hash"] = hashlib.sha256(body.encode()).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    (out / "results.json").write_text(
        json.dumps(
            {"manifest_hash": "0" * 64 if mismatch else manifest["manifest_hash"], "records": records},
            indent=1,
        ),
        encoding="utf-8",
    )


def _case(cid: str, expected: bool, klass: str) -> dict:
    return {"case_id": cid, "repo": "r", "patch_class": klass, "expected_accept": expected}


def _record(cid: str, expected: bool, klass: str, s: bool, c: bool) -> dict:
    return {
        **_case(cid, expected, klass),
        "arm_S": {"accepted": s, "seconds": 1.0, "commands": 3},
        "arm_C": {"accepted": c, "seconds": 2.0, "commands": 4, "verdict": "x"},
    }


def test_report_refuses_a_manifest_mismatch(tmp_path, capsys):
    out = tmp_path / "frozen"
    _write(out, [_case("a", True, "correct")], [_record("a", True, "correct", True, True)], mismatch=True)
    code = verify_bench.cmd_report(argparse.Namespace(out=str(out)))
    assert code == 1
    assert "REFUSING TO REPORT" in capsys.readouterr().out


def test_report_recomputes_rates_from_raw_records(tmp_path, capsys):
    out = tmp_path / "frozen"
    cases = [_case("a", True, "correct"), _case("b", True, "correct"), _case("c", False, "inert")]
    records = [
        _record("a", True, "correct", True, True),
        _record("b", True, "correct", True, False),
        _record("c", False, "inert", True, False),
    ]
    _write(out, cases, records)
    assert verify_bench.cmd_report(argparse.Namespace(out=str(out))) == 0
    text = capsys.readouterr().out
    assert "correct-patch acceptance" in text
    # arm C accepted 1 of 2 correct patches, arm S accepted 2 of 2
    assert "50.0%" in text and "100.0%" in text
    # arm S accepted the inert patch; arm C did not
    assert "inert" in text


def test_errored_cases_are_excluded_and_disclosed_never_counted_as_passes(tmp_path, capsys):
    out = tmp_path / "frozen"
    cases = [_case("a", True, "correct"), _case("b", True, "correct")]
    records = [_record("a", True, "correct", True, True), {**_case("b", True, "correct"), "error": "prepare failed"}]
    _write(out, cases, records)
    verify_bench.cmd_report(argparse.Namespace(out=str(out)))
    text = capsys.readouterr().out
    assert "EXCLUDED (harness errors, not results): 1" in text
    assert "usable 1" in text
    assert "cases 2" in text


def test_retention_floor_is_reported_against_the_standard_arm(tmp_path, capsys):
    out = tmp_path / "frozen"
    cases = [_case(str(i), True, "correct") for i in range(10)]
    records = [_record(str(i), True, "correct", True, i < 9) for i in range(10)]
    _write(out, cases, records)
    verify_bench.cmd_report(argparse.Namespace(out=str(out)))
    text = capsys.readouterr().out
    assert "correct-patch retention of arm S: 90.0%" in text
    assert "acceptance floor: 90%" in text


@pytest.mark.parametrize(
    "output,node,expected",
    [
        ("PASSED tests/t.py::test_a\n", "tests/t.py::test_a", "PASSED"),
        ("FAILED tests/t.py::test_a - boom\n", "tests/t.py::test_a", "FAILED"),
        ("PASSED tests/t.py::test_ab\n", "tests/t.py::test_a", None),
        ("PASSED tests/t.py::test_b\nFAILED tests/t.py::test_a - x\n", "tests/t.py::test_a", "FAILED"),
    ],
)
def test_node_verdict_reads_the_targets_own_line(output, node, expected):
    """A neighbour's outcome must never be credited to the target."""
    assert verify_bench.node_verdict(output, node) == expected
