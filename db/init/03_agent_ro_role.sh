#!/bin/bash
# =============================================================================
# Guardrail: dedicated read-only Postgres role for the AI chat agent.
# Runs after 01 (schema) and 02 (views) via docker-entrypoint-initdb.d ordering.
# A .sh file (not .sql) is required here because the role's password comes
# from an environment variable — plain .sql init files are piped through
# psql with no shell/env substitution.
#
# See AGENTIC_RAG_ARCHITECTURE.md §4 Stage 5 for why this exists: even a bug
# in the SQL AST validator can't do more than Postgres itself allows, because
# this role can only ever SELECT from the 5 Phase 1 tables + 10 views.
# =============================================================================
set -e

AGENT_DB_PASSWORD="${AGENT_DB_PASSWORD:-agent_ro_pw}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_ro') THEN
      CREATE ROLE agent_ro LOGIN PASSWORD '${AGENT_DB_PASSWORD}';
    ELSE
      ALTER ROLE agent_ro LOGIN PASSWORD '${AGENT_DB_PASSWORD}';
    END IF;
  END
  \$\$;

  -- No write/DDL privileges of any kind, and no ability to create objects.
  REVOKE CREATE ON SCHEMA public FROM agent_ro;

  -- Covers the 5 Phase 1 tables AND all 10 views (Postgres exposes views
  -- through pg_class same as tables, so "ALL TABLES IN SCHEMA" includes them).
  GRANT USAGE ON SCHEMA public TO agent_ro;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_ro;

  -- Any table/view added by a future migration is readable automatically —
  -- no manual GRANT needed when Phase 2+ tables are introduced.
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agent_ro;

  -- Belt-and-braces: cap how long/how much a single agent query can do,
  -- independent of the app-level statement_timeout set per-connection.
  ALTER ROLE agent_ro SET statement_timeout = '5s';
EOSQL

echo "[init] agent_ro read-only role ready."
