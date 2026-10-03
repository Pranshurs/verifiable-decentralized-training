# ZK experiment: a Groth16 proof of one training step (EXPERIMENTAL)

The white paper (§4, §5, §6) proposes zero-knowledge proofs that a provider ran a task
correctly without revealing the coder's code or data, generated in about 10–30 s. The
MVP's verifier doesn't do that (see VERIFICATION.md). This experiment checks how far a
small, practical toolchain gets on one piece of the problem.

## What is proven

[`zk/sgd_step.circom`](../zk/sgd_step.circom) is a circom 2 circuit, compiled and proven
with snarkjs (Groth16, BN254). It proves this statement:

> Given public commitments `C_w`, `C_next` and `C_batch` (salted Poseidon hashes), the
> prover knows weights `w`, a minibatch `(x, y)` and next weights `w_next` that open
> those commitments, where `w_next` is exactly one floor-rounded, fixed-point
> gradient-descent step of linear regression from `w` on that batch.

The proof reveals nothing else about `w`, `w_next` or the data. The setup is 4 rows,
2 features, fixed-point scale 1000 and a learning rate of 1/10. The floor division is
enforced with a range-checked remainder, so `w_next` is unique.

`zk/make_input.py` is the reference implementation that produces the witness.

## Measured (Apple Silicon, Node 24, snarkjs 0.7.6, circom2 0.2.23)

| | |
|---|---|
| Constraints | 3,153 (1,196 non-linear) |
| Powers of tau (2^12) + phase 2 setup, local | about 60 s, one-time |
| Proof generation | 0.65 s |
| Verification (snarkjs CLI, including Node start-up) | 0.43 s |
| Proof | Groth16, 3 public signals (805-byte JSON) |
| Cheating witness (`w_next` off by one) | witness generation fails: the constraint is unsatisfied |
| Honest proof checked against a different `C_next` | `Invalid proof` |

Run it with `make zk` (or `zk/run.sh`). It needs Node 18+.

## What this does NOT show

- **It's not the MVP workload.** The MVP trains logistic regression. The sigmoid is
  expensive to express in a circuit (lookup tables or polynomial approximations), so the
  experiment uses linear regression's squared-loss step instead.
- **It doesn't prove a training run.** It proves one step on 4 rows. A full run of `E`
  epochs over `n` rows needs `E·n/batch` steps. You would either prove every step and
  aggregate the proofs recursively (Nova/folding, or recursive SNARKs), or move to a zkVM
  (RISC Zero, SP1). Both are well beyond a one-night experiment, and proving costs grow
  roughly linearly with the arithmetic in the training run.
- **It isn't connected to the escrow or the verifier.** snarkjs can export a Solidity
  verifier, so `verify` could check the proof on-chain. That integration isn't built.
- **The trusted setup is a toy.** The local, single-contributor powers-of-tau ceremony
  means whoever ran setup could forge proofs. A real deployment needs a multi-party
  ceremony, or a transparent system (STARKs) with no trusted setup.
- **It doesn't meet the paper's target in context.** 0.65 s per step on 4 rows says
  nothing about "10–30 s per task" for real AI workloads. Proving general neural-network
  training remains an open research problem. The paper's privacy goal still depends on
  that problem, or on TEEs or MPC.

## Status in the implementation map

The zero-knowledge proof concept is **EXPERIMENTAL**: there's a working proof of a single
step. General ZK Proof-of-Compute is **DEFERRED**.
