"""The job specification a coder signs (white paper §4.2, "Task Submission").

Everything that affects what is run, on what hardware, and for how much is inside the
signed spec: workload, parameters, input commitments, resource requirements, timeout,
expected outputs, payment, deadline, and a nonce. The job id is the keccak-256 of the
canonical spec, and the same id keys the on-chain escrow. Changing any field changes the
id and invalidates the signature.
"""

from __future__ import annotations

import re
import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from eth_utils import is_checksum_address

from .crypto import canonical, keccak_hex, recover, sign_digest

SPEC_VERSION = "stesh-mvp/job/v1"
_HEX32 = re.compile(r"^[0-9a-f]{64}$")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Requirements(_Frozen):
    cpu_cores: int = Field(ge=1, le=256)
    ram_mb: int = Field(ge=64, le=1_048_576)
    gpu_required: bool = False
    min_vram_mb: int = Field(0, ge=0)
    runtime: str = Field(min_length=1, max_length=64)  # e.g. "python3.12-numpy2"


class InputRef(_Frozen):
    name: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    sha256: str
    size: int = Field(ge=0)

    @field_validator("sha256")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not _HEX32.match(v):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return v


class JobSpec(_Frozen):
    version: Literal["stesh-mvp/job/v1"] = SPEC_VERSION
    chain_id: int = Field(ge=1)
    escrow: str
    coder: str
    nonce: str
    workload: str = Field(pattern=r"^[a-z0-9-]+@[0-9]+$")   # name@major, resolved by the workload registry
    params: dict[str, int | str | bool]                     # integers only for numbers: see crypto.canonical
    inputs: tuple[InputRef, ...] = Field(min_length=1)
    requirements: Requirements
    timeout_s: int = Field(ge=1, le=86_400)
    expected_outputs: tuple[str, ...] = Field(min_length=1)
    payment_wei: int = Field(gt=0)
    deadline: int = Field(gt=0)                             # unix seconds; also the escrow deadline

    @field_validator("escrow", "coder")
    @classmethod
    def _address(cls, v: str) -> str:
        if not is_checksum_address(v):
            raise ValueError("must be an EIP-55 checksummed address")
        return v

    @field_validator("nonce")
    @classmethod
    def _nonce(cls, v: str) -> str:
        if not _HEX32.match(v):
            raise ValueError("nonce must be 64 lowercase hex characters")
        return v

    def canonical(self) -> bytes:
        return canonical(self.model_dump(mode="json"))

    def job_id(self) -> str:
        return keccak_hex(self.canonical())


def new_nonce() -> str:
    return secrets.token_hex(32)


class SignedJob(_Frozen):
    spec: JobSpec
    signature: str

    @property
    def job_id(self) -> str:
        return self.spec.job_id()

    def signer(self) -> str:
        return recover(self.job_id, self.signature)

    def signature_valid(self) -> bool:
        try:
            return self.signer() == self.spec.coder
        except Exception:
            return False


def sign_job(spec: JobSpec, private_key: str) -> SignedJob:
    return SignedJob(spec=spec, signature=sign_digest(spec.job_id(), private_key))
