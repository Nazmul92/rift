"""REPRESENTATION EXPERIMENT — freeze the manifest. PREPARATION ONLY, $0.00.

Everything decidable before a provider exists is decided here and hashed: the 24
cases and their identities, the 144 sample identities and their counterbalanced
order, both strict schemas, both prompts, the compiler and its authority
contract, the analysis plan, the coverage stratification, the derived worst-case
budget, and the execution-environment identity.

The budget is **derived and recorded, not authorized**. This module writes the
exact dollar requirement into the manifest so a reviewer can approve or refuse a
number rather than a hand-wave; it does not spend and cannot.
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
sys.path.insert(0, str(ROOT / "benchmark" / "analysis" / "source_recall_probe"))
sys.path.insert(0, str(ROOT / "src"))

import analysis_plan  # noqa: E402
import bm08_driver as driver  # noqa: E402
import bm08_oracle as oracle_module  # noqa: E402
import bm08_runner as bm08_runner  # noqa: E402
import cost as cost_module  # noqa: E402
import design  # noqa: E402
import edit_compiler  # noqa: E402
import execution_environment as environment  # noqa: E402
import rep_transactions as tx  # noqa: E402
import schemas  # noqa: E402

BM08 = ROOT / "benchmark" / "bm08"
AUDIT = ROOT / "benchmark" / "analysis" / "fix_region_audit" / "fix-region-coverage.json"
OUT = HERE / "representation-manifest.json"

MODEL = "claude-sonnet-4-6"
PRICING = {"input_per_mtok": 3.0, "output_per_mtok": 15.0, "currency": "USD"}
MAX_OUTPUT_TOKENS = 4000
SAMPLING = {
    "temperature": "provider default (not sent)",
    "top_p": "provider default (not sent)",
    "note": (
        "No sampling parameter is sent, matching BM-08's adapter. Repeats are "
        "stochastic by default sampling; the design measures that variation rather "
        "than suppressing it."
    ),
}

U_PROMPT = """\
You are fixing one failing test in a Python repository.

Failing test:
{node_id}

Observed failure:
{signature}

Source context (exactly the files and line ranges available to the original
attempt; nothing else in the repository is shown):

{context}

Return ONLY a JSON object of exactly this shape:

{{"diff": "<a single unified diff>"}}

Rules:
- `diff` holds one unified diff with `--- a/<path>` and `+++ b/<path>` headers,
  paths relative to the repository root.
- Do not modify test files. Do not create new files.
- The diff must apply cleanly to the source shown above.
- No prose, no explanation, no code fences.
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

Return ONLY a JSON object of exactly this shape:

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
- Do not include line numbers, hunk headers, or diff context; those are computed
  deterministically from your edits.
- No prose, no explanation, no code fences.
"""


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    print(edit_compiler.BANNER)
    if OUT.is_file():
        print(f"BLOCKED: {OUT} exists; a frozen manifest is immutable")
        return 2
    if not AUDIT.is_file():
        print("BLOCKED_CONTEXT_AUDIT: the historical-fix-region coverage audit has not been run")
        return 3

    bm08_manifest = json.loads((BM08 / "manifest-executable-v5.json").read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    coverage = {row["case_id"]: row for row in audit["cases"]}

    entries = []
    for case in bm08_manifest["cases"]:
        row = coverage.get(case["case_id"])
        if row is None:
            print(f"BLOCKED_CONTEXT_AUDIT: {case['case_id']} missing from the coverage audit")
            return 3
        entries.append(
            {
                "case_id": case["case_id"],
                "repository": case["repository"],
                "baseline_tree_hash": case["baseline_tree_hash"],
                "context_hash": row["context_hash"],
                "context_files": row["selected_files"],
                "node_id": case["target_node"],
                "expected_signature": case["failure_identity"],
                "src_layout": case.get("src_layout", "flat"),
                "historical_fix_region_coverage": row["coverage_status"],
                "historical_fix_region_coverage_code_only": row["code_only_coverage_status"],
            }
        )

    case_ids = [e["case_id"] for e in entries]
    samples = design.build(case_ids)
    problems = design.schedule_problems(samples, case_ids)
    if problems:
        for problem in problems:
            print(f"  SCHEDULE  {problem}")
        print("BLOCKED: the frozen schedule is not the design it claims to be")
        return 4

    # Derived from the largest reconstructed context rather than asserted. The
    # audit records each context's exact rendered size; the ceiling is that,
    # plus the prompt scaffold, at a pessimistic 3 characters per token, with
    # 50% headroom, rounded up to the next thousand. Cutting this to reach a
    # tidier budget would be reducing request authority to fit a number.
    largest_chars = max(coverage[e["case_id"]]["selected_chars"] for e in entries)
    max_input_tokens = int(((largest_chars + 4000) / 3) * 1.5 // 1000 + 1) * 1000
    budget = cost_module.worst_case_study(
        cases=len(entries),
        repeats=design.REPEATS,
        conditions=len(design.CONDITIONS),
        max_requests_per_sample=schemas.MAX_REQUESTS_PER_SAMPLE,
        max_input_tokens=max_input_tokens,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        pricing=PRICING,
    )
    detectable = analysis_plan.detectable_effect(cases=len(entries), repeats=design.REPEATS)

    manifest = {
        "label": "REPRESENTATION EXPERIMENT — FROZEN PREPARATION — NOT AUTHORIZED, NOT EXECUTED",
        "design_name": design.DESIGN_NAME,
        "powered": False,
        "corpus_manifest_hash": bm08_manifest["corpus_manifest_hash"],
        "repository_population_hash_v5": bm08_manifest["repository_population_hash_v5"],
        "exclusion_set_hash": bm08_manifest["exclusion_set_hash"],
        "coverage_audit_hash": audit["audit_manifest_hash"],
        "model": {"requested_model_id": MODEL, "required_reported_model_identity": "must equal requested"},
        "pricing": PRICING,
        "sampling": SAMPLING,
        "budget": {
            **budget,
            "max_input_tokens": max_input_tokens,
            "max_input_tokens_derivation": (
                f"largest reconstructed context {largest_chars} chars + 4000 scaffold, "
                "at 3 chars/token with 50% headroom, rounded up to the next thousand"
            ),
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "max_requests_per_sample": schemas.MAX_REQUESTS_PER_SAMPLE,
            "total_usd_ceiling": budget["recommended_authorization_ceiling_usd"],
            "authorization_status": "NOT AUTHORIZED — recorded for independent approval only",
        },
        "u_schema": schemas.U_SCHEMA,
        "s_schema": schemas.S_SCHEMA,
        "u_schema_hash": schemas.schema_hash(schemas.U_SCHEMA),
        "s_schema_hash": schemas.schema_hash(schemas.S_SCHEMA),
        "u_prompt_hash": prompt_hash(U_PROMPT),
        "s_prompt_hash": prompt_hash(S_PROMPT),
        "u_prompt_template": U_PROMPT,
        "s_prompt_template": S_PROMPT,
        "schema_repair_policy": (
            "exactly one governed schema repair per sample, identical for U and S; "
            "no semantic retries; every request counted and retained"
        ),
        "compiler_hash": edit_compiler.compiler_hash(),
        "compiler_authority_contract_hash": edit_compiler.authority_contract_hash(),
        "compiler_authority_contract": edit_compiler.AUTHORITY_CONTRACT,
        "canonicalizer_identity": {
            "runtime_hash": driver.observed_runtime_hash(),
            "records_py_sha256": hashlib.sha256((ROOT / "src" / "riftagent" / "records.py").read_bytes()).hexdigest(),
        },
        "driver_hash": driver.driver_hash(),
        "runner_hash": bm08_runner.runner_hash(),
        "oracle_hash": oracle_module.oracle_hash(),
        "execution_environment_hash": environment.environment_hash(),
        "execution_environment": environment.describe(),
        "transaction_implementation_hash": tx.implementation_hash(),
        "cost_field_authority": {
            "authoritative": cost_module.AUTHORITATIVE_FIELD,
            "reservation": cost_module.RESERVATION_FIELD,
            "non_authoritative": list(cost_module.NON_AUTHORITATIVE_FIELDS),
        },
        "repeats": design.REPEATS,
        "expected_samples": design.EXPECTED_SAMPLES,
        "order_balance": design.order_balance(samples),
        "analysis_plan": analysis_plan.as_dict(),
        "analysis_plan_hash": analysis_plan.plan_hash(),
        "detectable_effect": detectable,
        "cases": entries,
        "samples": samples,
    }
    body = {k: v for k, v in manifest.items() if k != "representation_experiment_manifest_hash"}
    manifest["representation_experiment_manifest_hash"] = hashlib.sha256(
        (json.dumps(body, indent=1, sort_keys=True) + "\n").encode("utf-8")
    ).hexdigest()
    OUT.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    from collections import Counter

    print(f"cases                    : {len(entries)}")
    print(f"samples                  : {len(samples)}  ({design.REPEATS} repeats x 2 conditions)")
    print(f"order balance            : {manifest['order_balance']}")
    print(f"coverage strata          : {dict(Counter(e['historical_fix_region_coverage'] for e in entries))}")
    print(f"u_schema_hash            : {manifest['u_schema_hash']}")
    print(f"s_schema_hash            : {manifest['s_schema_hash']}")
    print(f"u_prompt_hash            : {manifest['u_prompt_hash']}")
    print(f"s_prompt_hash            : {manifest['s_prompt_hash']}")
    print(f"compiler_hash            : {manifest['compiler_hash']}")
    print(f"authority_contract_hash  : {manifest['compiler_authority_contract_hash']}")
    print(f"transaction_hash         : {manifest['transaction_implementation_hash']}")
    print(f"execution_environment    : {manifest['execution_environment_hash']}")
    print(f"analysis_plan_hash       : {manifest['analysis_plan_hash']}")
    print(f"\nper-request reservation  : ${budget['per_request_usd']:.6f}")
    print(f"per-sample reservation   : ${budget['per_sample_reservation_usd']:.6f}")
    print(f"worst-case total         : ${budget['total_worst_case_usd']:.4f}")
    print(f"recommended ceiling      : ${budget['recommended_authorization_ceiling_usd']:.2f}  NOT AUTHORIZED")
    print(f"\ndetectable difference    : {detectable['approximate_detectable_difference']}")
    print(f"minimum effect of interest: {detectable['minimum_effect_of_interest']}")
    print(f"verdict                  : {detectable['verdict']}")
    print(f"\nrepresentation_experiment_manifest_hash: {manifest['representation_experiment_manifest_hash']}")
    print(f"written: {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
