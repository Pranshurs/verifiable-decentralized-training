from __future__ import annotations

import pytest

from stesh_mvp import scenario
from stesh_mvp.checkpoints import CheckpointError, CheckpointStore, new_job_key
from tests.conftest import needs_stack

JOB, OTHER = "0x" + "aa" * 32, "0x" + "bb" * 32


@pytest.fixture()
def store(tmp_path):
    return CheckpointStore(tmp_path / "store")


def test_round_trip(store):
    k = new_job_key()
    m = store.seal(k, JOB, 3, b'{"epoch": 3}')
    assert store.open(k, m) == b'{"epoch": 3}'


def test_tampered_ciphertext_is_rejected(store):
    k = new_job_key()
    m = store.seal(k, JOB, 1, b"state")
    obj = store.root / "objects" / m.object_id
    data = bytearray(obj.read_bytes())
    data[0] ^= 1
    obj.write_bytes(bytes(data))
    with pytest.raises(CheckpointError, match="content address"):
        store.open(k, m)


def test_wrong_key_and_moved_or_renumbered_checkpoints_fail_authentication(store):
    k = new_job_key()
    m = store.seal(k, JOB, 2, b"state")
    for bad in (lambda: store.open(new_job_key(), m),
                lambda: store.open(k, m.__class__(**{**m.__dict__, "job_id": OTHER})),
                lambda: store.open(k, m.__class__(**{**m.__dict__, "seq": 9}))):
        with pytest.raises(CheckpointError, match="authentication failed"):
            bad()


def test_latest_valid_skips_corrupt_and_refuses_stale(store):
    k = new_job_key()
    for seq in range(4):
        store.seal(k, JOB, seq, f"s{seq}".encode())
    newest = max(store.index(JOB), key=lambda m: m.seq)
    (store.root / "objects" / newest.object_id).write_bytes(b"garbage")
    meta, pt = store.latest_valid(k, JOB)
    assert (meta.seq, pt) == (2, b"s2")
    with pytest.raises(CheckpointError, match="stale"):
        store.latest_valid(k, JOB, min_seq=3)  # the provider announced 3; only 2 survives


@needs_stack
def test_provider_failure_is_recovered_by_another_provider(runs_dir):
    net = scenario.start(runs_dir)
    try:
        job = scenario.make_job(net)
        r = scenario.recovery_run(net, job, fail_after_epoch=8)
        assert r["a_failed"] and r["a_exit"] == 75
        assert r["provider_a"] != r["provider_b"]
        assert r["resumed_from_seq"] == 8 and r["b_exit"] == 0
        assert r["report"].accepted, r["report"].failures
        assert r["final_state"] == "Released"
        assert net.escrow.job(job.job_id)["provider"] == r["provider_b"]  # only B can be paid
    finally:
        net.close()
