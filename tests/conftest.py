from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _have(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs_stack = pytest.mark.skipif(
    not (shutil.which("anvil") and _have(["docker", "image", "inspect", "stesh-mvp/logreg-sgd:1"])
         and (ROOT / "contracts/out/JobEscrow.sol/JobEscrow.json").exists()),
    reason="needs anvil, `forge build`, and the workload image (make image)")


@pytest.fixture()
def runs_dir():
    # Under the repo (not /tmp) so Docker Desktop/Colima can mount it into the container.
    d = ROOT / ".runs" / "tests"
    if d.exists():
        shutil.rmtree(d)  # fail loudly if leftovers can't be removed
    d.mkdir(parents=True)
    yield d
