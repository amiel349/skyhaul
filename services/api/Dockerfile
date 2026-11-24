# Build from the repo root: docker build -f services/api/Dockerfile .
# The same image runs the API and the one-shot `migrate` (skyhaul-migrate upgrade head).
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Build the venv at the same path it runs from: venv scripts hard-code their interpreter path.
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY contracts/pyproject.toml ./contracts/pyproject.toml
COPY contracts/src ./contracts/src
COPY services/fleet_core ./services/fleet_core
COPY services/api ./services/api
RUN uv sync --frozen --no-dev --package skyhaul-api --no-editable

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# Healthy means ready: Postgres and Redis answer (and, from #26, the command publisher).
HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=4)"]
# One process, 32 threads (m2-design section 1): the command publisher thread is per process.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--worker-class", "gthread", "--threads", "32", "skyhaul_api:create_app()"]
