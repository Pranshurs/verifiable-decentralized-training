"""The three off-chain roles and how they use the pieces.

  Coordinator    admits signed jobs (signature, replay, escrow funding), matches a
                 provider, assigns it on-chain, and reassigns after a provider failure.
  ProviderAgent  runs the workload in a container, commits to the result (signed
                 manifest), and submits the result hash on-chain.
  VerifierService  re-checks the result (verifier.py), records the verdict on-chain,
                 and releases payment only if it accepted.

One process can host all three for the MVP; they share no state except the chain and the
files a provider hands over.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import executor, verifier
from .chain import Escrow
from .commitments import SignedManifest, build_manifest, sign_manifest
from .crypto import address_of
from .jobs import SignedJob
from .providers import Registry, SignedProfile, match
from .workloads import logreg_sgd


class JobRejected(Exception):
    pass


class ExecutionFailed(Exception):
    pass


@dataclass
class Assignment:
    job_id: str
    provider: str
    match_seconds: float
    ranked: list[str]


@dataclass
class Coordinator:
    escrow: Escrow
    key: str
    verifier_address: str
    registry: Registry = field(default_factory=Registry)
    seen_nonces: set = field(default_factory=set)
    jobs: dict = field(default_factory=dict)

    def register(self, signed: SignedProfile) -> None:
        self.registry.register(signed)

    def admit(self, job: SignedJob) -> None:
        spec, jid = job.spec, job.job_id
        checks = [
            (job.signature_valid(), "signature does not match the coder"),
            (spec.chain_id == self.escrow.w3.eth.chain_id, "wrong chain id"),
            (spec.escrow == self.escrow.address, "wrong escrow contract"),
            (spec.deadline > self.escrow.now(), "deadline has passed"),
            (spec.workload in executor.WORKLOADS, f"workload {spec.workload} is not allow-listed"),
            ((spec.coder, spec.nonce) not in self.seen_nonces, "nonce already used by this coder (replay)"),
            (jid not in self.jobs, "job id already submitted (replay)"),
        ]
        for ok, why in checks:
            if not ok:
                raise JobRejected(why)
        if spec.workload == logreg_sgd.NAME:
            try:
                logreg_sgd.validate_params(dict(spec.params))
            except ValueError as exc:
                raise JobRejected(str(exc)) from exc
            if spec.requirements.runtime != executor.WORKLOADS[spec.workload]["runtime"]:
                raise JobRejected("requirements.runtime does not match the workload image")
        onchain = self.escrow.job(jid)
        funded = [
            (onchain["state"] == "Funded", f"escrow state is {onchain['state']}, not Funded"),
            (onchain["coder"] == spec.coder, "escrow was funded by someone else"),
            (onchain["amount"] == spec.payment_wei, "escrow amount differs from the signed payment"),
            (onchain["verifier"] == self.verifier_address, "escrow names a different verifier"),
            (onchain["deadline"] == spec.deadline, "escrow deadline differs from the signed deadline"),
        ]
        for ok, why in funded:
            if not ok:
                raise JobRejected(why)
        self.seen_nonces.add((spec.coder, spec.nonce))
        self.jobs[jid] = {"job": job, "provider": None, "excluded": set()}

    def assign(self, job_id: str) -> Assignment:
        rec = self.jobs[job_id]
        t0 = time.perf_counter()
        ranked = [p for p in match(rec["job"].spec.requirements, list(self.registry.providers.values()))
                  if p.profile.address not in rec["excluded"]]
        seconds = time.perf_counter() - t0
        if not ranked:
            raise JobRejected("no eligible provider")
        chosen = ranked[0]
        self.escrow.assign(self.key, job_id, chosen.profile.address)
        chosen.active_jobs += 1
        rec["provider"] = chosen.profile.address
        return Assignment(job_id, chosen.profile.address, seconds, [p.profile.address for p in ranked])

    def finished(self, job_id: str) -> None:
        """Release the provider's capacity once the job is verified (either way) or abandoned."""
        rec = self.jobs[job_id]
        if rec["provider"] is not None and not rec.get("finished"):
            self.registry.providers[rec["provider"]].active_jobs -= 1
            rec["finished"] = True

    def provider_failed(self, job_id: str) -> Assignment:
        """Mark the assigned provider failed and reassign the job (on-chain) to the next match."""
        rec = self.jobs[job_id]
        failed = rec["provider"]
        rec["excluded"].add(failed)
        state = self.registry.providers[failed]
        state.active_jobs -= 1
        state.available = False
        return self.assign(job_id)


@dataclass
class ProviderAgent:
    key: str
    workdir: Path

    @property
    def address(self) -> str:
        return address_of(self.key)

    def execute(self, job: SignedJob, data_files: dict[str, Path], resume: Path | None = None,
                env: dict[str, str] | None = None) -> tuple[Path, executor.ExecResult]:
        run_dir = self.workdir / job.job_id[2:14] / self.address[2:10]
        if run_dir.exists():
            shutil.rmtree(run_dir)
        in_dir, out_dir = run_dir / "in", run_dir / "out"
        executor.stage_inputs(job.spec, job.job_id, data_files, in_dir, resume)
        result = executor.run(job.spec, in_dir, out_dir, env)
        return out_dir, result

    def commit(self, job: SignedJob, out_dir: Path, escrow: Escrow) -> SignedManifest:
        signed = sign_manifest(build_manifest(job.job_id, self.address, job.spec.inputs, out_dir), self.key)
        escrow.submit_result(self.key, job.job_id, signed.manifest.result_hash())
        return signed

    def run(self, job: SignedJob, data_files: dict[str, Path], escrow: Escrow) -> tuple[SignedManifest, Path]:
        out_dir, res = self.execute(job, data_files)
        if res.exit_code != 0:
            raise ExecutionFailed(f"exit {res.exit_code}, timed_out={res.timed_out}: {res.stderr[-300:]}")
        return self.commit(job, out_dir, escrow), out_dir


@dataclass
class VerifierService:
    key: str
    secret: bytes
    sample_k: int | None = None  # None: re-execute every epoch

    @property
    def address(self) -> str:
        return address_of(self.key)

    def judge(self, job: SignedJob, signed: SignedManifest, out_dir: Path, escrow: Escrow,
              data_files: dict[str, Path]) -> verifier.Report:
        onchain = escrow.job(job.job_id)
        report = verifier.verify(job, signed, out_dir, onchain["provider"], onchain["result_hash"],
                                 data_files, self.secret, self.sample_k)
        # The verdict always names the hash that is on-chain, so it can't be applied to anything else.
        escrow.verify(self.key, job.job_id, onchain["result_hash"], report.accepted)
        return report

    def settle(self, job_id: str, escrow: Escrow):
        return escrow.release(self.key, job_id)
