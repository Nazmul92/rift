"""POST-HOC CANONICALIZATION REPLAY — NOT AN OFFICIAL BENCHMARK RERUN.

Forensic only. Nothing here changes RIFT, the canonicalizer, repair semantics or
any measured benchmark result. No provider is configured and none is called.

The question is narrow: BM-08's canonical candidates applied far less often than
BM-07's. Hash inequality between stages proves a transformation happened, not
that it helped, so this replays the **exact retained bytes** through
`git apply --check` and lets git decide.

Three stages are replayed independently, each against its own fresh copy of the
frozen baseline, because testing `normalized` on a tree already mutated by `raw`
would measure the wrong thing:

    baseline copy A -> raw.diff
    baseline copy B -> normalized.diff
    baseline copy C -> canonical.diff

Every copy's `tree_hash` is re-verified against the frozen `baseline_tree_hash`
immediately before its check, so a classification can never be attributed to a
tree the benchmark did not use.

Patches are read, never rewritten. The replay does not canonicalize anything: it
measures bytes that already exist.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "src"))

import bm08_driver as driver  # noqa: E402

from riftagent.records import content_hash  # noqa: E402
from riftagent.sandbox import tree_hash  # noqa: E402

BM08 = pathlib.Path(__file__).parent
BANNER = "POST-HOC CANONICALIZATION REPLAY — NOT AN OFFICIAL BENCHMARK RERUN"
STAGES = ("raw", "normalized", "canonical")
REPO_ROOTS = (pathlib.Path("/repos"), pathlib.Path("/repos-v3"), pathlib.Path("/repos-v5"))


class BaselineIdentityError(RuntimeError):
    """The reconstructed tree is not the tree the benchmark measured."""


# --------------------------------------------------------------- git diagnostics

# Ordered most-specific first. A class is only used when the diagnostic actually
# supports it; anything else falls into the conservative bucket with its text
# retained, so an unrecognised git message can never be silently reinterpreted.
FAILURE_CLASSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("invalid_or_corrupt_patch", re.compile(r"corrupt patch at line|fatal: corrupt", re.I)),
    (
        "bad_hunk_count_or_malformed_header",
        re.compile(r"patch fragment without header|unrecognized input|malformed patch|expected \d+ lines", re.I),
    ),
    ("already_applied_or_reverse_patch", re.compile(r"reverse|already applied", re.I)),
    ("new_file_path_conflict", re.compile(r"already exists|new file .* exists", re.I)),
    (
        "file_not_found_or_wrong_path",
        re.compile(r"does not exist in index|No such file or directory|cannot read|unable to stat", re.I),
    ),
    ("context_mismatch", re.compile(r"while searching for|context mismatch|hunk #\d+ FAILED", re.I)),
    ("patch_does_not_apply_at_location", re.compile(r"patch failed:|patch does not apply", re.I)),
)


def classify_failure(diagnostic: str) -> str:
    """Deterministic, conservative classification of one git-apply diagnostic."""
    if not diagnostic.strip():
        return "other_git_apply_failure"
    for name, pattern in FAILURE_CLASSES:
        if pattern.search(diagnostic):
            return name
    return "other_git_apply_failure"


REPRESENTATION_CLASSES = frozenset({"bad_hunk_count_or_malformed_header", "invalid_or_corrupt_patch"})
SOURCE_CONTEXT_CLASSES = frozenset(
    {
        "context_mismatch",
        "file_not_found_or_wrong_path",
        "patch_does_not_apply_at_location",
        "new_file_path_conflict",
    }
)


def defect_level(failure_class: str) -> str:
    """Representation-level defects are inside a canonicalizer's reach; source
    and context defects are not, unless the implementation explicitly handles
    them. Kept separate so 'non-applicable' is never read as 'canonicalizer
    failure'."""
    if failure_class in REPRESENTATION_CLASSES:
        return "representation"
    if failure_class in SOURCE_CONTEXT_CLASSES:
        return "source_or_context"
    return "unclassified"


# ------------------------------------------------------------------ diff shape


def diff_shape(text: str) -> dict:
    """Deterministic structure of a unified diff. No difficulty judgements."""
    files: set[str] = set()
    hunks = added = removed = new_files = deleted_files = renames = 0
    for line in text.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            path = line[4:].strip()
            if path not in ("/dev/null",):
                files.add(path[2:] if path[:2] in ("a/", "b/") else path)
        elif line.startswith("@@"):
            hunks += 1
        elif line.startswith("new file mode"):
            new_files += 1
        elif line.startswith("deleted file mode"):
            deleted_files += 1
        elif line.startswith("rename from") or line.startswith("rename to"):
            renames += 1
        elif line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return {
        "files_touched": len(files),
        "hunk_count": hunks,
        "added_lines": added,
        "removed_lines": removed,
        "new_files": new_files,
        "deleted_files": deleted_files,
        "renames": renames // 2,
        "patch_bytes": len(text.encode("utf-8")),
        "single_file": len(files) == 1,
        "single_hunk": hunks == 1,
    }


# ------------------------------------------------------------------- the replay


def repo_for(name: str) -> pathlib.Path:
    found = [root / name for root in REPO_ROOTS if (root / name / ".git").is_dir()]
    if len(found) != 1:
        raise BaselineIdentityError(f"{name} resolved to {len(found)} repository roots")
    return found[0]


def materialise(case: dict, into: pathlib.Path) -> pathlib.Path:
    """The frozen baseline, identity-checked. Never proceeds on a different tree."""
    repo = repo_for(case["repository"])
    shutil.rmtree(into, ignore_errors=True)
    driver.materialise_baseline(case, repo.parent, into)
    observed = tree_hash(into)
    if observed != case["baseline_tree_hash"]:
        raise BaselineIdentityError(
            f"{case['case_id']}: observed {observed[:12]} != frozen {case['baseline_tree_hash'][:12]}"
        )
    return into


def apply_check(tree: pathlib.Path, patch: pathlib.Path) -> dict:
    """`git apply --check` only. It never writes to the tree."""
    proc = subprocess.run(
        ["git", "apply", "--check", "--verbose", str(patch)],
        cwd=str(tree),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=300,
    )
    diagnostic = (proc.stderr or "").strip()
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "").strip()[:4000],
        "stderr": diagnostic[:4000],
        "failure_class": "" if proc.returncode == 0 else classify_failure(diagnostic),
    }


def transition(raw_ok: bool, canonical_ok: bool) -> str:
    if not raw_ok and canonical_ok:
        return "RESCUED"
    if raw_ok and canonical_ok:
        return "PRESERVED"
    if raw_ok and not canonical_ok:
        return "DAMAGED"
    return "UNRECOVERED"


def replay_arm(case: dict, arm: str, evidence: pathlib.Path, master: pathlib.Path, work: pathlib.Path) -> dict:
    """One arm, three stages, three fresh identical baselines."""
    record: dict = {
        "benchmark": "BM-08",
        "case_id": case["case_id"],
        "repository": case["repository"],
        "arm": arm,
        "baseline_tree_hash": case["baseline_tree_hash"],
    }
    stage_ok: dict[str, bool] = {}
    for stage in STAGES:
        patch = evidence / f"{stage}.diff"
        text = patch.read_text(encoding="utf-8", errors="replace")
        record[f"{stage}_hash"] = content_hash(patch.read_bytes())
        record[f"{stage}_shape"] = diff_shape(text)

        # A fresh copy per stage, re-verified. Copying rather than reusing keeps
        # the stages independent even though --check does not write.
        copy = work / f"{case['case_id']}-{arm}-{stage}"
        shutil.rmtree(copy, ignore_errors=True)
        shutil.copytree(master, copy, symlinks=True)
        observed = tree_hash(copy)
        if observed != case["baseline_tree_hash"]:
            raise BaselineIdentityError(f"{case['case_id']}/{arm}/{stage}: stage copy tree hash drifted")
        record[f"{stage}_baseline_verified"] = True

        result = apply_check(copy, patch)
        shutil.rmtree(copy, ignore_errors=True)
        stage_ok[stage] = result["ok"]
        record[f"{stage}_apply_ok"] = result["ok"]
        record[f"{stage}_exit_code"] = result["exit_code"]
        record[f"{stage}_git_diagnostic"] = result["stderr"]
        record[f"{stage}_git_stdout"] = result["stdout"]
        record[f"failure_class_{stage}"] = result["failure_class"]
        record[f"defect_level_{stage}"] = defect_level(result["failure_class"]) if result["failure_class"] else ""

    record["primary_transition"] = transition(stage_ok["raw"], stage_ok["canonical"])
    record["raw_to_normalized"] = (
        f"{'PASS' if stage_ok['raw'] else 'FAIL'}->{'PASS' if stage_ok['normalized'] else 'FAIL'}"
    )
    record["normalized_to_canonical"] = (
        f"{'PASS' if stage_ok['normalized'] else 'FAIL'}->{'PASS' if stage_ok['canonical'] else 'FAIL'}"
    )
    return record


def verify_recorded_hashes(record: dict, official: dict) -> list[str]:
    """The replayed bytes must be the bytes the benchmark recorded."""
    problems = []
    for stage in STAGES:
        expected = official.get(f"{stage}_candidate_hash")
        if expected and record[f"{stage}_hash"] != expected:
            problems.append(f"{record['case_id']}/{record['arm']}/{stage}: retained bytes != recorded hash")
    return problems


def main() -> int:
    print(BANNER)
    print("=" * len(BANNER))
    manifest = json.loads((BM08 / "manifest-executable-v5.json").read_text(encoding="utf-8"))
    cases = {c["case_id"]: c for c in manifest["cases"]}
    official = {}
    for line in (BM08 / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            official[(r["case_id"], r["arm"])] = r

    evidence_root = BM08 / "results-evidence"
    work = pathlib.Path("/tmp/bm08-replay")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    records: list[dict] = []
    skipped: list[str] = []
    hash_problems: list[str] = []

    for case_id, case in sorted(cases.items()):
        arms = [
            arm for arm in ("A", "C") if all((evidence_root / case_id / arm / f"{s}.diff").is_file() for s in STAGES)
        ]
        for arm in ("A", "C"):
            if arm not in arms:
                skipped.append(f"{case_id}/{arm}: incomplete stage artifacts")
        if not arms:
            continue
        master = materialise(case, work / f"master-{case_id}")
        for arm in arms:
            record = replay_arm(case, arm, evidence_root / case_id / arm, master, work)
            hash_problems += verify_recorded_hashes(record, official.get((case_id, arm), {}))
            records.append(record)
            t = record["primary_transition"]
            print(
                f"  {case_id:28} {arm}  raw={'ok ' if record['raw_apply_ok'] else 'FAIL'}"
                f"  norm={'ok ' if record['normalized_apply_ok'] else 'FAIL'}"
                f"  canon={'ok ' if record['canonical_apply_ok'] else 'FAIL'}   {t}"
            )
        shutil.rmtree(master, ignore_errors=True)

    if hash_problems:
        for problem in hash_problems:
            print(f"  MISMATCH  {problem}")
        print("BLOCKED: retained bytes do not match the hashes the benchmark recorded")
        return 2

    out = BM08 / "canonicalization-replay.json"
    out.write_text(
        json.dumps(
            {
                "label": BANNER,
                "benchmark": "BM-08",
                "official_result_unchanged": True,
                "provider_calls": 0,
                "additional_spend_usd": 0.0,
                "skipped": skipped,
                "records": records,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nrecords: {len(records)}   skipped: {len(skipped)}   -> {out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaselineIdentityError as exc:
        print(f"BLOCKED_BASELINE_IDENTITY: {exc}")
        raise SystemExit(3) from exc
