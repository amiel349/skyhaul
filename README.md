# Skyhaul

A drone delivery operations platform, built to show three things end to end:

- **Test automation infrastructure**: a layered test pyramid, a fault-injecting drone simulator, parallel isolated E2E runs with pytest + Playwright, and evidence on every failure.
- **AI from the first commit**: an MCP server for the simulator, a Claude-powered dispatcher copilot with evals, and an agent that triages failed tests.
- **A full-stack product**: Flask services, PostgreSQL, RabbitMQ, Redis, and a Vue 3 + TypeScript dispatcher UI with a live map.

> Status: **M0 · Foundations**. The architecture is designed; services arrive milestone by milestone.

## Architecture

```mermaid
flowchart LR
  UI[Dispatcher UI<br/>Vue 3 + TS] -- REST / WS --> API[Fleet API<br/>Flask]
  UI -- chat --> COP[Copilot<br/>Claude tool use]
  COP -- tools --> API
  API -- commands --> MQ[(RabbitMQ)]
  MQ -- commands --> SIM[Drone simulator<br/>3 threads]
  SIM -- telemetry --> MQ
  MQ -- telemetry --> ING[Telemetry ingestor]
  ING --> PG[(PostgreSQL)]
  ING -- pub --> RD[(Redis)]
  RD -- sub --> API
  API --> PG
  PW[Playwright runner] -. drives .-> UI
  PW -. control API .-> SIM
  MCP[MCP server] -. control API .-> SIM
```

## Quick start

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Node 26, pnpm, gitleaks, pre-commit.

```sh
make setup   # dependencies and git hooks
make env     # .env with generated secrets
make up      # local stack on 127.0.0.1
make check   # lint, types, tests, secret scan
```

## Use the simulator from Claude Code

The repo's `.mcp.json` registers the `skyhaul-sim` MCP server (`services/mcp_sim/`, stdio). It holds only `${VAR}` references; the server reads its secrets from the environment Claude Code starts in, and never reads `.env`.

1. `make env` once, then `make sim` (RabbitMQ and the simulator with its control API on 127.0.0.1:8100). Add `SIM_DEBUG_TAP=1` to `.env` to also watch telemetry in the RabbitMQ management UI (queue `debug.telemetry`).
2. Start Claude Code in the repo with only the three variables the server needs:
   ```sh
   export $(grep -E '^(SIM_CONTROL_TOKEN|RABBITMQ_USER|RABBITMQ_PASSWORD)=' .env | xargs) && claude
   ```
   Don't source all of `.env` (or use a direnv `dotenv`): that would also export `ANTHROPIC_API_KEY`, which Claude Code itself may use instead of your own login. Optional: `SIM_CONTROL_URL`, `RABBITMQ_HOST`, `RABBITMQ_PORT` (defaults `http://127.0.0.1:8100`, `127.0.0.1`, `5672`).
3. Approve the project server, and check `/mcp` lists `skyhaul-sim`. Without the variables the server still starts, and every tool answers "not configured: set ...".
4. Try: *"Spawn 5 drones in dev. ARM dev-d001, take it off to 20 m, send it with GOTO to 32.001, 34.8 at 20 m, then read its POSITION telemetry for 10 seconds."*

Tools: `spawn_drones`, `list_drones`, `get_drone`, `inject_fault`, `clear_faults`, `reset_namespace` (control API), and `send_command`, `read_telemetry` (RabbitMQ, through the Codec, like a ground station). See ADR 0007.

## Roadmap

| Milestone | Scope |
|---|---|
| M0 | Foundations: repo, tooling, CI, security guardrails, agent team |
| M1 | Message contracts and the drone simulator, with an MCP server |
| M2 | Fleet backend: Flask API, ingestor, PostgreSQL, Redis, realtime |
| M3 | Dispatcher UI |
| M4 | Automation infrastructure |
| M5 | AI copilot with evals |
| M6 | AI test intelligence: failure triage |
| M7 | Demo deploy and write-up |

## How it's built

Development uses a team of Claude Code subagents defined in [`.claude/agents/`](.claude/agents): product, architect, backend, frontend, automation, QA and security. Each owns one area of the repo. A human approves every issue and merges every PR. See [`CLAUDE.md`](CLAUDE.md) for the workflow and [`docs/adr/`](docs/adr) for design decisions.

## License

MIT
