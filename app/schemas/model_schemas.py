"""
This module defines the Pydantic models (schemas) for the API.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.services import preferences
from app.services.preferences import RankedListLength
from app.services.visibility import VisibilityTier


# User data provided as input
class InUserBase(BaseModel):
    """
    Schema for user input data.
    """

    display_name: str
    email: str
    password: str


# User data returned in a response
class OutUserDisplay(BaseModel):
    """
    Schema for displaying user data.
    """

    id: str
    display_name: str
    email: EmailStr
    user_group: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        from_attributes=True,
    )


class OutResponseBaseModel(BaseModel):
    """
    All responses will have this format.
    """

    success: bool = True
    data: Optional[list] = []
    message: Optional[str] = 'None'

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        from_attributes=True,
        # These appear not to work. Ideally we would exclude None and unset.
        exclude_none=True,
        exclude_unset=True,
    )


class OutResponseUserModel(OutResponseBaseModel):
    """
    Response format for user data.
    """

    data: list[OutUserDisplay]

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        from_attributes=True,
    )


class InApiKeyCreate(BaseModel):
    """
    Request body for minting an API key.
    """

    name: str = Field(min_length=1, max_length=60)


class OutApiKey(BaseModel):
    """
    An API key as shown in listings — never includes the secret.
    """

    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class OutApiKeyCreated(OutApiKey):
    """
    Creation response: the one and only time the plaintext key appears.
    """

    key: str


class InRefreshToken(BaseModel):
    """
    Request body for the refresh and sign-out endpoints (#246).
    """

    refresh_token: str = Field(min_length=1)


class OutToken(BaseModel):
    """
    What every sign-in and refresh returns.

    Both lifetimes are reported in seconds so a client can size its cookies
    off the response instead of hardcoding guesses that drift from
    ``ACCESS_TOKEN_EXPIRE_MINUTES`` and ``REFRESH_TOKEN_EXPIRE_DAYS`` — which
    is exactly how the web session cookie ended up outliving its token.
    """

    access_token: str
    refresh_token: str
    token_type: str = 'bearer'
    expires_in: int
    refresh_expires_in: int
    user_id: str
    user_group: str
    email: str


class InVisibilityUpdate(BaseModel):
    """
    Request body for visibility settings (#274) and activity sharing (#280).
    Every shelf setting is a ``private``, ``friends`` or ``public`` tier;
    ``share_activity`` is the independent whole-feed opt-out.

    Only sent fields change; a null handle clears it (allowed only while
    everything is private). A null shelf tier clears its override and resumes
    using ``default_privacy``; an omitted field is left alone.
    """

    handle: Optional[str] = None
    default_privacy: Optional[VisibilityTier] = None
    visibility_profile: Optional[VisibilityTier] = None
    visibility_movies: Optional[VisibilityTier] = None
    visibility_tv: Optional[VisibilityTier] = None
    visibility_books: Optional[VisibilityTier] = None
    visibility_games: Optional[VisibilityTier] = None
    visibility_watchlist_movies: Optional[VisibilityTier] = None
    visibility_watchlist_tv: Optional[VisibilityTier] = None
    visibility_watchlist_books: Optional[VisibilityTier] = None
    visibility_watchlist_games: Optional[VisibilityTier] = None
    share_activity: Optional[bool] = None


class OutVisibility(BaseModel):
    """
    The caller's shelf tiers and activity-sharing setting.
    """

    handle: Optional[str] = None
    default_privacy: VisibilityTier = VisibilityTier.FRIENDS
    visibility_profile: VisibilityTier = VisibilityTier.PRIVATE
    visibility_movies: Optional[VisibilityTier] = None
    visibility_tv: Optional[VisibilityTier] = None
    visibility_books: Optional[VisibilityTier] = None
    visibility_games: Optional[VisibilityTier] = None
    visibility_watchlist_movies: Optional[VisibilityTier] = None
    visibility_watchlist_tv: Optional[VisibilityTier] = None
    visibility_watchlist_books: Optional[VisibilityTier] = None
    visibility_watchlist_games: Optional[VisibilityTier] = None
    share_activity: bool = True

    model_config = ConfigDict(from_attributes=True)


class InFriendRequest(BaseModel):
    """
    Request body for sending a friend request (#275).

    A handle and nothing else: there is no endpoint that lists or searches
    users, so an exact handle someone told you is the only way to reach them.
    """

    handle: str = Field(min_length=1, max_length=30)


class OutFriendAck(BaseModel):
    """
    Deliberately contentless acknowledgement of a friend-graph write.

    Send and decline both answer with this, and send answers with *exactly*
    this whether or not the handle resolved to anybody — see
    ``router_friends`` for why the response must not vary.
    """

    message: str


class OutFriendUser(BaseModel):
    """
    The other party in a friendship, as much of them as a friend may see.

    No email, no visibility settings — a friendship is not an introduction to
    someone's account.
    """

    id: str
    handle: Optional[str] = None
    display_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class OutFriend(BaseModel):
    """One accepted friendship. ``id`` addresses the friendship, not the user."""

    id: str
    user: OutFriendUser
    friends_since: datetime


class OutFriendRequest(BaseModel):
    """One pending request, from the perspective of whoever is listing it."""

    id: str
    user: OutFriendUser
    requested_at: datetime


class OutPendingFriendRequests(BaseModel):
    """
    Both directions in one response.

    The inbox badge and the "pending" state of an outgoing request are drawn
    together, so splitting these across two endpoints would only ever mean
    two calls.
    """

    incoming: list[OutFriendRequest] = []
    outgoing: list[OutFriendRequest] = []


class OutFollowUser(BaseModel):
    """
    The other party in a follow, as much of them as a follower may see.

    A separate class from ``OutFriendUser`` even though the fields match: the
    two relationships are deliberately kept apart everywhere else (#276), and
    a schema shared between them would be the one place that split quietly
    disappeared.
    """

    id: str
    handle: Optional[str] = None
    display_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class OutFollow(BaseModel):
    """
    One follow edge, from whichever side is listing it.

    ``id`` addresses the follow row, not either user. ``followed_at`` is the
    one fact both sides are shown; there is nothing else to negotiate since a
    follow needs no approval.
    """

    id: str
    user: OutFollowUser
    followed_at: datetime


class OutUserSearchResult(BaseModel):
    """One user returned from search."""

    id: str
    display_name: str
    handle: Optional[str] = None
    # Public profiles alone disclose their audience size. Friends-only
    # results carry null, matching the existing optional-handle convention
    # without turning a friend search into a follower-enumeration side channel.
    follower_count: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class OutUserSearchResponse(BaseModel):
    """Response shape for user search, mirroring GlobalSearchResponse."""

    query: str
    corrected: Optional[str] = None
    users: list[OutUserSearchResult]


class InPreferencesUpdate(BaseModel):
    """Request body for display preferences (#122). Only sent fields change."""

    ranked_list_length: Optional[RankedListLength] = None
    onboarding_completed: Optional[bool] = None
    time_zone: Optional[str] = None
    shelf_order: Optional[list[str]] = None
    enabled_shelves: Optional[list[str]] = None

    @field_validator('time_zone')
    @classmethod
    def validate_time_zone(cls, value: Optional[str]) -> Optional[str]:
        """Reject a zone tzdata cannot resolve here, as a 422 rather than a 500."""
        if value is None:
            return None
        if not preferences.is_valid_time_zone(value):
            raise ValueError(f'Unknown IANA time zone: {value}')
        return value

    @field_validator('shelf_order', 'enabled_shelves')
    @classmethod
    def validate_shelf_ids(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        """Reject invalid shelf identifiers before they reach the user row."""
        if value is None:
            return None
        shelf_ids = {'movies', 'tv', 'books', 'games'}
        unknown = set(value) - shelf_ids
        if unknown:
            raise ValueError(f'Unknown shelf id: {sorted(unknown)[0]}')
        if len(value) != len(set(value)):
            raise ValueError('Shelf ids must not contain duplicates')
        return value

    @field_validator('shelf_order')
    @classmethod
    def validate_shelf_order(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        """Require a complete order so no known shelf silently disappears."""
        if value is not None and set(value) != {'movies', 'tv', 'books', 'games'}:
            raise ValueError('Shelf order must include every shelf exactly once')
        return value


class OutPreferences(BaseModel):
    """The caller's display preferences, defaulted where unset."""

    ranked_list_length: RankedListLength = RankedListLength.TWENTY_FIVE
    onboarding_completed: bool = False
    # Always a concrete zone, never null: an unset column reads as the
    # deployment default, so no client has to own that fallback itself.
    time_zone: str = 'UTC'
    shelf_order: list[str] = ['movies', 'tv', 'games', 'books']
    enabled_shelves: list[str] = ['movies', 'tv', 'games', 'books']

    model_config = ConfigDict(from_attributes=True)


class OutSummaryEntry(BaseModel):
    """One ranked entry on a shelf's Top 5."""

    rank: int
    id: str
    title: str
    year: Optional[int] = None
    poster_url: Optional[str] = None


class OutSummaryShelf(BaseModel):
    """One domain's headline numbers plus its best-ranked entries."""

    category: str
    label: str
    ranked_count: int
    queued_count: int
    public: bool
    top: list[OutSummaryEntry]


class OutSummary(BaseModel):
    """
    Everything the home page renders, in one bounded response — see
    app/services/summary.py for why this endpoint exists.
    """

    handle: Optional[str] = None
    display_name: Optional[str] = None
    profile_public: bool = False
    shelves: list[OutSummaryShelf]
    total_ranked: int
    total_items: int
    onboarding_completed: bool
    needs_onboarding: bool
