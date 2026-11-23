# Build from the repo root: docker build -f services/api/Dockerfile .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
COPY services/api ./services/api
RUN uv sync --frozen --no-dev --package skyhaul-api --no-editable

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
COPY --from=build /src/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "skyhaul_api:create_app()"]
