# What the verifier guarantees, and what it doesn't

The paper's Proof-of-Compute (§4) proposes zero-knowledge proofs. This MVP doesn't use
zero-knowledge proofs. Its verifier is a layered check, and this page states exactly
what it establishes. Code: [`src/stesh_mvp/verifier.py`](../src/stesh_mvp/verifier.py).

## Layer 1: integrity (any workload)

| Check | Defeats |
|---|---|
| The manifest signature recovers to the provider the escrow assigned | results from anyone else |
| `manifest.job_id` equals keccak(signed job spec) | replaying a result from another job |
| On-chain `resultHash` equals keccak(manifest) | swapping the result after it was put on-chain |
| The input Merkle root equals the root of the coder-signed input hashes, and the coder's files match those hashes | claiming to have used different inputs |
| The files on disk are exactly the listed artifacts, each with its committed SHA-256, and the artifact Merkle root recomputes | editing, removing, adding or substituting files after commitment |
| Expected outputs are present, and so is a full checkpoint chain for epochs 0..E | partial results |

## Layer 2: re-execution (`logreg-sgd@1`)

| Check | Defeats |
|---|---|
| Checkpoint 0 equals the initial state the verifier derives from the data and the signed params | a made-up starting point |
| Sampled epoch transitions `i-1 → i` are recomputed and match checkpoint `i` (relative tolerance 1e-9) | fabricated or skipped training |
| `model.json` equals the final checkpoint | swapping in another model |
| `metrics.json` equals the metrics recomputed on the coder's hold-out rows | invented accuracy numbers |

The sampled epochs come from HMAC(verifier secret, result hash). They're fixed only after
the provider has committed, so the provider can't know in advance which transitions will
be checked.

## The guarantee

The verifier only accepts if:
- the provider committed to every artifact before learning which checks would run, and
- every checked transition matches.

If a provider falsified `f` of the `E` transitions and the verifier re-runs `k` of them,
the result is accepted with probability `C(E−f, k) / C(E, k)` (`verifier.miss_probability`).

**Default: k = E.** Every epoch is re-executed, so a falsified transition is always caught.
That is affordable here, because one epoch of this workload takes milliseconds.

For bigger jobs, choose `k < E` and accept the stated miss probability. For example, with
E = 20 and k = 5, a single falsified epoch slips through 75% of the time. A provider that
falsifies 10 epochs slips through 1.6% of the time.

## What it does NOT guarantee

- **Privacy.** The provider and the verifier both see the coder's data. The paper's goal of
  protecting coders' IP (§4.1) isn't met. That needs ZK, TEEs or MPC.
- **Cheap verification.** Re-execution costs `k/E` of the training compute. That's fine for
  this workload, but not for large models.
- **Arbitrary workloads.** Only workloads with a registered deterministic re-executor can
  be verified. Anything else fails closed.
- **Bit-exact reproducibility across hardware.** Comparisons use a 1e-9 relative
  tolerance, so tiny floating-point differences from other BLAS builds are accepted. That
  same tolerance is room a provider could exploit, but it's far below anything that
  changes the model.
- **Honest verifier.** The escrow trusts the verifier address the coder chose. A verifier
  colluding with the coder could reject honest work and get the coder refunded. The
  contract constrains the verifier only to naming the exact submitted hash.
- **Liveness.** If the verifier never answers, the coder can refund after the verify
  window. The honest provider is then unpaid; that case needs a dispute protocol (deferred).
