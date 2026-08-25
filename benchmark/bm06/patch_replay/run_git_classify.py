"""Recompute the classification using git, against each case's frozen baseline."""

from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import git_classify as G  # noqa: E402

ROOT = pathlib.Path("/w")
results = json.loads((ROOT / "benchmark/bm06/results.json").read_text(encoding="utf-8"))
manifest = json.loads((ROOT / "benchmark/bm06/manifest-preliminary.json").read_text(encoding="utf-8"))
cases = {c["case_id"]: c for c in manifest["cases"]}

rows = []
for rec in results["records"]:
    if rec.get("failed_phase") != "candidate":
        continue
    case = cases[rec["case_id"]]
    worktree = pathlib.Path(case["worktree"])
    patch = pathlib.Path(str(rec["patch"]).replace("/w/", str(ROOT) + "/"))
    raw = patch.read_bytes()

    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    verdict = G.classify(worktree, patch)
    rows.append(
        {
            "case": rec["case_id"],
            "arm": rec["arm"],
            "cause_class": rec["cause_class"],
            "patch": patch.name,
            "original_candidate_sha256": hashlib.sha256(raw).hexdigest(),
            "baseline_head": head,
            "baseline_head_matches_pin": head == case["parent"],
            "frozen_baseline_tree_hash": rec.get("baseline_tree_hash"),
            **verdict,
        }
    )

out = ROOT / "benchmark/bm06/patch_replay/classification.json"
out.write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8")

print(f"candidate failures : {len(rows)}")
for k, n in sorted(collections.Counter(r["classification"] for r in rows).items()):
    print(f"  {k:26} {n}")
rec_ok = sum(1 for r in rows if r["recount_applies"])
print(f"  applies under --recount alone: {rec_ok}")
print(f"  baseline HEAD == pinned parent: {sum(1 for r in rows if r['baseline_head_matches_pin'])}/{len(rows)}")
print()
for r in rows:
    flag = "RECOUNT-OK" if r["recount_applies"] else ""
    print(f"  {r['arm']}  {r['case'][:32]:32} {r['classification'][:24]:24} {flag:11} {r['git_error'][:44]}")
