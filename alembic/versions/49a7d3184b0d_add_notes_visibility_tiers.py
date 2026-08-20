"""Add notes visibility tiers

Revision ID: 49a7d3184b0d
Revises: c71d4e83f9a2
Create Date: 2026-08-19 21:19:59.760457

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '49a7d3184b0d'
down_revision: Union[str, Sequence[str], None] = 'c71d4e83f9a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    visibility_tier_enum = sa.Enum(
        'public', 'friends', 'private', name='visibilitytier'
    )

    op.add_column(
        'users',
        sa.Column('visibility_notes_movies', visibility_tier_enum, nullable=True),
    )
    op.add_column(
        'users', sa.Column('visibility_notes_tv', visibility_tier_enum, nullable=True)
    )
    op.add_column(
        'users',
        sa.Column('visibility_notes_books', visibility_tier_enum, nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('visibility_notes_games', visibility_tier_enum, nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'visibility_notes_games')
    op.drop_column('users', 'visibility_notes_books')
    op.drop_column('users', 'visibility_notes_tv')
    op.drop_column('users', 'visibility_notes_movies')
