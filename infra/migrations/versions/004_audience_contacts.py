"""Migrate audience contacts from JSON blob to normalised table

This migration:
  1. Creates the audience_contacts table (id, audience_id FK, phone_number,
     variables JSONB, metadata_ JSONB, created_at).
  2. Backfills existing JSON contact data from audiences.contacts into the
     new table using PostgreSQL's json_to_recordset().
  3. Drops the audiences.contacts column.

The downgrade reverses all three steps so rollback is fully safe.

Revision ID: 004_audience_contacts
Revises:     003_add_api_keys
Create Date: 2026-03-01 17:19:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers
revision: str = "004_audience_contacts"
down_revision: Union[str, None] = "003_add_api_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create audience_contacts table
    # ------------------------------------------------------------------
    op.create_table(
        "audience_contacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "audience_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audiences.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone_number", sa.Text(), nullable=False),
        # Ordered list of template variable values — matches template.variables order
        sa.Column("variables", postgresql.JSONB(), nullable=True),
        # Arbitrary per-contact key/value metadata (name, email, order_id, …)
        sa.Column(
            "metadata_",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Fast lookup by audience — the primary access pattern
    op.create_index(
        "ix_audience_contacts_audience_id",
        "audience_contacts",
        ["audience_id"],
    )
    # Keyset pagination cursor — sequential by insertion order within an audience
    op.create_index(
        "ix_audience_contacts_audience_id_id",
        "audience_contacts",
        ["audience_id", "id"],
    )
    # Deduplication: one row per (audience, phone)
    op.create_unique_constraint(
        "uq_audience_contact",
        "audience_contacts",
        ["audience_id", "phone_number"],
    )

    # ------------------------------------------------------------------
    # 2. Backfill: unnest existing JSON blobs into rows
    #
    # audiences.contacts is a JSONB array of objects:
    #   [{"phone_number": "+91…", "variables": […], "metadata": {…}}, …]
    #
    # jsonb_to_recordset() in PostgreSQL turns a JSONB array of objects
    # into a virtual table — exactly what we need for a bulk INSERT.
    # ------------------------------------------------------------------
    op.execute(
        """
        INSERT INTO audience_contacts (audience_id, phone_number, variables, metadata_)
        SELECT
            a.id                                            AS audience_id,
            c.phone_number::text                           AS phone_number,
            CASE
                WHEN c.variables IS NOT NULL
                THEN c.variables::jsonb
                ELSE NULL
            END                                            AS variables,
            COALESCE(c.metadata::jsonb, '{}'::jsonb)      AS metadata_
        FROM
            audiences a,
            jsonb_to_recordset(a.contacts) AS c(
                phone_number text,
                variables    jsonb,
                metadata     jsonb
            )
        WHERE
            a.contacts IS NOT NULL
            AND jsonb_array_length(a.contacts) > 0
        ON CONFLICT (audience_id, phone_number) DO NOTHING
        """
    )

    # ------------------------------------------------------------------
    # 3. Drop the contacts JSON column — data is now in audience_contacts
    # ------------------------------------------------------------------
    op.drop_column("audiences", "contacts")


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Reverse: re-add the JSON column, backfill from rows, drop table
    # ------------------------------------------------------------------

    # 1. Restore the column
    op.add_column(
        "audiences",
        sa.Column("contacts", postgresql.JSONB(), nullable=True),
    )

    # 2. Aggregate rows back into JSON array per audience
    op.execute(
        """
        UPDATE audiences a
        SET contacts = sub.contacts_json
        FROM (
            SELECT
                audience_id,
                jsonb_agg(
                    jsonb_build_object(
                        'phone_number', phone_number,
                        'variables',    COALESCE(variables, '[]'::jsonb),
                        'metadata',     COALESCE(metadata_, '{}'::jsonb)
                    )
                    ORDER BY created_at
                ) AS contacts_json
            FROM audience_contacts
            GROUP BY audience_id
        ) sub
        WHERE a.id = sub.audience_id
        """
    )

    # 3. Drop the normalised table
    op.drop_constraint("uq_audience_contact", "audience_contacts", type_="unique")
    op.drop_index("ix_audience_contacts_audience_id_id", table_name="audience_contacts")
    op.drop_index("ix_audience_contacts_audience_id", table_name="audience_contacts")
    op.drop_table("audience_contacts")
