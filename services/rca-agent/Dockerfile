# rca-agent image (Phase 4). Build context is the repo root.
#   docker build -f services/rca-agent/Dockerfile -t sentinelops/rca-agent .
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY libs/sentinelops_common ./libs/sentinelops_common
COPY services/rca-agent/rca_agent ./services/rca-agent/rca_agent
RUN pip install --no-cache-dir ".[rca]"

# Alembic config + migration scripts are needed at runtime (not packaged in the wheel).
COPY services/rca-agent/alembic.ini ./alembic.ini
COPY services/rca-agent/migrations ./migrations

RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# Default: the investigation API + incident.opened consumer. The rca-migrate
# one-shot overrides this with `alembic upgrade head` in compose.
CMD ["python", "-m", "rca_agent"]
