# 0019 · Visitor sandboxes for the public demo

Status: proposed · 2026-11-26

Amends [ADR 0011](0011-auth-and-roles.md) (a fourth role and namespace scoping inside the single organization), [ADR 0014](0014-realtime-push.md) (rooms per namespace, room taken from the drone id of a trusted event) and the [ADR 0004](0004-test-isolation.md) amendment (the demo deployment now includes the simulator, with its control API on the internal network only). Uses the hooks of [ADR 0018](0018-session-refresh.md). Implementation plan: [`docs/architecture/m3b-sandboxes.md`](../architecture/m3b-sandboxes.md).

## Context

The public demo must let a stranger try the dispatcher UI in one click, without an account, without seeing or disturbing anyone else, and without ever reaching the simulator's control API. Simulator namespaces (`^[a-z0-9]{1,16}$`, drone ids `<ns>-dNNN`, atomic `POST /reset` with epochs, ADR 0009) already isolate parallel tests, so they are the natural tenant boundary. The fleet API today has no tenancy at all: every authenticated user sees the whole fleet within their role.

## Decision

**Shape**
- Opening the site starts a simulation owned by the visitor: 5 drones by default, 1 to 10, random positions inside `contracts/geo/demo-area.v1.json` or points picked on the map, patrol missions optional. At most `SANDBOX_MAX_DRONES_TOTAL` (40) drones across all sandboxes. There is no shared live fleet and no anonymous viewer. Staff (admin, dispatcher, viewer) keep the normal login and see every namespace.
- Off unless `SANDBOX_ENABLED=true`. The dev and test stacks leave it off.

**Namespaces**
- A sandbox namespace is `sb` plus 10 random base32 characters (lowercase), new for every simulation, never reused. The `sb` prefix is reserved: the test namespace fixture and the seed script refuse it. Because ids are `<ns>-dNNN`, the namespace is a pure function of a drone id.
- A new simulation (restart) is stop plus start with a **new** namespace. Nothing from the old one (late replies, cached registry entries, stale Redis keys) can ever match the new one.

**Tenancy: one choke point**
- `drones`, `missions`, `alerts` and `commands` get a `namespace` column (not null, indexed; backfilled from the drone id prefix). `telemetry` is not touched: sandbox drones write none.
- Every read and write of those tables goes through `scoped(query, principal)` in `skyhaul_api/tenancy.py`. A staff principal gets the query unchanged; a visitor principal gets `WHERE namespace = :ns`. Lookups by id use the same filter, so another namespace's resource is **404, never 403**: a visitor learns nothing about other sandboxes.
- The principal's namespace comes from the database (the visitor's active `sandboxes` row), not from the token. The `ns` claim from `access_claims(user)` is informative only, as the role claim is (ADR 0011).
- Proof: a route-walking test over every path of `contracts/openapi/fleet-api.v1.json` with sandbox A's visitor against sandbox B's ids expects 404 (or 403 for routes the visitor role may not use at all) and never a B resource in a list. A new path without a fixture entry fails the test.

**Visitor role and session**
- `POST /auth/visitor` (`@public`) creates a `users` row with role `visitor`, a synthetic address `visitor-<uuid>@visitor.invalid` and no password (`password_hash` becomes nullable, with a check constraint that only visitors may lack one), then starts an ADR 0018 refresh family. `refresh_ttl(visitor)` is 2 hours absolute; `access_claims` adds `ns`. Visitor rows are deleted by the reaper once their family has expired (cascade).
- Permissions: viewer plus `command:send`, `mission:write`, `alert:ack`, `sandbox:use`, all inside the own namespace. Never `drone:admin`, `user:admin` or `audit:read`. The manager registers drones as the system actor `system:sandbox`, not as the visitor.
- New sandboxes per client IP are capped per day (`SANDBOX_DAILY_PER_IP`, 10) and visitor sessions too (30/day). The IP comes from the trusted tunnel's header only (M7); until then, `request.remote_addr`.

**Sandbox manager**
- Inside the fleet API, the only holder of `SIM_CONTROL_TOKEN`. The simulator runs in the demo deployment with its control API on the internal network, not published; nothing else may reach it (network policy in M7).
- Table `sandboxes`: `id`, `user_id`, `namespace` (unique), `state`, `drone_count`, `options jsonb`, `created_at`, `last_activity_at`, `state_changed_at`, `error`. States STARTING → RUNNING → STOPPING → STOPPED, and STARTING/STOPPING → FAILED. A partial unique index allows one non-terminal sandbox per user.
- **Start** (`POST /sandbox`): in one transaction, take `pg_advisory_xact_lock(capacity key)`, sum `drone_count` of non-terminal sandboxes, refuse with 503 `sandbox_capacity` over 40, insert the row as STARTING. The row exists before any simulator call, so no simulator namespace exists without a row. Then, on a bounded worker pool in the API process (4 threads, no locks held over HTTP): register the drones (system actor), spawn them one call per drone (`count: 1` with its own `home`), wait for each drone's first heartbeat in Redis (15 s deadline), create and start the patrol missions, then compare-and-set STARTING → RUNNING. Answers 202 with the row; the UI follows `sandbox.updated`.
- **Stop** (`DELETE /sandbox`, restart, reaper, failed start): compare-and-set any non-terminal state → STOPPING, then `POST /reset` for the namespace (retried on its 503), delete the namespace's `drone:` keys and limiter keys in Redis, delete its rows from `commands`, `alerts`, `missions` and `drones` in one transaction, then STOPPING → STOPPED. Every step is idempotent, so running stop twice, or after a crash, is safe. **Rollback of a failed start is this same stop**, ending in FAILED.
- **Races** are settled by conditional updates (`UPDATE sandboxes SET state=:new WHERE id=:id AND state IN (:allowed)`; zero rows means someone else won), never by checks then writes. A stop during a start wins: the starter's final STARTING → RUNNING update matches nothing, the starter stops creating things and leaves the cleanup to the stop (which runs after the reset, so any drone the starter spawned late is removed by the next stop pass the reaper makes; see below).
- **Reaper**: one thread per API process, every 30 s, does work only while it holds `pg_try_advisory_lock(reaper key)`, so one instance acts cluster-wide. It stops RUNNING sandboxes idle for 10 minutes (`last_activity_at`), STARTING or STOPPING rows older than 2 minutes (an API that died mid-operation), and RUNNING sandboxes whose drones have no Redis state for 30 s (simulator restarted). It also re-runs stop for STOPPED or FAILED rows changed in the last 5 minutes, which catches a drone spawned by a losing starter. Activity is any authenticated request by the visitor, written at most once per 30 s; an open socket does not count, so a forgotten tab expires.

**In-flight commands on stop**: the reset drops every queued and delayed reply of the namespace and bumps its epoch, so nothing produced before it is published after it. Pending command rows are deleted with the namespace, not marked failed: there is no one left to read them. A reply that is already on the broker reaches the ingestor for a drone that no longer exists and is dropped by the existing unknown-drone rule. The link watchdog must not raise alerts for unregistered drones (insert only when the drone row exists).

**Telemetry**: sandbox drones keep state in Redis only (`drone:<id>`, already namespaced by the id). The ingestor skips the `telemetry` insert for drone ids whose namespace starts with `sb`; ACK/NACK still update `commands`, alerts still land in `alerts`.

**Redis keys**: drone state keeps `drone:<id>`. New keys carry the namespace right after a fixed prefix so one `SCAN` per namespace finds them: `rl:sbx:<ns>:<bucket>` for limits. All names are built in `skyhaul_fleet_core.keys`.

**Rate limits per sandbox**: commands 30/min, mission writes 20/min, faults 6/min, New simulation 5/hour per user, keyed by namespace (or user), not IP, so visitors behind one NAT do not starve each other. 429 with `Retry-After`.

**Faults**: `POST /sandbox/faults` with `{drone_id, action}`, where `action` is one of `LINK_LOSS_30S`, `NACK_NEXT`, `BATTERY_DRAIN`. The manager maps each to a fixed simulator body (link loss 30 s, NACK next 1, drain factor 20 for 60 s). The drone must be in the visitor's namespace (404 otherwise). No visitor value is passed to the simulator.

**Socket.IO** (amends ADR 0014): rooms `ns:<namespace>` for a visitor and `all` for staff replace the role-wide `fleet` room; `role:dispatcher` and `role:admin` stay for `command.updated`. The fan-out derives the room from the event's `drone_id` (validated with `DRONE_ID_RE`; events come from the ingestor and the API over Redis, never from a client) and emits to `ns:<prefix>` and `all`. Events without a drone (`sandbox.updated`) carry an explicit `namespace`. When a visitor's sandbox changes namespace, the server disconnects their sockets; the client reconnects into the new room.

**Unaffected**: M2 tests and the simulator's own tests. Sandboxes are off in the test stack, test namespaces never start with `sb`, staff roles are unscoped, and existing rows get their namespace from their id. The isolation test turns sandboxes on with a fake control client.

## Consequences

- One place to get tenancy right, and a test that fails when a new route skips it.
- The demo host runs the simulator. The control API is now reachable by one more service (the API), on the internal network; a compromise of the API can drive any simulator namespace, but only simulated drones exist there.
- Visitors create user rows; the reaper must keep up. Capacity is a hard 40 drones; the 41st visitor sees the busy page.
- Up to 5 minutes after a lost start race, a stray simulated drone can exist without a row; nothing routes to it.
- The ingestor gains a namespace rule, and the fan-out reads a drone id from trusted events.

## Alternatives considered

- Shared live fleet with anonymous viewers: simpler, but one visitor's commands affect everyone, and no tenancy proof exists.
- Postgres row-level security: strong, but the API connects with one role, so it needs `SET LOCAL` per request plus a second choke point in Redis and sockets anyway.
- Separate simulator per visitor: perfect isolation, far too heavy for 40 drones.
- Reusing one namespace per visitor with reset: saves ids, but late messages and caches from the old run could match the new one.
- 403 for foreign resources: honest, but confirms that the id exists.
- Visitor sessions without a user row: saves rows, but breaks "the database authorizes" and the audit FK.
