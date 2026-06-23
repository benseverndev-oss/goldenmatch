"""Tamper-evident audit log: per-event entry_hash + audit_seals chain table.

Adds the nullable ``entry_hash`` column to ``identity_events`` and creates the
``audit_seals`` seal-chain table (#1078). Mirrors the runtime
``_pg_init_schema`` DDL in goldenmatch/identity/store.py, so this rev and the
store's on-open DDL converge to the same shape.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS entry_hash TEXT;
        CREATE TABLE IF NOT EXISTS audit_seals (
            seal_id BIGSERIAL PRIMARY KEY,
            dataset TEXT,
            root_hash TEXT NOT NULL,
            event_count BIGINT NOT NULL,
            last_event_id BIGINT,
            prev_seal_id BIGINT,
            prev_root TEXT,
            actor TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_audit_seals_dataset ON audit_seals(dataset);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS audit_seals;
        ALTER TABLE identity_events DROP COLUMN IF EXISTS entry_hash;
        """
    )
