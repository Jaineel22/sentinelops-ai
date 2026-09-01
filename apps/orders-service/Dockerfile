# orders-service image (Phase 1). Build context is the repo root.
#   docker build -f apps/orders-service/Dockerfile -t sentinelops/orders-service .
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY libs/sentinelops_common ./libs/sentinelops_common
COPY apps/api/sentinelops_api ./apps/api/sentinelops_api
COPY apps/orders-service/orders_service ./apps/orders-service/orders_service
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# Default: the HTTP API. The demo consumer overrides this in compose.
CMD ["python", "-m", "orders_service.app"]
