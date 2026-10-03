"""Workload ``logreg-sgd@1``: L2-regularised logistic regression trained by minibatch SGD.

The same file runs as the container entrypoint (provider side) and is imported by the
verifier to re-execute epochs. Only the standard library and numpy are used, so the math
is easy to audit and the image stays small.

Contract
--------
Input  (read-only ``/in``):  ``job.json`` {job_id, params}, ``data.csv`` (header row,
       numeric features, last column = 0/1 label), optional ``resume.json`` (a checkpoint
       to continue from; used after a provider failure).
Output (``/out``): ``checkpoints/epoch-NNNN.json`` after every epoch (epoch 0 = initial
       state), ``model.json`` (final weights), ``metrics.json`` (hold-out metrics).

Every epoch is a deterministic function of (data, params, previous checkpoint). That's
what lets a verifier re-run any sampled epoch and compare. Bit-exactness across
machines isn't assumed (BLAS summation order can differ), so comparisons use a tight
tolerance.

Params (integers, because the job spec is signed in a canonical form without floats):
  epochs, batch_size, seed, lr_micro (learning rate × 1e6), l2_micro (L2 × 1e6),
  holdout_permille (hold-out fraction × 1000).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

NAME = "logreg-sgd@1"
OUTPUTS = ("model.json", "metrics.json")
PARAM_BOUNDS = {
    "epochs": (1, 500), "batch_size": (1, 4096), "seed": (0, 2**31 - 1),
    "lr_micro": (1, 10_000_000), "l2_micro": (0, 10_000_000), "holdout_permille": (50, 500),
}


def validate_params(params: dict) -> dict:
    out = {}
    for k, (lo, hi) in PARAM_BOUNDS.items():
        v = params.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise ValueError(f"param {k} must be an int in [{lo}, {hi}]")
        out[k] = v
    extra = set(params) - set(PARAM_BOUNDS)
    if extra:
        raise ValueError(f"unknown params {sorted(extra)}")
    return out


def load_csv(path: Path) -> tuple[np.ndarray, np.ndarray, str]:
    raw = path.read_bytes()
    rows = [r for r in raw.decode().strip().splitlines()[1:] if r]
    arr = np.array([[float(x) for x in r.split(",")] for r in rows], dtype=np.float64)
    X, y = arr[:, :-1], arr[:, -1]
    if not set(np.unique(y)).issubset({0.0, 1.0}):
        raise ValueError("label column must be 0/1")
    return X, y, hashlib.sha256(raw).hexdigest()


def split(n: int, params: dict) -> tuple[np.ndarray, np.ndarray]:
    perm = np.random.default_rng(params["seed"]).permutation(n)
    k = math.ceil(n * params["holdout_permille"] / 1000)
    return np.sort(perm[k:]), np.sort(perm[:k])  # train, holdout


def init_state(X: np.ndarray, y: np.ndarray, data_sha256: str, params: dict) -> dict:
    train, _ = split(len(y), params)
    mean = X[train].mean(axis=0)
    std = X[train].std(axis=0)
    std[std == 0] = 1.0
    return {"workload": NAME, "data_sha256": data_sha256, "epoch": 0,
            "w": [0.0] * X.shape[1], "b": 0.0, "mean": mean.tolist(), "std": std.tolist()}


def _z(X: np.ndarray, state: dict) -> np.ndarray:
    return (X - np.asarray(state["mean"])) / np.asarray(state["std"])


def run_epoch(X: np.ndarray, y: np.ndarray, state: dict, params: dict) -> dict:
    """One pass over the training rows in an order fixed by (seed, epoch)."""
    train, _ = split(len(y), params)
    Z, t = _z(X[train], state), y[train]
    w, b = np.asarray(state["w"], dtype=np.float64), float(state["b"])
    lr, l2, bs = params["lr_micro"] / 1e6, params["l2_micro"] / 1e6, params["batch_size"]
    order = np.random.default_rng([params["seed"], state["epoch"] + 1]).permutation(len(t))
    for start in range(0, len(order), bs):
        idx = order[start:start + bs]
        p = 1.0 / (1.0 + np.exp(-(Z[idx] @ w + b)))
        err = p - t[idx]
        w = w - lr * (Z[idx].T @ err / len(idx) + l2 * w)
        b = b - lr * float(err.mean())
    return {**state, "epoch": state["epoch"] + 1, "w": w.tolist(), "b": b}


def predict_proba(X: np.ndarray, state: dict) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(_z(X, state) @ np.asarray(state["w"]) + state["b"])))


def _auc(y: np.ndarray, p: np.ndarray) -> float:
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    wins = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(wins / (len(pos) * len(neg)))


def metrics(X: np.ndarray, y: np.ndarray, state: dict, params: dict) -> dict:
    _, hold = split(len(y), params)
    p = predict_proba(X[hold], state)
    eps = 1e-15
    return {
        "holdout_rows": int(len(hold)),
        "accuracy": float(((p >= 0.5) == (y[hold] == 1)).mean()),
        "log_loss": float(-np.mean(y[hold] * np.log(np.clip(p, eps, 1)) + (1 - y[hold]) * np.log(np.clip(1 - p, eps, 1)))),
        "roc_auc": _auc(y[hold], p),
        "epochs": int(state["epoch"]),
    }


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True) + "\n")


def main(in_dir: str = "/in", out_dir: str = "/out") -> int:
    inp, out = Path(in_dir), Path(out_dir)
    job = json.loads((inp / "job.json").read_text())
    params = validate_params(job["params"])
    X, y, digest = load_csv(inp / "data.csv")
    resume = inp / "resume.json"
    if resume.exists():
        state = json.loads(resume.read_text())
        if state.get("data_sha256") != digest or state.get("workload") != NAME:
            print("resume checkpoint does not belong to this data/workload", file=sys.stderr)
            return 3
    else:
        state = init_state(X, y, digest, params)
        _write(out / "checkpoints" / "epoch-0000.json", state)
    # A provider-side fault-injection hook for the recovery demo: stop after this epoch.
    stop_after = int(os.environ.get("STESH_STOP_AFTER_EPOCH", "0"))
    while state["epoch"] < params["epochs"]:
        state = run_epoch(X, y, state, params)
        _write(out / "checkpoints" / f"epoch-{state['epoch']:04d}.json", state)
        if stop_after and state["epoch"] >= stop_after:
            print(f"stopping after epoch {state['epoch']} (fault injection)", file=sys.stderr)
            return 75
    _write(out / "model.json", {k: state[k] for k in ("workload", "data_sha256", "epoch", "w", "b", "mean", "std")})
    _write(out / "metrics.json", metrics(X, y, state, params))
    print(json.dumps({"job_id": job.get("job_id"), "epochs": state["epoch"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
