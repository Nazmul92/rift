"""BM-06 stage 2 — candidate confirmation. Model-free; makes no provider request.

A grep hit is never a case. Stage 1 produced candidates from commit messages;
this stage checks out each candidate's parent, runs the tests its fix touched,
and keeps only those that genuinely **fail at the parent and pass at the fix**.

Two rules the ruling makes non-negotiable, both enforced here rather than
promised:

1. **Every candidate gets a durable record**, accepted or rejected, with the
   exact reason. A confirmation pass that only records its successes is a
   selection procedure whose bias nobody can audit.
2. **No substitution.** A class that comes up short comes up short. This script
   never fills a shortfall from an easier class, and never manufactures a
   fixture.

Run phases separately so test execution can be network-isolated:

    install   network ON  — clone and install each project
    confirm   network OFF — check out, run, record. No egress at all.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discover_cases  # noqa: E402 - the frozen repository order lives with the survey that fixed it

CONTAINER_IMAGE = os.environ.get("BM06_IMAGE", "python:3.12-slim")
# The confirm phase runs with networking disabled, so the interpreter it uses
# must already carry every dependency. `install` builds that interpreter inside
# the repository volume; `confirm` points at it here.
PYTHON = os.environ.get("BM06_PYTHON", sys.executable)


def python_for(repo: Path) -> str:
    """The interpreter for one repository, in its own virtual environment.

    A shared environment cannot work here, and the failure is not subtle. Five
    admitted repositories — pluggy, packaging, attrs, jsonschema, more-itertools
    — are dependencies of pytest itself. Installed editable into one shared
    venv, checking out the *pluggy* repository at a commit predating its `src/`
    layout leaves `pluggy.__file__` as `None`, and pytest then dies at startup
    for **every other repository in the corpus**. The suite of an unrelated
    project appears to vanish because of where a different project's git
    checkout happens to be.

    One environment per repository makes the checkout of one project unable to
    affect the tests of another.
    """
    venv = repo.parent / ".venvs" / repo.name
    candidate = venv / "bin" / "python"
    return str(candidate) if candidate.exists() else PYTHON


# The M1a scan supplies genuine_source_bug candidates. They are carried in the
# stage-2 input explicitly rather than referenced as "from M1a", so every case
# in the manifest is confirmed by the same procedure.
M1A_MANIFEST = Path("benchmark/frozen/manifest.json")

_TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]+\.py$|_test\.py$")


def run(cmd: list[str], cwd: Path | None = None, timeout: float = 900.0, env: dict | None = None):
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, env=env, errors="replace"
    )


def run_input(cmd: list[str], text: str, timeout: float = 300.0) -> int:
    """Run a command with `text` on stdin. Returns the exit code."""
    return subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=timeout).returncode


def git(repo: Path, *args: str, timeout: float = 300.0) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(repo), *args], timeout=timeout)


def pytest_env(repo: Path, src: str) -> dict[str, str]:
    """A minimal environment. No credentials, no home directory, no inherited
    secrets — the same allowlist discipline the runtime's sandbox applies."""
    root = str(repo / src) if src else str(repo)
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp/bm06-home",
        "LANG": "C.UTF-8",
        "PYTHONPATH": root,
        "PYTHONDONTWRITEBYTECODE": "1",
        # Plugin autoload stays ON. Disabling it looked like extra hermeticity
        # and was in fact a defect: repositories whose suites need a plugin
        # (attrs needs hypothesis) collected nothing, and every candidate in
        # them was rejected for a reason that was about the harness rather than
        # about the candidate. Isolation here comes from the container — no
        # network, no home, no credentials — not from crippling the runner.
    }


# ---------------------------------------------------------------- install


# A project's test dependencies are declared by the project, not chosen here.
# These are the names projects actually use for that declaration; the installer
# tries each and records which one applied. Nothing repo-specific is hard-coded,
# so a shortfall can never be blamed on a dependency somebody forgot to list.
TEST_GROUPS = ("tests", "testing", "dev")
TEST_EXTRAS = ("tests", "testing", "test", "dev")
TEST_REQUIREMENTS = ("tests/requirements.txt", "requirements/tests.txt", "test-requirements.txt")


def install_test_deps(repo: Path) -> list[str]:
    """Install whatever the project declares as its own test dependencies.

    Without these, a suite does not fail — it fails to *collect*, and every
    candidate in the repository is rejected for a reason that is about this
    harness rather than about the candidate. attrs needs hypothesis, jinja needs
    trio, werkzeug needs pytest-xprocess; none of that is discoverable from
    `pip install -e .` alone.
    """
    applied: list[str] = []
    for group in TEST_GROUPS:
        if (
            run([python_for(repo), "-m", "pip", "install", "-q", "--group", group], cwd=repo, timeout=1800).returncode
            == 0
        ):
            applied.append(f"--group {group}")
            break
    for extra in TEST_EXTRAS:
        proc = run([python_for(repo), "-m", "pip", "install", "-q", "-e", f".[{extra}]"], cwd=repo, timeout=1800)
        # pip exits 0 for an extra the project does not provide, warning instead.
        # Recording that as applied would put a dependency set in the frozen
        # record that was never installed.
        if proc.returncode == 0 and "does not provide the extra" not in (proc.stdout + proc.stderr):
            applied.append(f".[{extra}]")
            break
    for rel in TEST_REQUIREMENTS:
        if (repo / rel).is_file():
            if run([python_for(repo), "-m", "pip", "install", "-q", "-r", rel], cwd=repo, timeout=1800).returncode == 0:
                applied.append(rel)
    return applied


def project_test_prereqs(repo: Path) -> list[str]:
    """Run the data-generation steps the project itself declares its tests need.

    Some projects generate test data rather than committing it, and say so in
    their Makefile: babel's is `test: import-cldr`, which downloads and imports
    the CLDR locale database. Running the suite without it is not a stricter
    test, it is a wrongly configured one — every locale test errors with
    `UnknownLocaleError`, and every candidate in the project would be rejected
    for a reason belonging to this harness. This is the same category as
    installing a declared test dependency, so it is done in the same phase,
    while the network is up.

    Only prerequisites of the project's own `test` target are run, and only the
    commands the project wrote. Nothing is invented here.
    """
    makefile = repo / "Makefile"
    if not makefile.is_file():
        return []
    text = makefile.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^test:[ \t]*([^\n#]*)", text, re.MULTILINE)
    if not match:
        return []
    applied: list[str] = []
    for target in match.group(1).split():
        if target in ("test", ".venv"):  # self-reference, or a venv this harness already provides
            continue
        body = re.search(rf"^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n)+)", text, re.MULTILINE)
        if not body:
            continue
        for line in body.group(1).splitlines():
            cmd = line.strip().lstrip("@-")
            # Only the project's own Python build scripts, never an arbitrary
            # shell line out of a Makefile.
            if not cmd.startswith("python ") or ";" in cmd or "|" in cmd:
                continue
            if run([python_for(repo), *cmd.split()[1:]], cwd=repo, timeout=1800).returncode == 0:
                applied.append(f"make {target}: {cmd}")
    return applied


def ensure_pytest(repo: Path) -> str:
    """Install pytest only if the project's own declarations did not."""
    probe = run([python_for(repo), "-c", "import pytest; print(pytest.__version__)"], cwd=repo, timeout=300)
    if probe.returncode == 0:
        return f"declared by the project: {probe.stdout.strip()}"
    run([python_for(repo), "-m", "pip", "install", "-q", "pytest"], cwd=repo, timeout=1800)
    probe = run([python_for(repo), "-c", "import pytest; print(pytest.__version__)"], cwd=repo, timeout=300)
    return f"installed by the harness: {probe.stdout.strip()}" if probe.returncode == 0 else "unavailable"


def install(work: Path, repos: list[str]) -> dict:
    """Network ON. Clone and install; nothing here decides anything."""
    report: dict[str, dict] = {}
    for name in repos:
        repo = work / name
        if not repo.exists():
            report[name] = {"installed": False, "reason": "not cloned by stage 1"}
            continue
        # Stage 1 cloned with --filter=blob:none. The confirm phase runs with
        # egress cut, where git cannot fetch a missing blob and every checkout
        # fails. Materialise the history now, while the network is still up.
        #
        # Unsetting the two promisor keys first is the load-bearing part:
        # `--refetch` re-applies whatever filter is still configured, so a
        # refetch alone leaves the clone just as partial as it was. Skipping
        # this produced 92 "could not check out the fix commit" rejections that
        # said nothing about the candidates.
        run(["git", "-C", str(repo), "config", "--unset", "remote.origin.promisor"], timeout=60)
        run(["git", "-C", str(repo), "config", "--unset", "remote.origin.partialclonefilter"], timeout=60)
        run(["git", "-C", str(repo), "fetch", "--refetch", "--quiet", "origin"], timeout=3600)
        # Install from the default tip, whatever a previous confirm run left
        # checked out, so the installed metadata is reproducible rather than a
        # function of run order.
        head = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip() or "origin/HEAD"
        git(repo, "checkout", "-q", "--force", head)
        git(repo, "clean", "-qfd")
        # One environment per repository. See `python_for` for why a shared one
        # silently destroys other repositories' results.
        venv = repo.parent / ".venvs" / repo.name
        if not (venv / "bin" / "python").exists():
            run([PYTHON, "-m", "venv", str(venv)], timeout=600)
        run([python_for(repo), "-m", "pip", "install", "-q", "-U", "pip"], cwd=repo, timeout=600)
        proc = run([python_for(repo), "-m", "pip", "install", "-q", "-e", "."], cwd=repo, timeout=1800)
        ok = proc.returncode == 0
        if not ok:
            proc = run([python_for(repo), "-m", "pip", "install", "-q", "."], cwd=repo, timeout=1800)
            ok = proc.returncode == 0
        report[name] = {
            "installed": ok,
            "test_deps": install_test_deps(repo) if ok else [],
            "test_prereqs": project_test_prereqs(repo) if ok else [],
            # Last, and only if the project's own declarations did not already
            # supply it. Installing pytest *first* with -U silently upgrades past
            # the version a project pins, which breaks the suites of older
            # projects — a harness-imposed failure dressed as a project one.
            "pytest": ensure_pytest(repo) if ok else "",
            "reason": "" if ok else proc.stderr.strip().splitlines()[-1][:200] if proc.stderr.strip() else "unknown",
        }
        print(
            f"[{name}] installed={ok} deps={report[name]['test_deps']} prereqs={report[name]['test_prereqs']}",
            flush=True,
        )
    return report


# ---------------------------------------------------------------- confirm


def node_ids(repo: Path, test_files: list[str], src: str) -> list[str]:
    """Collect the node ids a candidate's test files declare."""
    out: list[str] = []
    for rel in test_files:
        if not (repo / rel).is_file():
            continue
        proc = run(
            [python_for(repo), "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", rel],
            cwd=repo,
            env=pytest_env(repo, src),
            timeout=300,
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if "::" in line and line.startswith(rel):
                out.append(line)
    return out[:12]


def outcome(repo: Path, node: str, src: str) -> tuple[str, str]:
    """Run one node. Returns (verdict, signature-ish detail)."""
    proc = run(
        [python_for(repo), "-m", "pytest", "-q", "--tb=line", "-rA", "-p", "no:cacheprovider", node],
        cwd=repo,
        env=pytest_env(repo, src),
        timeout=600,
    )
    text = proc.stdout + proc.stderr
    for line in text.splitlines():
        stripped = line.strip()
        verdict, _, rest = stripped.partition(" ")
        if verdict in ("PASSED", "FAILED", "ERROR") and (rest.strip() == node or rest.strip().startswith(node + " ")):
            sig = ""
            for candidate in text.splitlines():
                if candidate.strip().startswith("E "):
                    sig = candidate.strip()[1:].strip()[:160]
            return verdict, sig
    if "no tests ran" in text.lower():
        return "NOTCOLLECTED", ""
    return "UNOBSERVED", text.strip().splitlines()[-1][:160] if text.strip() else ""


def suite_ran(text: str) -> bool:
    """Did pytest actually report on a suite, or did it fail to run one?

    An empty failure list means "everything passed" only if pytest got far
    enough to say so. When it dies during startup — a broken dependency, an
    unknown option in an old config — it prints a traceback containing no
    `FAILED` lines, and a parser that equates *no parsed failures* with *green*
    reports a healthy suite for a run that never happened. That mistake rejected
    142 candidates in one pass, every one of them for a reason belonging to this
    harness.

    So the green verdict now requires positive evidence: a pytest summary line.
    """
    return bool(re.search(r"\b\d+ (passed|failed|error|skipped|deselected|xfailed)", text) or "no tests ran" in text)


# Repositories whose suite proved too slow for the whole-suite confirmation
# path, learned during the run rather than configured. filelock's suite takes
# ~857s, so one source-only candidate would cost ~29 minutes across its parent
# and fix runs. Recorded per repository so the cost is paid once and the
# skipped candidates carry a reason that names the harness, not the candidate.
SLOW_SUITES: dict[str, float] = {}
SUITE_CAP_S = float(os.environ.get("BM06_SUITE_CAP_S", "300"))


def suite_failures(repo: Path, src: str, limit: int = 12) -> tuple[list[str], bool]:
    """Node ids that fail in the repository's own suite, at whatever commit is
    currently checked out, and whether the suite ran at all.

    Most real fixes are source-only: the regression test already existed, or was
    added in a later commit, so the fix commit's diff contains no test file.
    Rejecting those would not be strictness — it would be a silent bias toward
    the minority of commits that happen to ship their own test, and it drops
    almost every order-dependence candidate, which is the class BM-06 most needs.
    A node that fails at the parent and passes at the fix is a real reproducer
    whether or not the commit's author wrote it.
    """
    started = time.monotonic()
    proc = run(
        # A module that cannot be imported must not abort the scan. chardet's
        # accuracy suite clones its corpus over the network at collection time
        # and errors with egress cut; without this flag that one module hides
        # every reproducer in the repository.
        [
            python_for(repo),
            "-m",
            "pytest",
            "-q",
            "--tb=no",
            "-rf",
            "--continue-on-collection-errors",
            "-p",
            "no:cacheprovider",
        ],
        cwd=repo,
        env=pytest_env(repo, src),
        timeout=1800,
    )
    out: list[str] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        stripped = line.strip()
        verdict, _, rest = stripped.partition(" ")
        if verdict in ("FAILED", "ERROR") and "::" in rest:
            node = rest.split(" ")[0].strip()
            if node not in out:
                out.append(node)
    SLOW_SUITES[repo.name] = max(SLOW_SUITES.get(repo.name, 0.0), time.monotonic() - started)
    return out[:limit], suite_ran(proc.stdout + proc.stderr)


def confirm_source_only(repo: Path, rec: dict, cand: dict, src: str) -> dict:
    """The second confirmation path, for a fix whose diff touches no test file.

    Same acceptance criterion as the first — fail at the parent, pass at the fix
    — reached from the other direction: find the failures the parent already
    has, then see which of them the fix repairs.
    """
    parent = rec["parent"]
    if SLOW_SUITES.get(repo.name, 0.0) > SUITE_CAP_S:
        rec["reason"] = (
            f"whole-suite confirmation skipped: this repository's suite takes "
            f"{SLOW_SUITES[repo.name]:.0f}s, over the {SUITE_CAP_S:.0f}s budget"
        )
        rec["harness_limited"] = True
        return rec
    if git(repo, "checkout", "-q", "--force", parent).returncode != 0:
        rec["reason"] = "could not check out the parent commit"
        return rec
    git(repo, "clean", "-qfd")
    rec["commands"].append(f"git checkout {parent[:12]}; pytest (whole suite)")
    at_parent, ran = suite_failures(repo, src)
    if not ran:
        # Not a statement about the candidate. Kept distinct so a run cannot
        # report an environment failure as an absence of reproducers.
        rec["reason"] = "the parent's suite could not be run: pytest reported no summary"
        rec["harness_limited"] = True
        return rec
    if not at_parent:
        rec["reason"] = "source-only fix: the parent's own suite is green, so it has no reproducer"
        return rec

    if git(repo, "checkout", "-q", "--force", cand["sha"]).returncode != 0:
        rec["reason"] = "could not check out the fix commit"
        return rec
    git(repo, "clean", "-qfd")
    rec["commands"].append(f"git checkout {cand['sha'][:12]}; pytest (whole suite)")
    at_fix_nodes, ran_fix = suite_failures(repo, src, limit=10_000)
    if not ran_fix:
        rec["reason"] = "the fix commit's suite could not be run: pytest reported no summary"
        rec["harness_limited"] = True
        return rec
    at_fix = set(at_fix_nodes)
    repaired = [n for n in at_parent if n not in at_fix]
    if not repaired:
        rec["reason"] = "source-only fix: every node failing at the parent still fails at the fix"
        return rec

    for node in repaired:
        # In isolation at the fix the node must pass; that is the second half of
        # the criterion and it is the same for both shapes. A previous iteration
        # may have left the tree at the parent, so put it back first.
        git(repo, "checkout", "-q", "--force", cand["sha"])
        git(repo, "clean", "-qfd")
        if outcome(repo, node, src)[0] != "PASSED":
            continue
        git(repo, "checkout", "-q", "--force", parent)
        git(repo, "clean", "-qfd")
        isolated, sig = outcome(repo, node, src)
        if isolated in ("FAILED", "ERROR"):
            ordering, origin = None, "existing suite at the parent"
        else:
            # It failed inside the suite and passes alone. That is not a reason
            # to discard it — it is the definition of an order-dependent
            # failure, and it is the shape BM-06 needs four of. The reproducer
            # is the node plus its ordering precondition, which is what a
            # ReproductionContract carries.
            ordering, origin = (
                "full suite in declared collection order",
                "existing suite at the parent (order-dependent)",
            )
        rec.update(
            {
                "target": node,
                "target_origin": origin,
                "ordering_precondition": ordering,
                "parent_outcome": "FAILED_IN_SUITE" if ordering else isolated,
                "isolated_at_parent": isolated,
                "fixed_outcome": "PASSED",
                "signature": sig,
                "accepted": True,
                "reason": "parent-fail to fixed-pass confirmed (target from the existing suite)",
            }
        )
        return rec

    rec["reason"] = "source-only fix: no node repaired by the fix passes in isolation at the fix"
    return rec


def merge_commit(repo: Path, sha: str) -> str:
    """The merge that first brought `sha` onto the default branch, if any.

    These projects overwhelmingly fix a bug in one commit and add its regression
    test in another commit of the same pull request. Judged commit-by-commit the
    fix looks source-only and the test looks inert, so both are rejected and the
    pull request that plainly contains a reproducer is lost. The merge is the
    unit the project actually reviewed and shipped; evaluating it keeps the two
    halves together without inventing anything.
    """
    head = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip() or "origin/HEAD"
    proc = git(repo, "rev-list", "--ancestry-path", "--merges", f"{sha}..{head}", timeout=600)
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return ""
    oldest = lines[-1]  # rev-list prints newest first
    # A commit made directly on the mainline is followed by merges that have
    # nothing to do with it. Taking one of those would evaluate a different pull
    # request under this candidate's SHA and label — a wrong unit, silently. The
    # merge only shipped this commit if the commit was not already on its
    # first-parent side.
    if git(repo, "merge-base", "--is-ancestor", sha, f"{oldest}^1").returncode == 0:
        return ""
    return oldest


def confirm_one(repo: Path, cand: dict, cls: str, src: str, image: str, prior: dict | None = None) -> dict:
    """One candidate → one durable record, accepted or rejected.

    Two units are tried, in order: the commit itself, then the merge that
    shipped it. The acceptance criterion is identical for both — fail at the
    parent, pass at the fix — so this widens where a reproducer may be found
    without loosening what counts as one.

    `prior` supplies an already-recorded commit-level rejection, so a recovery
    pass re-attempts only the merge unit instead of repeating two full suite
    runs per candidate. The prior reason is carried into the new record.
    """
    rec = prior if prior is not None else confirm_commit(repo, cand, cls, src, image)
    if rec.get("accepted"):
        return rec
    merge = merge_commit(repo, cand["sha"])
    if not merge or merge == cand["sha"]:
        rec["merge_recovery"] = "no merge commit brings this commit onto the default branch"
        return rec

    # `git show` prints no diff for a merge. The pull request's contents are the
    # first-parent diff: mainline before the merge, against the merge itself.
    changed = git(repo, "diff", "--name-only", f"{merge}^1", merge, timeout=600).stdout.splitlines()
    tests = [t.strip() for t in changed if t.strip() and _TEST_PATH.search(t.strip())]
    merged = confirm_commit(repo, {**cand, "sha": merge, "tests": tests}, cls, src, image)
    merged["fix_commit"] = cand["sha"]
    merged["evaluated_commit"] = merge
    merged["evaluated_unit"] = "merge commit (the pull request as shipped)"
    # The commit-level attempt is kept whether or not the merge rescued it. A
    # record that showed only the successful unit would hide how the case was
    # actually found.
    merged["commit_level_attempt"] = {"reason": rec.get("reason", ""), "commands": rec.get("commands", [])}
    return merged


def confirm_commit(repo: Path, cand: dict, cls: str, src: str, image: str) -> dict:
    """One commit → one durable record, accepted or rejected."""
    rec = {
        "repo": repo.name,
        "cause_class": cls,
        "fix_commit": cand["sha"],
        "subject": cand.get("subject", ""),
        "shape": cand.get("shape"),
        "container_image": image,
        "src_layout": src,
        "commands": [],
        "target": None,
        "target_origin": None,
        "ordering_precondition": None,
        "parent": None,
        "parent_outcome": None,
        "isolated_at_parent": None,
        "fixed_outcome": None,
        "signature": None,
        "accepted": False,
        "reason": "",
    }
    parent = git(repo, "rev-parse", f"{cand['sha']}^").stdout.strip()
    if not parent:
        rec["reason"] = "no parent commit (root or unreachable)"
        return rec
    rec["parent"] = parent

    tests = [t for t in cand.get("tests", []) if t.endswith(".py")]
    rec["target_origin"] = "test file in the fix commit"
    if not tests:
        return confirm_source_only(repo, rec, cand, src)

    # At the fix commit: which of its tests pass?
    if git(repo, "checkout", "-q", "--force", cand["sha"]).returncode != 0:
        rec["reason"] = "could not check out the fix commit"
        return rec
    git(repo, "clean", "-qfd")
    rec["commands"].append(f"git checkout {cand['sha'][:12]}")
    nodes = node_ids(repo, tests, src)
    if not nodes:
        rec["commands"].append("no collectable node ids in the commit's test files")
        return confirm_source_only(repo, rec, cand, src)

    chosen = None
    for node in nodes:
        fixed_verdict, _ = outcome(repo, node, src)
        if fixed_verdict != "PASSED":
            continue
        # At the parent: the same node, with the commit's test half applied so
        # the target exists there at all.
        if git(repo, "checkout", "-q", "--force", parent).returncode != 0:
            continue
        git(repo, "clean", "-qfd")
        patch = git(repo, "diff", f"{parent}..{cand['sha']}", "--", *tests).stdout
        if patch.strip():
            proc = subprocess.run(
                ["git", "-C", str(repo), "apply", "--3way"],
                input=patch,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode != 0:
                git(repo, "checkout", "-q", "--force", cand["sha"])
                continue
        parent_verdict, sig = outcome(repo, node, src)
        rec["commands"].append(f"git checkout {parent[:12]} + apply test half")
        if parent_verdict in ("FAILED", "ERROR"):
            chosen = (node, parent_verdict, sig)
            rec["fixed_outcome"] = "PASSED"
            break
        git(repo, "checkout", "-q", "--force", cand["sha"])
        git(repo, "clean", "-qfd")

    git(repo, "checkout", "-q", "--force", cand["sha"])
    git(repo, "clean", "-qfd")

    if chosen is None:
        # The commit's own tests did not separate the two commits. The criterion
        # is the same either way, so try reaching it from the suite instead of
        # discarding a candidate the repository may still reproduce.
        rec["commands"].append("no node from the commit's tests failed at the parent and passed at the fix")
        return confirm_source_only(repo, rec, cand, src)
    node, parent_verdict, sig = chosen
    rec.update(
        {
            "target": node,
            "parent_outcome": parent_verdict,
            "signature": sig,
            "accepted": True,
            "reason": "parent-fail to fixed-pass confirmed",
        }
    )
    return rec


def main() -> int:
    parser = argparse.ArgumentParser(prog="confirm_cases")
    sub = parser.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--work", default="/tmp/bm06-repos")
    c = sub.add_parser("confirm")
    c.add_argument("--work", default="/tmp/bm06-repos")
    c.add_argument("--candidates", default="benchmark/bm06/candidates.json")
    c.add_argument("--out", default="benchmark/bm06/stage2-records.json")
    c.add_argument("--per-class", type=int, default=8, help="candidates to attempt per class per repo")
    c.add_argument("--layouts", default="benchmark/bm06/src-layouts.json")
    c.add_argument("--allocation", default="benchmark/bm06/allocation.json")
    c.add_argument(
        "--target-multiple",
        type=int,
        default=2,
        help="confirmations per class, as a multiple of the allocation, so label review has replacements",
    )
    c.add_argument(
        "--retry-from",
        default="",
        help="records from an earlier pass; rejected candidates are re-attempted at the merge unit only",
    )
    args = parser.parse_args()

    work = Path(args.work)
    if args.cmd == "install":
        cands = json.loads(Path("benchmark/bm06/candidates.json").read_text(encoding="utf-8"))
        report = install(work, [n for n, _ in discover_cases.REPOS if n in cands["repos"]])
        Path("benchmark/bm06/install-report.json").write_text(
            json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0

    cands = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    layouts = json.loads(Path(args.layouts).read_text(encoding="utf-8")) if Path(args.layouts).is_file() else {}
    # A recovery pass carries the earlier pass's rejections forward rather than
    # starting a fresh record set, so the reason a candidate first failed stays
    # attached to it and the two passes cannot be reported as independent.
    prior: dict[tuple[str, str], dict] = {}
    if args.retry_from:
        for rec in json.loads(Path(args.retry_from).read_text(encoding="utf-8")):
            prior[(rec["repo"], rec["fix_commit"])] = rec
    # Class-major, not repository-major. Repository order still fixes which
    # candidate is reached first *within* a class, but a class with 623
    # candidates must not consume the run before a class with 26 has been
    # attempted at all. Both orders are deterministic; only this one reaches
    # every class.
    allocation = json.loads(Path(args.allocation).read_text(encoding="utf-8"))["allocation"]
    repo_order = [name for name, _ in discover_cases.REPOS]
    records: list[dict] = []
    accepted_by_class: dict[str, int] = dict.fromkeys(allocation, 0)

    for cls in allocation:
        # Surplus beyond the allocation is deliberate: label review withdraws
        # cases, and a case withdrawn there may only be replaced from candidates
        # confirmed *before* any result was known. Stopping at the allocation
        # exactly would leave nothing to replace it with except a post-result
        # choice, which the ruling forbids.
        quota = allocation[cls] * args.target_multiple
        for repo_name in repo_order:
            if accepted_by_class[cls] >= quota:
                break
            repo = work / repo_name
            hits = cands["repos"].get(repo_name, {}).get(cls, [])
            if not repo.exists() or not hits:
                continue
            src = layouts.get(repo_name, "")
            for cand in hits[: args.per_class]:
                if accepted_by_class[cls] >= quota:
                    break
                seen = prior.get((repo_name, cand["sha"]))
                if seen is not None and seen.get("accepted"):
                    records.append(seen)  # already confirmed; do not re-run it
                    accepted_by_class[cls] += 1
                    continue
                try:
                    rec = confirm_one(repo, cand, cls, src, CONTAINER_IMAGE, prior=seen)
                except Exception as exc:  # noqa: BLE001 - a crash is a rejection with a reason
                    rec = {
                        "repo": repo_name,
                        "cause_class": cls,
                        "fix_commit": cand["sha"],
                        "accepted": False,
                        "reason": f"harness error: {type(exc).__name__}: {exc}"[:200],
                    }
                records.append(rec)
                if rec.get("accepted"):
                    accepted_by_class[cls] += 1
                flag = "ACCEPT" if rec.get("accepted") else "reject"
                print(
                    f"[{repo_name}/{cls}] {cand['sha'][:10]} {flag}: {rec.get('reason', '')[:80]}"
                    f"  ({accepted_by_class[cls]}/{quota})",
                    flush=True,
                )
        if accepted_by_class[cls] < quota:
            # Say what was actually exhausted. "Every candidate" would be false:
            # --per-class caps attempts per repository, so a class can end short
            # with candidates still unattempted, and a reader who took the
            # stronger wording would conclude the corpus is out of cases when it
            # is only out of *reached* cases.
            print(
                f"  !! {cls}: {accepted_by_class[cls]}/{quota} after attempting up to "
                f"{args.per_class} candidates per repository",
                flush=True,
            )

    Path(args.out).write_text(json.dumps(records, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    accepted = [r for r in records if r.get("accepted")]
    print(f"\nattempted {len(records)}, accepted {len(accepted)}")
    by_class: dict[str, set] = {}
    for r in accepted:
        by_class.setdefault(r["cause_class"], set()).add(r["repo"])
    for cls, repos in sorted(by_class.items()):
        n = sum(1 for r in accepted if r["cause_class"] == cls)
        print(f"  {cls:20} accepted={n:<3} repos={len(repos)}")
    print("\nEvery attempted candidate has a record, accepted or rejected. No class was")
    print("substituted and no fixture was manufactured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
