"""BM-08 environment equivalence: repository code is network-denied, the controller is not.

BM-08-v5 admitted every case with repository execution network-denied. The paid-path
preflight then ran some benchmark evaluation paths with network available, and
`dnspython`'s live-resolver tests behaved differently in the two environments. The
symptom was a preservation mismatch; the risk was worse — an oracle or weak evaluation
reaching the live network could disagree with a network-denied gate for reasons having
nothing to do with the candidate, manufacturing a gate-versus-truth result out of
infrastructure.

The root cause was not a design flaw. RIFT already confines repository children with
`bwrap --unshare-net` when bubblewrap is usable; the reference image did not contain it,
so RIFT degraded to `PARTIAL` — "no filesystem or network confinement". `src/riftagent`
is unchanged; the environment now supplies what RIFT already expects.

These tests pin the boundary and the identity that records it. They do not require the
isolation mechanism to be present, because the suite must also run on developer
machines; where it is absent they assert the harness *refuses* rather than silently
degrading, which is the property that actually matters.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).parents[1] / "benchmark"
BM08 = BENCH / "bm08"
if str(BM08) not in sys.path:
    sys.path.append(str(BM08))

import confinement  # noqa: E402
import execution_environment as environment  # noqa: E402

HAVE_UNSHARE = shutil.which("unshare") is not None
needs_unshare = pytest.mark.skipif(not HAVE_UNSHARE, reason="unshare unavailable on this host")


def manifest() -> dict:
    return json.loads((BM08 / "manifest-executable-v5.json").read_text(encoding="utf-8"))


def source(name: str) -> str:
    return (BM08 / name).read_text(encoding="utf-8")


# --------------------------------------------------------- the boundary, behaviourally


@needs_unshare
def test_a_repository_subprocess_cannot_reach_the_network(tmp_path):
    ok, detail = confinement.prove_isolation(sys.executable, tmp_path)
    assert ok, f"repository-side socket reached the network: {detail}"


@needs_unshare
def test_the_controller_is_not_confined_by_the_boundary():
    """Confining the whole controller would satisfy 'denied' and break the run.

    Arm C performs provider communication and repository execution inside one
    invocation, so confinement must wrap children only.
    """
    wrapped = confinement.confine([sys.executable, "-c", "pass"])
    assert wrapped[: len(confinement.UNSHARE_ARGV)] == list(confinement.UNSHARE_ARGV)
    assert wrapped[len(confinement.UNSHARE_ARGV) :] == [sys.executable, "-c", "pass"]
    # This test process is itself unconfined and can still spawn plain children.
    proc = subprocess.run([sys.executable, "-c", "print('controller ok')"], capture_output=True, text=True)
    assert proc.returncode == 0 and "controller ok" in proc.stdout


def test_isolation_is_never_silently_downgraded(monkeypatch):
    """A missing mechanism raises. It does not fall through to an unconfined run."""
    monkeypatch.setattr(confinement.shutil, "which", lambda name: None)
    with pytest.raises(confinement.IsolationUnavailable):
        confinement.confine([sys.executable, "-c", "pass"])


def test_isolation_is_proven_by_a_socket_not_by_a_flag():
    src = source("confinement.py")
    assert "socket.create_connection" in src
    assert "NETWORK_DENIED" in src and "NETWORK_REACHED" in src
    probes = source("prove_isolation.py")
    assert "socket.create_connection" in probes


def test_every_benchmark_pytest_path_uses_the_boundary():
    """Oracle/truth, Arm-A weak, preflight and corpus validation all route through it."""
    assert "confinement.run_repository_check" in source("bm08_oracle.py")
    assert source("validate_cases.py").count("confinement.run_repository_check") >= 2


def test_arm_c_confinement_is_rifts_own_sandbox_not_a_controller_wrapper():
    sandbox = (Path(__file__).parents[1] / "src" / "riftagent" / "sandbox.py").read_text(encoding="utf-8")
    assert "--unshare-net" in sandbox
    assert "confinement" not in source("bm08_runner.py"), "the controller must never be network-confined"


# ------------------------------------------------------ execution-environment identity


def test_the_environment_hash_is_deterministic():
    record = environment.describe()
    first = environment.environment_hash(record)
    assert first == environment.environment_hash(record)
    assert len(first) == 64


def test_the_environment_hash_binds_every_component_that_could_change_a_test_result():
    record = environment.describe()
    baseline = environment.environment_hash(record)
    for field in ("container_image_digest", "python_version", "docker_security_flags", "platform"):
        assert field in record, field
        moved = {**record, field: "changed"}
        assert environment.environment_hash(moved) != baseline, f"{field} does not affect the hash"
    for nested in ("harness_confinement", "rift_repository_isolation", "network_policy"):
        assert nested in record, nested
        moved = {**record, nested: {"changed": True}}
        assert environment.environment_hash(moved) != baseline, f"{nested} does not affect the hash"


def test_the_manifest_binds_the_execution_environment():
    exe = manifest()
    assert len(exe["execution_environment_hash"]) == 64
    recorded = exe["execution_environment"]
    assert environment.environment_hash(recorded) == exe["execution_environment_hash"]
    assert recorded["network_policy"]["repository_controlled_execution"] == "denied"
    assert recorded["network_policy"]["provider_and_controller"] == "allowed"


def test_an_environment_mismatch_blocks_before_any_provider_call_and_before_scoring():
    import bm08_runner

    src = Path(bm08_runner.__file__).read_text(encoding="utf-8")
    identity = src.split("def identity_problems(")[1].split("\ndef ")[0]
    assert "environment.environment_hash()" in identity and "environment identity" in identity
    aggregate = src.split("def aggregate(")[1].split("\ndef ")[0]
    assert "the execution environment changed after execution" in aggregate


# ------------------------------------------------------------- complete result identity


def test_every_result_record_carries_the_full_identity_set():
    import bm08_runner

    fields = set(bm08_runner.ArmRecord.__dataclass_fields__)
    for required in (
        "case_id",
        "arm",
        "runtime_hash",
        "driver_hash",
        "runner_hash",
        "oracle_hash",
        "manifest_hash",
        "corpus_manifest_hash",
        "repository_population_hash_v5",
        "exclusion_set_hash",
        "execution_environment_hash",
        "requested_model",
    ):
        assert required in fields, required


def test_aggregation_rejects_mixed_corpus_or_environment_identity():
    import bm08_runner

    aggregate = Path(bm08_runner.__file__).read_text(encoding="utf-8").split("def aggregate(")[1]
    for field in (
        "corpus_manifest_hash",
        "repository_population_hash_v5",
        "exclusion_set_hash",
        "execution_environment_hash",
    ):
        assert field in aggregate, f"aggregation does not verify {field}"


# --------------------------------------------------------------------- budget authority


def test_the_derived_reservation_is_unchanged_at_forty_eight_cents():
    import bm08_runner

    assert bm08_runner.required_reservation(manifest()) == pytest.approx(0.48)


def test_the_ceiling_covers_every_official_arm():
    import bm08_runner

    exe = manifest()
    worst = bm08_runner.required_reservation(exe) * len(exe["cases"]) * len(bm08_runner.OFFICIAL_ARMS)
    assert worst == pytest.approx(23.04)
    assert exe["budget"]["total_usd_ceiling"] >= worst
    assert bm08_runner.budget_authority_problems(exe) == []


def test_an_insufficient_ceiling_blocks_before_the_first_provider_call():
    """The $15.00 ceiling could not have reserved 48 arms; it would have stranded the
    run part-way through with money already spent."""
    import bm08_runner

    starved = json.loads(json.dumps(manifest()))
    starved["budget"]["total_usd_ceiling"] = 15.0
    problems = bm08_runner.budget_authority_problems(starved)
    assert problems and any("23.04" in p for p in problems)


# ------------------------------------------------------------ manifest and arm authority


def test_official_arms_are_exactly_a_and_c():
    import bm08_runner

    assert bm08_runner.OFFICIAL_ARMS == ("A", "C")
    assert set(manifest()["arms"]) == {"A", "C"}


def test_arm_membership_alone_would_have_admitted_b():
    """A,B,C satisfies 'A and C are present' and silently turns 48 records into 72."""
    import bm08_driver

    three = json.loads(json.dumps(manifest()))
    three["arms"]["B"] = "random-probe ablation"
    assert any("exactly A and C" in p for p in bm08_driver.validate_manifest(three))


def test_expected_official_records_is_forty_eight():
    exe = manifest()
    assert exe["expected_official_records"] == 48 == len(exe["cases"]) * 2


def test_the_official_command_resolves_to_the_v5_manifest():
    src = source("bm08_runner.py")
    assert "manifest-executable-v5.json" in src
    assert not (BM08 / "manifest-executable.json").exists(), "the stale v3 manifest must not exist"


def test_the_preflight_wording_is_not_hard_coded_to_six_cases():
    src = source("bm08_runner.py")
    assert "six-case preflight" not in src
    assert "mandatory preflight failed" in src


# -------------------------------------------------------- corpus and dnspython evidence


def test_the_corpus_identities_are_unchanged_by_the_environment_correction():
    exe = manifest()
    assert exe["corpus_manifest_hash"] == "e6bdd3f116981bc58daf7f21eb4a5e0a524e9a067227cd2cc40fc994a19ad3f9"
    assert exe["repository_population_hash_v5"] == "4645de61c549bf8ad06697e1b8279ddfee51d19af24379e1dd45880f350fe0bc"
    assert exe["exclusion_set_hash"] == "d4090113b0670321b1d5a9c48ebe3949adeb60f865e8b07bb414aea21f137e87"


def test_dnspython_is_still_in_the_corpus_with_its_divergent_nodes():
    """The case that exposed the defect was neither dropped nor edited."""
    case = next(c for c in manifest()["cases"] if c["case_id"] == "dnspython-246febc4")
    nodes = set(case["preservation_nodes"])
    assert "tests/test_resolver.py::LiveResolverTests::testResolveAddress" in nodes
    assert "tests/test_resolver.py::LiveResolverTests::testCanonicalNameDangling" in nodes


def test_the_isolated_preflight_evidence_shows_all_twenty_four_passing():
    log = (BM08 / "preflight-v5-isolated.log").read_text(encoding="utf-8")
    assert "ISOLATION PROVEN" in log
    assert "ALL 24 CASES PASS" in log
    assert "preflight failures: 0" in log
    for path in (
        "harness confinement",
        "oracle / truth path",
        "Arm A / weak path",
        "mandatory preflight path",
        "Arm C repository path",
    ):
        assert path in log, path
    assert "REACHED" in log.split("CONTROLLER SCOPING")[1], "controller scope was not demonstrated"


# ----------------------------------------------------------------- the frozen trees


def test_the_frozen_rift_runtime_is_unchanged_by_the_environment_correction():
    """The correction was environmental. `src/riftagent` must still hash to the
    value the manifest was built against."""
    import bm08_driver

    assert bm08_driver.observed_runtime_hash() == manifest()["runtime_hash"]
    assert manifest()["runtime_hash"] == "75196d8756b749d6105e520c97e927a8f8bd57dccc70e726aabf00a955775b26"


def test_bm07_is_untouched():
    """BM-07 is a paid, frozen result. Editing it would invalidate its own evidence.

    Ordering is by POSIX-relative path rather than by `Path` object: `Path`
    comparison is case-insensitive on Windows and case-sensitive on Linux, so
    sorting the objects produces a different digest per host and the assertion
    would fail for a reason that has nothing to do with BM-07 changing.
    """
    import hashlib

    root = BENCH / "bm07"
    items = sorted(
        (path.relative_to(root).as_posix(), path)
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    digest = hashlib.sha256()
    for relative, path in items:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    assert len(items) == 27
    assert digest.hexdigest() == "2202e0744d7c6ebe7c5f91971935481e8aea8fc923251c83cc1632cc92d7efb6"
