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

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import DbAdminAuditLog, DbUser


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


def record(  # pylint: disable=too-many-arguments
    db: Session,
    *,
    actor: Optional[DbUser],
    action: str,
    result: AdminAuditResult,
    request: Optional[Request] = None,
    target: Optional[DbUser] = None,
    detail: Optional[dict] = None,
    status_code: Optional[int] = None,
) -> DbAdminAuditLog:
    """
    Write one audit row and commit it immediately.

    Committed on its own rather than folded into the caller's transaction:
    the trail has to survive even if the surrounding request later rolls
    back, and it must never be the reason a read endpoint fails.
    """
    row = DbAdminAuditLog(
        actor_user_pk=actor.pk if actor else None,
        target_user_pk=target.pk if target else None,
        target_user_id=target.id if target else None,
        target_email=target.email if target else None,
        action=action,
        result=AdminAuditResult(result).value,
        detail=_redact(detail),
        request_id=getattr(request.state, 'request_id', None) if request else None,
        method=request.method if request else None,
        path=request.url.path if request else None,
        status_code=status_code,
        source_ip=request.client.host if request and request.client else None,
        user_agent=request.headers.get('user-agent') if request else None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def resolve_actor_best_effort(request: Request, db: Session) -> Optional[DbUser]:
    """
    Best-effort bearer-token resolution for a request that never reached a
    route handler, used only to attribute a denied admin request in the
    audit log. A 403 from :func:`app.auth.oauth2.require_admin` only ever
    follows a *successful* :func:`app.auth.oauth2.get_current_user`
    resolution (an invalid/missing token fails earlier, with a 401), so this
    is expected to succeed whenever it is called; it still fails closed
    (returns ``None``) rather than raise, since losing one denial's actor is
    far cheaper than a 500 in a piece of logging middleware.
    """
    # Local import: keeps this module's import surface centered on the audit
    # table rather than the whole auth stack.
    from app.auth.oauth2 import (  # pylint: disable=import-outside-toplevel
        API_KEY_PREFIX,
        _user_from_access_token,
        _user_from_api_key,
    )

    auth_header = request.headers.get('authorization', '')
    if not auth_header.lower().startswith('bearer '):
        return None
    token = auth_header[len('bearer ') :]
    unresolvable = ValueError('unresolvable actor')
    try:
        if token.startswith(API_KEY_PREFIX):
            return _user_from_api_key(token, db, unresolvable)[0]
        return _user_from_access_token(token, db, unresolvable)[0]
    except Exception:  # pylint: disable=broad-exception-caught
        return None
