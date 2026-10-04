"""Encrypted, authenticated checkpoints (white paper §3.1 and §5: "encrypted checkpoints", AES-256).

Each checkpoint is sealed with AES-256-GCM under a per-job key. The associated data binds
the job id, the sequence number (epoch) and the SHA-256 of the plaintext. A checkpoint
therefore can't be moved to another job, renumbered, or altered without decryption
failing.

Storage is a content-addressed directory: the object id is the SHA-256 of the ciphertext.
This *simulates* decentralised storage (the paper cites IPFS). Nothing is distributed.

Staleness: the coordinator records the highest sequence a provider announced. A resume
from an older checkpoint is refused, so a provider can't be handed a stale state.

Key distribution is simplified: the coder creates the job key and gives it to the assigned
provider and to the verifier out of band. Encrypting it to each provider's public key
(ECIES) is listed as deferred.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import canonical

ALG = "AES-256-GCM"


class CheckpointError(ValueError):
    pass


def new_job_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


@dataclass(frozen=True)
class CheckpointMeta:
    job_id: str
    seq: int
    content_sha256: str
    object_id: str      # sha256 of the ciphertext = storage address
    nonce_b64: str
    alg: str
    created_at: float

    def aad(self) -> bytes:
        return canonical({"job_id": self.job_id, "seq": self.seq, "content_sha256": self.content_sha256, "alg": self.alg})


class CheckpointStore:
    """Content-addressed ciphertext store plus a metadata index per job."""

    def __init__(self, root: Path):
        self.root = root
        (root / "objects").mkdir(parents=True, exist_ok=True)
        (root / "index").mkdir(parents=True, exist_ok=True)

    def seal(self, key: bytes, job_id: str, seq: int, plaintext: bytes) -> CheckpointMeta:
        nonce = os.urandom(12)
        partial = CheckpointMeta(job_id, seq, hashlib.sha256(plaintext).hexdigest(), "", "", ALG, time.time())
        ct = AESGCM(key).encrypt(nonce, plaintext, partial.aad())
        oid = hashlib.sha256(ct).hexdigest()
        (self.root / "objects" / oid).write_bytes(ct)
        meta = CheckpointMeta(job_id, seq, partial.content_sha256, oid, base64.b64encode(nonce).decode(), ALG,
                              partial.created_at)
        idx = self.root / "index" / f"{job_id}.jsonl"
        with open(idx, "a") as f:
            f.write(json.dumps(meta.__dict__) + "\n")
        return meta

    def open(self, key: bytes, meta: CheckpointMeta) -> bytes:
        path = self.root / "objects" / meta.object_id
        if not path.exists():
            raise CheckpointError(f"object {meta.object_id[:12]} missing from storage")
        ct = path.read_bytes()
        if hashlib.sha256(ct).hexdigest() != meta.object_id:
            raise CheckpointError("ciphertext does not match its content address")
        try:
            pt = AESGCM(key).decrypt(base64.b64decode(meta.nonce_b64), ct, meta.aad())
        except InvalidTag as exc:
            raise CheckpointError("authentication failed (tampered data, wrong key, or wrong job/seq)") from exc
        if hashlib.sha256(pt).hexdigest() != meta.content_sha256:
            raise CheckpointError("plaintext hash mismatch")
        return pt

    def index(self, job_id: str) -> list[CheckpointMeta]:
        idx = self.root / "index" / f"{job_id}.jsonl"
        if not idx.exists():
            return []
        return [CheckpointMeta(**json.loads(line)) for line in idx.read_text().splitlines() if line]

    def latest_valid(self, key: bytes, job_id: str, min_seq: int = 0) -> tuple[CheckpointMeta, bytes]:
        """Highest-sequence checkpoint that decrypts and verifies. Refuses anything below ``min_seq``."""
        for meta in sorted(self.index(job_id), key=lambda m: m.seq, reverse=True):
            if meta.job_id != job_id:
                continue
            try:
                pt = self.open(key, meta)
            except CheckpointError:
                continue
            if meta.seq < min_seq:
                raise CheckpointError(f"latest valid checkpoint is seq {meta.seq}, older than announced {min_seq} (stale)")
            return meta, pt
        raise CheckpointError("no valid checkpoint for this job")
