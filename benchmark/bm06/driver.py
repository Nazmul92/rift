"""BM-06 driver — runs arms A, B and C against a validated manifest.

Benchmark infrastructure, not a runtime module. It reuses the shipped runtime:
arms are `rift fix` invocations, shadow evaluation and ground truth are `rift
verify`, and no verdict is decided here.

## What was wrong before, and what changed

The first version of this file had tests and no experiment. Review found eleven
defects in the consuming path; the ones that mattered:

* all three arms invoked the identical `rift fix` command, so there were no arms;
* arm B's random draw was recorded but never selected anything, and its seed came
  from `hash()`, which is not stable across processes;
* arm A's patch was never captured, so shadow evaluation always received `None`
  and evaluated nothing;
* acceptance was inferred from a process return code;
* ground-truth correctness was never computed on a live run;
* spend was copied into result rows instead of referenced in the ledger.

The tests passed throughout, because they exercised `report()` arithmetic and a
dry run that substitutes the CLI. Helper tests over an experiment that does not
run are not evidence that it runs.

## Fail-closed

`validate_manifest` runs before anything else and **no provider request is made
if it returns a failure**. An arm whose orchestration the shipped CLI cannot
express is refused by name, never silently replaced by another arm's command —
three identical runs reported as three arms is the failure this file exists to
avoid repeating.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ARMS = ("A", "B", "C")

# An arm is expressible only if the shipped CLI offers the flag that makes it
# differ. Probed from `--help` rather than assumed, so this file starts working
# the day the flag exists and refuses honestly until then.
ARM_REQUIRES = {
    "A": "--model-alone",  # propose without kernel diagnosis; accept on target pass
    "B": "--probe-policy",  # random selection from the identical probe pool
    "C": "",  # the shipped default path
}


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_manifest(path: Path) -> tuple[dict, str]:
    """Read the manifest **once**, and return it with the digest of those bytes.

    One read, two derivations. An earlier version hashed the file again after
    the run finished, which is a different guarantee than it appeared to be: a
    manifest edited between startup and completion would have been executed as
    X and stamped as Y, and `--report-only Y` would then have accepted a report
    describing an experiment that never ran against Y.

    The digest travels in memory for the rest of the run. Re-reading the file to
    establish what the run used is exactly the mistake, because the file is the
    thing that may have changed.
    """
    raw = Path(path).read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


# The runtime whose behaviour a result describes. Every shipped module, so a
# change anywhere in the product invalidates a report built under it — the
# governed set is "the package", not a hand-kept list that can quietly omit the
# file somebody edited.
GOVERNED_RUNTIME = "src/riftagent/**/*.py"


def runtime_hash(root: Path, pattern: str = GOVERNED_RUNTIME) -> tuple[str, list[str]]:
    """Identity of the runtime source, read **once**, at startup.

    Each file contributes its normalised repository-relative path *and* its
    exact bytes, in sorted path order:

        for each governed file, sorted by normalised relative path:
            update(path bytes) ; update(b"\\0") ; update(file bytes) ; update(b"\\0")

    The path is hashed alongside the content because content alone cannot
    distinguish a file that moved from a file that changed — two trees with the
    same bytes under different names are not the same runtime.

    Returns the digest and the exact list of files it covers, so a report can
    state what was hashed rather than asking a reader to trust the pattern.
    """
    digest = hashlib.sha256()
    covered: list[str] = []
    for path in sorted(Path(root).glob(pattern)):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        covered.append(rel)
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), covered


def file_hash(path: Path) -> str:
    """Identity of one file's exact bytes, read once."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RuntimeDrift(RuntimeError):
    """The runtime that is about to execute is not the one that was frozen."""


# What a baseline tree's identity covers. Everything execution-relevant, and
# nothing that a test run legitimately creates. The list is deliberately short:
# excluding a file because it is inconvenient is how a tree hash stops meaning
# "the tree that ran".
BASELINE_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".rift",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        ".eggs",
        "htmlcov",
    }
)
BASELINE_EXCLUDE_SUFFIXES = (".pyc", ".pyo")


def baseline_tree_hash(root: Path) -> str:
    """Identity of a constructed baseline worktree.

    A case is not merely "the parent commit". Several carry the parent's source
    with the fix commit's *test half* laid over it, so `git rev-parse HEAD` is
    true and insufficient — it describes the commit, not the tree that will
    actually execute. Untracked files count for the same reason: a stray module
    on the import path changes what runs.

    Same construction as the runtime hash: sorted normalised relative path, then
    `path || \0 || bytes || \0`, so a move is distinguishable from an edit.
    """
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        parts = path.relative_to(root).parts
        if any(part in BASELINE_EXCLUDE_DIRS for part in parts):
            continue
        if path.suffix in BASELINE_EXCLUDE_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def runtime_env(runtime_root: Path) -> dict[str, str]:
    """An environment that can only import the intended runtime.

    Hashing bytes at startup says nothing about which `riftagent` a subprocess
    will import: an installed or editable copy earlier on `sys.path` would run
    while the frozen hash described source that never executed. Pinning
    `PYTHONPATH` to the intended tree makes the two the same thing.
    """
    env = dict(os.environ)
    src = (Path(runtime_root) / "src").resolve()
    env["PYTHONPATH"] = str(src) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def resolves_to(runtime_root: Path, env: dict[str, str], cwd: Path | None = None) -> str:
    """Where `import riftagent` actually lands, **from the directory that will
    run it**.

    `cwd` is not incidental. Python puts the working directory on `sys.path`
    for `-c` and `-m`, so a probe run from one place and an arm run from
    another are answering different questions — and the case worktrees are
    repositories that may well contain something importable. Asking from
    anywhere but the invocation directory is asking about a different import
    resolution than the one that will happen.
    """
    probe = subprocess.run(
        [sys.executable, "-c", "import riftagent,sys; sys.stdout.write(riftagent.__file__)"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        cwd=str(cwd) if cwd is not None else None,
    )
    return probe.stdout.strip()


def assert_runtime(runtime_root: Path, frozen: str, env: dict[str, str], when: str, cwd: Path | None = None) -> None:
    """Fail closed unless the intended runtime is both unchanged and the one
    that will be imported **from `cwd`**."""
    observed, _ = runtime_hash(runtime_root)
    if observed != frozen:
        raise RuntimeDrift(f"governed runtime changed {when}: frozen {frozen[:12]}, observed {observed[:12]}")
    landed = resolves_to(runtime_root, env, cwd)
    intended = (Path(runtime_root) / "src" / "riftagent").resolve()
    try:
        Path(landed).resolve().relative_to(intended)
    except (ValueError, OSError):
        raise RuntimeDrift(
            f"riftagent resolves to {landed or '<nothing>'} {when}, not the intended runtime at {intended}"
        ) from None


class ModelMismatch(RuntimeError):
    """The model that would run is not the model the manifest priced."""


def configured_model(env: dict[str, str] | None = None) -> str:
    """The model RIFT will actually request, from the environment it will read."""
    src = env if env is not None else os.environ
    return (src.get("RIFT_LLM_MODEL") or "").strip()


def model_binding_failures(manifest: dict, env: dict[str, str] | None = None) -> list[str]:
    """The manifest model and the configured model must be the same string.

    The manifest declares `claude-sonnet-4-6` and carries that model's prices
    and output caps. The model that actually runs comes from `RIFT_LLM_MODEL`,
    and nothing compared them. A run configured for a different model would have
    been reserved, charged and reported entirely under the manifest's identity —
    prices for one model, tokens from another, and no field in the result that
    disagreed.

    Fails closed on absent, empty, and different. There is no normalisation:
    two spellings of a model are two strings, and guessing which differences are
    cosmetic is how a mismatch becomes a rounding error.
    """
    declared = (manifest.get("model") or {}).get("id") or ""
    if not declared:
        return ["manifest.model.id is missing; there is no model to bind to"]
    actual = configured_model(env)
    if not actual:
        return [f"RIFT_LLM_MODEL is unset or empty; the manifest declares {declared!r} and nothing would enforce it"]
    if actual != declared:
        return [
            f"configured RIFT_LLM_MODEL {actual!r} is not the manifest model {declared!r}; "
            f"the run would be priced as {declared!r} and executed as {actual!r}"
        ]
    return []


class ModelIdentityUnresolved(RuntimeError):
    """An arm claims a task whose ledger cannot be read. Not `unavailable`."""


def priced_models(repo: Path, scope: str) -> list[str]:
    """The model the *spend ledger* was priced under, from `pricing.model`.

    Named for what it is. `pricing.model` is
    `os.environ.get("RIFT_LLM_MODEL")` written back out by the runtime, so it
    is the model RIFT *asked for* — configured identity, useful for checking
    that the price applied matches the model requested.

    It is **not** evidence of what the provider served, and DAR-026 read it as
    though it were: a run configured for 4.6 whose provider answered with 5
    would have re-read its own configuration and agreed with itself.
    """
    path = repo / ".rift" / "spend.jsonl"
    if not path.is_file():
        return []
    seen: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("scope") not in (scope, None):
            continue
        name = ((event.get("pricing") or {}).get("model") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def provider_reported_models(repo: Path, task_id: str) -> list[str]:
    """What the provider said it served, for **this arm's task**, in order.

    The authoritative source is `model_reported` on each
    `MODEL_RESPONSE_RECEIVED` event in `.rift/tasks/<task_id>/ledger.jsonl` —
    a value the adapter copies off the provider's own response, not out of our
    environment.

    Bound to one task id rather than scanned globally: a benchmark run writes a
    ledger per arm per case, and a check that swept all of them would attribute
    one arm's provider identity to another. If the task cannot be resolved,
    `ModelIdentityUnresolved` is raised — an arm whose evidence cannot be found
    is not the same as an arm whose provider declined to identify itself, and
    quietly downgrading the first to the second is how a missing check reads
    like a passed one.

    Every response is returned, in sequence, including a schema repair's. A
    task whose first response matched and whose repair came from a different
    model is a task that ran on two models.
    """
    if not task_id:
        raise ModelIdentityUnresolved("the arm's receipt carries no task_id; provider identity cannot be attributed")
    path = repo / ".rift" / "tasks" / task_id / "ledger.jsonl"
    if not path.is_file():
        raise ModelIdentityUnresolved(f"no ledger at {path}; the arm claims a task whose evidence is not readable")
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelIdentityUnresolved(f"{path} could not be read as a ledger: {exc}") from None

    out: list[str] = []
    for event in rows:
        if event.get("kind") != "model_response_received":
            continue
        # Absent identity is recorded as absent. Substituting the model we
        # asked for would be fabricating the agreement being checked.
        out.append(str((event.get("payload") or {}).get("model_reported") or "").strip() or "unavailable")
    return out


def provider_identity_failure(reported: list[str], expected: str) -> str:
    """Any response from a model other than the one requested invalidates the arm.

    `unavailable` is not a mismatch — it is the absence of a claim, and the
    caller keeps it in the record as such.
    """
    wrong = [m for m in reported if m != "unavailable" and m != expected]
    if wrong:
        return (
            f"the provider reported {sorted(set(wrong))!r} across {len(reported)} response(s) "
            f"for a run requested as {expected!r}"
        )
    return ""


def git(repo: Path, *args: str) -> tuple[int, str]:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return out.returncode, out.stdout.strip()


def parent_pin_failures(case: dict, repos: Path) -> list[str]:
    """The pinned parent must be the fix commit's **direct** parent.

    Not "the range reproduces the signature". `icalendar-30ec6eef` pinned a
    parent 71 commits behind its fix and reproduced perfectly — the target
    failed at that parent and passed at the fix, so every reproduction check
    agreed. What it measured was 71 commits of unrelated change, and the fix
    commit did not touch the frozen target at all. A case like that makes the
    benchmark score a repair task nobody posed.

    Reproduction is necessary and not sufficient; the pin is what makes the
    task the one commit that was actually the fix.
    """
    repo = repos / case["repo"]
    if not (repo / ".git").exists() and not repo.is_dir():
        return [f"{case['case_id']}: repository {repo} is not available to check the parent pin"]

    code, parents = git(repo, "rev-list", "--parents", "-n", "1", case["commit"])
    if code != 0 or not parents:
        return [f"{case['case_id']}: fix commit {case['commit'][:12]} does not resolve in {case['repo']}"]
    ancestry = parents.split()[1:]
    if len(ancestry) > 1:
        return [
            f"{case['case_id']}: fix commit {case['commit'][:12]} is a merge with {len(ancestry)} parents; "
            "which one the fix is relative to is not defined, and no protocol governs that yet"
        ]
    if not ancestry:
        return [f"{case['case_id']}: fix commit {case['commit'][:12]} is a root commit and has no parent to pin"]
    if ancestry[0] != case["parent"]:
        code, count = git(repo, "rev-list", "--count", f"{case['parent']}..{case['commit']}")
        span = f" ({count} commits apart)" if code == 0 and count else ""
        return [
            f"{case['case_id']}: pinned parent {case['parent'][:12]} is not the direct parent of "
            f"{case['commit'][:12]}, which is {ancestry[0][:12]}{span}"
        ]
    return []


class Bound:
    """Every RIFT subprocess this run makes, and the identity they are bound to.

    One object rather than a parameter threaded through call sites, because a
    parameter can be forgotten at one of them — and the one that forgets is the
    one that runs unbound. Ground-truth and shadow evaluation went unpinned for
    exactly that reason: `rift` grew an `env` argument and `evaluate_under_gate`
    was not updated, so the arms ran against the frozen runtime while the
    evaluation that scores them ran against whatever resolved first.

    `check()` is the same fail-closed assertion the arms use, asked from the
    directory the invocation will actually run in.
    """

    def __init__(self, runtime_root: Path, frozen_hash: str) -> None:
        self.runtime_root = Path(runtime_root)
        self.frozen_hash = frozen_hash
        self.env = runtime_env(self.runtime_root)

    def check(self, cwd: Path, when: str) -> None:
        assert_runtime(self.runtime_root, self.frozen_hash, self.env, when, cwd)

    def run(self, args: list[str], cwd: Path, label: str, timeout: float = 3600.0) -> subprocess.CompletedProcess:
        """Check, execute, check again — in that order, inside this method.

        The previous version centralised the *plumbing* and left the invariant
        to the caller: `Bound.check` existed and had to be remembered. It was
        remembered for the arms and forgotten for ground-truth scoring, which
        pre-checked and then called `rift` directly with no check afterwards.
        A convention that has already been broken once is not an invariant.

        So both checks live here. There is no way to start a RIFT subprocess
        through this object without them, and `_rift` — the raw call — is
        private and used by nothing that scores.

        The *same* `cwd` is used for both checks and for the execution, because
        the working directory is on `sys.path`: checking from one directory and
        running in another asks a different question than the one that matters.
        """
        self.check(cwd, f"before {label}")
        proc = _rift(args, cwd=cwd, timeout=timeout, env=self.env)
        # After, too. Drift during execution means what ran is not what the
        # result would claim ran, and that cannot be detected beforehand.
        self.check(cwd, f"during {label}")
        return proc

    def supports(self, flag: str, cwd: Path) -> bool:
        """Capability probe, under the same binding as everything else."""
        if not flag:
            return True
        help_text = self.run(["fix", "--help"], cwd=cwd, label=f"capability probe {flag}", timeout=120)
        return flag in (help_text.stdout + help_text.stderr)


def _rift(
    args: list[str], cwd: Path, timeout: float = 3600.0, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """One invocation of the shipped CLI, unbound.

    Private by name. Every benchmark and scoring path goes through `Bound.run`,
    which is the only place the runtime invariant is enforced; calling this
    directly is how ground-truth scoring came to run unverified.
    """
    return subprocess.run(
        [sys.executable, "-m", "riftagent", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
        env=env,
    )


def cli_supports(flag: str, cwd: Path, env: dict[str, str] | None = None) -> bool:
    """Unbound capability probe. Retained for tooling that has no run identity
    to bind to; the benchmark uses `Bound.supports` instead."""
    if not flag:
        return True
    help_text = _rift(["fix", "--help"], cwd=cwd, timeout=120, env=env)
    return flag in (help_text.stdout + help_text.stderr)


def receipt_of(proc: subprocess.CompletedProcess | None) -> dict:
    """The CLI's receipt, identified by carrying a verdict.

    `--json` streams every ledger event as its own JSON line, so "the last JSON
    object" is the receipt only when the command completed. An arm that died
    mid-gate leaves the last *event* there instead, and taking it as the receipt
    reports the crash as whatever that event happens to lack — which is how a
    missing `task_id` came to stand in for "the arm never finished".
    """
    if proc is None or not proc.stdout.strip():
        return {}
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "verdict" in parsed:
            return parsed
    return {}


# ------------------------------------------------------------------ validation


def validate_manifest(manifest: dict, work: Path, repos: Path | None = None) -> list[str]:
    """Everything that must hold before a single request is made.

    Fail-closed and specific: each failure names the case and the missing
    property, because a validation message that only says "invalid" invites the
    reader to fix it by deleting the check.
    """
    failures: list[str] = []
    if not manifest.get("arms"):
        failures.append("manifest.arms is empty; the arms are not defined")
    if not manifest.get("budget"):
        failures.append("manifest.budget is empty; there is no authorized ceiling")
    else:
        budget = manifest["budget"]
        if not budget.get("scope"):
            failures.append("manifest.budget.scope is missing; spend cannot be attributed")
        if budget.get("max_usd") in (None, 0, 0.0):
            failures.append("manifest.budget.max_usd is unset or zero")
    if not manifest.get("model", {}).get("id"):
        failures.append("manifest.model.id is missing; the arms would not share a model")
    # Before the first paid arm, not per-arm: pricing for one model and
    # execution of another is not a defect a later check can undo.
    failures.extend(model_binding_failures(manifest))
    if not manifest.get("cases"):
        failures.append("manifest.cases is empty")

    # Each arm must be defined, not merely present, and arm B's seed must be
    # frozen in the manifest: an unrecorded seed makes a rerun of B a different
    # experiment rather than a repetition of the same one.
    arms = manifest.get("arms") or {}
    for arm in ARMS:
        if arm not in arms:
            failures.append(f"manifest.arms.{arm} is not defined")
    if "B" in arms and arms["B"].get("seed") in (None, ""):
        failures.append("manifest.arms.B.seed is missing; arm B would not be reproducible")
    # Shared across arms by definition: if these differ or are absent, the arms
    # are not comparable and no amount of running them fixes it.
    model = manifest.get("model") or {}
    for field in ("price_input_per_mtok", "price_output_per_mtok", "max_output_tokens", "max_probes", "max_attempts"):
        if model.get(field) is None:
            failures.append(f"manifest.model.{field} is missing; the arms would not share a budget")

    for case in manifest.get("cases", []):
        cid = case.get("case_id", "<unnamed>")
        if case.get("status") == "GROUND_TRUTH_DISPUTED":
            failures.append(f"{cid}: GROUND_TRUTH_DISPUTED cases may not enter the scored set")
        if not case.get("target"):
            failures.append(f"{cid}: no target")
        if not case.get("signature"):
            failures.append(f"{cid}: no expected signature to match the reproduction against")
        if not case.get("preserve"):
            failures.append(f"{cid}: preservation checks are empty; nothing would constrain a destructive patch")
        if case.get("ordering_precondition") and not case.get("reproducer"):
            failures.append(f"{cid}: order-dependent case carries no exact reproducer; the bare node id will not fail")
        worktree = case.get("worktree")
        if not worktree:
            failures.append(f"{cid}: no worktree; the manifest names a repository but no materialized checkout")
        elif not (work / worktree).is_dir() and not Path(worktree).is_dir():
            failures.append(f"{cid}: worktree {worktree!r} does not exist; materialize it before running")
        # The pin, checked against git rather than trusted. Skipped only when no
        # repository root was supplied — and then said so, rather than passing
        # silently, because an unrun check that looks like a passed check is the
        # shape of every defect this file has found.
        if repos is not None:
            failures.extend(parent_pin_failures(case, repos))
        else:
            failures.append(f"{cid}: NOT_CHECKED parent pin — no repository root was supplied")
        # The constructed tree, not just the commit. Checked here so all eight
        # are proven before the first request rather than one at a time as the
        # run proceeds — a corpus that fails on case six has already been paid
        # for through case five.
        frozen_tree = case.get("baseline_tree_hash")
        if not frozen_tree:
            failures.append(f"{cid}: no baseline_tree_hash; the tree that will execute is not frozen")
        elif worktree:
            path = (work / worktree) if (work / worktree).is_dir() else Path(worktree)
            if path.is_dir():
                observed = baseline_tree_hash(path)
                if observed != frozen_tree:
                    failures.append(
                        f"{cid}: baseline tree {observed[:12]} does not match the frozen "
                        f"{frozen_tree[:12]}; the worktree is not the one that was curated"
                    )
                code, head = git(path, "rev-parse", "HEAD")
                if code != 0 or head != case.get("parent"):
                    failures.append(f"{cid}: worktree HEAD {head[:12] or '<none>'} is not the pinned parent")
    return failures


# ------------------------------------------------------------------ arms


def probe_seed(manifest: dict, case: dict) -> int:
    """Arm B's frozen seed, stable across processes.

    Derived by SHA-256 from the manifest seed and the case id. The previous
    version used `hash(case_id)`, which Python randomises per process — a rerun
    of B would have been a different experiment, which is the one thing the
    frozen seed exists to prevent.
    """
    material = f"{manifest['arms']['B']['seed']}:{case['case_id']}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def frozen_task_args(case: dict) -> list[str]:
    """The frozen task, as CLI arguments. Two independent dimensions.

    A case may carry a precondition sequence, a frozen signature, both, or
    neither, and the four combinations are unrelated. The previous version
    emitted `--expect-signature` only when a reproducer also existed, so all
    nine preliminary cases — every one of which has a signature and no ordering
    reproducer — silently ran without the failure evidence curation had frozen
    for them. Validation checked the field was present; nothing checked it was
    used.

    Returned as one list used by every arm and every evaluation path, so a
    dimension cannot reach one caller and not another.
    """
    args: list[str] = []
    for node in case.get("reproducer") or ():
        args += ["--precondition", node]
    if case.get("signature"):
        args += ["--expect-signature", case["signature"]]
    return args


def arm_argv(arm: str, case: dict, manifest: dict, scope: str) -> list[str]:
    """The command for one arm. Differs per arm by construction."""
    model = manifest["model"]
    argv = [
        "--repo",
        case["worktree"],
        "--json",
        "fix",
        case["target"],
        "--allow-partial-sandbox",
        "--max-usd",
        str(manifest["budget"]["max_usd"]),
        "--scope",
        scope,
        "--price-input",
        str(model["price_input_per_mtok"]),
        "--price-output",
        str(model["price_output_per_mtok"]),
        "--max-output-tokens",
        str(model["max_output_tokens"]),
        "--max-probes",
        str(model["max_probes"]),
        "--max-attempts",
        str(model["max_attempts"]),
        "--max-commands",
        str(model["max_commands"]),
        "--timeout",
        str(model["timeout_s"]),
        *[a for node in case.get("preserve", []) for a in ("--preserve", node)],
        # Every arm reproduces the *same* frozen task. This is shared rather
        # than per-arm because arms that reproduce different failures are not
        # comparable, which is the whole point of the comparison.
        *frozen_task_args(case),
    ]
    if arm == "A":
        # The incumbent: same model, same context budget, no kernel diagnosis,
        # accepted on the target passing. It still receives the *same frozen
        # task* as B and C — without the reproducer its baseline runs bare, an
        # order-dependent target passes there, and arm A reports nothing to
        # repair while B and C work the real failure.
        argv.append("--model-alone")
    elif arm == "B":
        # Identical probe pool and budgets; only the selection policy differs.
        argv += ["--probe-policy", "random", "--probe-seed", str(probe_seed(manifest, case))]
    return argv


def orchestration_key(arm: str, case: dict, manifest: dict, scope: str) -> str:
    """A fingerprint of what an arm actually executes.

    Two arms with the same key are the same experiment whatever they are called.
    Tests assert these differ; that assertion is the one that would have caught
    three arms running one command.
    """
    return " ".join(arm_argv(arm, case, manifest, scope))


# ------------------------------------------------------------------ evidence


def task_dir(repo: Path, receipt: dict) -> Path | None:
    task_id = receipt.get("task_id") or receipt.get("task")
    if not task_id:
        return None
    candidate = repo / ".rift" / "tasks" / str(task_id)
    return candidate if candidate.is_dir() else None


def capture_patch(repo: Path, receipt: dict, out_dir: Path, arm: str, case_id: str) -> Path | None:
    """The exact durable patch bytes an arm produced, copied out verbatim.

    `change-set.diff` is what the runtime wrote when the patch was accepted. It
    is copied rather than regenerated: a regenerated patch is a different
    artifact, and shadow evaluation is only meaningful on the same bytes.
    """
    td = task_dir(repo, receipt)
    if td is None:
        return None
    source = td / "change-set.diff"
    if not source.is_file() or not source.read_bytes().strip():
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"{case_id}-{arm}.diff"
    destination.write_bytes(source.read_bytes())
    return destination


def spend_event_ids(repo: Path, scope: str, since: int) -> dict:
    """A reference into `.rift/spend.jsonl`, not a copy of a number.

    The ledger is authoritative. Recording ids and letting the report read the
    ledger means a figure in a report can always be traced to the events that
    produced it, and a hand-maintained total can never drift from them.
    """
    path = repo / ".rift" / "spend.jsonl"
    if not path.is_file():
        return {"ledger": str(path), "event_ids": [], "present": False}
    ids: list[int] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if index < since or not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("scope") in (scope, None):
            ids.append(index)
    return {"ledger": str(path), "event_ids": ids, "present": True}


def spend_from_ledger(reference: dict) -> float | None:
    """Sum the referenced events. Returns None when the ledger is unreadable —
    never 0.0, which would report an unmeasured run as a free one."""
    path = Path(reference.get("ledger", ""))
    if not reference.get("present") or not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    total = 0.0
    for index in reference.get("event_ids", []):
        if index >= len(lines):
            continue
        try:
            row = json.loads(lines[index])
        except json.JSONDecodeError:
            continue
        total += float(row.get("charged_usd") or 0.0)
    return total


def evaluate_under_gate(case: dict, patch: Path | None, work: Path, bound: Bound) -> dict:
    """Score a patch through C's gate with `rift verify` — the same gate a user
    gets. Used for both shadow evaluation and ground truth.

    The case's **exact frozen reproducer** is passed, never the bare target. An
    order-dependent failure passes when run alone, so a bare-target evaluation
    would report a passing baseline and score every such case as already fixed.
    That defect has surfaced four times; there is no fallback here, by design.
    """
    if patch is None:
        return {"evaluated": False, "reason": "no patch was produced"}
    cwd = work / case["worktree"] if (work / case["worktree"]).is_dir() else Path(case["worktree"])
    # Refused by name. Evaluating without a dimension the case carries would
    # silently measure a different experiment — each is checked against the
    # flag it actually needs, not against the other one.
    # The evaluation that decides ground truth runs under the same binding as
    # the arm it scores, or the two are not comparable. `Bound.run` checks
    # before and after each of these invocations, including the probes.
    if case.get("reproducer") and not bound.supports("--precondition", cwd):
        return {"evaluated": False, "reason": "NOT_RUN_REPRODUCER_UNSUPPORTED"}
    if case.get("signature") and not bound.supports("--expect-signature", cwd):
        return {"evaluated": False, "reason": "NOT_RUN_SIGNATURE_UNSUPPORTED"}
    proc = bound.run(
        label=f"ground-truth evaluation of {case['case_id']}",
        args=[
            "--repo",
            str(cwd),
            "--json",
            "verify",
            str(patch),
            case["target"],
            "--allow-partial-sandbox",
            *frozen_task_args(case),
            *[a for node in case.get("preserve", []) for a in ("--preserve", node)],
        ],
        cwd=cwd,
    )
    verdict = receipt_of(proc).get("verdict")
    return {"evaluated": True, "verdict": verdict, "exit_code": proc.returncode}


def record(case: dict, arm: str, proc: subprocess.CompletedProcess | None, extra: dict) -> dict:
    receipt = receipt_of(proc)
    diagnosis = receipt.get("diagnosis") if isinstance(receipt.get("diagnosis"), dict) else {}
    return {
        "case_id": case["case_id"],
        "arm": arm,
        "label": case["label"],
        "cause_class": case["cause_class"],
        "status": case.get("status", "OK"),
        "expected_diagnostic_scope": case.get("expected_diagnostic_scope"),
        "verdict": receipt.get("verdict"),
        "repair_basis": receipt.get("repair_basis"),
        # The phase a lost case was lost at. This is the measurement that decides
        # whether a bounded repair loop is justified by data rather than by
        # intuition, so it is recorded even when the case succeeded.
        "failed_phase": receipt.get("rejected_phase"),
        "support": diagnosis.get("support"),
        "gate": diagnosis.get("gate"),
        "commands": receipt.get("commands"),
        "seconds": receipt.get("seconds"),
        "tokens": receipt.get("tokens"),
        "exit_code": None if proc is None else proc.returncode,
        **extra,
    }


# ------------------------------------------------------------------ report


def report(records: list[dict], manifest: dict) -> dict:
    """Every figure recomputed from the raw records. No stored aggregate."""
    scored = [r for r in records if r["status"] == "OK"]
    excluded = [r for r in records if r["status"] != "OK"]
    out: dict = {
        "per_arm": {},
        "excluded": [{"case_id": r["case_id"], "status": r["status"]} for r in excluded],
        "arms_refused": sorted({r["arm"] for r in records if r.get("arm_unavailable")}),
    }

    for arm in ARMS:
        rows = [r for r in scored if r["arm"] == arm]
        gateable = [r for r in rows if r["label"] == "gateable"]
        accepted = [r for r in rows if r.get("accepted")]
        correct = [r for r in accepted if r.get("ground_truth_correct")]
        observational = [r for r in rows if r["label"] == "observationally_diagnosable"]
        diagnosed = [r for r in observational if r.get("gate") == "not_applicable" and r.get("support")]

        charges = [spend_from_ledger(r.get("spend_reference") or {}) for r in rows]
        measured = [c for c in charges if c is not None]
        charged = sum(measured) if measured else None
        out["per_arm"][arm] = {
            "attempted": len(rows),
            # Abstentions are attempted tasks and stay in the denominator.
            "gateable_attempted": len(gateable),
            "accepted": len(accepted),
            "correct": len(correct),
            "false_fix_acceptance": (len(accepted) - len(correct)) / len(accepted) if accepted else None,
            "verified_fix_yield": len(correct) / len(gateable) if gateable else None,
            "observational_diagnosis_yield": len(diagnosed) / len(observational) if observational else None,
            # Zero correct outcomes is undefined, never zero.
            "cost_per_correct_fix": (charged / len(correct)) if correct and charged is not None else None,
            "cost_per_correct_fix_note": None if (correct and charged is not None) else "undefined",
            "charged_usd": None if charged is None else round(charged, 8),
            "charged_usd_note": None if charged is not None else "not measured: spend ledger unreadable",
            "spend_source": ".rift/spend.jsonl, by referenced event id",
            "failed_phases": sorted({r["failed_phase"] for r in rows if r.get("failed_phase")}),
        }

    shadows = [r for r in scored if r.get("arm") == "A" and (r.get("shadow") or {}).get("evaluated")]
    out["shadow_evaluation"] = {
        "arm_a_patches_evaluated": len(shadows),
        "accepted_by_c_gate": sum(
            1 for r in shadows if (r.get("shadow") or {}).get("verdict") == "verified_against_approved_checks"
        ),
        "note": (
            "Acceptance authority in isolation: arm A's own accepted patch bytes, re-scored under "
            "C's gate without re-proposing. A-versus-C remains the complete product effect."
        ),
    }
    return out


# ------------------------------------------------------------------ main


def main() -> int:
    parser = argparse.ArgumentParser(prog="bm06-driver")
    parser.add_argument("--manifest", default="benchmark/bm06/manifest.json")
    parser.add_argument("--out", default="benchmark/bm06/results.json")
    parser.add_argument("--work", default=".", help="root the manifest's worktree paths resolve against")
    parser.add_argument("--patches", default="benchmark/bm06/patches")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--repos", default="/repos", help="root holding the source repositories, for the parent pin")
    parser.add_argument("--runtime-root", default=".", help="root the governed runtime pattern resolves against")
    args = parser.parse_args()

    # ---------------------------------------------------------------- identity
    # Read once, here, before anything executes. Three identities, one snapshot.
    #
    # A result describes an experiment run by a *particular runtime* against a
    # *particular manifest* through a *particular driver*. Binding only the
    # manifest was the gap: 226 lines of behavioural runtime change landed while
    # the manifest SHA stayed the same, so two results with identical stamps
    # could describe products that behave differently.
    #
    # Re-reading any of these after the run to stamp the output is the TOCTOU
    # defect `load_manifest` already documents, one level up: the files are the
    # things that may have changed.
    manifest, frozen_manifest_hash = load_manifest(Path(args.manifest))
    frozen_runtime_hash, runtime_files = runtime_hash(Path(args.runtime_root))
    frozen_driver_hash = file_hash(Path(__file__))
    identity = {
        "manifest_hash": frozen_manifest_hash,
        "runtime_hash": frozen_runtime_hash,
        "driver_hash": frozen_driver_hash,
        "runtime_files": runtime_files,
        "runtime_pattern": GOVERNED_RUNTIME,
        # Both, always, and equal for an authorized run. Recording only the
        # manifest's model is what let the two diverge unnoticed.
        "manifest_model": (manifest.get("model") or {}).get("id", ""),
        "configured_model": configured_model(),
    }
    work = Path(args.work)

    if args.report_only:
        results = load(Path(args.out))
        # Here re-reading is the point: the files on disk now are being compared
        # against the identities the run recorded.
        mismatches = [
            (name, results.get(name, ""), observed)
            for name, observed in (
                ("manifest_hash", frozen_manifest_hash),
                ("runtime_hash", frozen_runtime_hash),
                ("driver_hash", frozen_driver_hash),
            )
            if results.get(name, "") != observed
        ]
        if mismatches:
            # Fail closed on any of the three. A report derived under one
            # runtime and printed under another attributes its numbers to a
            # product that did not produce them — and a reader has no way to
            # see that from the report.
            print("REFUSING TO REPORT — the run identity does not match what is on disk:")
            for name, stamped, observed in mismatches:
                print(f"  results.json {name:14}: {stamped or '<absent>'}")
                print(f"  observed now {name:14}: {observed}")
            return 2
        print(json.dumps(report(results["records"], manifest), indent=1, sort_keys=True))
        return 0

    failures = validate_manifest(manifest, work, Path(args.repos))
    if failures:
        print("MANIFEST INVALID — no provider request was made:")
        for failure in failures:
            print(f"  {failure}")
        return 2
    if args.validate_only:
        print("manifest valid")
        return 0

    scope = manifest["budget"]["scope"]
    bound = Bound(Path(args.runtime_root), frozen_runtime_hash)
    # Before anything is probed, let alone spent: the runtime that is about to
    # execute must be the one that was frozen, and it must be the one that will
    # actually be imported — asked from where it will run.
    bound.check(work, "at startup")
    available = {arm: bound.supports(ARM_REQUIRES[arm], work) for arm in ARMS}
    for arm, ok in available.items():
        if not ok:
            print(f"arm {arm}: NOT_RUN_ARM_UNSUPPORTED — the shipped CLI has no {ARM_REQUIRES[arm]}", flush=True)

    records: list[dict] = []
    for case in manifest["cases"]:
        repo = work / case["worktree"] if (work / case["worktree"]).is_dir() else Path(case["worktree"])
        # The tree that will actually execute, frozen now. `git rev-parse HEAD`
        # describes a commit; several cases lay the fix commit's test half over
        # the parent's source, so the commit is true and insufficient.
        # From the manifest, not measured now. A hash computed at startup
        # describes whatever the tree happened to be at startup, which is the
        # question it was meant to answer independently.
        frozen_tree = case["baseline_tree_hash"]
        head_code, head = git(repo, "rev-parse", "HEAD")
        if head_code != 0 or (case.get("parent") and head != case["parent"]):
            print(f"{case['case_id']}: ABORT — worktree HEAD {head[:12]} is not the pinned parent", flush=True)
            return 2
        patches: dict[str, Path | None] = {}
        for arm in ARMS:
            if not available[arm]:
                # Refused by name. Never substituted with another arm's command.
                records.append(record(case, arm, None, {"arm_unavailable": ARM_REQUIRES[arm], "accepted": False}))
                continue
            observed_tree = baseline_tree_hash(repo)
            if observed_tree != frozen_tree:
                print(
                    f"{case['case_id']} arm {arm}: ABORT — the baseline tree changed before this arm "
                    f"(frozen {frozen_tree[:12]}, observed {observed_tree[:12]})",
                    flush=True,
                )
                return 2
            spend_path = repo / ".rift" / "spend.jsonl"
            before = len(spend_path.read_text(encoding="utf-8").splitlines()) if spend_path.is_file() else 0
            # Both identity checks are inside `Bound.run`, so no arm can be
            # started or scored without them.
            try:
                proc = bound.run(arm_argv(arm, case, manifest, scope), cwd=repo, label=f"{case['case_id']} arm {arm}")
            except RuntimeDrift as drift:
                print(f"ABORT — {drift}; the run is invalid and no report will be written", flush=True)
                return 2
            receipt = receipt_of(proc)
            if not receipt:
                # No verdict at all means the arm did not finish. Say that,
                # rather than reporting whichever downstream check noticed first.
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
                print(
                    f"{case['case_id']} arm {arm}: ABORT — the arm emitted no receipt "
                    f"(exit {proc.returncode}); it did not complete. Last output: {' | '.join(tail) or '<none>'}",
                    flush=True,
                )
                return 2
            patches[arm] = capture_patch(repo, receipt, Path(args.patches), arm, case["case_id"])
            extra: dict = {
                # Acceptance is the arm's own verdict, read from the receipt.
                # A process exit code says whether the program ran, not whether
                # a patch was accepted.
                "accepted": receipt.get("verdict") in ("verified_against_approved_checks", "accepted_by_target_pass"),
                "patch": None if patches[arm] is None else str(patches[arm]),
                "spend_reference": spend_event_ids(repo, scope, before),
                "orchestration": orchestration_key(arm, case, manifest, scope),
            }
            # Ground truth is an independent evaluation of the arm's patch, never
            # the arm's own opinion of itself and never a return code.
            try:
                truth = evaluate_under_gate(case, patches[arm], work, bound)
            except RuntimeDrift as drift:
                print(f"ABORT — {drift}; scoring ran against a different runtime", flush=True)
                return 2
            extra["ground_truth_evaluation"] = truth
            extra["ground_truth_correct"] = truth.get("verdict") == "verified_against_approved_checks"
            if arm == "A":
                extra["shadow"] = truth
            extra["baseline_tree_hash"] = frozen_tree
            # Three identities, kept apart. `priced_models` is what the spend
            # ledger charged under — our own configuration echoed back — and is
            # recorded as configured evidence, never as the provider's word.
            extra["priced_models"] = priced_models(repo, scope) or ["unavailable"]
            try:
                reported = provider_reported_models(repo, str(receipt.get("task_id") or ""))
            except ModelIdentityUnresolved as exc:
                print(f"{case['case_id']} arm {arm}: ABORT — {exc}", flush=True)
                return 2
            extra["provider_reported_models"] = reported or ["unavailable"]
            problem = provider_identity_failure(reported, identity["configured_model"])
            if problem:
                print(f"{case['case_id']} arm {arm}: ABORT — {problem}; the run is invalid", flush=True)
                return 2
            records.append(record(case, arm, proc, extra))

            # Restore the baseline before the next arm, then prove it. An arm
            # that leaves the tree changed makes the next arm a different
            # experiment, and the comparison between them meaningless.
            git(repo, "checkout", "--", ".")
            git(repo, "clean", "-qfd", ":!.rift")
            restored = baseline_tree_hash(repo)
            if restored != frozen_tree:
                print(
                    f"{case['case_id']} arm {arm}: ABORT — the baseline could not be restored after this arm "
                    f"(frozen {frozen_tree[:12]}, restored {restored[:12]})",
                    flush=True,
                )
                return 2

    # Stamped from the startup snapshot, never re-read. A file edited during the
    # run is exactly why this must not be recomputed here.
    payload = {**identity, "records": records}
    Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report(records, manifest), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
