"""Behaviourally prove repository network denial on every execution path.

A flag, an environment variable or a config string is not proof. Each path below
runs a synthetic repository-side probe that attempts a real socket connection,
and reports what actually happened.

`dnspython` is deliberately not the proof. It is one project's tests reacting to
one kind of network access; a synthetic probe that opens a socket directly tests
the boundary rather than a symptom of it.

The controller-scoping check is the positive half: it establishes that the
architecture can support provider HTTP while repository subprocesses are denied,
which is the whole point. Confining the entire controller would satisfy "network
denied" and break the benchmark.

No model is called and no provider request is made.
"""

from __future__ import annotations

import json
import pathlib
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "src"))

import bm08_oracle as oracle  # noqa: E402
import confinement  # noqa: E402

from riftagent.sandbox import probe_isolation  # noqa: E402

PROBE_TEST = '''import socket


def test_repository_side_network_probe():
    """Synthetic repository-controlled code attempting a real connection."""
    try:
        s = socket.create_connection(("1.1.1.1", 53), timeout=4)
        s.close()
        raise AssertionError("NETWORK_REACHED")
    except AssertionError:
        raise
    except Exception:
        pass
'''


def make_probe_repo(root: pathlib.Path) -> pathlib.Path:
    tree = root / "probe-repo"
    (tree / "tests").mkdir(parents=True, exist_ok=True)
    (tree / "tests" / "test_probe.py").write_text(PROBE_TEST, encoding="utf-8")
    return tree


def main() -> int:
    root = pathlib.Path("/tmp/bm08-isolation")
    tree = make_probe_repo(root)
    node = "tests/test_probe.py::test_repository_side_network_probe"
    results: dict[str, str] = {}

    # 1. the direct harness boundary used by validation and preflight
    ok, detail = confinement.prove_isolation(sys.executable, tree)
    results["harness confinement (validation / preflight)"] = "DENIED" if ok else f"REACHED — {detail}"

    # 2-4. every benchmark path that runs repository tests goes through the
    # oracle's pytest entry point: oracle/truth, Arm-A weak evaluation, and the
    # mandatory preflight's target and preservation checks.
    outcome = oracle.run_node(tree, node, "flat")
    results["oracle / truth path"] = "DENIED" if outcome == "pass" else f"REACHED — probe outcome {outcome}"
    passed, failures, _ = oracle.run_all(tree, [node], "flat")
    results["Arm A / weak path"] = "DENIED" if passed else f"REACHED — {failures[:1]}"
    results["mandatory preflight path"] = "DENIED" if outcome == "pass" else f"REACHED — {outcome}"

    # 5. Arm C repository commands are confined by RIFT itself.
    probe = probe_isolation()
    full = str(probe.level) == "full" and bool(probe.bwrap)
    results["Arm C repository path (RIFT sandbox)"] = (
        f"DENIED — {probe.detail}" if full else f"NOT PROVEN — {probe.level}: {probe.detail}"
    )

    print("REPOSITORY-SIDE NETWORK PROBES")
    for label, verdict in results.items():
        print(f"  {label:42} {verdict}")

    # Positive scoping: the controller keeps network while children do not.
    controller = "REACHED"
    try:
        s = socket.create_connection(("1.1.1.1", 53), timeout=5)
        s.close()
    except Exception as exc:  # pragma: no cover - depends on host network
        controller = f"UNAVAILABLE ({type(exc).__name__})"
    print("\nCONTROLLER SCOPING")
    print(f"  controller-side connection                 {controller}")
    print("  controller process itself confined?        NO (confinement wraps children only)")

    denied = all(v.startswith("DENIED") for v in results.values())
    scoped = controller == "REACHED"
    print(f"\nmechanism: {json.dumps(confinement.mechanism(), sort_keys=True)}")
    if not denied:
        print("\nBLOCKED_ENVIRONMENT_ISOLATION: a repository path reached the network")
        return 2
    if not scoped:
        print("\nBLOCKED_ENVIRONMENT_ISOLATION: controller scope could not be demonstrated")
        return 2
    print("\nISOLATION PROVEN: every repository path denied, controller scope intact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
