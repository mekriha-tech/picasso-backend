"""create_testimonials

Revision ID: b7d3e1f9a2c6
Revises: a4c6f2b8d5e9
Create Date: 2026-11-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b7d3e1f9a2c6'
down_revision: Union[str, Sequence[str], None] = 'a4c6f2b8d5e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PRD 3.8. No enums, so no create_type concerns here.
    op.create_table(
        'testimonials',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('author_name', sa.Text(), nullable=False),
        sa.Column('author_role', sa.Text(), nullable=True),
        sa.Column('avatar_url', sa.Text(), nullable=True),
        sa.Column('quote', sa.Text(), nullable=False),
        sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
        sa.Column('is_published', sa.Boolean(), server_default='true', nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('testimonials')
