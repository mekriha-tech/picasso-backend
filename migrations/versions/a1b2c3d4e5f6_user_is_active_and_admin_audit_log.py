"""users.is_active and admin_audit_log

Revision ID: a1b2c3d4e5f6
Revises: d4f6b8a0c2e3
Create Date: 2026-11-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd4f6b8a0c2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )
    op.create_table(
        'admin_audit_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('actor_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('target_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('actor_email', sa.Text(), nullable=False),
        sa.Column('target_email', sa.Text(), nullable=False),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('admin_audit_log_target_idx', 'admin_audit_log', ['target_user_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('admin_audit_log_target_idx', table_name='admin_audit_log')
    op.drop_table('admin_audit_log')
    op.drop_column('users', 'is_active')
