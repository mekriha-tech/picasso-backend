"""artwork_images storage_provider

Revision ID: d4f6b8a0c2e3
Revises: c3e5a7b9d1f2
Create Date: 2026-11-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd4f6b8a0c2e3'
down_revision: Union[str, Sequence[str], None] = 'c3e5a7b9d1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain nullable text (no enum), so adding a provider later needs no migration.
    # NULL = the image is not in our storage (external URL, e.g. seeded images).
    op.add_column('artwork_images', sa.Column('storage_provider', sa.Text(), nullable=True))
    # Until now R2 was the only provider, so every row that has an object key lives in R2.
    op.execute("UPDATE artwork_images SET storage_provider = 'r2' WHERE storage_key IS NOT NULL")


def downgrade() -> None:
    op.drop_column('artwork_images', 'storage_provider')
