"""V-16: a clean wheel install exposes the shipped commands.

Marked slow because it builds a wheel and installs it into a throwaway venv.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def clean_env() -> dict[str, str]:
    """An environment that cannot resolve imports from the working tree.

    Without this the venv can import `riftagent` from the source checkout via
    `PYTHONPATH` and the test proves nothing about the wheel. `PIP_*` is
    stripped too, since a stray `PIP_TARGET` or index setting would silently
    change what gets installed.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("PIP_")}
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONSTARTUP", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    tree = {str(ROOT), str(SRC), str(ROOT / "tests")}
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and p not in tree]
    env["PATH"] = os.pathsep.join(parts)
    return env


def venv_python(env_dir: Path) -> Path:
    bin_dir = env_dir / ("Scripts" if sys.platform == "win32" else "bin")
    return bin_dir / ("python.exe" if sys.platform == "win32" else "python")


def rift_script(env_dir: Path) -> Path:
    bin_dir = env_dir / ("Scripts" if sys.platform == "win32" else "bin")
    return bin_dir / ("rift.exe" if sys.platform == "win32" else "rift")


def assert_cannot_import_riftagent(python: Path, env: dict[str, str]) -> None:
    probe = subprocess.run(
        [str(python), "-c", "import riftagent; print(riftagent.__file__)"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(python).parent),
    )
    assert probe.returncode != 0, (
        "the fresh venv could already import riftagent before the wheel was installed, "
        f"so this test would prove nothing: {probe.stdout.strip()}"
    )


def build_wheel(tmp_path: Path, env: dict[str, str]) -> Path:
    dist = tmp_path / "dist"
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(dist), str(ROOT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = list(dist.glob("riftagent-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    return wheels[0]


@pytest.mark.slow
def test_v16_clean_wheel_install_exposes_the_shipped_commands(tmp_path: Path):
    env = clean_env()
    wheel = build_wheel(tmp_path, env)

    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    python = venv_python(env_dir)

    # F1: prove the venv is genuinely empty before the wheel goes in.
    assert_cannot_import_riftagent(python, env)

    install = subprocess.run(
        [str(python), "-m", "pip", "install", "-q", str(wheel)], capture_output=True, text=True, env=env
    )
    assert install.returncode == 0, install.stdout + install.stderr

    # F1: the import must now resolve to site-packages, not the working tree.
    located = subprocess.run(
        [str(python), "-c", "import riftagent, pathlib; print(pathlib.Path(riftagent.__file__).resolve())"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(python.parent),
    )
    assert located.returncode == 0, located.stderr
    resolved = Path(located.stdout.strip())
    assert "site-packages" in resolved.parts, f"riftagent resolved to {resolved}"
    assert SRC.resolve() not in resolved.parents, f"riftagent resolved into the source tree: {resolved}"

    rift = rift_script(env_dir)
    assert rift.is_file(), "the `rift` console script was not installed"

    help_out = subprocess.run([str(rift), "--help"], capture_output=True, text=True, env=env)
    assert help_out.returncode == 0
    for command in ("verify", "resume", "replay"):
        assert command in help_out.stdout, f"`{command}` missing from CLI help"

    verify_help = subprocess.run([str(rift), "verify", "--help"], capture_output=True, text=True, env=env)
    assert verify_help.returncode == 0
    assert "--allow-partial-sandbox" in verify_help.stdout
    assert "--preserve" in verify_help.stdout

    freeze = subprocess.run([str(python), "-m", "pip", "freeze"], capture_output=True, text=True, env=env)
    installed = freeze.stdout.lower()
    for banned in ("openai", "anthropic", "langchain", "langgraph", "httpx", "requests", "pydantic"):
        assert banned not in installed, f"{banned} was pulled in by the wheel"

    shutil.rmtree(tmp_path / "dist", ignore_errors=True)


@pytest.mark.slow
def test_v16_installed_package_runs_a_real_verification(tmp_path: Path):
    """The installed console script, not the source tree, drives a real gate."""
    from tests.conftest import SIMPLE_FILES, SIMPLE_TARGET, build_repo, make_diff

    repo = build_repo(tmp_path / "repo", SIMPLE_FILES)
    diff = make_diff(repo, {"src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n"})
    patch = tmp_path / "fix.diff"
    patch.write_text(diff, encoding="utf-8", newline="\n")

    env = clean_env()
    wheel = build_wheel(tmp_path, env)
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    python = venv_python(env_dir)
    assert_cannot_import_riftagent(python, env)
    subprocess.run(
        [str(python), "-m", "pip", "install", "-q", str(wheel), "pytest==9.1.1"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    result = subprocess.run(
        [
            str(rift_script(env_dir)),
            "--repo",
            str(repo),
            "verify",
            str(patch),
            SIMPLE_TARGET,
            "--allow-partial-sandbox",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Verified against approved checks" in result.stdout
    assert (repo / ".rift" / "tasks").is_dir()


@pytest.mark.slow
def test_v16_source_tree_cannot_leak_into_the_installed_environment(tmp_path: Path):
    """The negative control for F1.

    If `PYTHONPATH` is allowed through, the venv imports riftagent from the
    checkout before anything is installed — which is exactly the condition the
    sanitised environment must rule out. This asserts the leak is real, so the
    sanitisation above is doing work rather than decorating.
    """
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    python = venv_python(env_dir)

    leaky = {**clean_env(), "PYTHONPATH": str(SRC)}
    probe = subprocess.run(
        [str(python), "-c", "import riftagent; print(riftagent.__file__)"],
        capture_output=True,
        text=True,
        env=leaky,
        cwd=str(python.parent),
    )
    assert probe.returncode == 0, "expected the source tree to be importable when PYTHONPATH is set"
    assert str(SRC) in probe.stdout

    assert_cannot_import_riftagent(python, clean_env())
