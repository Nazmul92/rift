"""Benchmark modules must resolve to the same files in any collection order.

`benchmark/bm07/` and `benchmark/bm08/` share four top-level module names. A
bare `import validate_cases` therefore resolved to whichever directory happened
to be earlier on `sys.path`, and `tests/test_bm07_curation.py` used to insert
bm07's directory at position 0 for the rest of the session. The BM-08 suites
then received bm07's module and failed with
`AttributeError: no attribute 'STABILITY_OBSERVATIONS'` — but only under some
collection orders, which is why it stayed hidden until an archive self-test
happened to reorder them.

Choosing a lucky order is not a fix. These tests assert the property directly:
whichever benchmark is loaded first, both resolve to their own files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from benchmark_modules import BENCH, load, source_of  # noqa: E402

SHARED_NAMES = ("validate_cases", "curation", "mine_corpus", "build_executable_manifest")


def test_the_collision_this_guards_against_is_real():
    """If the names stop colliding, this whole file can go."""
    for name in SHARED_NAMES:
        assert (BENCH / "bm07" / f"{name}.py").is_file(), name
        assert (BENCH / "bm08" / f"{name}.py").is_file(), name


@pytest.mark.parametrize("order", [("bm07", "bm08"), ("bm08", "bm07")])
def test_both_benchmarks_resolve_correctly_in_either_order(order):
    first, second = order
    a = load(first, "validate_cases")
    b = load(second, "validate_cases")
    assert source_of(a) == (BENCH / first / "validate_cases.py").resolve()
    assert source_of(b) == (BENCH / second / "validate_cases.py").resolve()
    assert a is not b, "the two benchmarks must not share one module object"


def test_the_qualified_names_are_distinct_in_sys_modules():
    load("bm07", "validate_cases")
    load("bm08", "validate_cases")
    assert "benchmark.bm07.validate_cases" in sys.modules
    assert "benchmark.bm08.validate_cases" in sys.modules
    assert sys.modules["benchmark.bm07.validate_cases"] is not sys.modules["benchmark.bm08.validate_cases"]


def test_the_bm08_module_is_the_one_carrying_the_stability_contract():
    """The exact attribute whose absence exposed the collision."""
    eight = load("bm08", "validate_cases")
    assert eight.STABILITY_OBSERVATIONS == 3
    seven = load("bm07", "validate_cases")
    assert not hasattr(seven, "STABILITY_OBSERVATIONS")


def test_loading_is_idempotent():
    assert load("bm08", "validate_cases") is load("bm08", "validate_cases")


def test_a_missing_module_is_a_clear_error_not_a_silent_fallback():
    with pytest.raises(ModuleNotFoundError):
        load("bm08", "no_such_module_exists")


def test_the_tests_no_longer_import_the_shared_names_bare():
    """A bare import would reintroduce order dependence."""
    for path in sorted(Path(__file__).parent.glob("test_bm0*.py")):
        source = path.read_text(encoding="utf-8")
        for name in SHARED_NAMES:
            assert f"\nimport {name}" not in source, f"{path.name} imports {name} bare"
            assert f"\nfrom {name} import" not in source, f"{path.name} imports from {name} bare"


def test_no_test_inserts_a_benchmark_directory_at_the_front_of_sys_path():
    """That insertion is what leaked across the whole session."""
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        for benchmark in ("bm07", "bm08"):
            offender = f'sys.path.insert(0, str(Path(__file__).parents[1] / "benchmark" / "{benchmark}"))'
            assert offender not in source, f"{path.name} still front-inserts {benchmark}"
