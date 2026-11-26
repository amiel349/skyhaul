# Session refresh: implementation plan

Decision and reasons: [ADR 0018](../adr/0018-session-refresh.md). Contract: `/auth/login`, `/auth/refresh`, `/auth/logout` in [`contracts/openapi/fleet-api.v1.json`](../../contracts/openapi/fleet-api.v1.json), golden bodies in `contracts/examples/api/`. Two issues, one per agent. Backend lands first or in parallel against the contract; the frontend builds against fake endpoints.

## Order of work and where the contract lives

The OpenAPI contract, its golden examples and the regenerated TypeScript types are ready on branch `feat/session-refresh-contract` (commit 75bf502). They do NOT land with this design PR: `make openapi-check` compares the API with the contract for every implemented path (login changes here), and `pnpm typecheck` fails while the web login fake lacks `csrf_token`. So:

1. **This PR:** ADR 0018, the threat-model rows and this plan (docs only).
2. **Backend PR:** starts from `feat/session-refresh-contract` (the contract changes land with the code that implements them, so `make openapi-check` stays green). It also adds `csrf_token` to the login fake in `web/tests/fake-endpoints/auth.ts` (one line), so `pnpm typecheck` stays green.
3. **Frontend PR:** after the backend PR, against the real contract.

## Backend issue: refresh cookie, rotation, logout

- **Migration `0006_refresh_tokens`** (`services/fleet_core/.../migrations/versions/`): table `refresh_tokens` (`id uuid pk`, `user_id uuid fk users on delete cascade`, `family_id uuid`, `token_hash bytea unique`, `csrf_hash bytea`, `issued_at`, `expires_at`, `used_at null`, `revoked_at null`, `replaced_by uuid null`), indexes on `family_id`, `user_id` and `expires_at`. Grants for `skyhaul_api`: select, insert, update, delete (as in 0005).
- **Model** `RefreshToken` in `skyhaul_fleet_core.models`.
- **Token service** `skyhaul_api/auth/sessions.py`: `start(user)`, `rotate(token, csrf, now)`, `revoke_family(token)`, `prune(now)`; hooks `refresh_ttl(role)` and `access_claims(user)` (used by `tokens.issue_token`). Rotation in one transaction with `SELECT ... FOR UPDATE`; reuse outside `REFRESH_GRACE_S` revokes the family; inside it raises `refresh_superseded`.
- **Routes** in `auth/routes.py`: login sets both cookies and returns `csrf_token` and revokes a presented cookie; new `refresh` and `logout`, both `@public`; add the paths to the spec, so `make openapi-check` passes again. New error responses `SessionInvalid`, `CsrfFailed`, `RefreshSuperseded` in `openapi.py`.
- **Settings** (`config.py`, `.env.example`): `REFRESH_TOKEN_TTL_S` (default 604800), `REFRESH_GRACE_S` (default 10), `AUTH_COOKIE_SECURE` (default true; `.env.example` and the dev compose set false). Cookie names are constants.
- **Rate limits**: refresh and logout 30/min per ip.
- **Audit**: `auth.refresh` failure with reason, `auth.refresh.reuse` denied, `auth.logout` success. Never the token, the CSRF token or the cookie header.
- **Cleanup**: CLI `flask skyhaul prune-sessions`; login deletes the user's own expired rows.
- **Tests**
  - Unit: token and CSRF generation (length, alphabet), hashing, `refresh_ttl`, cookie attributes builder (Secure on and off).
  - API through the test client: login sets both cookies with the right flags; refresh rotates (old cookie then refused as reuse); missing or wrong CSRF is 403 and rotates nothing; expired, revoked or unknown cookie is 401 and clears cookies; inactive user is 401; logout revokes and is idempotent; 429 on refresh; refresh and logout are reachable with no bearer and with an expired one (access control walk still passes).
  - Integration (Testcontainers Postgres, one test): two concurrent refreshes of one token give one 200 and one 409; a replay after the grace window revokes the whole family and writes `auth.refresh.reuse`.

## Frontend issue: silent refresh and retry

- **Auth store** (`web/src/stores/auth.ts`): keep `csrfToken` in memory next to the token; `refresh()` single-flight (one shared promise) inside `navigator.locks.request("skyhaul-refresh")`, reading `skyhaul_csrf` from `document.cookie` when the store has none; `restore()` called once before the router guard decides; a timer 60 s before expiry; `logout()` calls `POST /auth/logout` then clears the stores as today. Replaces the 30 s early logout of ADR 0015.
- **API client** (`web/src/api/client.ts`): on a 401 with a token, await one refresh and retry the request once; a second 401, or a failed refresh, logs out with the "expired" notice. Never retry `/auth/*`. On 409 `refresh_superseded` retry the refresh once.
- **Socket**: after each successful refresh, reconnect `/fleet` so the `auth` callback sends the new token; the existing reconnect path reloads state.
- **Tests** with the fake endpoints in `web/tests` (add `csrf_token` to the login fake, add refresh and logout fakes): reload restores the session; no cookie shows login; a 401 is retried once after a refresh; a second 401 logs out; the timer refreshes and reconnects the socket; logout calls the server; two refreshes at once send one request.
