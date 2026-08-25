"""One execution-trace observation, in its own process. Model-free, no provider.

A separate program rather than a function, for the same reason
`observe_signature.py` is: the process boundary *is* the measurement. Python's
tracing state, import caches and module-level side effects all live in the
interpreter, so three observations inside one process would agree with each
other for reasons that have nothing to do with the repository.

Two things are recorded per run and they must not be conflated:

    executed   every (file, line) the interpreter actually ran, filtered to the
               repository tree — not imported, not discovered, not present on
               sys.path. Execution.

    identity   the failing test's exception type and message, extracted the same
               way in traced and untraced mode so observer invariance is a
               like-for-like comparison rather than two different judges.

`sys.settrace` is deliberate: it is deterministic, it is in the standard library,
and it observes execution rather than inferring it. Nothing here interprets a
stack semantically, and no model is involved.

Usage:
    python trace_observe.py <tree> <node> <traced|untraced> <out.json>
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import threading
from types import FrameType


def implementation_hash() -> str:
    return hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()


class Recorder:
    """Records executed lines for files under one tree. Nothing else."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = str(root.resolve())
        self.executed: dict[str, set[int]] = {}

    def local(self, frame: FrameType, event: str, arg: object):
        if event == "line":
            self.executed.setdefault(frame.f_code.co_filename, set()).add(frame.f_lineno)
        return self.local

    def dispatch(self, frame: FrameType, event: str, arg: object):
        # Filter once per call, not per line: the per-line path must stay cheap
        # or the trace changes the timing enough to matter.
        if event != "call":
            return None
        filename = frame.f_code.co_filename
        if not filename.startswith(self.root):
            return None
        self.executed.setdefault(filename, set()).add(frame.f_lineno)
        return self.local


class IdentityPlugin:
    """The failing test's exception identity, from pytest's own report."""

    def __init__(self) -> None:
        self.identity: dict[str, str] = {}

    def pytest_runtest_logreport(self, report) -> None:  # noqa: ANN001 - pytest hook
        if report.when != "call" or not report.failed:
            return
        crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
        raw = getattr(crash, "message", "") or ""
        exception_type, _, message = raw.partition(":")
        self.identity = {
            "exception_type": exception_type.strip(),
            "message": message.strip(),
        }


def main() -> int:
    if len(sys.argv) != 5:
        print(json.dumps({"error": "usage: trace_observe.py <tree> <node> <traced|untraced> <out>"}))
        return 2
    tree = pathlib.Path(sys.argv[1]).resolve()
    node, mode, out = sys.argv[2], sys.argv[3], pathlib.Path(sys.argv[4])

    layout = tree / "src"
    sys.path.insert(0, str(layout if layout.is_dir() else tree))
    sys.path.insert(0, str(tree))

    import pytest

    plugin = IdentityPlugin()
    recorder = Recorder(tree) if mode == "traced" else None

    if recorder is not None:
        threading.settrace(recorder.dispatch)
        sys.settrace(recorder.dispatch)
    try:
        exit_code = pytest.main(
            ["-q", "-p", "no:cacheprovider", "-p", "no:randomly", "--no-header", node],
            plugins=[plugin],
        )
    finally:
        if recorder is not None:
            sys.settrace(None)
            threading.settrace(None)  # type: ignore[arg-type]

    executed = {}
    if recorder is not None:
        root = str(tree)
        for filename, lines in recorder.executed.items():
            try:
                relative = str(pathlib.Path(filename).resolve().relative_to(tree)).replace("\\", "/")
            except ValueError:
                continue
            if relative.startswith("src/"):
                relative_alt = relative[4:]
            else:
                relative_alt = relative
            executed.setdefault(relative, sorted(lines))
            if relative_alt != relative:
                executed.setdefault(relative_alt, sorted(lines))
        del root

    payload = {
        "mode": mode,
        "node": node,
        "pytest_exit_code": int(exit_code),
        "identity": plugin.identity,
        "executed": executed,
        "trace_implementation_hash": implementation_hash(),
        "python": sys.version.split()[0],
        "mechanism": "stdlib sys.settrace + threading.settrace, line events, repository-tree filtered",
    }
    out.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
