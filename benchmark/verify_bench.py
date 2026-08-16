"""M1a verify benchmark: real patches from real repositories, frozen first.

Three stages, deliberately separate so the ground truth cannot move once the
arms have been seen:

    build    discover real (failing check, gold patch) pairs, derive known-bad
             variants, and write a manifest with a content hash
    run      execute both arms over the frozen manifest, writing raw records
    report   recompute every metric from the raw records

Case construction follows the standard "parent + test patch" shape. For a real
bug-fix commit that changes both a test and a source file, the repository is
placed at the parent commit with the commit's *test* changes applied. The
target test then genuinely fails, and the commit's *source* changes are the
gold patch. Nothing is authored here: the diffs are the project's own.

Arms:

    S (standard protocol)  apply the patch, run the repository's test suite,
                           accept if the target node passed. This is the
                           protocol the false-fix experiment measured.
    C (counterfactual)     `rift verify`, accept only on
                           `verified_against_approved_checks`.

This harness lives outside the runtime modules and is never imported by them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

FIX_MESSAGE = re.compile(r"\b(fix|bug|regression|broken|incorrect|error|crash)\b", re.IGNORECASE)
TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]+\.py$|_test\.py$")

# Dependency-light pure-Python projects with self-contained pytest suites.
# The set is wider than the five the acceptance row requires because a project
# whose suite needs plugins this environment lacks yields nothing, and which
# ones those are is not knowable before scanning.
REPOS = [
    ("click", "https://github.com/pallets/click", "src"),
    ("pyparsing", "https://github.com/pyparsing/pyparsing", ""),
    ("sqlparse", "https://github.com/andialbrecht/sqlparse", ""),
    ("pluggy", "https://github.com/pytest-dev/pluggy", "src"),
    ("boltons", "https://github.com/mahmoud/boltons", ""),
    ("markdown", "https://github.com/Python-Markdown/markdown", ""),
    ("chardet", "https://github.com/chardet/chardet", ""),
    ("attrs", "https://github.com/python-attrs/attrs", "src"),
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def git(repo: Path, *args: str, check: bool = True, timeout: float = 300.0) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def pytest_env(repo: Path, src: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TMPDIR", "TERM")}
    env["PYTHONPATH"] = str(repo / src) if src else str(repo)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COLUMNS"] = "200"
    return env


def run_pytest(repo: Path, src: str, targets: list[str], timeout: float = 900.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rA",
            "--tb=no",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            # A collection error in an unrelated module must not decide the
            # arm's result; the target's own report line does.
            "--continue-on-collection-errors",
            *targets,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        errors="replace",
        env=pytest_env(repo, src),
        timeout=timeout,
    )


def pinned_ref(repo: Path) -> str:
    """The repository's default branch, resolved through the remote.

    F4 requires a pinned starting ref. `origin/HEAD` is resolved once per run
    and recorded in the manifest, so a reviewer can reproduce the enumeration
    rather than inherit whatever commit a previous run happened to leave
    checked out.
    """
    out = git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD", check=False).strip()
    if out:
        return out.removeprefix("refs/remotes/")
    for candidate in ("origin/main", "origin/master"):
        if git(repo, "rev-parse", "--verify", "-q", candidate, check=False).strip():
            return candidate
    return "HEAD"


def install_repo(repo: Path) -> bool:
    """Install the project so `importlib.metadata` can find it.

    Several of these projects read their own distribution metadata at import
    time, so `PYTHONPATH` alone is not enough. The install is non-editable and
    dependency-free: `PYTHONPATH` still points at the worktree, so the code
    under test comes from the tree while only the metadata comes from
    site-packages.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--no-build-isolation", str(repo)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "--no-deps", str(repo)],
            capture_output=True,
            text=True,
            timeout=900,
        )
    return proc.returncode == 0


def node_verdict(output: str, node_id: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        verdict, _, rest = stripped.partition(" ")
        if verdict not in ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"):
            continue
        rest = rest.strip()
        if rest == node_id or rest.startswith(node_id + " - "):
            return verdict
    return None


def failing_nodes(output: str) -> list[str]:
    out = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED "):
            out.append(stripped[len("FAILED ") :].split(" - ")[0].strip())
    return out


def split_diff(repo: Path, commit: str) -> tuple[str, str, list[str], list[str]]:
    """Split a commit into its test-side and source-side diffs."""
    files = [f for f in git(repo, "show", "--pretty=", "--name-only", commit).splitlines() if f.strip()]
    test_files = [f for f in files if TEST_PATH.search(f)]
    src_files = [f for f in files if f not in test_files and f.endswith(".py")]
    if not test_files or not src_files:
        return "", "", [], []
    test_diff = git(repo, "diff", f"{commit}~1", commit, "--", *test_files)
    src_diff = git(repo, "diff", f"{commit}~1", commit, "--", *src_files)
    return test_diff, src_diff, test_files, src_files


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------


@dataclass
class Case:
    case_id: str
    repo: str
    commit: str
    parent: str
    src_layout: str
    target: str
    patch_class: str
    expected_accept: bool
    patch_file: str
    test_files: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    note: str = ""


def derive_inert(repo: Path, source_files: list[str]) -> str | None:
    """A comment appended to the file the gold patch touches. Semantically
    inert by construction, so any acceptance is a false positive."""
    for rel in source_files:
        path = repo / rel
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8", errors="replace")
        path.write_text(original + "\n# fix applied\n", encoding="utf-8")
        diff = git(repo, "diff", "--", rel)
        path.write_text(original, encoding="utf-8")
        if diff.strip():
            return diff
    return None


def derive_unrelated(repo: Path, src_layout: str, source_files: list[str]) -> str | None:
    root = repo / src_layout if src_layout else repo
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(repo).as_posix()
        if rel in source_files or TEST_PATH.search(rel):
            continue
        original = path.read_text(encoding="utf-8", errors="replace")
        path.write_text(original + "\n# unrelated edit\n", encoding="utf-8")
        diff = git(repo, "diff", "--", rel)
        path.write_text(original, encoding="utf-8")
        if diff.strip():
            return diff
    return None


def derive_judge_weakening(repo: Path, target: str) -> str | None:
    """Delete the target test's body and replace it with a pass. This is the
    patch the frozen judge exists to refuse."""
    rel = target.split("::")[0]
    path = repo / rel
    if not path.is_file():
        return None
    original = path.read_text(encoding="utf-8", errors="replace")
    func = target.split("::")[-1].split("[")[0]
    lines = original.splitlines(keepends=True)
    out, skipping = [], False
    for line in lines:
        if line.lstrip().startswith(("def " + func + "(", "async def " + func + "(")):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(line)
            out.append(indent + "    return  # weakened\n")
            skipping = True
            continue
        if skipping:
            if line.strip() and not line.startswith((" ", "\t")):
                skipping = False
            else:
                continue
        out.append(line)
    if not skipping and "".join(out) == original:
        return None
    path.write_text("".join(out), encoding="utf-8")
    diff = git(repo, "diff", "--", rel)
    path.write_text(original, encoding="utf-8")
    return diff or None


def discover(
    work: Path, out_dir: Path, want_pairs: int, per_repo: int, scan: int, repo_refs: dict | None = None
) -> list[Case]:
    cases: list[Case] = []
    repo_refs = repo_refs if repo_refs is not None else {}
    patch_dir = out_dir / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    rotation = ["inert", "unrelated", "judge_weakening"]
    rot = 0

    for name, url, src_layout in REPOS:
        repo = work / name
        if not repo.exists():
            print(f"[{name}] cloning", flush=True)
            subprocess.run(["git", "clone", "-q", "--filter=blob:none", url, str(repo)], check=True, timeout=1200)
        if not install_repo(repo):
            print(f"[{name}] SKIPPED: project does not install", flush=True)
            continue

        # F4: enumerate from a pinned ref, never from whatever a previous run
        # left checked out. Without this the candidate set depends on prior
        # state and two runs sample different cases — which is exactly what
        # happened between the M1a benchmark's two runs.
        start_ref = pinned_ref(repo)
        git(repo, "checkout", "-q", "--force", start_ref)
        git(repo, "clean", "-qfd", check=False)
        start_commit = git(repo, "rev-parse", "HEAD").strip()
        repo_refs[name] = {"ref": start_ref, "commit": start_commit, "url": url}
        print(f"[{name}] enumerating from {start_ref} @ {start_commit[:12]}", flush=True)

        accepted = 0
        log = git(repo, "log", "--format=%H %s", f"-n{scan}", start_commit).splitlines()
        candidates = []
        for line in log:
            parts = line.split(" ", 1)
            if len(parts) > 1 and FIX_MESSAGE.search(parts[1]):
                candidates.append(parts[0])
        print(f"[{name}] {len(candidates)} candidate commits", flush=True)

        for commit in candidates:
            # Only a per-repository cap. A global cap would stop the scan
            # inside whichever repository happened to be first, and repository
            # breadth is part of what the benchmark is supposed to establish.
            if accepted >= per_repo:
                break
            try:
                files = [f for f in git(repo, "show", "--pretty=", "--name-only", commit).splitlines() if f.strip()]
                if not (1 <= len(files) <= 6):
                    continue
                test_diff, src_diff, test_files, src_files = split_diff(repo, commit)
                if not test_diff or not src_diff:
                    continue
                parent = git(repo, "rev-parse", f"{commit}~1").strip()

                git(repo, "checkout", "-q", "--force", parent)
                git(repo, "clean", "-qfd", check=False)
                patch = work / "test.diff"
                patch.write_text(test_diff, encoding="utf-8", newline="\n")
                applied = subprocess.run(
                    ["git", "-C", str(repo), "apply", "--whitespace=nowarn", str(patch)],
                    capture_output=True,
                    text=True,
                )
                if applied.returncode != 0:
                    continue

                before = run_pytest(repo, src_layout, test_files, timeout=420)
                broken = failing_nodes(before.stdout)
                if not broken:
                    continue
                target = broken[0]

                sp = work / "src.diff"
                sp.write_text(src_diff, encoding="utf-8", newline="\n")
                if (
                    subprocess.run(
                        ["git", "-C", str(repo), "apply", "--whitespace=nowarn", str(sp)], capture_output=True
                    ).returncode
                    != 0
                ):
                    continue
                after = run_pytest(repo, src_layout, [target], timeout=420)
                if node_verdict(after.stdout, target) != "PASSED":
                    continue
                subprocess.run(
                    ["git", "-C", str(repo), "apply", "-R", "--whitespace=nowarn", str(sp)], capture_output=True
                )

                base = f"{name}-{commit[:8]}"
                gold = patch_dir / f"{base}-correct.diff"
                gold.write_text(src_diff, encoding="utf-8", newline="\n")
                cases.append(
                    Case(
                        case_id=f"{base}-correct",
                        repo=name,
                        commit=commit,
                        parent=parent,
                        src_layout=src_layout,
                        target=target,
                        patch_class="correct",
                        expected_accept=True,
                        patch_file=gold.name,
                        test_files=test_files,
                        source_files=src_files,
                        note="the project's own fix for this test",
                    )
                )

                # Stage the current tree before deriving a known-bad patch.
                # `git diff` compares the working tree to the INDEX, and the
                # index still holds the parent commit, so without this the
                # derived diff for a test file silently carries the test patch
                # as well and cannot apply to the staged repository. That
                # defect invalidated the judge-weakening class of the first
                # frozen run; see IMPLEMENTATION_STATUS.md.
                git(repo, "add", "-A")

                klass = rotation[rot % len(rotation)]
                rot += 1
                if klass == "inert":
                    bad = derive_inert(repo, src_files)
                elif klass == "unrelated":
                    bad = derive_unrelated(repo, src_layout, src_files)
                else:
                    bad = derive_judge_weakening(repo, target)
                if bad:
                    bad_path = patch_dir / f"{base}-{klass}.diff"
                    bad_path.write_text(bad, encoding="utf-8", newline="\n")
                    cases.append(
                        Case(
                            case_id=f"{base}-{klass}",
                            repo=name,
                            commit=commit,
                            parent=parent,
                            src_layout=src_layout,
                            target=target,
                            patch_class=klass,
                            expected_accept=False,
                            patch_file=bad_path.name,
                            test_files=test_files,
                            source_files=src_files,
                            note="known-bad patch derived from the same case",
                        )
                    )
                accepted += 1
                print(f"[{name}] accepted {base} target={target} (+{klass})", flush=True)
            except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
                print(f"[{name}] {commit[:8]} skipped: {type(exc).__name__}", flush=True)
                continue
        git(repo, "checkout", "-q", "--force", "HEAD", check=False)
    return cases


def cmd_build(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    repo_refs: dict = {}
    cases = discover(work, out, args.pairs, args.per_repo, args.scan, repo_refs)
    manifest = {
        "schema": 1,
        "repositories": repo_refs,
        "instrument_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "arms": {
            "S": "apply the patch, run the repository suite, accept if the target node passed",
            "C": "rift verify; accept only on verified_against_approved_checks",
        },
        "cases": [asdict(c) for c in cases],
    }
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest["manifest_hash"] = hashlib.sha256(body.encode()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    repos = sorted({c.repo for c in cases})
    print(
        f"\nfrozen manifest: {len(cases)} cases across {len(repos)} repositories {repos}\n"
        f"hash {manifest['manifest_hash'][:16]}"
    )
    return 0


# --------------------------------------------------------------------------
# arms
# --------------------------------------------------------------------------


def prepare(case: dict, work: Path, staging: Path) -> Path:
    """Repository at parent + the commit's test changes, committed."""
    src = work / case["repo"]
    dest = staging / case["case_id"]
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    subprocess.run(["git", "clone", "-q", "--shared", str(src), str(dest)], check=True, timeout=600)
    git(dest, "checkout", "-q", "--force", case["parent"])
    test_diff = git(src, "diff", f"{case['commit']}~1", case["commit"], "--", *case["test_files"])
    patch = dest / "_tests.diff"
    patch.write_text(test_diff, encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(dest), "apply", "--whitespace=nowarn", str(patch)], check=True)
    patch.unlink()
    git(dest, "add", "-A")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "bench",
        "GIT_AUTHOR_EMAIL": "b@b",
        "GIT_COMMITTER_NAME": "bench",
        "GIT_COMMITTER_EMAIL": "b@b",
    }
    subprocess.run(["git", "-C", str(dest), "commit", "-q", "-m", "test patch"], check=True, env=env)
    return dest


def arm_standard(repo: Path, case: dict, patch: Path) -> dict:
    t0 = time.time()
    applied = subprocess.run(
        ["git", "-C", str(repo), "apply", "--whitespace=nowarn", str(patch)], capture_output=True, text=True
    )
    if applied.returncode != 0:
        return {"accepted": False, "seconds": time.time() - t0, "commands": 1, "detail": "patch did not apply"}
    proc = run_pytest(repo, case["src_layout"], ["tests" if (repo / "tests").is_dir() else "."], timeout=1200)
    subprocess.run(["git", "-C", str(repo), "apply", "-R", "--whitespace=nowarn", str(patch)], capture_output=True)
    verdict = node_verdict(proc.stdout, case["target"])
    return {
        "accepted": verdict == "PASSED",
        "seconds": time.time() - t0,
        "commands": 3,
        "detail": f"suite run; target={verdict}",
    }


def arm_counterfactual(repo: Path, case: dict, patch: Path) -> dict:
    t0 = time.time()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "riftagent",
            "--repo",
            str(repo),
            "verify",
            str(patch),
            case["target"],
            "--allow-partial-sandbox",
        ],
        capture_output=True,
        text=True,
        errors="replace",
        env={**pytest_env(repo, case["src_layout"]), "PYTHONPATH": os.environ.get("PYTHONPATH", "")},
        timeout=1800,
    )
    receipt = {}
    tasks = repo / ".rift" / "tasks"
    if tasks.is_dir():
        newest = sorted(tasks.iterdir())[-1]
        rp = newest / "receipt.json"
        if rp.is_file():
            receipt = json.loads(rp.read_text(encoding="utf-8"))
    return {
        "accepted": receipt.get("verdict") == "verified_against_approved_checks",
        "seconds": time.time() - t0,
        "commands": receipt.get("commands", 0),
        "verdict": receipt.get("verdict", "no_receipt"),
        "rejected_phase": receipt.get("rejected_phase"),
        "exit_code": proc.returncode,
        "detail": receipt.get("reason", "")[:200],
    }


def cmd_run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    work = Path(args.work)
    staging = Path(args.staging)
    staging.mkdir(parents=True, exist_ok=True)
    for name, pin in (manifest.get("repositories") or {}).items():
        repo = work / name
        if not repo.exists():
            print(f"REFUSING TO RUN: {name} is missing from the work tree")
            return 1
        head = git(repo, "rev-parse", pin["commit"], check=False).strip()
        if head != pin["commit"]:
            print(f"REFUSING TO RUN: {name} no longer contains the manifest commit {pin['commit'][:12]}")
            return 1
    instrument = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if manifest.get("instrument_sha256") not in (None, instrument):
        print("REFUSING TO RUN: the benchmark instrument changed since the manifest was frozen")
        return 1

    records = []
    for i, case in enumerate(manifest["cases"], start=1):
        patch = out / "patches" / case["patch_file"]
        print(f"[{i}/{len(manifest['cases'])}] {case['case_id']} ({case['patch_class']})", flush=True)
        try:
            repo = prepare(case, work, staging)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
            records.append({**case, "error": f"prepare failed: {exc}"})
            continue
        try:
            s = arm_standard(repo, case, patch)
            c = arm_counterfactual(repo, case, patch)
            records.append({**case, "arm_S": s, "arm_C": c})
            print(f"    S accepted={s['accepted']}   C accepted={c['accepted']} ({c.get('verdict')})", flush=True)
        except subprocess.TimeoutExpired as exc:
            records.append({**case, "error": f"timeout: {exc}"})
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    (out / "results.json").write_text(
        json.dumps({"manifest_hash": manifest["manifest_hash"], "records": records}, indent=1), encoding="utf-8"
    )
    print(f"\nwrote {len(records)} records")
    return 0


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def cmd_report(args: argparse.Namespace) -> int:
    out = Path(args.out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    if results["manifest_hash"] != manifest["manifest_hash"]:
        print("REFUSING TO REPORT: results were produced against a different manifest")
        return 1
    instrument = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if manifest.get("instrument_sha256") not in (None, instrument):
        print("REFUSING TO REPORT: the benchmark instrument changed since the manifest was frozen")
        return 1
    usable = [r for r in results["records"] if "error" not in r]
    errored = [r for r in results["records"] if "error" in r]
    correct = [r for r in usable if r["expected_accept"]]
    bad = [r for r in usable if not r["expected_accept"]]

    def rate(rows, arm, want):
        if not rows:
            return None
        return sum(1 for r in rows if r[arm]["accepted"] is want) / len(rows)

    lines = [
        "M1a verify benchmark",
        f"manifest {manifest['manifest_hash'][:16]}  cases {len(results['records'])}  "
        f"usable {len(usable)}  errored {len(errored)}",
        f"repositories: {sorted({r['repo'] for r in usable})}",
        "",
        f"correct patches        : {len(correct)}",
        f"known-bad patches      : {len(bad)}  {sorted({r['patch_class'] for r in bad})}",
        "",
        "                            arm S (standard)   arm C (counterfactual)",
    ]

    def pct(v):
        return "   n/a  " if v is None else f"  {v * 100:5.1f}%"

    gap = " " * 12
    lines.append(
        f"correct-patch acceptance {pct(rate(correct, 'arm_S', True))}{gap}{pct(rate(correct, 'arm_C', True))}"
    )
    lines.append(f"incorrect-patch acceptance{pct(rate(bad, 'arm_S', True))}{gap}{pct(rate(bad, 'arm_C', True))}")
    lines.append(
        f"false rejection (correct) {pct(rate(correct, 'arm_S', False))}{gap}{pct(rate(correct, 'arm_C', False))}"
    )

    s_accept = rate(correct, "arm_S", True)
    c_accept = rate(correct, "arm_C", True)
    retention = None if not s_accept else (c_accept or 0.0) / s_accept
    shown = "n/a" if retention is None else f"{retention * 100:.1f}%"
    lines += ["", f"correct-patch retention of arm S: {shown} (acceptance floor: 90%)"]
    for arm, label in (("arm_S", "S"), ("arm_C", "C")):
        secs = sum(r[arm]["seconds"] for r in usable)
        cmds = sum(r[arm].get("commands", 0) for r in usable)
        lines.append(f"arm {label}: {secs:8.1f}s total, {cmds} commands")

    by_class: dict[str, list] = {}
    for r in bad:
        by_class.setdefault(r["patch_class"], []).append(r)
    lines.append("")
    lines.append("known-bad acceptance by class:")
    for klass, rows in sorted(by_class.items()):
        lines.append(
            f"  {klass:18s} n={len(rows):2d}  S accepted {sum(1 for r in rows if r['arm_S']['accepted'])}"
            f"   C accepted {sum(1 for r in rows if r['arm_C']['accepted'])}"
        )
    if errored:
        lines.append("")
        lines.append(f"EXCLUDED (harness errors, not results): {len(errored)}")
        for r in errored[:10]:
            lines.append(f"  {r['case_id']}: {r['error'][:120]}")

    text = "\n".join(lines)
    (out / "report.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="verify_bench")
    parser.add_argument("--out", default="benchmark/frozen")
    parser.add_argument("--work", default="benchmark/work/repos")
    parser.add_argument("--staging", default="benchmark/work/staging")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--pairs", type=int, default=15)
    b.add_argument("--per-repo", type=int, default=4)
    b.add_argument("--scan", type=int, default=400)
    b.set_defaults(func=cmd_build)
    r = sub.add_parser("run")
    r.set_defaults(func=cmd_run)
    p = sub.add_parser("report")
    p.set_defaults(func=cmd_report)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
