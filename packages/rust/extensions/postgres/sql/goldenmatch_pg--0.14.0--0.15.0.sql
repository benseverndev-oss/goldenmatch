-- Upgrade goldenmatch_pg 0.14.0 -> 0.15.0
--
-- Adds the in-SQL steward corrections path (#1913 P3):
--   gm_identity_merge(dataset, entity_a, entity_b) -- manual merge (keep a,
--     absorb b), emits a manual_merge event on both.
--   gm_identity_split(dataset, entity_id, record_id) -- move a record into a
--     fresh identity, emits a manual_split event on both.
-- Both delegate to the Python steward path (manual_merge / manual_split) over
-- the goldenmatch.identity_dsn store, completing the write surface P1/P2 began.

CREATE FUNCTION "gm_identity_merge"(
    "dataset" TEXT,
    "entity_a" TEXT,
    "entity_b" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_identity_merge_wrapper';

CREATE FUNCTION "gm_identity_split"(
    "dataset" TEXT,
    "entity_id" TEXT,
    "record_id" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_identity_split_wrapper';
