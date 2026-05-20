"""Identity Graph schema v1 baseline.

Mirrors goldenmatch/db/migrations/identity_v1.sql plus the v2 widening of
the evidence_edges UNIQUE constraint to include ``kind`` (matches the
current ``_pg_init_schema`` behavior in goldenmatch/identity/store.py).

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


_SCHEMA = """
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
    UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)
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
"""

_DROP = """
DROP TABLE IF EXISTS identity_aliases;
DROP TABLE IF EXISTS identity_events;
DROP TABLE IF EXISTS evidence_edges;
DROP TABLE IF EXISTS source_records;
DROP TABLE IF EXISTS identity_nodes;
"""


def upgrade() -> None:
    op.execute(_SCHEMA)


def downgrade() -> None:
    op.execute(_DROP)
