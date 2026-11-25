# M3 design · Dispatcher UI

Status: draft · 2026-11-25 · owner: architect

Done when a dispatcher logs in, sees every drone move on a map in real time, and the view survives a socket drop without gaps.

**Services touched:** `web/` (from the stub), `Makefile`, `ci.yml`. The API and contracts are unchanged by this note; what the UI is missing is listed in section 10 as backend follow-ups. **No message schema change, `schema_version` stays 1.**

ADRs: [0015](../adr/0015-frontend-token-handling.md) (token in memory only), [0016](../adr/0016-map-library.md) (Leaflet instead of MapLibre).

## 1. Structure

Vue 3 + TypeScript + Vite + Pinia + Vue Router, all already in `web/package.json`. Composition API and `<script setup>` only.

```
web/src/
  main.ts  App.vue  router.ts   routes + one auth guard
  api/
    client.ts      openapi-fetch client, token middleware, 401 handling
    types.ts       re-export of contracts/ts/api/fleet-api.v1.ts (no hand-written API types)
  realtime/
    socket.ts      the only socket.io-client instance (connect, reconnect, event wiring)
  stores/          Pinia, setup style
    auth.ts        token (memory), user, role, expiresAt, login(), logout()
    fleet.ts       drones by id, applyState(), loadAll()
    connection.ts  socket status: connecting | live | reconnecting | offline
  auth/permissions.ts  role-to-permission table (section 6)
  composables/     useRole(), useFleetMap(), useNow()
  views/           LoginView.vue  FleetView.vue  (missions, alerts in later issues)
  components/      FleetMap.vue  DroneList.vue  ConnectionBadge.vue
  __tests__/       Vitest specs
```

- **Stores own shared state, components do not.** Reason: one place applies REST pages and socket events.
- **The socket and the API client are plain modules.** Stores call them; components never import `socket.io-client` or `fetch`. Reason: one mock point for tests.

## 2. API client

- **Types are already generated.** `make contracts` runs `openapi-typescript` into `contracts/ts/api/fleet-api.v1.ts`, and `make contracts-check` (in `make check`) catches drift. No new generator, no new `make` target, no new drift check.
- **Client: `openapi-fetch`** (about 6 kB, built for `openapi-typescript` output: typed paths, params and bodies, no generated runtime code). Reason: the types exist, so orval or openapi-generator would add a second generated tree and a second drift check for nothing.
- `web` imports the types through a path alias (`@contracts` → `../contracts/ts`); nothing is copied into `web/`.
- **Base URL `/api/v1`** (the OpenAPI `servers` entry), same origin everywhere.
- **Token injection:** one middleware reads the token from the auth store and sets `Authorization: Bearer`. It never logs headers.
- **401:** the middleware calls `auth.logout("expired")`: clear the store, close the socket, route to `/login?next=<path>`. A 403 shows "not allowed for your role" and does not log out. A 429 shows a retry hint from `Retry-After`.
- **Expiry ahead of time:** the store keeps `expiresAt` from `expires_in`. One timer logs out 30 s before it with a "session ended" notice, so no half-typed form dies on a surprise 401.

## 3. Socket.IO client for `/fleet`

- `io("/fleet", { path: "/socket.io", auth: (cb) => cb({ token: auth.token }), transports: ["websocket"] })`. The `auth` callback reads the current token on every (re)connect. Never in the query string; the server refuses that.
- **Events** (names from `services/api/.../realtime/rooms.py`, payloads from the OpenAPI components). The client emits nothing; there is no `join`.

| Event | Payload | Used from |
|---|---|---|
| `drone.state` | `DroneStateEvent` `{drone_id, state: DroneLiveState}` | thin slice |
| `mission.updated` | `Mission` | missions issue |
| `alert.raised`, `alert.updated` | `Alert` | alerts issue |
| `command.updated` | `Command` (dispatcher and admin rooms only) | commands issue |

- **Reconnect:** socket.io-client's built-in jittered backoff (1 s to 10 s). On every `connect` after the first, the fleet store calls `loadAll()` (`GET /drones?limit=200`, following `next_cursor`). Reason: Redis pub/sub drops whatever happened while we were away (ADR 0014); REST is the source of truth, events are hints.
- **Order and staleness:** `applyState()` keeps an incoming state only if its `state.ts` is newer than or equal to the stored one; otherwise it is dropped. This covers an event arriving before a slower REST page and the reverse. `ts` is per drone, so comparing one drone's values is sound.
- **Link loss** comes from `state.link = "LOST"` and `last_seen_at` (the ingestor watchdog decides). The UI runs no timeout of its own.
- **Unknown drone in an event** (registered after our last load): keep the state and fetch `GET /drones/{id}` once for name and home.
- **Token expiry mid-session:** the server's 30 s sweep disconnects expired sessions, and a reconnect with a bad token gets `connect_error {message: "token_expired" | "unauthorized"}` (`SocketConnectError`). On either reason the client stops reconnecting and calls `auth.logout("expired")`. Any other disconnect reconnects.
- **Connection badge:** live / reconnecting / offline, so the dispatcher knows when the map may be stale.

## 4. Auth and page reload

- **Access token in memory only** (the auth store), never `localStorage`, `sessionStorage` or a script-readable cookie. Reason: an XSS bug cannot lift a stored token for later ([ADR 0015](../adr/0015-frontend-token-handling.md)).
- **What the API offers today:** `POST /auth/login` returns a 15-minute access token and the user; `GET /auth/me` returns the user. **There is no refresh endpoint and no cookie.**
- **Honest consequence: a page reload, a new tab, or 15 minutes of work all mean logging in again.** The login page says so after an expiry.
- ADR 0011 planned "refresh tokens (httpOnly cookie plus CSRF) in M3". It is not built. That is open question Q1 for backend; the UI does not work around it.
- **Role** comes from `user.role` in the login response (and `GET /auth/me` once a refresh flow restores sessions). The UI never decodes the JWT.

## 5. Map ([ADR 0016](../adr/0016-map-library.md))

| | Leaflet 1.9 | MapLibre GL |
|---|---|---|
| Bundle (min+gz) | about 42 kB | about 230 kB plus a style |
| ~200 drones at 1-2 updates/s | canvas renderer, `circleMarker.setLatLng`: comfortable | GeoJSON source + layer: scales to thousands, overkill here |
| Vitest (jsdom) | runs; markers are DOM/canvas we can assert | needs WebGL, must be mocked entirely |
| Playwright headless | no GPU needed | WebGL through SwiftShader: slower, flakier in CI |
| Tiles | raster XYZ from any provider | vector tiles want a style and tile source |

**Decision: Leaflet with the canvas renderer.** Reason: 200 markers is far below where WebGL pays off, and real DOM/canvas keeps both test layers simple. This changes the blueprint's MapLibre choice.

- **Tiles:** never `tile.openstreetmap.org` from dev, CI or demos (its tile usage policy forbids app and heavy use). URL and attribution come from `VITE_TILE_URL` and `VITE_TILE_ATTRIBUTION`; decided: CARTO basemaps (`light_all`, free, no key, attribution "© OpenStreetMap contributors © CARTO"), set in `web/.env.example`. A public demo (M7) needs a provider with a domain-locked key and a spend cap. **Tests load no network tiles:** Vitest has none, Playwright routes tile requests to a blank PNG.
- Markers are coloured by `flight_state` and `link`. Selecting a drone opens a side panel, not a Leaflet HTML popup.

## 6. Role-aware UI

- The server enforces every permission (ADR 0011). The UI only hides or disables what the role cannot do, so nobody clicks into a certain 403.
- `useRole().can(permission)` reads `web/src/auth/permissions.ts`, a copy of the role table. A unit test checks it against the `x-permission` values in the OpenAPI document, so the copy cannot drift.
- Viewer: map and lists. Dispatcher: plus commands, missions, alert ack. Admin: plus drones, users, audit.

## 7. Testing

- **Vitest** (jsdom) for stores, composables and components. `realtime/socket.ts` is replaced by a fake emitter (`vi.mock`) the test drives: connect, event, disconnect with a reason. The API client is tested with `fetch` mocked at the boundary.
- Must-have unit tests (one per acceptance criterion): a stale `ts` is dropped; reconnect triggers `loadAll()`; `token_expired` logs out and stops reconnecting; 401 logs out; a drone name like `<img src=x onerror=...>` renders as text.
- **`data-testid`** on everything a test acts on or reads: kebab-case `area-thing[-id]`, e.g. `login-submit`, `fleet-map`, `drone-row-gw0-d001`, `connection-badge`. Tests never select by CSS class or copy text.
- **Playwright:** one happy path per feature against `make up` + `make sim`, written by automation from the acceptance criteria. Thin slice: log in, a sim drone appears and moves, the socket drops, the map catches up.
- **Out until M4:** visual regression, cross-browser matrix, accessibility audit, browser load test at 200+ drones, Storybook.

## 8. Local run

- `make up` (API on `127.0.0.1:8000`) and `make sim`, then `pnpm dev` in `web/`.
- **Vite proxy, not CORS:** `/api` and `/socket.io` (`ws: true`) proxy to the API. The API configures no CORS, and one origin behind one ingress keeps it that way in production. Reason: no allow-list to get wrong.
- The proxy target comes from `SKYHAUL_API_URL` (read in `vite.config.ts`, Node side, default `http://127.0.0.1:8000`; it is not a secret). Browser env is only `VITE_TILE_URL` and `VITE_TILE_ATTRIBUTION`. **Nothing secret may use the `VITE_` prefix:** it ships in the bundle. `web/.env.example` lists them empty.

## 9. Front-end security

- **Drone names, alert messages, NACK details and emails are untrusted.** Render with `{{ }}` only; `vue/no-v-html` is an ESLint error. Leaflet tooltips get text nodes, never HTML strings.
- **CSP** (an HTTP header from whatever serves the build, M4 deploy): `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'` (Leaflet sets inline styles); `img-src 'self' data: <tile host>; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`. Same origin keeps `connect-src` at `'self'`.
- No token in URLs, logs or error reports. Playwright traces are scrubbed before upload (automation).
- `?next=` after login accepts only a path that starts with a single `/`, so it cannot redirect off-site.

## 10. Contract gaps and open questions (backend follow-ups)

- **Q1 Refresh (decided: accept the hard logout for M3):** no refresh endpoint or cookie, despite ADR 0011. Without it, reload means re-login. Needs a backend issue (httpOnly `SameSite=Strict` refresh cookie plus CSRF, as ADR 0011 planned) and its own ADR when designed.
- **Q2 Mission and Alert have no `updated_at` or `version`,** so a late `mission.updated` or `alert.updated` cannot be told from a newer one. Interim: order by lifecycle rank (and `acknowledged_at`/`cleared_at` for alerts). Fix: a monotonic `version` field.
- **Q3 Refetch cost:** `GET /drones` caps at 200 per page, so a refetch past 200 drones is several requests. Fine for M3; revisit in M4.
- **Q4 Tile provider (decided):** CARTO, no key.
- **Q5 Logout is client-side only** (drop the token). No revocation, so a stolen token lives up to 15 minutes. Acceptable while Q1 is open.

## 11. Slice plan

1. **Thin slice (first issue): login + live map + `make seed`.** `make seed` creates a local admin (via `flask skyhaul bootstrap`), registers a few drones (`POST /drones`) and spawns them in the simulator, so the map has moving drones right after `make up` + `make sim`. Test profile only; the password is generated and printed once, never stored in git. `api/client.ts` with token middleware and 401; `stores/auth.ts`, `stores/fleet.ts`, `stores/connection.ts`; `realtime/socket.ts` with `drone.state`, reconnect refetch, `ts` staleness and `token_expired` logout; `LoginView`, `FleetView` with `FleetMap` and `ConnectionBadge`; router guard; Vite proxy; `vue/no-v-html`; the must-have unit tests; one Playwright path.
2. Drone list and detail panel (telemetry history), role-aware.
3. Missions: create, start, abort, `mission.updated`.
4. Alerts: list, ack, `alert.raised` / `alert.updated`.
5. Commands: send with an idempotency key, `command.updated`.
6. Admin: drones and users. The copilot UI waits for the copilot service.
