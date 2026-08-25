"""Load a benchmark module by path, under a name that cannot collide.

`benchmark/bm07/` and `benchmark/bm08/` both contain `validate_cases.py`,
`curation.py`, `mine_corpus.py` and `build_executable_manifest.py`. A bare
`import validate_cases` therefore resolves to whichever directory sits earlier on
`sys.path` at that moment — and `tests/test_bm07_curation.py` inserts bm07 at
position 0, where it stays for the rest of the session. Whether the BM-08 tests
got the BM-08 module then depended on pytest's collection order, which is not a
property any test should rest on.

This resolves each module from its own file and registers it under a qualified
key (`benchmark.bm08.validate_cases`), so the two never contend. Neither
benchmark's code is modified: the ambiguity was created by how the tests import,
and it is fixed there.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

BENCH = Path(__file__).parents[1] / "benchmark"


def load(benchmark: str, module: str) -> ModuleType:
    """`benchmark/<benchmark>/<module>.py`, imported unambiguously."""
    key = f"benchmark.{benchmark}.{module}"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached

    path = BENCH / benchmark / f"{module}.py"
    if not path.is_file():
        raise ModuleNotFoundError(f"{key}: {path} does not exist")

    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{key}: no loader for {path}")
    loaded = importlib.util.module_from_spec(spec)
    # Registered before execution so a module that imports itself, directly or
    # through a sibling, does not start a second copy.
    sys.modules[key] = loaded
    try:
        spec.loader.exec_module(loaded)
    except BaseException:
        sys.modules.pop(key, None)
        raise
    return loaded


def source_of(module: ModuleType) -> Path:
    """Where a loaded module actually came from — the thing worth asserting."""
    return Path(module.__file__ or "").resolve()
