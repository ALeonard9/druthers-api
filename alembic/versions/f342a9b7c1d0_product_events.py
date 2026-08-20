"""Add privacy-minimal product event instrumentation.

Revision ID: f342a9b7c1d0
Revises: c71d4e83f9a2
Create Date: 2026-08-20 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f342a9b7c1d0'
down_revision: Union[str, Sequence[str], None] = 'c71d4e83f9a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'product_events',
        sa.Column('pk', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id', sa.String(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.pk'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('pk'),
    )
    op.create_index(
        op.f('ix_product_events_pk'), 'product_events', ['pk'], unique=False
    )
    op.create_index(op.f('ix_product_events_id'), 'product_events', ['id'], unique=True)
    op.create_index(
        op.f('ix_product_events_user_id'), 'product_events', ['user_id'], unique=False
    )
    op.create_index(
        op.f('ix_product_events_event_type'),
        'product_events',
        ['event_type'],
        unique=False,
    )
    op.create_index(
        op.f('ix_product_events_occurred_at'),
        'product_events',
        ['occurred_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_product_events_occurred_at'), table_name='product_events')
    op.drop_index(op.f('ix_product_events_event_type'), table_name='product_events')
    op.drop_index(op.f('ix_product_events_user_id'), table_name='product_events')
    op.drop_index(op.f('ix_product_events_id'), table_name='product_events')
    op.drop_index(op.f('ix_product_events_pk'), table_name='product_events')
    op.drop_table('product_events')
