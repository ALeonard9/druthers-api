"""Add impersonation_sessions

Server-side state for the admin view-as feature (#341). The impersonation
JWT is the capability, but a token alone cannot satisfy two of the issue's
requirements: ending a session "at any time", and a session dying when the
acting admin is demoted or disabled. Both need a row to check per request.

Separate migration rather than folded into e584fd92429d: that one is the
audit-log/disabled_at foundation and has already been applied locally, and
these are different features that should be revertable independently.

Revision ID: c71d4e83f9a2
Revises: e584fd92429d
Create Date: 2026-08-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c71d4e83f9a2'
down_revision: Union[str, Sequence[str], None] = 'e584fd92429d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'impersonation_sessions',
        sa.Column('pk', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id', sa.String(), nullable=True),
        sa.Column('admin_user_pk', sa.Integer(), nullable=False),
        sa.Column('target_user_pk', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['admin_user_pk'],
            ['users.pk'],
            name='fk_impersonation_sessions_admin_user_pk',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['target_user_pk'],
            ['users.pk'],
            name='fk_impersonation_sessions_target_user_pk',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('pk'),
    )
    op.create_index(
        op.f('ix_impersonation_sessions_pk'),
        'impersonation_sessions',
        ['pk'],
        unique=False,
    )
    # Looked up by id on every impersonated request, so it needs the index
    # even though the base model would give it one anyway.
    op.create_index(
        op.f('ix_impersonation_sessions_id'),
        'impersonation_sessions',
        ['id'],
        unique=True,
    )
    op.create_index(
        op.f('ix_impersonation_sessions_admin_user_pk'),
        'impersonation_sessions',
        ['admin_user_pk'],
        unique=False,
    )
    op.create_index(
        op.f('ix_impersonation_sessions_target_user_pk'),
        'impersonation_sessions',
        ['target_user_pk'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_impersonation_sessions_target_user_pk'),
        table_name='impersonation_sessions',
    )
    op.drop_index(
        op.f('ix_impersonation_sessions_admin_user_pk'),
        table_name='impersonation_sessions',
    )
    op.drop_index(
        op.f('ix_impersonation_sessions_id'), table_name='impersonation_sessions'
    )
    op.drop_index(
        op.f('ix_impersonation_sessions_pk'), table_name='impersonation_sessions'
    )
    op.drop_table('impersonation_sessions')
