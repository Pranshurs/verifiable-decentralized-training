# White paper → implementation map

**Source:** *STESH: Scalable Tokenised Ecosystem for Shared Hardware: Decentralised GPU/CPU
Rental for India's AI Compute*, Pranshu Raj, 15 May 2025. It describes itself as a
**pre-beta proposal**.

Every quantitative figure in the paper is a **target**. None of them is a result:
- ₹62–83/hour
- 10–30 s proof generation, < 5 s by 2027
- > 95% uptime
- < 5 s matching
- the "Q1 2025" validation with 15 systems and 50 tasks
- the 100–1,000-user beta
- ₹0.8 per transaction
- 1,000–5,000 tx/s

This repository doesn't claim any of those happened. Measured MVP numbers are in
[`../benchmarks/results.json`](../benchmarks/results.json), shown next to the paper's
targets in the README.

Status values:
- **IMPLEMENTED:** works in this repository and is tested.
- **SIMULATED:** behaviour stands in for the real thing locally.
- **EXPERIMENTAL:** partial or exploratory.
- **DEFERRED:** not built.

| White-paper concept | Paper section | MVP implementation | Status | Notes |
|---|---|---|---|---|
| Task submission with specifications (VRAM, runtime, …) | §3.1, §4.2 | `jobs.py`: `JobSpec` with workload, params, input hashes, CPU/RAM/GPU/VRAM/runtime, timeout, outputs, payment, deadline, nonce | IMPLEMENTED | CLI and library; there's no web UI (§3.1 mentions one) |
| ECDSA-signed submissions | §4.2, §5 | Canonical JSON → keccak-256 job id, signed with secp256k1 ECDSA (EIP-191) | IMPLEMENTED | Any field change invalidates the signature (tested per field) |
| Replay protection | (implied by §5) | Nonce plus job id checked by the coordinator; job id unique on-chain; manifest bound to job id | IMPLEMENTED | |
| Fund escrow on a blockchain | §4.2, §5 | `contracts/src/JobEscrow.sol` on a local Anvil chain, with test ETH | IMPLEMENTED (local chain) | No public chain or L2 deployment; no token |
| ₹0.8/tx on Arbitrum | §3.2, §4.3 | Gas measured on the local chain: fund 118,852; release 47,033 | DEFERRED | ₹ cost depends on the chain and gas price; not measured |
| Provider matching on hardware and availability | §3.1, §6.1, §6.4 | `providers.py`: deterministic filter (CPU, RAM, GPU, VRAM, runtime, availability, capacity) and ranking | IMPLEMENTED | Hardware claims are signed but self-reported; there's no attestation |
| Reputation in matching | §3.1, §5, §6.4 | A `reputation` ranking key exists; every provider scores 0.0 | DEFERRED | No reputation system |
| Matching < 5 s | §3.5, §6.3 | Measured: 41 ms median over 100,000 providers on one machine | IMPLEMENTED (measured) | In-memory and single-process; there's no network discovery |
| Software containers, isolated | §3.1, §5, §6.1 | `executor.py`: Docker with no network, a read-only root, capabilities dropped, uid 65534, limits, a timeout, and allow-listed images only | IMPLEMENTED | A container isn't a VM; escape risk is in the threat model |
| Static code analysis of tasks | §5 | Not built. Jobs can't carry code: they name an allow-listed workload | DEFERRED | Avoids the need for now; arbitrary user code is out of scope |
| PyTorch/TensorFlow workloads | §6.1 | One real workload: numpy logistic-regression SGD on WDBC | DEFERRED (other frameworks) | The verifier needs a re-executor per workload |
| GPU execution | §2, §6.1 | GPU requirements are matched, but nothing runs on a GPU | DEFERRED | Runs on ordinary CPUs |
| Encrypted checkpoints (AES-256) | §3.1, §5, §12 | `checkpoints.py`: AES-256-GCM, with job id, sequence and content hash bound as associated data | IMPLEMENTED | Key distribution to providers is out of band (ECIES is deferred) |
| Decentralised storage for checkpoints (IPFS cited) | §3.1, §5, §6.1 | A content-addressed local directory (object id = SHA-256 of the ciphertext) | SIMULATED | Nothing is distributed or replicated |
| Job continuity: resume on another system | §8.2 | Provider A stops; on-chain reassign; B resumes from the latest valid checkpoint; full chain verified | IMPLEMENTED | Failure detection is a signal to the coordinator, not a heartbeat or liveness protocol |
| Proof-of-Compute: verify output | §4 | `verifier.py`: integrity layer (signature, job binding, input/artifact commitments, on-chain hash) plus re-execution of epoch transitions chosen after commitment, plus metric recomputation | IMPLEMENTED (not ZK) | See VERIFICATION.md for exactly what it guarantees |
| Merkle root hashing | §4.2, Glossary | Merkle roots over the input and artifact hashes, domain-separated | IMPLEMENTED | |
| Zero-knowledge proofs of task completion | §3.1, §4, §5, §6 | Not used by the MVP verifier | DEFERRED / EXPERIMENTAL | See "ZK" in the README. General ZK proof of ML training remains research |
| Proof generation 10–30 s / < 5 s | §4.3, §6.5, §13 | — | DEFERRED | No ZK proof; MVP verification takes about 0.15 s for this workload |
| Protecting coders' code and data from providers | §4.1, §5 | Not achieved: the provider sees the data, and so does the verifier | DEFERRED | Needs ZK, TEEs or MPC |
| Erasure proofs (data deletion post-task) | §5, §12 | — | DEFERRED | |
| TLS 1.3 transfers | §5, §12 | Single process, local files; no network transport | DEFERRED | |
| Payment release on verification | §4.2 | `verify` + `release` on the escrow; only the coder or the verifier can release | IMPLEMENTED | |
| UPI-integrated payments | §3.1, §4.2, §12 | — | DEFERRED | Fiat rails are out of scope for a local MVP |
| Disputes within 24 h using blockchain logs | §4.2, §4.3 | Contract events, plus a refund after a verify window if the verifier is silent | PARTIAL / DEFERRED | No dispute or arbitration protocol |
| > 95% uptime, provider availability | §3.5, §4.3 | Availability flag in the registry | DEFERRED | Not measured; there's no long-running network |
| Validation with 15 systems / 50 tasks | §3.5, §4.4, §11 | — | DEFERRED | Never run; the paper's dates were plans |
| 100–1,000-user beta | §3.4, §7.4, §11 | — | DEFERRED | |
| Cost ₹62–83/hour, provider earnings | §3.2 | — | DEFERRED | No pricing model |
| Blockchain throughput 1,000–5,000 tx/s | §6.1, §6.2 | — | DEFERRED | Local chain only |
| Community governance, on-chain voting | §9 | — | DEFERRED | |
| Tokens / incentives | §8.2, §12 | None. The paper defers them pending RBI clarity, and so does this repository | DEFERRED | No token was created |
| Predictive matching, broader workloads | §13 | — | DEFERRED | |
