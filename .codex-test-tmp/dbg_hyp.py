"""Throwaway diagnostic: what does `why` actually record on the unresolved fixture?"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "tests")
sys.path.insert(0, ".")

from tests.conftest import build_repo  # noqa: E402
from tests.test_propose_hypotheses import UNRESOLVED_FILES  # noqa: E402

tmp = Path(tempfile.mkdtemp())
repo = build_repo(tmp / "unresolved", UNRESOLVED_FILES)

from riftagent.app import main  # noqa: E402

code = main(
    [
        "--repo",
        str(repo),
        "--json",
        "why",
        "tests/test_calc.py::test_total",
        "--allow-partial-sandbox",
        "--max-probes",
        sys.argv[1] if len(sys.argv) > 1 else "8",
        "--max-commands",
        "400",
    ]
)
print("exit", code)
td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
for line in (td / "ledger.jsonl").read_text(encoding="utf-8").splitlines():
    e = json.loads(line)
    p = e["payload"]
    extra = ""
    if e["kind"] == "handles_discovered":
        extra = str([h["kind"] + ":" + h["arg"] for h in p["handles"]])
    elif e["kind"] == "hypotheses_proposed":
        extra = f"count={p['count']} roles={p['roles']}"
    elif e["kind"] == "probe_selected":
        extra = f"{p['probe']} -> {p['observation']['outcome']}"
    elif e["kind"] == "diagnosis_emitted":
        extra = p["diagnosis"]["status"] + " classes=" + str(p["diagnosis"]["surviving_classes"])
        extra += " notes=" + json.dumps(p["diagnosis"]["notes"])[:400]
    elif e["kind"] == "check_result":
        extra = p["result"]["outcome"]
    print(e["kind"], extra)
