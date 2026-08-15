"""follows table

The asymmetric, unapproved follow graph (api#276) that will feed the
activity feed in api#280. Deliberately separate from ``friendships``
(c9a2e7f31b04): a follow never appears in
``app.services.visibility.ceiling_for``, so there is no column here for a
migration to ever wire into that function by accident.

One row per *direction* rather than per pair - ``follower_id``,
``followee_id`` - since A following B and B following A are unrelated
facts. ``uq_follows_pair`` keeps a follower from accumulating duplicate rows
for the same target; ``ck_follows_not_self`` rules out a self-follow at the
database layer, the same belt-and-suspenders the service layer's
``assert_not_self`` provides above it.

Both FKs cascade on delete: a follow is meaningless without both parties, so
removing a user takes their outgoing and incoming follows with them.

Revision ID: 16ad1155c69d
Revises: c9a2e7f31b04
Create Date: 2026-08-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '16ad1155c69d'
down_revision: Union[str, Sequence[str], None] = 'c9a2e7f31b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the follows table."""
    op.create_table(
        'follows',
        sa.Column('follower_id', sa.Integer(), nullable=False),
        sa.Column('followee_id', sa.Integer(), nullable=False),
        sa.Column('followed_at', sa.DateTime(), nullable=False),
        sa.Column('pk', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['follower_id'], ['users.pk'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['followee_id'], ['users.pk'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('pk'),
        sa.UniqueConstraint('follower_id', 'followee_id', name='uq_follows_pair'),
        sa.CheckConstraint('follower_id != followee_id', name='ck_follows_not_self'),
    )
    with op.batch_alter_table('follows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_follows_id'), ['id'], unique=True)
        batch_op.create_index(batch_op.f('ix_follows_pk'), ['pk'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_follows_follower_id'), ['follower_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_follows_followee_id'), ['followee_id'], unique=False
        )


def downgrade() -> None:
    """Drop the follows table. Existing follow edges are lost."""
    with op.batch_alter_table('follows', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_follows_followee_id'))
        batch_op.drop_index(batch_op.f('ix_follows_follower_id'))
        batch_op.drop_index(batch_op.f('ix_follows_pk'))
        batch_op.drop_index(batch_op.f('ix_follows_id'))
    op.drop_table('follows')
