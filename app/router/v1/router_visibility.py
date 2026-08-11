# pylint: disable=missing-function-docstring
"""
Visibility settings (#143, tiered in #274) and the public read-only profile.

Everything is private by default. A user moves categories up the tier ladder
(``private`` -> ``friends`` -> ``public``) one by one and claims a handle;
the public endpoint then serves *ranked lists only* — no notes, no watch
state, no activity — plus watchlists where those are separately opted in.

Two rules hold the model together:

* a handle is required before anything leaves ``private`` (the handle *is*
  the profile URL), and
* the profile tier is always at least as open as the most-open shelf, so a
  shelf can never be more visible than the page that links to it.

Since #277 the public profile is *viewer-aware*: the caller's relationship to
the owner is resolved once per request and turned into a single tier ceiling,
which then filters every shelf. See :func:`public_profile` for why the
resolution has to happen once rather than per shelf.
"""

import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user, get_optional_current_user
from app.config import get_settings
from app.db.database import get_db
from app.db.db_follow import count_followers, is_following
from app.db.models import DbUser
from app.schemas.model_schemas import InVisibilityUpdate, OutVisibility
from app.services import handles
from app.services.profile_access import viewer_relationship
from app.services.shelves import SHELVES, Shelf, shelf_tier_fields
from app.services.visibility import (
    PROFILE_TIER_FIELD,
    ViewerRelationship,
    VisibilityTier,
    admits,
    ceiling_for,
    coerce,
    covers,
    most_open,
    is_public,
)

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

# Ranked entries served per shelf on a public profile when no length is
# requested (#279 parameterized this; 25 remains the default so existing
# callers see no change).
PROFILE_SHELF_LIMIT = 25

# Upper bound on a requested `limit` for a single shelf (#279). A request
# beyond this clamps rather than 422ing — deliberately looser than the
# tracker endpoints' MAX_PAGE (app/services/tracker_query.py), which error
# instead: those are for API integrators who should know the max, this is a
# shared link that has to stay robust to a hand-edited or scraped query
# string. Chosen with the 2,000-item shelf test case in mind (#122).
MAX_PUBLIC_SHELF_LIMIT = 2000

# The public profile answers differently per caller (#277), on the success
# path and on the 404 alike, so every response off it is keyed on credentials.
VARY_ON_AUTH = {'Vary': 'Authorization'}

# Every tier column a client may set, profile first. Shelf columns come from
# the registry, so a new domain arrives here without a code change.
TIER_FIELDS = (PROFILE_TIER_FIELD,) + tuple(field for field, _ in shelf_tier_fields())


def _validate_handle(db: Session, user: DbUser, raw: Optional[str]) -> Optional[str]:
    """Normalise and check a requested handle; None clears it."""
    handle = (raw or '').strip().lower() or None
    if handle is None:
        return None
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
    # Public profile URLs and share cards carry this, so it's checked at
    # claim time only — an existing handle is never re-checked. The
    # allowlist (HANDLE_PROFANITY_ALLOWLIST) is Adam's override for a
    # legitimate handle the wordlist flags anyway; see app/services/handles.py.
    if (
        handle not in get_settings().handle_profanity_allowlist_set
        and handles.is_profane(handle)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='That handle is not allowed',
        )
    taken = (
        db.query(DbUser).filter(DbUser.handle == handle, DbUser.pk != user.pk).first()
    )
    if taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='That handle is taken',
        )
    return handle


def _proposed_tiers(user: DbUser, data: dict) -> dict:
    """
    The nine tiers as they would stand after this request.

    Built without touching ``user`` so a rejected update leaves nothing
    half-applied on the session. An absent field and an explicit null both
    mean "leave it alone".
    """
    return {
        field: (
            VisibilityTier(data[field])
            if data.get(field) is not None
            else coerce(getattr(user, field))
        )
        for field in TIER_FIELDS
    }


def _assert_handle_present(handle: Optional[str], tiers: dict) -> None:
    """
    A handle is the profile URL, so nothing may leave ``private`` without one.

    Stated over the *resulting* state it also covers the reverse: clearing a
    handle is only allowed while every setting — profile included — is still
    private.
    """
    if handle:
        return
    if any(tier is not VisibilityTier.PRIVATE for tier in tiers.values()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Pick a handle before sharing anything — it becomes your '
            'profile URL',
        )


def _assert_profile_covers_shelves(tiers: dict) -> None:
    """
    Reject a profile tier less open than the most-open shelf.

    A shelf is only reachable through the profile page, so a public shelf
    behind a friends-only profile would either leak or mislead. The profile
    may be *more* open than every shelf (an empty public profile is fine);
    it may never be less.
    """
    profile = tiers[PROFILE_TIER_FIELD]
    required = most_open(tiers[field] for field, _ in shelf_tier_fields())
    if covers(profile, required):
        return
    # Name the most-open shelf: raising the profile to clear that one clears
    # every other, so it is the only instruction worth printing.
    label = next(
        label for field, label in shelf_tier_fields() if tiers[field] == required
    )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"{label} is set to {required.value}, so your profile must be "
        f"at least {required.value} — it is currently {profile.value}",
    )


def _shelf_payload(  # pylint: disable=too-many-arguments, too-many-positional-arguments, too-many-locals
    db: Session,
    user: DbUser,
    shelf: Shelf,
    ceiling: VisibilityTier,
    ranked_limit: int = PROFILE_SHELF_LIMIT,
    ranked_offset: int = 0,
    watchlist_limit: int = PROFILE_SHELF_LIMIT,
    watchlist_offset: int = 0,
) -> dict:
    """
    One shelf as the profile serves it: ranked list, and watchlist if allowed.

    Called only for a shelf the ceiling already admits. The watchlist tier
    (#236) is then checked against that same ceiling, which is what keeps a
    watchlist from ever being served for a shelf the viewer cannot see.

    ``ranked_limit``/``ranked_offset`` and their watchlist counterparts page
    each list independently (#279) — a client viewing one shelf's ranked list
    in depth has no reason to also pull a deep page of its watchlist, so the
    two default to the small preview size unless the caller specifically
    asked for that one to go deep.
    """
    tracker_model, catalog_model = shelf.tracker_model, shelf.catalog_model
    ranked = (
        tracker_model.user_id == user.pk,
        tracker_model.on_rankings.is_(True),
        tracker_model.rank.isnot(None),
    )
    # ranked_count is the shelf total, independent of ranked_limit — a client
    # paging through a long shelf still needs to know how much is left.
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
        .offset(ranked_offset)
        .limit(ranked_limit)
        .all()
    )
    payload = {
        'category': shelf.label,
        # Stable URL segment for a per-shelf profile page (#93), distinct
        # from the display label above.
        'slug': shelf.category,
        'ranked_count': ranked_count,
        'items': [
            {
                'rank': rank,
                'id': str(item.id),
                'title': item.title,
                'year': item.year,
                'poster_url': item.poster_url,
            }
            for rank, item in rows
        ],
    }

    if not admits(ceiling, getattr(user, shelf.watchlist_visibility_tier)):
        return payload

    watchlist_filter = (
        tracker_model.user_id == user.pk,
        tracker_model.on_watchlist.is_(True),
    )
    # watchlist_count mirrors ranked_count: the true total, independent of
    # watchlist_limit (#279).
    payload['watchlist_count'] = (
        db.query(func.count())  # pylint: disable=not-callable
        .select_from(tracker_model)
        .filter(*watchlist_filter)
        .scalar()
    )
    watchlist_rows = (
        db.query(catalog_model)
        .join(
            tracker_model,
            getattr(tracker_model, shelf.join_col) == catalog_model.pk,
        )
        .filter(*watchlist_filter)
        .order_by(tracker_model.created_at.desc())
        .offset(watchlist_offset)
        .limit(watchlist_limit)
        .all()
    )
    payload['watchlist'] = [
        {
            'id': str(item.id),
            'title': item.title,
            'year': item.year,
            'poster_url': item.poster_url,
        }
        for item in watchlist_rows
    ]
    return payload


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
    Update the handle and/or any of the nine visibility tiers.

    Only fields present in the body change. The result has to satisfy both
    invariants — a handle for anything non-private, and a profile at least as
    open as its most-open shelf — or the whole update is rejected. Both are
    checked against the *resulting* state before anything is written, so a
    rejection leaves the row exactly as it was.
    """
    user = current_user[0]
    data = request.model_dump(exclude_unset=True)

    handle = (
        _validate_handle(db, user, data['handle']) if 'handle' in data else user.handle
    )
    tiers = _proposed_tiers(user, data)

    _assert_handle_present(handle, tiers)
    _assert_profile_covers_shelves(tiers)

    user.handle = handle
    for field, tier in tiers.items():
        setattr(user, field, tier)

    db.commit()
    db.refresh(user)
    return user


@router.get(
    '/public/{handle}',
    # FastAPI emits the bearer scheme as the only security requirement, which
    # reads as "credentials required". Appending the empty alternative (extras
    # concatenate onto the generated list) is how OpenAPI spells "optional" —
    # without it a generated client would refuse to call this anonymously.
    openapi_extra={'security': [{}]},
)
def public_profile(  # pylint: disable=too-many-arguments, too-many-positional-arguments, too-many-locals
    handle: str,
    response: Response,
    db: Session = Depends(get_db),
    viewer: Optional[DbUser] = Depends(get_optional_current_user),
    # Depth controls (#279). `shelf` narrows the response to one category —
    # the hub view has no use for a deep page of every shelf at once, so
    # `limit`/`offset` only take effect when a specific shelf is named.
    # `kind` picks which of that shelf's two lists (ranked or watchlist)
    # the limit/offset apply to; the other stays at the small preview size.
    shelf: Optional[str] = None,
    kind: Literal['ranked', 'watchlist'] = 'ranked',
    limit: int = PROFILE_SHELF_LIMIT,
    offset: int = 0,
):
    """
    Read-only profile: ranked lists of the categories the owner has opted in,
    best first, filtered to what *this* caller may see.

    Authentication is optional (#277). Anonymous callers and signed-in
    strangers get ``public`` and nothing else; an accepted friend also gets
    ``friends``; the owner gets everything of their own — including private
    shelves, and so never a 404 on their own handle — which is why the
    response carries ``viewer.relationship``: a client rendering this page has
    to be able to say *whose* view it is showing. ``viewer.following`` (#276)
    rides alongside it for the same reason — whether to render a Follow or
    Following button — and is computed only *after* every access decision
    above has already been made: following is never part of the ceiling a
    caller is served, only a fact about the payload once that's settled.

    **One ceiling, resolved once.** The caller's relationship is turned into a
    single tier ceiling before any shelf is read, and every shelf — ranked
    list and watchlist alike — is then compared against that one value. The
    alternative, asking "is this viewer a friend?" per shelf, is what lets two
    shelves end up evaluated under different assumptions.

    **Nothing visible is indistinguishable from nothing there.** An unknown
    handle, a profile whose tier is above this caller, and a profile with no
    shelf this caller may see all raise the *same* exception object: same
    status, same body, same headers. Since visibility is now
    viewer-dependent, so is the 404 — a profile a friend can see 404s for
    everybody else exactly as a handle nobody ever claimed does. A named
    ``shelf`` that doesn't exist, or that this ceiling doesn't admit, folds
    into the same 404 rather than a distinct error — see #279.

    **Depth is viewer-controlled, not owner-controlled** (#279): the owner
    picks a tier, the viewer picks how much of an admitted shelf to read.
    ``limit`` clamps to ``MAX_PUBLIC_SHELF_LIMIT`` rather than erroring — a
    shared link has to survive a hand-edited or scraped query string — and
    only applies once a single ``shelf`` is named; the multi-shelf hub view
    always gets the small preview size for every shelf.
    """
    user = db.query(DbUser).filter(DbUser.handle == handle.lower()).first()
    # One object for every "you get nothing" outcome, so they cannot drift
    # apart. Vary rides on it as well: whether this handle 404s is itself
    # viewer-dependent, so a shared cache must not answer one viewer from
    # another viewer's copy — of the profile or of its absence.
    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail='No public profile here',
        headers=VARY_ON_AUTH,
    )
    if user is None:
        raise not_found

    relationship = viewer_relationship(db, user, viewer)
    ceiling = ceiling_for(relationship)
    if not admits(ceiling, user.visibility_profile):
        raise not_found

    response.headers.update(VARY_ON_AUTH)

    candidates = (
        SHELVES if shelf is None else tuple(s for s in SHELVES if s.category == shelf)
    )

    clamped_limit = max(1, min(limit, MAX_PUBLIC_SHELF_LIMIT))
    clamped_offset = max(0, offset)
    ranked_depth = (
        (clamped_limit, clamped_offset)
        if shelf is not None and kind == 'ranked'
        else (PROFILE_SHELF_LIMIT, 0)
    )
    watchlist_depth = (
        (clamped_limit, clamped_offset)
        if shelf is not None and kind == 'watchlist'
        else (PROFILE_SHELF_LIMIT, 0)
    )

    shelves = [
        _shelf_payload(db, user, s, ceiling, *ranked_depth, *watchlist_depth)
        for s in candidates
        if admits(ceiling, getattr(user, s.visibility_tier))
    ]

    # A named shelf query (`?shelf=...`) that doesn't exist or isn't admitted
    # returns 404. When no specific shelf is requested, an admitted profile
    # with no visible shelves returns 200 with an empty shelves list (#296).
    if shelf is not None and not shelves:
        raise not_found

    # Following (#276) grants no additional visibility — it never touches
    # `ceiling` or anything above this line — so it is computed only for the
    # response payload, after every access decision has already been made.
    # Self can never be "following" (a self-follow is unrepresentable), and an
    # anonymous caller has no pk to look up, so both skip the query.
    following = (
        relationship is ViewerRelationship.NONE
        or relationship is ViewerRelationship.FRIEND
    ) and is_following(db, viewer.pk, user.pk)

    payload = {
        'handle': user.handle,
        'display_name': user.display_name,
        'viewer': {'relationship': relationship.value, 'following': following},
        'shelves': shelves,
        'total_ranked': sum(s['ranked_count'] for s in shelves),
    }
    # Follow rows survive a profile becoming non-public so the follower can
    # later unfollow, but the row count is only public while the profile is.
    # Do not return a zero or null here: the field itself is the disclosure.
    if is_public(user.visibility_profile):
        payload['follower_count'] = count_followers(db, user.pk)
    return payload
