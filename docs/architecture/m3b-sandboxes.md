# M3b visitor sandboxes: implementation plan

Decision and reasons: [ADR 0019](../adr/0019-visitor-sandboxes.md). Contract: new paths in [`contracts/openapi/fleet-api.v1.json`](../../contracts/openapi/fleet-api.v1.json) and the `sandbox.updated` socket event, written by the architect in issue 1. Seven issues, landed in this order; each keeps `make check` green on its own, and every issue after 1 builds against the contract from 1.

## Order of work

1. **Contracts** (architect)
2. **Namespace scoping and the isolation test** (backend)
3. **Socket rooms per namespace** (backend)
4. **Visitor sessions** (backend)
5. **Sandbox manager and reaper** (backend; deploy part with automation)
6. **Faults** (backend)
7. **UI** (frontend; can start against fake endpoints after 1, merges after 5 and 6)

Issues 2 and 3 change no behaviour for staff, so they can merge while the feature is off. Until 5 merges, `SANDBOX_ENABLED` stays false everywhere.

## 1. Contracts (architect)

- OpenAPI: `POST /auth/visitor` (`@public`, 201 with the ADR 0018 session body plus cookies; 429 `visitor_daily_limit`); `GET /sandbox` (the caller's current sandbox or 404); `POST /sandbox` (body `drone_count` 1..10 default 5, `positions` absent for random or an array of `drone_count` points inside the demo area, `patrols` bool; 202; 409 `sandbox_active` while one is STARTING or STOPPING; 429 `sandbox_daily_limit`; 503 `sandbox_capacity`); `DELETE /sandbox` (202, idempotent 204 when none); `POST /sandbox/faults` (body `drone_id`, `action` enum `LINK_LOSS_30S`, `NACK_NEXT`, `BATTERY_DRAIN`; 202; 404). Schema `Sandbox`: `namespace`, `state`, `drone_count`, `drone_ids`, `patrols`, `idle_expires_at`, `error` (code only).
- Role enum gains `visitor`; error codes above added to the shared error schema.
- Socket event `sandbox.updated` (payload `Sandbox` plus `namespace`).
- Demo-area bounds: positions validated against `contracts/geo/demo-area.v1.json` (server side; the schema only fixes the shape).
- Golden examples in `contracts/examples/api/`; `make contracts` regenerates the TS types. As with session refresh, the contract lands on a branch and merges with the backend issue that first implements each path, so `make openapi-check` stays green.

## 2. Namespace scoping and the isolation test (backend)

- **Migration `0007_namespaces`**: `namespace text not null` on `drones`, `missions`, `alerts`, `commands`, backfilled with `split_part(drone_id, '-', 1)` (missions from their drone), index on each. Check constraint: matches `^[a-z0-9]{1,16}$`.
- **`skyhaul_api/tenancy.py`**: `Principal(user, role, namespace | None)` built once per request in the auth hook; `scoped(select, principal)` adds the filter for the four models; `get_scoped_or_404(model, id, principal)`. Every route and service function in the fleet, mission, alert and command modules uses them; a lint test greps those modules for `session.get(` / bare `select(Drone|Mission|Alert|Command)` outside `tenancy.py`.
- New rows take their namespace from the drone they belong to, never from the request body.
- **Isolation test** (API layer, test client): two visitor principals A and B (created directly in the DB, since issue 4 is not merged yet), each with a drone, a mission, an alert and a command. Walk every path and method of the OpenAPI contract with a table of fixtures; A on B's ids expects 404 (or 403 where the visitor role lacks the permission); A's lists contain no B row. A contract path without a table entry fails.
- Staff tests unchanged; the access control walk still passes.

## 3. Socket rooms per namespace (backend)

- `realtime/socket.py`: on connect, staff join `all` plus `role:<role>`; a visitor joins `ns:<namespace>` from their sandbox row (no sandbox: connect refused with `sandbox_required`).
- Fan-out: room from `drone_id` (`DRONE_ID_RE`, prefix) to `ns:<prefix>` and `all`; `command.updated` additionally to `role:dispatcher` and `role:admin` as today, and to `ns:<prefix>`; events with a `namespace` field go to `ns:<namespace>` and `all`. An event with neither is dropped and logged.
- Helper `disconnect_namespace_user(user_id)` for issue 5.
- Tests: unit for the room function; Socket.IO test client: a visitor of A receives A's `drone.updated` and never B's; staff receive both.

## 4. Visitor sessions (backend)

- **Migration `0008_visitor_role`**: `users.password_hash` nullable with `CHECK (password_hash IS NOT NULL OR role = 'visitor')`; role enum gains `visitor`.
- `POST /auth/visitor`: creates the user, starts a refresh family; `refresh_ttl(visitor)` = `VISITOR_SESSION_TTL_S` (7200); `access_claims` adds `ns` when a sandbox exists. Login with a visitor address always fails like an unknown email.
- Permission set `visitor` in the role table (ADR 0019 list); `sandbox:use` new.
- Rate limits: 30 visitor sessions per IP per day, 5/min per IP. Client IP through one function `client_ip(request)` (trusted header added in M7).
- Audit `auth.visitor` (success, no IP beyond what audit already stores).
- `flask skyhaul prune-visitors` (also run by the reaper): delete visitor users with no live family and no non-terminal sandbox.
- Tests: API (session issued, cookies as ADR 0018, permissions, 429), unit for `refresh_ttl`.

## 5. Sandbox manager and reaper (backend; deploy with automation)

- **Migration `0009_sandboxes`**: table per ADR 0019, partial unique index `(user_id) WHERE state IN ('STARTING','RUNNING','STOPPING')`, index on `(state, last_activity_at)`. Grants for `skyhaul_api`.
- **`skyhaul_api/sandbox/`**: `control.py` (the only control-API client, base URL and token from env, 5 s timeouts, token never logged; a fake for tests); `manager.py` (`start`, `stop`, `restart`, transitions only by conditional update); `worker.py` (bounded pool of 4); `reaper.py` (30 s loop under `pg_try_advisory_lock`).
- Start sequence and stop sequence exactly as in the ADR; capacity under `pg_advisory_xact_lock`; namespace `sb` + 10 base32 chars; positions random inside the demo area or as given; patrols built from the demo area.
- Ingestor: skip the `telemetry` insert for `sb` namespaces; link watchdog only alerts for registered drones.
- Activity: auth hook updates `last_activity_at` for visitors at most every 30 s. `idle_expires_at` in the `Sandbox` schema.
- Limiter keys `rl:sbx:<ns>:<bucket>` from `skyhaul_fleet_core.keys`; per-sandbox limits from the ADR on commands and mission writes.
- Settings: `SANDBOX_ENABLED`, `SANDBOX_MAX_DRONES_TOTAL` (40), `SANDBOX_IDLE_S` (600), `SANDBOX_DAILY_PER_IP` (10), `SIM_CONTROL_URL`, `SIM_CONTROL_TOKEN` (no defaults for the last two when enabled).
- Deploy (automation): a `demo` compose profile with the simulator on the internal network only, no published control port; the `test` profile unchanged.
- Tests
  - Unit: namespace generator (pattern, `sb` prefix), random positions inside bounds, state machine table.
  - API with the fake control client: start → RUNNING after fake heartbeats; heartbeat timeout → FAILED and the fake saw a reset; stop is idempotent; capacity 503 at 41 drones; daily cap 429; restart gives a new namespace and disconnects the socket.
  - Integration (Testcontainers Postgres, one test each): concurrent starts by one user give one 202 and one 409; stop during start ends STOPPED with nothing left in the fake; two reapers, one acts.
  - E2E (automation, real simulator, after merge): auto-start shows 5 drones; stop clears them; idle expiry with a shortened `SANDBOX_IDLE_S`.

## 6. Faults (backend)

- `POST /sandbox/faults`: drone through `get_scoped_or_404`; fixed mapping table action → simulator body; 6/min per sandbox; audited as `sandbox.fault` with the action only.
- Tests: API with the fake control client (each action sends exactly its fixed body; other namespace 404; unknown action 400; 429).

## 7. UI (frontend)

- Visitor start: no session on load → `POST /auth/visitor`, then `POST /sandbox` with defaults; a STARTING screen until `sandbox.updated` says RUNNING; 503 → busy page; 429 → daily-limit page.
- **New simulation** dialog: drone count 1..10, random or pick on the map (reuses the GoTo map picker of #71), patrols toggle; confirms that the current run ends.
- **Stop** button; stopped state with "Start again".
- Idle banner 2 minutes before `idle_expires_at`, "Keep going" sends one call; expired state offers a new simulation.
- Fault buttons on the drone panel (three actions); try-this checklist (send a GoTo, start a patrol, cut a link, acknowledge the alert), ticks driven by observed events, kept in `sessionStorage` (not a credential).
- Staff UI unchanged; visitor hides users and audit.
- Tests: one component test file per new `.vue` with per-component fakes; a store unit test for the start flow and the 503/429 branches.

## Open items carried into the issues

- Whether staff dispatchers and viewers should see sandbox namespaces at all (ADR: yes, all staff see all).
- Exact cap values (40 total, 10 per IP per day) once the demo host size is known.
