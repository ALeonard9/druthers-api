"""movie tmdb columns

Adds ``movies.tmdb`` and ``movies.rating_tmdb`` for the OMDb->TMDB migration
(#163). OMDb is CC BY-NC (non-commercial only) and its posters hotlinked
m.media-amazon.com; TMDB licenses both data and images for application use.

``tmdb`` becomes the catalog's external join key because TMDB's search
endpoint returns no IMDb id — ``tracked_status`` can only badge search hits
against something search actually returns. ``imdb`` is kept and still written
(the detail endpoint supplies it), just no longer joined on.

``rating_imdb`` is left in place holding its imported OMDb-era values. TMDB
has no IMDb rating, so it can never be refreshed; ``rating_tmdb`` carries
TMDB's ``vote_average`` instead and is what the UI displays.

Both columns are nullable and unpopulated by this migration — the values are
filled in afterwards by ``app.migration.backfill_tmdb``, which needs network
access and throttling that don't belong in a schema migration.

Revision ID: a1c4f80b6e37
Revises: bdf47c1723a4
Create Date: 2026-07-24 10:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1c4f80b6e37'
down_revision: Union[str, Sequence[str], None] = 'bdf47c1723a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('movies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tmdb', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('rating_tmdb', sa.Float(), nullable=True))
    # A unique *index* rather than a constraint: it works identically on
    # SQLite (local dev) and PostgreSQL, and NULLs stay allowed so rows the
    # backfill can't resolve don't collide with each other.
    op.create_index('ix_movies_tmdb', 'movies', ['tmdb'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_movies_tmdb', table_name='movies')
    with op.batch_alter_table('movies', schema=None) as batch_op:
        batch_op.drop_column('rating_tmdb')
        batch_op.drop_column('tmdb')
