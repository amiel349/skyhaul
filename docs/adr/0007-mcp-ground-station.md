# 0007 · MCP server as a ground station

Status: accepted · 2026-11-23

## Context

Developers working in Claude Code want to spawn drones, fly one and inject faults without writing curl calls. The simulator has two surfaces: the test-only control API (spawn, state, faults, reset) and the shared AMQP interface (commands and telemetry). The question is which surface the MCP tools use for commands, and how the server gets its secrets without anyone reading `.env`.

## Decision

- **`services/mcp_sim`** is a stdio MCP server (`skyhaul-mcp-sim`, Python SDK) registered in the repo's `.mcp.json`.
- **Control tools** (`spawn_drones`, `list_drones`, `get_drone`, `inject_fault`, `clear_faults`, `reset_namespace`) call the control API with the bearer token.
- **Command and telemetry tools** (`send_command`, `read_telemetry`) act as a ground station on RabbitMQ: they build and encode every command through the Codec, publish on `fleet.cmd` with `mandatory=True`, and read replies and telemetry from exclusive auto-delete queues bound to `drone.<id>.*`.
- **The control API has no command passthrough.**
- **Secrets are environment-only.** `.mcp.json` holds `${SIM_CONTROL_TOKEN}`, `${RABBITMQ_USER}` and `${RABBITMQ_PASSWORD}` references and no values. The human exports the variables (for example with direnv). Missing variables make every tool answer "not configured"; the server does not crash.
- Tool results are JSON data, and errors never contain the token or the password. Drone text (alert messages, NACK details) is returned as data, never as instructions.

## Consequences

- The MCP path exercises the real contract, so it doubles as a manual end-to-end check of the Codec and the simulator.
- The MCP server needs broker credentials as well as the control token.
- The fleet API remains the only production commander; the MCP server only exists for local development against the test profile.

## Alternatives considered

- A `POST /drones/{id}/commands` passthrough on the control API: simpler for the MCP server, but a second command path that bypasses the contract and the queue, and a backdoor a test could start depending on.
- Letting the MCP server read `.env`: convenient, but against the project rule that secrets reach code through environment variables only.
- An HTTP MCP transport: unnecessary for a local developer tool and a wider attack surface.
