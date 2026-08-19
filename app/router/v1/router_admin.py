# pylint: disable=missing-function-docstring
"""
Admin user directory (#344): search, per-user aggregates, and the audit
trail those actions leave.

``dependencies=[Depends(require_admin)]`` sits on the router, not on each
route. That is deliberate: a route added here later can't ship ungated by
someone forgetting a decorator, the way the ad-hoc admin checks in
``app.router.v1.user`` could (and did - see the cleanup there in this same
PR). Each route still separately depends on ``get_current_user`` to get the
resolved actor for the audit row; FastAPI caches that dependency per
request, so it costs no extra query.

Impersonation and reports are later increments - not built here.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.auth import refresh_tokens
from app.auth.oauth2 import get_current_user, require_admin
from app.db.database import get_db
from app.db.db_follow import count_followers, list_following
from app.db.db_friendship import friend_pks
from app.db.models import DbAdminAuditLog, DbApiKey, DbUser
from app.schemas.schemas_admin import (
    OutAdminAuditActor,
    OutAdminAuditEvent,
    OutAdminAuditResponse,
    OutAdminAuditTarget,
    OutAdminDomainCounts,
    OutAdminSocialCounts,
    OutAdminUserDetail,
    OutAdminUserListResponse,
    OutAdminUserSummary,
    OutAdminVisibility,
)
from app.services import admin_audit
from app.services.admin_audit import AdminAuditResult
from app.services.shelves import SHELVES

router = APIRouter(
    prefix='/v1/admin',
    tags=['Admin'],
    dependencies=[Depends(require_admin)],
)

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


def _clamp_page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, MAX_PAGE_SIZE)), max(0, offset)


def _user_status(user: DbUser) -> str:
    return 'disabled' if user.disabled_at is not None else 'active'


def _domain_counts_bulk(db: Session, user_pks: list[int]) -> dict[int, dict[str, dict]]:
    """
    Ranked/watchlist/total per shelf for every pk in ``user_pks``, in one
    grouped query per shelf rather than one query per user per shelf - a
    page of 50 users costs 4 queries here, not 200.
    """
    counts: dict[int, dict[str, dict]] = {
        user_pk: {
            shelf.category: {'ranked': 0, 'watchlist': 0, 'total': 0}
            for shelf in SHELVES
        }
        for user_pk in user_pks
    }
    if not user_pks:
        return counts
    for shelf in SHELVES:
        tracker = shelf.tracker_model
        rows = (
            db.query(
                tracker.user_id,
                func.count(  # pylint: disable=not-callable
                    case((tracker.on_rankings.is_(True), 1))
                ),
                func.count(  # pylint: disable=not-callable
                    case((tracker.on_watchlist.is_(True), 1))
                ),
            )
            .filter(tracker.user_id.in_(user_pks))
            .group_by(tracker.user_id)
            .all()
        )
        for user_pk, ranked, watchlist in rows:
            counts[user_pk][shelf.category] = {
                'ranked': ranked,
                'watchlist': watchlist,
                'total': ranked + watchlist,
            }
    return counts


def _last_tracked_bulk(db: Session, user_pks: list[int]) -> dict[int, Optional[object]]:
    """
    ``max(updated_at)`` across the four tracker tables, per pk - "wrote
    something", never relabeled as "last active" (real sign-in data is a
    later increment).
    """
    latest: dict[int, Optional[object]] = dict.fromkeys(user_pks)
    if not user_pks:
        return latest
    for shelf in SHELVES:
        tracker = shelf.tracker_model
        rows = (
            db.query(tracker.user_id, func.max(tracker.updated_at))
            .filter(tracker.user_id.in_(user_pks))
            .group_by(tracker.user_id)
            .all()
        )
        for user_pk, timestamp in rows:
            if timestamp and (latest[user_pk] is None or timestamp > latest[user_pk]):
                latest[user_pk] = timestamp
    return latest


def _user_summaries(db: Session, users: list[DbUser]) -> list[OutAdminUserSummary]:
    pks = [user.pk for user in users]
    domains_by_pk = _domain_counts_bulk(db, pks)
    last_tracked_by_pk = _last_tracked_bulk(db, pks)
    return [
        OutAdminUserSummary(
            id=user.id,
            handle=user.handle,
            display_name=user.display_name,
            email=user.email,
            user_group=user.user_group,
            status=_user_status(user),
            created_at=user.created_at,
            last_tracked_at=last_tracked_by_pk[user.pk],
            tracked_total=sum(d['total'] for d in domains_by_pk[user.pk].values()),
        )
        for user in users
    ]


@router.get('/users', response_model=OutAdminUserListResponse)
def search_users(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    request: Request,
    q: Optional[str] = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Search the directory by display name, handle, or email; paginated."""
    limit, offset = _clamp_page(limit, offset)
    query = db.query(DbUser)
    if q:
        like = f'%{q}%'
        query = query.filter(
            or_(
                DbUser.display_name.ilike(like),
                DbUser.handle.ilike(like),
                DbUser.email.ilike(like),
            )
        )
    total = query.count()
    # created_at alone is not unique - a secondary sort on pk keeps paging
    # stable instead of occasionally skipping or repeating a row that ties.
    rows = (
        query.order_by(DbUser.created_at.desc(), DbUser.pk.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    admin_audit.record(
        request,
        actor=current_user[0],
        action='admin.user.search',
        result=AdminAuditResult.ALLOWED,
        detail={'q': q, 'limit': limit, 'offset': offset, 'total': total},
        # These three routes are read-only and either return this exact
        # 200 or raise before reaching this call (get_user_detail's 404),
        # so the response's real status is already known here.
        status_code=200,
    )
    return OutAdminUserListResponse(
        total=total, limit=limit, offset=offset, users=_user_summaries(db, rows)
    )


def _build_user_detail(db: Session, user: DbUser) -> OutAdminUserDetail:
    """
    The full per-user aggregate payload. Shared by ``GET /users/{uuid}`` and
    the disable/enable actions below, which return this exact shape so the
    console can swap the row in place without a refetch.
    """
    domains = _domain_counts_bulk(db, [user.pk])[user.pk]
    last_tracked_at = _last_tracked_bulk(db, [user.pk])[user.pk]
    return OutAdminUserDetail(
        id=user.id,
        handle=user.handle,
        display_name=user.display_name,
        email=user.email,
        user_group=user.user_group,
        status=_user_status(user),
        created_at=user.created_at,
        last_tracked_at=last_tracked_at,
        visibility=OutAdminVisibility(
            profile=user.visibility_profile,
            default_privacy=user.default_privacy,
            movies=user.visibility_movies,
            tv=user.visibility_tv,
            books=user.visibility_books,
            games=user.visibility_games,
            watchlist_movies=user.visibility_watchlist_movies,
            watchlist_tv=user.visibility_watchlist_tv,
            watchlist_books=user.visibility_watchlist_books,
            watchlist_games=user.visibility_watchlist_games,
            share_activity=user.share_activity,
        ),
        domains={
            category: OutAdminDomainCounts(**counts)
            for category, counts in domains.items()
        },
        social=OutAdminSocialCounts(
            friends=len(friend_pks(db, user.pk)),
            followers=count_followers(db, user.pk),
            following=len(list_following(db, user.pk)),
        ),
    )


def _get_target_or_404(db: Session, uuid: str) -> DbUser:
    user = db.query(DbUser).filter(DbUser.id == uuid).first()
    if user is None:
        raise HTTPException(status_code=404, detail='User not found')
    return user


@router.get('/users/{uuid}', response_model=OutAdminUserDetail)
def get_user_detail(
    request: Request,
    uuid: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Per-user aggregates: tracked counts, visibility tiers, social counts."""
    user = _get_target_or_404(db, uuid)
    admin_audit.record(
        request,
        actor=current_user[0],
        target=user,
        action='admin.user.view',
        result=AdminAuditResult.ALLOWED,
        status_code=200,
    )
    return _build_user_detail(db, user)


@router.post('/users/{uuid}/disable', response_model=OutAdminUserDetail)
def disable_user(
    request: Request,
    uuid: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Disable an account: blocks sign-in, kills every live credential, and
    (D2, per product decision) makes the user disappear everywhere a
    deleted user would - see the read-path filters in router_visibility,
    router_comparison, router_friends, router_follows, router_activity, and
    db_user.search_users.

    Self-disable and disabling another admin are both refused outright
    (403) rather than merely discouraged: with a small operator pool,
    either one risks a lockout with nobody left to undo it. Both refusals
    are audited as denials, same as the router-level admin gate.
    """
    actor = current_user[0]
    target = _get_target_or_404(db, uuid)

    if target.pk == actor.pk:
        admin_audit.record(
            request,
            actor=actor,
            target=target,
            action='admin.user.disable',
            result=AdminAuditResult.DENIED,
            status_code=403,
            detail={'reason': 'self'},
        )
        raise HTTPException(
            status_code=403, detail='You cannot disable your own account'
        )
    if target.user_group == 'admin':
        admin_audit.record(
            request,
            actor=actor,
            target=target,
            action='admin.user.disable',
            result=AdminAuditResult.DENIED,
            status_code=403,
            detail={'reason': 'target_is_admin'},
        )
        raise HTTPException(
            status_code=403, detail='Cannot disable another admin account'
        )

    if target.disabled_at is None:
        # One transaction: the flag, every refresh-token family, and every
        # API key all have to change together, or a request already mid-
        # flight against this account could keep a stale credential alive.
        target.disabled_at = datetime.now(timezone.utc)
        refresh_tokens.revoke_all_for_user(db, target.pk)
        db.query(DbApiKey).filter(DbApiKey.user_id == target.pk).delete(
            synchronize_session=False
        )
        db.commit()

    admin_audit.record(
        request,
        actor=actor,
        target=target,
        action='admin.user.disable',
        result=AdminAuditResult.ALLOWED,
        status_code=200,
    )
    return _build_user_detail(db, target)


@router.post('/users/{uuid}/enable', response_model=OutAdminUserDetail)
def enable_user(
    request: Request,
    uuid: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Re-enable an account. Restores sign-in only - the refresh tokens and
    API keys a disable revoked are gone for good (this is load-bearing for
    the confirmation copy on the disable action: re-enabling is not
    "undo," the user has to sign in again and reissue any API keys).
    """
    target = _get_target_or_404(db, uuid)
    if target.disabled_at is not None:
        target.disabled_at = None
        db.commit()

    admin_audit.record(
        request,
        actor=current_user[0],
        target=target,
        action='admin.user.enable',
        result=AdminAuditResult.ALLOWED,
        status_code=200,
    )
    return _build_user_detail(db, target)


def _audit_actor_out(row: DbAdminAuditLog) -> Optional[OutAdminAuditActor]:
    if row.actor is not None:
        return OutAdminAuditActor(
            id=row.actor.id, handle=row.actor.handle, email=row.actor.email
        )
    if row.actor_user_id or row.actor_email:
        # The actor row is gone (self-delete is permitted); fall back to
        # what was denormalized at write time so the row still names who
        # did it instead of going anonymous.
        return OutAdminAuditActor(
            id=row.actor_user_id, handle=None, email=row.actor_email
        )
    return None


def _audit_target_out(row: DbAdminAuditLog) -> Optional[OutAdminAuditTarget]:
    if row.target is not None:
        return OutAdminAuditTarget(
            id=row.target.id, handle=row.target.handle, email=row.target.email
        )
    if row.target_user_id or row.target_email:
        # The target row is gone; fall back to what was denormalized at
        # write time so the entry still reads sensibly.
        return OutAdminAuditTarget(
            id=row.target_user_id, handle=None, email=row.target_email
        )
    return None


@router.get('/audit', response_model=OutAdminAuditResponse)
def list_audit(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    request: Request,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    actor: Optional[str] = None,
    target: Optional[str] = None,
    action: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """The audit trail itself: every admin action, allowed or denied."""
    limit, offset = _clamp_page(limit, offset)
    query = db.query(DbAdminAuditLog)
    if actor:
        # outerjoin, not join: an actor whose account is gone (SET NULL on
        # delete) still has to be findable by the denormalized fields below.
        query = query.outerjoin(
            DbUser, DbAdminAuditLog.actor_user_pk == DbUser.pk
        ).filter(
            or_(
                DbUser.handle == actor,
                DbUser.email == actor,
                DbUser.id == actor,
                DbAdminAuditLog.actor_user_id == actor,
                DbAdminAuditLog.actor_email == actor,
            )
        )
    if target:
        query = query.filter(
            or_(
                DbAdminAuditLog.target_user_id == target,
                DbAdminAuditLog.target_email == target,
            )
        )
    if action:
        query = query.filter(DbAdminAuditLog.action == action)
    else:
        # Reading the trail is itself an audited action (below). Without
        # this, paging through the trail during an investigation pushes the
        # very events under investigation off page one. Still writable and
        # still retrievable - just excluded from the unfiltered default view.
        query = query.filter(DbAdminAuditLog.action != 'admin.audit.view')

    total = query.count()
    # created_at alone is not unique - a secondary sort on pk keeps paging
    # stable instead of occasionally skipping or repeating a row that ties.
    rows = (
        query.order_by(DbAdminAuditLog.created_at.desc(), DbAdminAuditLog.pk.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    admin_audit.record(
        request,
        actor=current_user[0],
        action='admin.audit.view',
        result=AdminAuditResult.ALLOWED,
        detail={
            'limit': limit,
            'offset': offset,
            'total': total,
            'actor_filter': actor,
            'target_filter': target,
            'action_filter': action,
        },
        status_code=200,
    )
    return OutAdminAuditResponse(
        total=total,
        limit=limit,
        offset=offset,
        events=[
            OutAdminAuditEvent(
                id=row.pk,
                created_at=row.created_at,
                actor=_audit_actor_out(row),
                target=_audit_target_out(row),
                action=row.action,
                result=row.result,
                detail=row.detail,
                request_id=row.request_id,
                method=row.method,
                path=row.path,
                status_code=row.status_code,
                source_ip=row.source_ip,
            )
            for row in rows
        ],
    )
