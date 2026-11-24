# Build from the repo root: docker build -f services/drone_sim/Dockerfile .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Build the venv at the same path it runs from: venv scripts hard-code their interpreter path.
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY contracts ./contracts
COPY services/drone_sim ./services/drone_sim
RUN uv sync --frozen --no-dev --package skyhaul-drone-sim --no-editable

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
# The control API's /healthz: 200 only while every thread and both connections are up.
# SIM_CONTROL_TOKEN is never set here or passed as a build arg; it comes from the runtime env.
EXPOSE 8100
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=2)"]
# Exec form, so SIGTERM reaches the simulator as PID 1.
CMD ["skyhaul-drone-sim"]
