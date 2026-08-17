"""The frozen-tree gate: three consecutive green runs on an untouched tree.

The digest is taken before run 1 and after run 3 and must be equal. Any failing
command, or any mutation of the tree, restarts the sequence from run 1 — the
script stops and says so rather than continuing on a broken premise.

Excluded from the digest, and stated rather than assumed: `.git`, `.rift`,
`build`, the byte-compiled and tool caches, pytest's stray `pytest-cache-files-*`
directories, and `.codex-test-tmp` (this harness itself). Everything shipped is
included, including the markdown records.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/w")
SKIP_DIRS = {".git", ".rift", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", "build", ".codex-test-tmp"}

COMMANDS = [
    ("pytest", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header"]),
    ("ruff check", [sys.executable, "-m", "ruff", "check", "src", "tests", "benchmark"]),
    ("ruff format --check", [sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "benchmark"]),
    ("mypy", [sys.executable, "-m", "mypy", "src"]),
]


def files() -> list[Path]:
    out = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if any(part.startswith("pytest-cache-files-") for part in rel.parts):
            continue
        out.append(path)
    return out


def digest() -> tuple[str, int]:
    h = hashlib.sha256()
    paths = files()
    for path in paths:
        h.update(path.relative_to(ROOT).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest(), len(paths)


def main() -> int:
    start, n_start = digest()
    print(f"START digest {start} ({n_start} files)")
    rows = []
    for run in (1, 2, 3):
        for label, argv in COMMANDS:
            began = time.monotonic()
            proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
            took = time.monotonic() - began
            tail = (proc.stdout.strip().splitlines() or [""])[-1][:80]
            rows.append((run, label, proc.returncode, took, tail))
            print(f"run {run}  {label:<20} rc={proc.returncode}  {took:7.3f}s  {tail}")
            if proc.returncode != 0:
                print(proc.stdout[-3000:])
                print(proc.stderr[-1500:])
                print(f"\nGATE FAILED at run {run} on {label}. The sequence restarts from run 1.")
                return 1
        mid, n_mid = digest()
        if mid != start:
            print(f"\nTREE MUTATED after run {run}: {mid} ({n_mid} files). The sequence restarts from run 1.")
            return 1

    end, n_end = digest()
    print(f"\nEND digest   {end} ({n_end} files)")
    print(f"equal: {start == end}")
    return 0 if start == end else 1


if __name__ == "__main__":
    raise SystemExit(main())
