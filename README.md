# Verifiable Decentralized Model Training (Codespaces-ready)

This repository is a **Codespaces-ready** proof-of-concept for *verifiable decentralized model training*. It simulates training across multiple nodes, produces cryptographic checkpoints, and verifies training integrity using simple hash-chains and Merkle-like proofs.

**Goal:** show infrastructure + crypto + ML systems thinking for grad-school portfolio (MIT/Stanford/CMU).

## Quick demo (one-click in Codespaces)
1. Create a new GitHub repository and add the files in this repo (or copy-paste).  
2. Open the repo in **GitHub Codespaces** ("Code -> Open with Codespaces -> New codespace").  
3. In Codespaces terminal run:
   ```bash
   python -m pip install -r requirements.txt
   python src/orchestrator.py --nodes 3 --epochs 3
   # then in another terminal
   python web/app.py
   ```
4. Open `http://127.0.0.1:5000` in the Codespaces forwarded ports to view training checkpoints and verification status.

## What this project contains
- `src/node.py` -- simulated training node that produces checkpoints and signed hashes
- `src/orchestrator.py` -- coordinates multiple nodes, triggers training rounds, aggregates checkpoints
- `src/proof.py` -- utilities to compute checkpoint hashes and verification proofs
- `web/app.py` -- simple Flask UI to view checkpoints and verification results
- `.devcontainer/` -- Codespaces devcontainer config so the environment boots with correct deps

## Notes on research & extension ideas
- Replace hash-chains with Merkle trees or zk-proof schemes for stronger guarantees
- Extend to real distributed training by swapping the simulated trainer with a PyTorch tiny-model trainer
- Integrate with IPFS for decentralized storage of checkpoints
