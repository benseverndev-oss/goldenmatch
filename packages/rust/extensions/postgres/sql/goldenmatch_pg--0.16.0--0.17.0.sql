-- Upgrade goldenmatch_pg 0.16.0 -> 0.17.0
--
-- Adds the structural key-integrity certifier (the semantic-layer wedge) to the
-- SQL surface. goldenmatch_certify_structural(input_json) delegates native-direct
-- to the pyo3/pgrx-free goldenmatch-key-integrity-core crate -- the SAME reference
-- kernel the goldenmatch[native] wheel (semantic.certify_structural_json) + the
-- DuckDB goldenmatch_certify_structural UDF run -- so the certificate is
-- byte-identical across Python / TS / DuckDB / Postgres.
--
-- JSON in / JSON out (the core's native contract; measures make a columnar
-- signature unwieldy):
--   in : {"n_rows": N, "group_columns": [[..], ..],
--         "measures": [{"name": .., "values": [..]}, ..]}
--   out: {"n_rows", "n_key_groups", "duplicate_key_groups", "max_fan_out",
--         "is_unique_at_grain", "measure_fan_out": {name: ratio}}
-- Non-STRICT + fail-soft: invalid input returns a {"error": ..} envelope.

CREATE FUNCTION "goldenmatch_certify_structural"(
    "input_json" TEXT
) RETURNS TEXT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_certify_structural_wrapper';
