"""Add admin_audit_log table and users.disabled_at

Foundations for the admin console (#344): an append-only audit trail for
admin actions, and the disable/enable status column the directory reads
from. Nothing writes ``disabled_at`` yet - the toggle and enforcing it on
the auth path are a later increment - this migration only adds the column
so the web directory has a real status field from the start.

Revision ID: e584fd92429d
Revises: 8f2c1a4d9b73
Create Date: 2026-08-18 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e584fd92429d'
down_revision: Union[str, Sequence[str], None] = '8f2c1a4d9b73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('disabled_at', sa.DateTime(), nullable=True))

    op.create_table(
        'admin_audit_log',
        sa.Column('actor_user_pk', sa.Integer(), nullable=True),
        sa.Column('target_user_pk', sa.Integer(), nullable=True),
        sa.Column('target_user_id', sa.String(), nullable=True),
        sa.Column('target_email', sa.String(), nullable=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('result', sa.String(length=16), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('request_id', sa.String(), nullable=True),
        sa.Column('method', sa.String(length=10), nullable=True),
        sa.Column('path', sa.String(), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('source_ip', sa.String(), nullable=True),
        sa.Column('user_agent', sa.String(), nullable=True),
        sa.Column('pk', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['actor_user_pk'],
            ['users.pk'],
            name='fk_admin_audit_log_actor_user_pk_users',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['target_user_pk'],
            ['users.pk'],
            name='fk_admin_audit_log_target_user_pk_users',
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('pk'),
    )
    with op.batch_alter_table('admin_audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_admin_audit_log_id'), ['id'], unique=True)
        batch_op.create_index(batch_op.f('ix_admin_audit_log_pk'), ['pk'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_admin_audit_log_actor_user_pk'),
            ['actor_user_pk'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_admin_audit_log_target_user_pk'),
            ['target_user_pk'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_admin_audit_log_action'), ['action'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_admin_audit_log_created_at'), ['created_at'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('admin_audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_admin_audit_log_created_at'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_log_action'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_log_target_user_pk'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_log_actor_user_pk'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_log_pk'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_log_id'))
    op.drop_table('admin_audit_log')
    op.drop_column('users', 'disabled_at')
