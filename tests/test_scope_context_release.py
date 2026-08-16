"""Scope-keyed spend caps, bounded/redacted context, and the release archive.

Three obligations meet here because each is about a boundary that only fails
when something crosses it: two processes crossing one authorization, repository
bytes crossing to a provider, and local state crossing into a handoff archive.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from riftagent.app import MAX_EXCERPT_CHARS, WINDOW_RADIUS, excerpt, redact, select_context
from riftagent.records import BudgetRefused, ModelUsage, Pricing, SpendLedger

PRICING = Pricing(input_per_mtok=1.0, output_per_mtok=5.0, provider="test", model="m")


# --------------------------------------------------------------------------
# 7e — the cap is cumulative per authorization scope
# --------------------------------------------------------------------------


def test_two_sequential_tasks_under_one_scope_cannot_jointly_exceed_it(tmp_path: Path):
    """The failure this prevents: each task is individually affordable, and
    together they are not."""
    path = tmp_path / ".rift" / "spend.jsonl"
    limit = 0.06
    charged = 0.0
    for task in ("task-a", "task-b", "task-c"):
        led = SpendLedger(path, scope="run-1", limit_usd=limit, pricing=PRICING)
        try:
            led.reserve(f"req-{task}", task, 1, 20_000, 2_000)
        except BudgetRefused:
            continue
        record = led.settle(f"req-{task}", task, 1, ModelUsage(input_tokens=20_000, output_tokens=2_000))
        charged += record["charged_usd"]

    final = SpendLedger(path, scope="run-1", limit_usd=limit, pricing=PRICING)
    assert final.committed_usd() <= limit + 1e-9, final.committed_usd()
    assert charged > 0, "nothing was spent, so the cap was not actually exercised"
    assert any(e["kind"] == "refused" for e in final.events()), "the third task was never refused"


CHILD = """
import sys
from riftagent.records import BudgetRefused, ModelUsage, Pricing, SpendLedger

path, scope, limit, task, n = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4], int(sys.argv[5])
pricing = Pricing(input_per_mtok=1.0, output_per_mtok=5.0, provider="t", model="m")
led = SpendLedger(path, scope=scope, limit_usd=limit, pricing=pricing)
sent = 0
for i in range(n):
    rid = f"{task}-{i}"
    try:
        led.reserve(rid, task, i, 20_000, 2_000)
    except BudgetRefused:
        continue
    led.settle(rid, task, i, ModelUsage(input_tokens=20_000, output_tokens=2_000))
    sent += 1
print(sent)
"""


@pytest.mark.slow
def test_two_concurrent_processes_under_one_scope_cannot_jointly_exceed_it(tmp_path: Path):
    """Separate OS processes, one authorization, no coordination between them.
    Only the file lock stands between them and a jointly overspent scope: with
    an unlocked read-decide-append, both observe the same balance and both
    proceed."""
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    path = tmp_path / ".rift" / "spend.jsonl"
    limit = 0.20

    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(path), "run-9", str(limit), f"t{i}", "8"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(2)
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=180)
        assert proc.returncode == 0, err

    led = SpendLedger(path, scope="run-9", limit_usd=limit, pricing=PRICING)
    assert led.committed_usd() <= limit + 1e-9, f"scope overspent: {led.committed_usd()}"
    settled = [e for e in led.events() if e["kind"] == "settled"]
    assert settled, "no request completed, so the cap was not exercised"
    # Every settlement is unique: no request was charged twice under contention.
    ids = [e["request_id"] for e in settled]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------
# 8 — bounded windows and redaction
# --------------------------------------------------------------------------


def test_excerpt_sends_a_window_not_the_file():
    source = "\n".join(f"line {i}" for i in range(1, 501))
    text, ranges = excerpt(source, cited=[250], wanted=set())
    assert ranges == [(250 - WINDOW_RADIUS, 250 + WINDOW_RADIUS)]
    assert "line 250" in text
    assert "line 1\n" not in text and "line 500" not in text
    assert len(text) < len(source) / 5


def test_windows_merge_and_elision_is_marked():
    source = "\n".join(f"line {i}" for i in range(1, 201))
    text, ranges = excerpt(source, cited=[50, 55, 150], wanted=set())
    assert len(ranges) == 2, ranges  # 50 and 55 overlap
    assert "only the ranges above were sent" in text


def test_a_definition_anchors_a_window_with_no_traceback():
    """The wrong-value case: nothing raised, so no frame cites the function."""
    source = "\n".join(["# header"] * 100 + ["def target(a):", "    return a - 1"] + ["# tail"] * 100)
    text, ranges = excerpt(source, cited=[], wanted={"target"})
    assert "def target(a):" in text
    assert ranges and ranges[0][0] > 1


def test_the_excerpt_is_bounded():
    source = "\n".join(f"x = {i}" for i in range(1, 20_000))
    text, _ = excerpt(source, cited=[100, 2000, 4000, 6000, 8000, 10_000, 12_000], wanted=set())
    assert len(text) <= MAX_EXCERPT_CHARS + 200


@pytest.mark.parametrize(
    ("secret", "kind"),
    [
        ("sk-abcdefghijklmnopqrstuvwxyz012345", "provider_key"),
        ("AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github_token"),
        ("Bearer abcdefghijklmnopqrstuvwxyz0123", "bearer_token"),
        ('API_KEY = "hunter2hunter2"', "assigned_secret"),
        ("postgres://user:s3cretpw@db/app", "url_credentials"),
    ],
)
def test_credential_shapes_are_redacted(secret: str, kind: str):
    text, counts = redact(f"before\n{secret}\nafter")
    assert secret not in text, f"{kind} survived redaction"
    assert counts.get(kind, 0) >= 1, counts
    assert "before" in text and "after" in text, "redaction removed ordinary source"


def test_a_private_key_block_is_redacted_whole():
    block = "-----BEGIN RSA PRIVATE KEY-----\nSENTINEL-fake-body\n-----END RSA PRIVATE KEY-----"
    text, counts = redact(block)
    assert "SENTINEL-fake-body" not in text
    assert counts["private_key_block"] == 1


def test_ordinary_source_is_not_redacted():
    """A broad matcher would blank out the lines the model must read."""
    source = "def add(a, b):\n    return a + b\n\nMAX_RETRIES = 3\nname = 'widget'\n"
    text, counts = redact(source)
    assert text == source
    assert counts == {}


BROKEN = {
    "src/app/__init__.py": "",
    "src/app/calc.py": "\n".join(
        ["# padding"] * 60 + ['TOKEN = "sk-abcdefghijklmnopqrstuvwxyz012345"', "", "def add(a, b):", "    return a - b"]
    ),
    "tests/test_calc.py": "from app.calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n",
}


def build_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8", newline="\n")
    return root


def test_selected_context_is_windowed_and_redacted(tmp_path: Path):
    repo = build_repo(tmp_path / "ctx", BROKEN)
    chosen, manifest = select_context(repo, "", "tests/test_calc.py::test_add", ())
    body = "\n".join(t for _, t in chosen)

    assert "def add(a, b):" in body, "the definition under test was not sent"
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in body, "a credential was sent to the provider"
    assert "# padding" not in body or body.count("# padding") < 40, "the whole file was sent"


def test_the_manifest_records_ranges_and_counts_but_never_values(tmp_path: Path):
    repo = build_repo(tmp_path / "manifest", BROKEN)
    _, manifest = select_context(repo, "", "tests/test_calc.py::test_add", ())

    assert manifest["line_ranges"], "no byte/line ranges were recorded"
    for _rel, ranges in manifest["line_ranges"].items():
        assert all(len(r) == 2 and r[0] <= r[1] for r in ranges), ranges
    serialised = json.dumps(manifest)
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in serialised, "a redacted value reached the manifest"
    assert "whole files" not in manifest["selection"] or "no whole files" in manifest["selection"]


# --------------------------------------------------------------------------
# 1 — the handoff archive excludes local state
# --------------------------------------------------------------------------

FORBIDDEN_IN_ARCHIVE = (
    ".env",
    ".rift/",
    "build/",
    "__pycache__/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".pytest_cache/",
    ".egg-info",
    "benchmark/work/",
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def archive_members() -> list[str]:
    """What a release would contain, from the same rule the release script uses."""
    from riftagent.records import archive_manifest

    return archive_manifest(REPO_ROOT)


def test_the_archive_excludes_every_forbidden_path():
    """Structural, not procedural: a release containing local state fails here
    rather than being caught by whoever unpacks it."""
    members = archive_members()
    assert members, "the archive manifest is empty"
    for member in members:
        for forbidden in FORBIDDEN_IN_ARCHIVE:
            assert forbidden not in member, f"{member} matches forbidden pattern {forbidden}"


def test_the_archive_contains_the_things_a_handoff_needs():
    members = set(archive_members())
    for required in (
        "pyproject.toml",
        "requirements-dev.txt",
        "src/riftagent/records.py",
        "src/riftagent/kernel.py",
        "tests/test_v01_structure.py",
        "IMPLEMENTATION_STATUS.md",
    ):
        assert required in members, f"{required} is missing from the handoff archive"


def test_no_archived_file_contains_a_credential_shape():
    """A second, content-level check: exclusion by path is necessary and not
    sufficient, because a secret can be committed into an ordinary file.

    `tests/` is scanned separately below and `src/riftagent/app.py` holds the
    pattern table itself. Both legitimately contain credential *shapes*, and
    exempting them is the difference between a check that stays enforced and
    one that gets deleted the first time it fires on a fixture.
    """
    offenders: list[str] = []
    for member in archive_members():
        path = REPO_ROOT / member
        if member.startswith("tests/") or member.endswith("src/riftagent/app.py"):
            continue
        if not path.is_file() or path.suffix not in (".py", ".toml", ".txt", ".ini", ".cfg", ".md"):
            continue
        _, counts = redact(path.read_text(encoding="utf-8", errors="replace"))
        if counts:
            offenders.append(f"{member}: {sorted(counts)}")
    assert not offenders, offenders


def test_credential_shapes_in_tests_are_obvious_sentinels():
    """Tests must be free to carry adversarial inputs, but a real key must never
    hide among them. Every credential shape in `tests/` has to announce itself
    as synthetic."""
    SENTINEL_MARKERS = ("SENTINEL", "EXAMPLE", "hunter2", "abcdef", "s3cretpw", "test", "fake")
    offenders: list[str] = []
    for member in archive_members():
        if not member.startswith("tests/"):
            continue
        for line in (REPO_ROOT / member).read_text(encoding="utf-8", errors="replace").splitlines():
            _, counts = redact(line)
            if counts and not any(marker in line for marker in SENTINEL_MARKERS):
                offenders.append(f"{member}: {line.strip()[:80]}")
    assert not offenders, offenders
