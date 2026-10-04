# Threat model (MVP)

**Parties:**
- **coder:** submits and pays
- **provider:** executes
- **coordinator:** admits, matches and assigns
- **verifier:** judges and releases
- **escrow contract**

The MVP runs every party in one process on a local chain. This model describes the
protocol's properties, not a production deployment.

Status values:
- **MITIGATED:** enforced and tested.
- **PARTIAL:** reduced, with stated gaps.
- **OUT OF SCOPE:** not handled.

| Threat | Status | How, and where it's tested |
|---|---|---|
| Malicious provider fabricates a result without computing | MITIGATED | Re-execution of the checkpoint chain plus metric recomputation. `test_fabricated_result_without_doing_the_work_is_rejected`, `test_fabricated_but_consistently_committed_result_is_rejected` |
| Provider edits, removes or substitutes artifacts after commitment | MITIGATED | Committed hashes, Merkle root and on-chain hash. `test_result_tampered_after_commit_is_rejected_and_not_paid`, `test_removed_and_substituted_artifacts_are_rejected` |
| Provider falsifies some epochs | MITIGATED (k = E) / PARTIAL (k < E) | Post-commitment sampled re-execution; the miss probability is stated in VERIFICATION.md. `test_single_falsified_epoch_is_caught_with_full_reexecution` |
| Replay of an old result onto a new job | MITIGATED | The manifest is bound to the job id, and the on-chain hash must match. `test_replayed_old_result_is_rejected`, `test_result_from_another_job_is_rejected` |
| Replay of a job submission | MITIGATED | Nonce and job id checked at admission; job ids are single-use on-chain. `test_admission_rejects_replays_…`, Foundry `test_coder_cannot_be_own_verifier_and_job_ids_are_single_use` |
| Tampering with job terms (payment, requirements, workload, inputs, deadline) | MITIGATED | The ECDSA signature covers the whole canonical spec; the escrow amount, verifier and deadline must equal the signed values. `test_any_modification_is_rejected`, `test_admission_rejects_…` |
| Double release / double spend | MITIGATED | State moves before the transfer; a second release reverts. Foundry `test_honest_path_pays_provider_once`, `test_reentrant_provider_is_paid_exactly_once`; e2e duplicate-release check |
| Provider releases its own payment | MITIGATED | `release` is restricted to the coder or verifier. Foundry `test_provider_cannot_release_even_after_verification`, fuzzed caller; e2e |
| Payment before verification, or after rejection | MITIGATED | Release requires state `Verified`. Foundry `test_release_before_verification_reverts`, `test_rejected_result_never_pays_and_refunds_coder` |
| Verdict applied to a different result | MITIGATED | `verify` must name the submitted hash. Foundry `test_verdict_must_name_the_submitted_hash` |
| Checkpoint tampering, wrong key, moved or renumbered checkpoint | MITIGATED | AES-GCM with job id, sequence and content hash as associated data; content addressing. `test_checkpoints.py` |
| Stale checkpoint used for resume | MITIGATED | Resume below the announced sequence is refused. `test_latest_valid_skips_corrupt_and_refuses_stale` |
| Provider failure mid-job | MITIGATED (simulated failure) | On-chain reassign, then resume from the latest valid checkpoint. `test_provider_failure_is_recovered_by_another_provider`. Failure is signalled, not detected by heartbeats |
| Provider stalls (timeout) | MITIGATED | The container is killed; nothing is submitted; the coder refunds after the deadline. `test_timeout_is_a_safe_failure` |
| Container escape / host compromise | PARTIAL | No network, read-only root, capabilities dropped, no-new-privileges, non-root uid, PID/memory/CPU limits, allow-listed images only. Containers share the host kernel; a kernel exploit isn't covered. gVisor, Firecracker or VMs would be the next step |
| Untrusted code execution | MITIGATED (by design) | Jobs can't carry code or commands, only an allow-listed workload name and validated integer params |
| False hardware claims by a provider | PARTIAL | Profiles are signed (attributable), and an over-claiming provider fails or times out and isn't paid. There's no hardware attestation |
| Provider denial of service (accept jobs, never finish) | PARTIAL | Timeouts, deadlines and refunds protect the coder. There's no reputation or slashing, so a provider can keep doing it at no cost |
| Malicious coder rejects honest work through a colluding verifier | OUT OF SCOPE | The contract trusts the verifier address the coder chose; the coder can't be its own verifier. Independent or decentralised verification is deferred |
| Coordinator compromise | PARTIAL | A malicious coordinator can assign a colluding provider or refuse jobs. It can't move funds, verify or release. Matching is deterministic and auditable |
| Verifier compromise | OUT OF SCOPE | The verifier can approve bad work or reject good work for its jobs. Mitigation would be multiple independent verifiers or ZK |
| Privacy leakage of coder data to provider and verifier | OUT OF SCOPE | Both see the data. Checkpoints are encrypted at rest, but the provider holds the key. The paper's IP-protection goal needs ZK, TEE or MPC |
| Sybil providers / reputation gaming | OUT OF SCOPE | There's no reputation system yet; identities are free keypairs |
| Front-running or MEV on a public chain | OUT OF SCOPE | Local chain only |
| Key management (job keys, wallet keys) | OUT OF SCOPE | Development keys are Anvil's public test mnemonic; job keys are passed in-process. ECIES key wrapping is deferred |
