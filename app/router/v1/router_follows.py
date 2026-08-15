# pylint: disable=missing-function-docstring
"""
Asymmetric follows (#276): follow, unfollow, and the two lists.

Deliberately separate from the mutual friend graph in
:mod:`app.router.v1.router_friends`. Following needs no approval from the
followee and unlocks nothing - see the module docstring on
:mod:`app.services.follows` for why the two relationships are kept apart at
every layer. The one rule this router exists to enforce:

    a follow may only be created against a profile whose ``visibility_profile``
    is exactly ``public`` - never ``friends``, never ``private``, regardless of
    who is asking or what else they can already see.

That check happens once, in :func:`follow_user`, against the *current* tier at
the moment of the call. It is never re-checked afterwards: if the followee's
profile later drops out of ``public``, the follow row is left exactly where
it was (see :class:`app.db.models.DbFollow`) and simply stops admitting
anything, because nothing in :mod:`app.services.visibility` ever reads it.

Unlike a friend request, a handle here needs no probing defense: the target
must already be public to be followable, so there is no relationship whose
mere existence would leak private state. A handle that does not resolve to a
public profile therefore 404s exactly like one that resolves to nobody -
mirroring the indistinguishable-404 rule ``/v1/public/{handle}`` already
holds to - but there is no need for the generic-body trick
``router_friends`` uses, since neither outcome exposes anything the caller
did not already have to know to ask.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db import db_follow
from app.db.database import get_db
from app.db.models import DbFollow, DbUser
from app.schemas.model_schemas import OutFollow, OutFollowUser
from app.services.follows import assert_not_self
from app.services.rate_limit import follow_rate_limit
from app.services.visibility import is_public

router = APIRouter(prefix='/v1', tags=['Follows'])

NOT_FOUND_PROFILE = 'No public profile here'
NOT_FOUND_FOLLOW = 'Not following that user'


def _out_user(user: DbUser) -> OutFollowUser:
    return OutFollowUser(id=user.id, handle=user.handle, display_name=user.display_name)


def _out_follow(follow: DbFollow, other: DbUser) -> OutFollow:
    return OutFollow(
        id=follow.id, user=_out_user(other), followed_at=follow.followed_at
    )


def _followable_target(db: Session, handle: str) -> DbUser:
    """
    The user behind ``handle``, if and only if they are currently followable.

    Followable means the profile tier is exactly ``public`` right now - the
    same question ``/v1/public/{handle}`` answers for ``visibility_profile``,
    asked directly rather than through a viewer-relationship ceiling, since a
    follow ignores who is asking. An unknown handle and a handle that
    resolves to a non-public profile are the same 404, so neither leaks
    anything the other doesn't.
    """
    target = db.query(DbUser).filter(DbUser.handle == handle.lower()).first()
    if target is None or not is_public(target.visibility_profile):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_PROFILE
        )
    return target


@router.put(
    '/users/me/following/{handle}',
    response_model=OutFollow,
    dependencies=[Depends(follow_rate_limit)],
)
def follow_user(
    handle: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Follow a public profile. Idempotent: following twice just returns the
    existing row rather than erroring, since there is no second state for a
    repeat call to conflict with.
    """
    me = current_user[0]
    target = _followable_target(db, handle)

    try:
        assert_not_self(me.pk, target.pk)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='You cannot follow yourself',
        ) from exc

    existing = db_follow.find(db, me.pk, target.pk)
    if existing is not None:
        return _out_follow(existing, target)

    follow = DbFollow(follower_id=me.pk, followee_id=target.pk)
    db.add(follow)
    try:
        db.commit()
    except IntegrityError:
        # Two requests racing to follow the same target. The UNIQUE
        # constraint is the arbiter; the loser reads back the winner's row.
        db.rollback()
        follow = db_follow.find(db, me.pk, target.pk)
    else:
        db.refresh(follow)
    return _out_follow(follow, target)


@router.delete(
    '/users/me/following/{handle}',
    status_code=status.HTTP_204_NO_CONTENT,
)
def unfollow_user(
    handle: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Stop following someone.

    Deliberately does not require the target to still be public - a follow
    persists through a followee tightening their profile (that's the whole
    point of #276's "grants nothing, persists anyway" rule), and the follower
    must still be able to unfollow after that happens.
    """
    me = current_user[0]
    target = db.query(DbUser).filter(DbUser.handle == handle.lower()).first()
    found = db_follow.find(db, me.pk, target.pk) if target is not None else None
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_FOLLOW
        )
    db.delete(found)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/users/me/following', response_model=list[OutFollow])
def list_following(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Everyone the caller follows."""
    rows = db_follow.list_following(db, current_user[0].pk)
    return [_out_follow(row, other) for row, other in rows]


@router.get('/users/me/followers', response_model=list[OutFollow])
def list_followers(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Everyone following the caller."""
    rows = db_follow.list_followers(db, current_user[0].pk)
    return [_out_follow(row, other) for row, other in rows]
