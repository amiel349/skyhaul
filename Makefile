COMPOSE := docker compose --env-file .env -f deploy/compose.yaml

.PHONY: help setup env up down logs ps lint typecheck test check secrets

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-11s %s\n", $$1, $$2}'

setup: ## Install Python and web dependencies and git hooks
	uv sync
	cd web && pnpm install
	pre-commit install

env: ## Create .env with generated secrets
	./scripts/gen-env.sh

up: ## Start the local stack
	$(COMPOSE) up -d --build --wait

down: ## Stop the local stack
	$(COMPOSE) down

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

test: ## Run unit tests
	uv run pytest
	cd web && pnpm test

secrets: ## Scan the whole git history for secrets
	gitleaks git --redact --no-banner -v

check: lint typecheck test secrets ## Run everything CI runs
