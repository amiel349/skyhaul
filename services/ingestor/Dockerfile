# Build from the repo root: docker build -f services/ingestor/Dockerfile .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Build the venv at the same path it runs from: venv scripts hard-code their interpreter path.
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY contracts ./contracts
COPY services/fleet_core ./services/fleet_core
COPY services/ingestor ./services/ingestor
RUN uv sync --frozen --no-dev --package skyhaul-ingestor --no-editable

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
# No HTTP server (the ingestor is one thread): the loop touches this file every second.
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD ["python", "-c", "import os, sys, time; sys.exit(time.time() - os.path.getmtime('/tmp/skyhaul-ingestor.alive') > 15)"]
# Exec form, so SIGTERM reaches the ingestor as PID 1 and it flushes before exiting.
CMD ["skyhaul-ingestor"]
