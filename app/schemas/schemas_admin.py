"""
Pydantic schemas for the admin console's user-directory endpoints (#344).

Kept separate from :mod:`app.schemas.model_schemas` and
:mod:`app.schemas.schemas_sandbox`: these shapes are operator-only, never
returned from a user-facing route, and none of them reuse an existing
response model.
"""

from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.services.visibility import VisibilityTier


def _serialize_utc(value: datetime) -> str:
    """
    Stamp a naive datetime as UTC before emitting it (aware ones are just
    normalized to UTC). The backing columns are Postgres ``timestamp without
    time zone``, but every value written to them is already UTC
    (:func:`datetime.now` called with ``timezone.utc`` throughout the
    codebase) - only the serialization was silently dropping the
    designator. Per ECMAScript, a date-time string with no offset parses as
    *local* time, so every JS client was quietly shifting these by its own
    UTC offset (api#344 review).
    """
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


# Same wire type as `datetime`, but always serializes with an explicit UTC
# designator. See `_serialize_utc`. Use this in place of a bare `datetime`
# on any field this branch introduces.
UtcDatetime = Annotated[datetime, PlainSerializer(_serialize_utc, return_type=str)]


class OutAdminUserSummary(BaseModel):
    """One row of ``GET /v1/admin/users``."""

    id: str
    handle: Optional[str] = None
    display_name: str
    email: str
    user_group: str
    status: str
    created_at: UtcDatetime
    # Max(updated_at) across the four tracker tables - "wrote something",
    # not "visited". Deliberately not last_active_at; real sign-in data is a
    # later increment's separate field.
    last_tracked_at: Optional[UtcDatetime] = None
    tracked_total: int

    model_config = ConfigDict(from_attributes=True)


class OutAdminUserListResponse(BaseModel):
    """``GET /v1/admin/users``."""

    total: int
    limit: int
    offset: int
    users: list[OutAdminUserSummary]


class OutAdminDomainCounts(BaseModel):
    """Ranked/watchlist/total for one tracked domain."""

    ranked: int
    watchlist: int
    total: int


class OutAdminVisibility(BaseModel):
    """The nine visibility settings, plus activity sharing, for one user."""

    profile: VisibilityTier
    default_privacy: VisibilityTier
    movies: Optional[VisibilityTier] = None
    tv: Optional[VisibilityTier] = None
    books: Optional[VisibilityTier] = None
    games: Optional[VisibilityTier] = None
    watchlist_movies: Optional[VisibilityTier] = None
    watchlist_tv: Optional[VisibilityTier] = None
    watchlist_books: Optional[VisibilityTier] = None
    watchlist_games: Optional[VisibilityTier] = None
    share_activity: bool


class OutAdminSocialCounts(BaseModel):
    """Friend-graph size for one user."""

    friends: int
    followers: int
    following: int


class OutAdminUserDetail(BaseModel):
    """``GET /v1/admin/users/{uuid}``."""

    id: str
    handle: Optional[str] = None
    display_name: str
    email: str
    user_group: str
    status: str
    created_at: UtcDatetime
    last_tracked_at: Optional[UtcDatetime] = None
    visibility: OutAdminVisibility
    domains: dict[str, OutAdminDomainCounts]
    social: OutAdminSocialCounts


class OutAdminAuditActor(BaseModel):
    """
    Who performed one audit-logged action.

    ``id`` is optional, not just ``handle``/``email``: the actor's account
    can be deleted (self-delete is permitted), which SETs ``actor_user_pk``
    NULL - the row falls back to whatever of ``actor_user_id``/
    ``actor_email`` was denormalized at write time, and either can be
    missing if the actor could not be resolved at all (an expired-token
    denial, for instance).
    """

    id: Optional[str] = None
    handle: Optional[str] = None
    email: Optional[str] = None


class OutAdminAuditTarget(BaseModel):
    """Who one audit-logged action was about, if anyone."""

    id: Optional[str] = None
    handle: Optional[str] = None
    email: Optional[str] = None


class OutAdminAuditEvent(BaseModel):
    """One row of ``GET /v1/admin/audit``."""

    id: int
    created_at: UtcDatetime
    actor: Optional[OutAdminAuditActor] = None
    target: Optional[OutAdminAuditTarget] = None
    action: str
    result: str
    detail: Optional[dict] = None
    request_id: Optional[str] = None
    method: Optional[str] = None
    path: Optional[str] = None
    status_code: Optional[int] = None


class OutAdminAuditResponse(BaseModel):
    """``GET /v1/admin/audit``."""

    total: int
    limit: int
    offset: int
    events: list[OutAdminAuditEvent]
