"""
Append-only audit trail for the admin console (#344).

:func:`record` is the only way a row gets written; there is deliberately no
update or delete path anywhere in this module or its callers - a trail that
can be edited after the fact is not a trail.

``detail`` is built by allowlisting fields in, never by blocklisting fields
out. A payload dict handed to a future admin action can never leak a
password, hash, bearer token, API key, or refresh token into the log just
because it happened to be sitting in the same dict as something worth
recording - it has to be named in :data:`ALLOWED_DETAIL_FIELDS` first.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from typing import Iterator, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DbAdminAuditLog, DbUser
from app.services.rate_limit import client_ip


class AdminAuditResult(StrEnum):
    """Outcome of one admin action, as recorded in ``result``."""

    ALLOWED = 'allowed'
    DENIED = 'denied'


# Every key a caller is allowed to put in ``detail``. A new admin action has
# to add its fields here before they show up in the log - never the reverse.
ALLOWED_DETAIL_FIELDS = frozenset(
    {
        'q',
        'limit',
        'offset',
        'total',
        'actor_filter',
        'target_filter',
        'action_filter',
        'reason',
        'session_id',
        'ended',
        'via_impersonation',
    }
)


def _redact(detail: Optional[dict]) -> Optional[dict]:
    """Drop everything not on the allowlist; ``None``/empty stays ``None``."""
    if not detail:
        return None
    allowed = {
        key: value for key, value in detail.items() if key in ALLOWED_DETAIL_FIELDS
    }
    return allowed or None


@contextmanager
def _short_lived_session(request: Request) -> Iterator[Session]:
    """
    A session of its own for the audit write, resolved the same way FastAPI
    resolves ``get_db`` (so a test's ``dependency_overrides`` is honored,
    and the write lands in the same database the caller is using).

    Writing through the caller's own request-scoped session would work, but
    that session has ``expire_on_commit=True`` (the default on
    ``SessionLocal``): committing on it invalidates every ORM object the
    caller already loaded, so the next attribute access on any of them
    refetches one row at a time. In ``search_users`` that turned the bulk
    aggregate queries this router exists to batch into one query per user
    again. A short-lived session sidesteps that entirely, and as a bonus
    means the audit row still gets written even if the caller's own
    transaction later rolls back.
    """
    db_dependency = request.app.dependency_overrides.get(get_db, get_db)
    generator = db_dependency()
    try:
        session = next(generator)
    except StopIteration as exc:
        # PEP 479: an un-caught StopIteration inside this generator function
        # (this is a @contextmanager) becomes a confusing RuntimeError -
        # translate it into a real error about what actually went wrong.
        raise RuntimeError('get_db dependency yielded no session') from exc
    try:
        yield session
    finally:
        next(generator, None)


def record(  # pylint: disable=too-many-arguments
    request: Request,
    *,
    actor: Optional[DbUser],
    action: str,
    result: AdminAuditResult,
    target: Optional[DbUser] = None,
    detail: Optional[dict] = None,
    status_code: Optional[int] = None,
) -> DbAdminAuditLog:
    """Write one audit row, on its own short-lived session, and commit it."""
    row = DbAdminAuditLog(
        actor_user_pk=actor.pk if actor else None,
        actor_user_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        target_user_pk=target.pk if target else None,
        target_user_id=target.id if target else None,
        target_email=target.email if target else None,
        action=action,
        result=AdminAuditResult(result).value,
        detail=_redact(detail),
        request_id=getattr(request.state, 'request_id', None),
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        source_ip=client_ip(request),
        user_agent=request.headers.get('user-agent'),
        created_at=datetime.now(timezone.utc),
    )
    with _short_lived_session(request) as audit_db:
        audit_db.add(row)
        audit_db.commit()
        audit_db.refresh(row)
    # Lets admin_audit_denial_middleware (app/run.py) tell "this request
    # never reached a handler" (require_admin's own 403/401 - the one case
    # it exists to catch) apart from "the handler ran and denied the action
    # itself" (disable_user's self/another-admin guards, which call this
    # directly). Without the flag, a handler-level denial gets logged twice:
    # once here with the specific action, once more by the middleware as a
    # generic admin.access - both true, but the second is redundant noise.
    request.state.admin_audit_recorded = True
    return row


def resolve_actor_best_effort(
    request: Request, db: Session
) -> tuple[Optional[DbUser], bool]:
    """
    Best-effort bearer-token resolution for a request that never reached a
    route handler, used only to attribute a denied admin request in the
    audit log. Deliberately read-only: unlike the normal auth path, this
    must never have a side effect on the credential it is only trying to
    attribute, so it looks up an API key directly instead of going through
    :func:`app.auth.oauth2.get_current_user`'s ``_user_from_api_key`, which
    bumps and commits ``last_used_at`` as a matter of course on every real
    request.

    Returns ``(actor, via_impersonation)``. An impersonation token's
    resolved identity is the swapped-in TARGET, not the caller - if this
    attributed a denial to that resolved identity the way the normal auth
    path does, a denied admin-route probe made while impersonating would
    land on the innocent target's permanent audit record and hide the
    acting admin behind it. So an impersonation token is attributed to the
    admin named in its ``act`` claim instead, decoded directly rather than
    through the normal resolver (which would apply the read-only write-block
    and other session checks that do not matter for mere attribution, and
    would return the target regardless).

    A 403 or 401 on ``/v1/admin/*`` is the only case this is called for. A
    403 always follows a successful token resolution (an invalid/missing
    token fails earlier as a 401), so this succeeds whenever it's called for
    one; a 401 means resolution already failed once, so this fails closed
    (returns ``(None, False)``) the same way rather than raise - losing one
    denial's actor is far cheaper than a 500 in a piece of logging
    middleware.
    """
    # Local imports: keeps this module's import surface centered on the
    # audit table rather than the whole auth stack.
    from app.auth.oauth2 import (  # pylint: disable=import-outside-toplevel
        API_KEY_PREFIX,
        IMPERSONATION_TOKEN_TYPE,
        _user_from_access_token,
        decode_token_payload_best_effort,
        hash_api_key,
    )
    from app.db.models import DbApiKey  # pylint: disable=import-outside-toplevel

    auth_header = request.headers.get('authorization', '')
    if not auth_header.lower().startswith('bearer '):
        return None, False
    token = auth_header[len('bearer ') :]
    try:
        if token.startswith(API_KEY_PREFIX):
            row = (
                db.query(DbApiKey)
                .filter(DbApiKey.key_hash == hash_api_key(token))
                .first()
            )
            return (row.user if row else None), False

        payload = decode_token_payload_best_effort(token)
        if payload is not None and payload.get('typ') == IMPERSONATION_TOKEN_TYPE:
            admin_id = payload.get('act')
            admin = (
                db.query(DbUser).filter(DbUser.id == admin_id).first()
                if admin_id
                else None
            )
            return admin, True

        return (
            _user_from_access_token(
                token, db, ValueError('unresolvable actor'), request
            )[0],
            False,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        return None, False
