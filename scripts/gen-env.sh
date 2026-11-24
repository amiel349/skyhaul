#!/usr/bin/env bash
# Creates .env from .env.example and fills every empty secret with a random value.
# Leaves ANTHROPIC_API_KEY empty: add your own key by hand if you need it.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env already exists. Delete it first if you want new secrets." >&2
  exit 1
fi

umask 077
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" =~ ^([A-Z_]+)=$ && "${BASH_REMATCH[1]}" != "ANTHROPIC_API_KEY" ]]; then
    # 32 random bytes: 64 hex characters, above the control token's 32-character minimum.
    echo "${BASH_REMATCH[1]}=$(openssl rand -hex 32)"
  else
    echo "$line"
  fi
done < .env.example > .env

echo "Created .env with generated secrets (permissions 600)."
