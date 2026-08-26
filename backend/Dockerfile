# SystemDecoded backend image.
# Shared by the api, worker, scheduler and migrate services — same code, same
# dependencies, different command. Media tooling (FFmpeg, Playwright, Kokoro)
# is deliberately absent until Phase 2 needs it; ARCH §15.2 notes the worker
# image will diverge and grow at that point.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl  -> the api container's healthcheck
# procps -> pgrep, used by the worker and scheduler healthchecks
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies against a stub package first, so this layer stays cached
# when only source changes. `pip install -e` needs the package directory to
# exist, hence the stub; the real package is installed after the COPY below.
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
    && pip install --no-cache-dir -e ".[dev]"

COPY . .
RUN pip install --no-cache-dir -e ".[dev]"

# Non-root. Owns /app and /media so migrations and renders can write.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /media \
    && chown -R appuser:appuser /app /media
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
