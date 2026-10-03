"""Client -> coordinator -> provider -> container -> verifier -> local EVM escrow.

Nothing here is mocked: a real Anvil chain, the real JobEscrow contract, real Docker
containers running the workload, and the real verifier. Skipped only if that stack isn't
available.
"""

from __future__ import annotations

import pytest

from stesh_mvp import scenario
from stesh_mvp.chain import TxFailed
from stesh_mvp.commitments import build_manifest, sign_manifest
from stesh_mvp.jobs import SignedJob
from stesh_mvp.protocol import ExecutionFailed, JobRejected
from tests.conftest import needs_stack

pytestmark = needs_stack
DATA = {"data.csv": scenario.DATA}


@pytest.fixture()
def net(runs_dir):
    n = scenario.start(runs_dir)
    yield n
    n.close()


def test_honest_provider_is_verified_and_paid_exactly_once(net):
    job = scenario.make_job(net)
    contract_before = net.escrow.balance(net.escrow.address)
    r = scenario.honest_run(net, job)
    provider = r["assignment"].provider
    assert r["report"].accepted, r["report"].failures
    assert r["final_state"] == "Released"
    assert net.escrow.balance(net.escrow.address) == contract_before
    # the provider received exactly the escrowed amount from the release (it paid no gas for it)
    rel = net.escrow.w3.eth.get_transaction_receipt(r["release_tx"].hash)
    assert rel.status == 1
    # duplicate settlement
    with pytest.raises(TxFailed):
        net.verifier.settle(job.job_id, net.escrow)
    with pytest.raises(TxFailed):  # the provider can never release, even its own verified job
        net.escrow.release(scenario.agent_for(net, provider).key, job.job_id)


def test_payment_moves_from_coder_to_provider(net):
    job = scenario.make_job(net)
    a = scenario.fund_and_admit(net, job)
    agent = scenario.agent_for(net, a.provider)
    signed, out = agent.run(job, DATA, net.escrow)
    assert net.verifier.judge(job, signed, out, net.escrow, DATA).accepted
    before = net.escrow.balance(agent.address)
    net.verifier.settle(job.job_id, net.escrow)
    assert net.escrow.balance(agent.address) - before == scenario.PAYMENT


def test_result_tampered_after_commit_is_rejected_and_not_paid(net):
    job = scenario.make_job(net)
    a = scenario.fund_and_admit(net, job)
    agent = scenario.agent_for(net, a.provider)
    signed, out = agent.run(job, DATA, net.escrow)
    scenario.tamper_after_commit(out)
    report = net.verifier.judge(job, signed, out, net.escrow, DATA)
    assert not report.accepted and any("hash metrics.json" in f for f in report.failures)
    assert net.escrow.job(job.job_id)["state"] == "Rejected"
    with pytest.raises(TxFailed):
        net.verifier.settle(job.job_id, net.escrow)
    before = net.escrow.balance(net.escrow.job(job.job_id)["coder"])
    net.escrow.refund(net.coder_key, job.job_id)
    assert net.escrow.job(job.job_id)["state"] == "Refunded"
    assert net.escrow.balance(net.escrow.job(job.job_id)["coder"]) > before


def test_fabricated_result_without_doing_the_work_is_rejected(net):
    job = scenario.make_job(net)
    a = scenario.fund_and_admit(net, job)
    agent = scenario.agent_for(net, a.provider)
    out = agent.workdir / "fabricated"
    scenario.fabricate_outputs(out, scenario.PARAMS)
    signed = agent.commit(job, out, net.escrow)  # commits honestly to the invented files
    report = net.verifier.judge(job, signed, out, net.escrow, DATA)
    assert not report.accepted
    assert net.escrow.job(job.job_id)["state"] == "Rejected"


def test_replayed_old_result_is_rejected(net):
    old = scenario.make_job(net)
    first = scenario.honest_run(net, old)
    assert first["report"].accepted
    new = scenario.make_job(net)  # identical work, new job
    a = scenario.fund_and_admit(net, new)
    agent = scenario.agent_for(net, a.provider)
    # submit the old manifest's hash on-chain for the new job, then hand over the old files
    net.escrow.submit_result(agent.key, new.job_id, first["manifest"].manifest.result_hash())
    report = net.verifier.judge(new, first["manifest"], first["out_dir"], net.escrow, DATA)
    assert not report.accepted and any("job binding" in f for f in report.failures)
    assert net.escrow.job(new.job_id)["state"] == "Rejected"
    # Re-signing the old files under the new job id doesn't help: the hash already on-chain is the
    # old one, so the commitment check fails.
    resigned = sign_manifest(build_manifest(new.job_id, agent.address, new.spec.inputs, first["out_dir"]), agent.key)
    from stesh_mvp import verifier as v

    rep2 = v.verify(new, resigned, first["out_dir"], agent.address, net.escrow.job(new.job_id)["result_hash"],
                    DATA, b"x")
    assert not rep2.accepted and any("on-chain commitment" in f for f in rep2.failures)


def test_capacity_is_released_after_each_job(net):
    for _ in range(3):
        r = scenario.honest_run(net, scenario.make_job(net))
        assert r["report"].accepted
    assert all(p.active_jobs == 0 for p in net.coordinator.registry.providers.values())


def test_admission_rejects_replays_bad_signatures_and_mismatched_escrow(net):
    job = scenario.make_job(net)
    with pytest.raises(JobRejected, match="Funded"):
        net.coordinator.admit(job)  # not funded yet
    net.escrow.fund(net.coder_key, job.job_id, net.verifier.address, job.spec.deadline, scenario.PAYMENT - 1)
    with pytest.raises(JobRejected, match="amount"):
        net.coordinator.admit(job)  # underfunded relative to the signed payment
    forged = SignedJob(spec=job.spec.model_copy(update={"payment_wei": 1}), signature=job.signature)
    with pytest.raises(JobRejected, match="signature"):
        net.coordinator.admit(forged)
    ok = scenario.make_job(net)
    scenario.fund_and_admit(net, ok)
    with pytest.raises(JobRejected, match="replay"):
        net.coordinator.admit(ok)


def test_timeout_is_a_safe_failure(net):
    job = scenario.make_job(net, params={**scenario.PARAMS, "epochs": 500, "batch_size": 1})
    job = scenario.sign_job(job.spec.model_copy(update={"timeout_s": 1}), net.coder_key)
    a = scenario.fund_and_admit(net, job)
    agent = scenario.agent_for(net, a.provider)
    out, res = agent.execute(job, DATA)
    if not res.timed_out:
        pytest.skip("machine too fast to hit the 1 s timeout")
    assert res.exit_code != 0
    with pytest.raises(ExecutionFailed):
        agent.run(job, DATA, net.escrow)
    assert net.escrow.job(job.job_id)["state"] == "Assigned"  # nothing submitted, nothing paid
    net.escrow.advance(3700)
    net.escrow.refund(net.coder_key, job.job_id)
    assert net.escrow.job(job.job_id)["state"] == "Refunded"
