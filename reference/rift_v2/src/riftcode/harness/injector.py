"""PRIVATE benchmark harness (evaluator-only).

Builds tiny repositories whose target test fails for a hidden reason. The
fault families mirror the gridworld causal families one-for-one, because the
same causal shapes are what make real debugging hard:

    env_gated        <- possession        (a resource must be held)
    cache_stale      <- possession/timed  (state must be removed)
    retry_flake      <- attempt_counter   (the n-th attempt succeeds)
    order_dependent  <- ordered_sequence  (another action must happen first)
    decoy_correlated <- decoy_correlated  (the obvious signal is not the cause)
    code_defect      <- ungated           (control: nothing environmental helps)

The debugger never imports this module; enforced by tests.
"""

from __future__ import annotations

from pathlib import Path

FAULTS = (
    "env_gated",
    "cache_stale",
    "retry_flake",
    "order_dependent",
    "decoy_correlated",
    "code_defect",
)

_CONFTEST = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"

_SEED_TEST = """from gate import mark_seed


def test_seed():
    mark_seed()
"""

_TARGET_TEST = """from gate import check


def test_target():
    check()
"""

_GATE = {
    "env_gated": """import os

def mark_seed():
    pass

def check():
    if "APP_TOKEN" not in os.environ:
        raise AssertionError("startup aborted: missing configuration APP_TOKEN")
""",
    "cache_stale": """import pathlib

ROOT = pathlib.Path(__file__).parent

def mark_seed():
    pass

def check():
    if (ROOT / ".appcache" / "state").exists():
        raise AssertionError("index mismatch: stale entry under .appcache")
""",
    "retry_flake": """import pathlib

ROOT = pathlib.Path(__file__).parent
C = ROOT / ".runcount"

def mark_seed():
    pass

def check():
    n = int(C.read_text()) + 1 if C.exists() else 1
    C.write_text(str(n))
    if n < 3:
        raise AssertionError("connection not ready (attempt %d)" % n)
""",
    "order_dependent": """import pathlib

ROOT = pathlib.Path(__file__).parent
M = ROOT / ".seedmark"

def mark_seed():
    M.write_text("1")

def check():
    if not M.exists():
        raise AssertionError("fixture data absent; registry was never populated")
""",
    "decoy_correlated": """import os
import pathlib

ROOT = pathlib.Path(__file__).parent

def mark_seed():
    pass

def check():
    # the message names a variable that has nothing to do with the cause
    if (ROOT / ".appcache" / "state").exists():
        raise AssertionError("lookup failed for APP_TOKEN in namespace")
""",
    "code_defect": """def mark_seed():
    pass

def check():
    total = sum(range(5))
    if total != 11:
        raise AssertionError("expected 11, computed %d" % total)
""",
}

# Evaluator-only ground truth: the intervention label that actually fixes it,
# or None when the defect is in the code.
ORACLE_CAUSE: dict[str, tuple[str, ...] | None] = {
    "env_gated": ("env:APP_TOKEN",),
    "cache_stale": ("clear:.appcache",),
    "retry_flake": None,  # no intervention; identified by the retry counter
    # any selection that runs the seeding test first is a sufficient cause
    "order_dependent": (
        "first:tests/",
        "first:tests/test_seed.py",
        "first:tests/test_seed.py::test_seed",
    ),
    "decoy_correlated": ("clear:.appcache",),
    "code_defect": None,
}

TARGET = "tests/test_target.py::test_target"


def build_repo(fault: str, dest: Path, seed: int = 0) -> Path:
    if fault not in FAULTS:
        raise ValueError(fault)
    dest = Path(dest)
    (dest / "tests").mkdir(parents=True, exist_ok=True)
    (dest / "conftest.py").write_text(_CONFTEST)
    (dest / "gate.py").write_text(_GATE[fault])
    (dest / "tests" / "test_target.py").write_text(_TARGET_TEST)
    (dest / "tests" / "test_seed.py").write_text(_SEED_TEST)
    if fault in ("cache_stale", "decoy_correlated"):
        (dest / ".appcache").mkdir(exist_ok=True)
        (dest / ".appcache" / "state").write_text(f"stale-{seed}\n")
    return dest
