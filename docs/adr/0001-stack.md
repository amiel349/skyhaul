# 0001 · Technology stack

Status: accepted · 2026-11-23

## Context

The project has to show a full-stack product and a serious automation infrastructure, in a stack the author can defend in depth.

## Decision

- **Backend:** Python 3.12, Flask (app factory, blueprints), flask-smorest for OpenAPI, SQLAlchemy 2, Alembic.
- **Data:** PostgreSQL for relational data (users, roles, missions, audit log, telemetry). Redis for live state and pub/sub.
- **Messaging:** RabbitMQ topic exchanges between the backend and the drones.
- **Frontend:** Vue 3, TypeScript, Vite, Pinia, Vue Router.
- **Tests:** pytest and Playwright for Python, Vitest and Vue Testing Library for the UI.
- **Tooling:** uv workspace, ruff, mypy strict, pnpm, ESLint, vue-tsc, pre-commit.

## Consequences

- One language (Python) across the simulator, backend, test clients and E2E tests, so fixtures and clients are shared.
- TypeScript is pinned to 5.9 because vue-tsc and typescript-eslint don't support TypeScript 7 yet.

## Alternatives considered

- FastAPI: async and typed, but Flask is the author's production stack and flask-smorest still gives an OpenAPI spec.
- MongoDB: the data is relational, and PostgreSQL shows SQL work.
- Vuex: Pinia is Vue's official store.
- Playwright for TypeScript: more common, but would split the test stack across two languages.
