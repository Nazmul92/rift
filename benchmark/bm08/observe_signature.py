"""One failure-identity observation, in its own process. Model-free.

Exists as a separate program rather than a function because the process boundary
is the measurement. BM-08-v3 froze a signature containing an object's memory
address; that value is stable within an interpreter and differs between
interpreters. Three observations inside one Python process would have agreed
with each other and declared the case reproducible — reproducing the exact
defect the stability rule exists to catch.

So `validate_cases.py` launches this three times, each exiting before the next
begins, and compares what three independent processes saw.

Prints one JSON object on stdout: the governed failure identity, unmodified. No
normalisation of any kind — the amendment adds repeated evidence, not a new
judge.

Usage:
    python observe_signature.py <staging-tree> <target-node>
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "src"))

import bm08_driver as driver  # noqa: E402

from riftagent.sandbox import probe_isolation  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print(json.dumps({"error": "usage: observe_signature.py <tree> <node>"}))
        return 2
    tree, node = pathlib.Path(sys.argv[1]), sys.argv[2]
    try:
        signature = driver.observe_failure_identity(tree, node, probe_isolation())
    except Exception as exc:  # pragma: no cover - environment, not a verdict
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"[:200]}))
        return 1
    if signature is None:
        print(json.dumps({"error": "the governed observer captured no signature"}))
        return 1
    print(
        json.dumps(
            {
                "exception_type": getattr(signature, "exception_type", "") or "",
                "message": getattr(signature, "message", "") or "",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
