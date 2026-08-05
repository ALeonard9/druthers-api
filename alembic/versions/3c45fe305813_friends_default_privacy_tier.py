"""friends default privacy tier

Moves the server-side default for every visibility tier column from
``private`` to ``friends`` (web#156) — a fresh signup should be visible to
the friends who invited them, not invisible by default. Mirrors the
DEFAULT_TIER flip in app.services.visibility, which already changes the
ORM-side default; this keeps the DB-level default in parity for any insert
that bypasses the ORM.

Only changes the column default for *future* inserts. Existing users' stored
tier values are untouched.

Revision ID: 3c45fe305813
Revises: 4fe7bd675954
Create Date: 2026-08-04 23:18:04.366154

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3c45fe305813'
down_revision: Union[str, Sequence[str], None] = '4fe7bd675954'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same nine columns as alembic/versions/b7c1f4a9d2e3_visibility_tiers.py,
# which introduced them.
TIER_COLUMNS: Sequence[str] = (
    'visibility_profile',
    'visibility_movies',
    'visibility_tv',
    'visibility_books',
    'visibility_games',
    'visibility_watchlist_movies',
    'visibility_watchlist_tv',
    'visibility_watchlist_books',
    'visibility_watchlist_games',
)


def upgrade() -> None:
    """Default new tier columns to 'friends' instead of 'private'."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        for column in TIER_COLUMNS:
            batch_op.alter_column(
                column,
                existing_type=sa.String(length=16),
                existing_nullable=False,
                server_default='friends',
            )


def downgrade() -> None:
    """Restore the 'private' default."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        for column in TIER_COLUMNS:
            batch_op.alter_column(
                column,
                existing_type=sa.String(length=16),
                existing_nullable=False,
                server_default='private',
            )
