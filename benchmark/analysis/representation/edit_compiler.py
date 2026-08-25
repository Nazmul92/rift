"""REPRESENTATION EXPERIMENT — deterministic exact-edit → Git-diff compiler.

PREPARATION ONLY. No provider call is made from this module.

The experimental claim is that patch *metadata* — line numbers, hunk counts,
context lines — is deterministic bookkeeping the model should never have been
asked to produce. So the model declares exact edits, and the diff is compiled:

    before tree  +  declared exact edits  ->  after tree  ->  git diff  ->  compiled.diff

**Git generates the diff, not arithmetic written here.** That is deliberate. A
hand-rolled hunk engine would be a second implementation of the very thing the
experiment blames for the failures, and its bugs would be indistinguishable from
the effect under test. `git diff --no-index` already produces a correct unified
diff from two trees, deterministically, and it is the same program that will
later be asked to apply it.

The authority contract below is narrow on purpose. Every listed permission is
an exact-byte operation; every forbidden one is a guess. A compiler that quietly
resolved a near-miss would manufacture the result the experiment is trying to
measure.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

BANNER = "REPRESENTATION EXPERIMENT — DETERMINISTIC EXACT-EDIT COMPILER — PREPARATION, NO PROVIDER"

# Outcome classes. Conservative, mutually exclusive, no near-miss category.
PATH_NOT_FOUND = "PATH_NOT_FOUND"
SEARCH_NOT_FOUND = "SEARCH_NOT_FOUND"
SEARCH_AMBIGUOUS = "SEARCH_AMBIGUOUS"
SEARCH_OVERLAP = "SEARCH_OVERLAP"
SCHEMA_INVALID = "SCHEMA_INVALID"
UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
APPLIED = "APPLIED"

AUTHORITY_CONTRACT = {
    "version": 1,
    "permitted": [
        "exact path lookup",
        "exact byte lookup",
        "unique-match verification",
        "overlap detection",
        "atomic application",
        "before/after tree comparison",
        "deterministic Git diff production",
        "hashing and provenance",
    ],
    "forbidden": [
        "fuzzy matching",
        "whitespace normalization",
        "case folding",
        "regex interpretation",
        "nearest-match choice",
        "semantic relocation",
        "AST guessing",
        "did-you-mean suggestions",
        "model calls",
    ],
    "encoding": {
        "decode": "model strings arrive JSON-decoded as Unicode and are encoded to UTF-8 exactly",
        "normalization": "none — no whitespace, line-ending, casing or Unicode normalization before matching",
        "line_endings": (
            "LF and CRLF are distinct byte sequences and are never converted; a search containing "
            "LF will not match a CRLF file region"
        ),
        "final_newline": (
            "a file's trailing newline is part of its bytes; it is neither added nor removed unless "
            "the declared search or replace text contains it"
        ),
        "json_escaping": "standard JSON string escaping; the decoded value is authoritative",
        "binary": "binary files are unsupported; an edit naming one is UNSUPPORTED_OPERATION",
    },
    "new_files": "not supported; every path must already exist in the frozen baseline",
}

MAX_EDITS = 20
MAX_SEARCH_BYTES = 20000


def authority_contract_hash() -> str:
    return hashlib.sha256((json.dumps(AUTHORITY_CONTRACT, indent=1, sort_keys=True) + "\n").encode("utf-8")).hexdigest()


def compiler_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class EditReceipt:
    index: int
    path: str
    status: str
    search_hash: str = ""
    replace_hash: str = ""
    match_count: int = -1
    match_start_byte: int = -1
    match_end_byte: int = -1
    matched_bytes_hash: str = ""
    detail: str = ""


@dataclass
class Compilation:
    status: str
    ok: bool
    compiled_diff: str = ""
    compiled_diff_hash: str = ""
    before_tree_hash: str = ""
    after_tree_hash: str = ""
    receipts: list[EditReceipt] = field(default_factory=list)
    modified_paths: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "ok": self.ok,
            "compiled_diff_hash": self.compiled_diff_hash,
            "before_tree_hash": self.before_tree_hash,
            "after_tree_hash": self.after_tree_hash,
            "modified_paths": self.modified_paths,
            "detail": self.detail[:2000],
            "compiler_hash": compiler_hash(),
            "authority_contract_hash": authority_contract_hash(),
            "edits": [
                {
                    "index": r.index,
                    "path": r.path,
                    "status": r.status,
                    "search_hash": r.search_hash,
                    "replace_hash": r.replace_hash,
                    "match_count": r.match_count,
                    "match_start_byte": r.match_start_byte,
                    "match_end_byte": r.match_end_byte,
                    "matched_bytes_hash": r.matched_bytes_hash,
                    "detail": r.detail[:400],
                }
                for r in self.receipts
            ],
        }


def validate_schema(payload: object) -> tuple[list[dict], str]:
    """Structure only. Content correctness is decided against the baseline."""
    if not isinstance(payload, dict):
        return [], "response is not a JSON object"
    edits = payload.get("edits")
    if not isinstance(edits, list) or not edits:
        return [], "'edits' must be a non-empty list"
    if len(edits) > MAX_EDITS:
        return [], f"'edits' exceeds {MAX_EDITS}"
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return [], f"edits[{i}] is not an object"
        extra = set(edit) - {"path", "search", "replace"}
        if extra:
            return [], f"edits[{i}] has unsupported field(s): {sorted(extra)}"
        for key in ("path", "search", "replace"):
            if key not in edit:
                return [], f"edits[{i}] missing {key!r}"
            if not isinstance(edit[key], str):
                return [], f"edits[{i}].{key} is not a string"
        if not edit["path"].strip():
            return [], f"edits[{i}].path is empty"
        if not edit["search"]:
            return [], f"edits[{i}].search is empty"
        if len(edit["search"].encode("utf-8")) > MAX_SEARCH_BYTES:
            return [], f"edits[{i}].search exceeds {MAX_SEARCH_BYTES} bytes"
        if edit["path"].startswith("/") or ".." in Path(edit["path"]).parts:
            return [], f"edits[{i}].path escapes the repository"
    return edits, ""


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def git_diff(before: Path, after: Path) -> str:
    """Git's own deterministic diff between two trees. No arithmetic here."""
    proc = subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "diff.noprefix=false",
            "diff",
            "--no-index",
            "--no-color",
            "--no-ext-diff",
            "--unified=3",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            str(before),
            str(after),
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    # `--no-index` exits 1 when the trees differ, which is the expected case.
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"git diff --no-index failed: {(proc.stderr or '').strip()[:300]}")
    body = proc.stdout
    # Rewrite the absolute working paths back to repository-relative ones so the
    # compiled diff applies to the repository, not to a temp directory.
    return body.replace(f"a{before}", "a").replace(f"b{after}", "b").replace(str(before), "").replace(str(after), "")


def compile_edits(baseline: Path, payload: object, work: Path) -> Compilation:
    """Validate the whole set against the untouched baseline, then compile.

    Nothing is written until every edit has resolved to exactly one span, so a
    partially applied edit set cannot exist and a replacement can never create
    the match a later edit depends on.
    """
    edits, error = validate_schema(payload)
    if error:
        return Compilation(SCHEMA_INVALID, False, detail=error)

    before = work / "before"
    after = work / "after"
    shutil.rmtree(before, ignore_errors=True)
    shutil.rmtree(after, ignore_errors=True)
    shutil.copytree(baseline, before, symlinks=True)
    shutil.copytree(baseline, after, symlinks=True)

    originals: dict[str, bytes] = {}
    receipts: list[EditReceipt] = []
    spans: dict[str, list[tuple[int, int, int]]] = {}
    failed = False

    for i, edit in enumerate(edits):
        path = edit["path"]
        search = edit["search"].encode("utf-8")
        replace = edit["replace"].encode("utf-8")
        receipt = EditReceipt(i, path, APPLIED, _sha(search), _sha(replace))
        target = before / path
        if not target.is_file():
            receipt.status = PATH_NOT_FOUND
            receipt.detail = "no such file in the frozen baseline"
            receipts.append(receipt)
            failed = True
            continue
        if path not in originals:
            originals[path] = target.read_bytes()
        source = originals[path]
        if _looks_binary(source):
            receipt.status = UNSUPPORTED_OPERATION
            receipt.detail = "binary files are unsupported"
            receipts.append(receipt)
            failed = True
            continue

        count = source.count(search)
        receipt.match_count = count
        if count == 0:
            receipt.status = SEARCH_NOT_FOUND
            receipt.detail = "search bytes are not present in the baseline"
            receipts.append(receipt)
            failed = True
            continue
        if count > 1:
            receipt.status = SEARCH_AMBIGUOUS
            receipt.detail = f"search bytes occur {count} times"
            receipts.append(receipt)
            failed = True
            continue

        start = source.index(search)
        receipt.match_start_byte = start
        receipt.match_end_byte = start + len(search)
        receipt.matched_bytes_hash = _sha(source[start : start + len(search)])
        spans.setdefault(path, []).append((start, start + len(search), i))
        receipts.append(receipt)

    for path, path_spans in spans.items():
        ordered = sorted(path_spans)
        for a, b in zip(ordered, ordered[1:], strict=False):
            if a[1] > b[0]:
                for receipt in receipts:
                    if receipt.path == path and receipt.status == APPLIED:
                        receipt.status = SEARCH_OVERLAP
                        receipt.detail = "match regions overlap within one file"
                failed = True
                break

    if failed:
        first = next((r for r in receipts if r.status != APPLIED), None)
        shutil.rmtree(before, ignore_errors=True)
        shutil.rmtree(after, ignore_errors=True)
        return Compilation(
            first.status if first else SCHEMA_INVALID,
            False,
            receipts=receipts,
            detail=first.detail if first else "validation failed",
        )

    replacements = {
        edit_index: edits[edit_index]["replace"].encode("utf-8")
        for _, _, edit_index in [s for spans_ in spans.values() for s in spans_]
    }
    modified: list[str] = []
    for path, path_spans in spans.items():
        data = originals[path]
        for start, end, index in sorted(path_spans, reverse=True):
            data = data[:start] + replacements[index] + data[end:]
        (after / path).write_bytes(data)
        modified.append(path)

    compiled = git_diff(before, after)
    result = Compilation(
        APPLIED,
        True,
        compiled_diff=compiled,
        compiled_diff_hash=_sha(compiled.encode("utf-8")),
        receipts=receipts,
        modified_paths=sorted(modified),
    )
    return result


def verify_round_trip(baseline: Path, compilation: Compilation, work: Path) -> tuple[bool, str]:
    """Applying the compiled diff to the baseline must reproduce the after tree.

    Fail closed. A compiled diff that does not reconstruct exactly what the
    declared edits produced is not evidence about representation; it is a
    compiler defect wearing the experiment's clothes.
    """
    if not compilation.ok:
        return False, "no compilation to verify"
    check = work / "roundtrip"
    shutil.rmtree(check, ignore_errors=True)
    shutil.copytree(baseline, check, symlinks=True)
    patch = work / "compiled.diff"
    patch.write_text(compilation.compiled_diff, encoding="utf-8")
    proc = subprocess.run(
        ["git", "apply", "--unsafe-paths", "-p1", str(patch)],
        cwd=str(check),
        capture_output=True,
        text=True,
        errors="replace",
    )
    if proc.returncode != 0:
        return False, f"compiled diff does not apply: {(proc.stderr or '').strip()[:300]}"
    expected = work / "after"
    for path in compilation.modified_paths:
        if (check / path).read_bytes() != (expected / path).read_bytes():
            return False, f"{path}: round-trip bytes differ from the declared after tree"
    return True, "compiled diff reproduces the declared after tree exactly"


def bytes_changed_only_where_declared(work: Path, compilation: Compilation) -> tuple[bool, str]:
    """No byte outside a declared operation moved."""
    before, after = work / "before", work / "after"
    changed = []
    for path in before.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(before).as_posix()
        mirror = after / relative
        if not mirror.is_file():
            changed.append(relative)
        elif path.read_bytes() != mirror.read_bytes():
            changed.append(relative)
    unexpected = sorted(set(changed) - set(compilation.modified_paths))
    if unexpected:
        return False, f"files changed outside declared edits: {unexpected}"
    return True, "only declared paths differ between before and after"
