"""Initial schema — all tables including rcssms template columns

Creates all tables for the RCS platform in a single migration.
Includes external_template_id and rcs_type on the templates table
(previously split across two conflicting 001_* migrations with the same
down_revision = None, which caused Alembic to error on upgrade).

Revision ID: 001_initial_schema
Revises:
Create Date: 2024-01-15 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables."""

    # ------------------------------------------------------------------ #
    # campaigns
    # ------------------------------------------------------------------ #
    op.create_table(
        "campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, index=True),
        sa.Column("campaign_type", sa.String(50), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("enable_fallback", sa.Boolean, default=True),
        sa.Column("fallback_channel", sa.String(20), default="sms"),
        sa.Column("rate_limit", sa.Integer, nullable=True),
        sa.Column("recipient_count", sa.Integer, default=0),
        sa.Column("messages_sent", sa.Integer, default=0),
        sa.Column("messages_delivered", sa.Integer, default=0),
        sa.Column("messages_failed", sa.Integer, default=0),
        sa.Column("messages_read", sa.Integer, default=0),
        sa.Column("fallback_triggered", sa.Integer, default=0),
        sa.Column("opt_outs", sa.Integer, default=0),
        sa.Column("metadata_", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("tags", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_campaigns_tenant_status", "campaigns", ["tenant_id", "status"])
    op.create_index("ix_campaigns_scheduled", "campaigns", ["scheduled_for"])

    # ------------------------------------------------------------------ #
    # messages
    # ------------------------------------------------------------------ #
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("recipient_phone", sa.String(20), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, index=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("priority", sa.String(20), default="medium"),
        sa.Column("content", postgresql.JSON, nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retry_count", sa.Integer, default=0),
        sa.Column("max_retries", sa.Integer, default=3),
        sa.Column("fallback_enabled", sa.Boolean, default=True),
        sa.Column("fallback_triggered", sa.Boolean, default=False),
        sa.Column("aggregator", sa.String(50), nullable=True),
        sa.Column("external_id", sa.String(255), nullable=True, index=True),
        sa.Column("failure_reason", sa.String(50), nullable=True),
        sa.Column("metadata_", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_messages_status_created", "messages", ["status", "created_at"])
    op.create_index("ix_messages_tenant_status", "messages", ["tenant_id", "status"])

    # ------------------------------------------------------------------ #
    # templates  — includes external_template_id and rcs_type from the start
    # ------------------------------------------------------------------ #
    op.create_table(
        "templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, default="draft"),
        sa.Column("content", sa.Text, nullable=False),
        # rcssms.in approved template ID — required before any send
        sa.Column("external_template_id", sa.String(100), nullable=True),
        # Template type: BASIC, RICH, RICHCASOUREL
        sa.Column("rcs_type", sa.String(20), nullable=False, server_default="BASIC"),
        sa.Column("variables", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("rich_card_template", postgresql.JSON, nullable=True),
        sa.Column(
            "suggestions_template", postgresql.JSON, nullable=False, server_default="[]"
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("tags", postgresql.JSON, nullable=False, server_default="[]"),
        sa.Column("language", sa.String(10), default="en"),
        sa.Column("usage_count", sa.Integer, default=0),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_templates_tenant_status", "templates", ["tenant_id", "status"])
    op.create_index("ix_templates_external_id", "templates", ["external_template_id"])

    # ------------------------------------------------------------------ #
    # opt_ins
    # ------------------------------------------------------------------ #
    op.create_table(
        "opt_ins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("phone_number", sa.String(20), nullable=False),
        sa.Column(
            "promotional_status", sa.String(50), nullable=False, default="opted_out"
        ),
        sa.Column(
            "transactional_status", sa.String(50), nullable=False, default="opted_in"
        ),
        sa.Column(
            "informational_status", sa.String(50), nullable=False, default="opted_out"
        ),
        sa.Column("promotional_opted_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promotional_opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_on_dnd_registry", sa.Boolean, default=False),
        sa.Column("dnd_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consent_history", postgresql.JSON, nullable=False, server_default="[]"
        ),
        sa.Column("preferences", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("metadata_", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_opt_ins_tenant_phone", "opt_ins", ["tenant_id", "phone_number"], unique=True
    )

    # ------------------------------------------------------------------ #
    # events
    # ------------------------------------------------------------------ #
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False, index=True),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("aggregate_type", sa.String(50), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, default=1),
        sa.Column("data", postgresql.JSON, nullable=False),
        sa.Column("metadata_", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_events_aggregate", "events", ["aggregate_id", "version"])
    op.create_index("ix_events_type_created", "events", ["event_type", "created_at"])

    # ------------------------------------------------------------------ #
    # audiences
    # ------------------------------------------------------------------ #
    op.create_table(
        "audiences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("audience_type", sa.String(50), nullable=False, default="static"),
        sa.Column("status", sa.String(50), nullable=False, default="draft"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("tags", postgresql.JSON, nullable=True),
        sa.Column("query", postgresql.JSON, nullable=True),
        sa.Column("contacts", postgresql.JSON, nullable=True),
        sa.Column("total_contacts", sa.Integer, default=0),
        sa.Column("valid_contacts", sa.Integer, default=0),
        sa.Column("invalid_contacts", sa.Integer, default=0),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_audiences_tenant_status", "audiences", ["tenant_id", "status"]
    )
    op.create_index(
        "idx_audiences_tenant_type", "audiences", ["tenant_id", "audience_type"]
    )


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    op.drop_table("audiences")
    op.drop_table("events")
    op.drop_table("opt_ins")
    op.drop_table("templates")
    op.drop_table("messages")
    op.drop_table("campaigns")
