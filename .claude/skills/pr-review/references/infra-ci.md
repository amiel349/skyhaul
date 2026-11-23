# Infrastructure and CI

Applies to `deploy/**`, `**/Dockerfile`, `.github/**`, `Makefile`, `pyproject.toml`, lockfiles and `.pre-commit-config.yaml`.

## Docker images
- Multi-stage builds. The runtime stage has no build tools, no source checkout beyond what runs, and no package manager caches.
- Containers run as a non-root user with a fixed UID.
- No secrets in `ENV`, `ARG`, or copied files. `.dockerignore` keeps `.env`, `.git` and keys out of the build context.
- Base images are pinned to a version tag (and updated by Dependabot). No `latest`.
- A `HEALTHCHECK` exists for long-running services, and it checks real readiness.
- The virtualenv is built at the same path it runs from (venv scripts hard-code their interpreter path).

## Compose
- No default passwords: secrets use `${VAR:?message}`. A default like `${PASSWORD:-postgres}` is blocking.
- Ports bind to `127.0.0.1`, never to all interfaces.
- `depends_on` uses `condition: service_healthy`, not just start order.
- Test-only services (simulator control API, fake LLM) live in a `test` profile and are never in the default or production config.
- Volumes for data, no bind mounts of the repo into production-style services.

## GitHub Actions
- Every third-party action is pinned to a full commit SHA with the version in a comment. A tag or branch reference is blocking.
- Top-level `permissions: contents: read`. Jobs add only what they need, at job level.
- `actions/checkout` uses `persist-credentials: false` unless a later step needs to push.
- Never `pull_request_target` together with checking out or running PR code. Blocking.
- Untrusted input (`github.event.pull_request.title`, `body`, branch names) is never interpolated directly into `run:` scripts; pass it through `env:` and quote it. Direct interpolation is blocking (script injection).
- Secrets are only exposed to the steps that need them, never echoed, and never available to fork PRs.
- Jobs have `timeout-minutes` where they can hang, and `concurrency` to cancel superseded runs.
- Required checks in branch protection match the job names; renaming a job needs the protection rule updated.

## Dependencies
- New dependencies are needed, maintained, and widely used. Prefer the standard library or an existing dependency.
- Lockfiles (`uv.lock`, `pnpm-lock.yaml`) are updated in the same PR, and CI installs with `--locked` / `--frozen-lockfile`.
- Dependabot keeps a cooldown, and deliberate pins (TypeScript 5.x, Python 3.12) stay in its ignore list.
- Dev-only tools stay in dev dependency groups, not runtime dependencies.

## Makefile and scripts
- Shell scripts use `set -euo pipefail` and quote variables.
- Make targets match what CI runs, so `make check` locally predicts CI.
