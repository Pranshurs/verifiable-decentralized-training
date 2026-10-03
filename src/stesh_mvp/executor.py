"""Run an allow-listed workload image in a locked-down Docker container (white paper §3.1).

The job spec names a workload (``logreg-sgd@1``), never an image, a command or a shell
string. The registry below maps that name to a locally built image whose entrypoint is
fixed. Untrusted input reaches the container only as files in a read-only ``/in``.

Container settings: no network, read-only root filesystem, every Linux capability
dropped, no-new-privileges, PID/memory/CPU limits from the signed requirements, a small
tmpfs ``/tmp``, and ``/out`` as the only writable mount. It runs as the provider agent's
own unprivileged uid:gid, so outputs belong to the agent that has to hash and hand them
over. If the agent itself runs as root, the container runs as nobody (65534). On timeout,
the container is killed and removed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .jobs import JobSpec

WORKLOADS = {
    "logreg-sgd@1": {"image": "stesh-mvp/logreg-sgd:1", "runtime": "python3.12-numpy2",
                     "dockerfile": "workloads/logreg-sgd/Dockerfile"},
}


@dataclass
class ExecResult:
    exit_code: int
    timed_out: bool
    seconds: float
    container_start_seconds: float | None
    stdout: str
    stderr: str


def stage_inputs(spec: JobSpec, job_id: str, data_files: dict[str, Path], in_dir: Path,
                 resume: Path | None = None) -> None:
    in_dir.mkdir(parents=True, exist_ok=True)
    for ref in spec.inputs:
        shutil.copyfile(data_files[ref.name], in_dir / ref.name)
    (in_dir / "job.json").write_text(json.dumps({"job_id": job_id, "params": spec.params}))
    if resume is not None:
        shutil.copyfile(resume, in_dir / "resume.json")


def container_user() -> str:
    uid, gid = os.getuid(), os.getgid()
    return "65534:65534" if uid == 0 else f"{uid}:{gid}"


def docker_args(spec: JobSpec, name: str, in_dir: Path, out_dir: Path, env: dict[str, str]) -> list[str]:
    req = spec.requirements
    args = [
        "docker", "run", "--name", name, "--rm",
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", container_user(),
        "--pids-limit", "64", "--memory", f"{req.ram_mb}m", "--memory-swap", f"{req.ram_mb}m",
        "--cpus", str(req.cpu_cores), "--tmpfs", "/tmp:size=16m,noexec",
        "-v", f"{in_dir.resolve()}:/in:ro", "-v", f"{out_dir.resolve()}:/out:rw",
    ]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    return args + [WORKLOADS[spec.workload]["image"]]


def run(spec: JobSpec, in_dir: Path, out_dir: Path, env: dict[str, str] | None = None) -> ExecResult:
    if spec.workload not in WORKLOADS:
        raise ValueError(f"workload {spec.workload} is not allow-listed")
    out_dir.mkdir(parents=True, exist_ok=True)
    if container_user() == "65534:65534":
        out_dir.chmod(0o777)  # root agent: the container writes as nobody
    name = f"stesh-{uuid.uuid4().hex[:12]}"
    t0 = time.perf_counter()
    proc = subprocess.Popen(docker_args(spec, name, in_dir, out_dir, env or {}),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=spec.timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        out, err = proc.communicate()
        timed_out = True
    return ExecResult(proc.returncode if not timed_out else -9, timed_out, time.perf_counter() - t0,
                      None, out[-4000:], err[-4000:])


def container_start_latency(image: str) -> float:
    """Seconds to create, start and exit a no-op container from the workload image."""
    t0 = time.perf_counter()
    subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "true", image],
                   check=True, capture_output=True)
    return time.perf_counter() - t0
