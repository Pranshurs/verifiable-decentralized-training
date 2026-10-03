"""A complete local network (Anvil + escrow + roles + providers) and the demo scenarios.

Used by the integration tests, the demo CLI and the benchmarks, so all three exercise the
same code path.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

from .chain import Anvil, Escrow, dev_keys
from .crypto import address_of, sha256_file
from .jobs import InputRef, JobSpec, Requirements, SignedJob, new_nonce, sign_job
from .protocol import Coordinator, ProviderAgent, VerifierService
from .providers import ProviderProfile, sign_profile

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "examples" / "data" / "wdbc.csv"
PARAMS = {"epochs": 20, "batch_size": 32, "seed": 7, "lr_micro": 100_000, "l2_micro": 1_000, "holdout_permille": 200}
PAYMENT = 10**17  # 0.1 test ETH


@dataclass
class Network:
    anvil: Anvil
    escrow: Escrow
    coordinator: Coordinator
    verifier: VerifierService
    coder_key: str
    providers: dict[str, ProviderAgent]   # name -> agent
    workdir: Path

    def close(self) -> None:
        self.anvil.stop()


def start(workdir: Path, verifier_secret: bytes = b"verifier-secret", sample_k: int | None = None) -> Network:
    keys = dev_keys(10)
    deployer, coord_k, coder_k, verif_k = keys[0], keys[1], keys[2], keys[3]
    anvil = Anvil()
    escrow = Escrow.deploy(anvil.url, deployer, coordinator=address_of(coord_k))
    verif = VerifierService(verif_k, verifier_secret, sample_k)
    coord = Coordinator(escrow, coord_k, verif.address)
    fleet = {
        # name: (key, cpu, ram, gpu, vram, runtimes)
        "laptop-cpu": (keys[4], 4, 8192, None, 0, ("python3.12-numpy2",)),
        "gpu-rig": (keys[5], 16, 65536, "RTX 3060", 12288, ("python3.12-numpy2", "python3.12-torch2")),
        "small-vm": (keys[6], 1, 512, None, 0, ("python3.12-numpy2",)),
        "desktop-cpu": (keys[7], 8, 16384, None, 0, ("python3.12-numpy2",)),
    }
    agents = {}
    for name, (k, cpu, ram, gpu, vram, rts) in fleet.items():
        prof = ProviderProfile(address=address_of(k), cpu_cores=cpu, ram_mb=ram, gpu_model=gpu, vram_mb=vram, runtimes=rts)
        coord.register(sign_profile(prof, k))
        agents[name] = ProviderAgent(k, workdir / "providers" / name)
    return Network(anvil, escrow, coord, verif, coder_k, agents, workdir)


def make_job(net: Network, params: dict | None = None, payment: int = PAYMENT, deadline_s: int = 3600,
             ram_mb: int = 1024, cpu: int = 1) -> SignedJob:
    spec = JobSpec(
        chain_id=net.escrow.w3.eth.chain_id, escrow=net.escrow.address, coder=address_of(net.coder_key),
        nonce=new_nonce(), workload="logreg-sgd@1", params=params or PARAMS,
        inputs=(InputRef(name="data.csv", sha256=sha256_file(DATA), size=DATA.stat().st_size),),
        requirements=Requirements(cpu_cores=cpu, ram_mb=ram_mb, runtime="python3.12-numpy2"),
        timeout_s=300, expected_outputs=("model.json", "metrics.json"), payment_wei=payment,
        deadline=net.escrow.now() + deadline_s,
    )
    return sign_job(spec, net.coder_key)


def fund_and_admit(net: Network, job: SignedJob):
    net.escrow.fund(net.coder_key, job.job_id, net.verifier.address, job.spec.deadline, job.spec.payment_wei)
    net.coordinator.admit(job)
    return net.coordinator.assign(job.job_id)


def agent_for(net: Network, address: str) -> ProviderAgent:
    return next(a for a in net.providers.values() if a.address == address)


def honest_run(net: Network, job: SignedJob) -> dict:
    """Signed job -> match -> escrow -> container -> commitment -> verification -> settlement."""
    t = {}
    t0 = time.perf_counter()
    data = {"data.csv": DATA}
    a = fund_and_admit(net, job)
    t["admit_match_assign_s"] = time.perf_counter() - t0
    agent = agent_for(net, a.provider)
    t1 = time.perf_counter()
    signed, out_dir = agent.run(job, data, net.escrow)
    t["execute_commit_s"] = time.perf_counter() - t1
    t2 = time.perf_counter()
    report = net.verifier.judge(job, signed, out_dir, net.escrow, data)
    net.coordinator.finished(job.job_id)
    t["verify_s"] = time.perf_counter() - t2
    before = net.escrow.balance(agent.address)
    tx = None
    if report.accepted:
        tx = net.verifier.settle(job.job_id, net.escrow)
    t["total_s"] = time.perf_counter() - t0
    return {"assignment": a, "report": report, "manifest": signed, "out_dir": out_dir, "release_tx": tx,
            "provider_paid_wei": net.escrow.balance(agent.address) - before + (0 if tx is None else 0),
            "final_state": net.escrow.job(job.job_id)["state"], "timings": t}


# -- adversaries ---------------------------------------------------------------------

def tamper_after_commit(out_dir: Path) -> None:
    """Edit metrics.json after the manifest was signed and the hash was put on-chain."""
    p = out_dir / "metrics.json"
    m = json.loads(p.read_text())
    m["accuracy"] = 0.999
    p.write_text(json.dumps(m, sort_keys=True) + "\n")


def fabricate_outputs(out_dir: Path, params: dict, seed: int = 0) -> None:
    """Skip the work: write a plausible checkpoint chain, model and metrics with invented
    weights. Every file is internally consistent and gets honestly committed."""
    rng = random.Random(seed)
    from .workloads import logreg_sgd

    X, y, digest = logreg_sgd.load_csv(DATA)
    state = logreg_sgd.init_state(X, y, digest, params)  # a careful cheat even gets epoch 0 right
    ck = out_dir / "checkpoints"
    ck.mkdir(parents=True, exist_ok=True)
    (ck / "epoch-0000.json").write_text(json.dumps(state, sort_keys=True) + "\n")
    for e in range(1, params["epochs"] + 1):
        state = {**state, "epoch": e, "w": [w + rng.gauss(0, 0.05) for w in state["w"]], "b": state["b"] + 0.01}
        (ck / f"epoch-{e:04d}.json").write_text(json.dumps(state, sort_keys=True) + "\n")
    (out_dir / "model.json").write_text(json.dumps({k: state[k] for k in ("workload", "data_sha256", "epoch", "w", "b", "mean", "std")}, sort_keys=True) + "\n")
    (out_dir / "metrics.json").write_text(json.dumps({"accuracy": 0.99, "epochs": params["epochs"], "holdout_rows": 114,
                                                      "log_loss": 0.05, "roc_auc": 0.999}, sort_keys=True) + "\n")
