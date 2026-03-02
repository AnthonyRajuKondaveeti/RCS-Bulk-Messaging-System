"""Add api_keys table

Creates the api_keys table used by AuthMiddleware to validate API-key
credentials.  Raw keys are never stored — only their SHA-256 hash.

Schema:
    api_keys (
        id          UUID         PK
        key_hash    TEXT         NOT NULL UNIQUE  -- SHA-256(raw_key)
        user_id     UUID         NOT NULL
        tenant_id   UUID         NOT NULL
        is_active   BOOLEAN      NOT NULL DEFAULT TRUE
        expires_at  TIMESTAMPTZ  NULL
        created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
    )

Revision ID: 003_add_api_keys
Revises:     002_add_audiences
Create Date: 2026-02-28 21:44:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers
revision: str = "003_add_api_keys"
down_revision: Union[str, None] = "002_add_audiences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create api_keys table."""
    op.create_table(
        "api_keys",
        # Primary key
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Hashed key — the only representation of the raw key stored in the DB
        sa.Column("key_hash", sa.Text(), nullable=False),
        # Owning user
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Tenant scope — auth middleware extracts this for multi-tenancy
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Lifecycle flags
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # Audit
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Unique constraint on key_hash — prevents duplicate keys and makes lookups O(1)
    op.create_index(
        "ix_api_keys_key_hash",
        "api_keys",
        ["key_hash"],
        unique=True,
    )

    # Index on tenant_id — supports tenant-scoped key listing
    op.create_index(
        "ix_api_keys_tenant_id",
        "api_keys",
        ["tenant_id"],
    )


def downgrade() -> None:
    """Drop api_keys table."""
    op.drop_index("ix_api_keys_tenant_id", table_name="api_keys")
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")
