"""Add activity sharing opt-out

The social feed (#280) evaluates shelf tiers against their current values on
every read. This independent owner-level switch removes all of a user's
otherwise-visible events without rewriting tracker rows.

Revision ID: e2c7a94f1b30
Revises: a7d40c9b1e52
Create Date: 2026-08-11 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e2c7a94f1b30'
down_revision: Union[str, Sequence[str], None] = 'a7d40c9b1e52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the owner-level social activity sharing switch."""
    op.add_column(
        'users',
        sa.Column(
            'share_activity',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )


def downgrade() -> None:
    """Remove the activity sharing switch."""
    op.drop_column('users', 'share_activity')
