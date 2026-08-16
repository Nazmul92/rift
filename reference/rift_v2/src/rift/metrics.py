"""Statistics and classification metrics."""

from __future__ import annotations

import math
import random
import statistics
from typing import Sequence


def summary(xs: Sequence[float]) -> dict[str, float]:
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": statistics.fmean(xs),
        "median": statistics.median(xs),
        "std": statistics.pstdev(xs) if len(xs) > 1 else 0.0,
    }


def bootstrap_ci(
    xs: Sequence[float], iters: int = 2000, seed: int = 0, alpha: float = 0.05
) -> tuple[float, float]:
    if not xs:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(iters))
    lo = means[int(alpha / 2 * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (lo, hi)


def paired_diff_ci(
    a: Sequence[float], b: Sequence[float], iters: int = 2000, seed: int = 0
) -> dict[str, object]:
    diffs = [x - y for x, y in zip(a, b)]
    return {
        "mean_diff": statistics.fmean(diffs) if diffs else float("nan"),
        "ci95": bootstrap_ci(diffs, iters, seed),
    }


def classification_report(tp: int, fp: int, tn: int, fn: int) -> dict[str, object]:
    pos = tp + fn
    neg = tn + fp
    sens = tp / pos if pos else 0.0
    spec = tn / neg if neg else 0.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = sens
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    denom = math.sqrt(max(1e-12, (tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)))
    mcc = (tp * tn - fp * fn) / denom
    return {
        "balanced_accuracy": (sens + spec) / 2,
        "precision": prec,
        "recall": rec,
        "f1": f1,
        "mcc": mcc,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }
