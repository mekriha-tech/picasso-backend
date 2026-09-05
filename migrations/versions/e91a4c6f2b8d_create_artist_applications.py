"""create_artist_applications_and_application_works

Revision ID: e91a4c6f2b8d
Revises: a5c77090a161
Create Date: 2026-09-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e91a4c6f2b8d'
down_revision: Union[str, Sequence[str], None] = 'a5c77090a161'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    application_status = postgresql.ENUM(
        'draft', 'submitted', 'under_review', 'approved', 'rejected',
        name='application_status',
    )
    application_status.create(op.get_bind(), checkfirst=True)
    # Prevent op.create_table() below from trying to auto-create this enum a
    # second time (its own table-creation DDL runs with checkfirst=False,
    # which turns that redundant attempt into a fatal DuplicateObjectError).
    application_status.create_type = False

    op.create_table(
        'artist_applications',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', application_status, server_default='draft', nullable=False),
        sa.Column('full_name', sa.Text(), nullable=False),
        sa.Column('location', sa.Text(), nullable=False),
        sa.Column('primary_medium', sa.Text(), nullable=False),
        sa.Column('years_practising', sa.SmallInteger(), nullable=True),
        sa.Column('website_url', sa.Text(), nullable=True),
        sa.Column('instagram', sa.Text(), nullable=True),
        sa.Column('statement', sa.Text(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reviewed_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'one_open_application_per_user', 'artist_applications', ['user_id'], unique=True,
        postgresql_where=sa.text("status IN ('draft','submitted','under_review')"),
    )

    op.create_table(
        'application_works',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('application_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('slot_index', sa.SmallInteger(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('year', sa.SmallInteger(), nullable=True),
        sa.Column('medium', sa.Text(), nullable=True),
        sa.Column('dimensions', sa.Text(), nullable=True),
        sa.Column('image_url', sa.Text(), nullable=False),
        sa.CheckConstraint('slot_index BETWEEN 0 AND 2', name='application_works_slot_index_check'),
        sa.ForeignKeyConstraint(['application_id'], ['artist_applications.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('application_id', 'slot_index', name='uq_application_works_slot'),
    )


def downgrade() -> None:
    op.drop_table('application_works')
    op.drop_index('one_open_application_per_user', table_name='artist_applications')
    op.drop_table('artist_applications')
    application_status = postgresql.ENUM(name='application_status')
    application_status.drop(op.get_bind(), checkfirst=True)
