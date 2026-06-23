"""Provenance spine: actor + trust on identity events and evidence edges.

Adds nullable ``actor`` / ``trust`` columns to ``identity_events`` and
``evidence_edges`` (#1075 / #1078). Mirrors the runtime ``_pg_init_schema``
``ADD COLUMN IF NOT EXISTS`` in goldenmatch/identity/store.py, so this rev and
the store's on-open DDL converge to the same shape.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence_edges  ADD COLUMN IF NOT EXISTS actor TEXT;
        ALTER TABLE evidence_edges  ADD COLUMN IF NOT EXISTS trust DOUBLE PRECISION;
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS actor TEXT;
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS trust DOUBLE PRECISION;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence_edges  DROP COLUMN IF EXISTS actor;
        ALTER TABLE evidence_edges  DROP COLUMN IF EXISTS trust;
        ALTER TABLE identity_events DROP COLUMN IF EXISTS actor;
        ALTER TABLE identity_events DROP COLUMN IF EXISTS trust;
        """
    )
