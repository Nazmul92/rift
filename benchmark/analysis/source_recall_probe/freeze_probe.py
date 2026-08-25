"""POST-HOC SOURCE-RECALL / PROPOSAL-FORMAT PROBE
NOT BM-08 · NOT BM-09 · NOT OFFICIAL BENCHMARK EVIDENCE · EXPLORATORY — NOT CAUSAL

Select six cases, assign the counterbalanced order, and freeze the manifest.

Everything decidable in advance is decided here, before a single provider
response exists: which cases, which order each one is asked in, what the prompts
say, what the budget authority is. After the first response the manifest is
immutable — that is what stops a disappointing result from being reinterpreted
into a different experiment.

Selection is deterministic from a SHA-256 over the case identity, the same
device BM-08's corpus ordering uses. It cannot be steered toward cases that
would flatter either condition, because the ordering is fixed before any of them
is run and there is no outcome to steer by.

No provider is called by this module.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "benchmark" / "bm08"))
sys.path.insert(0, str(ROOT / "src"))

import bm08_driver as driver  # noqa: E402
import probe_context  # noqa: E402
import search_replace  # noqa: E402
import transactions  # noqa: E402

from riftagent.sandbox import tree_hash  # noqa: E402

BM08 = ROOT / "benchmark" / "bm08"
OUT = HERE / "probe-manifest.json"
WORK = pathlib.Path("/tmp/probe-freeze")
REPO_ROOTS = (pathlib.Path("/repos"), pathlib.Path("/repos-v3"), pathlib.Path("/repos-v5"))

MODEL = "claude-sonnet-4-6"
PRICING = {"input_per_mtok": 3.0, "output_per_mtok": 15.0, "currency": "USD"}

# One semantic proposal per condition, plus at most one schema repair — the same
# repair authority BM-08 granted, so S is not handed extra attempts that U never
# had. No semantic retries in either condition.
MAX_REQUESTS_PER_CONDITION = 2
MAX_OUTPUT_TOKENS = 4000

WANT_CONTEXT = 4  # context_mismatch
WANT_PATH = 2  # file_not_found_or_wrong_path

U_PROMPT = """\
You are fixing one failing test in a Python repository.

Failing test:
{node_id}

Observed failure:
{signature}

Source context (exactly the files and line ranges available to the original
attempt; nothing else in the repository is shown):

{context}

Propose a fix as a single unified diff against this exact source.

Rules:
- Output ONLY the unified diff. No prose, no fences, no commentary.
- Use `--- a/<path>` and `+++ b/<path>` headers with paths relative to the
  repository root.
- Do not modify test files.
- Do not create new files.
- The diff must apply cleanly to the source shown above.
"""

S_PROMPT = """\
You are fixing one failing test in a Python repository.

Failing test:
{node_id}

Observed failure:
{signature}

Source context (exactly the files and line ranges available to the original
attempt; nothing else in the repository is shown):

{context}

Propose a fix as exact search/replace edits against this exact source.

Output ONLY a JSON object of this shape:

{{"edits": [{{"path": "pkg/module.py", "search": "<exact source text>", "replace": "<replacement text>"}}]}}

Rules:
- `search` must be text copied verbatim from the source shown above, including
  indentation and line breaks.
- Each `search` must occur EXACTLY ONCE in the original file.
- Every `search` is matched against the ORIGINAL file, not against the result of
  any other edit in this list.
- Search regions must not overlap.
- `path` must be a file that already exists. Do not create new files.
- Do not modify test files.
- No line numbers, no wildcards, no regular expressions, no fuzzy matching.
"""


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(case_id: str, arm: str) -> str:
    return hashlib.sha256(f"source-recall-probe:{case_id}:{arm}".encode()).hexdigest()


def repo_for(name: str) -> pathlib.Path:
    found = [root / name for root in REPO_ROOTS if (root / name / ".git").is_dir()]
    if len(found) != 1:
        raise RuntimeError(f"BLOCKED_BASELINE_IDENTITY: {name} resolved to {len(found)} roots")
    return found[0]


def select() -> list[dict]:
    """Six canonical failures: four context mismatches, two wrong/missing paths."""
    replay = json.loads((BM08 / "canonicalization-replay.json").read_text(encoding="utf-8"))["records"]
    failures = [r for r in replay if not r["canonical_apply_ok"]]

    def pick(failure_class: str, want: int, taken: set[str]) -> list[dict]:
        pool = sorted(
            (r for r in failures if r["failure_class_canonical"] == failure_class),
            key=lambda r: digest(r["case_id"], r["arm"]),
        )
        chosen: list[dict] = []
        for record in pool:
            if record["case_id"] in taken:
                continue  # one arm per case; two arms of one bug is one bug
            taken.add(record["case_id"])
            chosen.append(record)
            if len(chosen) == want:
                break
        return chosen

    taken: set[str] = set()
    picked = pick("context_mismatch", WANT_CONTEXT, taken) + pick("file_not_found_or_wrong_path", WANT_PATH, taken)
    if len(picked) != WANT_CONTEXT + WANT_PATH:
        raise RuntimeError(f"BLOCKED: selection produced {len(picked)} cases, expected 6")
    return sorted(picked, key=lambda r: digest(r["case_id"], r["arm"]))


def main() -> int:
    print(search_replace.BANNER)
    if OUT.is_file():
        print(f"BLOCKED: {OUT} already exists; a frozen manifest is immutable")
        return 2

    manifest_cases = json.loads((BM08 / "manifest-executable-v5.json").read_text(encoding="utf-8"))
    by_id = {c["case_id"]: c for c in manifest_cases["cases"]}
    selected = select()

    entries = []
    for position, record in enumerate(selected):
        case = by_id[record["case_id"]]
        tree = WORK / record["case_id"]
        driver.materialise_baseline(case, repo_for(case["repository"]).parent, tree)
        observed = tree_hash(tree)
        if observed != case["baseline_tree_hash"]:
            print(f"BLOCKED_BASELINE_IDENTITY: {case['case_id']} {observed[:12]} != {case['baseline_tree_hash'][:12]}")
            return 3
        evidence = BM08 / "results-evidence" / record["case_id"] / record["arm"]
        try:
            context = probe_context.build(tree, evidence)
        except probe_context.ContextIdentityError as exc:
            print(f"BLOCKED_CONTEXT_IDENTITY: {exc}")
            return 4

        # Counterbalanced strictly by position in the frozen order: three U->S,
        # three S->U, decided before any response exists.
        order = ["U", "S"] if position % 2 == 0 else ["S", "U"]
        entries.append(
            {
                "case_id": record["case_id"],
                "repository": record["repository"],
                "bm08_arm": record["arm"],
                "failure_class": record["failure_class_canonical"],
                "baseline_tree_hash": case["baseline_tree_hash"],
                "context_hash": context["context_hash"],
                "context_files": context["files"],
                "context_chars": context["rendered_chars"],
                "node_id": context["node_id"],
                "expected_signature": context["expected_signature"],
                "order": order,
                "order_label": "->".join(order),
                "selection_digest": digest(record["case_id"], record["arm"]),
            }
        )

    largest = max(e["context_chars"] for e in entries)
    # Derived, not guessed: the biggest reconstructed context plus the prompt
    # scaffold, converted at a deliberately pessimistic 3 characters per token
    # and rounded up. Reducing this to fit a round number would be cutting
    # request authority to fit a budget, which the protocol forbids.
    max_input_tokens = int(((largest + 4000) / 3) * 1.5 // 1000 + 1) * 1000
    worst_per_request = (
        max_input_tokens / 1e6 * PRICING["input_per_mtok"] + MAX_OUTPUT_TOKENS / 1e6 * PRICING["output_per_mtok"]
    )
    worst_total = worst_per_request * len(entries) * 2 * MAX_REQUESTS_PER_CONDITION
    ceiling = float(int(worst_total + 1.0))

    manifest = {
        "label": search_replace.BANNER,
        "probe_id": "source-recall-probe-1",
        "not_official": True,
        "selection_rule": (
            "from the 29 BM-08 canonical-stage failures in canonicalization-replay.json, "
            "order every failure by sha256('source-recall-probe:<case_id>:<arm>'), then take the "
            "first 4 with failure_class_canonical == context_mismatch and the first 2 with "
            "file_not_found_or_wrong_path, at most one arm per case_id; frozen before any provider response"
        ),
        "counterbalance_rule": "even position in the frozen order -> U then S; odd position -> S then U",
        "model": {"requested_model_id": MODEL, "required_reported_model_identity": "must equal requested"},
        "pricing": PRICING,
        "budget": {
            "total_usd_ceiling": ceiling,
            "max_input_tokens": max_input_tokens,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "max_requests_per_condition": MAX_REQUESTS_PER_CONDITION,
            "schema_repair_policy": "at most one schema repair per condition; no semantic retries",
            "worst_case_usd": round(worst_total, 4),
            "derivation": (
                f"{len(entries)} cases x 2 conditions x {MAX_REQUESTS_PER_CONDITION} requests x "
                f"${worst_per_request:.4f} = ${worst_total:.4f}"
            ),
        },
        "expected_result_count": len(entries) * 2,
        "u_prompt_hash": prompt_hash(U_PROMPT),
        "s_prompt_hash": prompt_hash(S_PROMPT),
        "u_prompt_template": U_PROMPT,
        "s_prompt_template": S_PROMPT,
        "search_replace_executor_hash": search_replace.executor_hash(),
        "transaction_implementation_hash": transactions.implementation_hash(),
        "canonicalizer_identity": {
            "runtime_hash": driver.observed_runtime_hash(),
            "records_py_sha256": hashlib.sha256((ROOT / "src" / "riftagent" / "records.py").read_bytes()).hexdigest(),
        },
        "oracle_identity": {"bm08_oracle_used": False, "note": "target/truth evaluation is a secondary endpoint"},
        "bm08_corpus_manifest_hash": manifest_cases["corpus_manifest_hash"],
        "cases": entries,
    }
    body = {k: v for k, v in manifest.items() if k != "probe_manifest_hash"}
    manifest["probe_manifest_hash"] = hashlib.sha256(
        (json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")
    ).hexdigest()
    OUT.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"\nselection rule : {manifest['selection_rule'][:80]}…")
    for entry in entries:
        print(
            f"  {entry['case_id']:26} {entry['repository']:18} {entry['failure_class']:28} "
            f"{entry['order_label']:6} ctx={entry['context_chars']:6} {entry['context_hash'][:12]}"
        )
    print(f"\nmax_input_tokens      : {max_input_tokens}  (largest context {largest} chars)")
    print(f"worst case            : ${worst_total:.4f}   ceiling ${ceiling:.2f}")
    print(f"expected results      : {manifest['expected_result_count']}")
    print(f"u_prompt_hash         : {manifest['u_prompt_hash']}")
    print(f"s_prompt_hash         : {manifest['s_prompt_hash']}")
    print(f"executor_hash         : {manifest['search_replace_executor_hash']}")
    print(f"transaction_hash      : {manifest['transaction_implementation_hash']}")
    print(f"probe_manifest_hash   : {manifest['probe_manifest_hash']}")
    print(f"\nwritten: {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
