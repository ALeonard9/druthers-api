# pylint: disable=missing-function-docstring
"""
Data export: everything a user has tracked, in JSON (full fidelity) or
per-domain CSV. Your data is never locked in.
"""

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.db.models_sandbox import (
    DbTVEpisode,
    DbUserBook,
    DbUserMovie,
    DbUserTVEpisode,
    DbUserTVShow,
    DbUserVideoGame,
)
from app.schemas.schemas_sandbox import (
    UserBookResponse,
    UserMovieResponse,
    UserTVEpisodeResponse,
    UserTVShowResponse,
    UserVideoGameResponse,
)

router = APIRouter(prefix='/v1/users/me/export', tags=['Export'])

# Data-source licenses ride along so exports stay attributable
# (see druthers-web docs/DATA-SOURCES.md).
LICENSES = {
    'movies': 'Movie data from the OMDb API, CC BY-NC 4.0',
    'tv': 'TV data from TVmaze, CC BY-SA 4.0',
    'books': 'Book data from Open Library',
    'games': 'Game data from IGDB.com',
}


def _state(tracker) -> str:
    """One-word list membership, mirroring the Activity page's derivation."""
    if tracker.on_rankings and tracker.rank is not None:
        return 'ranked'
    if tracker.on_rankings:
        return 'to-rank'
    if tracker.on_watchlist:
        return 'watchlist'
    return 'none'


def _movies(db: Session, user_pk: int):
    return db.query(DbUserMovie).filter(DbUserMovie.user_id == user_pk).all()


def _shows(db: Session, user_pk: int):
    return db.query(DbUserTVShow).filter(DbUserTVShow.user_id == user_pk).all()


def _episode_marks(db: Session, user_pk: int):
    return (
        db.query(DbUserTVEpisode)
        .join(DbTVEpisode, DbUserTVEpisode.episode_id == DbTVEpisode.pk)
        .filter(DbUserTVEpisode.user_id == user_pk)
        .all()
    )


def _books(db: Session, user_pk: int):
    return db.query(DbUserBook).filter(DbUserBook.user_id == user_pk).all()


def _games(db: Session, user_pk: int):
    return db.query(DbUserVideoGame).filter(DbUserVideoGame.user_id == user_pk).all()


@router.get('')
def export_json(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Full-fidelity JSON export of every domain, using the same schemas the
    API serves - anything the UI can show is in here.
    """
    user = current_user[0]
    payload = {
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'account': {'email': user.email, 'display_name': user.display_name},
        'licenses': LICENSES,
        'movies': [
            UserMovieResponse.model_validate(t).model_dump(mode='json')
            for t in _movies(db, user.pk)
        ],
        'tv_shows': [
            UserTVShowResponse.model_validate(t).model_dump(mode='json')
            for t in _shows(db, user.pk)
        ],
        'tv_episode_marks': [
            UserTVEpisodeResponse.model_validate(t).model_dump(mode='json')
            for t in _episode_marks(db, user.pk)
        ],
        'books': [
            UserBookResponse.model_validate(t).model_dump(mode='json')
            for t in _books(db, user.pk)
        ],
        'games': [
            UserVideoGameResponse.model_validate(t).model_dump(mode='json')
            for t in _games(db, user.pk)
        ],
    }
    return payload


def _csv_response(filename: str, header: list, rows: list) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@router.get('/{domain}.csv')
def export_csv(
    domain: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Spreadsheet-friendly CSV for one domain:
    ``movies`` | ``tv-shows`` | ``tv-episodes`` | ``books`` | ``games``.
    """
    user_pk = current_user[0].pk

    if domain == 'movies':
        rows = [
            (
                t.movie.title,
                t.movie.year,
                _state(t),
                t.rank,
                t.movie.imdb,
                t.notes,
                t.created_at,
                t.updated_at,
            )
            for t in _movies(db, user_pk)
        ]
        header = [
            'title',
            'year',
            'state',
            'rank',
            'imdb_id',
            'notes',
            'added_at',
            'updated_at',
        ]
    elif domain == 'tv-shows':
        rows = [
            (
                t.tv_show.title,
                t.tv_show.year,
                _state(t),
                t.rank,
                t.tv_show.imdb,
                'yes' if t.freeze else 'no',
                t.notes,
                t.created_at,
                t.updated_at,
            )
            for t in _shows(db, user_pk)
        ]
        header = [
            'title',
            'year',
            'state',
            'rank',
            'imdb_id',
            'hidden_from_schedule',
            'notes',
            'added_at',
            'updated_at',
        ]
    elif domain == 'tv-episodes':
        rows = [
            (
                t.episode.tv_show.title,
                t.episode.season,
                t.episode.season_number,
                t.episode.title,
                t.episode.airdate,
                'yes' if t.watched else 'no',
                t.updated_at,
            )
            for t in _episode_marks(db, user_pk)
        ]
        header = [
            'show',
            'season',
            'episode',
            'title',
            'airdate',
            'watched',
            'marked_at',
        ]
    elif domain == 'books':
        rows = [
            (
                t.book.title,
                t.book.authors,
                t.book.year,
                _state(t),
                t.rank,
                t.book.isbn,
                t.notes,
                t.created_at,
                t.updated_at,
            )
            for t in _books(db, user_pk)
        ]
        header = [
            'title',
            'authors',
            'year',
            'state',
            'rank',
            'isbn',
            'notes',
            'added_at',
            'updated_at',
        ]
    elif domain == 'games':
        rows = [
            (
                t.game.title,
                t.game.year,
                _state(t),
                t.rank,
                'yes' if t.is_100_percent else 'no',
                t.game.platforms,
                t.notes,
                t.created_at,
                t.updated_at,
            )
            for t in _games(db, user_pk)
        ]
        header = [
            'title',
            'year',
            'state',
            'rank',
            'completed_100_percent',
            'platforms',
            'notes',
            'added_at',
            'updated_at',
        ]
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Unknown export domain - use movies, tv-shows, tv-episodes, books, or games',
        )

    return _csv_response(f'druthers-{domain}.csv', header, rows)
