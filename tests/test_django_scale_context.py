"""M1-R04: bounded context selection on a Django-scale repository.

The row is `required-or-disclosed`. It needs a real repository large enough that
an unbounded selector would obviously blow the caps, so it cannot run from the
credential-free, network-free main suite: it is skipped unless
`RIFT_LARGE_REPO` points at a checkout, and its `NOT_RUN_NETWORK_UNAVAILABLE`
disclosure lives in `IMPLEMENTATION_STATUS.md`.

What it must show is not "the caps held" — a selector that returned nothing at
all would satisfy that. It is that the caps held **while the files the failure
actually named survived**. Both halves are asserted, and the repository's size
is asserted too, so the test cannot quietly pass against a small checkout.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from riftagent.app import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_FILES,
    imported_modules,
    select_context,
)

LARGE_REPO = os.environ.get("RIFT_LARGE_REPO", "")

pytestmark = pytest.mark.skipif(
    not LARGE_REPO or not Path(LARGE_REPO).is_dir(),
    reason="NOT_RUN_NETWORK_UNAVAILABLE: set RIFT_LARGE_REPO to a pinned large checkout",
)

# A pytest-shaped traceback naming real files in the checkout. Written by hand
# only in its frame *shape*; every path in it is asserted to exist below, so a
# renamed module fails the test instead of silently selecting nothing.
FRAME_FILES = [
    "django/core/exceptions.py",
    "django/utils/text.py",
    "django/http/request.py",
]
TARGET = "tests/utils_tests/test_text.py::TextTests::test_get_text_list"


def repo() -> Path:
    return Path(LARGE_REPO).resolve()


def failure_text(root: Path) -> str:
    frames = "\n".join(
        f'  File "{root / rel}", line 42, in _something\n    raise ValueError("boom")' for rel in FRAME_FILES
    )
    return f"Traceback (most recent call last):\n{frames}\nValueError: boom\n"


def test_the_checkout_is_actually_django_scale():
    """The positive control for every assertion below. A cap trivially holds on
    a small repository, so the size is evidence, not background."""
    root = repo()
    py_files = list(root.rglob("*.py"))
    assert len(py_files) > 2000, f"only {len(py_files)} Python files; this is not a Django-scale checkout"
    assert (root / "django" / "__init__.py").is_file()
    for rel in FRAME_FILES:
        assert (root / rel).is_file(), f"{rel} is absent from this checkout"
    # Expressed against the cap rather than as a round number: what makes the
    # bound interesting is that the repository is orders of magnitude larger
    # than the context this selector is allowed to send. Django 5.0.6 measures
    # 17,060,221 bytes across 2,774 files, against a 60,000-character cap.
    total = sum(p.stat().st_size for p in py_files)
    assert total > 100 * MAX_CONTEXT_CHARS, f"{total} bytes of Python is not Django-scale against the cap"


def test_context_stays_within_caps_while_the_cited_files_survive():
    root = repo()
    chosen, manifest = select_context(root, failure_text(root), TARGET, ())

    # 1. the caps held
    assert len(chosen) <= MAX_CONTEXT_FILES, manifest["files"]
    assert manifest["chars"] <= MAX_CONTEXT_CHARS
    assert manifest["cap_files"] == MAX_CONTEXT_FILES
    assert sum(len(text) for _, text in chosen) == manifest["chars"]

    # 2. and they held by *excerpting*, not by dropping everything: the whole
    #    files are far larger than what was sent
    whole = sum((root / rel).stat().st_size for rel in FRAME_FILES)
    assert whole > manifest["chars"], "the selector sent as much as the raw files contain"
    assert chosen, "nothing was selected; a cap that holds by returning nothing proves nothing"

    # 3. the deepest cited frame is first, and every cited file that fits is
    #    present. Ordering is the property that decides what survives a cap.
    assert manifest["files"][0] == FRAME_FILES[-1], manifest["files"]
    for rel in FRAME_FILES:
        assert rel in manifest["files"], f"{rel} was cited by the failure and did not survive: {manifest}"

    # 4. only bounded windows left the machine, and they are recorded per file
    for rel in FRAME_FILES:
        ranges = manifest["line_ranges"][rel]
        assert ranges, f"{rel} was sent with no recorded line range"
        assert all(0 < start <= end for start, end in ranges), ranges

    # 4b. no *selected* file may record an empty range. Django's packages
    #     include empty `__init__.py` files, and one was being selected as a
    #     bare header with a `[1, 0]` range: a manifest entry claiming a file
    #     was sent when nothing was, and one of six slots spent on it.
    for rel, ranges in manifest["line_ranges"].items():
        assert ranges, f"{rel} is recorded as selected with no range at all"
        assert all(start <= end for start, end in ranges), f"{rel} records an empty range: {ranges}"

    # 4c. no file is sent twice. A traceback cites by absolute path and the
    #     import graph by relative path; both resolve to the same file, and
    #     `django/core/exceptions.py` was appearing twice in `files` against one
    #     entry in `line_ranges` — its bytes sent twice, two slots spent.
    assert len(manifest["files"]) == len(set(manifest["files"])), manifest["files"]
    assert set(manifest["files"]) == set(manifest["line_ranges"]), (
        manifest["files"],
        sorted(manifest["line_ranges"]),
    )
    assert len({rel for rel, _ in chosen}) == len(chosen)

    # 5. nothing outside the repository, nothing from .rift or .git
    for rel in manifest["files"]:
        path = (root / rel).resolve()
        assert path.is_file() and path.is_relative_to(root)
        assert ".rift" not in path.parts and ".git" not in path.parts


def test_the_target_module_and_its_imports_are_reachable_at_this_scale():
    """The import-graph signal is the half that a value-assertion failure
    depends on, because no frame names the implementation in that case."""
    root = repo()
    target_file = TARGET.split("::")[0]
    if not (root / target_file).is_file():
        pytest.skip(f"NOT_RUN_FIXTURE_ABSENT: {target_file} is not in this checkout")
    modules = imported_modules(root, target_file)
    assert modules, "the target test module imports nothing resolvable in the repository"
    assert len(modules) <= 8, modules
    chosen, manifest = select_context(root, "", TARGET, ())
    assert manifest["files"], "no context was selected from the import graph alone"
    assert len(chosen) <= MAX_CONTEXT_FILES
    assert manifest["chars"] <= MAX_CONTEXT_CHARS
    assert any(m in manifest["files"] for m in modules), (modules, manifest["files"])


def test_a_protected_path_is_never_selected_even_when_cited():
    root = repo()
    protected = (FRAME_FILES[-1],)
    _, manifest = select_context(root, failure_text(root), TARGET, protected)
    assert FRAME_FILES[-1] not in manifest["files"]
    assert any("protected or excluded" in s for s in manifest["skipped"]), manifest["skipped"]
