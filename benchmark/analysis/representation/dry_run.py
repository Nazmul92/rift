"""REPRESENTATION EXPERIMENT — 144-sample model-free dry run. $0.00, NO PROVIDER.

Traverses every expected sample identity through the *same* runner a paid study
would use, with a deterministic provider double in place of the network. The
point is not that 144 rows appear; it is that the transaction state machine, the
schema-repair rule, the compiler, the cost fields, the persistence order and the
144/144 completeness check all execute on the path that will later spend money.

The double is scripted per sample so the fault cases are exercised rather than
hoped for: valid responses, one-repair recoveries, twice-invalid abandonment,
and each compiler rejection class. The faults are injected by *response content*,
exactly as a real model failure would arrive.

Nothing here opens a socket.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
from collections import Counter

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

import analysis_plan  # noqa: E402
import cost as cost_module  # noqa: E402
import rep_runner  # noqa: E402
import rep_transactions as tx  # noqa: E402

MANIFEST = HERE / "representation-manifest.json"
WORK = pathlib.Path("/tmp/representation-dry-run")
RESULTS = WORK / "dry-run-results.jsonl"
LEDGER = WORK / "dry-run-ledger.jsonl"
REPORT = HERE / "dry-run-report.json"

BASELINE_FILES = {
    "pkg/module.py": "def compute(value):\n    return value + 1\n\n\ndef helper(x):\n    return x * 2\n",
    "pkg/other.py": "CONST = 3\n",
}

# Fault plan, keyed by (repeat, condition). Every governed failure mode appears.
FAULTS = {
    (1, "U"): "valid",
    (1, "S"): "valid",
    (2, "U"): "repair_then_valid",
    (2, "S"): "repair_then_valid",
    (3, "U"): "invalid_twice",
    (3, "S"): "search_not_found",
}
# Extra S faults rotate across cases so ambiguity and overlap are both covered.
S_ROTATION = ("search_not_found", "search_ambiguous", "search_overlap", "path_not_found", "invalid_twice", "valid")


def envelope(text: str, model: str, prompt_tokens: int = 1200, completion_tokens: int = 180) -> tuple[bytes, dict]:
    parsed = {
        "model": model,
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }
    return json.dumps(parsed).encode("utf-8"), parsed


VALID_U = json.dumps(
    {
        "diff": (
            "--- a/pkg/module.py\n"
            "+++ b/pkg/module.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def compute(value):\n"
            "-    return value + 1\n"
            "+    return value + 2\n"
        )
    }
)
VALID_S = json.dumps(
    {"edits": [{"path": "pkg/module.py", "search": "return value + 1", "replace": "return value + 2"}]}
)
S_NOT_FOUND = json.dumps({"edits": [{"path": "pkg/module.py", "search": "return value + 99", "replace": "x"}]})
S_AMBIGUOUS = json.dumps({"edits": [{"path": "pkg/module.py", "search": "return ", "replace": "return  "}]})
S_OVERLAP = json.dumps(
    {
        "edits": [
            {"path": "pkg/module.py", "search": "def compute(value):\n    return value + 1", "replace": "A"},
            {"path": "pkg/module.py", "search": "return value + 1", "replace": "B"},
        ]
    }
)
S_PATH = json.dumps({"edits": [{"path": "pkg/missing.py", "search": "anything", "replace": "x"}]})
GARBAGE = "I cannot produce that."


def make_provider(plan: str, model: str, mismatch: bool = False):
    """A deterministic double. Faults arrive as response content, like real ones."""
    state = {"calls": 0}

    def provider(condition: str, prompt: str, max_output_tokens: int) -> tuple[bytes, dict]:
        state["calls"] += 1
        reported = "some-other-model" if mismatch else model
        first = state["calls"] == 1
        if plan == "valid":
            return envelope(VALID_U if condition == "U" else VALID_S, reported)
        if plan == "repair_then_valid":
            if first:
                return envelope(GARBAGE, reported)
            return envelope(VALID_U if condition == "U" else VALID_S, reported)
        if plan == "invalid_twice":
            return envelope(GARBAGE, reported)
        if plan == "search_not_found":
            return envelope(VALID_U if condition == "U" else S_NOT_FOUND, reported)
        if plan == "search_ambiguous":
            return envelope(VALID_U if condition == "U" else S_AMBIGUOUS, reported)
        if plan == "search_overlap":
            return envelope(VALID_U if condition == "U" else S_OVERLAP, reported)
        if plan == "path_not_found":
            return envelope(VALID_U if condition == "U" else S_PATH, reported)
        raise AssertionError(f"unknown plan {plan!r}")

    return provider


def fake_oracle(record: dict) -> dict:
    """Deterministic stand-in for the frozen downstream pipeline.

    Alternates truth by pair so the analysis path sees both outcomes; the dry run
    tests the interface and the plumbing, never a scientific result.
    """
    truthy = int(record["pair_id"][:2], 16) % 3 == 0
    return {
        "target_pass": truthy,
        "truth_correct": truthy,
        "outcome_class": "TRUTH_CORRECT" if truthy else "TRUTH_WRONG",
        "oracle_stub": True,
    }


def build_baseline(root: pathlib.Path) -> pathlib.Path:
    tree = root / "baseline"
    shutil.rmtree(tree, ignore_errors=True)
    for name, body in BASELINE_FILES.items():
        target = tree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return tree


def main() -> int:
    print(rep_runner.BANNER)
    print("144-SAMPLE MODEL-FREE DRY RUN — NO PROVIDER, $0.00")
    print("=" * 60)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    model = manifest["model"]["requested_model_id"]

    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    baseline = build_baseline(WORK)
    ledger = tx.StudyLedger(LEDGER)

    entries = {e["case_id"]: e for e in manifest["cases"]}
    case_order = {e["case_id"]: i for i, e in enumerate(manifest["cases"])}
    results: list[dict] = []
    faults = Counter()
    spent = 0.0

    for sample in manifest["samples"]:
        entry = entries[sample["case_id"]]
        plan = FAULTS[(sample["repeat"], sample["condition"])]
        if sample["condition"] == "S" and plan == "search_not_found":
            # Rotate by CASE, not by global sample index. Repeat-3 S sits at a
            # fixed offset inside every six-sample case block, so indexing by
            # position landed on the same rotation entry every time and the
            # ambiguity, overlap and path faults were never exercised at all.
            plan = S_ROTATION[case_order[sample["case_id"]] % len(S_ROTATION)]
        faults[f"{sample['condition']}:{plan}"] += 1
        provider = make_provider(plan, model)
        work = WORK / sample["sample_id"]
        work.mkdir(parents=True, exist_ok=True)
        record = rep_runner.run_sample(
            manifest=manifest,
            entry={**entry, "context_hash": entry["context_hash"]},
            sample=sample,
            context="--- pkg/module.py (lines 1-6) ---\n" + BASELINE_FILES["pkg/module.py"],
            baseline=baseline,
            ledger=ledger,
            provider=provider,
            work=work,
            oracle=fake_oracle,
            spent_so_far=spent,
        )
        rep_runner.persist(RESULTS, ledger, record)
        results.append(record)
        spent += record["actual_usd"]
        shutil.rmtree(work, ignore_errors=True)

    expected = {(s["case_id"], s["repeat"], s["condition"]) for s in manifest["samples"]}
    problems = tx.completeness_problems(results, manifest["representation_experiment_manifest_hash"], expected)
    cost_problems = cost_module.cost_field_problems(results)
    ledger.require_reconciled()

    differences = analysis_plan.per_case_differences(results)
    interval = analysis_plan.bootstrap_interval(differences, iterations=2000)
    p_value = analysis_plan.sign_flip_p_value(differences, iterations=2000)

    print(f"\nsamples run       : {len(results)} of {tx.EXPECTED_RESULTS}")
    print(f"completeness      : {'PASS' if not problems else problems[:3]}")
    print(f"cost-field checks : {'PASS' if not cost_problems else cost_problems[:3]}")
    print(f"unreconciled      : {len(ledger.unreconciled())}")
    print(f"settled spend     : ${cost_module.total_settled(results):.4f} (simulated usage, no provider)")
    print("\nfault plans exercised:")
    for name, count in sorted(faults.items()):
        print(f"  {name:32} {count}")
    print("\noutcome classes:")
    for name, count in Counter(r.get("outcome_class", "?") for r in results).most_common():
        print(f"  {name:32} {count}")
    print("\ncompile status (S only):")
    for name, count in Counter(r.get("compile_status", "-") for r in results if r["condition"] == "S").most_common():
        print(f"  {name:32} {count}")
    print("\nanalysis path (stub outcomes; not a scientific result):")
    print(f"  cases with paired differences : {interval['cases']}")
    print(f"  point estimate S-U            : {interval['point']}")
    print(f"  95% case-cluster bootstrap    : [{interval['low']}, {interval['high']}]")
    print(f"  case-level sign-flip p        : {p_value:.4f}")

    report = {
        "label": "144-SAMPLE MODEL-FREE DRY RUN — NO PROVIDER — $0.00",
        "representation_experiment_manifest_hash": manifest["representation_experiment_manifest_hash"],
        "samples": len(results),
        "completeness_problems": problems,
        "cost_field_problems": cost_problems,
        "unreconciled_requests": len(ledger.unreconciled()),
        "fault_plans": dict(faults),
        "outcome_classes": dict(Counter(r.get("outcome_class", "?") for r in results)),
        "compile_status_s": dict(Counter(r.get("compile_status", "-") for r in results if r["condition"] == "S")),
        "analysis_path": {"interval": interval, "sign_flip_p": p_value},
        "provider_calls": 0,
        "additional_spend_usd": 0.0,
    }
    REPORT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    ok = not problems and not cost_problems and not ledger.unreconciled()
    print(f"\nDRY RUN: {'PASS' if ok else 'FAIL'}   -> {REPORT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
