-- goldenmatch_pg SQL extension schema v0.1.0

-- ── Table-based functions (primary interface) ──

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

-- ── Scalar functions ──

CREATE FUNCTION "goldenmatch_score"(
    "value_a" TEXT,
    "value_b" TEXT,
    "scorer" TEXT DEFAULT 'jaro_winkler'
) RETURNS DOUBLE PRECISION
STRICT PARALLEL SAFE
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

-- ── JSON-based functions (programmatic use) ──

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
