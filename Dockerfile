# Phase 0 image: runs only the minimal FastAPI foundation app.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package first so its layer caches independently of source churn.
COPY pyproject.toml README.md LICENSE ./
COPY apps/api/sentinelops_api ./apps/api/sentinelops_api
RUN pip install --no-cache-dir .

# Drop privileges.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "sentinelops_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
