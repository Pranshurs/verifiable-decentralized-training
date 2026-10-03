"""Proof-of-Compute verification for the MVP (white paper §4: "Result Verification").

This is *not* a zero-knowledge proof. It's a layered check that the verifier can actually
perform, and it guarantees exactly what is listed below.

Layer 1, integrity (any workload):
  1. The manifest is signed by the provider the escrow assigned.
  2. It names this job's id, so a result produced for another job can't be replayed.
  3. Its input root equals the Merkle root of the coder-signed input hashes.
  4. Every file it lists exists, with the committed SHA-256. There are no missing or extra
     files, and the artifact Merkle root recomputes.
  5. The expected outputs and a full, contiguous checkpoint chain (epoch 0..E) are present.
  6. The result hash submitted on-chain equals keccak(manifest).

Layer 2, workload re-execution (``logreg-sgd@1``):
  7. Checkpoint 0 equals the initial state the verifier computes itself from the coder's
     data and the signed params.
  8. ``k`` epoch transitions, chosen after commitment from a seed derived from the
     verifier's secret and the result hash, are recomputed, and each must match the next
     checkpoint (relative tolerance 1e-9).
  9. ``model.json`` equals checkpoint E, and ``metrics.json`` equals the metrics the
     verifier recomputes from that model on the coder's hold-out rows.

What it guarantees: a provider can't be paid for a result that doesn't come from the
committed data and params by the committed training procedure. The exception is a
provider who falsified ``f`` of the ``E`` transitions and wasn't sampled. That passes
with probability C(E-f, k) / C(E, k). With k = E (the default for small jobs) the whole
training run is re-executed and that probability is 0.

What it does NOT guarantee:
- privacy: the verifier sees the data;
- cheapness: re-execution costs k/E of the training compute;
- anything about a workload it has no re-executor for. Those fail closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .commitments import SignedManifest, input_root, merkle_root
from .crypto import sha256_file
from .jobs import SignedJob
from .workloads import logreg_sgd

RTOL, ATOL = 1e-9, 1e-12


@dataclass
class Report:
    accepted: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    sampled_epochs: list[int] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, ok, detail))
        return ok

    @property
    def failures(self) -> list[str]:
        return [f"{n}: {d}" for n, ok, d in self.checks if not ok]


def _close(a, b) -> bool:
    return np.allclose(np.asarray(a, dtype=float), np.asarray(b, dtype=float), rtol=RTOL, atol=ATOL)


def _states_equal(a: dict, b: dict) -> bool:
    return (a.get("workload") == b.get("workload") and a.get("data_sha256") == b.get("data_sha256")
            and a.get("epoch") == b.get("epoch") and _close(a["w"], b["w"]) and _close([a["b"]], [b["b"]])
            and _close(a["mean"], b["mean"]) and _close(a["std"], b["std"]))


def challenge_epochs(secret: bytes, result_hash: str, epochs: int, k: int) -> list[int]:
    """Transitions to re-run, fixed only after the provider has committed to result_hash."""
    seed = hmac.new(secret, result_hash.encode(), hashlib.sha256).digest()
    k = min(k, epochs)
    return sorted(random.Random(seed).sample(range(1, epochs + 1), k))


def miss_probability(epochs: int, falsified: int, k: int) -> float:
    """Chance that k uniform samples miss all falsified transitions."""
    if falsified <= 0:
        return 1.0
    return math.comb(epochs - falsified, k) / math.comb(epochs, k) if epochs - falsified >= k else 0.0


def verify(job: SignedJob, signed: SignedManifest, artifacts_dir: Path, assigned_provider: str,
           onchain_result_hash: str, data_files: dict[str, Path], secret: bytes, k: int | None = None) -> Report:
    r = Report(accepted=False)
    m, spec = signed.manifest, job.spec

    # ---- layer 1: integrity -------------------------------------------------------
    signer = signed.signer()
    if not r.add("provider signature", signer == assigned_provider, f"signed by {signer}, assigned {assigned_provider}"):
        return r
    if not r.add("job binding", m.job_id == job.job_id and m.provider == assigned_provider,
                 f"manifest job {m.job_id[:12]}…, this job {job.job_id[:12]}…"):
        return r
    r.add("on-chain commitment", onchain_result_hash == m.result_hash(),
          f"chain {onchain_result_hash[:12]}…, manifest {m.result_hash()[:12]}…")
    r.add("input commitment", m.input_root == input_root(spec.inputs))
    for ref in spec.inputs:
        r.add(f"coder input {ref.name}", sha256_file(data_files[ref.name]) == ref.sha256)
    on_disk = {str(p.relative_to(artifacts_dir)) for p in artifacts_dir.rglob("*") if p.is_file()}
    r.add("artifact set", on_disk == set(m.artifacts),
          f"missing {sorted(set(m.artifacts) - on_disk)}, extra {sorted(on_disk - set(m.artifacts))}")
    for name, a in m.artifacts.items():
        p = artifacts_dir / name
        if p.is_file():
            r.add(f"hash {name}", sha256_file(p) == a.sha256)
    r.add("artifact root", m.artifact_root == merkle_root((n, a.sha256) for n, a in m.artifacts.items()))
    for out in spec.expected_outputs:
        r.add(f"output {out}", out in m.artifacts)
    if r.failures:
        return r

    # ---- layer 2: re-execution ----------------------------------------------------
    if spec.workload != logreg_sgd.NAME:
        r.add("re-executor", False, f"no verifier for {spec.workload}; failing closed")
        return r
    params = logreg_sgd.validate_params(spec.params)
    epochs = params["epochs"]
    expected_ckpts = {f"checkpoints/epoch-{e:04d}.json" for e in range(epochs + 1)}
    if not r.add("checkpoint chain", expected_ckpts <= set(m.artifacts),
                 f"missing {sorted(expected_ckpts - set(m.artifacts))[:3]}"):
        return r
    X, y, digest = logreg_sgd.load_csv(data_files[spec.inputs[0].name])

    def ckpt(e: int) -> dict:
        return json.loads((artifacts_dir / f"checkpoints/epoch-{e:04d}.json").read_text())

    r.add("initial state", _states_equal(ckpt(0), logreg_sgd.init_state(X, y, digest, params)))
    r.sampled_epochs = challenge_epochs(secret, m.result_hash(), epochs, epochs if k is None else k)
    for e in r.sampled_epochs:
        r.add(f"epoch {e} re-executed", _states_equal(logreg_sgd.run_epoch(X, y, ckpt(e - 1), params), ckpt(e)))
    final = ckpt(epochs)
    model = json.loads((artifacts_dir / "model.json").read_text())
    r.add("model = final checkpoint", _states_equal({**final, **model}, final))
    reported = json.loads((artifacts_dir / "metrics.json").read_text())
    recomputed = logreg_sgd.metrics(X, y, final, params)
    r.add("metrics recomputed", all(math.isclose(reported.get(k_, -1), v, rel_tol=1e-9, abs_tol=1e-12)
                                     for k_, v in recomputed.items()), f"reported {reported}, recomputed {recomputed}")
    r.accepted = not r.failures
    return r
