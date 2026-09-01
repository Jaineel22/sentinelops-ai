# Reproducible environment for the Phase 2 ML pipeline.
#   docker build -f ml/Dockerfile -t sentinelops-ai:ml .
#   docker run --rm -v "$PWD/artifacts:/app/artifacts" sentinelops-ai:ml run all
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY apps/api/sentinelops_api ./apps/api/sentinelops_api
COPY apps/orders-service/orders_service ./apps/orders-service/orders_service
COPY ml ./ml
RUN pip install --no-cache-dir ".[ml]"

RUN useradd --create-home --uid 1000 mluser && mkdir -p artifacts && chown -R mluser artifacts
USER mluser

# Runs experiments on the committed datasets by default.
ENTRYPOINT ["python", "-m", "ml.experiments"]
CMD ["run", "all"]
