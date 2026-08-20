# pylint: disable=missing-function-docstring, too-many-lines
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

import csv
import io
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import reduce
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.auth import refresh_tokens
from app.auth.oauth2 import (
    IMPERSONATION_TOKEN_MINUTES,
    create_impersonation_token,
    get_current_user,
    is_impersonation_token,
    oauth2_scheme,
    require_admin,
)
from app.db.database import get_db
from app.db.db_follow import count_followers, list_following
from app.db.db_friendship import friend_pks
from app.db.models import (
    DbAdminAuditLog,
    DbApiKey,
    DbImpersonationSession,
    DbProductEvent,
    DbUser,
)
from app.schemas.schemas_admin import (
    InImpersonationStart,
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
    OutAdminReportResponse,
    OutImpersonationLiveSession,
    OutImpersonationParty,
    OutImpersonationSession,
    OutImpersonationSessionList,
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

REPORT_NAMES = (
    'signups',
    'active_users',
    'tracking_volume',
    'top_titles',
    'top_users',
    'engagement_by_tier',
    'activation',
    'retention',
    'conversion',
)
FUNNEL_REPORTS = {'activation', 'retention', 'conversion'}


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


def _greatest_ignoring_nulls(*expressions):
    """
    The greatest of several nullable scalar SQL expressions, NULL only when
    every one of them is.

    Built from plain CASE/comparison rather than a vendor function on
    purpose: Postgres has no scalar multi-argument ``MAX()`` (it needs
    ``GREATEST()``), SQLite's multi-argument ``max()`` is not the same
    function as its single-argument aggregate ``max()``, and the two
    dialects do not even agree on where a NULL sorts. This only has to
    behave identically on the Postgres this ships to and the SQLite the
    test suite runs on, so it is cheaper to avoid the vendor functions
    than to reconcile them.
    """

    def _pick(left, right):
        return case(
            (and_(left.isnot(None), or_(right.is_(None), left >= right)), left),
            else_=right,
        )

    return reduce(_pick, expressions)


def _last_tracked_sort_expr():
    """
    Correlated scalar subquery: ``max(updated_at)`` across the four tracker
    tables, for ``ORDER BY``.

    The SQL-level counterpart to :func:`_last_tracked_bulk`, which only
    runs on the page already fetched and therefore cannot sort the whole
    corpus - sorting only the loaded page would silently misrepresent
    every user not on it.
    """
    subqueries = [
        select(func.max(shelf.tracker_model.updated_at))
        .where(shelf.tracker_model.user_id == DbUser.pk)
        .correlate(DbUser)
        .scalar_subquery()
        for shelf in SHELVES
    ]
    return _greatest_ignoring_nulls(*subqueries)


def _tracked_total_sort_expr():
    """
    Correlated scalar subquery: ranked-or-watchlisted row count summed
    across the four tracker tables, for ``ORDER BY``. The SQL-level
    counterpart to :func:`_domain_counts_bulk`, for the same reason as
    :func:`_last_tracked_sort_expr` above.
    """
    subqueries = [
        select(func.count())  # pylint: disable=not-callable
        .select_from(shelf.tracker_model)
        .where(
            shelf.tracker_model.user_id == DbUser.pk,
            or_(
                shelf.tracker_model.on_rankings.is_(True),
                shelf.tracker_model.on_watchlist.is_(True),
            ),
        )
        .correlate(DbUser)
        .scalar_subquery()
        for shelf in SHELVES
    ]
    return reduce(lambda left, right: left + right, subqueries)


# One entry per value GET /v1/admin/users?sort= accepts. ``joined`` is a
# bare column; the other two are correlated subqueries, so all three are
# wrapped as no-arg callables - building the subquery versions eagerly
# would attach them to a query before one exists.
_SORT_EXPRESSIONS = {
    'joined': lambda: DbUser.created_at,
    'last_tracked': _last_tracked_sort_expr,
    'tracked_total': _tracked_total_sort_expr,
    'status': lambda: case((DbUser.disabled_at.is_(None), 0), else_=1),
}


@router.get('/users', response_model=OutAdminUserListResponse)
def search_users(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    request: Request,
    q: Optional[str] = None,
    status: Optional[Literal['active', 'disabled']] = None,
    sort: Optional[Literal['joined', 'last_tracked', 'tracked_total', 'status']] = None,
    direction: Literal['asc', 'desc'] = 'desc',
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Search the directory by display name, handle, or email; paginated.

    Sorted and filtered in SQL, not on the page already fetched:
    ``last_tracked``/``tracked_total`` are corpus-wide aggregates
    (:func:`_last_tracked_sort_expr`/:func:`_tracked_total_sort_expr`), so
    sorting only the returned page would answer "who joined this week" or
    "show me the disabled accounts" correctly for the visible rows and
    silently wrong for everyone else.
    """
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
    if status == 'active':
        query = query.filter(DbUser.disabled_at.is_(None))
    elif status == 'disabled':
        query = query.filter(DbUser.disabled_at.isnot(None))
    total = query.count()

    sort_expr = _SORT_EXPRESSIONS[sort]() if sort else DbUser.created_at
    primary = sort_expr.asc() if direction == 'asc' else sort_expr.desc()
    # The sort column alone is not unique - a secondary sort on pk keeps
    # paging stable instead of occasionally skipping or repeating a row
    # that ties (every sort here can tie: two accounts created in the same
    # second, two with no tracked rows, two active accounts, ...).
    rows = query.order_by(primary, DbUser.pk.desc()).offset(offset).limit(limit).all()

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
    actor: Optional[str] = Query(
        default=None, description='Match by handle, email, or id.'
    ),
    target: Optional[str] = Query(
        default=None, description='Match by handle, email, or id.'
    ),
    action: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """The audit trail itself: every admin action, allowed or denied."""
    limit, offset = _clamp_page(limit, offset)
    query = db.query(DbAdminAuditLog)
    if actor:
        # outerjoin against an alias, not the bare mapped class: target
        # below joins DbUser too, and joining the same unaliased table
        # twice in one query is invalid SQL once both filters are used
        # together. outerjoin (not join) because an actor whose account is
        # gone (SET NULL on delete) still has to be findable by the
        # denormalized fields below.
        actor_user = aliased(DbUser)
        query = query.outerjoin(
            actor_user, DbAdminAuditLog.actor_user_pk == actor_user.pk
        ).filter(
            or_(
                actor_user.handle == actor,
                actor_user.email == actor,
                actor_user.id == actor,
                DbAdminAuditLog.actor_user_id == actor,
                DbAdminAuditLog.actor_email == actor,
            )
        )
    if target:
        # Same shape as actor above - the console's audit table renders the
        # TARGET column as a handle, so a filter that could not match one
        # would confidently return "no events" while matching rows exist
        # (api#341 review). The denormalized id/email columns remain the
        # source of truth for a row whose target user was later deleted;
        # the join only adds the handle (and, for symmetry, a live id/email
        # match) for a target that still exists.
        target_user = aliased(DbUser)
        query = query.outerjoin(
            target_user, DbAdminAuditLog.target_user_pk == target_user.pk
        ).filter(
            or_(
                target_user.handle == target,
                target_user.email == target,
                target_user.id == target,
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


def _as_date(value: datetime) -> date:
    return value.date()


def _period(value: date, bucket: str) -> date:
    if bucket == 'week':
        return value - timedelta(days=value.weekday())
    if bucket == 'month':
        return value.replace(day=1)
    return value


def _periods(start: date, end: date, bucket: str) -> list[date]:
    periods = []
    current = _period(start, bucket)
    last = _period(end, bucket)
    while current <= last:
        periods.append(current)
        if bucket == 'day':
            current += timedelta(days=1)
        elif bucket == 'week':
            current += timedelta(days=7)
        elif current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return periods


def _in_range(value: datetime, start: date, end: date) -> bool:
    return start <= _as_date(value) <= end


def _report_csv(payload: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    if payload.get('rows') is not None:
        writer.writerow(['label', 'count', 'domain'])
        for row in payload['rows']:
            writer.writerow([row['label'], row['count'], row.get('domain', '')])
    else:
        keys = sorted({key for point in payload['series'] for key in point['values']})
        writer.writerow(['period', *keys])
        for point in payload['series']:
            writer.writerow(
                [point['period'], *[point['values'].get(key, '') for key in keys]]
            )
    return output.getvalue()


def _event_rows(db: Session) -> list[DbProductEvent]:
    return db.query(DbProductEvent).order_by(DbProductEvent.occurred_at).all()


# pylint: disable=too-many-locals
def _funnel_report(
    report: str, events: list[DbProductEvent], start: date, end: date, bucket: str
) -> dict:
    events_by_user: dict[int, list[DbProductEvent]] = defaultdict(list)
    for event in events:
        events_by_user[event.user_id].append(event)
    signups = [event for event in events if event.event_type == 'signup_completed']
    cohorts: dict[date, list[DbProductEvent]] = defaultdict(list)
    for signup in signups:
        if _in_range(signup.occurred_at, start, end):
            cohorts[_period(_as_date(signup.occurred_at), bucket)].append(signup)

    series = []
    for period in _periods(start, end, bucket):
        cohort = cohorts[period]
        cohort_size = len(cohort)
        values: dict[str, int] = {'cohort_size': cohort_size}
        if report in ('activation', 'conversion'):
            activated = 0
            for signup in cohort:
                kinds = {
                    event.event_type
                    for event in events_by_user[signup.user_id]
                    if event.occurred_at >= signup.occurred_at
                }
                if 'fifth_item_ranked' in kinds and (
                    'profile_completed' in kinds or 'first_share' in kinds
                ):
                    activated += 1
            if report == 'activation':
                values['activated'] = activated
            else:
                values['signup_to_activation'] = activated
                shares = [
                    event
                    for event in events
                    if event.event_type == 'first_share'
                    and _period(_as_date(event.occurred_at), bucket) == period
                ]
                share_ids = {
                    str(event.payload.get('share_id'))
                    for event in shares
                    if event.payload.get('share_id') is not None
                }
                referred_signups = sum(
                    1
                    for signup_event in signups
                    if str(signup_event.payload.get('share_id')) in share_ids
                )
                values['share_cohort_size'] = len(shares)
                values['share_to_signup'] = referred_signups
        if report == 'retention':
            returned_d7 = 0
            returned_d28 = 0
            for signup in cohort:
                returned = {
                    _as_date(event.occurred_at)
                    for event in events_by_user[signup.user_id]
                    if event.event_type == 'returning_session'
                }
                signup_date = _as_date(signup.occurred_at)
                returned_d7 += any(
                    signup_date + timedelta(days=7)
                    <= item
                    <= signup_date + timedelta(days=13)
                    for item in returned
                )
                returned_d28 += any(
                    signup_date + timedelta(days=28)
                    <= item
                    <= signup_date + timedelta(days=34)
                    for item in returned
                )
            values['retained_d7'] = returned_d7
            values['retained_d28'] = returned_d28
        series.append({'period': period.isoformat(), 'values': values})
    totals = {'cohort_size': sum(point['values']['cohort_size'] for point in series)}
    for key in {key for point in series for key in point['values']} - {'cohort_size'}:
        totals[key] = sum(point['values'].get(key, 0) for point in series)
    return {'series': series, 'totals': totals}


@router.get('/reports/{report}', response_model=OutAdminReportResponse)
def get_report(  # pylint: disable=too-many-arguments, too-many-locals, too-many-branches, too-many-positional-arguments, too-many-statements, redefined-builtin
    report: str,
    request: Request,
    from_date: Optional[date] = Query(default=None, alias='from'),
    to: Optional[date] = None,
    bucket: Literal['day', 'week', 'month'] = 'day',
    format: Optional[Literal['csv']] = None,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Product-health report data, with one stable chart/table envelope."""
    if report not in REPORT_NAMES:
        raise HTTPException(status_code=404, detail='Unknown report')
    end = to or datetime.now(timezone.utc).date()
    start = from_date or end - timedelta(days=29)
    if start > end:
        raise HTTPException(status_code=422, detail='from must not be after to')
    periods = _periods(start, end, bucket)
    payload: dict = {
        'report': report,
        'bucket': bucket,
        'from': start.isoformat(),
        'to': end.isoformat(),
        'series': [],
        'totals': {},
    }

    if report == 'signups':
        users = db.query(DbUser).all()
        cumulative = sum(1 for user in users if _as_date(user.created_at) < start)
        counts = Counter(
            _period(_as_date(user.created_at), bucket)
            for user in users
            if _in_range(user.created_at, start, end)
        )
        for period in periods:
            count = counts[period]
            cumulative += count
            payload['series'].append(
                {
                    'period': period.isoformat(),
                    'values': {'count': count, 'cumulative': cumulative},
                }
            )
        payload['totals'] = {'count': sum(counts.values())}
    elif report == 'active_users':
        activity: dict[date, set[int]] = defaultdict(set)
        all_activity: list[tuple[date, int]] = []
        for shelf in SHELVES:
            for user_id, updated_at in db.query(
                shelf.tracker_model.user_id, shelf.tracker_model.updated_at
            ):
                activity[_period(_as_date(updated_at), bucket)].add(user_id)
                all_activity.append((_as_date(updated_at), user_id))
        for period in periods:
            period_end = period + timedelta(
                days=6 if bucket == 'week' else 30 if bucket == 'month' else 0
            )
            dau = len(activity[period])
            wau = len(
                {
                    user_id
                    for day, user_id in all_activity
                    if period_end - timedelta(days=6) <= day <= period_end
                }
            )
            payload['series'].append(
                {'period': period.isoformat(), 'values': {'dau': dau, 'wau': wau}}
            )
        payload['totals'] = {
            'dau': sum(item['values']['dau'] for item in payload['series'])
        }
    elif report in ('tracking_volume', 'top_titles', 'top_users'):
        volume: dict[date, dict[str, Counter]] = defaultdict(
            lambda: defaultdict(Counter)
        )
        title_counts: Counter = Counter()
        user_counts: Counter = Counter()
        for shelf in SHELVES:
            tracker = shelf.tracker_model
            catalog = shelf.catalog_model
            rows = (
                db.query(tracker, catalog.title)
                .join(catalog, getattr(tracker, shelf.join_col) == catalog.pk)
                .all()
            )
            for row, title in rows:
                if not _in_range(row.created_at, start, end):
                    continue
                period = _period(_as_date(row.created_at), bucket)
                if row.on_rankings or row.on_watchlist:
                    volume[period][shelf.category]['tracked'] += 1
                    title_counts[(shelf.category, title)] += 1
                    user_counts[row.user_id] += 1
                if row.on_rankings:
                    volume[period][shelf.category]['ranked'] += 1
                if row.on_watchlist:
                    volume[period][shelf.category]['watchlisted'] += 1
        if report == 'tracking_volume':
            for period in periods:
                values = {}
                for shelf in SHELVES:
                    counts = volume[period][shelf.category]
                    values[f'{shelf.category}_tracked'] = counts['tracked']
                    values[f'{shelf.category}_ranked'] = counts['ranked']
                    values[f'{shelf.category}_watchlisted'] = counts['watchlisted']
                payload['series'].append(
                    {'period': period.isoformat(), 'values': values}
                )
            payload['totals'] = (
                {
                    key: sum(point['values'][key] for point in payload['series'])
                    for key in payload['series'][0]['values']
                }
                if payload['series']
                else {}
            )
        elif report == 'top_titles':
            payload['rows'] = [
                {'label': title, 'domain': domain, 'count': count}
                for (domain, title), count in title_counts.most_common(20)
            ]
            payload['totals'] = {'count': sum(title_counts.values())}
        else:
            users = {
                user.pk: user
                for user in db.query(DbUser).filter(DbUser.pk.in_(user_counts)).all()
            }
            payload['rows'] = [
                {
                    'label': users[user_pk].handle or users[user_pk].display_name,
                    'count': count,
                }
                for user_pk, count in user_counts.most_common(20)
            ]
            payload['totals'] = {'count': sum(user_counts.values())}
    elif report == 'engagement_by_tier':
        tiers: dict[date, Counter] = defaultdict(Counter)
        for user in db.query(DbUser).all():
            if _in_range(user.created_at, start, end):
                tiers[_period(_as_date(user.created_at), bucket)][
                    user.default_privacy or 'private'
                ] += 1
        for period in periods:
            values = {
                tier: tiers[period][tier] for tier in ('private', 'friends', 'public')
            }
            payload['series'].append({'period': period.isoformat(), 'values': values})
        payload['totals'] = {
            tier: sum(item['values'][tier] for item in payload['series'])
            for tier in ('private', 'friends', 'public')
        }
    else:
        events = _event_rows(db)
        instrumented = any(_in_range(event.occurred_at, start, end) for event in events)
        payload['instrumented'] = instrumented
        if instrumented:
            payload.update(_funnel_report(report, events, start, end, bucket))

    admin_audit.record(
        request,
        actor=current_user[0],
        action='admin.reports.view',
        result=AdminAuditResult.ALLOWED,
        status_code=200,
        detail={
            'report': report,
            'from': start.isoformat(),
            'to': end.isoformat(),
            'bucket': bucket,
        },
    )
    if format == 'csv':
        return Response(content=_report_csv(payload), media_type='text/csv')
    return payload


@router.get('/impersonation', response_model=OutImpersonationSessionList)
def list_impersonation_sessions(
    request: Request,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Every live (unended, unexpired) view-as session, across every admin -
    not scoped to the caller.

    Scoped admin-wide rather than to "my own sessions", the same choice
    ``GET /v1/admin/audit`` already made: that endpoint shows the whole
    trail, not just the caller's own rows, on the theory that an admin
    action is everyone's business on this surface, not just the acting
    admin's. A forgotten or abandoned session is exactly the case this
    endpoint exists to catch, and it has to be visible - and endable, see
    :func:`stop_impersonation_session` below - to an admin other than the
    one who started it.

    Never returns the bearer token itself: this is a status/oversight view,
    not a way to acquire someone else's live credential.
    """
    now = datetime.now(timezone.utc)
    rows = (
        db.query(DbImpersonationSession)
        .filter(
            DbImpersonationSession.ended_at.is_(None),
            DbImpersonationSession.expires_at > now,
        )
        .order_by(DbImpersonationSession.created_at.desc())
        .all()
    )
    # A session whose admin or target row is gone (deleted after the fact)
    # is already meaningless to show - it cannot be resumed and nobody
    # would recognize either party without the very fields that are
    # missing. Silently excluded rather than surfaced half-populated.
    live = [row for row in rows if row.admin is not None and row.target is not None]

    admin_audit.record(
        request,
        actor=current_user[0],
        action='admin.impersonation.list',
        result=AdminAuditResult.ALLOWED,
        status_code=200,
        detail={'live_count': len(live)},
    )
    return OutImpersonationSessionList(
        sessions=[
            OutImpersonationLiveSession(
                session_id=row.id,
                acting_admin=OutImpersonationParty.model_validate(row.admin),
                target=OutImpersonationParty.model_validate(row.target),
                started_at=row.created_at,
                expires_at=row.expires_at,
            )
            for row in live
        ]
    )


@router.delete('/impersonation/{session_id}')
def stop_impersonation_session(
    request: Request,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    End one specific live view-as session by id, whichever admin started
    it - the console-oversight counterpart to :func:`list_impersonation_
    sessions` above: a listing nobody could act on would not be much of a
    revoke capability. ``DELETE /v1/admin/impersonation`` (no id) still
    ends every session the CALLER owns, for the web's existing "back to
    admin" flow; this is for ending someone else's, or one of several of
    your own, without taking down the rest.

    Idempotent: an unknown, already-ended, or already-expired session id is
    a 200 with ``{"ended": 0}``, not an error - the caller asked for that
    session to be gone, and it is, one way or another.
    """
    actor = current_user[0]
    row = (
        db.query(DbImpersonationSession)
        .filter(DbImpersonationSession.id == session_id)
        .first()
    )
    ended = row is not None and row.ended_at is None
    if ended:
        row.ended_at = datetime.now(timezone.utc)
        db.commit()

    admin_audit.record(
        request,
        actor=actor,
        target=row.target if ended else None,
        action='admin.impersonation.stop',
        result=AdminAuditResult.ALLOWED,
        status_code=200,
        detail={'session_id': session_id, 'ended': 1 if ended else 0},
    )
    return {'ended': 1 if ended else 0}


@router.post('/impersonation', response_model=OutImpersonationSession)
def start_impersonation(
    request: Request,
    payload: InImpersonationStart,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
    current_user: list = Depends(get_current_user),
):
    """
    Begin a read-only view-as session (#341).

    Read-only is a blanket denial with no override: the "act on their behalf"
    criterion was cut from #341 on 2026-08-18, so there is no per-action flag
    to build. Enforcement lives in the auth resolver, not here, so a route
    added later cannot opt out of it.

    Impersonating a DISABLED account is allowed on purpose. "Why can't I sign
    in" is exactly the question this tool exists to answer, and the write
    block already covers the risk.
    """
    actor = current_user[0]
    target = _get_target_or_404(db, uuid=payload.target_uuid)

    def _deny(reason: str, message: str):
        admin_audit.record(
            request,
            actor=actor,
            target=target,
            action='admin.impersonation.start',
            result=AdminAuditResult.DENIED,
            status_code=403,
            detail={'reason': reason},
        )
        return HTTPException(status_code=403, detail=message)

    # Nesting: an impersonated caller must not mint a further session.
    # require_admin already refuses them, since the swapped identity is not an
    # admin; this covers the case where an admin target slipped through.
    # In practice this line is currently unreachable: POST is a non-safe
    # method, so an impersonation token gets refused by the write-block in
    # _resolve_impersonation before this handler body ever runs. Left in as
    # defense in depth for that check's own placement changing later - two
    # independent reasons a nested session can never mint is a better
    # invariant than relying on one.
    if is_impersonation_token(token):
        raise _deny('nested', 'Cannot impersonate from a view-as session')
    if target.pk == actor.pk:
        raise _deny('self', 'You are already yourself')
    if target.user_group == 'admin':
        raise _deny('target_is_admin', 'An admin cannot be impersonated')

    # One instant for both the row and the token below, so their expiries
    # cannot drift apart by however long two separate `now()` calls take.
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=IMPERSONATION_TOKEN_MINUTES
    )
    session = DbImpersonationSession(
        admin_user_pk=actor.pk,
        target_user_pk=target.pk,
        reason=payload.reason,
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    impersonation_token, expires = create_impersonation_token(
        target_id=target.id,
        admin_id=actor.id,
        session_id=session.id,
        expires_at=expires_at,
    )
    admin_audit.record(
        request,
        actor=actor,
        target=target,
        action='admin.impersonation.start',
        result=AdminAuditResult.ALLOWED,
        status_code=200,
        detail={'session_id': session.id, 'reason': payload.reason},
    )
    return OutImpersonationSession(
        token=impersonation_token,
        session_id=session.id,
        expires_at=expires,
        target=OutImpersonationParty.model_validate(target),
        acting_admin=OutImpersonationParty.model_validate(actor),
    )


@router.delete('/impersonation')
def stop_impersonation(
    request: Request,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    End every live view-as session this admin owns. Idempotent.

    Reached with the admin's OWN token, not the impersonation one: the
    impersonation credential cannot call this, because every ``/v1/admin/*``
    route is behind ``require_admin`` and the swapped identity is not an
    admin. The web client keeps the admin session in a separate cookie for
    exactly this reason, so "Back to admin" is one call plus a cookie delete.
    """
    actor = current_user[0]
    now = datetime.now(timezone.utc)
    live = (
        db.query(DbImpersonationSession)
        .filter(
            DbImpersonationSession.admin_user_pk == actor.pk,
            DbImpersonationSession.ended_at.is_(None),
        )
        .all()
    )
    # One target per row, not one row for the whole call: an admin can have
    # more than one live session, and a single aggregate row with no target
    # cannot say which one ended or who was being viewed.
    for session in live:
        session.ended_at = now
    if live:
        db.commit()
    if live:
        for session in live:
            admin_audit.record(
                request,
                actor=actor,
                target=session.target,
                action='admin.impersonation.stop',
                result=AdminAuditResult.ALLOWED,
                status_code=200,
            )
    else:
        # Still worth a row: a stop call with nothing live is a caller
        # asking to end a session that was already gone (or never existed),
        # not a no-op that vanishes without a trace.
        admin_audit.record(
            request,
            actor=actor,
            target=None,
            action='admin.impersonation.stop',
            result=AdminAuditResult.ALLOWED,
            status_code=200,
            detail={'ended': 0},
        )
    return {'ended': len(live)}
