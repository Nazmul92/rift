"""BM-08 stage A: find ordinary historical Python bugs. No RIFT-shaped selection.

BM-07 mined *for* the verification mechanism: it scored cases by whether a
plausible-but-wrong shortcut could pass the reproducer, because the question was
whether a stronger acceptance authority ever disagrees with a weaker one. That
made a fine mechanism study and a terrible efficiency benchmark, because the
corpus was chosen by the very property under test.

BM-08 asks a different question — across ordinary real bugs, does RIFT produce
more correct fixes per dollar than the same model alone — so the corpus must not
be chosen for RIFT at all. Nothing here scores preservation surface, adjacent
coverage, shortcut room, or "discrimination potential". The filters are only:

* it is a real historical bug fix, by its own commit message;
* it changes Python source, not docs, style, imports or version numbers;
* it ships its own test change, so a natural reproducer exists;
* the fix is small enough to be a defect rather than a rewrite.

Everything else is measured and reported, never selected on. Categories are
assigned so the *mix* can be described honestly after the fact — small,
multi-file, state/order, API misuse, edge case, parsing, config interaction,
inheritance, data transformation. A category is a label, not a filter.

The scan walks each repository's **complete** history with no per-repository
cap. An earlier version stopped after 40 eligible commits per repo, which sounds
harmless and is not: the walk is newest-first, so five large projects hit the cap
and contributed only recent history while small ones contributed all of theirs.
Deterministic ordering applied afterwards would then have been shuffling an
already-biased pool. The cap is gone; the pool is every eligible commit.

Ordering is deterministic: candidates are sorted by SHA-256 of the commit id,
never by `hash()` or `random`, so the same pool yields the same corpus in any
interpreter. Arm B's seed defect in BM-07 was exactly this mistake, and it is not
worth making twice.

Exclusion of previously-seen commits is **not** done here, and neither is the
BM-08-v2 author-date floor. The pool is reported raw so overlap with prior
benchmarks and era attrition can each be counted before they are applied; a
miner that silently dropped either would hide how much of this volume the
project has already looked at, and how much of it the frozen environment cannot
run.

Both `%aI` (author date) and `%cI` (committer date) are recorded. Eligibility
uses the **author** date; carrying both means that choice is visible in the
artifact rather than asserted in prose, and a rebase or cherry-pick — which
moves the committer date but not the author date — cannot silently change a
candidate's eligibility.

No model is called and no network is used.
"""

import collections
import hashlib
import json
import pathlib
import re
import subprocess
import sys

# BM-08-v3 mines two roots: the population carried forward from earlier
# benchmarks, and the repositories added by the v3 expansion. Only the
# *population* is wider — every mining rule below is unchanged.
REPO_ROOTS = [pathlib.Path("/repos"), pathlib.Path("/repos-v3"), pathlib.Path("/repos-v5")]
OUT = pathlib.Path("/s/bm08_pool.json")
SKIP = {".cases", ".venv", ".venvs", ".venvs-historical"}

FIX_WORDS = re.compile(
    r"\bfix(e[sd])?\b|\bbug\b|\bregress\w*|\bincorrect\w*|\bwrong\b|\bbroken\b|\bcrash\w*|"
    r"\bfail(s|ed|ure)?\b|\berror\b|\braise[sd]?\b|\bhandle\b",
    re.I,
)
# Not defects: sweeps, renames, docs, packaging.
NOT_A_DEFECT = re.compile(
    r"pep-?8|pyflakes|flake8|\blint\w*|typo|docstring|documentation|\bdocs\b|readme|changelog|"
    r"refactor|reformat|rename|cleanup|clean up|\bstyle\b|whitespace|import order|"
    r"\bversion\b|release|bump|deprecat|coverage|\bci\b|travis|appveyor|tox\.ini|"
    r"\bmerge\b|revert|\btypo\b|add test|adding test",
    re.I,
)
TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]+\.py$|_test\.py$")

# Labels, not filters. A case may carry several.
CATEGORIES = {
    "state_order": r"\bstate\b|\border\w*\b|\bsequenc\w*|\bmutat\w*|in-?place|\bcach\w*|\bresets?\b|"
    r"\bthread\w*|\brace\b|idempot\w*|\bstale\b",
    "api_misuse": r"\bapi\b|signature|\bargument\w*|\bparam\w*|keyword|positional|\bkwargs?\b|"
    r"\bcall(s|ed|ing)?\b.*\bwrong\b|deprecated call",
    "edge_case": r"\bempty\b|\bnone\b|\bnull\b|zero|negative|off-?by-?one|boundary|edge case|"
    r"\bsingle\b|\bmissing\b|\boverflow\b|\bunicode\b",
    "parsing": r"\bpars\w*|\btoken\w*|\blex\w*|\bregex\w*|\bsyntax\b|\bformat\w*|\bescap\w*|"
    r"\bquot\w*|\bdecod\w*|\bencod\w*",
    "config_interaction": r"\bconfig\w*|\boption\w*|\bsetting\w*|\bflag\b|\bdefault\w*|\benv\w*|"
    r"\blocale\b|timezone|tzinfo",
    "inheritance": r"\binherit\w*|\bsubclass\w*|\bsuper\(\)|\boverrid\w*|\bmixin\w*|\bmetaclass\w*|\bmro\b",
    "data_transform": r"\bconvert\w*|\bserial\w*|\bdeserial\w*|\btransform\w*|\bmap(ping|ped)?\b|"
    r"\bnormali[sz]\w*|\bround\w*|\bcast\w*|\bcoerc\w*",
}


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


pool = []
repositories = [
    repo
    for root in REPO_ROOTS
    if root.is_dir()
    for repo in sorted(root.iterdir())
    if repo.name not in SKIP and (repo / ".git").exists()
]
for repo in repositories:
    kept = 0
    for line in git(repo, "log", "--no-merges", "-n", "100000", "--format=%H%x1f%s%x1f%aI%x1f%cI").splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, subject, author_date, committer_date = parts
        if not FIX_WORDS.search(subject) or NOT_A_DEFECT.search(subject):
            continue
        rows = numstat(repo, sha)
        if not rows or len(rows) > 12:
            continue
        src = [r for r in rows if r[2].endswith(".py") and not TEST_PATH.search(r[2])]
        tst = [r for r in rows if r[2].endswith(".py") and TEST_PATH.search(r[2])]
        # A reproducer must exist, and the fix must actually change source.
        if not src or not tst:
            continue
        # Multi-file bugs are wanted; a five-file rewrite is not a defect.
        if len(src) > 5:
            continue

        added = sum(r[0] for r in src)
        removed = sum(r[1] for r in src)
        if not (1 <= added <= 60) or removed > 60:
            continue

        diff = git(repo, "show", "-U0", "--format=", sha, "--", *[r[2] for r in src])
        sites = sum(1 for ln in diff.splitlines() if ln.startswith("@@"))
        if sites < 1:
            continue

        test_diff = git(repo, "show", "-U0", "--format=", sha, "--", *[r[2] for r in tst])
        new_tests = sum(1 for ln in test_diff.splitlines() if re.match(r"^\+\s*(async )?def test", ln))
        if new_tests < 1:
            continue  # no new reproducer to run

        body = git(repo, "show", "--format=%B", "-s", sha)[:4000]
        text = f"{subject}\n{body}\n{diff}"
        cats = sorted(k for k, pat in CATEGORIES.items() if re.search(pat, text, re.I))
        if len(src) > 1:
            cats.append("multi_file")
        if len(src) == 1 and sites == 1 and added <= 5:
            cats.append("small")
        cats = sorted(set(cats)) or ["uncategorised"]

        pool.append(
            {
                "repo": repo.name,
                "fix_commit": sha,
                "parent": git(repo, "rev-parse", f"{sha}^").strip(),
                "subject": subject[:140],
                # `%aI`. Eligibility reads this field and no other.
                "author_date": author_date,
                # `%cI`, recorded for contrast only. Never used for eligibility.
                "committer_date": committer_date,
                "source_files": [r[2] for r in src],
                "test_files": [r[2] for r in tst],
                "source_lines_added": added,
                "source_lines_removed": removed,
                "edit_sites": sites,
                "new_test_functions": new_tests,
                "categories": cats,
                # Deterministic ordering key. Never `hash()`: it is randomised
                # per process and would make the corpus irreproducible.
                "order_key": hashlib.sha256(sha.encode()).hexdigest(),
            }
        )
        kept += 1
    if kept:
        print(f"  {repo.name:16} {kept:3} eligible", flush=True)

pool.sort(key=lambda c: c["order_key"])
OUT.write_text(json.dumps(pool, indent=1, sort_keys=True) + "\n", encoding="utf-8")

print(f"\neligible pool: {len(pool)} bugs from {len({c['repo'] for c in pool})} repositories")
print("by category:", dict(collections.Counter(k for c in pool for k in c["categories"]).most_common()))
rebased = sum(1 for c in pool if c["author_date"][:10] != c["committer_date"][:10])
print(f"author != committer date      : {rebased} (rebased/cherry-picked; eligibility follows the author date)")
print("by repo    :", dict(collections.Counter(c["repo"] for c in pool).most_common()))
print(f"\nwritten: {OUT}", file=sys.stderr)
