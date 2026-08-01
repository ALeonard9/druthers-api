"""refresh tokens

Revocable, rotating browser sessions (#246). Only the SHA-256 hash of each
token is stored; ``family_id`` groups a sign-in's rotation chain so a replayed
token can take the whole chain down.

Revision ID: b4c8d21e9f57
Revises: d3b81f4a9c67
Create Date: 2026-07-31 09:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b4c8d21e9f57'
down_revision: Union[str, Sequence[str], None] = 'd3b81f4a9c67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'refresh_tokens',
        sa.Column('pk', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('family_id', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.pk'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('pk'),
    )
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_refresh_tokens_id'), ['id'], unique=True)
        batch_op.create_index(batch_op.f('ix_refresh_tokens_pk'), ['pk'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_refresh_tokens_token_hash'), ['token_hash'], unique=True
        )
        batch_op.create_index(
            batch_op.f('ix_refresh_tokens_user_id'), ['user_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_refresh_tokens_family_id'), ['family_id'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_family_id'))
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_user_id'))
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_token_hash'))
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_pk'))
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_id'))
    op.drop_table('refresh_tokens')
