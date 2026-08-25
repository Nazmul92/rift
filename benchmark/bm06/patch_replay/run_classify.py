"""Recompute the candidate-failure classification from the frozen records."""

from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import classify as C  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
results = json.loads((ROOT / "benchmark/bm06/results.json").read_text(encoding="utf-8"))
rows = []
for rec in results["records"]:
    if rec.get("failed_phase") != "candidate":
        continue
    patch = pathlib.Path(str(rec["patch"]).replace("/w/", str(ROOT) + "/"))
    raw = patch.read_bytes() if patch.is_file() else b""
    kind, reasons = C.classify(raw.decode("utf-8", "replace")) if raw else (C.OTHER, ["no patch captured"])
    rows.append(
        {
            "case": rec["case_id"],
            "arm": rec["arm"],
            "cause_class": rec["cause_class"],
            "patch": patch.name,
            "original_candidate_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "classification": kind,
            "reasons": reasons,
        }
    )

out = ROOT / "benchmark/bm06/patch_replay/classification.json"
out.write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8")
tally = collections.Counter(r["classification"] for r in rows)
print(f"candidate failures recomputed : {len(rows)}")
for k, n in sorted(tally.items()):
    print(f"  {k:26} {n}")
print()
for r in rows:
    why = (r["reasons"][0] if r["reasons"] else "")[:34]
    print(f"  {r['arm']}  {r['case'][:30]:30} {r['classification'][:22]:22} {why}")
