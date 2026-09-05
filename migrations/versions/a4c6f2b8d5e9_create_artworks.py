"""create_artworks_and_artwork_images_tables

Revision ID: a4c6f2b8d5e9
Revises: f2b8d5a91c34
Create Date: 2026-09-05 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a4c6f2b8d5e9'
down_revision: Union[str, Sequence[str], None] = 'f2b8d5a91c34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    listing_type = postgresql.ENUM('sale', 'auction', 'display', name='listing_type')
    listing_type.create(op.get_bind(), checkfirst=True)
    artwork_status = postgresql.ENUM(
        'draft', 'published', 'reserved', 'sold', 'unlisted', 'removed', name='artwork_status',
    )
    artwork_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'artworks',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('artist_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('year', sa.SmallInteger(), nullable=True),
        sa.Column('medium', sa.Text(), nullable=True),
        sa.Column('dimensions', sa.Text(), nullable=True),
        sa.Column('width_cm', sa.Numeric(8, 2), nullable=True),
        sa.Column('height_cm', sa.Numeric(8, 2), nullable=True),
        sa.Column('category', sa.Text(), nullable=True),
        sa.Column('listing_type', listing_type, server_default='display', nullable=False),
        sa.Column('status', artwork_status, server_default='draft', nullable=False),
        sa.Column('price', sa.Numeric(12, 2), nullable=True),
        sa.Column('sold', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('sold_price', sa.Numeric(12, 2), nullable=True),
        sa.Column('sold_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('primary_image_url', sa.Text(), nullable=True),
        sa.Column('view_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("listing_type <> 'sale' OR price IS NOT NULL", name='sale_needs_price'),
        sa.CheckConstraint("listing_type <> 'display' OR price IS NULL", name='display_has_no_price'),
        sa.ForeignKeyConstraint(['artist_id'], ['artist_profiles.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )

    op.create_table(
        'artwork_images',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('artwork_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('alt_text', sa.Text(), nullable=True),
        sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
        sa.Column('is_primary', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('width_px', sa.Integer(), nullable=True),
        sa.Column('height_px', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['artwork_id'], ['artworks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'one_primary_image_per_artwork', 'artwork_images', ['artwork_id'], unique=True,
        postgresql_where=sa.text('is_primary'),
    )


def downgrade() -> None:
    op.drop_index('one_primary_image_per_artwork', table_name='artwork_images')
    op.drop_table('artwork_images')
    op.drop_table('artworks')
    artwork_status = postgresql.ENUM(name='artwork_status')
    artwork_status.drop(op.get_bind(), checkfirst=True)
    listing_type = postgresql.ENUM(name='listing_type')
    listing_type.drop(op.get_bind(), checkfirst=True)
