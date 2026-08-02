# pylint: disable=missing-function-docstring
"""
Visibility settings (#143) and the public read-only profile.

Everything is private by default. A user opts categories in one by one and
claims a handle; the public endpoint then serves *ranked lists only* — no
notes, no watchlist, no watch state, no activity.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.db.models import DbUser
from app.schemas.model_schemas import InVisibilityUpdate, OutVisibility
from app.services.shelves import SHELVES

router = APIRouter(prefix='/v1', tags=['Visibility'])

HANDLE_RE = re.compile(r'^[a-z0-9][a-z0-9-]{2,29}$')
# Namespace words a profile handle must never shadow.
RESERVED_HANDLES = {
    'about',
    'admin',
    'api',
    'druthers',
    'login',
    'me',
    'public',
    'settings',
    'u',
    'www',
}

# Ranked entries served per shelf on a public profile. The profile is the
# shareable surface (every share-card click lands here), so it gets a bound
# rather than the full ranked list it used to return.
PROFILE_SHELF_LIMIT = 25


@router.get('/users/me/visibility', response_model=OutVisibility)
def get_visibility(current_user: list = Depends(get_current_user)):
    return current_user[0]


@router.put('/users/me/visibility', response_model=OutVisibility)
def update_visibility(
    request: InVisibilityUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Update the handle and/or per-category public flags. Opening any category
    to the public requires a handle (that's the profile URL).
    """
    user = current_user[0]
    data = request.model_dump(exclude_unset=True)

    if 'handle' in data:
        handle = (data['handle'] or '').strip().lower() or None
        if handle is not None:
            if not HANDLE_RE.match(handle):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail='Handle must be 3-30 chars: lowercase letters, '
                    'digits, hyphens; starting with a letter or digit',
                )
            if handle in RESERVED_HANDLES:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='That handle is reserved',
                )
            taken = (
                db.query(DbUser)
                .filter(DbUser.handle == handle, DbUser.pk != user.pk)
                .first()
            )
            if taken:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='That handle is taken',
                )
        user.handle = handle

    for shelf in SHELVES:
        for flag in (shelf.visibility_flag, shelf.watchlist_visibility_flag):
            if flag in data and data[flag] is not None:
                setattr(user, flag, bool(data[flag]))

    if not user.handle and any(
        getattr(user, shelf.visibility_flag)
        or getattr(user, shelf.watchlist_visibility_flag)
        for shelf in SHELVES
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Pick a handle before making a category public — it '
            'becomes your profile URL',
        )

    db.commit()
    db.refresh(user)
    return user


@router.get('/public/{handle}')
def public_profile(handle: str, db: Session = Depends(get_db)):
    """
    Read-only public profile: ranked lists of the categories the owner has
    opted in, best first. Fully private profiles (and unknown handles) 404
    identically, so the endpoint never confirms a private account exists.
    """
    user = db.query(DbUser).filter(DbUser.handle == handle.lower()).first()
    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail='No public profile here'
    )
    if user is None:
        raise not_found

    shelves = []
    for shelf in SHELVES:
        if not getattr(user, shelf.visibility_flag):
            continue
        tracker_model, catalog_model = shelf.tracker_model, shelf.catalog_model
        ranked = (
            tracker_model.user_id == user.pk,
            tracker_model.on_rankings.is_(True),
            tracker_model.rank.isnot(None),
        )
        # ranked_count is the shelf total, so it comes from COUNT rather than
        # len(rows) now that rows are capped at PROFILE_SHELF_LIMIT.
        ranked_count = (
            db.query(func.count())  # pylint: disable=not-callable
            .select_from(tracker_model)
            .filter(*ranked)
            .scalar()
        )
        rows = (
            db.query(tracker_model.rank, catalog_model)
            .join(
                catalog_model,
                getattr(tracker_model, shelf.join_col) == catalog_model.pk,
            )
            .filter(*ranked)
            .order_by(tracker_model.rank)
            .limit(PROFILE_SHELF_LIMIT)
            .all()
        )
        shelf_out = {
            'category': shelf.label,
            # Stable URL segment for a per-shelf profile page (#93), distinct
            # from the display label above.
            'slug': shelf.category,
            'ranked_count': ranked_count,
            'items': [
                {
                    'rank': rank,
                    'title': item.title,
                    'year': item.year,
                    'poster_url': item.poster_url,
                }
                for rank, item in rows
            ],
        }

        # Watchlist visibility (#236) rides on top of the ranked-list opt-in
        # above: a category's watchlist is only public when both flags are
        # set, so there's no way to expose a watchlist without also having
        # opted the ranked list in.
        if getattr(user, shelf.watchlist_visibility_flag):
            watchlist_rows = (
                db.query(catalog_model)
                .join(
                    tracker_model,
                    getattr(tracker_model, shelf.join_col) == catalog_model.pk,
                )
                .filter(
                    tracker_model.user_id == user.pk,
                    tracker_model.on_watchlist.is_(True),
                )
                .order_by(tracker_model.created_at.desc())
                .limit(PROFILE_SHELF_LIMIT)
                .all()
            )
            shelf_out['watchlist'] = [
                {
                    'title': item.title,
                    'year': item.year,
                    'poster_url': item.poster_url,
                }
                for item in watchlist_rows
            ]

        shelves.append(shelf_out)

    if not shelves:
        raise not_found

    return {
        'handle': user.handle,
        'display_name': user.display_name,
        'shelves': shelves,
        'total_ranked': sum(s['ranked_count'] for s in shelves),
    }
