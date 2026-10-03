"""Provider registry and hardware-aware matching (white paper §3.1, §6.4).

Matching is deterministic: a hard compatibility filter, then a total order. The same
providers and the same job always produce the same ranking.

Filter (all must hold):
  available, below its concurrent-job limit, cpu_cores >= required, ram_mb >= required,
  runtime offered, and if a GPU is required: has a GPU with vram_mb >= min_vram_mb.

Rank (first difference wins):
  1. fewer active jobs                    (availability)
  2. no idle GPU                          (a CPU job never takes a GPU machine while a CPU machine fits)
  3. smaller normalised surplus           (capacity fit: keep big machines for big jobs)
  4. higher reputation                    (0.0 for everyone until a reputation system exists)
  5. provider address, lower-cased       (deterministic tie-break in numeric order)

Hardware is self-reported and signed by the provider. Signing proves who made the claim,
not that it's true. A provider that over-claims fails jobs, fails verification and isn't
paid (see docs/THREAT_MODEL.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from .crypto import canonical, keccak_hex, recover, sign_digest
from .jobs import Requirements


class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    address: str
    cpu_cores: int = Field(ge=1)
    ram_mb: int = Field(ge=64)
    gpu_model: str | None = None
    vram_mb: int = Field(0, ge=0)
    runtimes: tuple[str, ...]
    max_concurrent_jobs: int = Field(1, ge=1)

    def digest(self) -> str:
        return keccak_hex(canonical({"type": "stesh-mvp/provider/v1", **self.model_dump(mode="json")}))


class SignedProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: ProviderProfile
    signature: str

    def valid(self) -> bool:
        try:
            return recover(self.profile.digest(), self.signature) == self.profile.address
        except Exception:
            return False


def sign_profile(profile: ProviderProfile, private_key: str) -> SignedProfile:
    return SignedProfile(profile=profile, signature=sign_digest(profile.digest(), private_key))


@dataclass
class ProviderState:
    profile: ProviderProfile
    available: bool = True
    active_jobs: int = 0
    reputation: float = 0.0


@dataclass
class Registry:
    providers: dict[str, ProviderState] = field(default_factory=dict)

    def register(self, signed: SignedProfile) -> ProviderState:
        if not signed.valid():
            raise PermissionError("provider profile signature does not match its address")
        state = ProviderState(signed.profile)
        self.providers[signed.profile.address] = state
        return state

    def set_available(self, address: str, available: bool) -> None:
        self.providers[address].available = available


def ineligibility(req: Requirements, p: ProviderState) -> str | None:
    """Why a provider can't take the job, or None if it can."""
    prof = p.profile
    if not p.available:
        return "unavailable"
    if p.active_jobs >= prof.max_concurrent_jobs:
        return "at capacity"
    if prof.cpu_cores < req.cpu_cores:
        return f"cpu {prof.cpu_cores} < {req.cpu_cores}"
    if prof.ram_mb < req.ram_mb:
        return f"ram {prof.ram_mb} < {req.ram_mb}"
    if req.runtime not in prof.runtimes:
        return f"runtime {req.runtime} not offered"
    if req.gpu_required and prof.gpu_model is None:
        return "gpu required"
    if req.gpu_required and prof.vram_mb < req.min_vram_mb:
        return f"vram {prof.vram_mb} < {req.min_vram_mb}"
    return None


def _surplus(req: Requirements, prof: ProviderProfile) -> float:
    s = (prof.cpu_cores - req.cpu_cores) / prof.cpu_cores + (prof.ram_mb - req.ram_mb) / prof.ram_mb
    if req.gpu_required:
        s += (prof.vram_mb - req.min_vram_mb) / max(prof.vram_mb, 1)
    return s


def match(req: Requirements, providers: list[ProviderState]) -> list[ProviderState]:
    eligible = [p for p in providers if ineligibility(req, p) is None]
    return sorted(eligible, key=lambda p: (p.active_jobs, (not req.gpu_required) and p.profile.gpu_model is not None,
                                           round(_surplus(req, p.profile), 9), -p.reputation, p.profile.address.lower()))
