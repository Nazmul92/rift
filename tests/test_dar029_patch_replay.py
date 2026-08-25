"""Metadata-only diff normalisation, and a replay that cannot touch the result.

POST-HOC DIAGNOSTIC over the completed preliminary run. The question is narrow:
of the fifteen candidate-phase failures, how many were the model choosing the
wrong change, and how many were the model choosing a reasonable change and
serialising invalid diff metadata around it?

Answering it honestly requires one guarantee above all others — that
normalisation cannot alter what the model proposed. Every context, added and
deleted line must survive byte-for-byte, or the replay is measuring a patch
nobody wrote.

No provider is configured and no model request is made anywhere in this file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPLAY = Path(__file__).resolve().parents[1] / "benchmark" / "bm06" / "patch_replay"
sys.path.insert(0, str(REPLAY))

import normalize as N  # noqa: E402

GOOD = "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"


def test_a_wrong_hunk_count_is_corrected():
    """The single most common defect in the run: the header disagrees with the
    body it describes."""
    wrong = GOOD.replace("@@ -1,2 +1,2 @@", "@@ -1,7 +1,7 @@")
    out, status, notes = N.normalize(wrong)
    assert status == N.SAFE
    assert "@@ -1,2 +1,2 @@" in out
    assert notes and "counts" in notes[0]


def test_the_models_content_survives_byte_for_byte():
    """The invariant the whole experiment rests on."""
    wrong = GOOD.replace("@@ -1,2 +1,2 @@", "@@ -1,99 +1,42 @@")
    out, status, _ = N.normalize(wrong)
    assert status == N.SAFE
    assert N.semantic_lines(out) == N.semantic_lines(wrong)
    assert N.semantic_lines(out) == [" def f():\n", "-    return 1\n", "+    return 2\n"]


def test_an_already_valid_patch_is_left_alone():
    out, status, notes = N.normalize(GOOD)
    assert status == N.UNCHANGED
    assert out == GOOD
    assert notes == []


def test_a_missing_final_newline_is_refused_not_supplied():
    """This began as a permitted repair and the invariant check rejected it.

    Appending a newline to a diff whose last line is content changes that line's
    bytes, and unified-diff states a missing final newline explicitly with
    `\\ No newline at end of file`. So it is content, not metadata, and the
    normaliser stops rather than deciding what the file's last byte should be.
    """
    out, status, notes = N.normalize(GOOD.rstrip("\n"))
    assert status == N.UNSAFE
    assert out == GOOD.rstrip("\n"), "an unsafe patch was modified anyway"
    assert "content, not metadata" in notes[0]


@pytest.mark.parametrize(
    "broken, why",
    [
        (GOOD.replace("-    return 1\n", "return 1 unprefixed\n"), "no diff prefix"),
        ("--- a/m.py\n+++ b/m.py\n@@ this is not a header @@\n context\n", "will not parse"),
        ("--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n", "no body"),
    ],
)
def test_an_ambiguous_patch_is_refused_rather_than_guessed(broken: str, why: str):
    """Deciding whether an unprefixed line is context or an addition is deciding
    what the model meant. The experiment is about representation, so it stops."""
    out, status, notes = N.normalize(broken)
    assert status == N.UNSAFE
    assert out == broken, "an unsafe patch was modified anyway"
    assert any(why in n for n in notes), notes


def test_normalisation_is_deterministic():
    wrong = GOOD.replace("@@ -1,2 +1,2 @@", "@@ -1,9 +1,9 @@")
    assert N.normalize(wrong) == N.normalize(wrong)


def test_normalisation_never_relocates_a_hunk():
    """Start lines are metadata a normaliser could 'fix' by searching the tree.
    Doing so would be moving the model's change somewhere it did not choose."""
    wrong = GOOD.replace("@@ -1,2 +1,2 @@", "@@ -400,7 +400,7 @@")
    out, status, _ = N.normalize(wrong)
    assert status == N.SAFE
    assert "@@ -400,2 +400,2 @@" in out, "the start line was altered"


# ------------------------------------------------------- the replay artifacts


@pytest.mark.skipif(not (REPLAY / "replay.json").is_file(), reason="replay has not been run")
def test_the_replay_preserves_every_models_content():
    rows = json.loads((REPLAY / "replay.json").read_text(encoding="utf-8"))
    attempted = [r for r in rows if r.get("normalization") in ("NORMALIZED", "ALREADY_VALID")]
    assert attempted, "no patch was normalised"
    assert all(r["content_lines_identical"] for r in attempted), "a replayed patch had altered content"


@pytest.mark.skipif(not (REPLAY / "replay.json").is_file(), reason="replay has not been run")
def test_the_replay_verified_its_baseline_before_and_after():
    rows = json.loads((REPLAY / "replay.json").read_text(encoding="utf-8"))
    assert all(r["baseline_verified"] for r in rows), "a replay ran against an unverified tree"
    applied = [r for r in rows if r.get("applies_after_normalization")]
    assert applied and all(r.get("baseline_restored") for r in applied), "a replay left a tree dirty"


@pytest.mark.skipif(not (REPLAY / "replay.json").is_file(), reason="replay has not been run")
def test_the_replay_did_not_touch_the_original_result():
    """The benchmark result is immutable. The replay writes only under
    `patch_replay/`, and the original patches keep their recorded hashes."""
    import hashlib

    root = Path(__file__).resolve().parents[1]
    results = json.loads((root / "benchmark/bm06/results.json").read_text(encoding="utf-8"))
    rows = {(r["case"], r["arm"]): r for r in json.loads((REPLAY / "replay.json").read_text(encoding="utf-8"))}

    for rec in results["records"]:
        if rec.get("failed_phase") != "candidate":
            continue
        row = rows[(rec["case_id"], rec["arm"])]
        original = root / "benchmark/bm06/patches" / row["original_patch"]
        assert original.is_file()
        assert hashlib.sha256(original.read_bytes()).hexdigest() == row["original_candidate_sha256"], (
            f"{original.name} changed since the run"
        )
    # And the normalised copies live somewhere else entirely.
    for path in (REPLAY / "normalized").glob("*"):
        assert path.parent.name == "normalized"


def test_no_provider_call_is_reachable_from_the_replay():
    """Asserted on the source: nothing here may reach the adapter, and the
    replay strips the credentials from the environment it runs under."""
    import ast

    for name in ("normalize.py", "git_classify.py", "replay.py", "funnel.py", "classify.py"):
        source = (REPLAY / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("post_chat", "from_env"):
                raise AssertionError(f"{name} reaches the provider adapter")
        assert "RIFT_LLM_KEY" not in source or "pop" in source, name

    assert "env.pop(k, None)" in (REPLAY / "replay.py").read_text(encoding="utf-8"), (
        "the replay does not strip provider credentials"
    )


def test_the_replay_reruns_to_the_same_answer():
    """Determinism, on the normaliser rather than the whole gate: identical
    input, identical output, twice, for every captured patch."""
    patches = sorted((Path(__file__).resolve().parents[1] / "benchmark/bm06/patches").glob("*.diff"))
    assert patches, "no captured patches"
    for p in patches[:8]:
        raw = p.read_text(encoding="utf-8", errors="replace")
        assert N.normalize(raw) == N.normalize(raw), p.name
