# pylint: disable=missing-function-docstring
"""
Mutual friend requests (#275): send, accept, decline, cancel, unfriend, list.

Two properties are worth stating up front, because both are easy to break
with an innocuous-looking change.

**One row per relationship.** ``friendships`` stores the pair in canonical
order with a separate ``requested_by_id``; every endpoint here goes through
:mod:`app.db.db_friendship` so neither seat can produce a second row or a
contradictory one. Accepting is required before either side counts as a
friend, and once accepted, either side can unfriend.

**A first request does not confirm who exists.** There is no user search or
directory anywhere in the API, so a handle is the only way to reach a person
- which makes ``POST /friends/requests`` the place the user base could leak.
The cases:

* unknown handle, and a brand-new request to a real handle -> the same 202
  with the same body. A single probe therefore learns nothing;
* a repeat of a request the caller already sent -> **409**. Adam's explicit
  call (#275), overriding the earlier silently-idempotent behaviour: a user
  who double-clicks is told what happened rather than left guessing.

  Know what this costs. Sending twice *does* distinguish a real handle (409)
  from an unused one (202), so a determined caller can enumerate handles at
  two requests each. The rate limit is what bounds that, not this endpoint's
  response shape - so treat ``friend_request_rate_limit`` as a privacy
  control, not just an abuse control, and do not loosen it casually;
* already friends, or the other side has already asked -> 409, but reaching
  either requires the *other* user to have acted, which no amount of probing
  can arrange. The caller can already see both states in their own lists, so
  a clear error tells them nothing new;
* the caller's own handle -> 422, which reveals only the caller to themselves.

The rate limit counts attempts before the lookup, so a miss costs an attacker
exactly what a hit does.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db import db_friendship
from app.db.database import get_db
from app.db.models import DbFriendship, DbUser
from app.schemas.model_schemas import (
    InFriendRequest,
    OutFriend,
    OutFriendAck,
    OutFriendRequest,
    OutFriendUser,
    OutPendingFriendRequests,
)
from app.services.friendships import FriendshipStatus, canonical_pair
from app.services.rate_limit import friend_request_rate_limit

router = APIRouter(prefix='/v1', tags=['Friends'])

# The single answer POST /friends/requests gives for every outcome that is
# not the caller's own doing. Phrased so it stays true when the handle
# matched nobody: something was accepted for processing, and that is all.
SENT_MESSAGE = 'Friend request sent'

# Same treatment for a missing request/friendship: a wrong id, a stale id and
# somebody else's id are one response.
NOT_FOUND_REQUEST = 'Friend request not found'
NOT_FOUND_FRIEND = 'Friendship not found'


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _out_user(user: DbUser) -> OutFriendUser:
    return OutFriendUser(id=user.id, handle=user.handle, display_name=user.display_name)


def _out_friend(friendship: DbFriendship, other: DbUser) -> OutFriend:
    return OutFriend(
        id=friendship.id,
        user=_out_user(other),
        # responded_at is never NULL on an accepted row, but a row written
        # out of band shouldn't 500 a list endpoint.
        friends_since=friendship.responded_at or friendship.requested_at,
    )


def _out_request(friendship: DbFriendship, other: DbUser) -> OutFriendRequest:
    return OutFriendRequest(
        id=friendship.id,
        user=_out_user(other),
        requested_at=friendship.requested_at,
    )


@router.post(
    '/users/me/friends/requests',
    response_model=OutFriendAck,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(friend_request_rate_limit)],
)
def send_friend_request(
    request: InFriendRequest,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Send a friend request to an exact handle.

    Always 202 unless the caller is already a party to the relationship (or
    is the target) - see the module docstring for why the shape of the
    non-answers matters as much as the answer.
    """
    me = current_user[0]
    handle = request.handle.strip().lower()

    # No format validation on purpose: a malformed handle simply matches
    # nobody and takes the same generic path a valid-but-unused one does.
    target = db.query(DbUser).filter(DbUser.handle == handle).first()
    if target is None:
        return OutFriendAck(message=SENT_MESSAGE)

    if target.pk == me.pk:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='You cannot send yourself a friend request',
        )

    existing = db_friendship.friendship_between(db, me.pk, target.pk)
    if existing is not None:
        if existing.status == FriendshipStatus.ACCEPTED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='You are already friends with that user',
            )
        if existing.requested_by_id != me.pk:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='That user has already sent you a friend request - '
                'accept it instead',
            )
        # Adam's call (#275): a resend is a conflict, not a silent no-op, so
        # a double-click gets told what happened. The cost is accepted and
        # recorded in the module docstring - this is the one response that
        # confirms a handle exists.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='You have already sent that user a friend request',
        )

    low, high = canonical_pair(me.pk, target.pk)
    db.add(
        DbFriendship(
            user_low_id=low,
            user_high_id=high,
            requested_by_id=me.pk,
            status=FriendshipStatus.PENDING,
            requested_at=_now(),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # Two requests racing for the same pair. The UNIQUE constraint is the
        # arbiter; the loser reports success because a request does exist.
        db.rollback()
    return OutFriendAck(message=SENT_MESSAGE)


@router.get('/users/me/friends/requests', response_model=OutPendingFriendRequests)
def list_friend_requests(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    user_pk = current_user[0].pk
    incoming = db_friendship.list_with_other_party(
        db, user_pk, FriendshipStatus.PENDING, requested_by_me=False
    )
    outgoing = db_friendship.list_with_other_party(
        db, user_pk, FriendshipStatus.PENDING, requested_by_me=True
    )
    return OutPendingFriendRequests(
        incoming=[_out_request(row, other) for row, other in incoming],
        outgoing=[_out_request(row, other) for row, other in outgoing],
    )


@router.put('/users/me/friends/requests/{request_id}/accept', response_model=OutFriend)
def accept_friend_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Accept an incoming request; from here both sides count as friends."""
    user_pk = current_user[0].pk
    found = db_friendship.get_for_user(
        db, user_pk, request_id, FriendshipStatus.PENDING
    )
    # Requiring the caller to *not* be the requester is what stops someone
    # accepting their own outgoing request into a friendship.
    if found is None or found[0].requested_by_id == user_pk:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_REQUEST
        )
    friendship, other = found
    friendship.status = FriendshipStatus.ACCEPTED
    friendship.responded_at = _now()
    db.commit()
    db.refresh(friendship)
    return _out_friend(friendship, other)


@router.put(
    '/users/me/friends/requests/{request_id}/decline', response_model=OutFriendAck
)
def decline_friend_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Decline an incoming request.

    Deletes the row instead of recording a refusal: nobody should be able to
    read a standing "no" off the graph, and the pair can try again later.
    """
    user_pk = current_user[0].pk
    found = db_friendship.get_for_user(
        db, user_pk, request_id, FriendshipStatus.PENDING
    )
    if found is None or found[0].requested_by_id == user_pk:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_REQUEST
        )
    db.delete(found[0])
    db.commit()
    return OutFriendAck(message='Friend request declined')


@router.delete(
    '/users/me/friends/requests/{request_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
def cancel_friend_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Withdraw a request the caller sent, before it is answered."""
    user_pk = current_user[0].pk
    found = db_friendship.get_for_user(
        db, user_pk, request_id, FriendshipStatus.PENDING
    )
    if found is None or found[0].requested_by_id != user_pk:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_REQUEST
        )
    db.delete(found[0])
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/users/me/friends', response_model=list[OutFriend])
def list_friends(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    rows = db_friendship.list_with_other_party(
        db, current_user[0].pk, FriendshipStatus.ACCEPTED
    )
    return [_out_friend(row, other) for row, other in rows]


@router.delete(
    '/users/me/friends/{friendship_id}', status_code=status.HTTP_204_NO_CONTENT
)
def unfriend(
    friendship_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    End a friendship. Symmetric: either side may do this, and one row going
    away ends it for both - there is no second row to leave behind.
    """
    found = db_friendship.get_for_user(
        db, current_user[0].pk, friendship_id, FriendshipStatus.ACCEPTED
    )
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_FRIEND
        )
    db.delete(found[0])
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
