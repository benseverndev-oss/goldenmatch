-- Upgrade goldenmatch_pg 0.15.0 -> 0.16.0
--
-- Extends the in-SQL identity surface (post-#1913) with the audit chain,
-- steward mediation, and MDM operator reads that were already on the Python /
-- MCP / REST surfaces but not in SQL:
--
--   Reads (db_path = SQLite path or libpq DSN; empty = the in-DB dataset):
--     gm_identity_audit(dataset, db_path)        -- append-only audit-log page
--     gm_identity_audit_verify(dataset, db_path) -- seal-chain integrity verdict
--     gm_identity_profile(entity_id, db_path)    -- one entity's MDM profile
--     gm_identity_stats(dataset, db_path)        -- graph-level health summary
--     gm_identity_worklist(dataset, db_path)     -- prioritized steward queue
--
--   Writes (DSN-resolved via goldenmatch.identity_dsn, like gm_identity_merge):
--     gm_identity_audit_seal(dataset)            -- anchor the audit log
--     gm_identity_resolve_conflict(dataset, record_a, record_b, resolution,
--                                  reason)       -- steward conflict verdict
--     gm_identity_claim(entity_id, record_id, reason) -- attach record to entity
--
-- All delegate to goldenmatch.identity through the bridge; serialization is
-- single-sourced with the MCP tool layer, so output is byte-identical across
-- surfaces. Empty optional args (dataset / reason) mean "unset".

CREATE FUNCTION "gm_identity_audit"(
    "dataset" TEXT,
    "db_path" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_identity_audit_wrapper';

CREATE FUNCTION "gm_identity_audit_verify"(
    "dataset" TEXT,
    "db_path" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_identity_audit_verify_wrapper';

CREATE FUNCTION "gm_identity_profile"(
    "entity_id" TEXT,
    "db_path" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_identity_profile_wrapper';

CREATE FUNCTION "gm_identity_stats"(
    "dataset" TEXT,
    "db_path" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_identity_stats_wrapper';

CREATE FUNCTION "gm_identity_worklist"(
    "dataset" TEXT,
    "db_path" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_identity_worklist_wrapper';

CREATE FUNCTION "gm_identity_audit_seal"(
    "dataset" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_identity_audit_seal_wrapper';

CREATE FUNCTION "gm_identity_resolve_conflict"(
    "dataset" TEXT,
    "record_a" TEXT,
    "record_b" TEXT,
    "resolution" TEXT,
    "reason" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_identity_resolve_conflict_wrapper';

CREATE FUNCTION "gm_identity_claim"(
    "entity_id" TEXT,
    "record_id" TEXT,
    "reason" TEXT
) RETURNS TEXT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'gm_identity_claim_wrapper';
