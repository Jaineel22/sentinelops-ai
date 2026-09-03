# MLflow tracking + model-registry server for local development (Phase 6).
#
#   docker build -f docker/mlflow/Dockerfile -t sentinelops-ai:mlflow .
#
# The official MLflow image does not bundle a PostgreSQL driver, so we layer a
# pinned psycopg2-binary on top — the `mlflow` Compose service uses a
# `--backend-store-uri postgresql://…` pointing at a dedicated `mlflow` database
# on the existing Postgres instance. Artifacts are served over HTTP by the
# server itself (`--serve-artifacts`), so no client needs a shared volume.
FROM ghcr.io/mlflow/mlflow:v2.22.1

RUN pip install --no-cache-dir "psycopg2-binary==2.9.12"
