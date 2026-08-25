"""The failure funnel and per-arm counterfactual yield. POST-HOC DIAGNOSTIC."""

from __future__ import annotations

import collections
import json
import pathlib

ROOT = pathlib.Path("/w")
res = json.loads((ROOT / "benchmark/bm06/results.json").read_text(encoding="utf-8"))
rep = json.loads((ROOT / "benchmark/bm06/patch_replay/replay.json").read_text(encoding="utf-8"))
recs = res["records"]
by = {(r["case"], r["arm"]): r for r in rep}

total = len(recs)
fails = [x for x in recs if x.get("failed_phase") == "candidate"]
passed = total - len(fails)

struct = [r for r in rep if r.get("git_classification") == "STRUCTURALLY_INVALID"]
parse = [r for r in rep if r.get("git_classification") == "PARSEABLE_NON_APPLICABLE"]
safe = [r for r in struct if r.get("normalization") in ("NORMALIZED", "ALREADY_VALID")]
unsafe = [r for r in struct if r.get("normalization") == "NORMALIZATION_UNSAFE"]
applied = [r for r in safe if r.get("applies_after_normalization")]
non_app = [r for r in safe if not r.get("applies_after_normalization")]
verified = [r for r in applied if r.get("replay_verdict") == "verified_against_approved_checks"]
gate_fail = [r for r in applied if r.get("replay_verdict") != "verified_against_approved_checks"]

print(f"{total} total arm-runs")
print("│")
print(f"├── {passed} reached the acceptance path originally")
print("│")
print(f"└── {len(fails)} candidate-phase failures")
print("     │")
print(f"     ├── {len(struct)} structurally invalid (git: 'corrupt patch')")
print("     │    │")
print(f"     │    ├── {len(safe)} safely normalised (metadata only)")
print("     │    │    │")
print(f"     │    │    ├── {len(applied)} apply after normalisation")
print(f"     │    │    │    ├── {len(verified)} verify under the full gate")
print(f"     │    │    │    └── {len(gate_fail)} fail the deterministic gate")
print(f"     │    │    └── {len(non_app)} still non-applicable")
print("     │    │")
print(f"     │    └── {len(unsafe)} normalisation unsafe (refused, not guessed)")
print("     │")
print(f"     └── {len(parse)} parseable but non-applicable (content wrong, not metadata)")

print("\n=== per-arm counterfactual yield (POST-HOC, NOT THE BENCHMARK RESULT) ===")
print(f"{'arm':4} {'original correct':>17} {'recovered':>10} {'counterfactual':>15}")
for arm in "ABC":
    orig = sum(1 for x in recs if x["arm"] == arm and x.get("ground_truth_correct"))
    rec = sum(1 for r in verified if r["arm"] == arm)
    n = sum(1 for x in recs if x["arm"] == arm)
    print(f"{arm:4} {f'{orig}/{n}':>17} {f'+{rec}':>10} {f'{orig + rec}/{n}':>15}")

print("\n=== recoverable vs semantic ===")
print(f"  representation-recoverable (verified by metadata fix alone): {len(verified)}")
print(f"  remaining failures                                        : {len(fails) - len(verified)}")
print(f"    - normalisation unsafe (ambiguous representation)       : {len(unsafe)}")
print(f"    - parseable but content/context wrong                   : {len(parse)}")
print(f"    - normalised, applied, still failed the gate            : {len(gate_fail)}")
print(f"    - normalised but still non-applicable                   : {len(non_app)}")

print("\n=== invariant checks ===")
print(f"  content identical after normalisation: {sum(1 for r in rep if r.get('content_lines_identical'))}/{len(safe)}")
print(f"  baseline verified before replay: {sum(1 for r in rep if r.get('baseline_verified'))}/{len(rep)}")
print(f"  baseline restored after replay: {sum(1 for r in rep if r.get('baseline_restored'))}/{len(applied)}")
print(f"  unsafe by reason: {collections.Counter((r.get('normalization_notes') or ['-'])[0][:46] for r in unsafe)}")
