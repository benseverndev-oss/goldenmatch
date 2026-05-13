-- GoldenMatch Identity Graph -- schema v1
-- Apply via: psql -d <db> -f identity_v1.sql
-- Idempotent: every CREATE statement uses IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS identity_nodes (
    entity_id      TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'active',
    merged_into    TEXT,
    golden_record  JSONB,
    confidence     DOUBLE PRECISION,
    dataset        TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_identity_nodes_dataset ON identity_nodes(dataset);
CREATE INDEX IF NOT EXISTS idx_identity_nodes_status  ON identity_nodes(status);

CREATE TABLE IF NOT EXISTS source_records (
    record_id      TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    source_pk      TEXT NOT NULL,
    record_hash    TEXT NOT NULL,
    entity_id      TEXT REFERENCES identity_nodes(entity_id) ON DELETE SET NULL,
    payload        JSONB,
    dataset        TEXT,
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_source_records_entity ON source_records(entity_id);
CREATE INDEX IF NOT EXISTS idx_source_records_source ON source_records(source);
CREATE INDEX IF NOT EXISTS idx_source_records_hash   ON source_records(record_hash);

CREATE TABLE IF NOT EXISTS evidence_edges (
    edge_id              BIGSERIAL PRIMARY KEY,
    entity_id            TEXT NOT NULL,
    record_a_id          TEXT NOT NULL,
    record_b_id          TEXT NOT NULL,
    kind                 TEXT NOT NULL DEFAULT 'same_as',
    score                DOUBLE PRECISION,
    matchkey_name        TEXT,
    field_scores         JSONB,
    negative_evidence    JSONB,
    controller_snapshot  JSONB,
    run_name             TEXT,
    dataset              TEXT,
    recorded_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(entity_id, record_a_id, record_b_id, run_name)
);
CREATE INDEX IF NOT EXISTS idx_edges_entity ON evidence_edges(entity_id);
CREATE INDEX IF NOT EXISTS idx_edges_pair   ON evidence_edges(record_a_id, record_b_id);
CREATE INDEX IF NOT EXISTS idx_edges_run    ON evidence_edges(run_name);

CREATE TABLE IF NOT EXISTS identity_events (
    event_id     BIGSERIAL PRIMARY KEY,
    entity_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    payload      JSONB,
    run_name     TEXT,
    dataset      TEXT,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_entity ON identity_events(entity_id);
CREATE INDEX IF NOT EXISTS idx_events_kind   ON identity_events(kind);
CREATE INDEX IF NOT EXISTS idx_events_run    ON identity_events(run_name);

CREATE TABLE IF NOT EXISTS identity_aliases (
    alias        TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'external_id',
    dataset      TEXT,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (alias, kind, dataset)
);
CREATE INDEX IF NOT EXISTS idx_aliases_entity ON identity_aliases(entity_id);

-- ── Analytical views ────────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_identities AS
SELECT
    n.entity_id,
    n.status,
    n.merged_into,
    n.confidence,
    n.dataset,
    n.created_at,
    n.updated_at,
    COUNT(DISTINCT sr.record_id)              AS record_count,
    COUNT(DISTINCT sr.source)                 AS source_count,
    COUNT(DISTINCT ee.edge_id)                AS edge_count,
    COUNT(DISTINCT ev.event_id)               AS event_count
FROM identity_nodes n
LEFT JOIN source_records  sr ON sr.entity_id = n.entity_id
LEFT JOIN evidence_edges  ee ON ee.entity_id = n.entity_id
LEFT JOIN identity_events ev ON ev.entity_id = n.entity_id
GROUP BY n.entity_id, n.status, n.merged_into, n.confidence, n.dataset,
         n.created_at, n.updated_at;

CREATE OR REPLACE VIEW v_identity_pairs AS
SELECT
    ee.entity_id,
    ee.record_a_id,
    ee.record_b_id,
    ee.score,
    ee.kind,
    ee.matchkey_name,
    ee.run_name,
    ee.dataset,
    ee.recorded_at,
    sra.source        AS source_a,
    srb.source        AS source_b,
    sra.source_pk     AS source_pk_a,
    srb.source_pk     AS source_pk_b
FROM evidence_edges ee
LEFT JOIN source_records sra ON sra.record_id = ee.record_a_id
LEFT JOIN source_records srb ON srb.record_id = ee.record_b_id;

CREATE OR REPLACE VIEW v_identity_timeline AS
SELECT
    ev.event_id,
    ev.entity_id,
    n.dataset,
    ev.kind,
    ev.run_name,
    ev.payload,
    ev.recorded_at,
    n.status         AS current_status,
    n.merged_into    AS current_merged_into
FROM identity_events ev
LEFT JOIN identity_nodes n ON n.entity_id = ev.entity_id
ORDER BY ev.event_id DESC;
