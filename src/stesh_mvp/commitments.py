"""Result commitments: artifact hashes, a Merkle root, and a provider-signed manifest.

The manifest binds together:
  * the job id (keccak of the coder-signed spec), so a result can't be replayed onto
    another job;
  * the input root (Merkle root of the input files' SHA-256s from the signed spec), so the
    provider commits to having used the coder's inputs;
  * every output artifact and checkpoint, by SHA-256, under one Merkle root.

``result_hash`` (keccak of the canonical manifest) is what the provider submits to the
escrow contract. The verifier's on-chain verdict names that same hash.

Merkle tree: domain-separated leaves ``sha256(0x00 || name || 0x00 || file_sha256)``,
sorted by name, and nodes ``sha256(0x01 || left || right)``. An odd node is promoted
unchanged rather than duplicated, which avoids the CVE-2012-2459 duplicate-leaf ambiguity.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict

from .crypto import canonical, keccak_hex, recover, sha256_file, sign_digest

MANIFEST_VERSION = "stesh-mvp/result/v1"


def leaf(name: str, file_sha256: str) -> bytes:
    return hashlib.sha256(b"\x00" + name.encode() + b"\x00" + bytes.fromhex(file_sha256)).digest()


def merkle_root(entries: Iterable[tuple[str, str]]) -> str:
    level = [leaf(n, h) for n, h in sorted(entries)]
    if not level:
        raise ValueError("cannot commit to an empty set")
    while len(level) > 1:
        nxt = [hashlib.sha256(b"\x01" + level[i] + level[i + 1]).digest() for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0].hex()


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sha256: str
    size: int


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = MANIFEST_VERSION
    job_id: str
    provider: str
    input_root: str
    artifacts: dict[str, Artifact]   # relative path -> hash; includes checkpoints/epoch-NNNN.json
    artifact_root: str

    def result_hash(self) -> str:
        return keccak_hex(canonical(self.model_dump(mode="json")))


class SignedManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    manifest: Manifest
    signature: str

    def signer(self) -> str | None:
        try:
            return recover(self.manifest.result_hash(), self.signature)
        except Exception:
            return None


def input_root(inputs) -> str:
    return merkle_root((i.name, i.sha256) for i in inputs)


def scan(out_dir: Path) -> dict[str, Artifact]:
    files = sorted(p for p in out_dir.rglob("*") if p.is_file())
    return {str(p.relative_to(out_dir)): Artifact(sha256=sha256_file(p), size=p.stat().st_size) for p in files}


def build_manifest(job_id: str, provider: str, inputs, out_dir: Path) -> Manifest:
    arts = scan(out_dir)
    return Manifest(job_id=job_id, provider=provider, input_root=input_root(inputs), artifacts=arts,
                    artifact_root=merkle_root((n, a.sha256) for n, a in arts.items()))


def sign_manifest(m: Manifest, private_key: str) -> SignedManifest:
    return SignedManifest(manifest=m, signature=sign_digest(m.result_hash(), private_key))
