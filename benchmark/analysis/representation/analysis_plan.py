"""REPRESENTATION EXPERIMENT — the preregistered analysis plan.

PREPARATION ONLY. No provider call is made from this module.

Frozen before any sample exists, because an analysis chosen after seeing the
data is not an analysis, it is a search.

The single most important line here is the clustering rule. 144 samples come
from 24 cases, so treating them as 144 independent Bernoulli trials would shrink
the interval by roughly the square root of six for no reason other than counting
the same bugs repeatedly. Every interval and every test below resamples **cases**,
never samples.

The design is called a *repeated counterbalanced representation mechanism
experiment*, not a powered study. `detectable_effect()` says plainly what it can
and cannot separate.
"""

from __future__ import annotations

import hashlib
import json
import math

PRIMARY_ESTIMAND = "Paired difference in truth-correct probability, S minus U, on the 24 frozen BM-08 mechanism cases."
UNIT_OF_GENERALIZATION = "case (n = 24); the 3 repeats estimate within-case stochastic variation"
PAIRING = "case x repeat; each pair holds exactly one U and one S, with request order counterbalanced"

TRUTH_OUTCOME_RULE = {
    "truth_correct_candidate": 1,
    "truth_wrong_candidate": 0,
    "schema_invalid_after_governed_repair": 0,
    "no_candidate": 0,
    "non_applicable_candidate": 0,
    "target_failing_candidate": 0,
    "oracle_wrong": 0,
    "note": (
        "Failed generations stay in the primary denominator. A representation that "
        "cannot produce an applicable candidate has failed at the thing being measured, "
        "and dropping those samples would score each condition only on the samples it "
        "already survived."
    ),
}

MISSINGNESS_RULE = (
    "If the oracle cannot return a verdict for infrastructure reasons — sandbox failure, "
    "repository resolution, isolation unavailable — the sample is classified "
    "INFRASTRUCTURE_FAILURE, is not silently scored 0, and the run stops for reconciliation. "
    "Scientific outcomes and infrastructure failures are never merged."
)

CI_METHOD = {
    "name": "case-cluster bootstrap of the paired S-U difference",
    "resample": "cases with replacement, n = 24; all repeats of a resampled case travel with it",
    "iterations": 10000,
    "interval": "percentile, two-sided 95%",
    "companion_test": "case-level sign-flip permutation on per-case mean paired differences, 10000 iterations",
    "rationale": (
        "Both respect the cluster. Treating 72 U and 72 S observations as independent would "
        "understate the interval by roughly sqrt(3) and invent precision the design does not have."
    ),
}

MINIMUM_EFFECT_OF_INTEREST = 0.15
MINIMUM_EFFECT_RATIONALE = (
    "BM-08 measured 5 of 24 truth-correct for arm A and 3 of 24 for arm C. An absolute "
    "improvement smaller than 0.15 — under four cases in 24 — would not change any decision "
    "about whether to move patch metadata out of the model, so it is not the effect this "
    "study is built to find."
)

STRATIFICATION = {
    "variable": "historical_fix_region_coverage",
    "levels": ["COVERED", "PARTIALLY_COVERED", "NOT_COVERED"],
    "role": "preregistered stratification / explanatory variable",
    "not": (
        "not a solvability claim, not an exclusion criterion, and not a stop rule; "
        "NOT_COVERED means the model was not shown this known fix region, and the "
        "audit already contains one NOT_COVERED case that BM-08 nonetheless got "
        "truth-correct"
    ),
    "reporting": "primary estimand reported overall and within each coverage level",
}

SECONDARY_METRICS = (
    "u_raw_applicable",
    "u_canonical_applicable",
    "s_exact_source_valid",
    "s_compile_success",
    "s_compiled_applicable",
    "s_canonical_applicable",
    "target_pass",
    "truth_correct",
    "false_accept",
    "correct_but_rejected",
    "input_tokens",
    "output_tokens",
    "requests",
    "actual_usd",
    "truth_correct_per_dollar",
    "historical_fix_region_coverage",
)

SCOPED_CLAIM = (
    "On the previously observed BM-08 mechanism corpus, repeated counterbalanced sampling "
    "showed higher truth-correct proposal yield under deterministic exact-edit compilation "
    "than direct unified-diff generation."
)
FORBIDDEN_CLAIM = (
    "Search/replace generally makes RIFT better. A fresh unseen corpus is required for any claim of that shape."
)


def plan_hash() -> str:
    return hashlib.sha256((json.dumps(as_dict(), indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()


def as_dict() -> dict:
    return {
        "design_name": "repeated counterbalanced representation mechanism experiment",
        "primary_estimand": PRIMARY_ESTIMAND,
        "unit_of_generalization": UNIT_OF_GENERALIZATION,
        "pairing": PAIRING,
        "truth_outcome_rule": TRUTH_OUTCOME_RULE,
        "missingness_rule": MISSINGNESS_RULE,
        "ci_method": CI_METHOD,
        "minimum_effect_of_interest": MINIMUM_EFFECT_OF_INTEREST,
        "minimum_effect_rationale": MINIMUM_EFFECT_RATIONALE,
        "stratification": STRATIFICATION,
        "secondary_metrics": list(SECONDARY_METRICS),
        "scoped_claim": SCOPED_CLAIM,
        "forbidden_claim": FORBIDDEN_CLAIM,
        "powered": False,
        "powered_note": (
            "Not called powered. 144 samples cluster into 24 cases; see detectable_effect() "
            "for what the design can and cannot separate."
        ),
    }


# ------------------------------------------------------------------- estimation


def per_case_differences(samples: list[dict]) -> dict[str, float]:
    """Mean paired (S - U) truth outcome for each case."""
    pairs: dict[str, dict[str, int]] = {}
    for sample in samples:
        pairs.setdefault(sample["pair_id"], {})[sample["condition"]] = int(bool(sample["truth_correct"]))
    by_case: dict[str, list[float]] = {}
    case_of = {s["pair_id"]: s["case_id"] for s in samples}
    for pair_id, sides in pairs.items():
        if {"U", "S"} <= set(sides):
            by_case.setdefault(case_of[pair_id], []).append(float(sides["S"] - sides["U"]))
    return {case: sum(values) / len(values) for case, values in by_case.items() if values}


def bootstrap_interval(differences: dict[str, float], iterations: int = 10000, seed: int = 20260824) -> dict:
    """Percentile interval from resampling CASES, never individual samples."""
    cases = sorted(differences)
    if not cases:
        return {"point": None, "low": None, "high": None, "cases": 0}
    values = [differences[c] for c in cases]
    point = sum(values) / len(values)

    # Deterministic LCG: the plan is frozen, so the interval must reproduce.
    state = seed
    n = len(cases)
    draws: list[float] = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(n):
            state = (1103515245 * state + 12345) % (2**31)
            total += values[state % n]
        draws.append(total / n)
    draws.sort()
    low = draws[int(0.025 * len(draws))]
    high = draws[min(len(draws) - 1, int(0.975 * len(draws)))]
    return {"point": point, "low": low, "high": high, "cases": n, "iterations": iterations}


def sign_flip_p_value(differences: dict[str, float], iterations: int = 10000, seed: int = 20260824) -> float:
    """Case-level permutation. Each case's mean difference flips sign as a unit."""
    values = [differences[c] for c in sorted(differences)]
    if not values:
        return float("nan")
    observed = abs(sum(values) / len(values))
    state = seed
    extreme = 0
    for _ in range(iterations):
        total = 0.0
        for value in values:
            state = (1103515245 * state + 12345) % (2**31)
            total += value if state % 2 else -value
        if abs(total / len(values)) >= observed:
            extreme += 1
    return (extreme + 1) / (iterations + 1)


def detectable_effect(cases: int = 24, repeats: int = 3, baseline_rate: float = 0.15) -> dict:
    """What this design can meaningfully separate — stated before spending.

    A rough two-sided normal approximation on the case-level paired difference.
    It is deliberately not dressed up as a power calculation: with 24 clusters
    the honest summary is that only a large effect is distinguishable.
    """
    # Per-case difference variance, allowing for within-case repeat averaging.
    per_case_sd = math.sqrt(2 * baseline_rate * (1 - baseline_rate) / repeats)
    standard_error = per_case_sd / math.sqrt(cases)
    mde = 2.8 * standard_error  # ~alpha 0.05, power ~0.8, two-sided
    return {
        "cases": cases,
        "repeats": repeats,
        "assumed_baseline_truth_rate": baseline_rate,
        "approx_standard_error": round(standard_error, 4),
        "approximate_detectable_difference": round(mde, 3),
        "minimum_effect_of_interest": MINIMUM_EFFECT_OF_INTEREST,
        "design_can_distinguish_minimum_effect": bool(mde <= MINIMUM_EFFECT_OF_INTEREST),
        "verdict": (
            "The design can distinguish an effect at or above the minimum effect of interest."
            if mde <= MINIMUM_EFFECT_OF_INTEREST
            else "The design CANNOT reliably distinguish the minimum effect of interest; "
            "report as a repeated mechanism experiment, not a powered study."
        ),
        "note": (
            "Approximate. Reported so the limitation is visible before authorization rather "
            "than discovered afterwards. Repeat count is not changed automatically."
        ),
    }
