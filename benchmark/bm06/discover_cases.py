"""BM-06 case discovery — model-free, no provider request, no spending.

Stage 1 (`survey`) searches commit history for candidate fixes per cause class
and reports counts. It runs no repository code and installs nothing, so it is
cheap enough to answer the question that actually gates the manifest: *is a
30-case set across all eight classes reachable from this repository set at all?*

Stage 2 (`confirm`, not yet run) would check out each candidate's parent, run the
tests its fix touched, and keep only those that genuinely fail before and pass
after. That is the expensive half and it is deliberately separate: there is no
point installing eight projects to confirm candidates in a class that has none.

A candidate is not a case. Nothing here freezes anything.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

# Admitted by the rule in `repo-selection.md`, which was written before this list
# was searched. The first ten are the original round, retained with their
# rejection records; the rest widen the corpus into domains where the thinner
# classes can occur at all.
REPOS = [
    ("click", "https://github.com/pallets/click"),
    ("pyparsing", "https://github.com/pyparsing/pyparsing"),
    ("sqlparse", "https://github.com/andialbrecht/sqlparse"),
    ("pluggy", "https://github.com/pytest-dev/pluggy"),
    ("boltons", "https://github.com/mahmoud/boltons"),
    ("markdown", "https://github.com/Python-Markdown/markdown"),
    ("chardet", "https://github.com/chardet/chardet"),
    ("attrs", "https://github.com/python-attrs/attrs"),
    ("jinja", "https://github.com/pallets/jinja"),
    ("werkzeug", "https://github.com/pallets/werkzeug"),
    ("arrow", "https://github.com/arrow-py/arrow"),
    ("dateutil", "https://github.com/dateutil/dateutil"),
    ("croniter", "https://github.com/kiorky/croniter"),
    ("humanize", "https://github.com/python-humanize/humanize"),
    ("freezegun", "https://github.com/spulec/freezegun"),
    ("icalendar", "https://github.com/collective/icalendar"),
    ("babel", "https://github.com/python-babel/babel"),
    ("faker", "https://github.com/joke2k/faker"),
    ("cachetools", "https://github.com/tkem/cachetools"),
    ("tenacity", "https://github.com/jd/tenacity"),
    ("filelock", "https://github.com/tox-dev/filelock"),
    ("structlog", "https://github.com/hynek/structlog"),
    ("jsonschema", "https://github.com/python-jsonschema/jsonschema"),
    ("marshmallow", "https://github.com/marshmallow-code/marshmallow"),
    ("cerberus", "https://github.com/pyeve/cerberus"),
    ("packaging", "https://github.com/pypa/packaging"),
    ("pygments", "https://github.com/pygments/pygments"),
    ("bleach", "https://github.com/mozilla/bleach"),
    ("soupsieve", "https://github.com/facelessuser/soupsieve"),
    ("more-itertools", "https://github.com/more-itertools/more-itertools"),
]

# Commit-message markers per governed cause class. Deliberately generous at this
# stage: a false positive costs one confirmation run, a false negative costs a
# case that never enters the candidate pool at all.
MARKERS: dict[str, list[str]] = {
    "order_dependence": [
        "test isolation",
        "isolate the test",
        "test order",
        "order dependen",
        "depends on order",
        "test pollution",
        "pollutes",
        "leaks between tests",
        "when run alone",
        "in isolation",
        "random order",
        "randomly ordered",
        "test independence",
        "shared between tests",
        "isolation",
        "isolated",
        "interdependen",
        "side effect",
        "side-effect",
        "import order",
        "module level",
        "module-level",
        "registry",
        "reset()",
        "only when run",
        "passes alone",
        "fails alone",
        "fails when run",
        "run separately",
        "-p no:randomly",
        "pytest-randomly",
        "xdist",
        "forked",
        "reuse",
        "monkeypatch",
    ],
    "state_leakage": [
        "global state",
        "module state",
        "reset the cache",
        "clear the cache",
        "stale cache",
        "singleton",
        "leaks state",
        "teardown",
        "cleanup fixture",
        "not reset",
        "reset between",
    ],
    "missing_dependency": [
        "optional dependency",
        "importerror",
        "modulenotfounderror",
        "missing module",
        "not installed",
        "guard the import",
        "soft dependency",
        "extras_require",
    ],
    "version_mismatch": [
        "python 3.1",
        "deprecat",
        "compatibility with",
        "compat with",
        "breaking change in",
        "upstream change",
        "new version of",
        "pin the version",
        "version bump broke",
    ],
    "locale_timezone": [
        "locale",
        "timezone",
        "tzdata",
        "utcnow",
        "strftime",
        "strptime",
        "daylight",
        "dst ",
        "utc offset",
        "localtime",
    ],
    "nondeterminism": [
        "flaky",
        "flakiness",
        "race condition",
        "nondeterministic",
        "non-deterministic",
        "hash order",
        "set ordering",
        "dict ordering",
        "unordered",
        "sort order",
        "intermittent",
    ],
    "two_cause": [
        "two separate",
        "both issues",
        "also fixes",
        "second bug",
        "compound",
    ],
    # Carried in the stage-2 input explicitly rather than referenced as "from the
    # M1a scan", so every case in the manifest is confirmed by one procedure.
    # Narrower than "any commit saying fix": these markers name an observed wrong
    # behaviour, which is what a reproducer needs.
    "genuine_source_bug": [
        "off-by-one",
        "off by one",
        "returns the wrong",
        "returned the wrong",
        "incorrect result",
        "wrong result",
        "raises attributeerror",
        "raises typeerror",
        "raises keyerror",
        "raises indexerror",
        "raises valueerror",
        "unhandled exception",
        "crashes when",
        "crash when",
        "infinite loop",
        "silently ignore",
        "should not raise",
        "regression in",
    ],
}

# A commit that only touches tests fixes nothing; a commit that only touches
# source has no test to gate against. A case needs both.
_TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]+\.py$|_test\.py$")


def git(repo: Path, *args: str, timeout: float = 300.0) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout, errors="replace"
    )
    return proc.stdout


def touched(repo: Path, sha: str) -> list[str]:
    return [p for p in git(repo, "show", "--name-only", "--pretty=format:", sha).splitlines() if p.strip()]


def survey(work: Path, scan: int) -> dict:
    out: dict = {"repos": {}, "totals": {k: 0 for k in MARKERS}}
    for name, url in REPOS:
        repo = work / name
        if not repo.exists():
            print(f"[{name}] cloning", flush=True)
            subprocess.run(["git", "clone", "-q", "--filter=blob:none", url, str(repo)], check=True, timeout=1800)
        per_repo: dict[str, list[dict]] = {k: [] for k in MARKERS}
        log = git(repo, "log", f"-{scan}", "--pretty=format:%H%x1f%s%x1f%b%x1e")
        for entry in log.split("\x1e"):
            if not entry.strip():
                continue
            parts = entry.strip().split("\x1f")
            if len(parts) < 2:
                continue
            sha, subject = parts[0].strip(), parts[1]
            body = parts[2] if len(parts) > 2 else ""
            text = f"{subject}\n{body}".lower()
            for cls, markers in MARKERS.items():
                if not any(m in text for m in markers):
                    continue
                files = touched(repo, sha)
                tests = [f for f in files if _TEST_PATH.search(f)]
                source = [f for f in files if f.endswith(".py") and not _TEST_PATH.search(f)]
                if not source:
                    continue  # nothing for a source repair to change
                per_repo[cls].append(
                    {
                        "sha": sha,
                        "subject": subject[:110],
                        "tests": tests[:4],
                        "source": source[:4],
                        # Which shape it is decides whether the case is usable and how:
                        # "both"  -> the commit's own test can be applied at the parent
                        # "source_only" -> an existing test must already fail at the parent
                        "shape": "both" if tests else "source_only",
                    }
                )
        # Deterministic candidate ordering, fixed by `repo-selection.md` before
        # any outcome was known: commits carrying both halves first, history
        # order preserved within each group. Source-only commits are still
        # attempted — this decides what is reached first, not what is eligible.
        for hits in per_repo.values():
            hits.sort(key=lambda h: 0 if h["shape"] == "both" else 1)
        out["repos"][name] = {k: v for k, v in per_repo.items() if v}
        for cls, hits in per_repo.items():
            out["totals"][cls] += len(hits)
        counts = ", ".join(f"{k}={len(v)}" for k, v in per_repo.items() if v) or "none"
        print(f"[{name}] {counts}", flush=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(prog="discover_cases")
    parser.add_argument("--work", default="/tmp/bm06-repos")
    parser.add_argument("--scan", type=int, default=3000, help="commits of history to search per repo")
    parser.add_argument("--out", default="benchmark/bm06/candidates.json")
    args = parser.parse_args()

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    result = survey(work, args.scan)

    print("\n=== candidates per cause class, across all repositories ===")
    need = json.loads(Path("benchmark/bm06/allocation.json").read_text(encoding="utf-8"))["allocation"]
    for cls, total in sorted(result["totals"].items(), key=lambda kv: -kv[1]):
        repos_with = sum(1 for r in result["repos"].values() if r.get(cls))
        floor = need.get(cls, 0)
        mark = "ok" if total >= floor and repos_with >= (2 if cls == "order_dependence" else 1) else "SHORT"
        print(f"  {cls:20} candidates={total:<4} repos={repos_with:<3} floor={floor:<3} {mark}")
    print("\n  genuine_source_bug now has its own marker set and enters stage 2 by the same route")
    print("\nCandidates are not cases. Stage 2 must confirm each one reproduces at the parent commit.")

    Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
