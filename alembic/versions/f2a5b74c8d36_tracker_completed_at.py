"""tracker completed_at

The date the user finished an item (#159) - the old site's ``g_first`` field,
lost/partially conflated by the orion import. Defaults to the day an item
enters Rankings; editable on the detail page. Historical values are restored
separately by ``app.migration.backfill_completed_at`` (better data than any
in-schema backfill could manage).

Countries deliberately excluded - the product no longer tracks them.

Revision ID: f2a5b74c8d36
Revises: e9f4a63b7c25
Create Date: 2026-07-19 07:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f2a5b74c8d36'
down_revision: Union[str, Sequence[str], None] = 'e9f4a63b7c25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRACKER_TABLES = (
    'user_movies',
    'user_tv_shows',
    'user_books',
    'user_video_games',
)


def upgrade() -> None:
    """Upgrade schema."""
    for table_name in TRACKER_TABLES:
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(sa.Column('completed_at', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    for table_name in TRACKER_TABLES:
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.drop_column('completed_at')
