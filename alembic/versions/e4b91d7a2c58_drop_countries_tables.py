"""drop countries tables

Countries was the one tracked domain that never made it into the product
(api#273): it was never registered in ``SHELVES``, so it carried no visibility
flags and could never appear on a public profile. The models, router, schemas
and enrichment service are removed in the same change, so the schema and the
code never disagree.

``downgrade()`` faithfully recreates ``countries`` and ``user_countries`` at
their final shape — every column, index, unique/foreign-key constraint, and
the ``ck_user_countries_rank_1_based`` CHECK — so a rollback lands on a schema
autogenerate would consider identical to the pre-drop one. **The rows are not
recoverable**: the tables come back empty, and restoring the data needs a
backup taken before the upgrade ran.

Revision ID: e4b91d7a2c58
Revises: 5361d31e8e2b
Create Date: 2026-08-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e4b91d7a2c58'
down_revision: Union[str, Sequence[str], None] = '5361d31e8e2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the countries catalog and its per-user tracker."""
    # Tracker first: its country_id FK references countries.pk.
    op.drop_table('user_countries')
    op.drop_table('countries')


def downgrade() -> None:
    """
    Recreate both tables, empty, at the shape they had at 5361d31e8e2b.

    Columns/indexes/constraints are reproduced from a3732379d384 (initial
    schema) plus 5cfe462a0464 (detail columns + list flags), d3b81f4a9c67
    (rank CHECK) and e9f4a63b7c25 (ranked_at).
    """
    op.create_table(
        'countries',
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('country_code', sa.String(length=4), nullable=True),
        sa.Column('region', sa.String(length=100), nullable=True),
        sa.Column('subregion', sa.String(length=100), nullable=True),
        sa.Column('capital', sa.String(length=255), nullable=True),
        sa.Column('population', sa.Integer(), nullable=True),
        sa.Column('flag_emoji', sa.String(length=8), nullable=True),
        sa.Column('flag_url', sa.String(length=500), nullable=True),
        sa.Column('pk', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('pk'),
        sa.UniqueConstraint('country_code'),
    )
    with op.batch_alter_table('countries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_countries_id'), ['id'], unique=True)
        batch_op.create_index(batch_op.f('ix_countries_pk'), ['pk'], unique=False)

    op.create_table(
        'user_countries',
        sa.Column('country_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('on_watchlist', sa.Boolean(), nullable=False),
        sa.Column('on_rankings', sa.Boolean(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=True),
        sa.Column('ranked_at', sa.DateTime(), nullable=True),
        sa.Column('completed', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('first_visited', sa.DateTime(), nullable=True),
        sa.Column('pk', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            'rank IS NULL OR rank >= 1', name='ck_user_countries_rank_1_based'
        ),
        sa.ForeignKeyConstraint(
            ['country_id'],
            ['countries.pk'],
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.pk'],
        ),
        sa.PrimaryKeyConstraint('pk'),
    )
    with op.batch_alter_table('user_countries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_countries_id'), ['id'], unique=True)
        batch_op.create_index(batch_op.f('ix_user_countries_pk'), ['pk'], unique=False)
