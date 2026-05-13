-- goldenmatch_pg SQL extension schema v0.1.0

-- ══════════════════════════════════════════════════════════════════════
-- Pipeline tables (job management)
-- ══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS goldenmatch._jobs (
    name TEXT PRIMARY KEY,
    config_json JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    last_run_at TIMESTAMPTZ,
    status TEXT DEFAULT 'configured',
    -- v1.7-v1.12: most-recent run's AutoConfigController telemetry. NULL when
    -- the job used an explicit config (controller didn't fire) or hasn't run.
    last_telemetry_json JSONB
);
-- ALTER for upgrades from extension installs that pre-date the telemetry column.
ALTER TABLE goldenmatch._jobs ADD COLUMN IF NOT EXISTS last_telemetry_json JSONB;

CREATE TABLE IF NOT EXISTS goldenmatch._pairs (
    job_name TEXT REFERENCES goldenmatch._jobs(name) ON DELETE CASCADE,
    id_a BIGINT,
    id_b BIGINT,
    score DOUBLE PRECISION,
    matchkey TEXT,
    field_scores JSONB
);
CREATE INDEX IF NOT EXISTS idx_pairs_job ON goldenmatch._pairs(job_name, id_a, id_b);

CREATE TABLE IF NOT EXISTS goldenmatch._clusters (
    job_name TEXT REFERENCES goldenmatch._jobs(name) ON DELETE CASCADE,
    cluster_id BIGINT,
    record_id BIGINT,
    is_golden BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_clusters_job ON goldenmatch._clusters(job_name, cluster_id);

CREATE TABLE IF NOT EXISTS goldenmatch._golden (
    job_name TEXT REFERENCES goldenmatch._jobs(name) ON DELETE CASCADE,
    cluster_id BIGINT,
    record_data JSONB
);

-- ══════════════════════════════════════════════════════════════════════
-- Table-based functions (primary interface)
-- ══════════════════════════════════════════════════════════════════════

CREATE FUNCTION "goldenmatch_dedupe_table"(
    "table_name" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_dedupe_table_wrapper';

CREATE FUNCTION "goldenmatch_match_tables"(
    "target_table" TEXT,
    "reference_table" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_match_tables_wrapper';

-- ══════════════════════════════════════════════════════════════════════
-- Pipeline functions (job management)
-- ══════════════════════════════════════════════════════════════════════

CREATE FUNCTION "gm_configure"(
    "job_name" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_configure_wrapper';

CREATE FUNCTION "gm_run"(
    "job_name" TEXT,
    "table_name" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_run_wrapper';

CREATE FUNCTION "gm_jobs"() RETURNS TEXT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_jobs_wrapper';

CREATE FUNCTION "gm_golden"(
    "job_name" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_golden_wrapper';

CREATE FUNCTION "gm_drop"(
    "job_name" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_drop_wrapper';

CREATE FUNCTION "gm_pairs"(
    "job_name" TEXT
) RETURNS TABLE ("id_a" BIGINT, "id_b" BIGINT, "score" DOUBLE PRECISION)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_pairs_wrapper';

CREATE FUNCTION "gm_clusters"(
    "job_name" TEXT
) RETURNS TABLE ("cluster_id" BIGINT, "record_id" BIGINT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_clusters_wrapper';

-- ══════════════════════════════════════════════════════════════════════
-- Table-returning functions (structured results)
-- ══════════════════════════════════════════════════════════════════════

CREATE FUNCTION "goldenmatch_dedupe_pairs"(
    "table_name" TEXT,
    "config_json" TEXT
) RETURNS TABLE ("id_a" BIGINT, "id_b" BIGINT, "score" DOUBLE PRECISION)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_dedupe_pairs_wrapper';

CREATE FUNCTION "goldenmatch_dedupe_clusters"(
    "table_name" TEXT,
    "config_json" TEXT
) RETURNS TABLE ("cluster_id" BIGINT, "record_id" BIGINT, "cluster_size" BIGINT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_dedupe_clusters_wrapper';

-- ══════════════════════════════════════════════════════════════════════
-- Scalar functions
-- ══════════════════════════════════════════════════════════════════════

CREATE FUNCTION "goldenmatch_score"(
    "value_a" TEXT,
    "value_b" TEXT,
    "scorer" TEXT DEFAULT 'jaro_winkler'
) RETURNS DOUBLE PRECISION
STRICT PARALLEL RESTRICTED
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_score_wrapper';

CREATE FUNCTION "goldenmatch_score_pair"(
    "record_a" TEXT,
    "record_b" TEXT,
    "config" TEXT
) RETURNS DOUBLE PRECISION
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_score_pair_wrapper';

CREATE FUNCTION "goldenmatch_explain"(
    "record_a" TEXT,
    "record_b" TEXT,
    "config" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_explain_wrapper';

-- ══════════════════════════════════════════════════════════════════════
-- JSON-based functions (programmatic use)
-- ══════════════════════════════════════════════════════════════════════

CREATE FUNCTION "goldenmatch_dedupe"(
    "rows_json" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_dedupe_wrapper';

CREATE FUNCTION "goldenmatch_match"(
    "target_json" TEXT,
    "reference_json" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_match_wrapper';

-- ══════════════════════════════════════════════════════════════════════
-- AutoConfig + controller telemetry (v1.7-v1.12)
-- ══════════════════════════════════════════════════════════════════════

-- Run AutoConfigController on a table and return the committed GoldenMatchConfig
-- as JSON. Pipe the output into goldenmatch_dedupe_full to apply it.
CREATE FUNCTION "goldenmatch_autoconfig"(
    "table_name" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_autoconfig_wrapper';

-- Same as above but returns the controller telemetry blob (stop_reason,
-- decisions, health, indicator priors, committed NE).
CREATE FUNCTION "goldenmatch_autoconfig_telemetry"(
    "table_name" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_autoconfig_telemetry_wrapper';

-- Deduplicate a table with a *full* GoldenMatchConfig JSON (supports
-- negative_evidence / Path Y, per-matchkey scorers, standardization, etc.).
CREATE FUNCTION "goldenmatch_dedupe_full"(
    "table_name" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_dedupe_full_wrapper';

-- Same as above but returns the controller telemetry from that run.
CREATE FUNCTION "goldenmatch_dedupe_full_telemetry"(
    "table_name" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_dedupe_full_telemetry_wrapper';

-- Retrieve the telemetry from the most recent gm_run of a named job.
-- Returns '{"available":false}' for jobs that haven't run or used an
-- explicit config.
CREATE FUNCTION "gm_telemetry"(
    "job_name" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_telemetry_wrapper';

-- ══════════════════════════════════════════════════════════════════════
-- Identity Graph (v2.0)
-- ══════════════════════════════════════════════════════════════════════
-- Contract: docs/superpowers/specs/2026-05-12-identity-graph-duckdb-contract.md
-- All identity functions accept the path to the identity store as an explicit
-- second arg. For SQLite pass the filesystem path; for Postgres pass the libpq
-- DSN. Empty-string filters mean "no filter on that dimension".

-- Resolve a `{source}:{source_pk}` record id to its identity view JSON.
-- Returns {"found": false} when the record has no identity.
CREATE FUNCTION "goldenmatch_identity_resolve"(
    "record_id" TEXT,
    "db_path" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_identity_resolve_wrapper';

-- Return the full identity view JSON for a given entity_id.
CREATE FUNCTION "goldenmatch_identity_view"(
    "entity_id" TEXT,
    "db_path" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_identity_view_wrapper';

-- Return the temporal event log for an identity as a JSON array.
CREATE FUNCTION "goldenmatch_identity_history"(
    "entity_id" TEXT,
    "db_path" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_identity_history_wrapper';

-- List `conflicts_with` evidence edges as a JSON array.
CREATE FUNCTION "goldenmatch_identity_conflicts"(
    "dataset" TEXT,
    "db_path" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_identity_conflicts_wrapper';

-- List identities filtered by dataset and status (empty = no filter).
CREATE FUNCTION "goldenmatch_identity_list"(
    "dataset" TEXT,
    "status" TEXT,
    "db_path" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_identity_list_wrapper';
