"""
Attaches on_watchlist/on_rankings/rank to raw search-provider results by
joining the current user's tracker row via the domain's external catalog id
(imdb/tvmaze/igdb/isbn) — so search can badge items the user already tracks
instead of only offering "add" (web#31).
"""

from typing import List

from sqlalchemy.orm import Session

from app.db.models_sandbox import (
    DbBook,
    DbMovie,
    DbTVShow,
    DbUserBook,
    DbUserMovie,
    DbUserTVShow,
    DbUserVideoGame,
    DbVideoGame,
)

# domain -> (catalog model, catalog's external-id column, tracker model, tracker's FK column)
_DOMAIN_CONFIG = {
    # Movies join on tmdb, not imdb: TMDB search hits carry no IMDb id (#163).
    'movies': (DbMovie, 'tmdb', DbUserMovie, 'movie_id'),
    'tv_shows': (DbTVShow, 'tvmaze', DbUserTVShow, 'tv_show_id'),
    'games': (DbVideoGame, 'igdb', DbUserVideoGame, 'game_id'),
    'books': (DbBook, 'isbn', DbUserBook, 'book_id'),
}


def _catalog_pk_by_external_id(
    db: Session, catalog_model, external_key: str, ids: list
):
    rows = (
        db.query(catalog_model)
        .filter(getattr(catalog_model, external_key).in_(ids))
        .all()
    )
    return {getattr(row, external_key): row.pk for row in rows}


def _tracker_by_catalog_pk(
    db: Session, tracker_model, fk_column: str, user_pk: int, catalog_pks: list
):
    if not catalog_pks:
        return {}
    rows = (
        db.query(tracker_model)
        .filter(
            tracker_model.user_id == user_pk,
            getattr(tracker_model, fk_column).in_(catalog_pks),
        )
        .all()
    )
    return {getattr(t, fk_column): t for t in rows}


def attach_tracked_status(
    db: Session, user_pk: int, results: List[dict], domain: str
) -> List[dict]:
    """
    Mutates each result dict in place, adding on_watchlist/on_rankings/rank
    for items the user already tracks. Results without the domain's external
    id (e.g. a book missing an ISBN) are left untracked — there's nothing to
    join on. Safe to call with an empty results list.
    """
    catalog_model, external_key, tracker_model, fk_column = _DOMAIN_CONFIG[domain]
    ids = [r[external_key] for r in results if r.get(external_key)]
    if not ids:
        return results

    catalog_pk_by_id = _catalog_pk_by_external_id(db, catalog_model, external_key, ids)
    tracker_by_pk = _tracker_by_catalog_pk(
        db, tracker_model, fk_column, user_pk, list(catalog_pk_by_id.values())
    )

    for r in results:
        catalog_pk = catalog_pk_by_id.get(r.get(external_key))
        tracker = tracker_by_pk.get(catalog_pk) if catalog_pk else None
        r['on_watchlist'] = bool(tracker.on_watchlist) if tracker else False
        r['on_rankings'] = bool(tracker.on_rankings) if tracker else False
        r['rank'] = tracker.rank if tracker and tracker.on_rankings else None

    return results
