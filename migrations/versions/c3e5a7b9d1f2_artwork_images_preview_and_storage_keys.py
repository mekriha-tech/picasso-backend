"""artwork_images preview_url and storage keys

Revision ID: c3e5a7b9d1f2
Revises: b7d3e1f9a2c6
Create Date: 2026-11-16 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3e5a7b9d1f2'
down_revision: Union[str, Sequence[str], None] = 'b7d3e1f9a2c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All nullable: existing rows (seeded external URLs) have no preview and no bucket keys.
    op.add_column('artwork_images', sa.Column('preview_url', sa.Text(), nullable=True))
    op.add_column('artwork_images', sa.Column('storage_key', sa.Text(), nullable=True))
    op.add_column('artwork_images', sa.Column('preview_storage_key', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('artwork_images', 'preview_storage_key')
    op.drop_column('artwork_images', 'storage_key')
    op.drop_column('artwork_images', 'preview_url')
