"""Counterfactual replay: normalise metadata only, then run the existing gate.

POST-HOC DIAGNOSTIC. NOT BM-06. NOT A REPLACEMENT BENCHMARK RESULT.

Reads the frozen run and writes only under `patch_replay/`. No provider is
configured and no model request is made — the model's proposals are replayed
exactly as it made them, with only diff control metadata corrected.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import git_classify as G  # noqa: E402
import normalize as N  # noqa: E402

ROOT = pathlib.Path("/w")
HERE = ROOT / "benchmark/bm06/patch_replay"
results = json.loads((ROOT / "benchmark/bm06/results.json").read_text(encoding="utf-8"))
manifest = json.loads((ROOT / "benchmark/bm06/manifest-preliminary.json").read_text(encoding="utf-8"))
cases = {c["case_id"]: c for c in manifest["cases"]}

sys.path.insert(0, str(ROOT / "benchmark" / "bm06"))
import driver as D  # noqa: E402


def env_for(case: dict, worktree: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    # No provider is reachable from a replay: the credentials are removed, so a
    # model request would fail loudly rather than quietly costing money.
    for k in ("RIFT_LLM_URL", "RIFT_LLM_KEY", "RIFT_LLM_MODEL"):
        env.pop(k, None)
    layout = case.get("src_layout") or "flat"
    if layout != "flat":
        env["PYTHONPATH"] = str((worktree / layout).resolve())
    return env


def run_gate(case: dict, worktree: pathlib.Path, patch: pathlib.Path) -> dict:
    """The existing deterministic gate, in full. Not a target-pass shortcut."""
    argv = [
        sys.executable,
        "-m",
        "riftagent",
        "--repo",
        str(worktree),
        "--json",
        "verify",
        str(patch),
        case["target"],
        "--allow-partial-sandbox",
        "--expect-signature",
        case["signature"],
    ]
    for node in case.get("preserve", []):
        argv += ["--preserve", node]
    proc = subprocess.run(
        argv, cwd=str(worktree), capture_output=True, text=True, timeout=1800, env=env_for(case, worktree)
    )
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "verdict" in parsed:
            return {"verdict": parsed["verdict"], "reason": (parsed.get("reason") or "")[:200]}
    return {"verdict": "<no receipt>", "reason": (proc.stderr or "")[-200:]}


rows = []
for rec in results["records"]:
    if rec.get("failed_phase") != "candidate":
        continue
    case = cases[rec["case_id"]]
    worktree = pathlib.Path(case["worktree"])
    original = pathlib.Path(str(rec["patch"]).replace("/w/", str(ROOT) + "/"))
    raw = original.read_text(encoding="utf-8", errors="replace")

    row = {
        "case": rec["case_id"],
        "arm": rec["arm"],
        "cause_class": rec["cause_class"],
        "original_patch": original.name,
        "original_candidate_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "original_verdict": rec["verdict"],
        "original_failed_phase": rec["failed_phase"],
        "frozen_parent": case["parent"],
        "frozen_baseline_tree_hash": rec.get("baseline_tree_hash"),
    }

    # The replay must run against the same tree the arm ran against.
    observed_tree = D.baseline_tree_hash(worktree)
    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    row["baseline_verified"] = (observed_tree == rec.get("baseline_tree_hash")) and (head == case["parent"])
    if not row["baseline_verified"]:
        row["normalization"] = "SKIPPED_BASELINE_DRIFT"
        row["replay_verdict"] = "not attempted"
        rows.append(row)
        continue

    before = G.classify(worktree, original)
    row["git_classification"] = before["classification"]
    row["original_git_error"] = before["git_error"]

    text, status, notes = N.normalize(raw)
    row["normalization"] = status
    row["normalization_notes"] = notes
    row["content_lines_identical"] = N.semantic_lines(text) == N.semantic_lines(raw)

    if status == N.UNSAFE:
        row["replay_verdict"] = "not attempted"
        rows.append(row)
        continue

    normalized = HERE / "normalized" / original.name
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text(text, encoding="utf-8", newline="")
    row["normalized_patch"] = normalized.name
    row["normalized_candidate_sha256"] = hashlib.sha256(normalized.read_bytes()).hexdigest()

    code, err = G.git_apply(worktree, normalized)
    row["applies_after_normalization"] = code == 0
    row["apply_error"] = err[:200] if code else ""
    if code != 0:
        row["replay_verdict"] = "NORMALIZED_BUT_NON_APPLICABLE"
        rows.append(row)
        continue

    gate = run_gate(case, worktree, normalized)
    row["replay_verdict"] = gate["verdict"]
    row["replay_reason"] = gate["reason"]
    subprocess.run(["git", "-C", str(worktree), "checkout", "--", "."], capture_output=True)
    subprocess.run(["git", "-C", str(worktree), "clean", "-qfd", ":!.rift"], capture_output=True)
    row["baseline_restored"] = D.baseline_tree_hash(worktree) == rec.get("baseline_tree_hash")
    rows.append(row)
    print(
        f"  {row['arm']} {row['case'][:30]:30} {status[:20]:20} "
        f"applies={row.get('applies_after_normalization')} -> {row['replay_verdict'][:34]}",
        flush=True,
    )

(HERE / "replay.json").write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8")
print(f"\nreplayed {len(rows)} candidate failures; model requests made: 0")
