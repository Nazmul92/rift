"""BM-06 targeted curation pass — bounded, model-free, no provider request.

Three classes could not be filled by the widened round, each for a different
reason, so each gets a different treatment rather than more of the same walking:

* **missing_dependency** — zero confirmed, and structurally so: the widened round
  installed every repository's dependencies as declared at `HEAD`, and these are
  exactly the commits that *change* what a project depends on. The environment
  was wrong for one side of the comparison by construction. Here each candidate
  gets an environment built from **its own parent commit's declarations**.
  Per case, not a resolver: nothing is cached, generalised or reused.
* **nondeterminism** — a single run cannot distinguish a real intermittent
  failure from a lucky one. The repetition count and threshold are frozen below,
  before execution.
* **two_cause** — a commit message claiming two fixes is not evidence of two
  causes. Joint executed evidence is required.

Bounded: `MAX_CONFIRMATIONS` additional candidates, total. If the allocation
still cannot be filled within that budget, the run stops and reports protocol
infeasibility rather than expanding again.

Network is permitted only while an environment is being constructed, and is
disabled for every test execution. Confirmed evidence from the widened round is
never re-run.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import confirm_cases as cc  # noqa: E402 - the confirmation primitives are shared, deliberately
import discover_cases  # noqa: E402

MAX_CONFIRMATIONS = 150

# Frozen before execution (ruling section 5). A candidate is run this many times
# on each side; the threshold is what counts as "fails at the parent". Both are
# fixed for every nondeterminism candidate, so no case is classified from one
# lucky pass or one lucky failure.
NONDET_RUNS = 5
NONDET_PARENT_MIN_FAILURES = 2
NONDET_FIX_REQUIRED_PASSES = 5

# Where a project may declare what it depends on. Read from the parent commit,
# never from HEAD.
DEP_SOURCES = (
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "requirements.txt",
    "requirements/base.txt",
    "requirements-dev.txt",
    "tests/requirements.txt",
    "tox.ini",
)

_MISSING_DEP = re.compile(
    r"(ModuleNotFoundError|ImportError).{0,120}?(No module named|cannot import name)", re.IGNORECASE | re.DOTALL
)


def declared_at(repo: Path, commit: str) -> list[str]:
    """Which dependency declarations exist *at this commit*.

    Recorded rather than assumed: the manifest has to be able to say where a
    historical environment came from, and "we installed something that worked"
    is not an answer a reviewer can check.
    """
    listing = cc.git(repo, "ls-tree", "-r", "--name-only", commit).stdout.splitlines()
    present = {line.strip() for line in listing}
    return [source for source in DEP_SOURCES if source in present]


def build_parent_env(repo: Path, parent: str, venv: Path) -> dict:
    """One environment for one candidate, from that candidate's parent commit.

    Network is on here and off during the run that follows. Nothing about this
    is a general historical-environment resolver: it installs what the parent
    commit's own files declare, records the commands, and gives up honestly if
    that is not enough.
    """
    record: dict = {"venv": str(venv), "sources": declared_at(repo, parent), "commands": [], "built": False}
    if not record["sources"]:
        record["reason"] = "the parent commit declares no dependency source this pass can read"
        return record

    if not (venv / "bin" / "python").exists():
        cc.run([cc.PYTHON, "-m", "venv", str(venv)], timeout=600)
    python = str(venv / "bin" / "python")
    cc.run([python, "-m", "pip", "install", "-q", "-U", "pip"], timeout=600)

    # The project itself, as it stood at the parent. `--no-build-isolation` is
    # deliberately NOT used: the parent's own build requirements should apply.
    proc = cc.run([python, "-m", "pip", "install", "-q", "-e", "."], cwd=repo, timeout=1800)
    record["commands"].append(f"pip install -e . (at {parent[:12]}) -> rc={proc.returncode}")
    if proc.returncode != 0:
        proc = cc.run([python, "-m", "pip", "install", "-q", "."], cwd=repo, timeout=1800)
        record["commands"].append(f"pip install . (at {parent[:12]}) -> rc={proc.returncode}")
    if proc.returncode != 0:
        record["reason"] = f"the parent commit's own package will not install: {proc.stderr.strip()[-160:]}"
        return record

    for rel in record["sources"]:
        if rel.endswith(".txt"):
            got = cc.run([python, "-m", "pip", "install", "-q", "-r", rel], cwd=repo, timeout=1800)
            record["commands"].append(f"pip install -r {rel} -> rc={got.returncode}")
    for extra in cc.TEST_EXTRAS:
        got = cc.run([python, "-m", "pip", "install", "-q", "-e", f".[{extra}]"], cwd=repo, timeout=1800)
        if got.returncode == 0 and "does not provide the extra" not in (got.stdout + got.stderr):
            record["commands"].append(f"pip install -e .[{extra}]")
            break

    probe = cc.run([python, "-c", "import pytest; print(pytest.__version__)"], cwd=repo, timeout=300)
    if probe.returncode != 0:
        # No version is guessed. If the parent did not say, the harness says so.
        got = cc.run([python, "-m", "pip", "install", "-q", "pytest"], cwd=repo, timeout=1800)
        record["commands"].append(f"pip install pytest (undeclared at the parent) -> rc={got.returncode}")
        probe = cc.run([python, "-c", "import pytest; print(pytest.__version__)"], cwd=repo, timeout=300)
    record["pytest"] = probe.stdout.strip() if probe.returncode == 0 else "unavailable"
    record["built"] = probe.returncode == 0
    if not record["built"]:
        record["reason"] = "pytest could not be made available in the reconstructed environment"
    return record


def outcome_with(python: str, repo: Path, node: str, src: str) -> tuple[str, str]:
    """`cc.outcome`, but through a named interpreter."""
    original = cc.python_for
    cc.python_for = lambda _repo: python  # type: ignore[assignment]
    try:
        return cc.outcome(repo, node, src)
    finally:
        cc.python_for = original  # type: ignore[assignment]


def suite_targets(python: str, repo: Path, src: str, limit: int = 12) -> tuple[list[str], bool]:
    """Failing nodes at the current checkout, through a named interpreter.

    The first version of this pass required every candidate to carry its own
    test file, and 56 of 82 attempts were rejected for not having one. That is
    not a property of the candidates — the widened round confirmed cases from
    source-only commits routinely, via the repository's existing suite. Omitting
    the same fallback here turned a gap in this harness into what would have been
    reported as a corpus limit.
    """
    original = cc.python_for
    cc.python_for = lambda _repo: python  # type: ignore[assignment]
    try:
        return cc.suite_failures(repo, src, limit=limit)
    finally:
        cc.python_for = original  # type: ignore[assignment]


def confirm_missing_dependency(repo: Path, cand: dict, src: str, work: Path) -> dict:
    """Accept only on the class's own signature, observed at the parent."""
    rec = {
        "repo": repo.name,
        "cause_class": "missing_dependency",
        "fix_commit": cand["sha"],
        "subject": cand.get("subject", ""),
        "pass": "targeted/historical-environment",
        "accepted": False,
        "reason": "",
    }
    parent = cc.git(repo, "rev-parse", f"{cand['sha']}^").stdout.strip()
    if not parent:
        rec["reason"] = "no parent commit"
        return rec
    rec["parent"] = parent

    tests = [t for t in cand.get("tests", []) if t.endswith(".py")]

    if cc.git(repo, "checkout", "-q", "--force", parent).returncode != 0:
        rec["reason"] = "could not check out the parent commit"
        return rec
    cc.git(repo, "clean", "-qfd")
    env = build_parent_env(repo, parent, work / ".venvs-historical" / f"{repo.name}-{parent[:12]}")
    rec["environment"] = env
    if not env["built"]:
        rec["reason"] = f"parent environment not reconstructible: {env.get('reason', '')}"
        rec["harness_limited"] = True
        return rec

    python = str(Path(env["venv"]) / "bin" / "python")
    # The commit's test half, so the target exists at the parent at all.
    patch = cc.git(repo, "diff", f"{parent}..{cand['sha']}", "--", *tests).stdout
    if patch.strip():
        applied = subprocess.run(
            ["git", "-C", str(repo), "apply", "--3way"], input=patch, capture_output=True, text=True, timeout=120
        )
        if applied.returncode != 0:
            rec["reason"] = "the commit's test half does not apply to its parent"
            return rec

    if tests:
        original = cc.python_for
        cc.python_for = lambda _repo: python  # type: ignore[assignment]
        try:
            nodes = cc.node_ids(repo, tests, src)
        finally:
            cc.python_for = original  # type: ignore[assignment]
        rec["target_origin"] = "test file in the fix commit"
    else:
        # Source-only commit: the repository's existing suite, run in the
        # reconstructed parent environment, is where the reproducer will be.
        nodes, ran = suite_targets(python, repo, src)
        rec["target_origin"] = "existing suite at the parent, reconstructed environment"
        if not ran:
            rec["reason"] = "the parent suite could not be run in the reconstructed environment"
            rec["harness_limited"] = True
            return rec
    if not nodes:
        rec["reason"] = "no collectable node ids in the reconstructed environment"
        rec["harness_limited"] = True
        return rec

    for node in nodes:
        verdict, sig = outcome_with(python, repo, node, src)
        if verdict not in ("FAILED", "ERROR"):
            continue
        if not _MISSING_DEP.search(sig):
            # A failure at the parent is not enough. The class is defined by its
            # signature, and accepting any failure here would let an ordinary
            # source bug be filed as a missing dependency.
            rec.setdefault("rejected_signatures", []).append(sig[:120])
            continue
        cc.git(repo, "checkout", "-q", "--force", cand["sha"])
        cc.git(repo, "clean", "-qfd")
        fixed, _ = outcome_with(python, repo, node, src)
        if fixed != "PASSED":
            cc.git(repo, "checkout", "-q", "--force", parent)
            cc.git(repo, "clean", "-qfd")
            continue
        rec.update(
            {
                "target": node,
                "parent_outcome": verdict,
                "fixed_outcome": "PASSED",
                "signature": sig,
                "accepted": True,
                "reason": "missing-dependency signature at the parent, removed by the fix",
            }
        )
        return rec

    rec["reason"] = "no node showed a missing-dependency signature at the parent that the fix removed"
    return rec


def confirm_nondeterminism(repo: Path, cand: dict, src: str) -> dict:
    """Repeated runs, frozen count and threshold, every repetition recorded."""
    rec = {
        "repo": repo.name,
        "cause_class": "nondeterminism",
        "fix_commit": cand["sha"],
        "subject": cand.get("subject", ""),
        "pass": "targeted/repeated-runs",
        "runs": NONDET_RUNS,
        "parent_min_failures": NONDET_PARENT_MIN_FAILURES,
        "accepted": False,
        "reason": "",
    }
    parent = cc.git(repo, "rev-parse", f"{cand['sha']}^").stdout.strip()
    if not parent:
        rec["reason"] = "no parent commit"
        return rec
    rec["parent"] = parent
    tests = [t for t in cand.get("tests", []) if t.endswith(".py")]

    if cc.git(repo, "checkout", "-q", "--force", cand["sha"]).returncode != 0:
        rec["reason"] = "could not check out the fix commit"
        return rec
    cc.git(repo, "clean", "-qfd")
    if tests:
        nodes = cc.node_ids(repo, tests, src)
        rec["target_origin"] = "test file in the fix commit"
    else:
        # Source-only: take the parent's failing nodes as the candidate targets,
        # then apply the frozen repetition rule to them.
        cc.git(repo, "checkout", "-q", "--force", parent)
        cc.git(repo, "clean", "-qfd")
        nodes, ran = cc.suite_failures(repo, src)
        rec["target_origin"] = "existing suite at the parent"
        cc.git(repo, "checkout", "-q", "--force", cand["sha"])
        cc.git(repo, "clean", "-qfd")
        if not ran:
            rec["reason"] = "the parent suite could not be run"
            rec["harness_limited"] = True
            return rec
    if not nodes:
        rec["reason"] = "no collectable node ids"
        rec["harness_limited"] = True
        return rec

    for node in nodes:
        cc.git(repo, "checkout", "-q", "--force", cand["sha"])
        cc.git(repo, "clean", "-qfd")
        fix_runs = [cc.outcome(repo, node, src) for _ in range(NONDET_RUNS)]
        fix_verdicts = [v for v, _ in fix_runs]
        if fix_verdicts.count("PASSED") < NONDET_FIX_REQUIRED_PASSES:
            rec.setdefault("observations", []).append({"node": node, "at_fix": fix_verdicts})
            continue

        cc.git(repo, "checkout", "-q", "--force", parent)
        cc.git(repo, "clean", "-qfd")
        patch = cc.git(repo, "diff", f"{parent}..{cand['sha']}", "--", *tests).stdout
        if patch.strip():
            applied = subprocess.run(
                ["git", "-C", str(repo), "apply", "--3way"], input=patch, capture_output=True, text=True, timeout=120
            )
            if applied.returncode != 0:
                continue
        parent_runs = [cc.outcome(repo, node, src) for _ in range(NONDET_RUNS)]
        parent_verdicts = [v for v, _ in parent_runs]
        failures = sum(1 for v in parent_verdicts if v in ("FAILED", "ERROR"))
        rec.setdefault("observations", []).append(
            {"node": node, "at_fix": fix_verdicts, "at_parent": parent_verdicts, "parent_failures": failures}
        )
        if failures >= NONDET_PARENT_MIN_FAILURES:
            rec.update(
                {
                    "target": node,
                    "parent_outcome": f"{failures}/{NONDET_RUNS} failed",
                    "fixed_outcome": f"{fix_verdicts.count('PASSED')}/{NONDET_RUNS} passed",
                    "signature": next((s for v, s in parent_runs if v != "PASSED" and s), ""),
                    "intermittent": 0 < failures < NONDET_RUNS,
                    "accepted": True,
                    "reason": (
                        f"failed {failures}/{NONDET_RUNS} at the parent and passed "
                        f"{fix_verdicts.count('PASSED')}/{NONDET_RUNS} at the fix"
                    ),
                }
            )
            return rec

    rec["reason"] = f"no node reached {NONDET_PARENT_MIN_FAILURES}/{NONDET_RUNS} failures at the parent"
    return rec


def confirm_two_cause(repo: Path, cand: dict, src: str) -> dict:
    """Two causes require joint executed evidence, not a commit message.

    The commit must repair **two distinct targets** that each fail at the parent
    and pass at the fix. Two independently observed failures combined into a
    conjunction nobody executed is exactly the inference this rejects; here both
    targets are run at both commits, so the joint experiment is the evidence.
    """
    rec = {
        "repo": repo.name,
        "cause_class": "two_cause",
        "fix_commit": cand["sha"],
        "subject": cand.get("subject", ""),
        "pass": "targeted/joint-evidence",
        "accepted": False,
        "reason": "",
    }
    parent = cc.git(repo, "rev-parse", f"{cand['sha']}^").stdout.strip()
    if not parent:
        rec["reason"] = "no parent commit"
        return rec
    rec["parent"] = parent
    tests = [t for t in cand.get("tests", []) if t.endswith(".py")]

    if cc.git(repo, "checkout", "-q", "--force", cand["sha"]).returncode != 0:
        rec["reason"] = "could not check out the fix commit"
        return rec
    cc.git(repo, "clean", "-qfd")
    if tests:
        candidates = cc.node_ids(repo, tests, src)
        rec["target_origin"] = "test files in the fix commit"
    else:
        # Source-only: the parent's own failing nodes are the candidate targets.
        cc.git(repo, "checkout", "-q", "--force", parent)
        cc.git(repo, "clean", "-qfd")
        candidates, ran = cc.suite_failures(repo, src)
        rec["target_origin"] = "existing suite at the parent"
        cc.git(repo, "checkout", "-q", "--force", cand["sha"])
        cc.git(repo, "clean", "-qfd")
        if not ran:
            rec["reason"] = "the parent suite could not be run"
            rec["harness_limited"] = True
            return rec
    nodes = [n for n in candidates if cc.outcome(repo, n, src)[0] == "PASSED"]
    if len(nodes) < 2:
        rec["reason"] = "fewer than two passing targets at the fix; a second cause cannot be shown"
        return rec

    cc.git(repo, "checkout", "-q", "--force", parent)
    cc.git(repo, "clean", "-qfd")
    patch = cc.git(repo, "diff", f"{parent}..{cand['sha']}", "--", *tests).stdout
    if patch.strip():
        applied = subprocess.run(
            ["git", "-C", str(repo), "apply", "--3way"], input=patch, capture_output=True, text=True, timeout=120
        )
        if applied.returncode != 0:
            rec["reason"] = "the commit's test half does not apply to its parent"
            return rec

    failing: list[tuple[str, str]] = []
    for node in nodes:
        verdict, sig = cc.outcome(repo, node, src)
        if verdict in ("FAILED", "ERROR"):
            failing.append((node, sig))
    rec["failing_at_parent"] = [{"node": n, "signature": s[:140]} for n, s in failing]

    distinct = {s.split(":")[0] for _, s in failing if s}
    if len(failing) < 2:
        rec["reason"] = f"only {len(failing)} target(s) failed at the parent; two_cause needs two"
        return rec
    if len(distinct) < 2:
        # Two failures of the same kind are one cause observed twice.
        rec["reason"] = "both failing targets share one signature kind; this is one cause, not two"
        return rec

    rec.update(
        {
            "target": failing[0][0],
            "second_target": failing[1][0],
            "parent_outcome": "FAILED (both targets)",
            "fixed_outcome": "PASSED (both targets)",
            "signature": failing[0][1],
            "second_signature": failing[1][1],
            "accepted": True,
            "reason": "two targets with distinct signatures failed at the parent and pass at the fix",
        }
    )
    return rec


def main() -> int:
    parser = argparse.ArgumentParser(prog="targeted_pass")
    parser.add_argument("--work", default="/repos")
    parser.add_argument("--candidates", default="benchmark/bm06/candidates.json")
    parser.add_argument("--layouts", default="benchmark/bm06/src-layouts.json")
    parser.add_argument("--prior", default="benchmark/bm06/stage2-wide-records.json")
    parser.add_argument("--out", default="benchmark/bm06/targeted-records.json")
    parser.add_argument("--budget", type=int, default=MAX_CONFIRMATIONS)
    parser.add_argument("--per-class", type=int, default=25)
    parser.add_argument("--only", default="", help="restrict to one cause class")
    args = parser.parse_args()

    work = Path(args.work)
    cands = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    layouts = json.loads(Path(args.layouts).read_text(encoding="utf-8")) if Path(args.layouts).is_file() else {}
    prior = json.loads(Path(args.prior).read_text(encoding="utf-8"))
    # Evidence already valid is never re-run.
    done = {(r["repo"], r["fix_commit"]) for r in prior if r.get("accepted")}

    handlers = {
        "missing_dependency": lambda repo, cand, src: confirm_missing_dependency(repo, cand, src, work),
        "two_cause": confirm_two_cause,
        "nondeterminism": confirm_nondeterminism,
    }
    targets = {"missing_dependency": 4, "two_cause": 2, "nondeterminism": 1}
    if args.only:
        targets = {args.only: targets.get(args.only, 1)}

    records: list[dict] = []
    spent = 0
    for cls, want in targets.items():
        got = 0
        for repo_name, _ in discover_cases.REPOS:
            if got >= want or spent >= args.budget:
                break
            repo = work / repo_name
            hits = cands["repos"].get(repo_name, {}).get(cls, [])
            if not repo.exists() or not hits:
                continue
            src = layouts.get(repo_name, "")
            for cand in hits[: args.per_class]:
                if got >= want or spent >= args.budget:
                    break
                if (repo_name, cand["sha"]) in done:
                    continue
                spent += 1
                try:
                    rec = handlers[cls](repo, cand, src)
                except Exception as exc:  # noqa: BLE001
                    rec = {
                        "repo": repo_name,
                        "cause_class": cls,
                        "fix_commit": cand["sha"],
                        "accepted": False,
                        "reason": f"harness error: {type(exc).__name__}: {exc}"[:200],
                    }
                records.append(rec)
                if rec.get("accepted"):
                    got += 1
                flag = "ACCEPT" if rec.get("accepted") else "reject"
                print(
                    f"[{repo_name}/{cls}] {cand['sha'][:10]} {flag}: {rec.get('reason', '')[:88]}"
                    f"  ({got}/{want}, budget {spent}/{args.budget})",
                    flush=True,
                )
        if got < want:
            print(f"  !! {cls}: {got}/{want}", flush=True)

    Path(args.out).write_text(json.dumps(records, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    accepted = [r for r in records if r.get("accepted")]
    limited = [r for r in records if r.get("harness_limited")]
    print(f"\nattempted {len(records)}/{args.budget} budget, accepted {len(accepted)}, harness-limited {len(limited)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
