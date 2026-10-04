"""Commitments and the verifier, with the workload run in-process (no Docker or chain needed)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from stesh_mvp import verifier
from stesh_mvp.chain import dev_keys
from stesh_mvp.commitments import build_manifest, merkle_root, sign_manifest
from stesh_mvp.crypto import address_of, sha256_file
from stesh_mvp.jobs import InputRef, JobSpec, Requirements, new_nonce, sign_job
from stesh_mvp.scenario import DATA, PARAMS, fabricate_outputs
from stesh_mvp.workloads import logreg_sgd

CODER, PROVIDER, OTHER = dev_keys(3)
SECRET = b"s"


def make_job(params=PARAMS):
    return sign_job(JobSpec(
        chain_id=31337, escrow=address_of(OTHER), coder=address_of(CODER), nonce=new_nonce(), workload="logreg-sgd@1",
        params=params, inputs=(InputRef(name="data.csv", sha256=sha256_file(DATA), size=DATA.stat().st_size),),
        requirements=Requirements(cpu_cores=1, ram_mb=512, runtime="python3.12-numpy2"), timeout_s=60,
        expected_outputs=("model.json", "metrics.json"), payment_wei=1, deadline=2_000_000_000), CODER)


def run_inprocess(job, tmp: Path) -> Path:
    inp, out = tmp / "in", tmp / "out"
    inp.mkdir(parents=True)
    shutil.copyfile(DATA, inp / "data.csv")
    (inp / "job.json").write_text(json.dumps({"job_id": job.job_id, "params": job.spec.params}))
    assert logreg_sgd.main(str(inp), str(out)) == 0
    return out


def check(job, out, key=PROVIDER, assigned=None, onchain=None, k=None):
    signed = sign_manifest(build_manifest(job.job_id, address_of(key), job.spec.inputs, out), key)
    return verifier.verify(job, signed, out, assigned or address_of(PROVIDER),
                           onchain or signed.manifest.result_hash(), {"data.csv": DATA}, SECRET, k), signed


@pytest.fixture()
def honest(tmp_path):
    job = make_job()
    return job, run_inprocess(job, tmp_path)


def test_honest_result_is_accepted(honest):
    rep, _ = check(*honest)
    assert rep.accepted, rep.failures
    assert rep.sampled_epochs == list(range(1, PARAMS["epochs"] + 1))


def test_changed_artifact_after_commit_is_rejected(honest):
    job, out = honest
    signed = sign_manifest(build_manifest(job.job_id, address_of(PROVIDER), job.spec.inputs, out), PROVIDER)
    (out / "metrics.json").write_text('{"accuracy": 1.0}\n')
    rep = verifier.verify(job, signed, out, address_of(PROVIDER), signed.manifest.result_hash(), {"data.csv": DATA}, SECRET)
    assert not rep.accepted and any("hash metrics.json" in f for f in rep.failures)


def test_removed_and_substituted_artifacts_are_rejected(honest, tmp_path):
    job, out = honest
    signed = sign_manifest(build_manifest(job.job_id, address_of(PROVIDER), job.spec.inputs, out), PROVIDER)
    (out / "checkpoints" / "epoch-0007.json").unlink()
    rep = verifier.verify(job, signed, out, address_of(PROVIDER), signed.manifest.result_hash(), {"data.csv": DATA}, SECRET)
    assert not rep.accepted and any("artifact set" in f for f in rep.failures)
    # Substituting a file from a different (valid) run, recommitted: the re-execution catches it.
    other = run_inprocess(make_job({**PARAMS, "seed": 8}), tmp_path / "other")
    shutil.copyfile(other / "checkpoints" / "epoch-0007.json", out / "checkpoints" / "epoch-0007.json")
    rep, _ = check(job, out)
    assert not rep.accepted and any("epoch" in f for f in rep.failures)


def test_result_from_another_job_is_rejected(honest, tmp_path):
    job, out = honest
    new_job = make_job()  # same work, different job (nonce)
    old_signed = sign_manifest(build_manifest(job.job_id, address_of(PROVIDER), job.spec.inputs, out), PROVIDER)
    rep = verifier.verify(new_job, old_signed, out, address_of(PROVIDER), old_signed.manifest.result_hash(),
                          {"data.csv": DATA}, SECRET)
    assert not rep.accepted and any("job binding" in f for f in rep.failures)


def test_manifest_signed_by_unassigned_party_is_rejected(honest):
    rep, _ = check(*honest, key=OTHER)
    assert not rep.accepted and any("provider signature" in f for f in rep.failures)


def test_onchain_hash_must_match_manifest(honest):
    rep, _ = check(*honest, onchain="0x" + "00" * 32)
    assert not rep.accepted and any("on-chain commitment" in f for f in rep.failures)


def test_fabricated_but_consistently_committed_result_is_rejected(tmp_path):
    job = make_job()
    out = tmp_path / "out"
    fabricate_outputs(out, PARAMS)
    rep, _ = check(job, out)
    assert not rep.accepted
    assert any(f.startswith("epoch") for f in rep.failures)       # transitions don't follow from the data
    assert any("metrics recomputed" in f for f in rep.failures)   # reported metrics are invented


def test_single_falsified_epoch_is_caught_with_full_reexecution(honest):
    job, out = honest
    p = out / "checkpoints" / "epoch-0012.json"
    s = json.loads(p.read_text())
    s["w"][0] += 1e-3
    p.write_text(json.dumps(s, sort_keys=True) + "\n")
    rep, _ = check(job, out)
    assert not rep.accepted and any("epoch 12" in f or "epoch 13" in f for f in rep.failures)


def test_sampling_is_fixed_after_commitment_and_its_miss_probability_is_exact():
    a = verifier.challenge_epochs(b"k", "0xaa", 20, 5)
    assert a == verifier.challenge_epochs(b"k", "0xaa", 20, 5) and len(set(a)) == 5
    assert a != verifier.challenge_epochs(b"k", "0xab", 20, 5)
    assert verifier.miss_probability(20, 1, 5) == pytest.approx(15 / 20)
    assert verifier.miss_probability(20, 1, 20) == 0.0


def test_unknown_workload_fails_closed(honest):
    job, out = honest
    other = job.spec.model_copy(update={"workload": "mystery@1"})
    rep, _ = check(sign_job(other, CODER), out)
    assert not rep.accepted and any("failing closed" in f for f in rep.failures)


def test_merkle_root_is_order_independent_and_domain_separated():
    a = merkle_root([("a", "00" * 32), ("b", "11" * 32), ("c", "22" * 32)])
    assert a == merkle_root([("c", "22" * 32), ("a", "00" * 32), ("b", "11" * 32)])
    assert a != merkle_root([("a", "00" * 32), ("b", "11" * 32), ("c", "22" * 32), ("c", "22" * 32)])
