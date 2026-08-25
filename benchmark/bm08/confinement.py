"""One authoritative confinement boundary for benchmark-issued repository execution.

Arm C's repository commands are confined by RIFT itself: with `bwrap` usable,
`sandbox.probe_isolation()` reports `IsolationLevel.FULL` and every repository
child runs under `--unshare-net`, while the controller keeps provider access.

The benchmark's own subprocesses bypass RIFT entirely — the oracle, the weak
Arm-A evaluation, preflight checks and corpus validation all invoke pytest
directly. Those were the paths that reached the live network while admission ran
network-denied, so this module gives them the same boundary.

It deliberately does not build a sandbox framework and does not touch RIFT's
sandbox semantics. It wraps an argv in a network-denied namespace and refuses to
run at all if that namespace cannot be created:

    isolation requested but unprovable  ->  raise, do not run

A best-effort fallback would be worse than failing. Repository code that quietly
reached the network is precisely the defect this exists to prevent, and a
silently-degraded wrapper reproduces it while reporting success.

No model is called.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# `unshare --user --net --map-root-user` needs no privileges on a kernel with
# unprivileged user namespaces, and denies the child every network interface
# except its own empty loopback. The parent is untouched.
UNSHARE_ARGV = ("unshare", "--user", "--net", "--map-root-user")

PROBE_SOURCE = (
    "import socket,sys\n"
    "try:\n"
    "    s = socket.create_connection(('1.1.1.1', 53), timeout=4); s.close()\n"
    "    print('NETWORK_REACHED')\n"
    "except Exception as exc:\n"
    "    print('NETWORK_DENIED', type(exc).__name__)\n"
)


class IsolationUnavailable(RuntimeError):
    """Raised when network denial cannot be established. Never downgraded."""


def mechanism() -> dict:
    """What is enforcing confinement, recorded rather than assumed."""
    exe = shutil.which("unshare")
    version = ""
    if exe:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
        version = (proc.stdout or "").strip().splitlines()[0] if proc.returncode == 0 else ""
    return {
        "mechanism": "unshare --user --net --map-root-user",
        "executable": exe or "",
        "version": version,
        "network_policy": "repository subprocess: no interfaces except empty loopback",
    }


def confine(argv: list[str]) -> list[str]:
    """Wrap an argv so the child has no network. Never silently passes through."""
    if not shutil.which("unshare"):
        raise IsolationUnavailable("unshare is not available; repository network denial cannot be enforced")
    return [*UNSHARE_ARGV, *argv]


def run_repository_check(
    argv: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 1800,
) -> subprocess.CompletedProcess:
    """Run one repository-controlled command, network-denied, in an exact worktree."""
    import os

    return subprocess.run(
        confine(argv),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def prove_isolation(python: str, cwd: Path) -> tuple[bool, str]:
    """Behaviourally demonstrate denial by attempting a real socket connection.

    A flag, an environment variable or a config string is not proof. This opens a
    socket from inside the confinement and reports what actually happened.
    """
    proc = run_repository_check([python, "-c", PROBE_SOURCE], cwd, timeout=120)
    out = (proc.stdout or proc.stderr or "").strip()
    return ("NETWORK_DENIED" in out), out[:200]
