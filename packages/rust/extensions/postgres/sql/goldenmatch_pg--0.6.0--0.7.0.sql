-- goldenmatch_pg 0.6.0 -> 0.7.0 upgrade.
--
-- Adds gm_embed(text) -> real[] (#737): a 1-arg convenience over the in-house
-- embedder, model dir from GOLDENEMBED_MODEL_DIR, float4 output for parity with
-- the DataFusion goldenmatch_embed UDF. NULL -> "" (so NOT STRICT).
CREATE FUNCTION "gm_embed"(
    "text" TEXT  /* nullable: NULL -> "" */
) RETURNS real[]
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_embed_wrapper';
