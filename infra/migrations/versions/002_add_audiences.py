"""add audiences table

Revision ID: 002_add_audiences
Revises: 001_initial_schema
Create Date: 2024-01-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers, used by Alembic.
revision = '002_add_audiences'
down_revision = '001_initial_schema'
branch_labels = None
depends_on = None


def upgrade():
    """Create audiences table"""
    
    op.create_table(
        'audiences',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('audience_type', sa.String(50), nullable=False, server_default='static'),
        sa.Column('status', sa.String(50), nullable=False, server_default='draft'),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', JSONB, nullable=True),
        sa.Column('query', JSONB, nullable=True),
        sa.Column('contacts', JSONB, nullable=True),
        sa.Column('total_contacts', sa.Integer(), server_default='0'),
        sa.Column('valid_contacts', sa.Integer(), server_default='0'),
        sa.Column('invalid_contacts', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
    )
    
    # Create indexes
    op.create_index(
        'idx_audiences_tenant_status',
        'audiences',
        ['tenant_id', 'status'],
    )
    
    op.create_index(
        'idx_audiences_tenant_type',
        'audiences',
        ['tenant_id', 'audience_type'],
    )
    
    op.create_index(
        'idx_audiences_created_at',
        'audiences',
        ['created_at'],
    )


def downgrade():
    """Drop audiences table"""
    
    op.drop_index('idx_audiences_created_at', table_name='audiences')
    op.drop_index('idx_audiences_tenant_type', table_name='audiences')
    op.drop_index('idx_audiences_tenant_status', table_name='audiences')
    op.drop_table('audiences')
