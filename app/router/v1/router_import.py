# pylint: disable=missing-function-docstring
"""
Data import: bring your library in from other services.
Currently: Goodreads CSV (books) and the generic CSV/XLSX movie template.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.schemas.schemas_sandbox import MovieImportResponse
from app.services.generic_movie_import import (
    ingest_movie_import,
    movie_template_csv,
    movie_template_xlsx,
    validate_movie_import,
)
from app.services.goodreads_import import import_goodreads_csv

router = APIRouter(prefix='/v1/users/me/import', tags=['Import'])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.get('/movies/template.csv', response_class=Response)
def download_movie_csv_template(
    current_user: list = Depends(get_current_user),
):
    """
    Download the movies CSV template.

    Required columns are ``title`` (text, max 255 characters),
    ``release_year`` (YYYY), ``tmdb_id`` (positive integer), and
    ``watched_date`` (YYYY-MM-DD, not in the future). Every field is required;
    title and release year must agree with the referenced TMDB movie. Uploads
    are limited to 5 MiB and 5,000 movie rows.
    """
    del current_user
    return Response(
        content=movie_template_csv(),
        media_type='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': 'attachment; filename="druthers-movies-template.csv"'
        },
    )


@router.get('/movies/template.xlsx', response_class=Response)
def download_movie_xlsx_template(
    current_user: list = Depends(get_current_user),
):
    """
    Download the movies XLSX template.

    The Movies sheet has the required upload columns. The Instructions sheet
    documents accepted formats, examples, TMDB matching rules, and row limits.
    """
    del current_user
    return Response(
        content=movie_template_xlsx(),
        media_type=(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ),
        headers={
            'Content-Disposition': 'attachment; filename="druthers-movies-template.xlsx"'
        },
    )


@router.post(
    '/movies',
    response_model=MovieImportResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            'model': MovieImportResponse,
            'description': 'The complete per-row validation report; no rows were written.',
        }
    },
)
async def import_movies(
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Validate and atomically import a filled movies CSV or XLSX template.

    Validation checks the required columns and values, rejects duplicate TMDB
    IDs, resolves each reference against the local catalog or TMDB, and checks
    that title and release year identify the same movie. A 422 returns every
    field error with its source row and performs no writes.

    A valid file commits once and returns one outcome per row: ``imported``
    for a new history tracker, ``matched`` when an existing tracker was
    promoted or completed, and ``skipped`` when it was already present.
    Re-importing the same file is therefore safe and creates no duplicates.
    """
    content_length = request.headers.get('content-length')
    try:
        too_large = bool(content_length and int(content_length) > MAX_UPLOAD_BYTES)
    except ValueError:
        too_large = False
    if too_large:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail='File too large for a movie import',
        )

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail='File too large for a movie import',
        )

    validation = validate_movie_import(db, raw, file.filename)
    if validation.errors:
        body = MovieImportResponse(
            valid=False,
            errors=[error.as_dict() for error in validation.errors],
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=body.model_dump(),
        )

    report = ingest_movie_import(db, current_user[0].pk, validation.rows)
    return MovieImportResponse(
        valid=True,
        summary=report.summary,
        rows=[outcome.as_dict() for outcome in report.outcomes],
    )


@router.post('/goodreads')
async def import_goodreads(
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Upload the standard Goodreads library export CSV. Idempotent - re-running
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
            detail='File is not UTF-8 text - upload the raw Goodreads CSV',
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
