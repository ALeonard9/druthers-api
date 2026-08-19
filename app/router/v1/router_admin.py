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

Disable/enable and its auth-path enforcement, impersonation, and reports are
later increments - not built here. This increment adds the ``disabled_at``
column and reads it into ``status``, but nothing writes it yet.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user, require_admin
from app.db.database import get_db
from app.db.db_follow import count_followers, list_following
from app.db.db_friendship import friend_pks
from app.db.models import DbAdminAuditLog, DbUser
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
    rows = query.order_by(DbUser.created_at.desc()).offset(offset).limit(limit).all()

    admin_audit.record(
        db,
        actor=current_user[0],
        action='admin.user.search',
        result=AdminAuditResult.ALLOWED,
        request=request,
        detail={'q': q, 'limit': limit, 'offset': offset, 'total': total},
        # These three routes are read-only and either return this exact
        # 200 or raise before reaching this call (get_user_detail's 404),
        # so the response's real status is already known here.
        status_code=200,
    )
    return OutAdminUserListResponse(
        total=total, limit=limit, offset=offset, users=_user_summaries(db, rows)
    )


@router.get('/users/{uuid}', response_model=OutAdminUserDetail)
def get_user_detail(
    request: Request,
    uuid: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Per-user aggregates: tracked counts, visibility tiers, social counts."""
    user = db.query(DbUser).filter(DbUser.id == uuid).first()
    if user is None:
        raise HTTPException(status_code=404, detail='User not found')

    domains = _domain_counts_bulk(db, [user.pk])[user.pk]
    last_tracked_at = _last_tracked_bulk(db, [user.pk])[user.pk]

    admin_audit.record(
        db,
        actor=current_user[0],
        target=user,
        action='admin.user.view',
        result=AdminAuditResult.ALLOWED,
        request=request,
        status_code=200,
    )
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


def _audit_actor_out(row: DbAdminAuditLog) -> Optional[OutAdminAuditActor]:
    if row.actor is None:
        return None
    return OutAdminAuditActor(
        id=row.actor.id, handle=row.actor.handle, email=row.actor.email
    )


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
        query = query.join(DbUser, DbAdminAuditLog.actor_user_pk == DbUser.pk).filter(
            or_(DbUser.handle == actor, DbUser.email == actor, DbUser.id == actor)
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

    total = query.count()
    rows = (
        query.order_by(DbAdminAuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    admin_audit.record(
        db,
        actor=current_user[0],
        action='admin.audit.view',
        result=AdminAuditResult.ALLOWED,
        request=request,
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
            )
            for row in rows
        ],
    )
