-- goldenmatch_pg SQL extension schema
-- Generated manually to work around pgrx 0.12.9 SQL generation bug

CREATE FUNCTION "goldenmatch_score"(
    "value_a" TEXT,
    "value_b" TEXT,
    "scorer" TEXT DEFAULT 'jaro_winkler'
) RETURNS DOUBLE PRECISION
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_score_wrapper';

CREATE FUNCTION "goldenmatch_score_pair"(
    "record_a" TEXT,
    "record_b" TEXT,
    "config" TEXT
) RETURNS DOUBLE PRECISION
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_score_pair_wrapper';

CREATE FUNCTION "goldenmatch_explain"(
    "record_a" TEXT,
    "record_b" TEXT,
    "config" TEXT
) RETURNS TEXT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_explain_wrapper';

CREATE FUNCTION "goldenmatch_dedupe"(
    "rows_json" TEXT,
    "config_json" TEXT
) RETURNS TEXT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_dedupe_wrapper';

CREATE FUNCTION "goldenmatch_match"(
    "target_json" TEXT,
    "reference_json" TEXT,
    "config_json" TEXT
) RETURNS TEXT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_match_wrapper';
