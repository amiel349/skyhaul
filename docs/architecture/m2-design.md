# M2 design · Fleet backend

Status: accepted · 2026-11-24 · single organization (multi-tenancy dropped from the approved draft, decision 0) · owner: architect

Issues: #23 (this design and the contracts), #24 (foundation), #25 (auth, drone registry, bootstrap CLI), #27 (ingestor, ACK/NACK handling, command sweeper), #26 (command service), #28 (missions and alerts), #29 (realtime and hardening). Build order: #24, #25, #27, #26, #28, #29.

Done when one curl creates a mission, a simulated drone flies it, and its telemetry lands in Postgres.

**Services touched:**
- new `services/fleet_core/` (shared models and rules)
- `services/api/` (from the stub)
- new `services/ingestor/`
- `contracts/` (OpenAPI, topology constants; landed with #23)
- `deploy/compose.yaml`, Postgres init script, `.env.example`, `gen-env.sh`, `Makefile`, `ci.yml`
- the simulator is unchanged

**No message schema changes, so `schema_version` stays 1.**

Decisions someone could reasonably question are recorded as ADRs: [0010](../adr/0010-fleet-backend-command-flow.md) (boundaries and command flow), [0011](../adr/0011-auth-and-roles.md) (auth, roles, single organization), [0012](../adr/0012-telemetry-storage.md) (telemetry storage and retention), [0013](../adr/0013-least-privilege-infrastructure.md) (least-privilege infrastructure, amends [0006](../adr/0006-command-delivery.md)), [0014](../adr/0014-realtime-push.md) (realtime push).

## 1. Service boundaries

| Service | Does | Doesn't |
|---|---|---|
| `api` (Flask, gunicorn `-w 1 -k gthread --threads 32`, Flask-SocketIO in threading mode) | REST, auth, RBAC, command and mission service (validate, persist, publish), Socket.IO fan-out from Redis | Consume AMQP; derive alerts |
| `ingestor` (plain Python, **one thread**) | Consume all of `fleet.telemetry` (ACK and NACK included), batch-write Postgres, update Redis latest state, publish Redis events, watchdog (link loss, command timeouts, telemetry retention) | Publish commands; serve HTTP (only a `/healthz`) |
| `fleet_core` (library) | SQLAlchemy models, Alembic migrations, command and mission transition tables, Redis key builders, settings helpers | Import Flask |

**Replies go to the ingestor:** it is the single owner of everything that arrives from drones. The API never needs a consumer, so it needs no AMQP read permission and has no per-worker reply queues. The ACK-vs-telemetry order risk from M1 is handled in one place.

**The ingestor is single-threaded:** one consumer loop (`process_data_events(time_limit=0.2)`) does the batching, flushing, watchdog and sweeper. All of its in-memory state (batch buffer, last-heartbeat map, drone registry cache) has one owner thread, so it needs no locks.

## 2. Package layout

```
services/fleet_core/src/skyhaul_fleet_core/
  settings.py     env helpers, fail fast, no secret defaults
  db.py           engine and session factory
  models.py       all tables (section 3)
  transitions.py  COMMAND_TRANSITIONS, MISSION_TRANSITIONS, cas_update()
  keys.py         Redis key and channel builders
  migrations/     Alembic (env.py, versions/)
services/api/src/skyhaul_api/
  __init__.py     create_app(); extensions.py (db, smorest Api, SocketIO, Limiter, redis)
  config.py  logging.py (JSON, request_id/correlation_id)  errors.py  pagination.py  cli.py
  auth/           routes.py tokens.py passwords.py permissions.py (Permission, ROLE_PERMISSIONS, @require, @public)
  users/ drones/ commands/ missions/ alerts/ audit/   each: routes.py schemas.py service.py
  commands/publisher.py   CommandPublisher thread (single owner of the AMQP connection)
  realtime/       socket.py (connect auth, rooms)  fanout.py (Redis PSUBSCRIBE thread)
services/ingestor/src/skyhaul_ingestor/
  __main__.py  config.py  consumer.py (loop)  batch.py (flush: DB, then Redis, then ack)
  handlers.py (per message type -> row and event)  watchdog.py  registry.py (registered-drone cache)
```

## 3. Data model (PostgreSQL 16)

Skyhaul serves a single organization: there is no tenant table, column or key ([ADR 0011](../adr/0011-auth-and-roles.md)).

| Table | Key and main columns | Indexes and constraints |
|---|---|---|
| `users` | `id uuid` PK, `email citext` unique, `password_hash`, `role` enum(`admin`,`dispatcher`,`viewer`), `is_active`, `created_at` | |
| `drones` | `id text` PK (`DroneId`, globally unique, as in the routing keys), `name`, `home_lat`, `home_lon`, `created_at`, `deleted_at` | Soft delete; an id is never reused |
| `missions` | `id uuid` PK (sent as the contract `mission_id`), `drone_id`, `state` enum, `waypoints jsonb`, `correlation_id uuid`, `created_by`, `created_at`, `started_at`, `ended_at`, `failure_reason` | FK `drone_id → drones(id)`; partial unique `(drone_id) WHERE state IN ('DISPATCHING','ACTIVE')` (one live mission per drone); `(state, created_at desc)` |
| `mission_events` | `id bigserial`, `mission_id` FK, `ts`, `kind`, `data jsonb` | `(mission_id, ts)` |
| `commands` | `id uuid` PK (= `msg_id`), `drone_id`, `mission_id` null, `type`, `payload jsonb`, `correlation_id`, `status` enum(`PENDING`,`SENT`,`ACKED`,`NACKED`,`TIMED_OUT`,`FAILED`), `nack_reason`, `nack_detail`, `late bool`, `requested_by`, `idempotency_key`, `created_at`, `sent_at`, `replied_at` | FK to drones; `unique(requested_by, idempotency_key)`; partial `(sent_at) WHERE status='SENT'` for the sweeper |
| `alerts` | `id uuid`, `drone_id`, `code` (backend enum: contract `AlertCode` plus `LINK_LOST`), `severity`, `message`, `value`, `source` (`DRONE`/`DERIVED`), `msg_id` unique null, `raised_at`, `acknowledged_by`, `acknowledged_at`, `cleared_at` | partial unique `(drone_id, code) WHERE source='DERIVED' AND cleared_at IS NULL` (one open derived alert); `(cleared_at, raised_at desc)` |
| `telemetry` | `msg_id uuid` PK, `drone_id`, `type`, `ts` (drone clock), `received_at`, `correlation_id`, `payload jsonb` | `(drone_id, type, ts desc)`. Insert `ON CONFLICT (msg_id) DO NOTHING` |
| `audit_log` | `id bigserial`, `actor_user_id` null (failed login with an unknown email), `action`, `target_type`, `target_id`, `outcome`, `ip`, `ts`, `data jsonb` (no secrets) | `(ts desc)`. INSERT-only grant |

**Telemetry storage** ([ADR 0012](../adr/0012-telemetry-storage.md)):
- **Persisted:** POSITION, BATTERY, MISSION_STATUS and ALERT go to `telemetry`. ACK and NACK go to `commands`.
- **HEARTBEAT is not persisted:** it updates Redis and the link watchdog only, which removes about 40 % of the rows.
- **Retention:** `TELEMETRY_RETENTION_DAYS=7`. The ingestor watchdog deletes old rows hourly in batches of 10,000 by `ts` (the ingestor role has DELETE on `telemetry` only).
- **No partitioning in M2:** at demo scale (about 20 drones, about 35 rows/s, about 3 M rows/day) a plain table is enough.

**Single organization** ([ADR 0011](../adr/0011-auth-and-roles.md)): multi-tenancy is out of scope. Access control is by role only, and a missing resource is simply 404. Parallel E2E tests isolate by simulator namespace (per-worker drone id prefixes such as `gw0-d001`, ADR 0004), not by tenant.

## 4. Auth

- **JWT:** HS256 with `JWT_SECRET` (env, at least 32 bytes, no default), via PyJWT.
  - Claims: `iss=skyhaul-api`, `aud=skyhaul`, `sub` (user id), `role`, `iat`, `exp` (15 min), `jti`.
  - The token identifies the user; the database authorizes. Each request loads the user by `sub` and rejects it if inactive, and the role comes from the database. Deactivation and role changes take effect at once, at the cost of one indexed lookup per request.
  - Access token only in M2. Refresh tokens (httpOnly cookie plus CSRF) come in M3.
- **Roles and permissions:**
  - viewer: `fleet:read`, `mission:read`, `alert:read`
  - dispatcher: viewer plus `command:send`, `mission:write`, `alert:ack`
  - admin: dispatcher plus `drone:admin`, `user:admin`, `audit:read`
- **`@require(Permission.X)`:** sets `g.principal = Principal(user_id, role)`.
- **Default deny:** a `before_request` hook refuses any endpoint that has neither `@require` nor `@public`. A test walks `app.url_map` to prove every route has one.
- **Passwords:** argon2id via `argon2-cffi` `PasswordHasher()` (RFC 9106 low-memory profile). Rehash on login when the parameters change. Verify against a dummy hash when the email is unknown, so timing does not reveal accounts.
- **Rate limits:** Flask-Limiter with Redis storage.
  - `POST /auth/login`: 5/min per (ip, email) and 20/min per ip.
  - Commands: 30/min per user. Everything else: 300/min per user.
  - Answer 429 with `Retry-After`. Failed and successful logins go to `audit_log`.
- **Bootstrap:** there is no signup. `flask skyhaul bootstrap --admin-email <e>` creates the organization's first admin and reads the password from stdin. It is get-or-create, so test fixtures can call it repeatedly. Admins then create users through `POST /users`.

## 5. REST API (`/api/v1`, flask-smorest, marshmallow schemas)

The contract is [`contracts/openapi/fleet-api.v1.json`](../../contracts/openapi/fleet-api.v1.json). This table is a summary; the file wins.

| Resource | Endpoints | Permission |
|---|---|---|
| Auth | `POST /auth/login` → `{access_token, token_type, expires_in, user}`; `GET /auth/me` | public (rate-limited); any |
| Users | `GET /users`, `POST /users`, `PATCH /users/{id}` (role, is_active) | `user:admin` |
| Drones | `GET /drones` (rows merged with Redis latest state), `GET /drones/{id}`, `POST /drones` `{id, name, home}`, `DELETE /drones/{id}` (soft) | `fleet:read`; `drone:admin` |
| Telemetry | `GET /drones/{id}/telemetry?type=&since=&until=&limit=&cursor=` | `fleet:read` |
| Commands | `POST /drones/{id}/commands` `{type, payload}` → **202** `Command`; `GET /drones/{id}/commands`; `GET /commands/{id}` | `command:send`; `fleet:read` |
| Missions | `POST /missions` `{drone_id, waypoints, start}` → 201 `{mission, commands}`; `GET /missions?state=&drone_id=`; `GET /missions/{id}`; `GET /missions/{id}/events`; `POST /missions/{id}/start` → 202; `POST /missions/{id}/abort` `{action: RETURN_HOME or LAND}` → 202; `POST /missions/{id}/cancel` (PLANNED → CANCELLED, 409 otherwise) | `mission:write`; `mission:read` |
| Alerts | `GET /alerts?open=&drone_id=`; `POST /alerts/{id}/ack` | `alert:read`; `alert:ack` |
| Audit | `GET /audit` | `audit:read` |
| Health | `GET /healthz` (process alive), `GET /readyz` (DB, Redis, publisher connected; 503 otherwise) | public |

- **Command payloads:** built with `Codec.new` from the contracts' Pydantic models, so the contracts are the single validator. An `EncodeError` becomes 422 with field paths and never input values. The OpenAPI file types `payload` as an object and points to `contracts/schemas/v1/commands`; the UI uses the payload types from `@skyhaul/contracts`, so the payload shape has one definition.
- **START_MISSION is refused on the raw commands endpoint** (422; the request enum `DirectCommandType` leaves it out). It goes only through `/missions`, so the missions table is always the source of truth.
- **`start` defaults to false:** `POST /missions` without it plans a mission (`PLANNED`); `start: true` launches it at once (section 6).
- **Idempotency:** `Idempotency-Key` (scoped to the user) is accepted on `POST /drones/{id}/commands` and `POST /missions`. A replay returns the original resource and publishes nothing.
- **Errors** use the same shape as the control API: `{"error": "<code>", "detail": "<text>", "fields": {...}?, "request_id": "<uuid>"}`.
  - Status codes: 400, 401, 403 (wrong role), 404 (missing), 409 (live mission exists, drone id taken, illegal transition), 413 (body over 64 KiB), 415, 422, 429, 503 (`command_not_sent`, publisher unavailable).
  - `detail` never echoes headers, tokens or input values.
- **Pagination:** keyset everywhere: `?limit=` (default 50, max 200) and an opaque `cursor` (base64 of `(ts, id)`). Responses are `{items, next_cursor}`, with `next_cursor: null` on the last page.
- **Correlation:** `X-Request-ID` is accepted if it is a UUID, otherwise generated, and appears in every log line and response. A single command uses `correlation_id = request id`; a mission launch uses `missions.correlation_id`. The ingestor logs the envelope's `correlation_id`.
- **OpenAPI is the UI contract:**
  - `contracts/openapi/fleet-api.v1.json` (OpenAPI 3.1, self-contained) landed first with #23. `contracts/tests/contract/test_openapi.py` validates it against the vendored official 3.1 meta-schema, checks every component schema as JSON Schema 2020-12, resolves every `$ref`, requires each operation to be authenticated or explicitly `security: []`, and keeps the shared enums and patterns equal to `contracts/schemas/v1/common.schema.json`.
  - `x-permission` on each operation names the permission the server checks. It is documentation for reviewers and the default-deny test, not something a client acts on.
  - The Socket.IO payloads are named component schemas (`DroneStateEvent`, `CommandUpdatedEvent`, `MissionUpdatedEvent`, `AlertRaisedEvent`, `AlertUpdatedEvent`, `SocketAuth`, `SocketConnectError`), and the root extension `x-socketio` maps event names to them.
  - **Deferred to M2-1 (#24):** `openapi-typescript` (exact pin, devDependency of `web/`) generating `contracts/ts/api/`, and `make openapi` / `make openapi-check` (part of `make check` and CI), which exports the spec from the app (`flask spec`, with `x-socketio` and `x-permission` added through the spec options) and fails on any difference with the committed file. Until then the file is the contract by review.

## 6. Command service flow

1. Authenticate, check the permission, look up the drone (404 if missing or deleted).
2. Build the message with `Codec.new(type, drone_id, payload, correlation_id)` (422 on `EncodeError`). Check `Idempotency-Key`.
3. In one transaction: INSERT into `commands` (`PENDING`, `id = msg_id`) and INSERT into `audit_log`. Commit.
4. Hand the message to the **`CommandPublisher`**: one thread per API process, the only owner of its pika connection.
   - It has a bounded queue of 1,000 (full → 503) and a `Future` per message.
   - It uses publisher confirms with `mandatory=True`, the same bounded broker-nack retry as M1, and a 5 s deadline.
   - The publisher always resolves the future: confirmed, nacked or unroutable, or dropped before sending because the deadline passed. The request thread therefore never decides about a publish whose outcome it doesn't know.
5. Confirmed: CAS `PENDING → SENT` (sets `sent_at`) and answer 202. Otherwise: `PENDING → FAILED` and 503 `command_not_sent`.
6. **Ingestor, on ACK or NACK:** `UPDATE commands SET status, replied_at, nack_* WHERE id = command_msg_id AND drone_id = envelope.drone_id AND status IN ('PENDING','SENT','TIMED_OUT')`.
   - **Late replies win:** a reply that arrives after `TIMED_OUT` sets the real status and `late = true`. A timeout means "we stopped waiting", not "it failed".
   - It then publishes `command.updated`.
7. **Ingestor sweeper, every 1 s:**
   - `SENT → TIMED_OUT` when `sent_at < now() - COMMAND_TIMEOUT_S` (default 10).
   - `PENDING → FAILED` when older than 30 s (a crash between commit and publish).
   - `UPDATE ... RETURNING` emits the events.

**Mission launch (the done-state curl):** `POST /missions` with `start: true`:
- Insert the mission as `DISPATCHING`. The partial unique index makes a concurrent launch for the same drone a 409.
- Read the flight state from Redis; if the drone is LANDED, send ARM. Then send START_MISSION. Both carry the mission's `correlation_id` and `mission_id`.
- **The two commands are pipelined without waiting**, relying on ADR 0006's per-drone order (one queue, single active consumer): if ARM is NACKed, START_MISSION is NACKed `INVALID_STATE`.
- The START_MISSION reply (or the MISSION_STATUS telemetry) is authoritative for the mission.

## 7. Ingestor

**Topology** (in `skyhaul_contracts.topology`, which stays the single source of declare arguments; landed with #23 as `INGESTOR_QUEUE`, `INGESTOR_QUEUE_ARGUMENTS` and `declare_ingestor_queue`):
- Queue `ingestor.telemetry`: quorum, durable, bound to `fleet.telemetry` with `drone.*.*`.
- Arguments: `x-single-active-consumer=true` (a second replica becomes a hot standby), `x-delivery-limit=5`, `x-dead-letter-exchange=fleet.dlx`, `x-max-length=100000`, `x-max-length-bytes=6553600000` (100,000 × 64 KiB).
- **`x-overflow=drop-head`:** a stopped ingestor must never push back on the simulator's publisher. With `reject-publish`, its nack retries would stall the simulator's outbox and its commands. The cost is that the oldest telemetry is dropped first, and a lost ACK shows up as `TIMED_OUT`.
- **Consumer:** manual ack, `prefetch_count=1000`. Decoding goes only through the Codec. An undecodable message is rejected without requeue (DLX). A message for a drone that is not registered is acked, counted (`unregistered_drone_messages`) and never stored; the simulator spawns many such drones.
- **Batching:** flush every 200 ms or 500 messages, in this order:
  1. One DB transaction: telemetry `INSERT ... ON CONFLICT DO NOTHING`, command CAS updates, mission CAS updates plus `mission_events`, `alerts` inserts `ON CONFLICT (msg_id) DO NOTHING`. Commit.
  2. One Redis pipeline: latest-state writes and event publishes.
  3. `basic_ack(last_tag, multiple=True)`.

  Acking only after commit gives at-least-once delivery. Unique `msg_id` and CAS updates make redelivery a no-op. A Redis failure retries the Redis step before the ack; Redis writes are idempotent (below).
- **Registry cache:** remembers whether a `drone_id` is registered, positive TTL 30 s, negative TTL 5 s. Because drone ids are never reused (soft delete, global PK), a stale entry can at worst store up to 30 s of telemetry from a drone deleted a moment ago, never attribute it to another drone.
- **Redis:**
  - `drone:{id}` is a hash: `flight_state, armed, battery_pct, est_remaining_s, lat, lon, altitude_m, heading_deg, ground_speed_mps, target, mission_id, last_heartbeat_at, last_seen_at, link (UP/LOST), ts`. EXPIRE 24 h.
  - It is written by a Lua script that compares each field group's `ts` and never overwrites with older data.
  - Channel `events` carries `{"event": ..., "data": ...}`. Events: `drone.state` (at most once per drone per flush), `command.updated`, `mission.updated`, `alert.raised`, `alert.updated`. `data` has the shape of the matching OpenAPI component schema.
  - Key and channel names come only from the builders in `fleet_core/keys.py`.

## 8. Missions and alerts

**Mission states:**
- `PLANNED → DISPATCHING` (launch)
- `DISPATCHING → ACTIVE` (START_MISSION ACK **or** MISSION_STATUS ACTIVE; the M1 note says telemetry can come first)
- `DISPATCHING → FAILED` (NACK on START_MISSION, or no ACTIVE within `MISSION_DISPATCH_TIMEOUT_S=60`)
- `ACTIVE → COMPLETED` or `ABORTED` (final MISSION_STATUS; this also covers the simulator's automatic RETURN_HOME at critical battery)
- `DISPATCHING → COMPLETED` or `ABORTED` is allowed when the final status overtakes the ACK
- `PLANNED → CANCELLED` (user, `POST /missions/{id}/cancel`; 409 from any other state)
- Terminal states absorb. Every transition is a CAS (`UPDATE ... WHERE id = :id AND state = ANY(:allowed_from)`), so duplicates and out-of-order messages never move a mission backwards.
- A MISSION_STATUS whose `mission_id` belongs to another drone is ignored, logged and counted.
- Abort sends RETURN_HOME or LAND; the mission becomes `ABORTED` when the drone's final status arrives.

**Alerts:**
- Drone ALERT messages (LOW_BATTERY, CRITICAL_BATTERY, GEOFENCE_BREACH, LINK_RESTORED) become rows with `source=DRONE`, deduplicated by `msg_id`. The backend derives only what a drone cannot report.
- **`LINK_LOST`:** the watchdog runs every 1 s on the ingestor thread. It keeps an in-memory map of drone → last HEARTBEAT `received_at` on the ingestor's monotonic clock, seeded from Redis at startup.
- No heartbeat for `LINK_LOSS_AFTER_S=5` raises a derived `LINK_LOST` (WARNING) and sets Redis `link=LOST`. The next heartbeat clears it (`cleared_at`) and sets `link=UP`.
- The partial unique index on open derived alerts makes a standby replica, or a restart, unable to raise a second one.
- Only drones that have sent at least one heartbeat are watched.

## 9. Realtime (Flask-SocketIO, threading mode)

- **Connect:**
  - The client connects to namespace `/fleet` with `auth: {token}` (never the query string, which gets logged).
  - The server verifies the JWT and the active user and joins the rooms `fleet` and `role:{role}` itself. There is no client `join` handler and no other client event in M2.
  - A sweep every 30 s disconnects sessions whose token has expired.
- **Fan-out:**
  - One background thread per API process subscribes to `events` and emits to the rooms.
  - **The room comes from the event name, never from the payload**, so a payload cannot redirect an emit.
  - `command.updated` goes to `role:dispatcher` and `role:admin` only; everything else goes to `fleet`.
- **Scaling:** one API process in M2 (section 1). More workers (M4) need `message_queue=redis://` and sticky sessions.

## 10. Least privilege

**RabbitMQ** ([ADR 0013](../adr/0013-least-privilege-infrastructure.md)):
- A one-shot `broker-init` (`flask skyhaul broker-init`, admin credentials, management HTTP API) creates the users and permissions and declares the whole topology (`declare_topology` and `declare_ingestor_queue`). The fleet services declare nothing.
- This replaces the M1 rule "command senders call `declare_topology`" for the fleet API and amends ADR 0006. The MCP exception (it does not declare `drone_sim.cmd`) still stands.

| User | configure | write | read |
|---|---|---|---|
| `skyhaul_api` | `^$` | `^fleet\.cmd$` | `^$` |
| `skyhaul_ingestor` | `^$` | `^$` | `^ingestor\.telemetry$` |
| admin (`RABBITMQ_USER`) | used by `broker-init`, and in M2 still by the simulator and MCP server (test profile only) | | |

**Postgres:**
- `deploy/postgres/initdb.d/10-roles.sh` creates the roles from env passwords. It runs on a fresh volume only, so existing local stacks need `make down -v` once.
- `skyhaul_owner` owns the schema and runs Alembic through a one-shot `migrate` service.
- `skyhaul_api`: SELECT/INSERT/UPDATE on the app tables, INSERT on `audit_log`, no DDL, no DELETE except soft deletes via UPDATE.
- `skyhaul_ingestor`: INSERT on `telemetry`, `alerts` and `mission_events`; UPDATE on `commands`, `missions` and `alerts`; DELETE on `telemetry`; SELECT on `drones`, `missions` and `commands`.
- None of them is a superuser or `CREATEROLE`.

**Redis:** `requirepass` as today.

## 11. Compose, Makefile, CI

- **Compose:**
  - Postgres mounts `initdb.d`.
  - New one-shots `migrate` and `broker-init`. `api` and `ingestor` start after both have completed (`service_completed_successfully`).
  - `ingestor` is a new service with the same hardening as `drone-sim` (read-only, `cap_drop: ALL`, `no-new-privileges`) and no published port.
  - `api` runs gunicorn with `-w 1 -k gthread --threads 32`.
- **New `.env.example` and `gen-env.sh` keys:** `JWT_SECRET`, `DB_OWNER_PASSWORD`, `DB_API_PASSWORD`, `DB_INGESTOR_PASSWORD`, `AMQP_API_PASSWORD`, `AMQP_INGESTOR_PASSWORD`.
- **Makefile:** `make migrate`, `make bootstrap`, `make openapi` and `make openapi-check` (#24), and `make demo-m2` (spawn a simulated drone, register it, log in, one curl `POST /missions`, then query `telemetry`).
- **CI:**
  - `python` job: api and ingestor unit tests.
  - `integration`: Testcontainers Postgres and Redis next to RabbitMQ; `alembic upgrade head` plus `alembic check` (no model drift); the done-state flow end to end with the simulator as a subprocess.
  - `contracts`: also `openapi-check` (#24).
  - `compose` smoke: `--wait` on the whole stack.
  - Schemathesis stays in M4.

## 12. Invariants worth a test

1. The API's AMQP connection is used only by the `CommandPublisher` thread; request threads never call pika (a fake channel records thread ids).
2. The ingestor runs on exactly one thread (`threading.enumerate()` under load).
3. The ingestor never acks before the batch's commit. Killing it between commit and ack, then redelivering, adds no duplicate row.
4. Telemetry and drone-alert inserts are idempotent: replaying the same batch leaves the counts unchanged.
5. Commands: an ACK racing the timeout sweep ends in exactly one final status; a duplicate ACK is a no-op; a late ACK after `TIMED_OUT` ends `ACKED` with `late=true`; a reply naming another drone's command changes nothing.
6. Missions never regress: an out-of-order MISSION_STATUS (ACTIVE after COMPLETED) is ignored, and a MISSION_STATUS before the START_MISSION ACK still reaches ACTIVE. Cancel outside PLANNED is a 409.
7. Redis latest state never goes back in time (an older `ts` loses).
8. One live mission per drone: N concurrent `POST /missions` give one 201 and N−1 409s.
9. `Idempotency-Key`: the same key twice gives one row and one publish.
10. Default deny: every route in `url_map` has `@require` or `@public`. For every endpoint, a lower role gets 403 and no token gets 401.
11. Sockets: a viewer never receives `command.updated`; a payload field cannot redirect an emit; an expired token is disconnected.
12. Link loss under a fake clock: one `LINK_LOST` after 5 s of silence, one clear on the next heartbeat, none duplicated by a second watchdog.
13. Telemetry from unregistered drones is never stored and is counted.
14. Login: rate limited, an unknown email takes the same time (dummy hash), every attempt audited, no token or password in any log.

## 13. Decisions

Approved by the human on 2026-11-24.

| # | Decision | Recorded in |
|---|---|---|
| 0 | **Single organization; multi-tenancy is out of scope** for M2. No tenant table, column, claim, key prefix or room. It can be added later with a tenant column, a token claim and scoped repositories. | ADR 0011 |
| 1 | **Drone ids are globally unique** (simple PK, as in the routing keys). Registering an id that is or was registered returns 409 `drone_id_taken`. | ADR 0011 |
| 2 | **HEARTBEAT is not persisted**; it updates Redis and the link watchdog only. | ADR 0012 |
| 3 | **Link-loss threshold is 5 s** without a heartbeat (`LINK_LOSS_AFTER_S=5`). | Section 8 |
| 4 | **One API process in M2** (gunicorn, one worker, 32 threads). Multi-replica API (Socket.IO message queue, sticky sessions, more publisher threads) is deferred to M4. | ADR 0010, 0014 |
| 5 | **The ingestor owns all inbound messages**, ACK and NACK included; the API never consumes AMQP. | ADR 0010 |
| 6 | **`POST /missions {start: true}` pipelines ARM and START_MISSION** without waiting, relying on per-drone order (ADR 0006). | ADR 0010 |
| 7 | **Late replies win:** `TIMED_OUT → ACKED/NACKED` with `late=true`. A mission waits up to 60 s before `FAILED`. | ADR 0010 |
| 8 | **7-day telemetry retention, plain table, no partitioning** in M2. | ADR 0012 |
| 9 | **Access token only** (15 min); refresh cookie and CSRF in M3. | ADR 0011 |
| 10 | **Bootstrap through a CLI** (`flask skyhaul bootstrap --admin-email`) creates the first admin; there is no signup. | ADR 0011 |
| 11 | **Parallel E2E tests isolate by simulator namespace** (per-worker drone id prefixes), not by tenant. | ADR 0004 |
| 12 | **`broker-init` owns the topology and the per-service broker users**; services declare nothing. Amends ADR 0006; the MCP exception stands. | ADR 0013 |
| 13 | **The simulator and the MCP server keep the admin broker user** in M2 (test profile only); their own users come in M4. | ADR 0013 |
| 14 | **Database roles via an init script**, which runs on a fresh volume only: existing local stacks need `make down -v` once. | ADR 0013 |
| 15 | **`openapi-typescript` generation and the `openapi-check` drift job** are part of M2-1 (#24), not #23. | Section 5 |

## 14. Contracts landed with #23

- `contracts/openapi/fleet-api.v1.json`, including the Socket.IO event schemas, and `contracts/tests/contract/test_openapi.py`.
- `skyhaul_contracts.topology`: `INGESTOR_QUEUE`, `INGESTOR_QUEUE_ARGUMENTS`, `declare_ingestor_queue`, with unit and integration tests. An additive change with no `schema_version` bump.
