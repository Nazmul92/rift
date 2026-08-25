"""REPRESENTATION EXPERIMENT — the sample runner.

PREPARATION ONLY. **This module never opens a socket.** The provider is injected
as a callable, so the model-free dry run exercises the identical code path that a
paid run would take — the transaction sequence, the schema-repair rule, the
compiler, the evaluation, the persistence order — with a deterministic double in
place of the network.

That matters more than it might look. A dry run that exercised a *parallel*
code path would prove nothing about the path that later spends money; this one
differs from the paid run only in which callable is handed in.

U and S diverge at exactly one point and nowhere else:

    U   response.diff                          -> existing raw/normalized/canonical pipeline
    S   response.edits -> compiler -> diff     -> existing raw/normalized/canonical pipeline

Everything downstream — ChangeSet, withdrawal, reapply, target, preservation,
verification gate, oracle — is frozen and shared.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys
from collections.abc import Callable

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

import analysis_plan  # noqa: E402
import cost as cost_module  # noqa: E402
import edit_compiler  # noqa: E402
import rep_transactions as tx  # noqa: E402
import schemas  # noqa: E402

BANNER = "REPRESENTATION EXPERIMENT — PREPARATION — NO PROVIDER CALL FROM THIS MODULE"

# A provider double (or the real adapter) returns exactly this shape. Keeping it
# a plain tuple means the dry run cannot accidentally depend on adapter internals.
ProviderCall = Callable[[str, str, int], tuple[bytes, dict]]


class ModelIdentityError(RuntimeError):
    """Reported model is not the requested model. Blocks the result."""


class BudgetAuthorityError(RuntimeError):
    """The ceiling cannot cover what remains of the frozen design."""


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def reply_parts(parsed: dict) -> tuple[str, str, dict]:
    choice = (parsed.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    return text, parsed.get("model") or "", parsed.get("usage") or {}


def settle(usage: dict, pricing: dict) -> float:
    """Settled spend from provider-reported usage. Never an estimate."""
    return (
        usage.get("prompt_tokens", 0) / 1e6 * pricing["input_per_mtok"]
        + usage.get("completion_tokens", 0) / 1e6 * pricing["output_per_mtok"]
    )


def render_signature(signature: object) -> str:
    """The frozen failure identity as one deterministic string.

    BM-08 stores it as a structured record (exception type, message, node), so it
    is serialised with sorted keys rather than str()-ed: the same signature must
    produce the same prompt bytes on every repeat, or the three repeats would not
    be repeats of the same question.
    """
    if isinstance(signature, str):
        return signature
    return json.dumps(signature, sort_keys=True)


def build_prompt(manifest: dict, entry: dict, condition: str, context: str) -> str:
    template = manifest["u_prompt_template"] if condition == "U" else manifest["s_prompt_template"]
    return template.format(
        node_id=entry["node_id"], signature=render_signature(entry["expected_signature"]), context=context
    )


def semantic_body_hash(manifest: dict, entry: dict, context: str) -> str:
    """The task, with the representation-specific wrapper excluded.

    Both conditions must be asked the same question. Hashing the semantic body
    separately is what makes that checkable rather than asserted.
    """
    body = chr(10).join([entry["node_id"], render_signature(entry["expected_signature"]), context])
    return sha(body)


def evaluate_u(payload: dict) -> dict:
    """U hands its diff straight to the frozen pipeline."""
    return {"proposal_diff": payload["diff"], "compile_status": "N/A", "compile_ok": None}


def evaluate_s(baseline: pathlib.Path, payload: dict, work: pathlib.Path) -> dict:
    """S compiles its exact edits into a diff Git generated."""
    compilation = edit_compiler.compile_edits(baseline, payload, work)
    out = {
        "compile_status": compilation.status,
        "compile_ok": compilation.ok,
        "compiler_hash": edit_compiler.compiler_hash(),
        "compiler_authority_contract_hash": edit_compiler.authority_contract_hash(),
        "compiler_receipt": compilation.to_dict(),
        "proposal_diff": compilation.compiled_diff if compilation.ok else "",
        "exact_source_valid": compilation.ok,
    }
    if compilation.ok:
        round_trip_ok, detail = edit_compiler.verify_round_trip(baseline, compilation, work)
        scoped_ok, scope_detail = edit_compiler.bytes_changed_only_where_declared(work, compilation)
        out["round_trip_ok"] = round_trip_ok
        out["round_trip_detail"] = detail
        out["scoped_change_ok"] = scoped_ok
        out["scoped_change_detail"] = scope_detail
        if not (round_trip_ok and scoped_ok):
            # Fail closed: a compiler that cannot reproduce its own after-tree is
            # a defect, not a representation result.
            out["compile_ok"] = False
            out["compile_status"] = "COMPILER_INVARIANT_VIOLATION"
            out["proposal_diff"] = ""
    return out


def run_sample(
    *,
    manifest: dict,
    entry: dict,
    sample: dict,
    context: str,
    baseline: pathlib.Path,
    ledger: tx.StudyLedger,
    provider: ProviderCall,
    work: pathlib.Path,
    oracle: Callable[[dict], dict] | None = None,
    spent_so_far: float = 0.0,
) -> dict:
    """One sample, end to end, with the transaction discipline in force."""
    condition = sample["condition"]
    budget = manifest["budget"]
    reservation = cost_module.reservation_per_request(
        budget["max_input_tokens"], budget["max_output_tokens"], manifest["pricing"]
    )
    remaining = budget["total_usd_ceiling"] - spent_so_far
    if remaining < reservation * budget["max_requests_per_sample"]:
        raise BudgetAuthorityError(
            f"remaining ${remaining:.4f} cannot reserve this sample's worst case "
            f"${reservation * budget['max_requests_per_sample']:.4f}"
        )

    prompt = build_prompt(manifest, entry, condition, context)
    requests_made = 0
    actual = 0.0
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    raw_hashes: list[str] = []
    reported = ""
    payload: object | None = None
    schema_detail = ""

    while requests_made < budget["max_requests_per_sample"]:
        ordinal = requests_made + 1
        request_id = ledger.start_request(
            manifest_hash=manifest["representation_experiment_manifest_hash"],
            sample_id=sample["sample_id"],
            case_id=sample["case_id"],
            repeat=sample["repeat"],
            condition=condition,
            ordinal=ordinal,
            prompt_hash=sha(prompt),
            requested_model=manifest["model"]["requested_model_id"],
            reserved_usd=reservation,
        )
        try:
            raw, parsed = provider(condition, prompt, budget["max_output_tokens"])
        except Exception as exc:  # provider double or adapter failure
            ledger.failure(request_id, f"{type(exc).__name__}: {exc}")
            raise

        raw_hash = sha(raw)
        raw_hashes.append(raw_hash)
        (work / "raw").mkdir(parents=True, exist_ok=True)
        (work / "raw" / f"{sample['sample_id']}-{ordinal}.json").write_bytes(raw)

        text, reported, usage = reply_parts(parsed)
        requests_made += 1
        usage_total["prompt_tokens"] += usage.get("prompt_tokens", 0)
        usage_total["completion_tokens"] += usage.get("completion_tokens", 0)
        this_cost = settle(usage, manifest["pricing"])
        actual += this_cost
        ledger.response(request_id, reported_model=reported, raw_hash=raw_hash, usage=usage, actual_usd=this_cost)

        if reported != manifest["model"]["requested_model_id"]:
            raise ModelIdentityError(f"requested {manifest['model']['requested_model_id']!r}, reported {reported!r}")

        candidate = schemas.extract_json(text)
        valid, schema_detail = (
            schemas.validate(condition, candidate)
            if candidate is not None
            else (
                False,
                "no JSON object in the response",
            )
        )
        if valid:
            payload = candidate
            break
        # Exactly one governed schema repair, identical for U and S.
        prompt = (
            build_prompt(manifest, entry, condition, context)
            + "\n\n"
            + schemas.schema_repair_instruction(condition, schema_detail)
        )

    record: dict = {
        "representation_experiment_manifest_hash": manifest["representation_experiment_manifest_hash"],
        "sample_id": sample["sample_id"],
        "case_id": sample["case_id"],
        "repeat": sample["repeat"],
        "pair_id": sample["pair_id"],
        "condition": condition,
        "request_position": sample["request_position"],
        "order_label": sample["order_label"],
        "baseline_tree_hash": entry["baseline_tree_hash"],
        "context_hash": entry["context_hash"],
        "historical_fix_region_coverage": entry["historical_fix_region_coverage"],
        "prompt_hash": manifest["u_prompt_hash"] if condition == "U" else manifest["s_prompt_hash"],
        "semantic_body_hash": semantic_body_hash(manifest, entry, context),
        "compiler_authority_contract_hash": edit_compiler.authority_contract_hash(),
        # Stamped for every S sample, including one that never reached the
        # compiler: it identifies the harness that would have compiled, which is
        # what a reviewer needs, and its absence would otherwise read as a
        # missing field rather than an abandoned generation.
        "compiler_hash": edit_compiler.compiler_hash() if condition == "S" else "",
        "canonicalizer_identity": manifest["canonicalizer_identity"],
        "execution_environment_hash": manifest["execution_environment_hash"],
        "requested_model": manifest["model"]["requested_model_id"],
        "reported_model": reported,
        "raw_response_hash": raw_hashes[-1] if raw_hashes else "",
        "raw_response_hashes": raw_hashes,
        "request_count": requests_made,
        "input_tokens": usage_total["prompt_tokens"],
        "output_tokens": usage_total["completion_tokens"],
        "reserved_usd": reservation * requests_made,
        "actual_usd": round(actual, 6),
    }

    if payload is None:
        record.update(
            {
                "schema_valid": False,
                "schema_detail": schema_detail,
                "outcome_class": "NO_CANDIDATE",
                "proposal_diff_hash": "",
                "truth_correct": False,
            }
        )
        return record

    record["schema_valid"] = True
    evaluation = evaluate_u(payload) if condition == "U" else evaluate_s(baseline, payload, work)
    record.update(evaluation)
    record["proposal_diff_hash"] = sha(evaluation["proposal_diff"]) if evaluation["proposal_diff"] else ""

    if not evaluation["proposal_diff"]:
        record["outcome_class"] = evaluation.get("compile_status", "NO_CANDIDATE")
        record["truth_correct"] = False
        return record

    # Downstream is frozen and identical for both conditions. The oracle is
    # injected so the dry run can exercise the interface without a repository.
    verdict = oracle(record) if oracle else {"truth_correct": False, "outcome_class": "ORACLE_NOT_RUN"}
    if verdict.get("outcome_class") == tx.INFRASTRUCTURE_FAILURE:
        record.update(verdict)
        record["truth_correct"] = False
        return record
    record.update(verdict)
    record["truth_correct"] = bool(verdict.get("truth_correct"))
    return record


def persist(results_path: pathlib.Path, ledger: tx.StudyLedger, record: dict) -> None:
    """Result evidence durable BEFORE the sample is marked terminal."""
    line = json.dumps(record, sort_keys=True) + "\n"
    with results_path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        import os

        os.fsync(handle.fileno())
    ledger.result_persisted(record["sample_id"], sha(line))
    ledger.sample_terminal(record["sample_id"])


def cleanup(work: pathlib.Path) -> None:
    shutil.rmtree(work, ignore_errors=True)


__all__ = [
    "BANNER",
    "BudgetAuthorityError",
    "ModelIdentityError",
    "analysis_plan",
    "cleanup",
    "persist",
    "run_sample",
]
