"""Deterministic hardware-aware matching, and its latency at increasing fleet sizes."""

from __future__ import annotations

import random
import time

import pytest

from stesh_mvp.chain import dev_keys
from stesh_mvp.crypto import address_of
from stesh_mvp.jobs import Requirements
from stesh_mvp.providers import (
    ProviderProfile,
    ProviderState,
    Registry,
    ineligibility,
    match,
    sign_profile,
)

RT = "python3.12-numpy2"


def p(addr_seed: int, cpu=4, ram=8192, gpu=None, vram=0, runtimes=(RT,), available=True, active=0, maxj=1, rep=0.0):
    addr = "0x" + f"{addr_seed:040x}"
    from eth_utils import to_checksum_address

    prof = ProviderProfile(address=to_checksum_address(addr), cpu_cores=cpu, ram_mb=ram, gpu_model=gpu, vram_mb=vram,
                           runtimes=runtimes, max_concurrent_jobs=maxj)
    return ProviderState(prof, available=available, active_jobs=active, reputation=rep)


CPU_JOB = Requirements(cpu_cores=2, ram_mb=4096, runtime=RT)
GPU_JOB = Requirements(cpu_cores=2, ram_mb=4096, gpu_required=True, min_vram_mb=8192, runtime=RT)


def test_insufficient_resources_and_incompatibility():
    assert "ram" in ineligibility(CPU_JOB, p(1, ram=2048))
    assert "cpu" in ineligibility(CPU_JOB, p(2, cpu=1))
    assert ineligibility(GPU_JOB, p(3)) == "gpu required"
    assert "vram" in ineligibility(GPU_JOB, p(4, gpu="RTX 3050", vram=4096))
    assert "runtime" in ineligibility(CPU_JOB, p(5, runtimes=("python3.11-torch2",)))
    assert ineligibility(CPU_JOB, p(6, available=False)) == "unavailable"
    assert ineligibility(CPU_JOB, p(7, active=1, maxj=1)) == "at capacity"
    assert ineligibility(GPU_JOB, p(8, gpu="RTX 3060", vram=12288)) is None


def test_ranking_prefers_idle_then_best_fit_then_reputation_then_address():
    exact = p(10, cpu=2, ram=4096)
    roomy = p(11, cpu=16, ram=65536)
    gpu_box = p(12, cpu=2, ram=4096, gpu="A100", vram=40960)
    busy_exact = p(13, cpu=2, ram=4096, active=1, maxj=2)
    assert [x.profile.address for x in match(CPU_JOB, [roomy, gpu_box, busy_exact, exact])] == \
        [exact.profile.address, roomy.profile.address, gpu_box.profile.address, busy_exact.profile.address]
    a, b = p(20, rep=0.9), p(21, rep=0.1)
    assert match(CPU_JOB, [b, a])[0] is a
    t1, t2 = p(31), p(30)
    assert match(CPU_JOB, [t1, t2])[0] is t2  # equal on everything: lowest address wins, every time


def test_no_eligible_provider_gives_empty_list():
    assert match(GPU_JOB, [p(40), p(41, available=False)]) == []


def test_registry_rejects_forged_profiles():
    k1, k2 = dev_keys(2)
    prof = ProviderProfile(address=address_of(k1), cpu_cores=4, ram_mb=8192, runtimes=(RT,))
    reg = Registry()
    reg.register(sign_profile(prof, k1))
    with pytest.raises(PermissionError):
        reg.register(sign_profile(prof, k2))  # someone else claiming k1's identity/hardware


@pytest.mark.parametrize("n", [100, 1_000, 10_000, 100_000])
def test_matching_latency(n, record_property):
    rng = random.Random(n)
    fleet = [p(i + 1, cpu=rng.choice([1, 2, 4, 8, 16]), ram=rng.choice([1024, 4096, 8192, 32768]),
               gpu=rng.choice([None, None, "RTX 3060"]), vram=12288, available=rng.random() > 0.1)
             for i in range(n)]
    t0 = time.perf_counter()
    ranked = match(GPU_JOB, fleet)
    dt = time.perf_counter() - t0
    record_property("seconds", dt)
    assert ranked and all(ineligibility(GPU_JOB, x) is None for x in ranked)
    print(f"\nmatch n={n}: {dt * 1000:.1f} ms, {len(ranked)} eligible")
