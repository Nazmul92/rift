"""The pinned parent, the run's identity, and the audit's fidelity to the product.

Three defects, one theme: something that looked checked was not.

* `icalendar-30ec6eef` pinned a parent **71 commits** behind its fix commit's
  direct parent, and the fix commit did not touch the frozen target at all. It
  passed every reproduction check — the target failed at that parent and passed
  at the fix — so "reproduces the signature" agreed with a task nobody posed.
* A result bound only to the manifest. 226 lines of behavioural runtime change
  landed while the manifest SHA stayed identical, so two results with the same
  stamp could describe products that behave differently.
* The context audit read `case["preserve_files"]`, a key the manifest does not
  have, and so audited an empty protected set against a product that protects
  the target and every preservation test.

Nothing here makes a model call.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark" / "bm06"))

import context_audit as audit  # noqa: E402
import driver as d  # noqa: E402

from riftagent import app  # noqa: E402


def run(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with a real parent → fix pair, plus an unrelated commit."""
    root = tmp_path / "repos" / "proj"
    root.mkdir(parents=True)
    run(root, "init", "-q")
    run(root, "config", "user.email", "t@t")
    run(root, "config", "user.name", "t")
    (root / "calc.py").write_text("def total():\n    return 4\n", encoding="utf-8")
    run(root, "add", "-A")
    run(root, "commit", "-qm", "first")
    (root / "unrelated.py").write_text("x = 1\n", encoding="utf-8")
    run(root, "add", "-A")
    run(root, "commit", "-qm", "unrelated")
    (root / "calc.py").write_text("def total():\n    return 5\n", encoding="utf-8")
    run(root, "add", "-A")
    run(root, "commit", "-qm", "fix")
    return root


def revs(repo: Path) -> list[str]:
    return run(repo, "rev-list", "--reverse", "HEAD").splitlines()


def case_for(repo: Path, parent: str, commit: str) -> dict:
    return {"case_id": "c1", "repo": repo.name, "parent": parent, "commit": commit}


# ------------------------------------------------------------- the parent pin


def test_the_direct_parent_is_accepted(repo: Path):
    first, unrelated, fix = revs(repo)
    assert d.parent_pin_failures(case_for(repo, unrelated, fix), repo.parent) == []


def test_an_ancestor_that_is_not_the_direct_parent_is_rejected(repo: Path):
    """The `icalendar-30ec6eef` shape: a real ancestor, a real reproduction, and
    a task that is not the one commit that fixed it."""
    first, _unrelated, fix = revs(repo)
    failures = d.parent_pin_failures(case_for(repo, first, fix), repo.parent)
    assert failures and "is not the direct parent" in failures[0]
    assert "commits apart" in failures[0]


def test_an_unresolvable_commit_is_rejected(repo: Path):
    _first, _unrelated, fix = revs(repo)
    bad = "0" * 40
    assert any("does not resolve" in f for f in d.parent_pin_failures(case_for(repo, fix, bad), repo.parent))


def test_a_merge_commit_is_rejected_until_a_protocol_governs_it(repo: Path):
    run(repo, "checkout", "-q", "-b", "side", revs(repo)[0])
    (repo / "side.py").write_text("y = 2\n", encoding="utf-8")
    run(repo, "add", "-A")
    run(repo, "commit", "-qm", "side")
    run(repo, "checkout", "-q", "master") if run(repo, "rev-parse", "--abbrev-ref", "HEAD") != "master" else None
    run(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")
    merge = run(repo, "rev-parse", "HEAD")
    parents = run(repo, "rev-list", "--parents", "-n", "1", merge).split()[1:]
    assert len(parents) == 2, "the fixture did not produce a merge"

    failures = d.parent_pin_failures(case_for(repo, parents[0], merge), repo.parent)
    assert failures and "is a merge" in failures[0]


def test_a_missing_repository_is_rejected_rather_than_skipped(tmp_path: Path):
    case = {"case_id": "c1", "repo": "absent", "parent": "a" * 40, "commit": "b" * 40}
    assert d.parent_pin_failures(case, tmp_path / "nowhere")


def test_validation_says_so_when_the_pin_was_not_checked(tmp_path: Path):
    """An unrun check that reads like a passed check is the shape of every
    defect this file exists for."""
    manifest = {
        "arms": {a: {"description": "x", **({"seed": 1} if a == "B" else {})} for a in ("A", "B", "C")},
        "budget": {"scope": "s", "max_usd": 1.0},
        "model": {
            "id": "m",
            "max_probes": 1,
            "max_attempts": 1,
            "max_commands": 1,
            "max_output_tokens": 1,
            "price_input_per_mtok": 3.0,
            "price_output_per_mtok": 15.0,
            "timeout_s": 600.0,
        },
        "cases": [
            {
                "case_id": "c1",
                "repo": "proj",
                "parent": "a" * 40,
                "commit": "b" * 40,
                "target": "t.py::t",
                "signature": "E: m",
                "preserve": ["t.py::p"],
                "worktree": str(tmp_path),
                "cause_class": "genuine_source_bug",
                "label": "l",
            }
        ],
    }
    failures = d.validate_manifest(manifest, tmp_path, repos=None)
    assert any("NOT_CHECKED parent pin" in f for f in failures)


# --------------------------------------------------------------- run identity


def test_the_runtime_hash_covers_the_shipped_package(tmp_path: Path):
    digest, files = d.runtime_hash(Path(__file__).resolve().parents[1])
    assert files, "no runtime files were hashed"
    assert all(f.startswith("src/riftagent/") and f.endswith(".py") for f in files)
    assert "src/riftagent/app.py" in files and "src/riftagent/kernel.py" in files
    assert not any("__pycache__" in f for f in files)
    assert len(digest) == 64


def test_the_runtime_hash_changes_when_any_governed_file_changes(tmp_path: Path):
    root = tmp_path / "r"
    (root / "src" / "riftagent").mkdir(parents=True)
    (root / "src" / "riftagent" / "a.py").write_text("x = 1\n", encoding="utf-8")
    first, _ = d.runtime_hash(root)
    (root / "src" / "riftagent" / "a.py").write_text("x = 2\n", encoding="utf-8")
    second, _ = d.runtime_hash(root)
    assert first != second


def test_the_runtime_hash_distinguishes_a_move_from_a_change(tmp_path: Path):
    """Content alone cannot tell a renamed file from an edited one, so the path
    is hashed alongside the bytes."""
    root = tmp_path / "r"
    pkg = root / "src" / "riftagent"
    pkg.mkdir(parents=True)
    (pkg / "a.py").write_text("x = 1\n", encoding="utf-8")
    first, _ = d.runtime_hash(root)
    (pkg / "a.py").unlink()
    (pkg / "b.py").write_text("x = 1\n", encoding="utf-8")
    assert d.runtime_hash(root)[0] != first


def test_the_runtime_hash_is_deterministic(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    assert d.runtime_hash(root) == d.runtime_hash(root)


# ------------------------------------------------- the startup snapshot rule


def test_the_result_carries_the_startup_identity_not_a_later_re_read(tmp_path: Path, monkeypatch):
    """The TOCTOU rule, proven by mutating a governed file mid-run.

    A driver that re-reads to stamp its output would execute under one runtime
    and label the result with another, and nothing in the artifact would show
    it.
    """
    root = tmp_path / "r"
    pkg = root / "src" / "riftagent"
    pkg.mkdir(parents=True)
    (pkg / "app.py").write_text("VERSION = 1\n", encoding="utf-8")
    startup_hash, _ = d.runtime_hash(root)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "arms": {a: {"description": "x", **({"seed": 1} if a == "B" else {})} for a in ("A", "B", "C")},
                "budget": {"scope": "s", "max_usd": 1.0},
                "model": {
                    "id": "m",
                    "max_probes": 1,
                    "max_attempts": 1,
                    "max_commands": 1,
                    "max_output_tokens": 1,
                    "price_input_per_mtok": 3.0,
                    "price_output_per_mtok": 15.0,
                    "timeout_s": 600.0,
                },
                "cases": [
                    {
                        "case_id": "c1",
                        "repo": "proj",
                        "parent": "p",
                        "commit": "c",
                        "target": "t.py::t",
                        "signature": "E: m",
                        "preserve": ["t.py::p"],
                        "worktree": str(worktree),
                        "cause_class": "genuine_source_bug",
                        "baseline_tree_hash": "frozen-tree",
                        "label": "l",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # Every gate the run would apply is stubbed out except the one under test,
    # and the runtime file is edited *during* the run — at the moment the driver
    # would be executing arms.
    monkeypatch.setattr(d, "validate_manifest", lambda *a, **k: [])
    # A synthetic runtime root cannot win the import race against the
    # installed package, and that refusal is correct — it is asserted
    # directly in test_dar024_binding. What is under test here is which
    # hash gets stamped, so only the resolution probe is stood in for.
    monkeypatch.setattr(d, "resolves_to", lambda root, env, cwd=None: str(Path(root) / "src" / "riftagent" / "app.py"))
    monkeypatch.setattr(d, "baseline_tree_hash", lambda root: "frozen-tree")
    monkeypatch.setattr(d, "git", lambda repo, *a: (0, "p") if a[:1] == ("rev-parse",) else (0, ""))

    # The capability probe is where the run first touches the CLI, and since
    # DAR-026 it goes through `Bound.supports` rather than the module-level
    # `cli_supports` — so that is where a mid-run edit has to be injected.
    def mutate_then_refuse(self, flag, cwd):
        (pkg / "app.py").write_text("VERSION = 2\n", encoding="utf-8")
        return False

    monkeypatch.setattr(d.Bound, "supports", mutate_then_refuse)

    out = tmp_path / "results.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "driver",
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--work",
            str(tmp_path),
            "--runtime-root",
            str(root),
            "--repos",
            str(tmp_path),
        ],
    )
    assert d.main() == 0

    later_hash, _ = d.runtime_hash(root)
    assert startup_hash != later_hash, "the fixture did not actually change the runtime mid-run"

    stamped = json.loads(out.read_text(encoding="utf-8"))["runtime_hash"]
    assert stamped == startup_hash, "the result was stamped with a re-read hash, not the startup snapshot"
    assert stamped != later_hash


# ------------------------------------------------ report-only fails on each


@pytest.mark.parametrize("field", ["manifest_hash", "runtime_hash", "driver_hash"])
def test_report_only_refuses_when_any_identity_differs(tmp_path: Path, capsys, field: str, monkeypatch):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"cases": [], "arms": {}}), encoding="utf-8")
    _, manifest_hash = d.load_manifest(manifest)
    runtime_root = tmp_path / "r"
    (runtime_root / "src" / "riftagent").mkdir(parents=True)
    (runtime_root / "src" / "riftagent" / "a.py").write_text("x = 1\n", encoding="utf-8")
    rt_hash, _ = d.runtime_hash(runtime_root)
    drv_hash = d.file_hash(Path(d.__file__))

    stamped = {"manifest_hash": manifest_hash, "runtime_hash": rt_hash, "driver_hash": drv_hash, "records": []}
    stamped[field] = "0" * 64  # exactly one identity disagrees
    out = tmp_path / "results.json"
    out.write_text(json.dumps(stamped), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "driver",
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--report-only",
            "--runtime-root",
            str(runtime_root),
        ],
    )
    assert d.main() == 2, f"a differing {field} was reported anyway"
    printed = capsys.readouterr().out
    assert "REFUSING TO REPORT" in printed
    assert field in printed


def test_report_only_proceeds_when_all_three_agree(tmp_path: Path, capsys, monkeypatch):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"cases": [], "arms": {}}), encoding="utf-8")
    _, manifest_hash = d.load_manifest(manifest)
    runtime_root = tmp_path / "r"
    (runtime_root / "src" / "riftagent").mkdir(parents=True)
    (runtime_root / "src" / "riftagent" / "a.py").write_text("x = 1\n", encoding="utf-8")
    rt_hash, _ = d.runtime_hash(runtime_root)

    out = tmp_path / "results.json"
    out.write_text(
        json.dumps(
            {
                "manifest_hash": manifest_hash,
                "runtime_hash": rt_hash,
                "driver_hash": d.file_hash(Path(d.__file__)),
                "records": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "driver",
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--report-only",
            "--runtime-root",
            str(runtime_root),
        ],
    )
    assert d.main() == 0


# ------------------------------------------------------ audit/product parity


def test_the_audit_takes_its_protected_paths_from_the_product(tmp_path: Path):
    """Not an equivalent reimplementation — the same function.

    The previous audit read `case["preserve_files"]`, which the manifest does
    not define, so it passed an empty protected set while the product protected
    the target and every preservation test. It audited a configuration that
    never runs.
    """
    target = "tests/test_calc.py::test_total"
    preserve = ["tests/test_other.py::test_a", "tests/test_other.py::test_b"]

    product = app.build_checkset(target, tuple(preserve), tmp_path, 600.0).protected_paths
    assert "tests/test_calc.py" in product
    assert "tests/test_other.py" in product

    # Asserted on the code, not the prose. The module docstring names the old
    # broken key in order to explain it, and a plain grep would fail on the
    # explanation while passing a file that still contained the defect.
    tree = ast.parse(Path(audit.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            node.value.value = ""
    code = ast.unparse(tree)
    assert "build_checkset" in code, "the audit builds its own protected set instead of using the product's"
    assert "preserve_files" not in code, "the audit still reads a key the manifest does not define"
    assert "'preserve'" in code or '"preserve"' in code


def test_the_manifest_uses_preserve_and_not_preserve_files():
    """The key the defect turned on, asserted against the frozen manifest."""
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "benchmark" / "bm06" / "manifest-preliminary.json").read_text(
            encoding="utf-8"
        )
    )
    for case in manifest["cases"]:
        assert "preserve" in case, case["case_id"]
        assert "preserve_files" not in case, case["case_id"]


# ---------------------------------------------------------------- quarantine


def test_the_invalid_case_is_quarantined_and_preserved():
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "benchmark" / "bm06" / "manifest-preliminary.json").read_text(
            encoding="utf-8"
        )
    )
    live = {c["case_id"] for c in manifest["cases"]}
    assert "icalendar-30ec6eef-locale_timezone" not in live, "the invalid case is still runnable"
    assert len(live) == 8, f"the valid denominator is {len(live)}, not 8"

    quarantined = manifest["quarantined"]
    assert len(quarantined) == 1
    entry = quarantined[0]
    assert entry["case"]["case_id"] == "icalendar-30ec6eef-locale_timezone"
    assert entry["reason"] == "fix-parent mismatch; target not part of fix commit"
    # The whole record, not a stub: a quarantined case that cannot be
    # re-examined is an assertion, not evidence.
    assert entry["case"]["parent"] and entry["case"]["commit"] and entry["case"]["target"]
    assert manifest["corpus_status"]["valid_cases"] == 8
    assert manifest["corpus_status"]["quarantined_cases"] == 1
