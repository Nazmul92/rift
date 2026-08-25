"""The execution environment as a frozen identity. No model, no network.

Source-code hashes pin what runs. They said nothing about *where*, and BM-08
learned the hard way that the difference is scientific: a corpus admitted under
network denial and evaluated with network available is not the same experiment,
and the mismatch surfaced only when a paid run was already authorised.

So the environment becomes an identity of the same standing as
`runtime_hash` — recorded in preflight evidence and every arm result, checked
before the first provider call and again before aggregation.

Bound here: the container image digest, the Python version, the isolation
mechanism and its version, and the network-isolation configuration. Anything
that could change a repository test's behaviour without changing a single byte
of tracked source.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import platform
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import confinement  # noqa: E402

# The container this benchmark executes in, and the flags that make repository
# confinement possible. Recorded rather than assumed: bwrap needs these to
# create namespaces, and without them RIFT silently degrades to PARTIAL —
# "no filesystem or network confinement" — which is the defect this exists to
# prevent recurring.
IMAGE = "rift-reference-iso:3.12-slim"
DOCKER_FLAGS = "--cap-add SYS_ADMIN --cap-add NET_ADMIN --security-opt seccomp=unconfined"


def image_digest() -> str:
    """The image's own content digest, read from inside it where possible."""
    for candidate in ("/etc/bm08-image-digest", "/etc/image-digest"):
        path = pathlib.Path(candidate)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    # Fall back to a digest over the interpreter and OS release, which still
    # changes when the image is rebuilt on a different base.
    parts = [sys.version, platform.platform()]
    release = pathlib.Path("/etc/os-release")
    if release.is_file():
        parts.append(release.read_text(encoding="utf-8"))
    return "sha256:" + hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def rift_isolation() -> dict:
    """The tier RIFT itself selects for Arm C's repository commands."""
    from riftagent.sandbox import probe_isolation

    probe = probe_isolation()
    return {
        "level": str(probe.level),
        "detail": probe.detail,
        "bwrap": probe.bwrap or "",
        "bwrap_version": _bwrap_version(),
    }


def _bwrap_version() -> str:
    import shutil

    exe = shutil.which("bwrap")
    if not exe:
        return ""
    proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def describe() -> dict:
    """The canonical environment record. Ordered fields, no timestamps."""
    return {
        "container_image": IMAGE,
        "container_image_digest": image_digest(),
        "docker_security_flags": DOCKER_FLAGS,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "harness_confinement": confinement.mechanism(),
        "rift_repository_isolation": rift_isolation(),
        "network_policy": {
            "repository_controlled_execution": "denied",
            "provider_and_controller": "allowed",
        },
    }


def environment_hash(record: dict | None = None) -> str:
    body = json.dumps(record or describe(), indent=1, sort_keys=True) + "\n"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def main() -> int:
    record = describe()
    digest = environment_hash(record)
    print(json.dumps(record, indent=1, sort_keys=True))
    print(f"\nexecution_environment_hash : {digest}")
    out = pathlib.Path("/s/bm08_execution_environment.json")
    out.write_text(
        json.dumps({**record, "execution_environment_hash": digest}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"written: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
