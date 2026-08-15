"""games detail fields + tracker lists

Revision ID: 32dcf2b915ce
Revises: 5cfe462a0464
Create Date: 2026-07-12 07:30:13.325690

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '32dcf2b915ce'
down_revision: Union[str, Sequence[str], None] = '5cfe462a0464'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add IGDB detail columns and Movies-style list flags, then backfill."""
    with op.batch_alter_table('video_games', schema=None) as batch_op:
        # IGDB cover URLs overflow the legacy 100-char column.
        batch_op.alter_column(
            'poster_url',
            existing_type=sa.String(length=100),
            type_=sa.String(length=254),
            existing_nullable=True,
        )
        batch_op.add_column(sa.Column('year', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('genre', sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column('platforms', sa.String(length=254), nullable=True)
        )
        batch_op.add_column(sa.Column('summary', sa.Text(), nullable=True))

    # Add with a server_default so existing rows are populated, then backfill.
    with op.batch_alter_table('user_video_games', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'on_watchlist',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                'on_rankings',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # Legacy import: completed=1 => played (ranked list, ranks already
    # 1-based); otherwise the backlog, whose rank column holds a meaningless
    # 0 sentinel that must not leak into the ranked list.
    op.execute('UPDATE user_video_games SET on_rankings = TRUE WHERE completed = 1')
    # "anything but completed=1" - matches orion_import's flag derivation.
    op.execute(
        'UPDATE user_video_games SET on_watchlist = TRUE '
        'WHERE completed != 1 OR completed IS NULL'
    )
    op.execute('UPDATE user_video_games SET rank = NULL WHERE on_rankings = FALSE')

    # Drop the server defaults now that existing rows are populated; the app
    # model supplies the default on insert.
    with op.batch_alter_table('user_video_games', schema=None) as batch_op:
        batch_op.alter_column('on_watchlist', server_default=None)
        batch_op.alter_column('on_rankings', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('user_video_games', schema=None) as batch_op:
        batch_op.drop_column('on_rankings')
        batch_op.drop_column('on_watchlist')

    with op.batch_alter_table('video_games', schema=None) as batch_op:
        batch_op.drop_column('summary')
        batch_op.drop_column('platforms')
        batch_op.drop_column('genre')
        batch_op.drop_column('year')
        batch_op.alter_column(
            'poster_url',
            existing_type=sa.String(length=254),
            type_=sa.String(length=100),
            existing_nullable=True,
        )
