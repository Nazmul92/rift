"""Freeze the set of commits BM-08 must not reuse. No network, no model.

BM-08's claim is "evaluation on unseen historical bugs". The miner scans the same
repository volume every prior benchmark scanned, so that claim is not free — it
has to be established by exclusion, not assumed because the pool was newly
generated.

This walks every benchmark artifact in the tree and harvests every 40-character
hex object id it can find, from any field, at any depth. Deliberately
over-broad: harvesting a commit BM-08 could legitimately have used costs one
candidate out of hundreds, while missing one silently converts a reused bug into
a "previously unseen" result. When the two errors are that asymmetric, the
harvesting rule should not be clever.

Sources are every JSON artifact under `benchmark/`, plus the frozen diff fixtures
and the BM-06/BM-07 result and replay records — pilots, abandoned freezes and
invalidated runs included. A commit inspected during calibration and then
discarded is still a commit this project has looked at.

The output is written once and treated as frozen input to selection.
"""

import collections
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parents[2]
BENCH = ROOT / "benchmark"
OUT = pathlib.Path("/s/bm08_exclusions.json")

SHA = re.compile(r"\b[0-9a-f]{40}\b")
# The authority is **pre-BM-08 exposure only**. BM-08's own model-free artifacts
# are not prior benchmark exposure: a commit this pipeline mined, queued, curated
# or rejected has not been "seen" in any sense that could contaminate an
# unseen-bug claim. Harvesting them would make the exclusion set grow every time
# the pipeline runs — each pass excluding its own predecessors until the corpus
# starved itself. That failure would be gradual, self-inflicted, and almost
# invisible in the counts.
SKIP_DIRS = {"bm08", "work", "reference-env"}
# BM-08's own test fixtures are equally not prior art.
SKIP_TEST_PREFIX = "test_bm08"


def harvest(text: str) -> set[str]:
    return set(SHA.findall(text))


sources: dict[str, set[str]] = {}
for path in sorted(BENCH.rglob("*")):
    if not path.is_file():
        continue
    if any(part in SKIP_DIRS for part in path.relative_to(BENCH).parts):
        continue
    if path.suffix not in (".json", ".jsonl", ".txt", ".md", ".diff"):
        continue
    try:
        found = harvest(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        continue
    if found:
        sources[str(path.relative_to(ROOT))] = found

# Test fixtures are development exposure too — except BM-08's own.
for path in sorted((ROOT / "tests").rglob("*")):
    if path.name.startswith(SKIP_TEST_PREFIX):
        continue
    if path.is_file() and path.suffix in (".json", ".diff", ".py"):
        found = harvest(path.read_text(encoding="utf-8", errors="replace"))
        if found:
            sources[str(path.relative_to(ROOT))] = found

excluded: set[str] = set()
for found in sources.values():
    excluded |= found

# The named benchmark cases, kept separately so overlap can be attributed.
named: dict[str, list[dict]] = {}
for label, rel in (
    ("BM-07 official", "benchmark/bm07/manifest-executable.json"),
    ("BM-07 validated", "benchmark/bm07/validated-cases.json"),
    ("BM-07 shortlist", "benchmark/bm07/shortlist.json"),
    ("BM-07 candidate pool", "benchmark/bm07/candidate-pool.json"),
    ("BM-07 mined pool", "benchmark/bm07/mined-pool.json"),
    ("BM-06 manifest", "benchmark/bm06/manifest-preliminary.json"),
    ("BM-06 candidates", "benchmark/bm06/candidates.json"),
    ("frozen pilot", "benchmark/frozen/manifest.json"),
    ("pilot cases", "benchmark/pilot-frozen/case-manifest.json"),
):
    path = ROOT / rel
    if not path.is_file():
        continue
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    rows = blob.get("cases") if isinstance(blob, dict) else blob
    if not isinstance(rows, list):
        continue
    entries = []
    for row in rows:
        if isinstance(row, dict) and (row.get("fix_commit") or row.get("commit")):
            entries.append(
                {
                    "repo": row.get("repository") or row.get("repo") or "",
                    "fix_commit": row.get("fix_commit") or row.get("commit"),
                    "parent": row.get("parent", ""),
                    "target_node": row.get("target_node", ""),
                }
            )
    if entries:
        named[label] = entries

# The set's own identity, so a later pass can prove it did not drift.
identity = hashlib.sha256("\n".join(sorted(excluded)).encode("utf-8")).hexdigest()

payload = {
    "authority": "pre-BM-08 exposure only; BM-08 artifacts are never harvested",
    "excluded_commit_set_hash": identity,
    "excluded_commit_count": len(excluded),
    "excluded_commits": sorted(excluded),
    "sources": {k: len(v) for k, v in sorted(sources.items())},
    "named_prior_cases": named,
}
OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")

print(f"artifacts scanned : {len(sources)}")
print(f"commits excluded  : {len(excluded)}")
print(f"exclusion-set hash: {identity}")
bm08_sources = [p for p in sources if "bm08" in p]
print(f"BM-08 sources harvested : {bm08_sources or 'none (correct: BM-08 evidence is not prior exposure)'}")
print("\nnamed prior benchmark cases:")
for label, entries in named.items():
    repos = collections.Counter(e["repo"] for e in entries)
    print(f"  {label:22} {len(entries):4} cases  {dict(repos.most_common(6))}")
print("\ntop artifact sources:")
for path, n in sorted(sources.items(), key=lambda kv: -len(kv[1]))[:12]:
    print(f"  {n if isinstance(n, int) else len(n):6}  {path}")
print(f"\nwritten: {OUT}", file=sys.stderr)
