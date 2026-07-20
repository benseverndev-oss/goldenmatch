-- Upgrade goldenmatch_pg 0.13.0 -> 0.14.0
--
-- Adds gm_resolve(): the in-database stateful identity write path (#1913 P1).
-- Resolves a configured job's table into a Postgres-native identity dataset
-- (create/absorb/merge, incremental across runs). Identity writes go to the
-- DSN configured via the goldenmatch.identity_dsn GUC (or the backend
-- GOLDENMATCH_IDENTITY_DSN / GOLDENMATCH_DATABASE_URL env).

CREATE FUNCTION "gm_resolve"(
    "job_name" TEXT,
    "table_name" TEXT,
    "dataset" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_resolve_wrapper';
