"""
Pydantic schemas for the admin console's user-directory endpoints (#344).

Kept separate from :mod:`app.schemas.model_schemas` and
:mod:`app.schemas.schemas_sandbox`: these shapes are operator-only, never
returned from a user-facing route, and none of them reuse an existing
response model.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.services.visibility import VisibilityTier


class OutAdminUserSummary(BaseModel):
    """One row of ``GET /v1/admin/users``."""

    id: str
    handle: Optional[str] = None
    display_name: str
    email: str
    user_group: str
    status: str
    created_at: datetime
    # Max(updated_at) across the four tracker tables - "wrote something",
    # not "visited". Deliberately not last_active_at; real sign-in data is a
    # later increment's separate field.
    last_tracked_at: Optional[datetime] = None
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
    created_at: datetime
    last_tracked_at: Optional[datetime] = None
    visibility: OutAdminVisibility
    domains: dict[str, OutAdminDomainCounts]
    social: OutAdminSocialCounts


class OutAdminAuditActor(BaseModel):
    """Who performed one audit-logged action."""

    id: str
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
    created_at: datetime
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
