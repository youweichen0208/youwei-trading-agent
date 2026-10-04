#!/bin/bash
# Phase 1A production PostgreSQL bootstrap (runs once on first initdb).
# Creates the migration role (DDL owner) and application role (DML only).
# Credentials come from the container environment; nothing is embedded.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set migrate_password="$POSTGRES_MIGRATE_PASSWORD" \
  --set app_password="$POSTGRES_APP_PASSWORD" <<'SQL'
-- psql interpolates :'var' only at the top level, NOT inside a
-- dollar-quoted DO $$...$$ body. So the role creation is written as
-- plain SQL statements (psql interpolates the password variables there).
SELECT format('CREATE ROLE youwei_migrate LOGIN PASSWORD %L', :'migrate_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'youwei_migrate')
\gexec

SELECT format('CREATE ROLE youwei_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'youwei_app')
\gexec

ALTER DATABASE youwei OWNER TO youwei_migrate;
GRANT ALL ON DATABASE youwei TO youwei_migrate;

GRANT CONNECT ON DATABASE youwei TO youwei_app;
GRANT USAGE ON SCHEMA public TO youwei_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO youwei_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO youwei_app;
ALTER DEFAULT PRIVILEGES FOR ROLE youwei_migrate IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO youwei_app;
ALTER DEFAULT PRIVILEGES FOR ROLE youwei_migrate IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO youwei_app;
SQL
