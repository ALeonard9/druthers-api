"""friendships table

The mutual friend graph (api#275) the ``friends`` visibility tier resolves
against (api#277).

One row per *relationship*, not one per direction. The pair is stored in
canonical order and both invariants that depend on it are declared in the
database, not just in the service layer:

* ``uq_friendships_pair`` - a pair can hold at most one row, so a race
  between two simultaneous requests resolves to a single relationship instead
  of two rows that later disagree;
* ``ck_friendships_canonical_order`` - ``user_low_id < user_high_id``, which
  also rules out a self-friendship without a separate constraint.

``status`` is a VARCHAR with a CHECK rather than a native Postgres enum, for
the same reasons the visibility tiers are (b7c1f4a9d2e3): a bad write fails
loudly, and a rollback leaves no orphaned type behind. There is no
``declined`` value - declining or cancelling deletes the row.

The three FKs cascade on delete: a friendship is meaningless without both
parties, so removing a user takes their edges with them rather than leaving
rows on NOT NULL columns pointing at nobody.

Revision ID: c9a2e7f31b04
Revises: b7c1f4a9d2e3
Create Date: 2026-08-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c9a2e7f31b04'
down_revision: Union[str, Sequence[str], None] = 'b7c1f4a9d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match the CHECK SQLAlchemy renders for the Enum on
# models.DbFriendship.status, or autogenerate sees drift on the next revision.
ALLOWED_STATUS = "('pending', 'accepted')"


def upgrade() -> None:
    """Create the friendships table."""
    op.create_table(
        'friendships',
        sa.Column('user_low_id', sa.Integer(), nullable=False),
        sa.Column('user_high_id', sa.Integer(), nullable=False),
        sa.Column('requested_by_id', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.String(length=16),
            nullable=False,
            server_default='pending',
        ),
        sa.Column('requested_at', sa.DateTime(), nullable=False),
        sa.Column('responded_at', sa.DateTime(), nullable=True),
        sa.Column('pk', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_low_id'], ['users.pk'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_high_id'], ['users.pk'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['requested_by_id'], ['users.pk'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('pk'),
        sa.UniqueConstraint('user_low_id', 'user_high_id', name='uq_friendships_pair'),
        sa.CheckConstraint(
            'user_low_id < user_high_id', name='ck_friendships_canonical_order'
        ),
        sa.CheckConstraint(f'status IN {ALLOWED_STATUS}', name='ck_friendships_status'),
    )
    with op.batch_alter_table('friendships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_friendships_id'), ['id'], unique=True)
        batch_op.create_index(batch_op.f('ix_friendships_pk'), ['pk'], unique=False)
        # Either seat can be the one asking, so both sides are indexed.
        batch_op.create_index(
            batch_op.f('ix_friendships_user_low_id'), ['user_low_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_friendships_user_high_id'), ['user_high_id'], unique=False
        )


def downgrade() -> None:
    """Drop the friendships table. Pending and accepted edges are lost."""
    with op.batch_alter_table('friendships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_friendships_user_high_id'))
        batch_op.drop_index(batch_op.f('ix_friendships_user_low_id'))
        batch_op.drop_index(batch_op.f('ix_friendships_pk'))
        batch_op.drop_index(batch_op.f('ix_friendships_id'))
    op.drop_table('friendships')
