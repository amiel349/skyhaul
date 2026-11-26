# 0018 · Session refresh

Status: proposed · 2026-11-26 · becomes accepted when the backend PR lands

Amends [ADR 0015](0015-frontend-token-handling.md): a reload, a new tab or the 15-minute expiry no longer means signing in again, and the "log out 30 s before expiry" rule becomes "refresh 60 s before expiry". Fulfils the refresh cookie that [ADR 0011](0011-auth-and-roles.md) planned for M3. Implementation plan: [`docs/architecture/session-refresh.md`](../architecture/session-refresh.md).

## Context

The access token is a 15-minute JWT kept in memory only. Today a reload drops it, so every reload and every shift longer than 15 minutes ends at the login page. The fix must not put any credential where a script can read it. A later public demo will hand out anonymous VISITOR sessions (one click, a role, a sandbox namespace, short lived), so the session must allow a per-role lifetime and extra claims. Visitor sessions are out of scope here.

## Decision

**Refresh token**
- Opaque, 32 random bytes from `secrets.token_urlsafe(32)` (256 bits, 43 base64url characters). Not a JWT: the server must look it up anyway to rotate and revoke it.
- Stored only as `SHA-256(token)`. A slow hash buys nothing for a 256-bit random value, and SHA-256 allows an indexed equality lookup.
- New table `refresh_tokens`: `id`, `user_id` (FK, on delete cascade), `family_id`, `token_hash` (unique), `csrf_hash`, `issued_at`, `expires_at`, `used_at`, `revoked_at`, `replaced_by`. A family is one login; every rotation adds a row to it.

**Lifetime**
- 7 days **absolute** per family, not sliding: every row of a family carries the family's `expires_at`, so a stolen and refreshed token still dies 7 days after the login. A dispatcher signs in once a week.
- Per-role hook: `refresh_ttl(role) -> timedelta` returns `REFRESH_TOKEN_TTL_S` for every role today. A VISITOR role later returns a short value from the same place.
- Claim hook: `access_claims(user) -> dict` builds the access-token claims for login and refresh alike. A visitor's sandbox namespace becomes one more claim there later. The database still authorizes (ADR 0011).

**Rotation and reuse detection**
- Every refresh marks the presented row `used_at = now`, `replaced_by = new.id` and issues a new row in the same family, in one transaction that holds `SELECT ... FOR UPDATE` on the presented row. The row lock is the guarantee: two refreshes of one token are serialized by Postgres, never interleaved.
- A presented token that is already used (outside the grace window below) or revoked is **reuse**: revoke every row of the family (`revoked_at = now`), audit `auth.refresh.reuse`, answer 401. Both the thief and the user lose the session; the user signs in again.

**Concurrent tabs: single-flight in the browser plus a 10 s server grace window**
- Tabs share one cookie jar. The SPA serializes refreshes with the Web Locks API (`navigator.locks.request("skyhaul-refresh", ...)`) and, inside the lock, first checks whether another tab already rotated (the `skyhaul_csrf` cookie changed); then it only needs a refresh of its own with the current cookie.
- The server still tolerates a race the client cannot prevent (old browser, two requests in flight): a token used less than `REFRESH_GRACE_S` (10 s) ago answers **409 `refresh_superseded`**, revokes nothing and issues nothing. The client retries once; the browser now sends the new cookie. Issuing nothing for an old token keeps one live token per family.

**Cookies** (fixed names, set by login and refresh, cleared with `Max-Age=0` by logout and by a 401 from refresh)
- `skyhaul_refresh`: the refresh token. `HttpOnly`, `SameSite=Strict`, `Path=/api/v1/auth`, `Max-Age` = remaining family lifetime, `Secure` from `AUTH_COOKIE_SECURE` (default true; `.env.example` sets false for http://localhost). No `__Host-` prefix: it forbids a narrow `Path` and plain http in dev.
- `skyhaul_csrf`: the CSRF token. Same flags but not `HttpOnly` and `Path=/`, so the SPA can read it after a reload.

**CSRF: a token bound to the refresh token, echoed in a header**
- Login and refresh return `csrf_token` (32 random bytes) in the JSON body and in the `skyhaul_csrf` cookie; the row stores its hash. `/auth/refresh` and `/auth/logout` need `X-CSRF-Token` equal to it (compared with `hmac.compare_digest` on the hashes). A missing or wrong header is 403 `csrf_failed` and changes nothing.
- Why not SameSite=Strict alone: it does not cover same-site attackers (a sibling subdomain of the demo host), and it depends on every browser getting SameSite right. A custom header cannot be sent cross-origin without a CORS preflight, which the API never grants.
- Why a readable cookie: the in-memory copy is gone after a reload, and the silent refresh needs the token. The CSRF token is not a credential: without the httpOnly cookie it does nothing, and a script that can read it can already send same-origin requests (XSS is out of CSRF's reach anyway).

**Endpoints** (contract: `contracts/openapi/fleet-api.v1.json`)
- `POST /auth/login`: as before, plus `csrf_token` in the body and both cookies. A refresh cookie sent with the login is revoked first and a new family starts (no session fixation). Login limits stay.
- `POST /auth/refresh` (`@public`, cookie plus `X-CSRF-Token`): rotates the cookie and returns `access_token`, `token_type`, `expires_in`, a new `csrf_token` and `user`. Errors: 401 `session_invalid` (no cookie, unknown, expired, revoked, reused, user inactive or deleted; cookies cleared), 403 `csrf_failed`, 409 `refresh_superseded`, 429.
- `POST /auth/logout` (`@public`, cookie plus `X-CSRF-Token`): revokes the family, clears both cookies, 204. Idempotent: no cookie or a dead one is still 204. A live cookie with a wrong header is 403.
- Both new routes are `@public` because the access token may have expired; the cookie and the CSRF header are their authentication. One 401 code for every refusal so a caller learns nothing; the audit row keeps the reason.

**Rate limits**: refresh and logout 30/min per ip each (a normal tab refreshes every ~14 min). Login stays 5/min per (ip, email) and 20/min per ip.

**Audit**: `auth.refresh` with outcome `failure` and a reason (`unknown`, `expired`, `revoked`, `inactive`, `csrf`), `auth.refresh.reuse` (outcome `denied`, family id), `auth.logout` (success). A successful refresh is not audited: one row per user every 14 minutes adds volume and no decision value; the login row already records the session start.

**Disabled or deleted user**: refresh loads the user and refuses an inactive one (401, family revoked). A deleted user's rows go with the cascade. The socket sweep already drops their live sockets.

**Socket.IO**: unchanged on the server. The socket's session expires with the access token it connected with, and the 30 s sweep disconnects it. The client therefore reconnects right after each refresh with the new token (the `auth` callback reads the store), so the sweep never cuts a live session.

**Cleanup**: `flask skyhaul prune-sessions` deletes rows whose `expires_at` is more than 1 day past (the extra day keeps reuse evidence briefly). Run by hand or a cron job; login also deletes the user's own expired rows.

**Frontend flow**
1. App start: before the router guard decides, call `POST /auth/refresh` once with the `skyhaul_csrf` cookie value. 200: the user is signed in. 401: show login. Network error: show login with the "unreachable" notice.
2. A timer refreshes 60 s before `expires_in` runs out, then reconnects the socket.
3. A 401 on any other call triggers one refresh (single-flight) and one retry of that call; a second 401 logs out.
4. Logout calls `POST /auth/logout`, then clears the store as today.
5. A failed refresh clears the store and goes to login with the "expired" notice.

**Not changed**: the access token stays in memory only (ADR 0015); the API stays same-origin behind the Vite proxy and the ingress, with no CORS; the bearer scheme, the 15-minute access token and the database-authorizes rule (ADR 0011); no "log out everywhere" endpoint yet.

## Consequences

- A reload or a new tab costs one refresh call instead of a login. Shifts run up to 7 days without a password.
- The API gains state per session: one table, one row per refresh, and a cleanup job.
- Stolen refresh cookies are caught on the next use by either party, at the price of ending the user's session too.
- The 10 s grace window lets a thief who races the user within 10 s get a 409 instead of tripping reuse detection; they still receive no token.
- The web app needs Web Locks (all current browsers) and its fake endpoints must learn the cookie flow.
- Deactivating a user ends their refresh at the next attempt, at most 15 minutes of access-token life after the change, as today.

## Alternatives considered

- Access token in `localStorage` or `sessionStorage`: survives reload, but XSS can steal it (ADR 0015).
- Refresh token as a JWT: stateless, but rotation and reuse detection need a server row anyway.
- Sliding lifetime: fewer logins for daily users, but a stolen token can live forever.
- SameSite=Strict alone, or an `Origin` check alone: simpler, but weaker against same-site attackers and browser bugs; the header costs one line in the client.
- Classic double-submit without binding: an attacker who can plant a cookie on a sibling subdomain chooses both values; binding to the refresh row stops that.
- Grace window that issues a second token: smoother for races, but two live tokens per family and a weaker reuse signal.
- Server-side sessions with a session cookie on every request: needs CSRF on every route and changes the socket auth; the access token stays the per-request credential instead.
