"""V-01: `rift verify` is model-free by construction, not by intention.

These are AST and filesystem assertions over the shipped runtime. They are the
cheapest possible evidence and the hardest to fake: a provider import cannot be
added later without turning one of them red.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import riftagent

RUNTIME = Path(riftagent.__file__).parent
MODULES = sorted(p for p in RUNTIME.glob("*.py"))

# `llm.py` is the single module permitted to reach the network. Everything
# else stays offline, and the exemption is named here rather than implied.
NETWORK_IMPORTS = {"urllib", "urllib3", "http", "socket", "ssl", "httpx", "requests", "aiohttp"}
NETWORK_EXEMPT = {"llm.py"}

FORBIDDEN_IMPORTS = {
    "openai",
    "anthropic",
    "httpx",
    "requests",
    "aiohttp",
    "urllib",
    "urllib3",
    "http",
    "socket",
    "ssl",
    "langchain",
    "langgraph",
    "crewai",
    "autogen",
    "llama_index",
    "pydantic",
    "typer",
    "rich",
    "click",
    "sqlite3",
    "pickle",
    "shelve",
}

# The kernel is the epistemic authority and must stay unreachable from the
# model side of the system, so it may not import the loop, the renderer, a
# provider, or the network.
KERNEL_FORBIDDEN = {"riftagent.app", "riftagent.llm", "riftagent.sandbox", "riftagent.checks"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.add(node.module)
    return found


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_runtime_module_imports_a_provider_or_the_network(path: Path):
    roots = {name.split(".")[0] for name in _imports(path)}
    allowed = NETWORK_IMPORTS if path.name in NETWORK_EXEMPT else set()
    offending = (roots & FORBIDDEN_IMPORTS) - allowed
    assert not offending, f"{path.name} imports {sorted(offending)}"


@pytest.mark.parametrize("path", sorted(set(MODULES) - {RUNTIME / "llm.py"}), ids=lambda p: p.name)
def test_only_llm_may_reach_the_network(path: Path):
    """Every module except the adapter stays offline."""
    roots = {name.split(".")[0] for name in _imports(path)}
    assert not (roots & NETWORK_IMPORTS), f"{path.name} imports a networking package"


def test_no_provider_sdk_is_shipped():
    """The adapter is plain HTTP. A provider SDK would smuggle in retry,
    tool-calling and streaming behaviour the design deliberately excludes."""
    text = (RUNTIME / "llm.py").read_text(encoding="utf-8")
    for banned in ("import openai", "import anthropic", "from openai", "from anthropic"):
        assert banned not in text, f"llm.py uses a provider SDK ({banned})"


def test_llm_imports_no_kernel_sandbox_or_checks():
    """The adapter proposes; it never decides or executes."""
    imported = _imports(RUNTIME / "llm.py")
    for forbidden in ("riftagent.kernel", "riftagent.sandbox", "riftagent.checks", "riftagent.app"):
        assert forbidden not in imported, f"llm.py imports {forbidden}"


def test_llm_and_kernel_share_contracts_only_through_records():
    """Both sides validate against the same contract without touching each
    other — the IR lives in records.py for exactly this reason."""
    llm_imports = {i for i in _imports(RUNTIME / "llm.py") if i.startswith("riftagent")}
    kernel_imports = {i for i in _imports(RUNTIME / "kernel.py") if i.startswith("riftagent")}
    assert llm_imports <= {"riftagent.records"}, f"llm.py reaches beyond records: {llm_imports}"
    assert kernel_imports <= {"riftagent.records"}, f"kernel.py reaches beyond records: {kernel_imports}"


def test_kernel_imports_no_loop_provider_or_execution_authority():
    imported = _imports(RUNTIME / "kernel.py")
    for forbidden in KERNEL_FORBIDDEN:
        assert forbidden not in imported, f"kernel imports {forbidden}"


def test_kernel_exposes_no_callable_injection_point():
    """A callback parameter would be the import boundary's back door."""
    tree = ast.parse((RUNTIME / "kernel.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            args = node.args
            names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
            for name in names:
                assert not any(
                    token in name.lower() for token in ("callback", "client", "model", "proposer", "llm", "adapter")
                ), f"kernel.{node.name} accepts an injectable {name}"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_runtime_path_uses_a_shell(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    assert not (isinstance(kw.value, ast.Constant) and kw.value.value), f"{path.name} uses shell=True"
        if isinstance(node, ast.Attribute) and node.attr in ("system", "popen"):
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                pytest.fail(f"{path.name} calls os.{node.attr}")


def test_no_orchestration_or_checkpoint_dependency():
    pyproject = (RUNTIME.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    for banned in ("langgraph", "langchain", "crewai", "autogen", "prefect", "airflow", "celery"):
        assert banned not in pyproject.lower(), f"{banned} appears in project metadata"


def test_runtime_declares_no_dependencies():
    pyproject = (RUNTIME.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
