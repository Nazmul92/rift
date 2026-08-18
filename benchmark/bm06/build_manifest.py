"""Assemble the BM-06 manifest from confirmed records plus applied label review.

Two inputs, deliberately separate:

* `stage2-*-records.json` — what was **executed**. A case is here only because a
  target was observed failing at the parent and passing at the fix.
* `label-decisions.json` — what was **judged**. Cause class, gateability,
  expected diagnostic scope, ground truth, and any dispute.

Neither input can produce a manifest alone, and this script will not invent the
missing half. A confirmed record with no label decision is reported as
unreviewed and excluded; a label decision naming a case that was never confirmed
is an error, not a case. That is the whole point of keeping them apart: a label
cannot be back-fitted to make an allocation come out right, because the
allocation is checked against the *executed* set.

Model-free. Makes no provider request and spends nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discover_cases  # noqa: E402 - the frozen clone urls live with the survey

URLS = dict(discover_cases.REPOS)

# selection-amendment.md
REPO_CAP = 4
MIN_REPOS = 10


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=300, errors="replace"
    )
    return proc.stdout


def source_files(repo: Path, parent: str, commit: str) -> list[str]:
    """The files the project's own fix touched, excluding its tests.

    This is ground truth for a correct repair — the answer key — so it is read
    from the repository rather than restated by hand.
    """
    changed = git(repo, "diff", "--name-only", parent, commit).splitlines()
    return sorted(f.strip() for f in changed if f.strip().endswith(".py") and not discover_cases._TEST_PATH.search(f))


def case_id(rec: dict) -> str:
    return f"{rec['repo']}-{rec['fix_commit'][:8]}-{rec['cause_class']}"


def build(records: list[dict], decisions: dict, work: Path, allocation: dict) -> tuple[list[dict], list[dict]]:
    """Returns (selected cases, excluded entries with reasons)."""
    confirmed = [r for r in records if r.get("accepted")]
    cases: list[dict] = []
    excluded: list[dict] = []

    for rec in confirmed:
        cid = case_id(rec)
        decision = decisions.get(cid)
        if decision is None:
            excluded.append({"case_id": cid, "reason": "confirmed but not label-reviewed"})
            continue
        if decision.get("withdraw"):
            excluded.append({"case_id": cid, "reason": decision.get("rationale", "withdrawn at label review")})
            continue
        repo = work / rec["repo"]
        cases.append(
            {
                "case_id": cid,
                "repo": rec["repo"],
                "url": URLS.get(rec["repo"], ""),
                "parent": rec["parent"],
                "commit": rec["fix_commit"],
                "target": rec["target"],
                "src_layout": rec.get("src_layout") or "flat",
                # Assigned by review before any arm runs, never derived from the
                # confirmation, because gateability is a judgement about whether
                # a safe apply/withdraw intervention exists — not about whether
                # a test failed.
                "label": decision["label"],
                "cause_class": decision["cause_class"],
                "expected_diagnostic_scope": decision.get("expected_diagnostic_scope"),
                "ordering_precondition": rec.get("ordering_precondition"),
                "signature": rec.get("signature"),
                "ground_truth": {
                    "fixable": decision["fixable"],
                    "source_files": source_files(repo, rec["parent"], rec["fix_commit"]),
                    "rationale": decision["rationale"],
                },
                "adversarial_review": decision.get("adversarial_review", {"required": not decision["fixable"]}),
                "preserve": decision.get("preserve", []),
                "status": decision.get("status", "OK"),
                "invalidation": decision.get("invalidation"),
                "confirmation": {
                    "parent_outcome": rec.get("parent_outcome"),
                    "fixed_outcome": rec.get("fixed_outcome"),
                    "isolated_at_parent": rec.get("isolated_at_parent"),
                    "target_origin": rec.get("target_origin"),
                    "container_image": rec.get("container_image"),
                    "commands": rec.get("commands", []),
                },
            }
        )

    # One commit, one class. Stage 1's marker sets overlap, so a single commit
    # message can match several classes and be confirmed into each of them —
    # filelock 059cca26 was confirmed as both order_dependence and
    # state_leakage. Shipping both would count one repair as two independent
    # cases and inflate two strata from the same evidence. The duplicate is
    # excluded here rather than left for a reader to notice, and which class
    # keeps it is a label-review decision, not a first-come accident.
    by_commit: dict[tuple[str, str], list[dict]] = {}
    for case in cases:
        by_commit.setdefault((case["repo"], case["commit"]), []).append(case)
    for (repo_name, commit), group in by_commit.items():
        if len(group) < 2:
            continue
        keep = next((c for c in group if decisions.get(c["case_id"], {}).get("primary_class")), None)
        if keep is None:
            for case in group:
                excluded.append(
                    {
                        "case_id": case["case_id"],
                        "reason": (
                            f"{repo_name} {commit[:8]} was confirmed into {len(group)} classes "
                            f"({', '.join(sorted(c['cause_class'] for c in group))}); label review must set "
                            f"primary_class on exactly one"
                        ),
                    }
                )
            cases = [c for c in cases if c not in group]
            continue
        for case in group:
            if case is not keep:
                excluded.append(
                    {"case_id": case["case_id"], "reason": f"duplicate commit; label review kept {keep['case_id']}"}
                )
        cases = [c for c in cases if c not in group or c is keep]

    # A decision for a case that was never confirmed is a bug in the review, not
    # a case. Surfaced loudly rather than silently ignored.
    confirmed_ids = {case_id(r) for r in confirmed}
    for cid in decisions:
        if cid not in confirmed_ids:
            excluded.append({"case_id": cid, "reason": "label decision names a case that was never confirmed"})

    cases.sort(key=lambda c: (c["cause_class"], c["repo"], c["commit"]))

    # The diversity cap from selection-amendment.md, enforced here rather than
    # trusted: a manifest that violates it is not a manifest. Order is the
    # amendment's — classes in allocation order, repositories in the frozen
    # order, candidates as stage 2 reached them — so which case is displaced is
    # decided by a rule written before any outcome, not by a later preference.
    per_repo: dict[str, int] = {}
    capped: list[dict] = []
    for case in cases:
        if per_repo.get(case["repo"], 0) >= REPO_CAP:
            excluded.append(
                {"case_id": case["case_id"], "reason": f"displaced by the {REPO_CAP}-cases-per-repository cap"}
            )
            continue
        per_repo[case["repo"]] = per_repo.get(case["repo"], 0) + 1
        capped.append(case)
    return capped, excluded


def distribution(cases: list[dict]) -> dict:
    per_class: dict[str, int] = {}
    per_repo: dict[str, int] = {}
    class_repos: dict[str, set] = {}
    for c in cases:
        if c["status"] != "OK":
            continue
        per_class[c["cause_class"]] = per_class.get(c["cause_class"], 0) + 1
        per_repo[c["repo"]] = per_repo.get(c["repo"], 0) + 1
        class_repos.setdefault(c["cause_class"], set()).add(c["repo"])
    return {
        "per_class": dict(sorted(per_class.items())),
        "per_repo": dict(sorted(per_repo.items())),
        "repos_per_class": {k: len(v) for k, v in sorted(class_repos.items())},
    }


def shortfalls(dist: dict, allocation: dict) -> list[str]:
    out = []
    for cls, need in allocation.items():
        have = dist["per_class"].get(cls, 0)
        if have < need:
            out.append(f"{cls}: {have}/{need}")
    if len(dist["per_repo"]) < MIN_REPOS:
        out.append(f"repositories: {len(dist['per_repo'])}/{MIN_REPOS}")
    if dist["repos_per_class"].get("order_dependence", 0) < 2:
        out.append(f"order_dependence repositories: {dist['repos_per_class'].get('order_dependence', 0)}/2")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(prog="build_manifest")
    parser.add_argument("--records", nargs="+", default=["benchmark/bm06/stage2-wide-records.json"])
    parser.add_argument("--decisions", default="benchmark/bm06/label-decisions.json")
    parser.add_argument("--allocation", default="benchmark/bm06/allocation.json")
    parser.add_argument("--work", default="/repos")
    parser.add_argument("--out", default="benchmark/bm06/manifest.json")
    args = parser.parse_args()

    records: list[dict] = []
    for path in args.records:
        records.extend(json.loads(Path(path).read_text(encoding="utf-8")))
    decisions = json.loads(Path(args.decisions).read_text(encoding="utf-8")) if Path(args.decisions).is_file() else {}
    allocation = json.loads(Path(args.allocation).read_text(encoding="utf-8"))["allocation"]

    cases, excluded = build(records, decisions, Path(args.work), allocation)
    dist = distribution(cases)
    missing = shortfalls(dist, allocation)

    protocol = Path("benchmark/bm06/PROTOCOL.md").read_bytes()
    model = json.loads(Path("benchmark/bm06/model-and-pricing.json").read_text(encoding="utf-8"))
    manifest = {
        "schema": 1,
        "protocol_sha256": hashlib.sha256(protocol).hexdigest(),
        "model": model.get("model", model),
        "budget": model.get("budget", {}),
        "arms": model.get("arms", {}),
        "allocation": allocation,
        "distribution": dist,
        "cases": cases,
        "excluded": excluded,
    }
    # The hash covers the cases, arms and model together: a case swapped after
    # freezing changes it, and the driver refuses to report against a manifest
    # hash that does not match the one stamped into its results.
    canonical = json.dumps(
        {k: manifest[k] for k in ("cases", "arms", "model", "budget", "allocation")},
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest["manifest_hash"] = hashlib.sha256(canonical.encode()).hexdigest()

    Path(args.out).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"cases selected : {len(cases)}")
    print(f"excluded       : {len(excluded)}")
    print(f"per class      : {dist['per_class']}")
    print(f"per repository : {dist['per_repo']}")
    print(f"manifest_hash  : {manifest['manifest_hash']}")
    if missing:
        print("\nNOT FROZEN — the frozen allocation is not met:")
        for row in missing:
            print(f"  short: {row}")
        print("\nWiden discovery again. Do not relabel a confirmed case to fill a class.")
        return 1
    print("\nAllocation met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
