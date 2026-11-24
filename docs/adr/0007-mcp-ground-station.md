# 0007 · MCP server as a ground station

Status: accepted · 2026-11-23 · amended 2026-11-24

## Context

Developers working in Claude Code want to spawn drones, fly one and inject faults without writing curl calls. The simulator has two surfaces: the test-only control API (spawn, state, faults, reset) and the shared AMQP interface (commands and telemetry). The question is which surface the MCP tools use for commands, and how the server gets its secrets without anyone reading `.env`.

## Decision

- **`services/mcp_sim`** is a stdio MCP server (`skyhaul-mcp-sim`, Python SDK) registered in the repo's `.mcp.json`.
- **Control tools** (`spawn_drones`, `list_drones`, `get_drone`, `inject_fault`, `clear_faults`, `reset_namespace`) call the control API with the bearer token.
- **Command and telemetry tools** (`send_command`, `read_telemetry`) act as a ground station on RabbitMQ: they build and encode every command through the Codec, publish on `fleet.cmd` with `mandatory=True`, and read replies and telemetry from exclusive auto-delete queues bound to `drone.<id>.*`. The server declares only the two exchanges and its own queue, never `drone_sim.cmd` (ADR 0006 amendment). `send_command` answers `no_reply` with an explanation when nothing comes back within `timeout_s` (at most 60 s, so a 30 s REPLY_DELAY fits).
- **Tool arguments mirror the control API.** `inject_fault(drone_id, fault)` takes a union keyed on `kind`, one model per kind matching the control API's `FaultRequest`, instead of flat optional arguments. `list_drones` defaults to namespace `dev`. Drone ids are checked against the contracts' `DroneId` pattern before any call.
- **The control API has no command passthrough.**
- **Secrets are environment-only.** `.mcp.json` holds `${SIM_CONTROL_TOKEN:-}`, `${RABBITMQ_USER:-}` and `${RABBITMQ_PASSWORD:-}` references and no values, plus `SIM_CONTROL_URL`, `RABBITMQ_HOST` and `RABBITMQ_PORT` with local defaults (the simulator's variable names). The human starts Claude Code with exactly those three secrets exported, for example `export $(grep -E '^(SIM_CONTROL_TOKEN|RABBITMQ_USER|RABBITMQ_PASSWORD)=' .env | xargs) && claude`. Exporting all of `.env` (a direnv `dotenv`, `set -a; . .env`) is not recommended: it would also export `ANTHROPIC_API_KEY`, which Claude Code itself may pick up and use instead of the developer's own login. Missing variables make every tool answer "not configured"; the server does not crash.
- **Dependency:** `mcp==1.30.0`, the 1.x line. The newest 2.x depends on `httpx2` releases younger than the project's 14-day minimum release age.
- Tool results are JSON data, and errors never contain the token or the password. Drone text (alert messages, NACK details) is returned as data, never as instructions.

## Consequences

- The MCP path exercises the real contract, so it doubles as a manual end-to-end check of the Codec and the simulator.
- The MCP server needs broker credentials as well as the control token.
- The fleet API remains the only production commander; the MCP server only exists for local development against the test profile.

## Alternatives considered

- A `POST /drones/{id}/commands` passthrough on the control API: simpler for the MCP server, but a second command path that bypasses the contract and the queue, and a backdoor a test could start depending on.
- Letting the MCP server read `.env`: convenient, but against the project rule that secrets reach code through environment variables only.
- Declaring `drone_sim.cmd` from the MCP server (like other command senders): commands sent before the simulator ever ran would be kept, but an unroutable command could then never be reported, and the developer would only see a timeout.
- An HTTP MCP transport: unnecessary for a local developer tool and a wider attack surface.
