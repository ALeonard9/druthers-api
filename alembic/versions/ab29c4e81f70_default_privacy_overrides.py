"""default privacy and nullable shelf overrides

Adds the account-wide default privacy tier introduced by api#298. Existing
concrete shelf tiers are retained as overrides so profiles do not change when
upgraded; only new or explicitly-cleared shelf settings inherit the default.

Revision ID: ab29c4e81f70
Revises: e2c7a94f1b30
Create Date: 2026-08-11 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'ab29c4e81f70'
down_revision: Union[str, Sequence[str], None] = 'e2c7a94f1b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SHELF_TIER_COLUMNS: Sequence[str] = (
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
    """Add the default and let shelf values be absent overrides."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'default_privacy',
                sa.Enum(
                    'private',
                    'friends',
                    'public',
                    name='ck_users_default_privacy',
                    native_enum=False,
                    create_constraint=True,
                    length=16,
                ),
                nullable=False,
                server_default='friends',
            )
        )
        for column in SHELF_TIER_COLUMNS:
            batch_op.alter_column(
                column,
                existing_type=sa.String(length=16),
                existing_nullable=False,
                nullable=True,
                server_default=None,
            )


def downgrade() -> None:
    """Restore concrete friends-tier shelf values before dropping the default."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        for column in SHELF_TIER_COLUMNS:
            batch_op.execute(
                sa.text(
                    f'UPDATE users SET {column} = default_privacy WHERE {column} IS NULL'
                )
            )
            batch_op.alter_column(
                column,
                existing_type=sa.String(length=16),
                existing_nullable=True,
                nullable=False,
                server_default='friends',
            )
        batch_op.drop_column('default_privacy')
