"""Add notes visibility tiers

Revision ID: 49a7d3184b0d
Revises: c71d4e83f9a2
Create Date: 2026-08-19 21:19:59.760457

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '49a7d3184b0d'
down_revision: Union[str, Sequence[str], None] = 'c71d4e83f9a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        for domain in ('movies', 'tv', 'books', 'games'):
            column = f'visibility_notes_{domain}'
            batch_op.add_column(
                sa.Column(
                    column,
                    sa.Enum(
                        'private',
                        'friends',
                        'public',
                        name=f'ck_users_{column}',
                        native_enum=False,
                        create_constraint=True,
                        length=16,
                    ),
                    nullable=True,
                )
            )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        for domain in ('games', 'books', 'tv', 'movies'):
            column = f'visibility_notes_{domain}'
            batch_op.drop_constraint(f'ck_users_{column}', type_='check')
            batch_op.drop_column(column)
