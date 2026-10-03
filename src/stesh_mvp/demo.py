"""``stesh-mvp demo``: run every scenario against a fresh local chain and print what happened."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import scenario
from .chain import TxFailed

ETH = 10**18


def _names(net) -> dict[str, str]:
    return {a.address: n for n, a in net.providers.items()}


def main(argv: list[str] | None = None) -> int:
    work = Path(".runs/demo").resolve()
    shutil.rmtree(work, ignore_errors=True)
    net = scenario.start(work)
    names = _names(net)
    data = {"data.csv": scenario.DATA}
    ok = True
    try:
        print(f"local chain {net.anvil.url}, escrow {net.escrow.address}")
        print(f"providers: {', '.join(names.values())}\n")

        print("1. HONEST PROVIDER")
        job = scenario.make_job(net)
        r = scenario.honest_run(net, job)
        a = r["assignment"]
        print(f"   job {job.job_id[:18]}… signed by coder; escrow funded {job.spec.payment_wei / ETH} ETH")
        print(f"   matched -> {names[a.provider]} (ranked: {', '.join(names[x] for x in a.ranked)})")
        print(f"   container ran {scenario.PARAMS['epochs']} epochs; metrics: "
              f"{(r['out_dir'] / 'metrics.json').read_text().strip()}")
        print(f"   verifier: {'ACCEPTED' if r['report'].accepted else 'REJECTED'}, "
              f"{len(r['report'].checks)} checks, re-executed epochs {r['report'].sampled_epochs[0]}..{r['report'].sampled_epochs[-1]}")
        print(f"   escrow: {r['final_state']}  (end to end {r['timings']['total_s']:.2f} s)")
        ok &= r["report"].accepted and r["final_state"] == "Released"
        try:
            net.verifier.settle(job.job_id, net.escrow)
            ok = False
            print("   !! second release succeeded")
        except TxFailed:
            print("   second release attempt: reverted (no double payment)")

        print("\n2. TAMPERED RESULT")
        job = scenario.make_job(net)
        a = scenario.fund_and_admit(net, job)
        agent = scenario.agent_for(net, a.provider)
        signed, out = agent.run(job, data, net.escrow)
        scenario.tamper_after_commit(out)
        rep = net.verifier.judge(job, signed, out, net.escrow, data)
        net.coordinator.finished(job.job_id)
        print(f"   provider edited metrics.json after committing; verifier: {'ACCEPTED' if rep.accepted else 'REJECTED'}")
        print(f"   reason: {rep.failures[0]}")
        try:
            net.verifier.settle(job.job_id, net.escrow)
            ok = False
        except TxFailed:
            print(f"   release: reverted. escrow {net.escrow.job(job.job_id)['state']}")
        net.escrow.refund(net.coder_key, job.job_id)
        print(f"   coder refunded: escrow {net.escrow.job(job.job_id)['state']}")
        ok &= not rep.accepted

        print("\n3. FABRICATED RESULT (no work done, consistent commitment)")
        job = scenario.make_job(net)
        a = scenario.fund_and_admit(net, job)
        agent = scenario.agent_for(net, a.provider)
        out = agent.workdir / "fabricated"
        shutil.rmtree(out, ignore_errors=True)
        scenario.fabricate_outputs(out, scenario.PARAMS)
        signed = agent.commit(job, out, net.escrow)
        rep = net.verifier.judge(job, signed, out, net.escrow, data)
        net.coordinator.finished(job.job_id)
        print(f"   verifier: {'ACCEPTED' if rep.accepted else 'REJECTED'}; first failure: {rep.failures[0]}")
        print(f"   escrow: {net.escrow.job(job.job_id)['state']}")
        ok &= not rep.accepted

        print("\n4. REPLAYED OLD RESULT")
        old = scenario.make_job(net)
        first = scenario.honest_run(net, old)
        new = scenario.make_job(net)
        a = scenario.fund_and_admit(net, new)
        agent = scenario.agent_for(net, a.provider)
        net.escrow.submit_result(agent.key, new.job_id, first["manifest"].manifest.result_hash())
        rep = net.verifier.judge(new, first["manifest"], first["out_dir"], net.escrow, data)
        net.coordinator.finished(new.job_id)
        print(f"   old job's result submitted for a new job; verifier: {'ACCEPTED' if rep.accepted else 'REJECTED'}")
        print(f"   reason: {rep.failures[0]}")
        ok &= not rep.accepted

        print("\n5. PROVIDER FAILURE AND CHECKPOINT RECOVERY")
        job = scenario.make_job(net)
        r = scenario.recovery_run(net, job, fail_after_epoch=8)
        print(f"   {names[r['provider_a']]} stopped after epoch 8 (exit {r['a_exit']}); "
              f"{r['checkpoints_sealed']} checkpoints sealed with AES-256-GCM")
        print(f"   reassigned on-chain to {names[r['provider_b']]}, resumed from checkpoint {r['resumed_from_seq']}")
        print(f"   verifier: {'ACCEPTED' if r['report'].accepted else 'REJECTED'}; escrow {r['final_state']} "
              f"(paid to {names[net.escrow.job(job.job_id)['provider']]})")
        ok &= r["report"].accepted and r["final_state"] == "Released"
    finally:
        net.close()
    print("\nall scenarios behaved as expected" if ok else "\nUNEXPECTED OUTCOME (see above)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
