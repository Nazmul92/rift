"""Five-case live calibration for `rift fix`, plus one smoke request.

Ground truth is declared per case *before* anything runs, so a false acceptance
is measurable rather than a matter of opinion. Three cases are genuinely fixable
by an implementation patch; two are not, and for those the only correct outcomes
are abstention or gate rejection — an acceptance there is a false acceptance and
is counted as one.

Every request goes through the runtime's own reservation rule. This script
never talks to a provider itself; it invokes the CLI.
"""

import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path("/cal")

# ---------------------------------------------------------------- fixtures

CASES = [
    {
        "id": "C1-sign",
        "fixable": True,
        "why": "a single wrong operator in the implementation",
        "files": {
            "src/app/__init__.py": "",
            "src/app/calc.py": "def add(a, b):\n    return a - b\n",
            "tests/test_calc.py": (
                "from app.calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"
            ),
        },
        "target": "tests/test_calc.py::test_add",
    },
    {
        "id": "C2-offbyone",
        "fixable": True,
        "why": "an off-by-one slice bound",
        "files": {
            "src/app/__init__.py": "",
            "src/app/text.py": "def first_n(items, n):\n    return items[: n - 1]\n",
            "tests/test_text.py": (
                "from app.text import first_n\n\n\n"
                "def test_first_n():\n    assert first_n([1, 2, 3, 4], 3) == [1, 2, 3]\n"
            ),
        },
        "target": "tests/test_text.py::test_first_n",
    },
    {
        "id": "C3-default",
        "fixable": True,
        "why": "a missing default on a dict lookup",
        "files": {
            "src/app/__init__.py": "",
            "src/app/cfg.py": "def setting(d, key):\n    return d[key]\n",
            "tests/test_cfg.py": (
                "from app.cfg import setting\n\n\n"
                "def test_missing_key_is_none():\n    assert setting({}, 'absent') is None\n"
            ),
        },
        "target": "tests/test_cfg.py::test_missing_key_is_none",
    },
    {
        "id": "C4-order",
        "fixable": False,
        "why": "order-dependent pollution from a neighbouring test; no patch to the "
        "target module fixes it, and the test alone already passes",
        "files": {
            "src/app/__init__.py": "",
            "src/app/registry.py": "REGISTRY = {}\n\n\ndef put(k, v):\n    REGISTRY[k] = v\n",
            "tests/test_a_pollute.py": (
                "from app.registry import put\n\n\ndef test_pollutes():\n    put('leak', 1)\n    assert True\n"
            ),
            "tests/test_target.py": (
                "from app.registry import REGISTRY\n\n\n"
                "def test_clean():\n    assert REGISTRY == {}\n"
            ),
        },
        "target": "tests/test_target.py::test_clean",
    },
    {
        "id": "C5-impossible",
        "fixable": False,
        "why": "the test asserts a contradiction; no implementation satisfies it, and "
        "the test is frozen",
        "files": {
            "src/app/__init__.py": "",
            "src/app/pure.py": "def value():\n    return 1\n",
            "tests/test_pure.py": (
                "from app.pure import value\n\n\n"
                "def test_contradiction():\n    v = value()\n    assert v > 0 and v < 0\n"
            ),
        },
        "target": "tests/test_pure.py::test_contradiction",
    },
]


def build(case: dict) -> pathlib.Path:
    repo = ROOT / case["id"]
    for rel, body in case["files"].items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8", newline="\n")
    return repo


# ---------------------------------------------------------------- runner

MAX_OUTPUT = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
CAP = sys.argv[2] if len(sys.argv) > 2 else "0.30"


def run_case(case: dict, cap: str, max_output: int) -> dict:
    repo = build(case)
    t0 = time.time()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "riftagent",
            "--repo",
            str(repo),
            "--json",
            "fix",
            case["target"],
            "--allow-partial-sandbox",
            "--max-probes",
            "4",
            "--max-attempts",
            "1",
            "--max-output-tokens",
            str(max_output),
            "--max-usd",
            cap,
            "--price-input",
            "1.0",
            "--price-output",
            "5.0",
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    wall = time.time() - t0
    receipt = {}
    for line in reversed(proc.stdout.strip().splitlines()):
        if line.startswith("{"):
            receipt = json.loads(line)
            break

    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    events = [json.loads(x) for x in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    spend = [e["payload"] for e in events if e["kind"] == "spend_settled"]
    reserved = [e["payload"] for e in events if e["kind"] == "spend_reserved"]
    refused = [e["payload"] for e in events if e["kind"] == "spend_refused"]

    verdict = receipt.get("verdict", "no-receipt")
    accepted = verdict == "verified_against_approved_checks"
    return {
        "id": case["id"],
        "ground_truth_fixable": case["fixable"],
        "verdict": verdict,
        "accepted": accepted,
        "false_acceptance": accepted and not case["fixable"],
        "correct": accepted if case["fixable"] else not accepted,
        "exit_code": proc.returncode,
        "commands": receipt.get("commands", 0),
        "wall_s": round(wall, 2),
        "requests": len(reserved),
        "reserved_usd": round(sum(r["reserved_usd"] for r in reserved), 6),
        "charged_usd": round(sum(s["charged_usd"] for s in spend), 6),
        "released_usd": round(sum(s["released_usd"] for s in spend), 6),
        "input_tokens": sum((s["usage"].get("input_tokens") or 0) for s in spend),
        "output_tokens": sum((s["usage"].get("output_tokens") or 0) for s in spend),
        "usage_unknown": any(s["usage_source"] != "provider_reported" for s in spend),
        "refused": len(refused),
        "rejected_phase": receipt.get("rejected_phase"),
    }


if __name__ == "__main__":
    which = sys.argv[3] if len(sys.argv) > 3 else "all"
    selected = [c for c in CASES if which in ("all", c["id"])]
    out = [run_case(c, CAP, MAX_OUTPUT) for c in selected]
    print("RESULTS_JSON " + json.dumps(out))
