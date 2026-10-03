"""Measure the MVP on this machine. Nothing here is tuned toward the white paper's targets.

    python -m stesh_mvp.bench --runs 5 --out benchmarks/results.json
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import subprocess
import time
from pathlib import Path

from . import executor, scenario
from .jobs import Requirements
from .providers import ProviderProfile, ProviderState, match


def _stats(xs: list[float]) -> dict:
    xs = sorted(xs)
    return {"n": len(xs), "median": statistics.median(xs), "min": xs[0], "max": xs[-1]}


def matching_latency(sizes=(100, 1_000, 10_000, 100_000), reps: int = 5) -> dict:
    from eth_utils import to_checksum_address

    req = Requirements(cpu_cores=2, ram_mb=4096, gpu_required=True, min_vram_mb=8192, runtime="python3.12-numpy2")
    out = {}
    for n in sizes:
        rng = random.Random(n)
        fleet = [ProviderState(ProviderProfile(
            address=to_checksum_address(f"0x{i + 1:040x}"), cpu_cores=rng.choice([1, 2, 4, 8, 16]),
            ram_mb=rng.choice([1024, 4096, 8192, 32768]), gpu_model=rng.choice([None, None, "RTX 3060"]),
            vram_mb=12288, runtimes=("python3.12-numpy2",)), available=rng.random() > 0.1) for i in range(n)]
        times = []
        for _ in range(reps):
            t0 = time.perf_counter()
            match(req, fleet)
            times.append(time.perf_counter() - t0)
        out[str(n)] = _stats(times)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default="benchmarks/results.json")
    args = ap.parse_args(argv)
    work = Path(".runs/bench").resolve()
    results: dict = {"machine": {"platform": platform.platform(), "python": platform.python_version(),
                                 "docker": subprocess.run(["docker", "version", "--format", "{{.Server.Version}} {{.Server.Os}}/{{.Server.Arch}}"],
                                                          capture_output=True, text=True).stdout.strip()},
                     "matching_seconds": matching_latency()}
    starts = [executor.container_start_latency(executor.WORKLOADS["logreg-sgd@1"]["image"]) for _ in range(args.runs)]
    results["container_start_seconds"] = _stats(starts)

    net = scenario.start(work)
    rows = []
    try:
        for _ in range(args.runs):
            job = scenario.make_job(net)
            t0 = time.perf_counter()
            fund = net.escrow.fund(net.coder_key, job.job_id, net.verifier.address, job.spec.deadline, job.spec.payment_wei)
            net.coordinator.admit(job)
            a = net.coordinator.assign(job.job_id)
            agent = scenario.agent_for(net, a.provider)
            t1 = time.perf_counter()
            out, res = agent.execute(job, {"data.csv": scenario.DATA})
            t2 = time.perf_counter()
            signed = agent.commit(job, out, net.escrow)
            t3 = time.perf_counter()
            report = net.verifier.judge(job, signed, out, net.escrow, {"data.csv": scenario.DATA})
            t4 = time.perf_counter()
            net.coordinator.finished(job.job_id)
            rel = net.verifier.settle(job.job_id, net.escrow)
            t5 = time.perf_counter()
            assert report.accepted and res.exit_code == 0
            rows.append({"match_s": a.match_seconds, "fund_tx_s": fund.seconds, "workload_s": t2 - t1,
                         "commit_incl_tx_s": t3 - t2, "verify_incl_tx_s": t4 - t3, "release_tx_s": rel.seconds,
                         "end_to_end_s": t5 - t0, "fund_gas": fund.gas_used, "release_gas": rel.gas_used})
        rec = []
        for _ in range(args.runs):
            r = scenario.recovery_run(net, scenario.make_job(net), fail_after_epoch=8)
            assert r["report"].accepted
            net.coordinator.registry.set_available(r["provider_a"], True)  # the failed machine comes back online
            rec.append({"seal_s": r["seal_seconds"], "checkpoints": r["checkpoints_sealed"],
                        "recovery_to_commit_s": r["recovery_seconds"]})
    finally:
        net.close()
    results["lifecycle"] = {k: _stats([r[k] for r in rows]) for k in rows[0]}
    results["checkpoint_recovery"] = {
        "seal_seconds_per_checkpoint": _stats([r["seal_s"] / r["checkpoints"] for r in rec]),
        "failure_to_resumed_result_committed_s": _stats([r["recovery_to_commit_s"] for r in rec])}
    results["workload"] = {"name": "logreg-sgd@1", "params": scenario.PARAMS, "rows": 569}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({k: v for k, v in results.items() if k != "machine"}, indent=1)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
