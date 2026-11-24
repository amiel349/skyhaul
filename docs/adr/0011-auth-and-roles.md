# 0011 · Auth and roles, single organization

Status: accepted · 2026-11-24

## Context

The fleet API can move aircraft, so every endpoint and socket event needs authentication and a permission check on the server. M2 needs a login, three roles (admin, dispatcher, viewer) and an audit trail. Earlier drafts also made the API multi-tenant (a tenant table, a tenant claim, tenant-scoped keys and rooms). Skyhaul is operated by one organization, and parallel E2E tests already isolate through simulator namespaces (ADR 0004, 0009), not tenants.

## Decision

- **Single organization; multi-tenancy is out of scope.** There is no tenant table, no `tenant_id` column, no tenant claim, no tenant prefix on Redis keys and no tenant socket room. The reason is to keep M2 simple. Adding it later means a tenant column with composite keys, a token claim and repositories that take the tenant as a required argument.
- **Drone ids are globally unique** (simple primary key, the same id as in the routing keys) and never reused: delete is a soft delete, and registering a taken id is a 409.
- **JWT access tokens**, HS256 with `JWT_SECRET` (env, at least 32 bytes, no default), via PyJWT. Claims: `iss=skyhaul-api`, `aud=skyhaul`, `sub`, `role`, `iat`, `exp` (15 min), `jti`. Access token only in M2; refresh tokens (httpOnly cookie plus CSRF) come in M3.
- **The token identifies, the database authorizes.** Each request loads the user by `sub`, rejects an inactive one, and takes the role from the row, not from the claim.
- **Permissions, default deny.** viewer: `fleet:read`, `mission:read`, `alert:read`. dispatcher: viewer plus `command:send`, `mission:write`, `alert:ack`. admin: dispatcher plus `drone:admin`, `user:admin`, `audit:read`. Every route carries `@require(Permission.X)` or `@public`; a `before_request` hook refuses any route with neither, and a test walks `url_map` to prove it. No token is 401, a lower role is 403, a missing resource is 404.
- **Passwords:** argon2id (`argon2-cffi` defaults, RFC 9106 low-memory profile), rehash on login when parameters change, a dummy verify for unknown emails so timing does not reveal accounts.
- **Rate limits** (Flask-Limiter, Redis storage): login 5/min per (ip, email) and 20/min per ip; commands 30/min per user; everything else 300/min per user. 429 with `Retry-After`. Every login attempt is audited.
- **Bootstrap through a CLI:** `flask skyhaul bootstrap --admin-email <e>` creates the first admin, reading the password from stdin. There is no signup endpoint; admins create users through `POST /users`.

## Consequences

- One indexed user lookup per request buys immediate effect for deactivation and role changes.
- Without tenants, any authenticated user sees the whole fleet within their role. That is the intended model for one organization.
- A 409 on drone registration reveals that an id exists; with one organization there is nobody to hide it from.
- Adding tenants later touches every table and query. The scoping must then be added everywhere at once, with cross-tenant tests, not one endpoint at a time.
- 15-minute tokens without refresh mean dispatchers log in again during long shifts until M3.

## Alternatives considered

- Multi-tenant from M2 (tenant column, `tid` claim, scoped repositories, 404 across tenants): the approved draft, dropped to simplify M2; there is no second organization to serve.
- Role in the token, trusted until expiry: saves a lookup, but a demoted or deactivated user keeps access for up to 15 minutes.
- Server-side sessions: easy revocation, but needs a session store and CSRF handling now; JWT keeps M2 stateless and M3 adds refresh.
- bcrypt: fine, but argon2id is the current recommendation and is memory-hard.
- A signup or platform-admin API for the first user: an unauthenticated path to admin that nothing needs.
