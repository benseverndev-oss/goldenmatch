"""Persisted blocking index for incremental resolution (C2).

Adds ``identity_record_block_keys`` -- one row per (record, blocking pass) with
the block key that record fell in, so incremental resolution can find candidate
persisted records that share a block key WITHOUT re-blocking the whole corpus in
RAM (control-plane manifesto §4(ii) / decision 0047 §9.1). Mirrors the runtime
``_pg_init_schema`` DDL in goldenmatch/identity/store.py, so this rev and the
store's on-open DDL converge to the same shape. Additive.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS identity_record_block_keys (
            record_id  TEXT NOT NULL,
            entity_id  TEXT,
            block_key  TEXT NOT NULL,
            pass_sig   TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (record_id, pass_sig, block_key)
        );
        CREATE INDEX IF NOT EXISTS idx_rbk_block  ON identity_record_block_keys(pass_sig, block_key);
        CREATE INDEX IF NOT EXISTS idx_rbk_entity ON identity_record_block_keys(entity_id);
        CREATE INDEX IF NOT EXISTS idx_rbk_record ON identity_record_block_keys(record_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS identity_record_block_keys;")
