# pylint: disable=missing-function-docstring, useless-return
"""
This module contains the API routes for Books.

Mirrors the Movies pattern: admin-only global catalog CRUD, an Open Library
search proxy, lazy enrichment on detail view (keyed on isbn), and per-user
trackers with independent Watchlist (to-read) / Rankings (read) lists.
"""

from typing import List, Union

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.db.database import get_db
from app.services.rate_limit import catalog_add_cap, search_rate_limit
from app.services.tracker_query import (
    list_tracker_items,
    list_params,
    tracker_list_response,
)
from app.services.tracker_rules import (
    default_completed_at,
    enforce_single_home,
    utc_now,
)
from app.db.models_sandbox import DbBook, DbUserBook
from app.auth.oauth2 import get_current_user, require_admin
from app.schemas.schemas_sandbox import (
    BookCreate,
    BookRankingReorder,
    BookResponse,
    BookSearchResult,
    BookSummary,
    BookUpdate,
    RankPlacement,
    TrackerListPage,
    UserBookCreate,
    UserBookResponse,
    UserBookUpdate,
)
from app.services.book_search import (
    apply_detail_to_book,
    get_book_detail,
    search_books as openlibrary_search_books,
)
from app.services.search_correction import correct_query
from app.services.tracked_status import attach_tracked_status

router = APIRouter(prefix='/v1', tags=['Books'])


# Global Entity Endpoints
@router.get('/books', response_model=List[BookSummary])
def get_all_books(db: Session = Depends(get_db)):
    return db.query(DbBook).all()


@router.get(
    '/books/search',
    response_model=List[BookSearchResult],
    dependencies=[Depends(search_rate_limit)],
)
def search_books_endpoint(
    q: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    results = openlibrary_search_books(q)
    if not results:
        corrected = correct_query(q)
        if corrected:
            results = openlibrary_search_books(corrected)
    return attach_tracked_status(db, current_user[0].pk, results, 'books')


@router.post(
    '/books',
    response_model=BookResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(catalog_add_cap)],
)
def create_book(
    request: BookCreate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    # Any signed-in user may add to the shared catalog (the add-from-search
    # flow); editing and deleting catalog entries stay admin-only.
    del current_user
    # Normalize before the dedupe check — enrichment stores dash-free ISBNs.
    isbn = (request.isbn or '').replace('-', '').strip() or None
    if request.googleid:
        existing = db.query(DbBook).filter(DbBook.googleid == request.googleid).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Book googleid already exists',
            )
    if isbn:
        existing = db.query(DbBook).filter(DbBook.isbn == isbn).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Book isbn already exists',
            )

    new_book = DbBook(**{**request.model_dump(), 'isbn': isbn})
    # Enrich from Open Library on add so detail/filtering work immediately
    # (best effort).
    detail = get_book_detail(isbn)
    if detail:
        apply_detail_to_book(new_book, detail)
    db.add(new_book)
    db.commit()
    db.refresh(new_book)
    return new_book


@router.get('/books/{book_id}', response_model=BookResponse)
def get_book(
    book_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Return one book's full detail, enriching from Open Library on first view."""
    del current_user
    book = _get_book(db, book_id)
    # Lazily backfill detail the first time a sparse book is opened.
    if book.description is None and book.authors is None:
        detail = get_book_detail(book.isbn)
        if detail:
            apply_detail_to_book(book, detail)
            db.commit()
            db.refresh(book)
    return book


@router.put('/books/{book_id}', response_model=BookResponse)
def update_book(
    book_id: str,
    request: BookUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    book = _get_book(db, book_id)

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(book, key, value)

    db.commit()
    db.refresh(book)
    return book


@router.delete('/books/{book_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_book(
    book_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    book = _get_book(db, book_id)
    db.delete(book)
    db.commit()
    return None


# User Tracker Endpoints
def _get_book(db: Session, book_id: str) -> DbBook:
    book = db.query(DbBook).filter(DbBook.id == book_id).first()
    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Book not found'
        )
    return book


def _get_tracker(db: Session, user_pk: int, book_pk: int):
    return (
        db.query(DbUserBook)
        .filter(DbUserBook.user_id == user_pk, DbUserBook.book_id == book_pk)
        .first()
    )


def _placed_count(db: Session, user_pk: int) -> int:
    """Number of books with an assigned rank position for this user."""
    return (
        db.query(func.count())  # pylint: disable=not-callable
        .select_from(DbUserBook)
        .filter(
            DbUserBook.user_id == user_pk,
            DbUserBook.on_rankings.is_(True),
            DbUserBook.rank.isnot(None),
        )
        .scalar()
    )


def _close_rank_gap(db: Session, user_pk: int, vacated_rank) -> None:
    """After a ranked item leaves the list, shift everything below it up."""
    if vacated_rank is None:
        return
    db.query(DbUserBook).filter(
        DbUserBook.user_id == user_pk,
        DbUserBook.on_rankings.is_(True),
        DbUserBook.rank.isnot(None),
        DbUserBook.rank > vacated_rank,
    ).update({DbUserBook.rank: DbUserBook.rank - 1}, synchronize_session=False)


@router.get(
    '/users/me/books',
    response_model=Union[List[UserBookResponse], TrackerListPage[UserBookResponse]],
)
def get_user_books(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
    params: dict = Depends(list_params),
):
    query = (
        db.query(DbUserBook)
        .options(joinedload(DbUserBook.book))
        .filter(DbUserBook.user_id == current_user[0].pk)
    )
    rows, total = list_tracker_items(query, DbUserBook, DbBook, params)
    return tracker_list_response(rows, total, params, 'Book')


@router.put('/users/me/books/rankings/order', response_model=List[UserBookResponse])
def reorder_rankings(
    request: BookRankingReorder,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Persist a new ranking order (drag-and-drop). Rank = position in the list."""
    user_pk = current_user[0].pk

    books = db.query(DbBook).filter(DbBook.id.in_(request.book_ids)).all()
    book_pk_by_id = {b.id: b.pk for b in books}

    if book_pk_by_id:
        trackers = (
            db.query(DbUserBook)
            .filter(
                DbUserBook.user_id == user_pk,
                DbUserBook.book_id.in_(book_pk_by_id.values()),
            )
            .all()
        )
        tracker_by_book_pk = {t.book_id: t for t in trackers}
    else:
        tracker_by_book_pk = {}

    for position, book_id in enumerate(request.book_ids, start=1):
        book_pk = book_pk_by_id.get(book_id)
        if not book_pk:
            continue
        tracker = tracker_by_book_pk.get(book_pk)
        if tracker:
            if tracker.rank != position:
                tracker.ranked_at = utc_now()
            tracker.rank = position
            tracker.on_rankings = True
            tracker.on_watchlist = False
    db.commit()
    return (
        db.query(DbUserBook)
        .filter(DbUserBook.user_id == user_pk, DbUserBook.on_rankings.is_(True))
        .order_by(DbUserBook.rank)
        .all()
    )


@router.get('/users/me/books/{book_id}', response_model=UserBookResponse)
def get_user_book(
    book_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Return the current user's tracker for one book (404 if not tracked)."""
    book = _get_book(db, book_id)
    tracker = _get_tracker(db, current_user[0].pk, book.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Book not marked'
        )
    return tracker


@router.put('/users/me/books/{book_id}/rank', response_model=UserBookResponse)
def set_book_rank(
    book_id: str,
    request: RankPlacement,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Place a book at an exact 1-based position in the ranked list, shifting the
    books at and below that position down by one. Works for a not-yet-ranked
    book (jump it in) or an already-ranked one (move it).
    """
    user_pk = current_user[0].pk
    book = _get_book(db, book_id)
    tracker = _get_tracker(db, user_pk, book.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Book not marked'
        )

    old_rank = tracker.rank
    was_on_rankings = tracker.on_rankings
    tracker.on_rankings = True
    tracker.on_watchlist = False
    default_completed_at(tracker, was_on_rankings)
    # Remove from its current slot first so the shift math excludes it.
    tracker.rank = None
    db.flush()
    if old_rank is not None:
        db.query(DbUserBook).filter(
            DbUserBook.user_id == user_pk,
            DbUserBook.on_rankings.is_(True),
            DbUserBook.rank.isnot(None),
            DbUserBook.rank > old_rank,
        ).update({DbUserBook.rank: DbUserBook.rank - 1}, synchronize_session=False)

    target = max(1, min(request.position, _placed_count(db, user_pk) + 1))
    db.query(DbUserBook).filter(
        DbUserBook.user_id == user_pk,
        DbUserBook.on_rankings.is_(True),
        DbUserBook.rank.isnot(None),
        DbUserBook.rank >= target,
    ).update({DbUserBook.rank: DbUserBook.rank + 1}, synchronize_session=False)

    tracker.rank = target
    tracker.ranked_at = utc_now()
    db.commit()
    db.refresh(tracker)
    return tracker


@router.post(
    '/users/me/books/{book_id}',
    response_model=UserBookResponse,
    status_code=status.HTTP_201_CREATED,
)
def mark_book(
    book_id: str,
    request: UserBookCreate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Add a book to the user's lists (idempotent — merges list membership)."""
    user_pk = current_user[0].pk
    book = _get_book(db, book_id)
    tracker = _get_tracker(db, user_pk, book.pk)
    data = request.model_dump(exclude_unset=True)

    if tracker is None:
        was_on_rankings = False
        old_rank = None
        tracker = DbUserBook(
            user_id=user_pk,
            book_id=book.pk,
            on_watchlist=bool(data.get('on_watchlist', False)),
            on_rankings=bool(data.get('on_rankings', False)),
            notes=data.get('notes'),
            completed_at=data.get('completed_at'),
        )
        db.add(tracker)
    else:
        was_on_rankings = tracker.on_rankings
        old_rank = tracker.rank
        for key in ('on_watchlist', 'on_rankings', 'notes', 'completed_at'):
            if key in data:
                setattr(tracker, key, data[key])

    # A book only holds a rank while it's on the ranked list AND was already
    # placed. Entering Rankings (or leaving it) resets to unplaced so it lands
    # in the "to rank" bucket rather than at a stale/leftover position —
    # unless it's the first ranked book, which auto-places at #1 (#289).
    enforce_single_home(tracker, data)
    default_completed_at(tracker, was_on_rankings)
    entering_rankings = tracker.on_rankings and not was_on_rankings
    if entering_rankings and _placed_count(db, user_pk) == 0:
        tracker.rank = 1
        tracker.ranked_at = utc_now()
    elif not tracker.on_rankings or not was_on_rankings:
        tracker.rank = None
        tracker.ranked_at = None
    if old_rank is not None and tracker.rank is None:
        _close_rank_gap(db, user_pk, old_rank)
    db.commit()
    db.refresh(tracker)
    return tracker


@router.put('/users/me/books/{book_id}', response_model=UserBookResponse)
def update_user_book(
    book_id: str,
    request: UserBookUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Update list membership, rank, or notes for a tracked book."""
    user_pk = current_user[0].pk
    book = _get_book(db, book_id)
    tracker = _get_tracker(db, user_pk, book.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Book not marked'
        )

    old_rank = tracker.rank
    was_on_rankings = tracker.on_rankings
    data = request.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(tracker, key, value)
    enforce_single_home(tracker, data)
    default_completed_at(tracker, was_on_rankings)

    # Entering Rankings (or leaving it) resets to unplaced so a stale/leftover
    # rank never places the book automatically; it lands in "to rank" instead.
    if not tracker.on_rankings or not was_on_rankings:
        tracker.rank = None
        tracker.ranked_at = None

    # A removed placement leaves a gap — shift everything below it up.
    if old_rank is not None and tracker.rank is None:
        _close_rank_gap(db, user_pk, old_rank)

    # If it's on neither list, drop the tracker entirely.
    if not tracker.on_watchlist and not tracker.on_rankings:
        response = UserBookResponse.model_validate(tracker)
        db.delete(tracker)
        db.commit()
        return response

    db.commit()
    db.refresh(tracker)
    return tracker


@router.delete('/users/me/books/{book_id}', status_code=status.HTTP_204_NO_CONTENT)
def unmark_book(
    book_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    user_pk = current_user[0].pk
    book = _get_book(db, book_id)
    tracker = _get_tracker(db, user_pk, book.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Book not marked'
        )
    _close_rank_gap(db, user_pk, tracker.rank)
    db.delete(tracker)
    db.commit()
    return None
