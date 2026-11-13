# One image for the whole app: FastAPI serves the API (/api) and the built
# Svelte frontend (/) from the same origin.

# ---- 1. build the frontend ------------------------------------------------------
FROM node:24-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. backend + built frontend ------------------------------------------------
FROM python:3.12-slim
# osmium cuts areas out of the offline road extracts (Overpass fallback).
RUN apt-get update && apt-get install -y --no-install-recommends osmium-tool \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN UV_HTTP_TIMEOUT=300 uv sync --frozen --no-dev
COPY backend/app ./app
COPY backend/seed ./seed
COPY --from=frontend /src/dist ./static
ENV PATH="/app/.venv/bin:$PATH" \
    STATIC_DIR=/app/static \
    SEED_DIR=/app/seed \
    DATA_DIR=/data \
    PORT=8000
EXPOSE 8000
# Cloud Run sets $PORT; default 8000 for local / docker-compose use.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
