# pylint: disable=missing-function-docstring
"""
Data import: bring your library in from other services.
Currently: Goodreads CSV (books). IMDb CSV (movies/TV) is next.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status, Request
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.services.goodreads_import import import_goodreads_csv

router = APIRouter(prefix='/v1/users/me/import', tags=['Import'])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # a Goodreads export is tens of KB


@router.post('/goodreads')
async def import_goodreads(
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Upload the standard Goodreads library export CSV. Idempotent — re-running
    the same file updates rather than duplicates. Returns counts plus any
    skipped rows with reasons (nothing is dropped silently).
    """
    content_length = request.headers.get('content-length')
    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail='File too large for a Goodreads export',
        )

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail='File too large for a Goodreads export',
        )
    try:
        content = raw.decode('utf-8-sig')  # Goodreads ships a BOM sometimes
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='File is not UTF-8 text — upload the raw Goodreads CSV',
        ) from exc

    report = import_goodreads_csv(db, current_user[0].pk, content)
    return {
        'books_created': report.books_created,
        'books_matched': report.books_matched,
        'trackers_created': report.trackers_created,
        'trackers_updated': report.trackers_updated,
        'unplaced_read_book_ids': report.unplaced_read_book_ids,
        'skipped': report.skipped,
    }
