"""
Goodreads CSV import.

Parses the standard Goodreads library export (goodreads.com → My Books →
Import/Export) and maps each row onto the Books domain:

- ``Exclusive Shelf`` ``read`` → on the rankings list; the first imported
  read book is placed at #1 when the user has no placed books.
  ``to-read``/``currently-reading`` → watchlist.
- Catalog matching is by ISBN first (Goodreads wraps them in ``="…"``), then
  case-insensitive title+author; unmatched rows use the normal Open Library
  enrichment path, with the CSV fields as a fallback when it has no result.
- Idempotent: re-uploading the same file updates existing trackers instead
  of duplicating, and never overwrites notes you've written since.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models_sandbox import DbBook, DbUserBook
from app.services.book_search import apply_detail_to_book, get_book_detail
from app.services.tracker_rules import utc_now


@dataclass
class ImportReport:
    """Counts + skipped rows for the import response."""

    books_created: int = 0
    books_matched: int = 0
    trackers_created: int = 0
    trackers_updated: int = 0
    unplaced_rankings_count: int = 0
    next_unplaced_book_id: str | None = None
    skipped: list = field(default_factory=list)


def _clean_isbn(raw: str | None) -> str | None:
    """Goodreads exports ISBNs as ``="0439023483"`` — unwrap them."""
    if not raw:
        return None
    cleaned = raw.strip().removeprefix('=').strip('"').strip()
    return cleaned or None


def _int_or_none(raw: str | None) -> int | None:
    try:
        return int(raw) if raw and raw.strip() else None
    except ValueError:
        return None


def _notes(row: dict) -> str | None:
    """Review + rating become the tracker note (only when tracker has none)."""
    parts = []
    review = (row.get('My Review') or '').strip()
    if review:
        parts.append(review)
    rating = _int_or_none(row.get('My Rating'))
    if rating:
        parts.append(f'Goodreads rating: {rating}/5')
    return '\n\n'.join(parts) or None


def _completed_at(row: dict) -> date | None:
    """Parse Goodreads' Date Read when it is a usable calendar date."""
    raw = (row.get('Date Read') or '').strip()
    if not raw:
        return None
    for pattern in ('%Y/%m/%d', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            pass
    return None


def _find_book(db: Session, isbn: str | None, title: str, author: str | None):
    if isbn:
        book = db.query(DbBook).filter(DbBook.isbn == isbn).first()
        if book:
            return book
    query = db.query(DbBook).filter(func.lower(DbBook.title) == title.lower())
    if author:
        query = query.filter(func.lower(DbBook.authors).contains(author.lower()))
    return query.first()


def _has_placed_books(db: Session, user_pk: int) -> bool:
    return (
        db.query(DbUserBook)
        .filter(
            DbUserBook.user_id == user_pk,
            DbUserBook.on_rankings.is_(True),
            DbUserBook.rank.isnot(None),
        )
        .first()
        is not None
    )


def import_goodreads_csv(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    db: Session, user_pk: int, content: str
) -> ImportReport:
    """
    Run the import for one user. Commits once at the end.
    """
    report = ImportReport()
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or 'Title' not in reader.fieldnames:
        report.skipped.append(
            {'row': 0, 'reason': 'Not a Goodreads export (no Title column)'}
        )
        return report

    has_placed_books = _has_placed_books(db, user_pk)
    for line_no, row in enumerate(reader, start=2):
        title = (row.get('Title') or '').strip()
        if not title:
            report.skipped.append({'row': line_no, 'reason': 'Missing title'})
            continue

        shelf = (row.get('Exclusive Shelf') or 'read').strip().lower()
        if shelf not in ('read', 'to-read', 'currently-reading'):
            report.skipped.append(
                {'row': line_no, 'reason': f'Unknown shelf "{shelf}"'}
            )
            continue

        author = (row.get('Author') or '').strip() or None
        isbn = _clean_isbn(row.get('ISBN13')) or _clean_isbn(row.get('ISBN'))

        book = _find_book(db, isbn, title, author)
        if book is None:
            book = DbBook(
                title=title,
                isbn=isbn,
                authors=author,
                year=_int_or_none(row.get('Original Publication Year'))
                or _int_or_none(row.get('Year Published')),
                page_count=_int_or_none(row.get('Number of Pages')),
            )
            detail = get_book_detail(isbn)
            if detail:
                apply_detail_to_book(book, detail)
            db.add(book)
            db.flush()
            report.books_created += 1
        else:
            report.books_matched += 1

        tracker = (
            db.query(DbUserBook)
            .filter(DbUserBook.user_id == user_pk, DbUserBook.book_id == book.pk)
            .first()
        )
        read = shelf == 'read'
        completed_at = _completed_at(row)
        if tracker is None:
            rank = 1 if read and not has_placed_books else None
            db.add(
                DbUserBook(
                    user_id=user_pk,
                    book_id=book.pk,
                    on_rankings=read,
                    on_watchlist=not read,
                    rank=rank,
                    ranked_at=utc_now() if rank else None,
                    notes=_notes(row),
                    completed_at=(completed_at or utc_now().date()) if read else None,
                )
            )
            if rank:
                has_placed_books = True
            report.trackers_created += 1
        else:
            # Promote watchlist → read if Goodreads says so; never demote a
            # ranked book, never clobber existing notes.
            changed = False
            if read and not tracker.on_rankings:
                tracker.on_rankings = True
                tracker.on_watchlist = False
                if not has_placed_books:
                    tracker.rank = 1
                    tracker.ranked_at = utc_now()
                    has_placed_books = True
                if tracker.completed_at is None:
                    tracker.completed_at = completed_at or utc_now().date()
                changed = True
            if not tracker.notes:
                notes = _notes(row)
                if notes:
                    tracker.notes = notes
                    changed = True
            if changed:
                report.trackers_updated += 1

    db.flush()
    unplaced = (
        db.query(DbUserBook)
        .filter(
            DbUserBook.user_id == user_pk,
            DbUserBook.on_rankings.is_(True),
            DbUserBook.rank.is_(None),
        )
        .order_by(DbUserBook.pk)
        .all()
    )
    report.unplaced_rankings_count = len(unplaced)
    if unplaced:
        report.next_unplaced_book_id = unplaced[0].book.id
    db.commit()
    return report
