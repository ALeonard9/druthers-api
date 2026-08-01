"""
This module defines the Pydantic models (schemas) for the API.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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
    Request body for visibility settings. Only sent fields change; a null
    handle clears it (allowed only while everything is private).
    """

    handle: Optional[str] = None
    public_movies: Optional[bool] = None
    public_tv: Optional[bool] = None
    public_books: Optional[bool] = None
    public_games: Optional[bool] = None


class OutVisibility(BaseModel):
    """
    The caller's visibility settings. NULL flags read as private.
    """

    handle: Optional[str] = None
    public_movies: Optional[bool] = False
    public_tv: Optional[bool] = False
    public_books: Optional[bool] = False
    public_games: Optional[bool] = False

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
