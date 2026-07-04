# syntax=docker/dockerfile:1
#
# Backend image: FastAPI app (spec section 23) served by uvicorn.
#
# Build context is ./backend (see docker-compose.yml at repo root) — data/,
# runs/, results/, and the dataset_builder/ + evals/ packages are never baked
# into this image. Dataset building, ingestion, and eval runs stay host-side
# via `uv run --project backend ...` per the spec's scope guard; this image
# only serves the API against whatever is bind-mounted in at /app/data,
# /app/runs, /app/results.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app/backend

# Install locked dependencies first so editing app/ doesn't bust this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application source.
COPY app ./app

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# config.py resolves ROOT as two directories above this file (backend/app/config.py
# -> backend -> ROOT), so /app is ROOT inside the container: data/, runs/, and
# results/ are mounted as /app/data, /app/runs, /app/results by docker-compose.yml.
CMD ["uv", "run", "--no-sync", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
