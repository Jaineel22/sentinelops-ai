# remediation-controller image (Phase 5, 5A-5G). Build context is the repo root.
#   docker build -f services/remediation-controller/Dockerfile -t sentinelops/remediation-controller .
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY libs/sentinelops_common ./libs/sentinelops_common
COPY services/remediation-controller/remediation_controller ./services/remediation-controller/remediation_controller
RUN pip install --no-cache-dir ".[remediation]"

# Alembic config + migration scripts are needed at runtime (not packaged in the wheel).
COPY services/remediation-controller/alembic.ini ./alembic.ini
COPY services/remediation-controller/migrations ./migrations

RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# Default: the approval + execution + verification API, which also publishes
# remediation.events lifecycle events (5G). The remediation-migrate one-shot
# overrides this with `alembic upgrade head` in compose. The only executor is
# LocalSimulationExecutor — no real infrastructure is touched.
CMD ["python", "-m", "remediation_controller"]
