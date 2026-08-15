"""exclusive list membership

One-time cleanup for the one-home rule (#145): an item lives on exactly one
of Watchlist / To-be-ranked / Ranked. For existing overlaps, Rankings wins
(ranked > to-rank > watchlist). One-way - the overlap isn't recoverable.

Revision ID: c7d2e91b4f03
Revises: b41f0a7c9e21
Create Date: 2026-07-18 10:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7d2e91b4f03'
down_revision: Union[str, Sequence[str], None] = 'b41f0a7c9e21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRACKER_TABLES = (
    'user_movies',
    'user_tv_shows',
    'user_books',
    'user_video_games',
    'user_countries',
)


def upgrade() -> None:
    """Upgrade schema."""
    for table_name in TRACKER_TABLES:
        tracker = sa.table(
            table_name,
            sa.column('on_watchlist', sa.Boolean),
            sa.column('on_rankings', sa.Boolean),
        )
        op.execute(
            tracker.update()
            .where(tracker.c.on_rankings == sa.true())
            .values(on_watchlist=sa.false())
        )


def downgrade() -> None:
    """Downgrade schema (data cleanup is one-way; nothing to restore)."""
