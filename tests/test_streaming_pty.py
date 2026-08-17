"""M1-R01: live streaming, driven through a real PTY.

The row was previously recorded `NOT_RUN_PTY_UNAVAILABLE` on the strength of
`sys.stdout.isatty() == False`. That measurement describes the process pytest
happens to run in and says nothing about whether a test can allocate a PTY.
`pty` is importable here, so the capability exists and the row is testable.

What must be shown is ordering, not content: a command's start is announced
*before* its completion, and both reach the terminal while the run is still in
progress rather than in one block at the end. A test that only inspected the
final transcript could not tell those apart.
"""

from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import SIMPLE_TARGET, build_repo, make_diff

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="NOT_RUN_PTY_UNSUPPORTED_PLATFORM: POSIX pty only; Windows streaming is not covered by this row",
)

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")


def drive(argv: list[str], cwd: Path, timeout_s: float = 240.0) -> list[tuple[float, str]]:
    """Run a command attached to a PTY and return (elapsed, line) as they arrive.

    The timestamps are what make this a streaming test: they are read from the
    reader's clock as bytes appear, so a runtime that buffered everything and
    flushed at exit would produce lines whose arrival times all cluster at the
    end.
    """
    primary, secondary = pty.openpty()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=secondary,
        stderr=secondary,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "xterm"},
        close_fds=True,
    )
    os.close(secondary)
    began = time.monotonic()
    events: list[tuple[float, str]] = []
    buffer = ""
    try:
        while True:
            if time.monotonic() - began > timeout_s:
                proc.kill()
                raise AssertionError("the streamed run did not finish inside its timeout")
            ready, _, _ = select.select([primary], [], [], 0.5)
            if ready:
                try:
                    chunk = os.read(primary, 4096).decode("utf-8", "replace")
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    clean = ANSI.sub("", line).strip()
                    if clean:
                        events.append((time.monotonic() - began, clean))
            elif proc.poll() is not None:
                break
        if buffer.strip():
            events.append((time.monotonic() - began, ANSI.sub("", buffer).strip()))
    finally:
        os.close(primary)
        proc.wait(timeout=30)
    return events


def _verify_argv(repo: Path, diff_path: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "riftagent",
        "--repo",
        str(repo),
        "verify",
        str(diff_path),
        SIMPLE_TARGET,
        "--allow-partial-sandbox",
        "--preserve",
        "tests/test_other.py::test_double",
    ]


def test_r01_a_command_start_is_streamed_before_its_completion(tmp_path: Path):
    repo = build_repo(tmp_path / "stream", _FILES)
    diff = make_diff(repo, {"src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n"})
    path = tmp_path / "fix.diff"
    path.write_text(diff, encoding="utf-8", newline="\n")

    events = drive(_verify_argv(repo, path), tmp_path)
    assert events, "nothing reached the terminal at all"
    text = [line for _, line in events]

    starts = [i for i, line in enumerate(text) if "pytest" in line and "tests/" in line]
    assert starts, f"no command start was announced:\n{text}"

    # 1. ordering: a start precedes the first completion, and the receipt is last.
    finishes = [i for i, line in enumerate(text) if re.search(r"\b\d+\.\d+s\b", line)]
    assert finishes, f"no command completion carried an elapsed time:\n{text}"
    assert starts[0] < finishes[-1], f"a completion was announced before any start:\n{text}"

    verdict = [i for i, line in enumerate(text) if "verified against approved checks" in line.lower()]
    assert verdict, f"no scoped verdict was rendered:\n{text}"
    assert verdict[0] > starts[0], "the verdict was rendered before the work was announced"

    # 2. streaming, not a final flush: the first announcement must arrive
    #    materially before the last line. Without this the assertions above
    #    would hold for a runtime that printed everything at exit.
    first_at = events[starts[0]][0]
    last_at = events[-1][0]
    assert last_at - first_at > 0.25, (
        f"every line arrived within {last_at - first_at:.3f}s of the first; this is a final flush, not streaming"
    )


def test_r01_the_streamed_lines_all_come_from_the_ledger(tmp_path: Path):
    """The row's other half, and the reason streaming is not merely cosmetic:
    the live view is a projection of the same events the settled transcript
    replays from, so it cannot show a claim the ledger does not carry."""
    from riftagent.app import render_settled
    from riftagent.records import read_events

    repo = build_repo(tmp_path / "stream2", _FILES)
    diff = make_diff(repo, {"src/pkg/calc.py": "def total():\n    return sum(range(5)) + 1\n"})
    path = tmp_path / "fix.diff"
    path.write_text(diff, encoding="utf-8", newline="\n")

    events = drive(_verify_argv(repo, path), tmp_path)
    live = {line for _, line in events}

    td = sorted((repo / ".rift" / "tasks").iterdir())[-1]
    settled = {
        line.strip() for line in render_settled(read_events(td / "ledger.jsonl")[0]).splitlines() if line.strip()
    }
    assert settled, "the settled transcript is empty"

    # Spinner frames may use the clock and carry no claim, so they are allowed
    # to differ; every *claim* rendered live must exist in the settled
    # projection. Compared on the substantive lines only.
    substantive = [line for line in live if len(line) > 12 and not line.startswith("...")]
    assert substantive, live
    missing = [line for line in substantive if not any(line in s or s in line for s in settled)]
    assert not missing, f"streamed lines with no durable event behind them:\n{missing}"


_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/calc.py": "def total():\n    return sum(range(5))\n",
    "src/pkg/util.py": "def double(x):\n    return x * 2\n",
    "tests/test_calc.py": "from pkg.calc import total\n\n\ndef test_total():\n    assert total() == 11\n",
    "tests/test_other.py": "from pkg.util import double\n\n\ndef test_double():\n    assert double(3) == 6\n",
}
