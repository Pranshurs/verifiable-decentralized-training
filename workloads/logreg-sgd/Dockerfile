# Workload image for logreg-sgd@1. Built from the repository root:
#   docker build -f workloads/logreg-sgd/Dockerfile -t stesh-mvp/logreg-sgd:1 .
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
RUN pip install --no-cache-dir numpy==2.5.3
COPY src/stesh_mvp/workloads/logreg_sgd.py /app/logreg_sgd.py
# Runs as nobody; the executor also forces --user, --read-only, --network none, --cap-drop ALL.
USER 65534:65534
ENTRYPOINT ["python", "/app/logreg_sgd.py", "/in", "/out"]
