"""Add rate_limit column to api_keys table

Revision ID: 006_add_api_key_rate_limit
Revises: 005_rcssms_template_columns
Create Date: 2026-03-02 12:44:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '006_add_api_key_rate_limit'
down_revision = '005_rcssms_template_columns'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('api_keys', sa.Column('rate_limit', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('api_keys', 'rate_limit')
