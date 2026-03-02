"""Add external_template_id and rcs_type to templates table

This migration is intentionally a no-op on fresh installs because
001_initial_schema already includes these columns.

It is kept ONLY for databases that were created from the OLD initial schema
(before 2026-02-28) and already have the templates table without these columns.

If you are doing a fresh install, this migration will run and safely skip
the column additions (they already exist). On old databases it adds the
missing columns.

Revision ID: 001_rcssms_template_columns
Revises:     001_initial_schema
Create Date: 2026-02-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers
revision = "001_rcssms_template_columns"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    """Check if a column already exists (safe for re-entrant runs)."""
    bind = op.get_bind()
    inspector = inspect(bind)
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _column_exists("templates", "external_template_id"):
        op.add_column(
            "templates",
            sa.Column("external_template_id", sa.String(100), nullable=True),
        )
        op.create_index(
            "ix_templates_external_id",
            "templates",
            ["external_template_id"],
        )

    if not _column_exists("templates", "rcs_type"):
        op.add_column(
            "templates",
            sa.Column(
                "rcs_type", sa.String(20), nullable=False, server_default="BASIC"
            ),
        )


def downgrade() -> None:
    if _column_exists("templates", "external_template_id"):
        op.drop_index("ix_templates_external_id", table_name="templates")
        op.drop_column("templates", "external_template_id")

    if _column_exists("templates", "rcs_type"):
        op.drop_column("templates", "rcs_type")
