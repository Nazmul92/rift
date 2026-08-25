"""BM-08 stage C: turn queue entries into runnable case records. No model, no network.

The mechanics are BM-07's — AST target resolution and preservation-set
construction — because those are neutral: they work out which pytest node the
fix commit's new test creates, and which pre-existing tests it leaves untouched.
Nothing about them favours one arm.

What is deliberately **not** carried over is BM-07's RIFT-shaped filtering. That
curation dropped a candidate for having no named "shortcut structure", scored
`discrimination_potential`, and kept one primary case per repository so the
mechanism study had maximal room to disagree. Every one of those would bias an
efficiency benchmark toward the thing being measured.

What remains are mechanical requirements without which a case cannot be *run*:

* the declared parent is the fix commit's real first parent;
* the fix commit adds at least one runnable test function, giving a target node;
* at least three pre-existing tests survive untouched, so preservation means
  something. A fix that rewrites every test in its file leaves nothing to
  preserve and cannot show a regression either way.

Everything else is recorded and reported, never selected on.
"""

import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from curation import direct_parent_valid, preservation_candidates, resolve_targets  # noqa: E402
from repo_resolution import preflight  # noqa: E402

REPO_ROOTS = [pathlib.Path("/repos"), pathlib.Path("/repos-v3"), pathlib.Path("/repos-v5")]
S = pathlib.Path("/s")
QUEUE = S / "bm08_queue.json"
OUT = S / "bm08_cases.json"

MIN_PRESERVATION = 3


def repo_path(name: str, roots: list[pathlib.Path]) -> pathlib.Path:
    """Resolve a repository name across the v3 mining roots.

    The population spans two roots — the one carried forward from earlier
    benchmarks and the v3 expansion. Resolving against only the first silently
    turns every new-repository candidate into a provenance mismatch, because git
    commands against a nonexistent path fail exactly like a bad parent does.
    Returns the first root that actually holds the repository.
    """
    for root in roots:
        if (root / name / ".git").exists():
            return root / name
    return roots[0] / name


RUNNER_CONFIG = ("conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")

queue = json.loads(QUEUE.read_text(encoding="utf-8"))

# Infrastructure authority, before any candidate is examined. A missing checkout
# must never be able to present itself as `direct_parent_invalid`.
resolution = preflight({entry["repo"] for entry in queue}, REPO_ROOTS, "repository preflight")
if not resolution.ok:
    raise SystemExit(3)

kept, dropped = [], []
counters: collections.Counter = collections.Counter()

for entry in queue:
    repo = resolution.path(entry["repo"])
    sha = entry["fix_commit"]
    cid = f"{entry['repo']}-{sha[:8]}"

    ok, detail = direct_parent_valid(repo, sha, entry["parent"])
    if not ok:
        dropped.append({"case_id": cid, "reason": f"direct_parent_invalid: {detail}"})
        counters["direct_parent_invalid"] += 1
        continue
    parent = detail

    resolved, excluded = resolve_targets(repo, sha, entry["test_files"])
    if not resolved:
        why = excluded[0].reason if excluded else "the fix commit adds no runnable test function"
        dropped.append({"case_id": cid, "reason": f"no_resolvable_target: {why}"})
        counters["no_resolvable_target"] += 1
        continue

    preservation = preservation_candidates(repo, sha, parent, entry["test_files"])
    if len(preservation.nodes) < MIN_PRESERVATION:
        dropped.append(
            {
                "case_id": cid,
                "reason": (
                    f"insufficient_untouched_preservation: {len(preservation.nodes)} untouched "
                    f"(<{MIN_PRESERVATION}); {len(preservation.touched)} pre-existing tests were "
                    f"modified by the fix and no longer count"
                ),
            }
        )
        counters["insufficient_untouched_preservation"] += 1
        continue

    kept.append(
        {
            "case_id": cid,
            "repository": entry["repo"],
            # `%aI`; eligibility already applied upstream. `%cI` carried for contrast.
            "author_date": entry["author_date"],
            "committer_date": entry["committer_date"],
            "fix_commit": sha,
            "parent": parent,
            "subject": entry["subject"],
            "target_node": resolved[0].node_id,
            "target_resolution_method": resolved[0].method,
            "additional_targets": [r.node_id for r in resolved[1:]],
            "source_files": entry["source_files"],
            "test_files": entry["test_files"],
            # The complete set, never a sample: ground truth computed from the
            # first N nodes cannot see a candidate that breaks node N+1.
            "untouched_preservation_nodes": preservation.nodes,
            "untouched_preservation_count": len(preservation.nodes),
            "preexisting_tests_modified_by_fix": preservation.touched,
            "tests_added_by_fix": preservation.added,
            # Labels only. BM-08-v2 imposes no category quota and does no
            # hand-balancing; the distribution is reported after the fact.
            "categories": entry["categories"],
            "source_lines_added": entry["source_lines_added"],
            "source_lines_removed": entry["source_lines_removed"],
            "edit_sites": entry["edit_sites"],
            "order_key": entry["order_key"],
            "queue_position": queue.index(entry),
        }
    )

OUT.write_text(
    json.dumps(
        {"attempted": len(queue), "curated": len(kept), "cases": kept, "dropped": dropped},
        indent=1,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)

print(f"queue      : {len(queue)}")
print(f"curated    : {len(kept)} from {len({c['repository'] for c in kept})} repositories")
print(f"dropped    : {len(dropped)}  {dict(counters.most_common())}")
print("by category:", dict(collections.Counter(k for c in kept for k in c["categories"]).most_common()))
print("by repo    :", dict(collections.Counter(c["repository"] for c in kept).most_common()))
if kept:
    widths = [c["untouched_preservation_count"] for c in kept]
    print(f"preservation: min {min(widths)}  max {max(widths)}  median {sorted(widths)[len(widths) // 2]}")
print(f"\nwritten: {OUT}", file=sys.stderr)
