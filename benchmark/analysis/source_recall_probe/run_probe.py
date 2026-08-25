"""POST-HOC SOURCE-RECALL / PROPOSAL-FORMAT PROBE
NOT BM-08 · NOT BM-09 · NOT OFFICIAL BENCHMARK EVIDENCE · EXPLORATORY — NOT CAUSAL

Run the frozen twelve conditions: six cases, each asked once as a unified diff
and once as exact search/replace, in the counterbalanced order the manifest
froze before any response existed.

One stochastic sample per condition. That is enough to notice a pattern worth
investigating and nowhere near enough to attribute one, so nothing here reports
an effect size and the reporting language is bounded accordingly.

Transaction discipline is the same shape BM-07 and BM-08 run under: the
`REQUEST_STARTED` record is durable before the provider is touched, an
unreconciled request stops the whole probe rather than its own condition, the
complete raw response is retained, and a condition cannot go terminal until its
result evidence is already on disk.

Provider configuration is validated by RIFT's own `ProviderConfig.from_env`, but
the HTTP call is made here so the complete raw response body can be retained —
`post_chat` parses and discards it, and the protocol requires the bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "benchmark" / "bm08"))
sys.path.insert(0, str(ROOT / "src"))

import bm08_driver as driver  # noqa: E402
import confinement  # noqa: E402
import probe_context  # noqa: E402
import search_replace  # noqa: E402
import transactions  # noqa: E402

from riftagent.llm import ModelUnavailable, ProviderConfig  # noqa: E402
from riftagent.records import canonical_diff, canonicalize_patch  # noqa: E402
from riftagent.sandbox import tree_hash  # noqa: E402

BM08 = ROOT / "benchmark" / "bm08"
MANIFEST = HERE / "probe-manifest.json"
RESULTS = HERE / "probe-results.jsonl"
LEDGER = HERE / "probe-ledger.jsonl"
RAW_DIR = HERE / "raw-responses"
WORK = pathlib.Path("/tmp/probe-run")
REPO_ROOTS = (pathlib.Path("/repos"), pathlib.Path("/repos-v3"), pathlib.Path("/repos-v5"))
MAX_RESPONSE_BYTES = 4_000_000


class ProbeBlocked(RuntimeError):
    pass


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def repo_for(name: str) -> pathlib.Path:
    found = [root / name for root in REPO_ROOTS if (root / name / ".git").is_dir()]
    if len(found) != 1:
        raise ProbeBlocked(f"BLOCKED_BASELINE_IDENTITY: {name} resolved to {len(found)} roots")
    return found[0]


# ------------------------------------------------------------------- provider


def call_provider(config: ProviderConfig, prompt: str, max_output_tokens: int) -> tuple[bytes, dict]:
    """One bounded request. Returns the complete raw body and the parsed JSON."""
    body = json.dumps(
        {
            "model": config.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": int(max_output_tokens),
        }
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - https enforced by ProviderConfig
        config.url,
        data=body,
        method="POST",
        headers={"content-type": "application/json", "authorization": f"Bearer {config.key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180.0) as response:  # noqa: S310
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ModelUnavailable(f"provider returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ModelUnavailable(f"provider unreachable: {type(exc).__name__}") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ModelUnavailable("provider response exceeded the size bound")
    return raw, json.loads(raw.decode("utf-8"))


def reply_parts(parsed: dict) -> tuple[str, str, dict]:
    choice = (parsed.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content")) or ""
    if isinstance(text, list):  # some gateways return content parts
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    usage = parsed.get("usage") or {}
    return text, parsed.get("model") or "", usage


def cost_of(usage: dict, pricing: dict) -> float:
    return (
        usage.get("prompt_tokens", 0) / 1e6 * pricing["input_per_mtok"]
        + usage.get("completion_tokens", 0) / 1e6 * pricing["output_per_mtok"]
    )


# ------------------------------------------------------------------- proposals


def extract_diff(text: str) -> str:
    """The unified diff, with fences stripped. No repair of content."""
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines)
    start = body.find("--- ")
    if start == -1:
        start = body.find("diff --git ")
    if start == -1:
        return ""
    return canonical_diff(body[start:])


def extract_json(text: str) -> object | None:
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None


# ------------------------------------------------------------------ evaluation


def git_apply_check(tree: pathlib.Path, patch: pathlib.Path) -> dict:
    proc = confinement.run_repository_check(["git", "apply", "--check", "--verbose", str(patch)], tree, timeout=300)
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "diagnostic": (proc.stderr or "").strip()[:3000],
    }


def structural(tree: pathlib.Path, patch_text: str, work: pathlib.Path) -> tuple[int, int]:
    """git's own verdicts on whether the diff parses, for the canonicalizer."""
    path = work / "structural.diff"
    path.write_text(patch_text, encoding="utf-8")
    raw = confinement.run_repository_check(["git", "apply", "--numstat", str(path)], tree, timeout=120)
    recount = confinement.run_repository_check(["git", "apply", "--numstat", "--recount", str(path)], tree, timeout=120)
    return raw.returncode, recount.returncode


def evaluate_unified(case: dict, text: str, work: pathlib.Path, master: pathlib.Path) -> dict:
    diff = extract_diff(text)
    out: dict = {"raw_diff_hash": sha(diff) if diff else "", "raw_diff_chars": len(diff)}
    if not diff:
        out.update(
            {
                "raw_apply_ok": False,
                "raw_diagnostic": "no unified diff found in the response",
                "raw_failure_class": "SCHEMA_INVALID",
                "canonical_apply_ok": False,
            }
        )
        return out

    tree = work / "u-raw"
    shutil.rmtree(tree, ignore_errors=True)
    shutil.copytree(master, tree, symlinks=True)
    patch = work / "u-raw.diff"
    patch.write_text(diff, encoding="utf-8")
    raw_result = git_apply_check(tree, patch)
    out["raw_apply_ok"] = raw_result["ok"]
    out["raw_exit_code"] = raw_result["exit_code"]
    out["raw_diagnostic"] = raw_result["diagnostic"]

    # Secondary only: the frozen canonicalizer, unchanged, for comparison with
    # BM-08's pipeline. Condition U is not defined by this number.
    s_raw, s_recount = structural(tree, diff, work)
    canon = canonicalize_patch(diff, structural_raw=s_raw, structural_recount=s_recount)
    canon_tree = work / "u-canon"
    shutil.rmtree(canon_tree, ignore_errors=True)
    shutil.copytree(master, canon_tree, symlinks=True)
    canon_patch = work / "u-canon.diff"
    canon_patch.write_text(canon.diff, encoding="utf-8")
    canon_result = git_apply_check(canon_tree, canon_patch)
    out.update(
        {
            "canonical_status": canon.status,
            "canonical_diff_hash": sha(canon.diff),
            "canonical_apply_ok": canon_result["ok"],
            "canonical_diagnostic": canon_result["diagnostic"],
        }
    )
    shutil.rmtree(tree, ignore_errors=True)
    shutil.rmtree(canon_tree, ignore_errors=True)
    return out


def evaluate_search_replace(text: str, work: pathlib.Path, master: pathlib.Path) -> dict:
    payload = extract_json(text)
    tree = work / "s-tree"
    shutil.rmtree(tree, ignore_errors=True)
    shutil.copytree(master, tree, symlinks=True)
    if payload is None:
        result = search_replace.ApplyResult(
            search_replace.SCHEMA_INVALID, False, False, detail="no JSON object found in the response"
        )
    else:
        result = search_replace.apply_edits(tree, payload)
    out = result.to_dict()
    out["proposal_hash"] = sha(json.dumps(payload, sort_keys=True)) if payload is not None else ""
    out["tree_after"] = tree_hash(tree) if result.apply_ok else ""
    if not result.apply_ok:
        shutil.rmtree(tree, ignore_errors=True)
    return out, (tree if result.apply_ok else None)


def target_check(tree: pathlib.Path, node_id: str, layout: str) -> dict:
    """Secondary endpoint. Repository execution, network-denied."""
    src = tree / layout if layout and layout != "flat" else tree
    proc = confinement.run_repository_check(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", node_id],
        tree,
        {"PYTHONPATH": str(src.resolve())},
        timeout=900,
    )
    return {"target_pass": proc.returncode == 0, "target_exit": proc.returncode}


# ------------------------------------------------------------------------ main


def run_condition(
    *,
    entry: dict,
    condition: str,
    position: int,
    manifest: dict,
    config: ProviderConfig,
    ledger: transactions.ProbeLedger,
    master: pathlib.Path,
    context: dict,
) -> dict:
    budget = manifest["budget"]
    template = manifest["u_prompt_template"] if condition == "U" else manifest["s_prompt_template"]
    prompt = template.format(
        node_id=entry["node_id"], signature=entry["expected_signature"], context=context["context"]
    )
    prompt_h = manifest["u_prompt_hash"] if condition == "U" else manifest["s_prompt_hash"]
    reservation = (
        budget["max_input_tokens"] / 1e6 * manifest["pricing"]["input_per_mtok"]
        + budget["max_output_tokens"] / 1e6 * manifest["pricing"]["output_per_mtok"]
    )

    requests_made, cost, usage_total = 0, 0.0, {"prompt_tokens": 0, "completion_tokens": 0}
    text = reported = ""
    raw_hashes: list[str] = []

    while requests_made < budget["max_requests_per_condition"]:
        ordinal = requests_made + 1
        request_id = ledger.start_request(
            probe_manifest_hash=manifest["probe_manifest_hash"],
            case_id=entry["case_id"],
            condition=condition,
            ordinal=ordinal,
            prompt_hash=sha(prompt) if ordinal == 1 else sha(prompt + f"::repair{ordinal}"),
            requested_model=config.model,
            reserved_usd=reservation,
        )
        try:
            raw, parsed = call_provider(config, prompt, budget["max_output_tokens"])
        except ModelUnavailable as exc:
            ledger.failure(request_id, str(exc))
            raise ProbeBlocked(f"BLOCKED_PROVIDER: {exc}") from None

        raw_hash = sha(raw)
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / f"{entry['case_id']}-{condition}-{ordinal}.json").write_bytes(raw)
        raw_hashes.append(raw_hash)
        text, reported, usage = reply_parts(parsed)
        requests_made += 1
        usage_total["prompt_tokens"] += usage.get("prompt_tokens", 0)
        usage_total["completion_tokens"] += usage.get("completion_tokens", 0)
        cost += cost_of(usage, manifest["pricing"])
        ledger.response(
            request_id,
            reported_model=reported,
            raw_hash=raw_hash,
            usage=usage,
            cost_usd=cost_of(usage, manifest["pricing"]),
        )

        if reported != config.model:
            raise ProbeBlocked(f"BLOCKED_MODEL_IDENTITY: requested {config.model!r}, reported {reported!r}")

        # One schema repair, matching BM-08's authority. Never a semantic retry:
        # the ask is unchanged, only the shape of the answer is re-requested.
        parsed_ok = bool(extract_diff(text)) if condition == "U" else extract_json(text) is not None
        if parsed_ok:
            break
        prompt = (
            template.format(node_id=entry["node_id"], signature=entry["expected_signature"], context=context["context"])
            + "\n\nYour previous response could not be parsed in the required format. "
            "Return only the required output, with no prose and no code fences."
        )

    work = WORK / f"{entry['case_id']}-{condition}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    applied_tree = None
    if condition == "U":
        evaluation = evaluate_unified(entry, text, work, master)
        evaluation["exact_source_quote_valid"] = None  # not defined for a diff
    else:
        evaluation, applied_tree = evaluate_search_replace(text, work, master)

    # Secondary endpoint: only meaningful if something actually applied.
    secondary = {"target_pass": None, "target_exit": None}
    if condition == "S" and applied_tree is not None:
        secondary = target_check(applied_tree, entry["node_id"], entry.get("src_layout", "flat"))
        shutil.rmtree(applied_tree, ignore_errors=True)

    record = {
        "label": manifest["label"],
        "probe_manifest_hash": manifest["probe_manifest_hash"],
        "case_id": entry["case_id"],
        "repository": entry["repository"],
        "condition": condition,
        "order_position": position,
        "order_label": entry["order_label"],
        "bm08_failure_class": entry["failure_class"],
        "baseline_tree_hash": entry["baseline_tree_hash"],
        "context_hash": context["context_hash"],
        "prompt_hash": prompt_h,
        "search_replace_executor_hash": manifest["search_replace_executor_hash"],
        "transaction_implementation_hash": manifest["transaction_implementation_hash"],
        "canonicalizer_identity": manifest["canonicalizer_identity"],
        "requested_model": config.model,
        "reported_model": reported,
        "raw_response_hash": raw_hashes[-1] if raw_hashes else "",
        "raw_response_hashes": raw_hashes,
        "request_count": requests_made,
        "input_tokens": usage_total["prompt_tokens"],
        "output_tokens": usage_total["completion_tokens"],
        "cost_usd": round(cost, 6),
        **secondary,
        **evaluation,
    }
    shutil.rmtree(work, ignore_errors=True)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(prog="run_probe")
    parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print(manifest["label"])
    print("=" * 60)

    ledger = transactions.ProbeLedger(LEDGER)
    try:
        ledger.require_reconciled()
    except transactions.UnreconciledRequest as exc:
        print(f"BLOCKED_TRANSACTION_RECONCILIATION: {exc}")
        return 5

    config = ProviderConfig.from_env()
    if config.model != manifest["model"]["requested_model_id"]:
        print(
            f"BLOCKED_MODEL_IDENTITY: env model {config.model!r} != frozen {manifest['model']['requested_model_id']!r}"
        )
        return 6

    case_manifest = json.loads((BM08 / "manifest-executable-v5.json").read_text(encoding="utf-8"))
    by_id = {c["case_id"]: c for c in case_manifest["cases"]}

    existing = []
    if RESULTS.is_file():
        existing = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    done = {(r["case_id"], r["condition"]) for r in existing}

    spent = sum(r["cost_usd"] for r in existing)
    records = list(existing)

    for entry in manifest["cases"]:
        case = by_id[entry["case_id"]]
        master = WORK / f"master-{entry['case_id']}"
        shutil.rmtree(master, ignore_errors=True)
        driver.materialise_baseline(case, repo_for(case["repository"]).parent, master)
        observed = tree_hash(master)
        if observed != entry["baseline_tree_hash"]:
            print(f"BLOCKED_BASELINE_IDENTITY: {entry['case_id']}")
            return 3
        evidence = BM08 / "results-evidence" / entry["case_id"] / entry["bm08_arm"]
        context = probe_context.build(master, evidence)
        if context["context_hash"] != entry["context_hash"]:
            print(f"BLOCKED_CONTEXT_IDENTITY: {entry['case_id']}")
            return 4

        for position, condition in enumerate(entry["order"]):
            if (entry["case_id"], condition) in done:
                continue
            if spent >= manifest["budget"]["total_usd_ceiling"]:
                print("BLOCKED_BUDGET_AUTHORITY: ceiling reached before the frozen design completed")
                return 7
            entry_with_layout = {**entry, "src_layout": case.get("src_layout", "flat")}
            record = run_condition(
                entry=entry_with_layout,
                condition=condition,
                position=position,
                manifest=manifest,
                config=config,
                ledger=ledger,
                master=master,
                context=context,
            )
            # Result evidence durable BEFORE the condition is marked terminal.
            with RESULTS.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
            ledger.result_persisted(entry["case_id"], condition, sha(json.dumps(record, sort_keys=True)))
            ledger.condition_terminal(entry["case_id"], condition)
            records.append(record)
            spent += record["cost_usd"]
            quote = record.get("exact_source_quote_valid")
            print(
                f"  {entry['case_id']:26} {condition}"
                f"  applied={str(record.get('apply_ok', record.get('canonical_apply_ok'))):5}"
                f"  quote_valid={quote}  ${record['cost_usd']:.4f}"
            )
        shutil.rmtree(master, ignore_errors=True)

    expected_pairs = {(e["case_id"], c) for e in manifest["cases"] for c in e["order"]}
    problems = transactions.completeness_problems(records, manifest["probe_manifest_hash"], expected_pairs)
    ledger.require_reconciled()
    print(f"\ntotal spend : ${spent:.4f} of ${manifest['budget']['total_usd_ceiling']:.2f}")
    if problems:
        for problem in problems:
            print(f"  INCOMPLETE  {problem}")
        print("aggregation refused")
        return 8
    print(f"completeness: {len(records)} of {transactions.EXPECTED_RESULTS} -> COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeBlocked as exc:
        print(str(exc))
        raise SystemExit(9) from exc
