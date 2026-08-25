"""REPRESENTATION EXPERIMENT — the frozen 144-sample counterbalanced design.

PREPARATION ONLY. No provider call is made from this module.

24 cases x 3 repeats x 2 conditions = 144 samples. That number is not a sample
size in the statistical sense and this module is careful never to imply it is:
the unit of generalization stays at **24 cases**, and the three repeats measure
within-case stochastic variation, not 144 independent bugs.

Order is counterbalanced within each case so that a condition's advantage cannot
come from always being asked first. Each repeat is a pair — one U, one S — and
the pair is the unit the analysis differences over.
"""

from __future__ import annotations

import hashlib

REPEATS = 3
CONDITIONS = ("U", "S")
EXPECTED_SAMPLES = 144

DESIGN_NAME = "repeated counterbalanced representation mechanism experiment"
UNIT_OF_GENERALIZATION = "case"
CASES_EXPECTED = 24


def pair_id(case_id: str, repeat: int) -> str:
    """One U and one S share a pair id; the analysis differences within it."""
    return hashlib.sha256(f"representation-pair:{case_id}:{repeat}".encode()).hexdigest()[:16]


def sample_id(case_id: str, repeat: int, condition: str) -> str:
    return hashlib.sha256(f"representation-sample:{case_id}:{repeat}:{condition}".encode()).hexdigest()[:16]


def order_for(case_index: int, repeat: int) -> tuple[str, str]:
    """Counterbalanced request order.

    Alternating on `case_index + repeat` gives each case both orders across its
    three repeats and splits the corpus evenly at every repeat, so neither
    condition is systematically first for a case or for a round.
    """
    return ("U", "S") if (case_index + repeat) % 2 == 0 else ("S", "U")


def build(case_ids: list[str]) -> list[dict]:
    """Every expected sample identity, frozen before any provider response."""
    samples: list[dict] = []
    for case_index, case_id in enumerate(case_ids):
        for repeat in range(1, REPEATS + 1):
            order = order_for(case_index, repeat)
            for position, condition in enumerate(order, start=1):
                samples.append(
                    {
                        "sample_id": sample_id(case_id, repeat, condition),
                        "case_id": case_id,
                        "repeat": repeat,
                        "condition": condition,
                        "pair_id": pair_id(case_id, repeat),
                        "request_position": position,
                        "order_label": "->".join(order),
                    }
                )
    return samples


def schedule_problems(samples: list[dict], case_ids: list[str]) -> list[str]:
    """Everything that would make the schedule not the design it claims to be."""
    problems: list[str] = []
    if len(samples) != EXPECTED_SAMPLES:
        problems.append(f"expected {EXPECTED_SAMPLES} samples, found {len(samples)}")
    if len(case_ids) != CASES_EXPECTED:
        problems.append(f"expected {CASES_EXPECTED} cases, found {len(case_ids)}")

    ids = [s["sample_id"] for s in samples]
    if len(set(ids)) != len(ids):
        problems.append("duplicate sample_id in the schedule")

    for case_id in case_ids:
        for repeat in range(1, REPEATS + 1):
            pair = [s for s in samples if s["case_id"] == case_id and s["repeat"] == repeat]
            if len(pair) != 2:
                problems.append(f"{case_id} repeat {repeat}: {len(pair)} samples, expected 2")
                continue
            if {s["condition"] for s in pair} != {"U", "S"}:
                problems.append(f"{case_id} repeat {repeat}: not one U and one S")
            if len({s["pair_id"] for s in pair}) != 1:
                problems.append(f"{case_id} repeat {repeat}: pair_id is not shared")
            if {s["request_position"] for s in pair} != {1, 2}:
                problems.append(f"{case_id} repeat {repeat}: request positions are not 1 and 2")

    # Counterbalance: neither condition may be first appreciably more often.
    first_u = sum(1 for s in samples if s["condition"] == "U" and s["request_position"] == 1)
    first_s = sum(1 for s in samples if s["condition"] == "S" and s["request_position"] == 1)
    if first_u != first_s:
        problems.append(f"counterbalance broken: U first {first_u} times, S first {first_s}")
    return problems


def order_balance(samples: list[dict]) -> dict:
    return {
        "U_first": sum(1 for s in samples if s["condition"] == "U" and s["request_position"] == 1),
        "S_first": sum(1 for s in samples if s["condition"] == "S" and s["request_position"] == 1),
        "pairs": len({s["pair_id"] for s in samples}),
    }
