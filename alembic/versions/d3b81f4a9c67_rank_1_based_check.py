"""tracker ranks are 1-based, enforced

Rank 0 has come back to prod repeatedly (#229 and before it #181). Every fix
so far was a *repair* — ``backfill_rank_base``, run as a step in the deploy
workflow — so the invariant only held on releases where that step ran, and
nothing stopped the column from holding a 0 in between. The Top 5 board prints
the stored rank deliberately (a mismatch is a data bug worth seeing), so every
lapse showed up as "0" against Adam's best movie.

This moves the repair into the migration chain (``alembic upgrade head`` runs
on every release, unconditionally) and then makes the bad state
unrepresentable with a CHECK, so the next write that tries it fails at the
write instead of surfacing on the home page weeks later.

The repair is a renumber rather than a +1 shift: ``ROW_NUMBER()`` over the
current order collapses 0-based ranks, gaps left by deleted rows, and
duplicate positions into a dense 1..N in one pass, and is a no-op on a shelf
that is already correct. ``backfill_rank_base`` could only handle the first of
those, and only when the *whole* shelf started at 0.

Rows carrying a rank while off the rankings list are unplaced (rank NULL).
That is already the app's rule — mark_movie and update_user_movie both clear
the rank when a tracker leaves Rankings — but orion_import copied the legacy
0-based rank onto watchlist rows too, which the CHECK would reject.

Revision ID: d3b81f4a9c67
Revises: 6b17f4396788
Create Date: 2026-07-30 19:20:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd3b81f4a9c67'
down_revision: Union[str, Sequence[str], None] = '6b17f4396788'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRACKER_TABLES = (
    'user_movies',
    'user_tv_shows',
    'user_books',
    'user_video_games',
    'user_countries',
)


def _constraint_name(table: str) -> str:
    """Matches models_sandbox.rank_is_1_based, so autogenerate stays quiet."""
    return f'ck_{table}_rank_1_based'


def upgrade() -> None:
    """Repair any non-1-based ranks, then enforce the invariant."""
    for table in TRACKER_TABLES:
        # An unplaced tracker holds no position (see module docstring).
        op.execute(f"""
            UPDATE {table}
               SET rank = NULL
             WHERE rank IS NOT NULL
               AND on_rankings IS NOT TRUE
            """)
        # Dense 1..N per user, preserving the order the ranks already express.
        # pk breaks ties so duplicate ranks resolve deterministically.
        op.execute(f"""
            WITH renumbered AS (
                SELECT pk,
                       ROW_NUMBER() OVER (
                           PARTITION BY user_id ORDER BY rank, pk
                       ) AS new_rank
                  FROM {table}
                 WHERE on_rankings IS TRUE
                   AND rank IS NOT NULL
            )
            UPDATE {table} AS t
               SET rank = r.new_rank
              FROM renumbered AS r
             WHERE t.pk = r.pk
               AND t.rank IS DISTINCT FROM r.new_rank
            """)
        op.create_check_constraint(
            _constraint_name(table), table, 'rank IS NULL OR rank >= 1'
        )


def downgrade() -> None:
    """Drop the constraint. The renumbered data is left as-is (it is correct)."""
    for table in TRACKER_TABLES:
        op.drop_constraint(_constraint_name(table), table, type_='check')
