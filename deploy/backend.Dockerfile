# The backend image for the cloud machine: backend/Dockerfile, plus gunicorn to serve the API
# and the cloud settings. Built from the repo root (deploy/compose.yml). Keep the first part
# in step with backend/Dockerfile.
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# Debian's ffmpeg can draw text and subtitles (drawtext, subtitles); DejaVu is the font it
# draws them in.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
RUN uv pip install gunicorn==23.0.0
COPY backend/ .
COPY deploy/cloud_settings.py adforge/cloud_settings.py
