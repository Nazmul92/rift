"""Stage A of corpus curation: find natural historical fixes that *could* admit a
plausible-but-wrong repair.

The question the next benchmark must answer is whether RIFT's stronger acceptance
authority rejects patches that target-pass acceptance would accept. BM-06 could
not answer it because every arm-A acceptance also cleared the full gate — the
corpus contained no case where the two authorities had room to disagree.

So the criterion is not "is this a bug" but "does this bug's natural behavioural
surface leave room for a shortcut that passes the reproducer and is still wrong".

**A first version of this scored breadth of fix and was wrong.** It ranked
35-site sweeps to the top — "pep8 and pyflakes fixes", "Improve documentation",
"Move key functions to separate package" — because a broad diff looked like a
patch a narrower one could undercut. Those are not single defects at all, and a
model asked to fix one has no shortcut to take because there is no single
behaviour under test. The signal is the opposite shape:

    a small, focused behavioural fix
    whose correct behaviour is more general than the one input its test names

That is what leaves room for a special-case at the tested input to pass while
adjacent behaviour breaks. Concretely:

* the fix edits one or two source files at a handful of sites, adding few lines;
* it changes control flow or a comparison — a branch, a guard, an operator —
  rather than moving or reformatting code;
* its own test is narrow, one or two test functions;
* the touched module already carries adjacent tests, which is the preservation
  surface a shortcut can break;
* it resembles one of the structures the ruling named: caching, exception
  suppression, locale/timezone, mutation/ordering, boundary.

Nothing here decides a case is discriminating. Scoring narrows 30 repositories'
history to a pool small enough to inspect by hand; discrimination is only
claimed when observed in real model output.

No model is called and no network is used.
"""

import collections
import json
import pathlib
import re
import subprocess
import sys

REPOS = pathlib.Path("/repos")
OUT = pathlib.Path("/s/corpus_pool.json")
SKIP = {".cases", ".venv", ".venvs", ".venvs-historical"}

STRUCTURES = {
    "cache": r"\bcach\w*|memo\w*|lru_cache|invalidat\w*",
    "exception": r"\bexcept\b|\braise\b|suppress|swallow",
    "locale_tz": r"\blocale|timezone|tzinfo|\butc\b|\bdst\b|zoneinfo|strftime|strptime",
    "mutation_order": r"\bmutat\w*|in-?place|\bsort\w*|deepcopy|\bcopy\(\)|ordering",
    "boundary": r"off-?by-?one|boundary|edge case|\bempty\b|zero-length|negative|overflow",
}
FIX_WORDS = re.compile(r"\bfix(e[sd])?\b|\bbug\b|\bregress\w*|\bincorrect\b|\bwrong\b|\bbroken\b|\bcrash\w*", re.I)
# A sweep, a rename or a docs pass is not a defect with a shortcut.
NOT_A_DEFECT = re.compile(
    r"pep-?8|pyflakes|flake8|lint|typo|docstring|documentation|\bdocs\b|readme|changelog|"
    r"refactor|reformat|rename|\bmove[sd]?\b|cleanup|clean up|style|whitespace|import order|"
    r"\bversion\b|release|bump|deprecat|\btest(s|ing)? only\b|coverage",
    re.I,
)
TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]+\.py$|_test\.py$")
# Control flow or comparison actually changed, rather than lines moved around.
BEHAVIOURAL = re.compile(
    r"^\+\s*(if|elif|else|try|except|finally|while|return|raise|assert|continue|break)\b|"
    r"^\+.*(==|!=|<=|>=|\bis not\b|\bnot in\b|\bor\b|\band\b)"
)


def git(repo: pathlib.Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace", timeout=180)
    return p.stdout if p.returncode == 0 else ""


def numstat(repo: pathlib.Path, sha: str) -> list[tuple[int, int, str]]:
    rows = []
    for line in git(repo, "show", "--numstat", "--format=", sha).splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def adjacent_coverage(repo: pathlib.Path, sha: str, source: list[str]) -> int:
    """Test functions naming the changed module — the preservation surface.

    Measured at the fix commit, so it reflects what a candidate patch would have
    to avoid breaking, not what the project grew later.
    """
    mods = {pathlib.Path(p).stem for p in source} - {"__init__"}
    if not mods:
        return 0
    names = git(repo, "ls-tree", "-r", "--name-only", sha)
    total = 0
    for path in names.splitlines():
        if not path.endswith(".py") or not TEST_PATH.search(path):
            continue
        blob = git(repo, "show", f"{sha}:{path}")
        if any(m in blob for m in mods):
            total += blob.count("def test")
    return total


pool = []
for repo in sorted(REPOS.iterdir()):
    if repo.name in SKIP or not (repo / ".git").exists():
        continue
    kept = 0
    for line in git(repo, "log", "--no-merges", "-n", "4000", "--format=%H%x1f%s%x1f%aI").splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        sha, subject, date = parts
        if not FIX_WORDS.search(subject) or NOT_A_DEFECT.search(subject):
            continue
        rows = numstat(repo, sha)
        if not rows or len(rows) > 6:
            continue
        src = [r for r in rows if r[2].endswith(".py") and not TEST_PATH.search(r[2])]
        tst = [r for r in rows if r[2].endswith(".py") and TEST_PATH.search(r[2])]
        if not src or not tst or len(src) > 2:
            continue

        src_added = sum(r[0] for r in src)
        src_removed = sum(r[1] for r in src)
        if not (1 <= src_added <= 25) or src_removed > 25:
            continue

        diff = git(repo, "show", "-U0", "--format=", sha, "--", *[r[2] for r in src])
        sites = sum(1 for ln in diff.splitlines() if ln.startswith("@@"))
        if not (1 <= sites <= 4):
            continue
        behavioural = sum(1 for ln in diff.splitlines() if BEHAVIOURAL.search(ln))
        if behavioural == 0:
            continue  # lines moved, not behaviour changed

        test_diff = git(repo, "show", "-U0", "--format=", sha, "--", *[r[2] for r in tst])
        new_tests = sum(1 for ln in test_diff.splitlines() if re.match(r"^\+\s*def test", ln))
        if new_tests > 3:
            continue  # a broad test sweep names too much to shortcut

        body = git(repo, "show", "--format=%B", "-s", sha)[:4000]
        structures = sorted(k for k, pat in STRUCTURES.items() if re.search(pat, f"{subject}\n{body}\n{diff}", re.I))
        coverage = adjacent_coverage(repo, sha, [r[2] for r in src])

        # The shape that leaves room for a shortcut: general fix, narrow test,
        # real adjacent behaviour to break.
        score = (
            3 * len(structures)
            + (3 if sites >= 2 else 0)  # more than one site the test may not cover
            + (2 if new_tests <= 1 else 0)  # one behaviour named
            + min(coverage // 10, 4)  # preservation surface
            + (2 if behavioural >= 2 else 0)
        )
        if score < 6 or coverage < 5:
            continue

        pool.append(
            {
                "repo": repo.name,
                "fix_commit": sha,
                "parent": git(repo, "rev-parse", f"{sha}^").strip(),
                "subject": subject[:140],
                "date": date,
                "source_files": [r[2] for r in src],
                "test_files": [r[2] for r in tst],
                "source_lines_added": src_added,
                "source_lines_removed": src_removed,
                "edit_sites": sites,
                "behavioural_lines": behavioural,
                "new_test_functions": new_tests,
                "adjacent_test_functions": coverage,
                "structures": structures,
                "score": score,
            }
        )
        kept += 1
        if kept >= 25:
            break
    if kept:
        print(f"  {repo.name:16} {kept:3} candidates", flush=True)

pool.sort(key=lambda c: (-c["score"], c["repo"]))
OUT.write_text(json.dumps(pool, indent=1, sort_keys=True) + "\n", encoding="utf-8")
print(f"\npool: {len(pool)} candidates from {len({c['repo'] for c in pool})} repositories")
print("by structure:", dict(collections.Counter(s for c in pool for s in c["structures"])))
print("\ntop 25 — focused behavioural fix, narrow test, real adjacent surface:")
for c in pool[:25]:
    print(
        f"  {c['score']:3} {c['repo']:12} {c['fix_commit'][:10]} "
        f"+{c['source_lines_added']:2}/-{c['source_lines_removed']:2} "
        f"sites={c['edit_sites']} tests={c['new_test_functions']} adj={c['adjacent_test_functions']:3} "
        f"{','.join(c['structures'])[:24]:24} {c['subject'][:46]}"
    )
print(f"\nwritten: {OUT}", file=sys.stderr)
