"""ranked list length preference

Viewer display preference (api#122): how many entries of a ranked list to
show by default. NULL means unset, read as the default (25) everywhere via
``app.services.preferences.coerce`` — nothing here backfills existing rows,
since NULL already means the right thing.

The unrelated index/constraint drift autogenerate also detected (an existing
mismatch between a handful of tracker/movie indexes and the current models,
predating this change) is deliberately left out of this migration — it
belongs to whichever change introduced it, not to this one.

Revision ID: 4fe7bd675954
Revises: 16ad1155c69d
Create Date: 2026-08-03 18:37:35.583225

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4fe7bd675954'
down_revision: Union[str, Sequence[str], None] = '16ad1155c69d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the ranked_list_length preference column."""
    op.add_column(
        'users',
        sa.Column(
            'ranked_list_length',
            sa.Enum(
                '25',
                '50',
                '100',
                'all',
                name='ck_users_ranked_list_length',
                native_enum=False,
                create_constraint=True,
                length=4,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the ranked_list_length preference column."""
    op.drop_column('users', 'ranked_list_length')
