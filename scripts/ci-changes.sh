#!/usr/bin/env bash
# Decide which CI jobs a change needs. Reads changed file paths on stdin (one per line) and
# prints `name=true|false` lines for $GITHUB_OUTPUT.
#
# Safe by default: a path that matches no rule below turns everything on. Only paths known to
# be documentation turn nothing on. CI and build plumbing (.github, Makefile, this script)
# turns everything on, because those change how every job runs.
#
# Usage: git diff --name-only BASE HEAD | scripts/ci-changes.sh
set -euo pipefail

python=false web=false contracts=false integration=false compose=false

all() { python=true web=true contracts=true integration=true compose=true; }

while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$f" in
    .github/* | Makefile | scripts/ci-changes.sh) all ;;
    services/* | pyproject.toml | uv.lock | tests/*)
      python=true integration=true contracts=true compose=true ;;
    contracts/*)
      python=true integration=true contracts=true web=true compose=true ;;
    web/*)
      web=true contracts=true ;;
    deploy/* | scripts/*)
      compose=true ;;
    docs/* | .claude/* | *.md | LICENSE | .gitignore | .dockerignore | .gitleaks.toml | .pre-commit-config.yaml | .editorconfig)
      ;; # documentation and repo hygiene: the secret scan (always on) is enough
    *) all ;;
  esac
done

for name in python web contracts integration compose; do
  echo "$name=${!name}"
done
