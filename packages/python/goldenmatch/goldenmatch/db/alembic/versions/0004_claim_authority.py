"""Claim-authority tier: claim_type + evidence_ref + previous_claim_id.

Adds three nullable columns to ``identity_events`` (#1256): ``claim_type`` (the
categorical authority tier -- observation / inference / verified / directive,
orthogonal to numeric ``trust``), ``evidence_ref`` (typed provenance of what
backs the claim), and ``previous_claim_id`` (chains a claim's promote/amend/
revoke lifecycle to the event it supersedes). Mirrors the runtime
``_pg_init_schema`` DDL in goldenmatch/identity/store.py, so this rev and the
store's on-open DDL converge to the same shape. Additive/nullable: pre-#1256
rows read back as NULL.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS claim_type TEXT;
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS evidence_ref TEXT;
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS previous_claim_id BIGINT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE identity_events DROP COLUMN IF EXISTS previous_claim_id;
        ALTER TABLE identity_events DROP COLUMN IF EXISTS evidence_ref;
        ALTER TABLE identity_events DROP COLUMN IF EXISTS claim_type;
        """
    )
