"""visibility tiers

Replaces the eight Boolean visibility flags on ``users`` with nine
``private | friends | public`` tier columns (api#274) - one per shelf, one
per shelf watchlist, and a ninth governing the profile page itself.

Stored as VARCHAR with a per-column CHECK rather than an integer or a native
Postgres enum. An integer would let a typo become a *more* permissive
setting; a native enum leaves a type behind on rollback. A constrained string
fails loudly on a bad write and disappears with the column.

Data mapping:

* ``True`` -> ``public``; ``False`` and ``NULL`` -> ``private``.
* ``visibility_profile`` takes the most-open shelf tier found on the row, so
  every existing public shelf keeps resolving through its profile. In
  practice that is ``public`` if any flag was set and ``private`` otherwise -
  ``friends`` cannot exist yet - but the statement is written for the general
  case so a re-run against partly-migrated data still lands correctly.

``downgrade()`` recreates the booleans as ``tier = 'public'``. **Friends-tier
settings are not recoverable**: a friends-only shelf comes back as ``False``,
which is the safe direction (private) rather than the lossy-open one.

Revision ID: b7c1f4a9d2e3
Revises: e4b91d7a2c58
Create Date: 2026-08-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7c1f4a9d2e3'
down_revision: Union[str, Sequence[str], None] = 'e4b91d7a2c58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (old Boolean column, new tier column), in DbUser declaration order.
SHELF_COLUMNS: Sequence[tuple] = (
    ('public_movies', 'visibility_movies'),
    ('public_tv', 'visibility_tv'),
    ('public_books', 'visibility_books'),
    ('public_games', 'visibility_games'),
    ('public_watchlist_movies', 'visibility_watchlist_movies'),
    ('public_watchlist_tv', 'visibility_watchlist_tv'),
    ('public_watchlist_books', 'visibility_watchlist_books'),
    ('public_watchlist_games', 'visibility_watchlist_games'),
)

PROFILE_COLUMN = 'visibility_profile'
TIER_COLUMNS: Sequence[str] = (PROFILE_COLUMN,) + tuple(
    tier for _, tier in SHELF_COLUMNS
)

# Must match the CHECK SQLAlchemy renders for models.tier_column(), or
# autogenerate will see drift on the next revision.
ALLOWED = "('private', 'friends', 'public')"


def upgrade() -> None:
    """Add the nine tier columns, carry the flags over, drop the flags."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        for column in TIER_COLUMNS:
            batch_op.add_column(sa.Column(column, sa.String(length=16), nullable=True))

    for boolean, tier in SHELF_COLUMNS:
        op.execute(
            f'UPDATE users SET {tier} = '  # nosec: fixed column names
            f"CASE WHEN {boolean} THEN 'public' ELSE 'private' END"
        )

    any_public = ' OR '.join(f"{tier} = 'public'" for _, tier in SHELF_COLUMNS)
    any_friends = ' OR '.join(f"{tier} = 'friends'" for _, tier in SHELF_COLUMNS)
    op.execute(
        f'UPDATE users SET {PROFILE_COLUMN} = CASE '  # nosec: fixed column names
        f"WHEN {any_public} THEN 'public' "
        f"WHEN {any_friends} THEN 'friends' "
        f"ELSE 'private' END"
    )

    with op.batch_alter_table('users', schema=None) as batch_op:
        for column in TIER_COLUMNS:
            batch_op.alter_column(
                column,
                existing_type=sa.String(length=16),
                nullable=False,
                server_default='private',
            )
            batch_op.create_check_constraint(
                f'ck_users_{column}', f'{column} IN {ALLOWED}'
            )
        for boolean, _ in SHELF_COLUMNS:
            batch_op.drop_column(boolean)


def downgrade() -> None:
    """Restore the booleans (``public`` -> True, everything else -> False)."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        for boolean, _ in SHELF_COLUMNS:
            batch_op.add_column(sa.Column(boolean, sa.Boolean(), nullable=True))

    for boolean, tier in SHELF_COLUMNS:
        op.execute(
            f'UPDATE users SET {boolean} = '  # nosec: fixed column names
            f"CASE WHEN {tier} = 'public' THEN true ELSE false END"
        )

    with op.batch_alter_table('users', schema=None) as batch_op:
        for column in TIER_COLUMNS:
            batch_op.drop_constraint(f'ck_users_{column}', type_='check')
            batch_op.drop_column(column)
