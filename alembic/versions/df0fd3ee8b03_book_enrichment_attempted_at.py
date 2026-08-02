"""book enrichment attempted at

Track when enrich_books last resolved a book, hit or miss (#258): a missing
field is a real, permanent answer from the source, not "never enriched", so
pending_books retries on an interval instead of re-fetching an unresolvable
field every run forever.

Revision ID: df0fd3ee8b03
Revises: c1e6a92f7b48
Create Date: 2026-08-02 20:37:36.678331

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'df0fd3ee8b03'
down_revision: Union[str, Sequence[str], None] = 'c1e6a92f7b48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('books', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('enrichment_attempted_at', sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('books', schema=None) as batch_op:
        batch_op.drop_column('enrichment_attempted_at')
