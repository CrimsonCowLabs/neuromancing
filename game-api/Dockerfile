# Build context is the repo root (see docker-compose.yml) so we can vendor the
# shared package alongside the service. This one image runs game-api web, the
# Temporal worker, and market-ingest (different commands per compose service).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY shared ./shared
COPY game-api ./game-api

WORKDIR /app/game-api
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
