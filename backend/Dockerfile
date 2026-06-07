# Production image for Render. Multi-stage: build deps once, copy app.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

# uv for dependency install
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /usr/local/bin/uv

WORKDIR /app

# Install deps first for layer cache.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Copy app.
COPY tenk_signal ./tenk_signal
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

# Ensure the uv-created environment is on PATH so uvicorn and other scripts are available.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Render injects $PORT; default 8000 for local docker run.
CMD ["sh", "-c", "uvicorn tenk_signal.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
