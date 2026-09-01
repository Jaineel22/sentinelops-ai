# anomaly-detector image (Phase 3). Build context is the repo root.
#   docker build -f services/anomaly-detector/Dockerfile -t sentinelops/anomaly-detector .
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY libs/sentinelops_common ./libs/sentinelops_common
COPY services/anomaly-detector/anomaly_detector ./services/anomaly-detector/anomaly_detector
# The committed run_a telemetry is bundled so the detector can train at first start.
COPY ml ./ml
RUN pip install --no-cache-dir ".[ml,detector]"

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/artifacts/models \
    && chown -R appuser /app/artifacts
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["python", "-m", "anomaly_detector"]
