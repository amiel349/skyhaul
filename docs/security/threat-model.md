# Threat model

Owner: security agent. Updated whenever a milestone adds a surface.

Skyhaul is a public repository. The main risks are leaked secrets, test evidence published with tokens in it, broken access control, and AI-specific abuse.

| Risk | Where | Mitigation | Milestone | Status |
|---|---|---|---|---|
| Committed secrets | .env, compose, CI config | Only `.env.example` in git; gitleaks in pre-commit and CI; GitHub secret scanning and push protection; `.env` blocked by a pre-commit hook | M0 | done |
| Default passwords | RabbitMQ, Postgres, Redis | No defaults: compose fails if a value is missing; `make env` generates random values | M0 | done |
| Secrets in images | Dockerfiles, registry | `.dockerignore` excludes `.env`; multi-stage builds; no secrets in ENV or build args; non-root user; Trivy scan | M0, M7 | partial |
| Personal data | git history, README | Commits use the GitHub noreply address; no phone or email in the repo | M0 | done |
| Employer material | The author's past and current employers | No code, names, documents or message names from either | always | ongoing |
| CI supply chain | Actions, dependencies | Actions pinned by SHA; `permissions: contents: read`; `persist-credentials: false`; no `pull_request_target`; lockfiles; Dependabot with a 7-day cooldown; pnpm `minimumReleaseAge` of 7 days | M0 | done |
| Agents reading secrets | Claude Code | Deny rules for `.env*`, keys and `secrets/` in `.claude/settings.json`; agent instructions | M0 | done |
| Exposed services | Local stack | Ports bound to 127.0.0.1 only | M0 | done |
| Malicious or malformed drone messages | `skyhaul_contracts.Codec`, every AMQP consumer and producer | The Codec is the only decode/encode path: 64 KiB (65,536 bytes) size check before parsing; strict UTF-8 and JSON (no NaN/Infinity, no duplicate keys); closed envelope; ASCII-only id and type patterns that are also the routing-key words; payloads validated strictly; error details name fields and kinds only, never input values (`<extra>` for unexpected keys); encode re-decodes its own output. Every decode failure is a `DecodeError` (deep nesting cannot escape as `RecursionError`) | M1 | done |
| Queue flooding and poison messages | `drone_sim.cmd`, `fleet.dlx` / `fleet.dead` | Queue arguments: quorum command queue with `x-delivery-limit=5` dead-letters messages that keep crashing a consumer; both queues bounded by count (10,000) and bytes (655,360,000), command queue rejects publishes when full, `fleet.dead` drops its oldest; `fleet.dead` keeps raw bodies and is inspected by hand only | M1 | done |
| Consumer message handling | Simulator consumer (and later the ingestor) | Undecodable bodies rejected without requeue to the DLX; routing-key `<id>` and `<TYPE>` checked against the envelope (and the version hint) before any NACK is sent; ack only after replies are in the outbox; a message the broker keeps nacking is retried a bounded number of times, then dropped and logged | M1 (#7) | done |
| Test backdoors in production: simulator control API | `drone-sim` control API (`/drones`, `/drones/{id}/faults`, `/reset`, `/stats`) | Compose `test` profile only, published on `127.0.0.1:8100` only; bearer token from env, at least 32 characters, no default, constant-time compare, checked before routing, never logged; request bodies validated and capped at 16 KiB; one validated model per fault kind with ranges; delayed replies capped at 1,000 per namespace and dropped on reset or when their drone is deleted; `/healthz` reveals only ok/degraded; no reset endpoint in the fleet API | M1 (#9) | done |
| Test backdoors in production: fake LLM | `fake-llm` | Test compose profile only, internal network; never configured in the demo deployment | M5, M7 | planned |
| Broken access control | API endpoints, Socket.IO rooms | Server-side permission checks, tenant filter on every query, socket auth per connection and room, cross-tenant tests | M2 | planned |
| Password guessing | Login | argon2id, rate limit, failed logins in the audit log | M2 | planned |
| Over-privileged services | RabbitMQ, Postgres, Redis | Per-service RabbitMQ users limited to their exchanges; non-superuser DB role; Redis password | M2 | planned |
| Token theft | Browser | Access token in memory, refresh token in an httpOnly SameSite cookie, CSRF token, CSP | M3 | planned |
| Secrets in test evidence | Traces, videos, Allure, logs | Scrub auth headers, cookies and tokens before upload; throwaway users per run; reports published from main only | M4 | planned |
| Prompt injection | Drone names, alert text, chat | Tool results treated as data; tools run with the user's token; confirmation before moving drones; injection eval cases | M5 | planned |
| XSS from model output | Chat panel | DOMPurify; never `v-html` on raw text | M5 | planned |
| API key cost abuse | Demo, CI | PR CI uses the fake LLM; separate demo key with a spend limit; rate limits | M5, M7 | planned |
| AI PR reviewer abuse | `claude-review.yml` | Runs only on demand: a `/claude-review` comment from the owner, members or collaborators, or a manual dispatch; the workflow file always comes from `main`, so a PR can't change it; user text passed through `env` and the model name checked against an allowlist; contents read-only; tools limited to reading and commenting; PR text treated as data; advisory, not a required check | M0 | done |
| Triage agent leaks | Logs to Claude, PR comments | Redact before sending; agent has no tools; fixed code posts with a `pull-requests: write` token | M6 | planned |
