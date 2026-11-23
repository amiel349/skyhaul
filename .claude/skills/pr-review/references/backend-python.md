# Backend · Python and Flask

Applies to `services/**`.

## Structure
- Flask app factory (`create_app`) and blueprints. No app object created at import time, no module-level DB or broker connections.
- Config comes from environment variables, read once at startup into a typed config object. Missing required config fails startup with a clear message. Secrets have no defaults.
- Route handlers stay thin: parse and validate input, call a service function, map the result to a response. Business logic does not live in routes or in SQLAlchemy models.
- Functions do one thing. No speculative abstractions, base classes with one subclass, or config flags nobody sets.

## Types and validation
- Full type hints; mypy strict passes without `# type: ignore` unless a comment explains why.
- Every request body, query parameter and path parameter is validated (flask-smorest/Pydantic schemas). Unknown fields are rejected or explicitly ignored, never silently stored.
- Validate at the edge, then trust typed objects inside. No `dict[str, Any]` flowing through business logic.

## HTTP API
- Correct status codes: 201 with the created resource, 204 for empty success, 400/422 for bad input, 401 unauthenticated, 403 forbidden, 404 not found (also for resources in another tenant), 409 conflicts.
- Error responses share one JSON shape and never include stack traces, SQL, or internal paths.
- Endpoints that change state are idempotent where retries are likely (commands carry a client `msg_id`).
- Lists are paginated and bounded. No endpoint returns an unbounded table.
- Every new endpoint appears in the OpenAPI spec with request and response schemas.

## Database (SQLAlchemy 2, Alembic, PostgreSQL)
- Every query on tenant data filters by tenant. A query without a tenant filter on a tenant table is blocking.
- No string-built SQL. Use the ORM or bound parameters.
- Schema changes come with an Alembic migration that is reversible, and never edit an applied migration.
- Watch for N+1 queries in list endpoints; use `selectinload` or joins.
- Transactions are explicit and short. No network calls (RabbitMQ, HTTP, LLM) inside an open transaction.
- Indexes exist for the columns the new queries filter and sort on.
- Timestamps are timezone-aware UTC.

## Errors and resources
- No bare `except:` or `except Exception: pass`. Catch what you can handle; let the rest reach the error handler and get logged.
- Connections, channels, files and sessions are closed with context managers.
- Timeouts on every outbound call (HTTP, broker, LLM). No call that can hang forever.
- Retries are bounded, with backoff, and only for errors that are actually transient.

## Logging
- JSON logs with `correlation_id`, `drone_id`, and `tenant_id` where they apply.
- Log levels mean something: `error` needs action, `warning` is unexpected but handled, `info` is a business event.
- Never log passwords, tokens, API keys, full headers, or whole request bodies.

## Workers (ingestor and other consumers)
- Manual ack after the work is committed, never before.
- A poison message is logged and dead-lettered, not requeued forever.
- Batch writes have a size and time bound.
- Graceful shutdown on SIGTERM: stop consuming, finish in-flight work, close connections.
