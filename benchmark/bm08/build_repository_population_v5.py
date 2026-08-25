"""BM-08-v5 stage 0: freeze the expanded repository population before outcomes.

The benchmark is two repositories short of its minimum. Adding two would
optimise directly to the threshold, and a denominator engineered to clear its
own bar measures the engineering rather than the question. So this declares a
substantially broader batch and accepts whatever the frozen pipeline returns.

The list is written and hashed before a single v5 candidate is mined. A
repository that contributes nothing stays in the record; swapping it for a more
productive one after seeing its yield is the manoeuvre this file exists to make
impossible.

Selection is project-level only: a real public Python project, available Git
history, substantial post-2018 development, ordinary source-plus-test commits,
and a suite compatible in principle with the existing pytest pipeline. Not
criteria: easy patches, known model performance, similarity to current
successes, expected yield, or ability to clear the repository threshold.

Repositories already represented in BM-06/BM-07/BM-08 v1-v4 are avoided as a
diversity preference. The contamination boundary remains the frozen
commit-level pre-BM-08 exclusion set, unchanged.

No model is called.
"""

import hashlib
import json
import pathlib
import subprocess
import sys

OUT = pathlib.Path("/s/bm08_repository_population_v5.json")
BM08 = pathlib.Path(__file__).parent
PRIOR = BM08 / "repository-population.json"
ROOT_V5 = pathlib.Path("/repos-v5")

# Declared before mining. Alphabetical so the list carries no ranking.
NEW_REPOSITORIES = [
    ("anyio", "https://github.com/agronholm/anyio.git"),
    ("blinker", "https://github.com/pallets-eco/blinker.git"),
    ("charset-normalizer", "https://github.com/jawah/charset_normalizer.git"),
    ("dateparser", "https://github.com/scrapinghub/dateparser.git"),
    ("dnspython", "https://github.com/rthalley/dnspython.git"),
    ("dpath", "https://github.com/dpath-maintainers/dpath-python.git"),
    ("emoji", "https://github.com/carpedm20/emoji.git"),
    ("fastjsonschema", "https://github.com/horejsek/python-fastjsonschema.git"),
    ("funcy", "https://github.com/Suor/funcy.git"),
    ("glom", "https://github.com/mahmoud/glom.git"),
    ("h11", "https://github.com/python-hyper/h11.git"),
    ("httpcore", "https://github.com/encode/httpcore.git"),
    ("humanfriendly", "https://github.com/xolox/python-humanfriendly.git"),
    ("itsdangerous", "https://github.com/pallets/itsdangerous.git"),
    ("jsonpointer", "https://github.com/stefankoegl/python-json-pointer.git"),
    ("lark", "https://github.com/lark-parser/lark.git"),
    ("markdown-it-py", "https://github.com/executablebooks/markdown-it-py.git"),
    ("markupsafe", "https://github.com/pallets/markupsafe.git"),
    ("mistune", "https://github.com/lepture/mistune.git"),
    ("parse", "https://github.com/r1chardj0n3s/parse.git"),
    ("pathvalidate", "https://github.com/thombashi/pathvalidate.git"),
    ("prettytable", "https://github.com/prettytable/prettytable.git"),
    ("schema", "https://github.com/keleshev/schema.git"),
    ("semver", "https://github.com/python-semver/python-semver.git"),
    ("starlette", "https://github.com/encode/starlette.git"),
    ("toolz", "https://github.com/pytoolz/toolz.git"),
    ("typeguard", "https://github.com/agronholm/typeguard.git"),
    ("xmltodict", "https://github.com/martinblech/xmltodict.git"),
]

CRITERIA = [
    "real public Python project with available Git history",
    "substantial post-2018 development history",
    "ordinary source-plus-test change commits",
    "test suite compatible in principle with the existing pytest pipeline",
    "not already represented in BM-06/BM-07/BM-08 v1-v4, where practical",
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
        print("usage: build_repository_population_v5.py <YYYY-MM-DD>")
        return 2
    added = sys.argv[1]

    prior = json.loads(PRIOR.read_text(encoding="utf-8"))
    rows = list(prior["repositories"])
    prior_names = {r["repository"] for r in rows}

    overlap = sorted(prior_names & {name for name, _ in NEW_REPOSITORIES})
    if overlap:
        print(f"BLOCKED: v5 batch re-declares existing repositories: {overlap}")
        return 2

    rows += [
        describe(name, ROOT_V5, origin, "BM-08-v5 repository expansion, declared before mining", added)
        for name, origin in NEW_REPOSITORIES
    ]

    payload = {
        "benchmark": "BM-08-v5",
        "selection_criteria": CRITERIA,
        "previous_repository_count": prior["total_repository_count"],
        "new_repository_count": len(NEW_REPOSITORIES),
        "total_repository_count": len(rows),
        "prior_population_hash": prior["repository_population_hash"],
        "repositories": rows,
    }
    body = json.dumps(payload, indent=1, sort_keys=True) + "\n"
    payload["repository_population_hash_v5"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    missing = [r["repository"] for r in rows if not r["head_commit"]]
    print(f"previous repositories : {payload['previous_repository_count']}")
    print(f"new repositories      : {payload['new_repository_count']}")
    print(f"total population      : {payload['total_repository_count']}")
    print(f"unresolved heads      : {missing or 'none'}")
    print(f"prior population hash : {payload['prior_population_hash']}")
    print(f"\nrepository_population_hash_v5 : {payload['repository_population_hash_v5']}")
    print(f"\nwritten: {OUT}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
