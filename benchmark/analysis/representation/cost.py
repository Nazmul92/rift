"""REPRESENTATION EXPERIMENT — one authoritative settled-spend field.

PREPARATION ONLY. No provider call is made from this module.

BM-08 carried `actual_usd`; the exploratory probe wrote `cost_usd`. Two names for
settled spend is one too many — an aggregator that reads the wrong one reports a
number nobody paid. This fixes the authority before any paid run:

    reserved_usd    what was set aside before the request      NOT spend
    actual_usd      settled from provider-reported usage       AUTHORITATIVE
    estimated_usd   any pre-settlement guess                   NON-AUTHORITATIVE

Aggregation reads `actual_usd` and nothing else. `estimated_usd` may exist for
diagnostics but can never override, and `settled_spend` refuses a record that
carries only an estimate — a reservation is not a receipt.
"""

from __future__ import annotations

AUTHORITATIVE_FIELD = "actual_usd"
RESERVATION_FIELD = "reserved_usd"
NON_AUTHORITATIVE_FIELDS = ("estimated_usd", "cost_usd", "approx_usd")


class CostAuthorityError(ValueError):
    """A record whose settled spend cannot be established."""


def settled_spend(record: dict) -> float:
    """The one number that counts as money spent."""
    if AUTHORITATIVE_FIELD not in record:
        present = sorted(f for f in NON_AUTHORITATIVE_FIELDS if f in record)
        raise CostAuthorityError(
            f"no {AUTHORITATIVE_FIELD!r} in the record; "
            f"non-authoritative field(s) present: {present or 'none'} — an estimate is not settled spend"
        )
    value = record[AUTHORITATIVE_FIELD]
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise CostAuthorityError(f"{AUTHORITATIVE_FIELD!r} is not numeric")
    if value < 0:
        raise CostAuthorityError(f"{AUTHORITATIVE_FIELD!r} is negative")
    return float(value)


def total_settled(records: list[dict]) -> float:
    return sum(settled_spend(r) for r in records)


def cost_field_problems(records: list[dict]) -> list[str]:
    """Refuse anything that could let an estimate stand in for settled spend."""
    problems: list[str] = []
    for record in records:
        label = f"{record.get('case_id')}/{record.get('repeat')}/{record.get('condition')}"
        try:
            settled_spend(record)
        except CostAuthorityError as exc:
            problems.append(f"{label}: {exc}")
            continue
        for field in NON_AUTHORITATIVE_FIELDS:
            if field in record and record[field] != record[AUTHORITATIVE_FIELD]:
                problems.append(f"{label}: {field!r} disagrees with {AUTHORITATIVE_FIELD!r} and must never override it")
    return problems


def reservation_per_request(max_input_tokens: int, max_output_tokens: int, pricing: dict) -> float:
    """Worst-case cost of one request under the frozen token ceilings."""
    return max_input_tokens / 1e6 * pricing["input_per_mtok"] + max_output_tokens / 1e6 * pricing["output_per_mtok"]


def worst_case_study(
    *,
    cases: int,
    repeats: int,
    conditions: int,
    max_requests_per_sample: int,
    max_input_tokens: int,
    max_output_tokens: int,
    pricing: dict,
) -> dict:
    """The exact dollar requirement, derived rather than guessed."""
    per_request = reservation_per_request(max_input_tokens, max_output_tokens, pricing)
    per_sample = per_request * max_requests_per_sample
    samples = cases * repeats * conditions
    total = per_sample * samples
    return {
        "per_request_usd": round(per_request, 6),
        "per_sample_reservation_usd": round(per_sample, 6),
        "samples": samples,
        "total_worst_case_usd": round(total, 4),
        "recommended_authorization_ceiling_usd": float(int(total + 1.0)),
        "derivation": (
            f"{cases} cases x {repeats} repeats x {conditions} conditions x "
            f"{max_requests_per_sample} requests x ${per_request:.6f} = ${total:.4f}"
        ),
        "authorized": False,
        "spent": 0.0,
    }
