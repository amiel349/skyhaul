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
# Until the control API (#9) serves /healthz, healthy means the simulator process (PID 1)
# is alive: it exits on its own when RabbitMQ stays unreachable.
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD ["python", "-c", "import os; os.kill(1, 0)"]
# Exec form, so SIGTERM reaches the simulator as PID 1.
CMD ["skyhaul-drone-sim"]
