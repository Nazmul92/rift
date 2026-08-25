"""BM-08-v3 stage 0: freeze the repository population before any outcome exists.

The corpus needs four more cases and six more repositories. Adding exactly six
repositories would optimise directly to the threshold and the resulting
denominator would be worth nothing. So this declares a substantially broader
population than the shortfall requires, on project-level criteria alone, and
accepts whatever the frozen pipeline returns.

The list is written and hashed **before** a single candidate from these
repositories is mined. A repository that contributes nothing stays in the record;
swapping it for a more productive one after seeing its yield is exactly the
manoeuvre this file exists to make impossible.

Selection criteria, all objective and all independent of model performance:

* a real public Python project with available Git history;
* a pytest-compatible executable suite the existing miner can analyse;
* ordinary source-plus-test change commits;
* meaningful history after 2018-01-01.

Repositories that were major contributors to BM-06/BM-07/BM-08-v1/v2 corpora are
avoided where practical. That is a diversity preference, not a contamination
boundary — commit-level prior exposure remains the actual boundary and is
unchanged.

No model is called. Nothing here reads a validation result.
"""

import hashlib
import json
import pathlib
import subprocess
import sys

OUT = pathlib.Path("/s/bm08_repository_population.json")
EXISTING_ROOT = pathlib.Path("/repos")
NEW_ROOT = pathlib.Path("/repos-v3")

# Declared before mining. Ordered alphabetically so the list carries no ranking.
NEW_REPOSITORIES = [
    ("asttokens", "https://github.com/gristlabs/asttokens.git"),
    ("cattrs", "https://github.com/python-attrs/cattrs.git"),
    ("deepdiff", "https://github.com/seperman/deepdiff.git"),
    ("flask", "https://github.com/pallets/flask.git"),
    ("httpx", "https://github.com/encode/httpx.git"),
    ("inflect", "https://github.com/jaraco/inflect.git"),
    ("iniconfig", "https://github.com/pytest-dev/iniconfig.git"),
    ("isodate", "https://github.com/gweis/isodate.git"),
    ("isort", "https://github.com/PyCQA/isort.git"),
    ("jmespath", "https://github.com/jmespath/jmespath.py.git"),
    ("jsonpatch", "https://github.com/stefankoegl/python-json-patch.git"),
    ("natsort", "https://github.com/SethMMorton/natsort.git"),
    ("pathspec", "https://github.com/cpburnz/python-pathspec.git"),
    ("platformdirs", "https://github.com/tox-dev/platformdirs.git"),
    ("prompt-toolkit", "https://github.com/prompt-toolkit/python-prompt-toolkit.git"),
    ("pycodestyle", "https://github.com/PyCQA/pycodestyle.git"),
    ("python-slugify", "https://github.com/un33k/python-slugify.git"),
    ("requests", "https://github.com/psf/requests.git"),
    ("rich", "https://github.com/Textualize/rich.git"),
    ("sortedcontainers", "https://github.com/grantjenks/python-sortedcontainers.git"),
    ("tabulate", "https://github.com/astanin/python-tabulate.git"),
    ("tomlkit", "https://github.com/sdispater/tomlkit.git"),
    ("tqdm", "https://github.com/tqdm/tqdm.git"),
    ("typer", "https://github.com/fastapi/typer.git"),
    ("urllib3", "https://github.com/urllib3/urllib3.git"),
    ("voluptuous", "https://github.com/alecthomas/voluptuous.git"),
    ("wcwidth", "https://github.com/jquast/wcwidth.git"),
]

CRITERIA = [
    "real public Python project with available Git history",
    "pytest-compatible executable test suite analysable by the existing miner",
    "ordinary source-plus-test change commits",
    "meaningful commit history after 2018-01-01",
    "not a major contributor to BM-06/BM-07/BM-08-v1/v2 corpora, where practical",
]


def git(repo: pathlib.Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace", timeout=120
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def describe(name: str, root: pathlib.Path, origin: str, provenance: str, added: str) -> dict:
    repo = root / name
    return {
        "repository": name,
        "canonical_origin": origin,
        "resolved_source": git(repo, "config", "--get", "remote.origin.url") or origin,
        "reference_branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "head_commit": git(repo, "rev-parse", "HEAD"),
        "mining_root": str(root),
        "selection_provenance": provenance,
        "date_added": added,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: build_repository_population.py <YYYY-MM-DD>")
        return 2
    added = sys.argv[1]

    existing = sorted(p.name for p in EXISTING_ROOT.iterdir() if (p / ".git").exists())
    rows = [
        describe(name, EXISTING_ROOT, "", "BM-06/BM-07/BM-08-v1 population, carried forward", "pre-v3")
        for name in existing
    ]
    rows += [
        describe(name, NEW_ROOT, origin, "BM-08-v3 repository expansion, declared before mining", added)
        for name, origin in NEW_REPOSITORIES
    ]

    payload = {
        "benchmark": "BM-08-v3",
        "selection_criteria": CRITERIA,
        "previous_repository_count": len(existing),
        "new_repository_count": len(NEW_REPOSITORIES),
        "total_repository_count": len(rows),
        "repositories": rows,
    }
    body = json.dumps({k: v for k, v in payload.items()}, indent=1, sort_keys=True) + "\n"
    payload["repository_population_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"previous repositories : {payload['previous_repository_count']}")
    print(f"new repositories      : {payload['new_repository_count']}")
    print(f"total population      : {payload['total_repository_count']}")
    missing = [r["repository"] for r in rows if not r["head_commit"]]
    print(f"unresolved heads      : {missing or 'none'}")
    print(f"\nrepository_population_hash : {payload['repository_population_hash']}")
    print(f"\nwritten: {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
