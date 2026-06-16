-- goldenmatch_pg 0.8.0 -> 0.9.0 upgrade.
--
-- Adds goldenmatch_match_pairs: two-table record linkage that returns the
-- (target_id, reference_id, score) matched pairs as rows. Backs the
-- goldenmatch_match dbt materialization. target_id / reference_id are 0-based
-- row indices into the respective input tables.
CREATE FUNCTION "goldenmatch_match_pairs"(
    "target_table" TEXT,
    "reference_table" TEXT,
    "config_json" TEXT
) RETURNS TABLE ("target_id" BIGINT, "reference_id" BIGINT, "score" DOUBLE PRECISION)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_match_pairs_wrapper';
