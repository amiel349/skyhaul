# Skyhaul

Drone delivery operations platform. Dispatchers watch a live fleet, plan deliveries and talk to an AI copilot. The drones are a separate system behind a shared message interface, so a multithreaded simulator plays their side. A pytest + Playwright framework tests the whole stack, and an AI agent triages failures.

The test automation infrastructure is the centerpiece. Treat test code with the same care as product code.

## Layout

| Path | What | Owner agent |
|---|---|---|
| `contracts/` | JSON Schemas, codec, generated Pydantic + TS types | architect |
| `services/api/` | Flask fleet API | backend |
| `services/ingestor/` | Telemetry worker | backend |
| `services/copilot/` | AI assistant service | backend |
| `services/drone_sim/` | 3-thread simulator + control API | backend |
| `services/mcp_sim/` | MCP server over the control API | backend |
| `web/` | Vue 3 + TypeScript dispatcher UI | frontend |
| `tests/` | Framework, API E2E, UI E2E, evals | automation |
| `tools/` | Fake LLM, triage agent | automation |
| `deploy/` | Compose, Dockerfiles, k8s | automation |
| `docs/adr/` | Architecture decision records | architect |
| `docs/architecture/` | Milestone design notes | architect |
| `docs/security/` | Threat model | security |
| `docs/product/` | Product notes | product |

Folders appear when their milestone starts. Today `contracts/` exists, plus `services/api/` and `web/` as stubs.

## Commands

```sh
make setup      # uv sync, pnpm install, pre-commit install
make env        # create .env with generated secrets (never commit it)
make up         # start postgres, rabbitmq, redis, api (ports on 127.0.0.1)
make check      # lint, typecheck, tests, contracts drift check, secret scan: what CI runs
make contracts  # regenerate Pydantic models and TS types after changing contracts/schemas
make test-integration  # Testcontainers tests (needs Docker); `make test` skips them
```

Python: `uv run pytest`, `uv run ruff check .`, `uv run mypy`. Web (in `web/`): `pnpm lint`, `pnpm typecheck`, `pnpm test`.

## Key design rules

- Every message has an envelope: `msg_id`, `correlation_id`, `drone_id`, `type`, `schema_version`, `ts`. Commands go to the `fleet.cmd` topic exchange, telemetry to `fleet.telemetry`, routing key `drone.<id>.<TYPE>` (types are uppercase). All encoding and decoding goes through `skyhaul_contracts.Codec`; the schemas in `contracts/schemas/` are the source of truth. See `contracts/README.md` and `docs/adr/0005-contract-versioning.md`.
- Simulator: three long-lived threads. The consumer (main) decodes and dispatches. The scheduler ticks every 100 ms. The publisher is the only thread that sends, on its own connection. Periodic state lives on an in-memory jobs board behind one lock. Handlers never sleep and never publish. See `docs/adr/0003-simulator-threading.md`.
- Tests isolate by namespace plus a reset call, not by a fresh environment per test. See `docs/adr/0004-test-isolation.md`.
- Push every check to the lowest test layer that can prove it.

## Security rules (public repo)

- Never commit secrets. Only `.env.example` is in git, with empty values. gitleaks runs in pre-commit and CI.
- Never read or print `.env`. Secrets reach code through environment variables only, with no defaults.
- Never log tokens, passwords, API keys or full request headers. Scrub test evidence before upload.
- Permission and tenant checks happen on the server, on every endpoint and socket event.
- No personal data of the author, and nothing from the author's past or current employers.

## Workflow

- Trunk-based. `main` is protected. One task = one branch (`feat/m1-jobs-board`, `fix/m2-ack-timeout`) = one PR.
- Conventional commits (`feat:`, `fix:`, `test:`, `ci:`, `docs:`, `chore:`), squash merge.
- Per task: product writes the issue, architect writes the design note and lands contracts first, backend and frontend implement (in parallel, separate worktrees), automation writes E2E tests from the acceptance criteria, QA verifies, security reviews, then a PR. The human approves the issue and merges every PR.
- Small tasks skip agents that add nothing.
- On-demand AI review: comment `/claude-review` on a PR (optionally `/claude-review sonnet`; opus, sonnet or haiku), or run `gh workflow run claude-review.yml -f pr=N -f model=opus`. It follows the `pr-review` skill (`.claude/skills/pr-review/`): one checklist of best practices per area. It is advisory; address or answer its comments before merging. Run `/pr-review` locally to catch the same things earlier.
