"""Add parent_message_id for fallback message linking

Revision ID: 007_parent_message_id
Revises: 006_add_api_key_rate_limit
Create Date: 2026-03-03

This migration adds parent-child linkage for SMS fallback messages.
When RCS fails, a NEW SMS message is created with parent_message_id pointing
to the original RCS message. This eliminates the broken FAILED → PENDING
state transition that caused stuck messages.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '007_parent_message_id'
down_revision = '006_add_api_key_rate_limit'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add parent_message_id column
    op.add_column(
        'messages',
        sa.Column(
            'parent_message_id',
            postgresql.UUID(as_uuid=True),
            nullable=True
        )
    )
    
    # Add foreign key constraint (self-referential)
    op.create_foreign_key(
        'fk_messages_parent_message_id',
        'messages',
        'messages',
        ['parent_message_id'],
        ['id'],
        ondelete='SET NULL'
    )
    
    # Add index for fast lookup of child messages
    op.create_index(
        'ix_messages_parent_message_id',
        'messages',
        ['parent_message_id']
    )
    
    # Add composite index for campaign completion queries
    # This allows fast lookup: "get all parent messages for campaign"
    op.create_index(
        'ix_messages_campaign_status_parent',
        'messages',
        ['campaign_id', 'status', 'parent_message_id']
    )


def downgrade() -> None:
    op.drop_index('ix_messages_campaign_status_parent', table_name='messages')
    op.drop_index('ix_messages_parent_message_id', table_name='messages')
    op.drop_constraint('fk_messages_parent_message_id', 'messages', type_='foreignkey')
    op.drop_column('messages', 'parent_message_id')
