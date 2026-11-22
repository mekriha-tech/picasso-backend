"""create_exhibitions

Revision ID: f7a9c1e3b5d2
Revises: a1b2c3d4e5f6
Create Date: 2026-11-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f7a9c1e3b5d2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PRD 3.8 plus viewer_kind and sort_order. viewer_kind is TEXT + CHECK, not a native enum.
    op.create_table(
        'exhibitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('cover_image_url', sa.Text(), nullable=True),
        sa.Column('vr_tour_url', sa.Text(), nullable=True),
        sa.Column('viewer_kind', sa.Text(), server_default='vendor_embed', nullable=False),
        sa.Column('curator_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('starts_on', sa.Date(), nullable=True),
        sa.Column('ends_on', sa.Date(), nullable=True),
        sa.Column('is_published', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("viewer_kind IN ('vendor_embed', 'first_party')", name='exhibition_viewer_kind_valid'),
        sa.CheckConstraint(
            "viewer_kind <> 'vendor_embed' OR vr_tour_url IS NOT NULL",
            name='vendor_embed_needs_vr_tour_url',
        ),
        sa.ForeignKeyConstraint(['curator_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    op.create_table(
        'exhibition_artworks',
        sa.Column('exhibition_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('artwork_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('wall_position', sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(['exhibition_id'], ['exhibitions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['artwork_id'], ['artworks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('exhibition_id', 'artwork_id'),
    )


def downgrade() -> None:
    op.drop_table('exhibition_artworks')
    op.drop_table('exhibitions')
