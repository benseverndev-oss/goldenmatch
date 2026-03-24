-- goldenmatch_pg SQL extension schema v0.1.0
-- Note: pgrx generates C wrapper functions with _wrapper suffix

CREATE OR REPLACE FUNCTION "goldenmatch_score"(
    "value_a" TEXT,
    "value_b" TEXT,
    "scorer" TEXT DEFAULT 'jaro_winkler'
) RETURNS DOUBLE PRECISION
STRICT PARALLEL SAFE
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_score_wrapper';

CREATE OR REPLACE FUNCTION "goldenmatch_score_pair"(
    "record_a" TEXT,
    "record_b" TEXT,
    "config" TEXT
) RETURNS DOUBLE PRECISION
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_score_pair_wrapper';

CREATE OR REPLACE FUNCTION "goldenmatch_explain"(
    "record_a" TEXT,
    "record_b" TEXT,
    "config" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_explain_wrapper';

CREATE OR REPLACE FUNCTION "goldenmatch_dedupe"(
    "rows_json" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_dedupe_wrapper';

CREATE OR REPLACE FUNCTION "goldenmatch_match"(
    "target_json" TEXT,
    "reference_json" TEXT,
    "config_json" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_match_wrapper';
