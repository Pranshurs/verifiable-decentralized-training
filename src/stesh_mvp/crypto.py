"""Canonical encoding, hashing and ECDSA signatures.

Signatures are Ethereum-style ECDSA over secp256k1 (EIP-191 "personal_sign" of a 32-byte
digest), so the same keys identify parties on the escrow contract and in the off-chain
protocol. The white paper (§4.2) specifies ECDSA-signed task submissions.

Canonical form: JSON with sorted keys, no whitespace, UTF-8. Floats are rejected in
anything that gets signed, because two encoders can print the same float differently.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address


class CanonicalError(ValueError):
    pass


def _check(value: Any, path: str = "$") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return
    if isinstance(value, float):
        raise CanonicalError(f"{path}: floats are not allowed in signed data")
    if isinstance(value, list):
        for i, v in enumerate(value):
            _check(v, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise CanonicalError(f"{path}: keys must be strings")
            _check(v, f"{path}.{k}")
        return
    raise CanonicalError(f"{path}: unsupported type {type(value).__name__}")


def canonical(value: Any) -> bytes:
    _check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def keccak_hex(data: bytes) -> str:
    return "0x" + keccak(data).hex()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sign_digest(digest_hex: str, private_key: str) -> str:
    msg = encode_defunct(primitive=bytes.fromhex(digest_hex.removeprefix("0x")))
    return "0x" + Account.sign_message(msg, private_key).signature.hex().removeprefix("0x")


def recover(digest_hex: str, signature: str) -> str:
    msg = encode_defunct(primitive=bytes.fromhex(digest_hex.removeprefix("0x")))
    return to_checksum_address(Account.recover_message(msg, signature=signature))


def address_of(private_key: str) -> str:
    return to_checksum_address(Account.from_key(private_key).address)
