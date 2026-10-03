"""Signed job specs: any change to what is run, where, or for how much breaks the signature."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stesh_mvp.chain import dev_keys
from stesh_mvp.crypto import CanonicalError, address_of, canonical
from stesh_mvp.jobs import (
    InputRef,
    JobSpec,
    Requirements,
    SignedJob,
    new_nonce,
    sign_job,
)

KEY, OTHER = dev_keys(2)


def spec(**over) -> JobSpec:
    base = dict(chain_id=31337, escrow=address_of(OTHER), coder=address_of(KEY), nonce=new_nonce(),
                workload="logreg-sgd@1", params={"epochs": 5, "seed": 1},
                inputs=(InputRef(name="data.csv", sha256="a" * 64, size=10),),
                requirements=Requirements(cpu_cores=1, ram_mb=512, runtime="python3.12-numpy2"),
                timeout_s=60, expected_outputs=("model.json",), payment_wei=10**17, deadline=2_000_000_000)
    return JobSpec(**{**base, **over})


def tampered(signed: SignedJob, **changes) -> SignedJob:
    return SignedJob(spec=signed.spec.model_copy(update=changes), signature=signed.signature)


def test_valid_signature_accepted():
    s = sign_job(spec(), KEY)
    assert s.signature_valid() and s.signer() == address_of(KEY)


@pytest.mark.parametrize("change", [
    {"requirements": Requirements(cpu_cores=1, ram_mb=64, runtime="python3.12-numpy2")},   # resource requirement
    {"workload": "logreg-sgd@2"},                                                          # workload
    {"params": {"epochs": 500, "seed": 1}},                                                # workload params
    {"payment_wei": 1},                                                                    # payment
    {"inputs": (InputRef(name="data.csv", sha256="b" * 64, size=10),)},                    # input commitment
    {"nonce": "0" * 64},                                                                   # replay nonce
    {"deadline": 2_000_000_001},
    {"escrow": address_of(KEY)},
], ids=["requirements", "workload", "params", "payment", "inputs", "nonce", "deadline", "escrow"])
def test_any_modification_is_rejected(change):
    assert not tampered(sign_job(spec(), KEY), **change).signature_valid()


def test_signature_by_someone_else_is_rejected():
    s = sign_job(spec(), OTHER)  # coder field names KEY's address
    assert not s.signature_valid()


def test_job_id_is_content_addressed_and_nonce_makes_it_unique():
    a, b = spec(nonce="1" * 64), spec(nonce="1" * 64)
    assert a.job_id() == b.job_id()
    assert spec().job_id() != spec().job_id()


def test_floats_cannot_enter_signed_data():
    with pytest.raises(CanonicalError):
        canonical({"lr": 0.1})
    with pytest.raises(ValidationError):
        spec(params={"lr": 0.1})


def test_malformed_fields_are_rejected():
    with pytest.raises(ValidationError):
        spec(coder=address_of(KEY).lower())  # not checksummed
    with pytest.raises(ValidationError):
        spec(nonce="xyz")
    with pytest.raises(ValidationError):
        spec(workload="logreg sgd; rm -rf /")
