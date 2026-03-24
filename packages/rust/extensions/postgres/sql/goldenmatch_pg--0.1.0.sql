-- goldenmatch_pg SQL extension schema v0.1.0

-- ══════════════════════════════════════════════════════════════════════
-- Pipeline tables (job management)
-- ══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS goldenmatch._jobs (
    name TEXT PRIMARY KEY,
    config_json JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    last_run_at TIMESTAMPTZ,
    status TEXT DEFAULT 'configured'
);

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
