"""BM-07 end-to-end dry run: everything except the provider.

Exercises the full execution path against the real frozen manifest with a
deterministic fake adapter standing in for the model: manifest load, identity
and budget preflight, baseline construction and identity check, the candidate
pipeline (raw -> normalized -> canonical), then the three independent verdicts
and the result record.

The point is not to predict BM-07's findings. It is to prove the harness can run
and can write down bad news before any money is spent — and that the one
canonical candidate hash appears in all three verdict records.

No network. No provider. No spend.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "src"))

import bm07_driver as driver  # noqa: E402

from riftagent.records import canonicalize_patch, content_hash, normalize_candidate  # noqa: E402
from riftagent.sandbox import structural_parse  # noqa: E402

ROOT = pathlib.Path("/w")
REPOS = pathlib.Path("/repos")
WORK = pathlib.Path("/tmp/bm07-dry")
MANIFEST = ROOT / "benchmark/bm07/manifest-executable.json"
OUT = ROOT / "benchmark/bm07/dry-run.json"


def fake_model_response(case: dict, repos: pathlib.Path) -> str:
    """A deterministic stand-in for `propose_change`.

    Returns the project's own historical source fix, which is what a perfect
    model would produce. No provider is contacted and no token is spent; the
    upstream patch is never shown to a model anywhere in the real protocol, and
    is used here only because a dry run needs *some* candidate bytes.
    """
    repo = repos / case["repository"]
    return driver.run(["git", "show", "--format=", case["fix_commit"], "--", *_source_files(case, repo)], repo).stdout


def _source_files(case: dict, repo: pathlib.Path) -> list[str]:
    names = driver.run(["git", "show", "--numstat", "--format=", case["fix_commit"]], repo).stdout
    tests = set(case["test_files"])
    out = []
    for line in names.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2].endswith(".py") and parts[2] not in tests:
            out.append(parts[2])
    return out


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    WORK.mkdir(parents=True, exist_ok=True)
    report: dict = {"manifest_hash": manifest["manifest_hash"], "cases": [], "provider_calls": 0, "spend_usd": 0.0}

    problems = driver.validate_manifest(manifest)
    if problems:
        print(f"manifest invalid: {problems}")
        return 1
    print(f"manifest         : {len(manifest['cases'])} cases, 0 validation failures")

    # The pre-spend contract, exercised with the identities this tree actually has.
    budget_problems = driver.budget_preflight(
        manifest,
        configured_model=manifest["model"]["requested_model_id"],
        spent_usd=0.0,
        reserve_usd=manifest["budget"]["per_case_arm_max_usd"],
        observed_runtime_hash=manifest["runtime_hash"],
        observed_driver_hash=driver.driver_hash(),
    )
    print(f"budget preflight : {len(budget_problems)} failures {budget_problems if budget_problems else ''}")
    if budget_problems:
        return 1

    for case in manifest["cases"]:
        cid = case["case_id"]
        raw = fake_model_response(case, REPOS)
        if not raw.strip():
            print(f"  SKIP {cid}: fake adapter produced no candidate")
            continue

        # The shared proposal boundary, identical for every arm.
        normalized = normalize_candidate(raw)
        sr = structural_parse(normalized.diff)
        sc = structural_parse(normalized.diff, recount=True) if sr != 0 else 0
        canonical = canonicalize_patch(normalized.diff, sr, sc)
        canonical_hash = content_hash(canonical.diff.encode("utf-8"))

        verdicts = driver.evaluate_candidate(case, canonical.diff, REPOS, WORK)
        row = verdicts.to_dict()
        row["raw_candidate_hash"] = content_hash(raw.encode("utf-8"))
        row["normalized_candidate_hash"] = content_hash(normalized.diff.encode("utf-8"))
        row["canonical_candidate_hash"] = canonical_hash
        row["same_bytes_everywhere"] = (
            verdicts.weak_candidate_hash
            == verdicts.strong_candidate_hash
            == verdicts.truth_candidate_hash
            == canonical_hash
        )
        report["cases"].append(row)
        print(
            f"  {cid:22} weak={verdicts.weak_verdict:6} strong={verdicts.strong_verdict:6} "
            f"truth={row['ground_truth_verdict']:7} {verdicts.classification:38} "
            f"same_bytes={row['same_bytes_everywhere']}"
        )

    shutil.rmtree(WORK, ignore_errors=True)
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    all_same = all(r["same_bytes_everywhere"] for r in report["cases"])
    print(f"\ncases run        : {len(report['cases'])}")
    print(f"same canonical bytes in all three verdicts: {all_same}")
    print(f"provider calls   : {report['provider_calls']}")
    print(f"spend            : ${report['spend_usd']:.2f}")
    return 0 if (report["cases"] and all_same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
