-- goldenmatch_pg SQL extension schema v0.5.0

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

-- ══════════════════════════════════════════════════════════════════════
-- Core-API parity (mirrors duckdb/core_apis.py -- 13 UDFs)
-- ══════════════════════════════════════════════════════════════════════
-- Wrappers over goldenmatch's function-shaped core APIs. IDENTICAL JSON
-- in / JSON out contract to the DuckDB `goldenmatch_*` core-API UDFs so the
-- two backends are interchangeable. Table-input functions read the table via
-- SPI (`row_to_json`, like goldenmatch_dedupe_table); JSON-in functions take
-- the payloads directly. All read-only -- no REVOKE.

-- Profile a table (column stats, types, quality signals) as JSON.
CREATE FUNCTION "goldenmatch_profile_table"(
    "table_name" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_profile_table_wrapper';

-- Otsu threshold over a JSON array of scores. Returns SQL NULL when the
-- distribution is unimodal / too few scores.
CREATE FUNCTION "goldenmatch_suggest_threshold"(
    "scores_json" TEXT
) RETURNS DOUBLE PRECISION
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_suggest_threshold_wrapper';

-- Domain profile for a JSON array of column names, as JSON.
CREATE FUNCTION "goldenmatch_detect_domain"(
    "columns_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_detect_domain_wrapper';

-- Extract structured features from text. kind: product/electronics (default),
-- software, biblio/bibliographic. Returns the features as JSON.
CREATE FUNCTION "goldenmatch_extract_features"(
    "text" TEXT,
    "kind" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_extract_features_wrapper';

-- Evaluate predicted pairs (JSON array) or clusters (JSON object) against a
-- ground-truth JSON array of [a, b] pairs. Returns EvalResult.summary() JSON.
CREATE FUNCTION "goldenmatch_evaluate"(
    "pairs_json" TEXT,
    "ground_truth_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_evaluate_wrapper';

-- CCMS comparison of two clusterings (JSON objects). Returns
-- CompareResult.summary() JSON.
CREATE FUNCTION "goldenmatch_compare_clusters"(
    "a_json" TEXT,
    "b_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_compare_clusters_wrapper';

-- Run validation rules (JSON array of ValidationRule) over a table. Returns
-- {report, valid_rows, quarantine_rows, quarantine} JSON.
CREATE FUNCTION "goldenmatch_validate_table"(
    "table_name" TEXT,
    "rules_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_validate_table_wrapper';

-- Apply auto-fixes to a table. Returns {fixes, fixed_rows, rows} JSON.
CREATE FUNCTION "goldenmatch_autofix_table"(
    "table_name" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_autofix_table_wrapper';

-- Flag suspicious records in a table. sensitivity: low/medium/high. Returns
-- a JSON array of anomaly dicts.
CREATE FUNCTION "goldenmatch_detect_anomalies"(
    "table_name" TEXT,
    "sensitivity" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_detect_anomalies_wrapper';

-- Validate (table, full GoldenMatchConfig JSON) before a run. Returns
-- {has_errors, config_was_modified, findings} JSON.
CREATE FUNCTION "goldenmatch_preflight"(
    "table_name" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_preflight_wrapper';

-- Post-run signal report for (table, config). Derives pair_scores by running
-- dedupe_df on the table. Returns {signals, adjustments, advisories} JSON.
CREATE FUNCTION "goldenmatch_postflight"(
    "table_name" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_postflight_wrapper';

-- Train Fellegi-Sunter m/u probabilities via EM. Returns the EMResult JSON;
-- pass it straight to goldenmatch_score_probabilistic.
CREATE FUNCTION "goldenmatch_train_em"(
    "rows_json" TEXT,
    "matchkey_json" TEXT,
    "params_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_train_em_wrapper';

-- Score record pairs with trained Fellegi-Sunter probabilities. Returns a
-- JSON array of [a, b, score] triples above the link threshold.
CREATE FUNCTION "goldenmatch_score_probabilistic"(
    "rows_json" TEXT,
    "matchkey_json" TEXT,
    "em_result_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_score_probabilistic_wrapper';

-- ─── Correction CRUD (v0.5.0, Phase 6A of #437 surface sync) ──────────
--
-- File pair-level / field-level / cluster-decision corrections into
-- the goldenmatch MemoryStore. Read with `correction_list` or via
-- the `goldenmatch.corrections` view (no auth required for reads).
--
-- Permissions: `correction_add` is REVOKEd from PUBLIC at the bottom
-- of this file and granted to `goldenmatch_correction_writer`. Roles
-- needing write access must be GRANTed that role.

CREATE FUNCTION "correction_add"(
    "decision" TEXT,
    "dataset" TEXT,
    "id_a" BIGINT DEFAULT NULL,
    "id_b" BIGINT DEFAULT NULL,
    "cluster_id" BIGINT DEFAULT NULL,
    "field_name" TEXT DEFAULT NULL,
    "original_value" TEXT DEFAULT NULL,
    "corrected_value" TEXT DEFAULT NULL,
    "cluster_score" DOUBLE PRECISION DEFAULT NULL,
    "cluster_outcome" TEXT DEFAULT NULL,
    "reason" TEXT DEFAULT NULL,
    "matchkey_name" TEXT DEFAULT NULL,
    "source" TEXT DEFAULT NULL,
    "memory_path" TEXT DEFAULT NULL
) RETURNS TEXT
LANGUAGE c
AS 'MODULE_PATHNAME', 'correction_add_wrapper';

CREATE FUNCTION "correction_list"(
    "dataset" TEXT DEFAULT NULL,
    "memory_path" TEXT DEFAULT NULL
) RETURNS TEXT
LANGUAGE c
AS 'MODULE_PATHNAME', 'correction_list_wrapper';

-- Learning Memory: force a MemoryLearner pass; returns JSON
-- { "count": N, "adjustments": [...] }. Needs >= 10 corrections per matchkey.
CREATE FUNCTION "memory_learn"(
    "matchkey_name" TEXT DEFAULT NULL,
    "memory_path" TEXT DEFAULT NULL
) RETURNS TEXT
LANGUAGE c
AS 'MODULE_PATHNAME', 'memory_learn_wrapper';

-- Learning Memory status: JSON
-- { "total_corrections": N, "last_learn_time": ISO|null, "adjustments": [...] }.
CREATE FUNCTION "memory_stats"(
    "memory_path" TEXT DEFAULT NULL
) RETURNS TEXT
LANGUAGE c
AS 'MODULE_PATHNAME', 'memory_stats_wrapper';

-- Read-only view over the correction store. Wraps `correction_list`
-- with `json_array_elements` so callers can write SQL like
-- `SELECT * FROM goldenmatch.corrections WHERE dataset = 'customers'`.
-- View access uses the default GRANT chain; no special role required.
CREATE OR REPLACE VIEW goldenmatch.corrections AS
SELECT
    (entry->>'id')::TEXT              AS id,
    (entry->>'id_a')::BIGINT          AS id_a,
    (entry->>'id_b')::BIGINT          AS id_b,
    (entry->>'decision')::TEXT        AS decision,
    (entry->>'source')::TEXT          AS source,
    (entry->>'trust')::DOUBLE PRECISION AS trust,
    (entry->>'reason')::TEXT          AS reason,
    (entry->>'dataset')::TEXT         AS dataset,
    (entry->>'matchkey_name')::TEXT   AS matchkey_name,
    (entry->>'field_name')::TEXT      AS field_name,
    (entry->>'original_value')::TEXT  AS original_value,
    (entry->>'corrected_value')::TEXT AS corrected_value,
    (entry->>'cluster_score')::DOUBLE PRECISION AS cluster_score,
    (entry->>'cluster_outcome')::TEXT AS cluster_outcome,
    (entry->>'created_at')::TIMESTAMPTZ AS created_at
FROM jsonb_array_elements(
    goldenmatch.correction_list(NULL, NULL)::jsonb
) AS entry;

-- ─── Permissions ──────────────────────────────────────────────────────
--
-- `correction_add` writes into the Learning Memory store; default
-- REVOKE from PUBLIC so accidental SELECT-from-anywhere can't file
-- correctness signals. Grant `goldenmatch_correction_writer` to any
-- role that needs to file corrections.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'goldenmatch_correction_writer') THEN
        CREATE ROLE goldenmatch_correction_writer NOLOGIN;
    END IF;
END
$$;

REVOKE EXECUTE ON FUNCTION goldenmatch.correction_add(
    TEXT, TEXT, BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT,
    DOUBLE PRECISION, TEXT, TEXT, TEXT, TEXT, TEXT
) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION goldenmatch.correction_add(
    TEXT, TEXT, BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT,
    DOUBLE PRECISION, TEXT, TEXT, TEXT, TEXT, TEXT
) TO goldenmatch_correction_writer;

REVOKE EXECUTE ON FUNCTION goldenmatch.correction_list(TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION goldenmatch.correction_list(TEXT, TEXT) TO goldenmatch_correction_writer;

-- memory_learn mutates the store (writes learned adjustments); gate it like
-- correction_add. memory_stats is read-only status (counts + thresholds, no
-- correction contents) and stays available to PUBLIC.
REVOKE EXECUTE ON FUNCTION goldenmatch.memory_learn(TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION goldenmatch.memory_learn(TEXT, TEXT) TO goldenmatch_correction_writer;
GRANT SELECT ON goldenmatch.corrections TO goldenmatch_correction_writer;

-- ─── GoldenFlow transforms (src/goldenflow.rs) ────────────────────────────
--
-- 8 scalar text -> text wrappers over goldenflow's transform registry,
-- mirroring the DuckDB goldenflow_* UDFs (goldenmatch_duckdb/goldenflow.py).
-- Closes the last DuckDB <-> Postgres parity gap. All STRICT (NULL in -> NULL
-- out); the bridge fails open (passes the input through unchanged when
-- goldenflow isn't installed / the transform errors), so these are read-only
-- and safe for PUBLIC -- no REVOKE.

-- Normalize an email address. Wraps goldenflow `email_normalize`.
CREATE FUNCTION "goldenflow_normalize_email"(
    "value" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenflow_normalize_email_wrapper';

-- Normalize a phone number to E.164. Wraps goldenflow `phone_e164`.
CREATE FUNCTION "goldenflow_normalize_phone"(
    "value" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenflow_normalize_phone_wrapper';

-- Normalize a date to ISO-8601. Wraps goldenflow `date_iso8601`.
CREATE FUNCTION "goldenflow_normalize_date"(
    "value" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenflow_normalize_date_wrapper';

-- Proper-case a personal name. Wraps goldenflow `name_proper`.
CREATE FUNCTION "goldenflow_normalize_name_proper"(
    "value" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenflow_normalize_name_proper_wrapper';

-- Canonicalize a URL. Wraps goldenflow `url_normalize`.
CREATE FUNCTION "goldenflow_canonicalize_url"(
    "value" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenflow_canonicalize_url_wrapper';

-- Standardize a postal address. Wraps goldenflow `address_standardize`.
CREATE FUNCTION "goldenflow_canonicalize_address"(
    "value" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenflow_canonicalize_address_wrapper';

-- Strip leading/trailing whitespace. Wraps goldenflow `strip`.
CREATE FUNCTION "goldenflow_strip"(
    "value" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenflow_strip_wrapper';

-- Collapse internal whitespace runs. Wraps goldenflow `collapse_whitespace`.
CREATE FUNCTION "goldenflow_whitespace_normalize"(
    "value" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenflow_whitespace_normalize_wrapper';

-- ── Native Core graph kernels (#509) ──────────────────────────────────────
-- Native-direct: these call the pyo3-free goldenmatch-graph-core crate in pure
-- Rust (NO embedded CPython). DuckDB<->Postgres lockstep with core_kernels.py.
-- Accept-both: int64 ids on the bare name, string ids on the _str sibling
-- (a first-seen str<->i64 dictionary wraps the i64 kernel).

-- Canonical max-score pairs over int64 id arrays. Returns (a, b, s) rows.
CREATE FUNCTION "goldenmatch_pair_dedup"(
    "id_a" BIGINT[],
    "id_b" BIGINT[],
    "score" DOUBLE PRECISION[]
) RETURNS TABLE ("a" BIGINT, "b" BIGINT, "s" DOUBLE PRECISION)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_pair_dedup_wrapper';

-- String-id variant: same kernel via a first-seen str<->i64 dictionary.
CREATE FUNCTION "goldenmatch_pair_dedup_str"(
    "id_a" TEXT[],
    "id_b" TEXT[],
    "score" DOUBLE PRECISION[]
) RETURNS TABLE ("a" TEXT, "b" TEXT, "s" DOUBLE PRECISION)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_pair_dedup_str_wrapper';

-- Connected components over int64 edge arrays + an int64 id universe.
-- Returns (component_index, member) rows.
CREATE FUNCTION "goldenmatch_connected_components"(
    "id_a" BIGINT[],
    "id_b" BIGINT[],
    "score" DOUBLE PRECISION[],
    "all_ids" BIGINT[]
) RETURNS TABLE ("component" BIGINT, "member" BIGINT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_connected_components_wrapper';

-- String-id variant of connected components.
CREATE FUNCTION "goldenmatch_connected_components_str"(
    "id_a" TEXT[],
    "id_b" TEXT[],
    "score" DOUBLE PRECISION[],
    "all_ids" TEXT[]
) RETURNS TABLE ("component" BIGINT, "member" TEXT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_connected_components_str_wrapper';

-- Embed one text with a local in-house model (a saved GoldenEmbedModel dir)
-- via goldenembed-rs (pure Rust, no embedded CPython). Returns the vector as
-- float8[].
CREATE FUNCTION "goldenmatch_embed_local"(
    "text" TEXT,
    "model_path" TEXT
) RETURNS double precision[]
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_embed_local_wrapper';

-- Canonical record fingerprint (64 hex) of a JSON record object. The
-- cross-surface stable record-id hash; matches the DuckDB
-- goldenmatch_record_fingerprint UDF + the Python identity path.
CREATE FUNCTION "goldenmatch_record_fingerprint"(
    "record_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_record_fingerprint_wrapper';
