-- goldenmatch_pg 0.5.0 -> 0.6.0 upgrade.
--
-- #509: the graph kernels move from the CPython JSON-bridge (JSON in / JSON out)
-- to native-direct (pure-Rust goldenmatch-graph-core, no embedded CPython) with
-- columnar array I/O + accept-both int64/string ids. The two old TEXT-signature
-- functions are dropped and replaced by four array-signature, table-returning
-- functions. goldenmatch_embed_local also moves to native-direct (goldenembed-rs,
-- no CPython) and its return type changes TEXT -> float8[]. The record-fingerprint
-- function is unchanged in 0.6.0.

-- Drop the old JSON-bridge graph functions (TEXT in / TEXT out).
DROP FUNCTION IF EXISTS "goldenmatch_connected_components"(TEXT);
DROP FUNCTION IF EXISTS "goldenmatch_pair_dedup"(TEXT);

-- Native-direct replacements (mirror sql/goldenmatch_pg--0.6.0.sql).

CREATE FUNCTION "goldenmatch_pair_dedup"(
    "id_a" BIGINT[],
    "id_b" BIGINT[],
    "score" DOUBLE PRECISION[]
) RETURNS TABLE ("a" BIGINT, "b" BIGINT, "s" DOUBLE PRECISION)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_pair_dedup_wrapper';

CREATE FUNCTION "goldenmatch_pair_dedup_str"(
    "id_a" TEXT[],
    "id_b" TEXT[],
    "score" DOUBLE PRECISION[]
) RETURNS TABLE ("a" TEXT, "b" TEXT, "s" DOUBLE PRECISION)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_pair_dedup_str_wrapper';

CREATE FUNCTION "goldenmatch_connected_components"(
    "id_a" BIGINT[],
    "id_b" BIGINT[],
    "score" DOUBLE PRECISION[],
    "all_ids" BIGINT[]
) RETURNS TABLE ("component" BIGINT, "member" BIGINT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_connected_components_wrapper';

CREATE FUNCTION "goldenmatch_connected_components_str"(
    "id_a" TEXT[],
    "id_b" TEXT[],
    "score" DOUBLE PRECISION[],
    "all_ids" TEXT[]
) RETURNS TABLE ("component" BIGINT, "member" TEXT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_connected_components_str_wrapper';

-- goldenmatch_embed_local: native-direct (goldenembed-rs, no CPython); return
-- type changes TEXT -> float8[], so drop the old signature and recreate.
DROP FUNCTION IF EXISTS "goldenmatch_embed_local"(TEXT, TEXT);

CREATE FUNCTION "goldenmatch_embed_local"(
    "text" TEXT,
    "model_path" TEXT
) RETURNS double precision[]
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_embed_local_wrapper';
