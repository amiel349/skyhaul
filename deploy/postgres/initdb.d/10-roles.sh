#!/usr/bin/env bash
# Database roles of the fleet backend (ADR 0013, m2-design section 10).
#
# Runs once, as the bootstrap superuser, when the Postgres volume is fresh: an existing
# local stack needs `make down -v` once. Passwords come from the environment only.
#
# - skyhaul_owner owns the public schema and runs the migrations (DDL). It owns the tables.
# - skyhaul_api and skyhaul_ingestor log in with no rights beyond CONNECT and schema USAGE;
#   their table grants come with the tables, from the migrations (0005 and later), so a new
#   table can never be granted wider than its migration says.
# - None of them is a superuser, and none may create roles or databases.
set -euo pipefail

: "${DB_OWNER_PASSWORD:?DB_OWNER_PASSWORD is not set}"
: "${DB_API_PASSWORD:?DB_API_PASSWORD is not set}"
: "${DB_INGESTOR_PASSWORD:?DB_INGESTOR_PASSWORD is not set}"

# psql variables (:'name' quotes a literal, :"name" an identifier), so no value is spliced
# into the SQL text by the shell.
psql -v ON_ERROR_STOP=1 --no-psqlrc \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" \
  -v owner_password="$DB_OWNER_PASSWORD" \
  -v api_password="$DB_API_PASSWORD" \
  -v ingestor_password="$DB_INGESTOR_PASSWORD" <<'SQL'
CREATE ROLE skyhaul_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'owner_password';
CREATE ROLE skyhaul_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'api_password';
CREATE ROLE skyhaul_ingestor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'ingestor_password';

-- Only these roles may connect; nobody else gets temp tables.
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO skyhaul_owner, skyhaul_api, skyhaul_ingestor;

-- citext is created here by the superuser, so the owner needs no CREATE on the database.
CREATE EXTENSION IF NOT EXISTS citext;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO skyhaul_owner;
GRANT USAGE ON SCHEMA public TO skyhaul_api, skyhaul_ingestor;
SQL
