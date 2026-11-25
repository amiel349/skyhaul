# SKYHAUL_ENV picks the environment: dev (default) or prod. In dev, an optional git-ignored .env.dev is
# layered over .env (later files win) and `make up` also starts the dev-only dev-admin service.
SKYHAUL_ENV ?= dev
DEV_ENV_FILE := $(if $(filter dev,$(SKYHAUL_ENV)),$(wildcard .env.dev))
COMPOSE := docker compose --env-file .env $(addprefix --env-file ,$(DEV_ENV_FILE)) -f deploy/compose.yaml

.PHONY: help setup env up sim seed down down-volumes logs ps lint typecheck test web-components web-tests-check test-integration contracts contracts-check openapi openapi-check migrate check secrets

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-17s %s\n", $$1, $$2}'

setup: ## Install Python and web dependencies, the test browser and git hooks
	uv sync
	cd web && pnpm install
	cd web && pnpm exec playwright install chromium
	pre-commit install

env: ## Create .env with generated secrets
	./scripts/gen-env.sh

up: ## Start the local stack (SKYHAUL_ENV=dev default; =prod skips .env.dev and the dev admin)
	$(COMPOSE) up -d --build --wait
# A run, not part of `up --wait`: --wait treats a one-shot that exited as a failure.
	$(if $(filter dev,$(SKYHAUL_ENV)),$(COMPOSE) --profile dev run --rm dev-admin)

sim: ## Start RabbitMQ and the drone simulator with its control API (test profile)
	$(COMPOSE) --profile test up -d --build --wait rabbitmq drone-sim

seed: ## Create a local admin and a few drones (needs make up and make sim)
	python3 scripts/seed.py

down: ## Stop the local stack
	$(COMPOSE) --profile dev --profile test down

# `make down -v` would be make's own -v (--version), so the volume reset has its own target.
# It addresses the stack by project name, not by compose file: the file needs every .env key to
# interpolate, so an .env from before a new key was added would make `down` fail.
down-volumes: ## Stop the local stack and delete its volumes (the database)
	docker compose -p skyhaul down -v --remove-orphans

logs: ## Follow logs of the local stack
	$(COMPOSE) logs -f

ps: ## Show stack status
	$(COMPOSE) ps

lint: ## Lint Python and web code
	uv run ruff check .
	uv run ruff format --check .
	cd web && pnpm lint

typecheck: ## Type-check Python and web code
	uv run mypy
	cd web && pnpm typecheck

test: ## Run unit, contract and concurrency tests
	uv run pytest -m "not integration"
	cd web && pnpm test

web-components: ## Run the web component tests in headless Chromium (pnpm exec playwright install chromium once)
	cd web && pnpm test:components

web-tests-check: ## Fail if a web .vue file has no sibling test file
	cd web && pnpm check:tests

test-integration: ## Run integration tests (needs Docker for Testcontainers)
	uv run pytest -m integration

contracts: ## Regenerate Pydantic models and TS types from contracts/schemas
	uv run python contracts/scripts/generate.py

contracts-check: ## Fail if the generated contracts are out of date
	uv run python contracts/scripts/generate.py --check

OPENAPI_CONTRACT := contracts/openapi/fleet-api.v1.json
SPEC_APP := 'skyhaul_api:create_spec_app()'

# The API TS types (contracts/ts/api) are generated with the other contract types, so
# `make contracts` regenerates them and `make contracts-check` catches their drift.
openapi: contracts ## Regenerate the API TS types from the OpenAPI contract (same as contracts)

openapi-check: ## Fail if the API's exported spec differs from the OpenAPI contract
	uv run flask --app $(SPEC_APP) spec --check --contract $(OPENAPI_CONTRACT)

migrate: ## Run the database migrations of the local stack
	$(COMPOSE) run --rm migrate

secrets: ## Scan the whole git history for secrets
	gitleaks git --redact --no-banner -v

check: lint typecheck test web-components web-tests-check contracts-check openapi-check secrets ## Run everything CI runs (except integration)
